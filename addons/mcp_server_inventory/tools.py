# -*- coding: utf-8 -*-
"""Curated MCP tools for the Inventory domain (``stock.picking`` / stock).

Thin wrappers over the standard ORM. Every tool executes as the authenticated
``res.users`` (the ``env`` handed in), so Inventory ACLs / record rules apply -
no ``sudo`` anywhere. Write tools use the shared propose/confirm contract.
Delivery-slip PDF links are produced via the core ``mcp.report.link`` facility.
"""
from datetime import timedelta

from odoo import fields

from odoo.addons.mcp_server.mcp import constants
from odoo.addons.mcp_server.mcp.exceptions import ToolExecutionError
from odoo.addons.mcp_server.mcp.registry import tool

# Visible only to MCP users. Actual Inventory access is enforced per call.
_GROUPS = ["mcp_server.group_mcp_user"]

_DELIVERY_REPORT = "stock.action_report_delivery"
_PICKING_STATES = ["draft", "waiting", "confirmed", "assigned", "done", "cancel"]

# ``product_expiry`` adds these datetime fields to ``stock.lot``.
_LOT_DATE_FIELDS = ["expiration_date", "use_date", "removal_date", "alert_date"]
_DEFAULT_SOON_DAYS = 30

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


def _quant_location_domain(env, arguments):
    """Restrict ``stock.quant`` to the requested scope (location > warehouse >
    all internal locations)."""
    if arguments.get("location_id"):
        return [("location_id", "child_of", int(arguments["location_id"]))]
    if arguments.get("warehouse_id"):
        wh = env["stock.warehouse"].browse(int(arguments["warehouse_id"]))
        if wh.exists() and wh.view_location_id:
            return [("location_id", "child_of", wh.view_location_id.id)]
    return [("location_id.usage", "=", "internal")]


def _quant_product_filter(arguments):
    """Same product text / id filter as the tool, expressed on ``stock.quant``."""
    dom = []
    if arguments.get("product_id"):
        dom.append(("product_id", "=", int(arguments["product_id"])))
    query = (arguments.get("query") or "").strip()
    if query:
        dom += [
            "|", "|",
            ("product_id.name", "ilike", query),
            ("product_id.default_code", "ilike", query),
            ("product_id.barcode", "ilike", query),
        ]
    return dom


def _location_breakdown(env, product_id, arguments):
    """Per-location on-hand breakdown for one product (locations with stock)."""
    domain = [("product_id", "=", product_id), ("quantity", ">", 0)]
    domain += _quant_location_domain(env, arguments)
    groups = env["stock.quant"].read_group(
        domain, ["quantity:sum", "reserved_quantity:sum"], ["location_id"]
    )
    rows = []
    for g in groups:
        if not g.get("location_id"):
            continue
        on_hand = g.get("quantity") or 0.0
        reserved = g.get("reserved_quantity") or 0.0
        rows.append(
            {
                "location_id": g["location_id"][0],
                "location": g["location_id"][1],
                "qty_on_hand": on_hand,
                "qty_reserved": reserved,
                "qty_available": on_hand - reserved,
            }
        )
    rows.sort(key=lambda r: r["qty_on_hand"], reverse=True)
    return rows


# ---------------------------------------------------------------------------
# expiry helpers
# ---------------------------------------------------------------------------
def _expiry_enabled(env):
    """True when the ``product_expiry`` app is installed (lots carry dates).

    Odoo 16 renamed ``stock.production.lot`` to ``stock.lot``; guard against the
    model being absent so this never raises a raw ``KeyError``.
    """
    if "stock.lot" not in env:
        return False
    return "expiration_date" in env["stock.lot"]._fields


def _require_expiry(env):
    if not _expiry_enabled(env):
        raise ToolExecutionError(
            "Expiration tracking is not enabled on this database. Install the "
            "'Expiration Dates' app (product_expiry) to track lot/serial "
            "expiry."
        )


