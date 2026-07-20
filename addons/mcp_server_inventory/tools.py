# -*- coding: utf-8 -*-
"""Curated MCP tools for the Inventory domain (``stock.picking`` / stock).

Thin wrappers over the standard ORM. Every tool executes as the authenticated
``res.users`` (the ``env`` handed in), so Inventory ACLs / record rules apply -
no ``sudo`` anywhere. Write tools use the shared propose/confirm contract.
Delivery-slip PDF links are produced via the core ``mcp.report.link`` facility.
"""
from odoo.addons.mcp_server.mcp import constants
from odoo.addons.mcp_server.mcp.exceptions import ToolExecutionError
from odoo.addons.mcp_server.mcp.registry import tool

# Visible only to MCP users. Actual Inventory access is enforced per call.
_GROUPS = ["mcp_server.group_mcp_user"]

_DELIVERY_REPORT = "stock.action_report_delivery"
_PICKING_STATES = ["draft", "waiting", "confirmed", "assigned", "done", "cancel"]

_PICKING_FIELDS = [
    "id",
    "name",
    "state",
    "partner_id",
    "picking_type_id",
    "scheduled_date",
    "date_done",
    "origin",
    "location_id",
    "location_dest_id",
    "backorder_id",
    "priority",
    "company_id",
]

_MOVE_FIELDS = [
    "id",
    "product_id",
    "product_uom_qty",
    "quantity_done",
    "product_uom",
    "state",
    "location_id",
    "location_dest_id",
]

_PRODUCT_STOCK_FIELDS = ["id", "name", "default_code", "uom_id"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _get_picking(env, picking_id):
    picking = env["stock.picking"].browse(int(picking_id))
    if not picking.exists():
        raise ToolExecutionError("Transfer %s not found" % picking_id)
    return picking


def _resolve_picking_type(env, arguments):
    if arguments.get("picking_type_id"):
        pt = env["stock.picking.type"].browse(int(arguments["picking_type_id"]))
        if not pt.exists():
            raise ToolExecutionError(
                "Picking type %s not found" % arguments["picking_type_id"]
            )
        return pt
    code = arguments.get("picking_type_code")
    if not code:
        raise ToolExecutionError(
            "Provide 'picking_type_id' or 'picking_type_code' "
            "(incoming/outgoing/internal)."
        )
    domain = [("code", "=", code)]
    if arguments.get("warehouse_id"):
        domain.append(("warehouse_id", "=", int(arguments["warehouse_id"])))
    pt = env["stock.picking.type"].search(domain, limit=1)
    if not pt:
        raise ToolExecutionError("No picking type found for code %r" % code)
    return pt


def _resolve_transfer_locations(env, ptype, arguments):
    src = arguments.get("location_id") or ptype.default_location_src_id.id
    dst = arguments.get("location_dest_id") or ptype.default_location_dest_id.id
    if not src:
        if ptype.code == "incoming":
            src = env.ref("stock.stock_location_suppliers").id
        elif ptype.warehouse_id:
            src = ptype.warehouse_id.lot_stock_id.id
    if not dst:
        if ptype.code == "outgoing":
            dst = env.ref("stock.stock_location_customers").id
        elif ptype.warehouse_id:
            dst = ptype.warehouse_id.lot_stock_id.id
    if not src or not dst:
        raise ToolExecutionError(
            "Could not determine source/destination locations; "
            "pass 'location_id' and 'location_dest_id'."
        )
    return int(src), int(dst)


def _default_stock_location(env):
    wh = env["stock.warehouse"].search(
        [("company_id", "=", env.company.id)], limit=1
    )
    if not wh:
        raise ToolExecutionError("No warehouse configured for this company.")
    return wh.lot_stock_id


def _pdf_filename(picking):
    return "Delivery Slip - %s.pdf" % (picking.name or picking.id).replace("/", "-")


def _transfer_pdf_url(env, picking):
    return env["mcp.report.link"].mint(
        _DELIVERY_REPORT, picking, filename=_pdf_filename(picking)
    )


def _transfer_detail(env, picking, with_pdf=True):
    data = picking.read(_PICKING_FIELDS)[0]
    data["moves"] = picking.move_ids.read(_MOVE_FIELDS)
    if with_pdf:
        try:
            data["pdf_url"] = _transfer_pdf_url(env, picking)
        except ToolExecutionError as exc:
            data["pdf_url"] = None
            data["pdf_note"] = str(exc)
    return data


_MOVES_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "product_id": {"type": "integer", "description": "product.product id"},
            "quantity": {"type": "number", "minimum": 0},
            "description": {"type": "string"},
        },
        "required": ["product_id"],
    },
}


