# -*- coding: utf-8 -*-
"""Curated MCP tools for the Purchase domain (``purchase.order``).

Thin wrappers over the standard ORM. Every tool executes as the authenticated
``res.users`` (the ``env`` handed in), so Purchase ACLs / record rules apply —
no ``sudo`` anywhere. Write tools use the shared propose/confirm contract. PDF
report links are produced via the core ``mcp.report.link`` facility (tokenized,
short-lived, rendered as the requesting user).
"""
from odoo.addons.mcp_server.mcp import constants
from odoo.addons.mcp_server.mcp.exceptions import ToolExecutionError
from odoo.addons.mcp_server.mcp.registry import tool

# Visible only to MCP users. Actual Purchase access is still enforced per call.
_GROUPS = ["mcp_server.group_mcp_user"]

_PURCHASE_REPORT = "purchase.action_report_purchase_order"
_BILL_REPORT = "account.account_invoices"
# draft = RFQ, sent = RFQ sent; both are still editable requests for quotation.
_EDITABLE_STATES = ("draft", "sent")

_ORDER_FIELDS = [
    "id",
    "name",
    "state",
    "partner_id",
    "date_order",
    "date_planned",
    "date_approve",
    "amount_untaxed",
    "amount_tax",
    "amount_total",
    "currency_id",
    "partner_ref",
    "user_id",
    "company_id",
    "notes",
    "create_date",
]

_LINE_FIELDS = [
    "id",
    "product_id",
    "name",
    "product_qty",
    "product_uom",
    "price_unit",
    "taxes_id",
    "date_planned",
    "price_subtotal",
    "price_total",
]

# ``qty_available`` is a stock-module field; keep to product/base fields so this
# add-on does not implicitly require ``stock``.
_PRODUCT_FIELDS = [
    "id",
    "name",
    "default_code",
    "standard_price",
    "list_price",
    "uom_po_id",
    "type",
    "categ_id",
    "barcode",
]

_STATE_ENUM = ["draft", "sent", "to approve", "purchase", "done", "cancel"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _get_order(env, order_id):
    order = env["purchase.order"].browse(int(order_id))
    if not order.exists():
        raise ToolExecutionError("Purchase order %s not found" % order_id)
    return order


def _require_editable(order):
    if order.state not in _EDITABLE_STATES:
        raise ToolExecutionError(
            "Order %s is in state '%s'; only draft/sent RFQs can be edited."
            % (order.name, order.state)
        )


def _pdf_filename(order):
    label = "RFQ" if order.state in _EDITABLE_STATES else "Purchase Order"
    return "%s - %s.pdf" % (label, order.name or order.id)


def _order_pdf_url(env, order):
    """Mint a downloadable PDF link (raises ToolExecutionError if unavailable)."""
    return env["mcp.report.link"].mint(
        _PURCHASE_REPORT, order, filename=_pdf_filename(order)
    )


def _order_detail(env, order, with_pdf=True):
    data = order.read(_ORDER_FIELDS)[0]
    data["order_lines"] = order.order_line.read(_LINE_FIELDS)
    if with_pdf:
        try:
            data["pdf_url"] = _order_pdf_url(env, order)
        except ToolExecutionError as exc:
            data["pdf_url"] = None
            data["pdf_note"] = str(exc)
    return data


def _line_command(line):
    if not line.get("product_id"):
        raise ToolExecutionError("Each order line needs a 'product_id'.")
    vals = {
        "product_id": int(line["product_id"]),
        "product_qty": float(line.get("quantity") or 1.0),
    }
    if line.get("description"):
        vals["name"] = line["description"]
    return (0, 0, vals)


def _apply_explicit_prices(order, lines_in):
    """Post-create: honour any explicit price_unit (which the onchange overrides)."""
    for line_rec, li in zip(order.order_line, lines_in):
        if li.get("price_unit") is not None:
            line_rec.price_unit = float(li["price_unit"])


_BILL_FIELDS = [
    "id",
    "name",
    "move_type",
    "state",
    "payment_state",
    "partner_id",
    "invoice_date",
    "invoice_date_due",
    "amount_untaxed",
    "amount_tax",
    "amount_total",
    "amount_residual",
    "currency_id",
    "ref",
    "invoice_origin",
]


def _bill_detail(env, bill, with_pdf=True):
    """Compact summary of a vendor bill (``account.move`` of type in_invoice)."""
    data = bill.read(_BILL_FIELDS)[0]
    if with_pdf:
        try:
            data["pdf_url"] = env["mcp.report.link"].mint(
                _BILL_REPORT, bill, filename="Vendor Bill - %s.pdf" % (bill.name or bill.id)
            )
        except ToolExecutionError as exc:
            data["pdf_url"] = None
            data["pdf_note"] = str(exc)
    return data


_ORDER_LINE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "product_id": {"type": "integer", "description": "product.product id"},
            "quantity": {"type": "number", "minimum": 0},
            "price_unit": {
                "type": "number",
                "description": "Optional unit price override; defaults to the "
                "vendor / product cost price",
            },
            "description": {"type": "string"},
        },
        "required": ["product_id"],
    },
}