def _lot_dates(lot):
    """Serialise the product_expiry date fields of a lot (ISO strings)."""
    out = {}
    for fname in _LOT_DATE_FIELDS:
        val = lot[fname]
        out[fname] = fields.Datetime.to_string(val) if val else None
    return out


def _expiry_status(days, soon_days):
    if days is None:
        return "no_expiry"
    if days < 0:
        return "expired"
    if days <= soon_days:
        return "expiring_soon"
    return "ok"


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
    "storable products. Products that are on hand are prioritised (returned "
    "first, most stock first). Filter by product text, optionally scope to a "
    "warehouse or location, restrict to on-hand only, and/or break the on-hand "
    "quantity down per location.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Matched against product name / internal reference / barcode",
            },
            "product_id": {"type": "integer", "description": "Specific product.product id"},
            "warehouse_id": {"type": "integer"},
            "location_id": {
                "type": "integer",
                "description": "Scope to this location (includes its sub-locations)",
            },
            "only_on_hand": {
                "type": "boolean",
                "description": "Only return products with a positive on-hand "
                "quantity in scope (default false).",
            },
            "group_by_location": {
                "type": "boolean",
                "description": "Include a per-location on-hand breakdown "
                "('by_location') for each product (default false).",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Check stock"},
)
def check_stock(env, arguments):
    only_on_hand = bool(arguments.get("only_on_hand"))
    group_by_location = bool(arguments.get("group_by_location"))
    limit = min(int(arguments.get("limit") or 20), 100)

    ctx = {}
    if arguments.get("warehouse_id"):
        ctx["warehouse"] = int(arguments["warehouse_id"])
    if arguments.get("location_id"):
        ctx["location"] = int(arguments["location_id"])
    Product = env["product.product"].with_context(**ctx)

    # Rank products with stock on hand first, by quantity within the requested
    # scope. Driven by stock.quant so the ordering reflects real on-hand and we
    # do not have to scan the whole catalogue.
    quant_domain = [("quantity", ">", 0)]
    quant_domain += _quant_location_domain(env, arguments)
    quant_domain += _quant_product_filter(arguments)
    grouped = env["stock.quant"].read_group(
        quant_domain, ["product_id", "quantity:sum"], ["product_id"]
    )
    grouped.sort(key=lambda g: g.get("quantity") or 0.0, reverse=True)
    onhand_ids = [g["product_id"][0] for g in grouped if g.get("product_id")]

    ordered_ids = onhand_ids[:limit]

    # Optionally pad with matching storable products that have no stock in scope
    # (they rank last), so callers still see catalogue items unless they asked
    # for on-hand only.
    if not only_on_hand and len(ordered_ids) < limit:
        domain = [("type", "=", "product"), ("id", "not in", onhand_ids)]
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
        remaining = Product.search(
            domain, limit=limit - len(ordered_ids), order="name"
        )
        ordered_ids = ordered_ids + remaining.ids

    rows = []
    for pid in ordered_ids:
        p = Product.browse(pid)
        row = {
            "id": p.id,
            "name": p.display_name,
            "default_code": p.default_code or None,
            "uom": p.uom_id.name,
            "qty_on_hand": p.qty_available,
            "qty_forecast": p.virtual_available,
            "qty_available": p.free_qty,
        }
        if group_by_location:
            row["by_location"] = _location_breakdown(env, p.id, arguments)
        rows.append(row)

    return {
        "products": rows,
        "returned": len(rows),
        "on_hand_count": len(onhand_ids),
        "scope": ctx or "all internal locations",
        "sorted_by": "on-hand first",
    }


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