# ---------------------------------------------------------------------------
# read tools
# ---------------------------------------------------------------------------
@tool(
    name="inventory.check_stock",
    description="Check stock quantities (on hand, forecast, available) for "
    "storable products. Filter by product text and optionally scope to a "
    "warehouse or a specific location.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Matched against product name / internal reference / barcode",
            },
            "product_id": {"type": "integer", "description": "Specific product.product id"},
            "warehouse_id": {"type": "integer"},
            "location_id": {"type": "integer"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Check stock"},
)
def check_stock(env, arguments):
    ctx = {}
    if arguments.get("warehouse_id"):
        ctx["warehouse"] = int(arguments["warehouse_id"])
    if arguments.get("location_id"):
        ctx["location"] = int(arguments["location_id"])
    Product = env["product.product"].with_context(**ctx)

    domain = [("type", "=", "product")]
    if arguments.get("product_id"):
        domain.append(("id", "=", int(arguments["product_id"])))
    query = (arguments.get("query") or "").strip()
    if query:
        domain += [
            "|", "|",
            ("name", "ilike", query),
            ("default_code", "ilike", query),
            ("barcode", "ilike", query),
        ]
    limit = min(int(arguments.get("limit") or 20), 100)
    products = Product.search(domain, limit=limit, order="name")
    rows = []
    for p in products:
        rows.append(
            {
                "id": p.id,
                "name": p.display_name,
                "default_code": p.default_code or None,
                "uom": p.uom_id.name,
                "qty_on_hand": p.qty_available,
                "qty_forecast": p.virtual_available,
                "qty_available": p.free_qty,
            }
        )
    return {"products": rows, "returned": len(rows), "scope": ctx or "all locations"}


@tool(
    name="inventory.search_transfers",
    description="Search stock transfers (receipts, deliveries, internal "
    "transfers). Filter by reference/origin text, state, type, partner and "
    "scheduled-date range.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Matched against reference and origin"},
            "state": {"type": "string", "enum": _PICKING_STATES},
            "picking_type_code": {
                "type": "string",
                "enum": ["incoming", "outgoing", "internal"],
            },
            "partner_id": {"type": "integer"},
            "date_from": {"type": "string", "description": "Scheduled date >= (YYYY-MM-DD)"},
            "date_to": {"type": "string", "description": "Scheduled date <= (YYYY-MM-DD)"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "offset": {"type": "integer", "minimum": 0},
        },
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Search transfers"},
)
def search_transfers(env, arguments):
    domain = []
    query = (arguments.get("query") or "").strip()
    if query:
        domain += ["|", ("name", "ilike", query), ("origin", "ilike", query)]
    if arguments.get("state"):
        domain.append(("state", "=", arguments["state"]))
    if arguments.get("picking_type_code"):
        domain.append(("picking_type_id.code", "=", arguments["picking_type_code"]))
    if arguments.get("partner_id"):
        domain.append(("partner_id", "=", int(arguments["partner_id"])))
    if arguments.get("date_from"):
        domain.append(("scheduled_date", ">=", arguments["date_from"]))
    if arguments.get("date_to"):
        domain.append(("scheduled_date", "<=", arguments["date_to"]))

    limit = min(int(arguments.get("limit") or 20), 100)
    offset = int(arguments.get("offset") or 0)
    records = env["stock.picking"].search_read(
        domain, _PICKING_FIELDS, limit=limit, offset=offset, order="scheduled_date desc"
    )
    total = env["stock.picking"].search_count(domain)
    return {"records": records, "returned": len(records), "total": total, "offset": offset}


@tool(
    name="inventory.get_transfer",
    description="Fetch full detail for one transfer (picking), including its "
    "product moves and a downloadable delivery-slip PDF link.",
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "integer", "description": "stock.picking id"}},
        "required": ["id"],
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Get transfer detail"},
)
def get_transfer(env, arguments):
    picking = _get_picking(env, arguments["id"])
    picking.check_access_rule("read")
    return {"transfer": _transfer_detail(env, picking)}