# ---------------------------------------------------------------------------
# read tools
# ---------------------------------------------------------------------------
@tool(
    name="purchase.search_orders",
    description="Search purchase RFQs and orders. Filter by free text "
    "(order number / vendor reference), state, vendor, purchaser and "
    "order-date range.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Matched against order number and vendor reference",
            },
            "state": {
                "type": "string",
                "enum": _STATE_ENUM,
                "description": "draft=RFQ, sent=RFQ sent, purchase=purchase order",
            },
            "partner_id": {"type": "integer", "description": "Vendor res.partner id"},
            "purchaser_id": {"type": "integer", "description": "res.users id"},
            "date_from": {"type": "string", "description": "Order date >= (YYYY-MM-DD)"},
            "date_to": {"type": "string", "description": "Order date <= (YYYY-MM-DD)"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "offset": {"type": "integer", "minimum": 0},
        },
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Search purchase orders"},
)
def search_orders(env, arguments):
    domain = []
    query = (arguments.get("query") or "").strip()
    if query:
        domain += ["|", ("name", "ilike", query), ("partner_ref", "ilike", query)]
    if arguments.get("state"):
        domain.append(("state", "=", arguments["state"]))
    if arguments.get("partner_id"):
        domain.append(("partner_id", "=", int(arguments["partner_id"])))
    if arguments.get("purchaser_id"):
        domain.append(("user_id", "=", int(arguments["purchaser_id"])))
    if arguments.get("date_from"):
        domain.append(("date_order", ">=", arguments["date_from"]))
    if arguments.get("date_to"):
        domain.append(("date_order", "<=", arguments["date_to"]))

    limit = min(int(arguments.get("limit") or 20), 100)
    offset = int(arguments.get("offset") or 0)
    records = env["purchase.order"].search_read(
        domain, _ORDER_FIELDS, limit=limit, offset=offset, order="date_order desc"
    )
    total = env["purchase.order"].search_count(domain)
    return {"records": records, "returned": len(records), "total": total, "offset": offset}


@tool(
    name="purchase.get_order",
    description="Fetch full detail for one RFQ / purchase order, including its "
    "order lines and a downloadable PDF link.",
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "integer", "description": "purchase.order id"}},
        "required": ["id"],
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Get purchase order detail"},
)
def get_order(env, arguments):
    order = _get_order(env, arguments["id"])
    order.check_access_rule("read")
    return {"order": _order_detail(env, order)}


@tool(
    name="purchase.list_products",
    description="Look up purchasable products with their cost price. Search by "
    "name, internal reference or barcode.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "List purchasable products"},
)
def list_products(env, arguments):
    domain = [("purchase_ok", "=", True)]
    query = (arguments.get("query") or "").strip()
    if query:
        domain += [
            "|", "|",
            ("name", "ilike", query),
            ("default_code", "ilike", query),
            ("barcode", "ilike", query),
        ]
    limit = min(int(arguments.get("limit") or 20), 100)
    records = env["product.product"].search_read(
        domain, _PRODUCT_FIELDS, limit=limit, order="name"
    )
    return {"records": records, "returned": len(records)}


@tool(
    name="purchase.get_order_pdf",
    description="Get a short-lived, downloadable PDF link for an RFQ / purchase "
    "order. The link renders the report on demand and does not require an Odoo "
    "login to open.",
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "integer", "description": "purchase.order id"}},
        "required": ["id"],
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Get order PDF link"},
)
def get_order_pdf(env, arguments):
    order = _get_order(env, arguments["id"])
    order.check_access_rule("read")
    url = _order_pdf_url(env, order)
    return {
        "order_id": order.id,
        "order_name": order.name,
        "pdf_url": url,
        "filename": _pdf_filename(order),
        "expires_in": env["mcp.report.link"]._ttl(),
    }