@tool(
    name="inventory.check_expiry",
    description="Report on-hand stock that is expiring or already expired, based "
    "on lot/serial expiration dates (requires the 'Expiration Dates' app). "
    "Answers questions like 'is any stock about to expire and when'. Rows are "
    "sorted soonest-expiry first. Filter by product text/id, scope to a "
    "warehouse or location, limit to a number of days ahead ('within_days'), "
    "optionally exclude already-expired stock, and group the result by lot "
    "(default, shows where each expiring lot sits) or by product.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Matched against product name / internal reference / barcode",
            },
            "product_id": {"type": "integer", "description": "Specific product.product id"},
            "warehouse_id": {"type": "integer"},
            "location_id": {
                "type": "integer",
                "description": "Scope to this location (includes its sub-locations)",
            },
            "within_days": {
                "type": "integer",
                "minimum": 0,
                "description": "Only include lots expiring on/before today + N days "
                "(expired lots are still included unless include_expired is false). "
                "Omit to include all lots that carry an expiration date.",
            },
            "include_expired": {
                "type": "boolean",
                "description": "Include lots whose expiration date has already passed "
                "(default true).",
            },
            "only_on_hand": {
                "type": "boolean",
                "description": "Only count lots with a positive on-hand quantity in "
                "scope (default true).",
            },
            "group_by": {
                "type": "string",
                "enum": ["lot", "product"],
                "description": "Granularity of the returned rows (default 'lot').",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Check stock expiry"},
)
def check_expiry(env, arguments):
    _require_expiry(env)
    Lot = env["stock.lot"]
    today = fields.Date.context_today(env.user)
    group_by = arguments.get("group_by") or "lot"
    include_expired = arguments.get("include_expired", True)
    only_on_hand = arguments.get("only_on_hand", True)
    within_days = arguments.get("within_days")
    within_days = int(within_days) if within_days is not None else None
    soon_days = within_days if within_days is not None else _DEFAULT_SOON_DAYS
    limit = min(int(arguments.get("limit") or 50), 200)

    quant_domain = [
        ("lot_id", "!=", False),
        ("lot_id.expiration_date", "!=", False),
    ]
    if only_on_hand:
        quant_domain.append(("quantity", ">", 0))
    quant_domain += _quant_location_domain(env, arguments)
    quant_domain += _quant_product_filter(arguments)

    groups = env["stock.quant"].read_group(
        quant_domain,
        ["quantity:sum", "reserved_quantity:sum"],
        ["lot_id", "location_id"],
        lazy=False,
    )

    lot_cache = {}

    def _lot(lot_id):
        if lot_id not in lot_cache:
            lot_cache[lot_id] = Lot.browse(lot_id)
        return lot_cache[lot_id]

    lot_rows = []
    for g in groups:
        if not g.get("lot_id") or not g.get("location_id"):
            continue
        lot = _lot(g["lot_id"][0])
        exp = lot.expiration_date
        if not exp:
            continue
        days = (exp.date() - today).days
        if not include_expired and days < 0:
            continue
        if within_days is not None and days > within_days:
            continue
        on_hand = g.get("quantity") or 0.0
        reserved = g.get("reserved_quantity") or 0.0
        product = lot.product_id
        row = {
            "lot_id": lot.id,
            "lot_name": lot.name,
            "product_id": product.id,
            "product": product.display_name,
            "default_code": product.default_code or None,
            "uom": product.uom_id.name,
            "location_id": g["location_id"][0],
            "location": g["location_id"][1],
            "qty_on_hand": on_hand,
            "qty_reserved": reserved,
            "qty_available": on_hand - reserved,
            "days_to_expiry": days,
            "status": _expiry_status(days, soon_days),
        }
        row.update(_lot_dates(lot))
        lot_rows.append(row)

    lot_rows.sort(key=lambda r: (r["days_to_expiry"], -r["qty_on_hand"]))

    expired_count = sum(1 for r in lot_rows if r["status"] == "expired")
    expiring_soon_count = sum(1 for r in lot_rows if r["status"] == "expiring_soon")
    nearest = lot_rows[0]["expiration_date"] if lot_rows else None

    if group_by == "product":
        prod_map = {}
        for r in lot_rows:
            entry = prod_map.get(r["product_id"])
            if entry is None:
                entry = {
                    "product_id": r["product_id"],
                    "product": r["product"],
                    "default_code": r["default_code"],
                    "uom": r["uom"],
                    "qty_on_hand": 0.0,
                    "qty_reserved": 0.0,
                    "qty_available": 0.0,
                    "expired_qty": 0.0,
                    "lot_ids": set(),
                    "nearest_days_to_expiry": r["days_to_expiry"],
                    "nearest_expiration_date": r["expiration_date"],
                }
                prod_map[r["product_id"]] = entry
            entry["qty_on_hand"] += r["qty_on_hand"]
            entry["qty_reserved"] += r["qty_reserved"]
            entry["qty_available"] += r["qty_available"]
            if r["status"] == "expired":
                entry["expired_qty"] += r["qty_on_hand"]
            entry["lot_ids"].add(r["lot_id"])
            if r["days_to_expiry"] < entry["nearest_days_to_expiry"]:
                entry["nearest_days_to_expiry"] = r["days_to_expiry"]
                entry["nearest_expiration_date"] = r["expiration_date"]
        rows = []
        for entry in prod_map.values():
            entry["lot_count"] = len(entry.pop("lot_ids"))
            entry["status"] = _expiry_status(
                entry["nearest_days_to_expiry"], soon_days
            )
            rows.append(entry)
        rows.sort(key=lambda r: (r["nearest_days_to_expiry"], -r["qty_on_hand"]))
    else:
        rows = lot_rows

    rows = rows[:limit]

    if arguments.get("location_id"):
        scope = "location %s" % arguments["location_id"]
    elif arguments.get("warehouse_id"):
        scope = "warehouse %s" % arguments["warehouse_id"]
    else:
        scope = "all internal locations"

    return {
        "group_by": group_by,
        "rows": rows,
        "returned": len(rows),
        "summary": {
            "as_of": fields.Date.to_string(today),
            "within_days": within_days,
            "expired_lot_count": expired_count,
            "expiring_soon_lot_count": expiring_soon_count,
            "nearest_expiration_date": nearest,
        },
        "scope": scope,
    }