@tool(
    name="inventory.get_transfer_pdf",
    description="Get a short-lived, downloadable delivery-slip PDF link for a "
    "transfer. The link renders the report on demand and needs no Odoo login.",
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "integer", "description": "stock.picking id"}},
        "required": ["id"],
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Get delivery-slip PDF link"},
)
def get_transfer_pdf(env, arguments):
    picking = _get_picking(env, arguments["id"])
    picking.check_access_rule("read")
    url = _transfer_pdf_url(env, picking)
    return {
        "transfer_id": picking.id,
        "transfer_name": picking.name,
        "pdf_url": url,
        "filename": _pdf_filename(picking),
        "expires_in": env["mcp.report.link"]._ttl(),
    }


@tool(
    name="inventory.list_warehouses",
    description="List warehouses with their main stock location (useful to pick "
    "a warehouse_id / location_id for other tools).",
    input_schema={"type": "object", "properties": {}},
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "List warehouses"},
)
def list_warehouses(env, arguments):
    records = env["stock.warehouse"].search_read(
        [], ["id", "name", "code", "lot_stock_id", "company_id"], order="name"
    )
    return {"warehouses": records, "returned": len(records)}


@tool(
    name="inventory.list_locations",
    description="List internal stock locations (useful to pick a location_id for "
    "adjustments or transfers).",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "usage": {
                "type": "string",
                "enum": ["internal", "supplier", "customer", "inventory", "transit"],
                "description": "Location usage; defaults to internal",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "List locations"},
)
def list_locations(env, arguments):
    domain = [("usage", "=", arguments.get("usage") or "internal")]
    query = (arguments.get("query") or "").strip()
    if query:
        domain.append(("complete_name", "ilike", query))
    limit = min(int(arguments.get("limit") or 50), 100)
    records = env["stock.location"].search_read(
        domain, ["id", "complete_name", "usage", "warehouse_id"],
        limit=limit, order="complete_name",
    )
    return {"locations": records, "returned": len(records)}