# ---------------------------------------------------------------------------
# write tools (propose/confirm)
# ---------------------------------------------------------------------------
@tool(
    name="purchase.create_rfq",
    description="Create a draft RFQ (request for quotation) for a vendor with one "
    "or more product lines. Two-step: first call previews and returns a "
    "confirmation_token; re-call with the same arguments plus the token to "
    "create it.",
    input_schema={
        "type": "object",
        "properties": {
            "partner_id": {"type": "integer", "description": "Vendor res.partner id"},
            "order_lines": _ORDER_LINE_SCHEMA,
            "partner_ref": {"type": "string", "description": "Vendor reference"},
            "date_planned": {
                "type": "string",
                "description": "Expected arrival date (YYYY-MM-DD)",
            },
            "confirmation_token": {"type": "string"},
        },
        "required": ["partner_id", "order_lines"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    required_groups=_GROUPS,
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "title": "Create an RFQ",
    },
)
def create_rfq(env, arguments):
    partner = env["res.partner"].browse(int(arguments["partner_id"]))
    if not partner.exists():
        raise ToolExecutionError("Vendor %s not found" % arguments["partner_id"])
    lines_in = arguments.get("order_lines") or []
    if not lines_in:
        raise ToolExecutionError("Provide at least one order line.")
    commands = [_line_command(li) for li in lines_in]

    order_vals = {"partner_id": partner.id, "order_line": commands}
    if arguments.get("partner_ref"):
        order_vals["partner_ref"] = arguments["partner_ref"]
    if arguments.get("date_planned"):
        order_vals["date_planned"] = arguments["date_planned"]

    preview = "Will CREATE an RFQ for %s with %d line(s)." % (
        partner.display_name,
        len(commands),
    )
    env["mcp.action.confirmation"].require("purchase.create_rfq", arguments, preview)

    order = env["purchase.order"].create(order_vals)
    _apply_explicit_prices(order, lines_in)
    return {"created": True, "order": _order_detail(env, order)}


@tool(
    name="purchase.add_order_line",
    description="Add a product line to an existing draft/sent RFQ. "
    "Two-step propose/confirm.",
    input_schema={
        "type": "object",
        "properties": {
            "order_id": {"type": "integer", "description": "purchase.order id"},
            "product_id": {"type": "integer", "description": "product.product id"},
            "quantity": {"type": "number", "minimum": 0},
            "price_unit": {"type": "number", "description": "Optional unit price override"},
            "description": {"type": "string"},
            "confirmation_token": {"type": "string"},
        },
        "required": ["order_id", "product_id"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": False, "idempotentHint": False, "title": "Add order line"},
)
def add_order_line(env, arguments):
    order = _get_order(env, arguments["order_id"])
    _require_editable(order)
    product = env["product.product"].browse(int(arguments["product_id"]))
    if not product.exists():
        raise ToolExecutionError("Product %s not found" % arguments["product_id"])
    qty = float(arguments.get("quantity") or 1.0)
    preview = "Will ADD %s x %s to RFQ %s." % (
        qty,
        product.display_name,
        order.name,
    )
    env["mcp.action.confirmation"].require("purchase.add_order_line", arguments, preview)

    # name / price_unit / date_planned are computed (store=True, readonly=False)
    # from product_id & product_qty, so a plain create populates them.
    line = env["purchase.order.line"].create(
        {"order_id": order.id, "product_id": product.id, "product_qty": qty}
    )
    if arguments.get("price_unit") is not None:
        line.price_unit = float(arguments["price_unit"])
    return {"added": True, "order": _order_detail(env, order)}


@tool(
    name="purchase.update_order",
    description="Update header fields (vendor reference, planned date, note) "
    "on a draft/sent RFQ. Two-step propose/confirm.",
    input_schema={
        "type": "object",
        "properties": {
            "order_id": {"type": "integer", "description": "purchase.order id"},
            "partner_ref": {"type": "string"},
            "date_planned": {"type": "string", "description": "YYYY-MM-DD"},
            "notes": {"type": "string", "description": "Internal note / terms"},
            "confirmation_token": {"type": "string"},
        },
        "required": ["order_id"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": False, "idempotentHint": True, "title": "Update order"},
)
def update_order(env, arguments):
    order = _get_order(env, arguments["order_id"])
    _require_editable(order)
    values = {}
    for key in ("partner_ref", "date_planned", "notes"):
        if key in arguments and arguments[key] is not None:
            values[key] = arguments[key]
    if not values:
        raise ToolExecutionError(
            "No updatable fields supplied (partner_ref, date_planned, notes)."
        )
    preview = "Will UPDATE RFQ %s with: %s" % (order.name, values)
    env["mcp.action.confirmation"].require("purchase.update_order", arguments, preview)
    order.write(values)
    return {"updated": True, "order": _order_detail(env, order)}


@tool(
    name="purchase.send_rfq",
    description="Mark a draft RFQ as 'sent' to the vendor. Two-step propose/confirm.",
    input_schema={
        "type": "object",
        "properties": {
            "order_id": {"type": "integer", "description": "purchase.order id"},
            "confirmation_token": {"type": "string"},
        },
        "required": ["order_id"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": False, "idempotentHint": True, "title": "Mark RFQ sent"},
)
def send_rfq(env, arguments):
    order = _get_order(env, arguments["order_id"])
    if order.state not in ("draft", "sent"):
        raise ToolExecutionError(
            "Only a draft/sent RFQ can be marked sent (state=%s)." % order.state
        )
    preview = "Will mark RFQ %s as sent." % order.name
    env["mcp.action.confirmation"].require("purchase.send_rfq", arguments, preview)
    order.write({"state": "sent"})
    return {"sent": True, "order": _order_detail(env, order, with_pdf=False)}


@tool(
    name="purchase.confirm_order",
    description="Confirm an RFQ into a purchase order (draft/sent -> purchase). "
    "Two-step propose/confirm.",
    input_schema={
        "type": "object",
        "properties": {
            "order_id": {"type": "integer", "description": "purchase.order id"},
            "confirmation_token": {"type": "string"},
        },
        "required": ["order_id"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    required_groups=_GROUPS,
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "title": "Confirm purchase order",
    },
)
def confirm_order(env, arguments):
    order = _get_order(env, arguments["order_id"])
    if order.state not in ("draft", "sent"):
        raise ToolExecutionError(
            "Only a draft/sent RFQ can be confirmed (state=%s)." % order.state
        )
    preview = "Will CONFIRM RFQ %s for %s totalling %s %s." % (
        order.name,
        order.partner_id.display_name,
        order.amount_total,
        order.currency_id.name or "",
    )
    env["mcp.action.confirmation"].require("purchase.confirm_order", arguments, preview)
    order.button_confirm()
    return {"confirmed": True, "order": _order_detail(env, order)}


@tool(
    name="purchase.cancel_order",
    description="Cancel an RFQ or purchase order. Two-step propose/confirm.",
    input_schema={
        "type": "object",
        "properties": {
            "order_id": {"type": "integer", "description": "purchase.order id"},
            "confirmation_token": {"type": "string"},
        },
        "required": ["order_id"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    required_groups=_GROUPS,
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "title": "Cancel order",
    },
)
def cancel_order(env, arguments):
    order = _get_order(env, arguments["order_id"])
    if order.state == "cancel":
        raise ToolExecutionError("Order %s is already cancelled." % order.name)
    preview = "Will CANCEL order %s (%s)." % (order.name, order.partner_id.display_name)
    env["mcp.action.confirmation"].require("purchase.cancel_order", arguments, preview)
    order.button_cancel()
    return {"cancelled": True, "order": _order_detail(env, order, with_pdf=False)}


@tool(
    name="purchase.create_vendor_bill",
    description="Generate a draft vendor bill from a confirmed purchase order "
    "(purchase.order.action_create_invoice), so a supplier's invoice can be "
    "recorded against the PO. The PO must be confirmed and have something left "
    "to invoice. Optionally stamp the vendor's bill date and reference on the "
    "created draft. Two-step propose/confirm; the resulting bill stays in draft "
    "for review (post it with accounting.post_invoice).",
    input_schema={
        "type": "object",
        "properties": {
            "order_id": {"type": "integer", "description": "purchase.order id"},
            "invoice_date": {
                "type": "string",
                "description": "Bill date as printed on the vendor invoice (YYYY-MM-DD)",
            },
            "ref": {
                "type": "string",
                "description": "Vendor's bill reference / invoice number",
            },
            "confirmation_token": {"type": "string"},
        },
        "required": ["order_id"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    required_groups=_GROUPS,
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "title": "Create vendor bill from PO",
    },
)
def create_vendor_bill(env, arguments):
    order = _get_order(env, arguments["order_id"])
    if order.state not in ("purchase", "done"):
        raise ToolExecutionError(
            "Only a confirmed purchase order can be billed (state=%s). "
            "Confirm the RFQ first with purchase.confirm_order." % order.state
        )
    if order.invoice_status != "to invoice":
        raise ToolExecutionError(
            "Order %s has nothing to invoice (invoice_status=%s). If its products "
            "use a 'on received quantities' control policy, register the receipt "
            "before billing." % (order.name, order.invoice_status)
        )

    preview = "Will CREATE a draft vendor bill from PO %s for %s totalling %s %s." % (
        order.name,
        order.partner_id.display_name,
        order.amount_total,
        order.currency_id.name or "",
    )
    env["mcp.action.confirmation"].require(
        "purchase.create_vendor_bill", arguments, preview
    )

    existing = set(order.invoice_ids.ids)
    order.action_create_invoice()
    order.invalidate_recordset(["invoice_ids"])
    new_bills = order.invoice_ids.filtered(lambda m: m.id not in existing)
    if not new_bills:
        raise ToolExecutionError("No vendor bill was created for PO %s." % order.name)
    bill = new_bills[0]

    values = {}
    if arguments.get("invoice_date"):
        values["invoice_date"] = arguments["invoice_date"]
    if arguments.get("ref"):
        values["ref"] = arguments["ref"]
    if values:
        bill.write(values)

    return {
        "created": True,
        "order_id": order.id,
        "order_name": order.name,
        "invoice_status": order.invoice_status,
        "bill": _bill_detail(env, bill),
    }