@tool(
    name="inventory.get_lot",
    description="Fetch full detail for one lot/serial number: its product, "
    "expiration / use / removal / alert dates, expiry-alert flag, and a "
    "per-location on-hand breakdown. Requires the 'Expiration Dates' app for "
    "the date fields.",
    input_schema={
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "stock.lot id"}
        },
        "required": ["id"],
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Get lot / serial detail"},
)
def get_lot(env, arguments):
    _require_expiry(env)
    lot = env["stock.lot"].browse(int(arguments["id"]))
    if not lot.exists():
        raise ToolExecutionError("Lot %s not found" % arguments["id"])
    lot.check_access_rule("read")

    today = fields.Date.context_today(env.user)
    exp = lot.expiration_date
    days = (exp.date() - today).days if exp else None

    groups = env["stock.quant"].read_group(
        [
            ("lot_id", "=", lot.id),
            ("quantity", ">", 0),
            ("location_id.usage", "=", "internal"),
        ],
        ["quantity:sum", "reserved_quantity:sum"],
        ["location_id"],
    )
    by_location = []
    total = 0.0
    for g in groups:
        if not g.get("location_id"):
            continue
        on_hand = g.get("quantity") or 0.0
        reserved = g.get("reserved_quantity") or 0.0
        total += on_hand
        by_location.append(
            {
                "location_id": g["location_id"][0],
                "location": g["location_id"][1],
                "qty_on_hand": on_hand,
                "qty_reserved": reserved,
                "qty_available": on_hand - reserved,
            }
        )
    by_location.sort(key=lambda r: r["qty_on_hand"], reverse=True)

    data = {
        "id": lot.id,
        "name": lot.name,
        "product_id": lot.product_id.id,
        "product": lot.product_id.display_name,
        "default_code": lot.product_id.default_code or None,
        "company_id": lot.company_id.id or None,
        "days_to_expiry": days,
        "status": _expiry_status(days, _DEFAULT_SOON_DAYS),
        "product_expiry_alert": bool(lot.product_expiry_alert),
        "total_on_hand": total,
        "by_location": by_location,
    }
    data.update(_lot_dates(lot))
    return {"lot": data}


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