# ---------------------------------------------------------------------------
# write tools (propose/confirm)
# ---------------------------------------------------------------------------
@tool(
    name="inventory.create_transfer",
    description="Create a stock transfer (receipt/delivery/internal) with one or "
    "more product moves. Specify either picking_type_id or picking_type_code "
    "(+ optional warehouse_id). Two-step propose/confirm.",
    input_schema={
        "type": "object",
        "properties": {
            "picking_type_id": {"type": "integer"},
            "picking_type_code": {
                "type": "string",
                "enum": ["incoming", "outgoing", "internal"],
            },
            "warehouse_id": {"type": "integer"},
            "partner_id": {"type": "integer", "description": "Customer/vendor res.partner id"},
            "location_id": {"type": "integer", "description": "Override source location"},
            "location_dest_id": {"type": "integer", "description": "Override destination location"},
            "scheduled_date": {"type": "string", "description": "YYYY-MM-DD [HH:MM:SS]"},
            "origin": {"type": "string", "description": "Source document reference"},
            "moves": _MOVES_SCHEMA,
            "confirmation_token": {"type": "string"},
        },
        "required": ["moves"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    required_groups=_GROUPS,
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "title": "Create a transfer",
    },
)
def create_transfer(env, arguments):
    ptype = _resolve_picking_type(env, arguments)
    moves_in = arguments.get("moves") or []
    if not moves_in:
        raise ToolExecutionError("Provide at least one move.")
    src, dst = _resolve_transfer_locations(env, ptype, arguments)

    move_cmds = []
    for m in moves_in:
        if not m.get("product_id"):
            raise ToolExecutionError("Each move needs a 'product_id'.")
        product = env["product.product"].browse(int(m["product_id"]))
        if not product.exists():
            raise ToolExecutionError("Product %s not found" % m["product_id"])
        move_cmds.append(
            (0, 0, {
                "name": m.get("description") or product.display_name,
                "product_id": product.id,
                "product_uom_qty": float(m.get("quantity") or 1.0),
                "product_uom": product.uom_id.id,
                "location_id": src,
                "location_dest_id": dst,
            })
        )

    vals = {
        "picking_type_id": ptype.id,
        "location_id": src,
        "location_dest_id": dst,
        "move_ids": move_cmds,
    }
    if arguments.get("partner_id"):
        vals["partner_id"] = int(arguments["partner_id"])
    if arguments.get("scheduled_date"):
        vals["scheduled_date"] = arguments["scheduled_date"]
    if arguments.get("origin"):
        vals["origin"] = arguments["origin"]

    preview = "Will CREATE a %s transfer (%s) with %d move(s)." % (
        ptype.code,
        ptype.name,
        len(move_cmds),
    )
    env["mcp.action.confirmation"].require("inventory.create_transfer", arguments, preview)

    picking = env["stock.picking"].create(vals)
    picking.action_confirm()
    try:
        picking.action_assign()
    except Exception:  # noqa: BLE001 - reservation is best-effort
        pass
    return {"created": True, "transfer": _transfer_detail(env, picking)}


@tool(
    name="inventory.validate_transfer",
    description="Validate (complete) a transfer. Any moves without a done "
    "quantity are set to their demand. Two-step propose/confirm.",
    input_schema={
        "type": "object",
        "properties": {
            "transfer_id": {"type": "integer", "description": "stock.picking id"},
            "confirmation_token": {"type": "string"},
        },
        "required": ["transfer_id"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    required_groups=_GROUPS,
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "title": "Validate a transfer",
    },
)
def validate_transfer(env, arguments):
    picking = _get_picking(env, arguments["transfer_id"])
    if picking.state == "done":
        raise ToolExecutionError("Transfer %s is already done." % picking.name)
    if picking.state == "cancel":
        raise ToolExecutionError("Transfer %s is cancelled." % picking.name)

    preview = "Will VALIDATE transfer %s (%d move(s))." % (
        picking.name,
        len(picking.move_ids),
    )
    env["mcp.action.confirmation"].require("inventory.validate_transfer", arguments, preview)

    if picking.state == "draft":
        picking.action_confirm()
    picking.action_assign()
    for move in picking.move_ids:
        if move.quantity_done == 0:
            move.quantity_done = move.product_uom_qty

    res = picking.button_validate()
    # button_validate may return a wizard action (immediate transfer /
    # backorder). Since we filled quantities to demand this is rarely hit, but
    # handle it defensively by processing the wizard.
    if isinstance(res, dict) and res.get("res_model"):
        wiz_model = res["res_model"]
        wiz_ctx = res.get("context") or {}
        wizard = env[wiz_model].with_context(**wiz_ctx).create({})
        if hasattr(wizard, "process"):
            wizard.process()

    picking.invalidate_recordset()
    return {"validated": True, "transfer": _transfer_detail(env, picking)}


@tool(
    name="inventory.adjust_quantity",
    description="Set the on-hand quantity of a storable product at a location "
    "(an inventory adjustment). If no location is given, the company's main "
    "stock location is used. Two-step propose/confirm.",
    input_schema={
        "type": "object",
        "properties": {
            "product_id": {"type": "integer", "description": "product.product id"},
            "quantity": {"type": "number", "description": "New on-hand quantity"},
            "location_id": {"type": "integer", "description": "Internal location id"},
            "confirmation_token": {"type": "string"},
        },
        "required": ["product_id", "quantity"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    required_groups=_GROUPS,
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "title": "Adjust on-hand quantity",
    },
)
def adjust_quantity(env, arguments):
    product = env["product.product"].browse(int(arguments["product_id"]))
    if not product.exists():
        raise ToolExecutionError("Product %s not found" % arguments["product_id"])
    if product.type != "product":
        raise ToolExecutionError(
            "Only storable products have tracked stock (product %s is '%s')."
            % (product.display_name, product.type)
        )
    if arguments.get("location_id"):
        location = env["stock.location"].browse(int(arguments["location_id"]))
        if not location.exists():
            raise ToolExecutionError("Location %s not found" % arguments["location_id"])
    else:
        location = _default_stock_location(env)

    new_qty = float(arguments["quantity"])
    current = product.with_context(location=location.id).qty_available
    preview = "Will SET on-hand of %s at %s to %s (currently %s)." % (
        product.display_name,
        location.complete_name,
        new_qty,
        current,
    )
    env["mcp.action.confirmation"].require("inventory.adjust_quantity", arguments, preview)

    Quant = env["stock.quant"].with_context(inventory_mode=True)
    quant = Quant.search(
        [("product_id", "=", product.id), ("location_id", "=", location.id)], limit=1
    )
    if quant:
        quant.inventory_quantity = new_qty
    else:
        quant = Quant.create(
            {
                "product_id": product.id,
                "location_id": location.id,
                "inventory_quantity": new_qty,
            }
        )
    quant.action_apply_inventory()
    return {
        "adjusted": True,
        "product_id": product.id,
        "location_id": location.id,
        "previous_on_hand": current,
        "new_on_hand": product.with_context(location=location.id).qty_available,
    }
