# -*- coding: utf-8 -*-
"""Curated MCP tools for the Sales domain (``sale.order``).

Thin wrappers over the standard ORM. Every tool executes as the authenticated
``res.users`` (the ``env`` handed in), so Sales ACLs / record rules apply — no
``sudo`` anywhere. Write tools use the shared propose/confirm contract. PDF
report links are produced via the core ``mcp.report.link`` facility (tokenized,
short-lived, rendered as the requesting user).
"""
from odoo.addons.mcp_server.mcp import constants
from odoo.addons.mcp_server.mcp.exceptions import ToolExecutionError
from odoo.addons.mcp_server.mcp.registry import tool

# Visible only to MCP users. Actual Sales access is still enforced per call.
_GROUPS = ["mcp_server.group_mcp_user"]

_SALE_REPORT = "sale.action_report_saleorder"
_EDITABLE_STATES = ("draft", "sent")

_ORDER_FIELDS = [
    "id",
    "name",
    "state",
    "partner_id",
    "date_order",
    "validity_date",
    "amount_untaxed",
    "amount_tax",
    "amount_total",
    "currency_id",
    "client_order_ref",
    "user_id",
    "team_id",
    "company_id",
    "note",
    "create_date",
]

_LINE_FIELDS = [
    "id",
    "product_id",
    "name",
    "product_uom_qty",
    "product_uom",
    "price_unit",
    "discount",
    "tax_id",
    "price_subtotal",
    "price_total",
]

# ``qty_available`` is a stock-module field; keep to product/base fields so this
# add-on does not implicitly require ``stock``.
_PRODUCT_FIELDS = [
    "id",
    "name",
    "default_code",
    "list_price",
    "uom_id",
    "type",
    "categ_id",
    "barcode",
]

_STATE_ENUM = ["draft", "sent", "sale", "done", "cancel"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _get_order(env, order_id):
    order = env["sale.order"].browse(int(order_id))
    if not order.exists():
        raise ToolExecutionError("Sale order %s not found" % order_id)
    return order


def _require_editable(order):
    if order.state not in _EDITABLE_STATES:
        raise ToolExecutionError(
            "Order %s is in state '%s'; only draft/sent quotations can be edited."
            % (order.name, order.state)
        )


def _pdf_filename(order):
    label = "Quotation" if order.state in _EDITABLE_STATES else "Sale Order"
    return "%s - %s.pdf" % (label, order.name or order.id)


def _order_pdf_url(env, order):
    """Mint a downloadable PDF link (raises ToolExecutionError if unavailable)."""
    return env["mcp.report.link"].mint(
        _SALE_REPORT, order, filename=_pdf_filename(order)
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
        "product_uom_qty": float(line.get("quantity") or 1.0),
    }
    if line.get("description"):
        vals["name"] = line["description"]
    return (0, 0, vals)


def _apply_explicit_prices(order, lines_in):
    """Post-create: honour any explicit price_unit (which the compute overrides)."""
    for line_rec, li in zip(order.order_line, lines_in):
        if li.get("price_unit") is not None:
            line_rec.price_unit = float(li["price_unit"])


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
                "product's pricelist price",
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
    name="sales.search_orders",
    description="Search sales quotations and orders. Filter by free text "
    "(order number / customer reference), state, customer, salesperson and "
    "order-date range.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Matched against order number and customer reference",
            },
            "state": {
                "type": "string",
                "enum": _STATE_ENUM,
                "description": "draft=quotation, sent=quotation sent, sale=order",
            },
            "partner_id": {"type": "integer", "description": "Customer res.partner id"},
            "salesperson_id": {"type": "integer", "description": "res.users id"},
            "date_from": {"type": "string", "description": "Order date >= (YYYY-MM-DD)"},
            "date_to": {"type": "string", "description": "Order date <= (YYYY-MM-DD)"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "offset": {"type": "integer", "minimum": 0},
        },
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Search sales orders"},
)
def search_orders(env, arguments):
    domain = []
    query = (arguments.get("query") or "").strip()
    if query:
        domain += ["|", ("name", "ilike", query), ("client_order_ref", "ilike", query)]
    if arguments.get("state"):
        domain.append(("state", "=", arguments["state"]))
    if arguments.get("partner_id"):
        domain.append(("partner_id", "=", int(arguments["partner_id"])))
    if arguments.get("salesperson_id"):
        domain.append(("user_id", "=", int(arguments["salesperson_id"])))
    if arguments.get("date_from"):
        domain.append(("date_order", ">=", arguments["date_from"]))
    if arguments.get("date_to"):
        domain.append(("date_order", "<=", arguments["date_to"]))

    limit = min(int(arguments.get("limit") or 20), 100)
    offset = int(arguments.get("offset") or 0)
    records = env["sale.order"].search_read(
        domain, _ORDER_FIELDS, limit=limit, offset=offset, order="date_order desc"
    )
    total = env["sale.order"].search_count(domain)
    return {"records": records, "returned": len(records), "total": total, "offset": offset}


@tool(
    name="sales.get_order",
    description="Fetch full detail for one quotation / sale order, including its "
    "order lines and a downloadable PDF link.",
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "integer", "description": "sale.order id"}},
        "required": ["id"],
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Get sales order detail"},
)
def get_order(env, arguments):
    order = _get_order(env, arguments["id"])
    order.check_access_rule("read")
    return {"order": _order_detail(env, order)}


@tool(
    name="sales.list_products",
    description="Look up sellable products with their sales price. Search by "
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
    annotations={"readOnlyHint": True, "title": "List sellable products"},
)
def list_products(env, arguments):
    domain = [("sale_ok", "=", True)]
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
    name="sales.get_order_pdf",
    description="Get a short-lived, downloadable PDF link for a quotation / sale "
    "order. The link renders the report on demand and does not require an Odoo "
    "login to open.",
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "integer", "description": "sale.order id"}},
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
    name="sales.create_quotation",
    description="Create a draft quotation for a customer with one or more product "
    "lines. Two-step: first call previews and returns a confirmation_token; "
    "re-call with the same arguments plus the token to create it.",
    input_schema={
        "type": "object",
        "properties": {
            "partner_id": {"type": "integer", "description": "Customer res.partner id"},
            "order_lines": _ORDER_LINE_SCHEMA,
            "client_order_ref": {"type": "string", "description": "Customer reference"},
            "validity_date": {"type": "string", "description": "Quotation expiry (YYYY-MM-DD)"},
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
        "title": "Create a quotation",
    },
)
def create_quotation(env, arguments):
    partner = env["res.partner"].browse(int(arguments["partner_id"]))
    if not partner.exists():
        raise ToolExecutionError("Customer %s not found" % arguments["partner_id"])
    lines_in = arguments.get("order_lines") or []
    if not lines_in:
        raise ToolExecutionError("Provide at least one order line.")
    commands = [_line_command(li) for li in lines_in]

    order_vals = {"partner_id": partner.id, "order_line": commands}
    if arguments.get("client_order_ref"):
        order_vals["client_order_ref"] = arguments["client_order_ref"]
    if arguments.get("validity_date"):
        order_vals["validity_date"] = arguments["validity_date"]

    preview = "Will CREATE a quotation for %s with %d line(s)." % (
        partner.display_name,
        len(commands),
    )
    env["mcp.action.confirmation"].require("sales.create_quotation", arguments, preview)

    order = env["sale.order"].create(order_vals)
    _apply_explicit_prices(order, lines_in)
    return {"created": True, "order": _order_detail(env, order)}


@tool(
    name="sales.add_order_line",
    description="Add a product line to an existing draft/sent quotation. "
    "Two-step propose/confirm.",
    input_schema={
        "type": "object",
        "properties": {
            "order_id": {"type": "integer", "description": "sale.order id"},
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
    preview = "Will ADD %s x %s to quotation %s." % (
        qty,
        product.display_name,
        order.name,
    )
    env["mcp.action.confirmation"].require("sales.add_order_line", arguments, preview)

    line = env["sale.order.line"].create(
        {"order_id": order.id, "product_id": product.id, "product_uom_qty": qty}
    )
    if arguments.get("price_unit") is not None:
        line.price_unit = float(arguments["price_unit"])
    return {"added": True, "order": _order_detail(env, order)}


@tool(
    name="sales.update_order",
    description="Update header fields (customer reference, validity date, note) "
    "on a draft/sent quotation. Two-step propose/confirm.",
    input_schema={
        "type": "object",
        "properties": {
            "order_id": {"type": "integer", "description": "sale.order id"},
            "client_order_ref": {"type": "string"},
            "validity_date": {"type": "string", "description": "YYYY-MM-DD"},
            "note": {"type": "string", "description": "Terms & conditions / note"},
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
    for key in ("client_order_ref", "validity_date", "note"):
        if key in arguments and arguments[key] is not None:
            values[key] = arguments[key]
    if not values:
        raise ToolExecutionError(
            "No updatable fields supplied (client_order_ref, validity_date, note)."
        )
    preview = "Will UPDATE quotation %s with: %s" % (order.name, values)
    env["mcp.action.confirmation"].require("sales.update_order", arguments, preview)
    order.write(values)
    return {"updated": True, "order": _order_detail(env, order)}


@tool(
    name="sales.set_quotation_sent",
    description="Mark a draft quotation as 'sent'. Two-step propose/confirm.",
    input_schema={
        "type": "object",
        "properties": {
            "order_id": {"type": "integer", "description": "sale.order id"},
            "confirmation_token": {"type": "string"},
        },
        "required": ["order_id"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": False, "idempotentHint": True, "title": "Mark quotation sent"},
)
def set_quotation_sent(env, arguments):
    order = _get_order(env, arguments["order_id"])
    if order.state not in ("draft", "sent"):
        raise ToolExecutionError(
            "Only a draft/sent quotation can be marked sent (state=%s)." % order.state
        )
    preview = "Will mark quotation %s as sent." % order.name
    env["mcp.action.confirmation"].require("sales.set_quotation_sent", arguments, preview)
    order.action_quotation_sent()
    return {"sent": True, "order": _order_detail(env, order, with_pdf=False)}


@tool(
    name="sales.confirm_order",
    description="Confirm a quotation into a sale order (draft/sent -> sale). "
    "Two-step propose/confirm.",
    input_schema={
        "type": "object",
        "properties": {
            "order_id": {"type": "integer", "description": "sale.order id"},
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
        "title": "Confirm sale order",
    },
)
def confirm_order(env, arguments):
    order = _get_order(env, arguments["order_id"])
    if order.state not in ("draft", "sent"):
        raise ToolExecutionError(
            "Only a draft/sent quotation can be confirmed (state=%s)." % order.state
        )
    preview = "Will CONFIRM quotation %s for %s totalling %s %s." % (
        order.name,
        order.partner_id.display_name,
        order.amount_total,
        order.currency_id.name or "",
    )
    env["mcp.action.confirmation"].require("sales.confirm_order", arguments, preview)
    order.action_confirm()
    return {"confirmed": True, "order": _order_detail(env, order)}


@tool(
    name="sales.cancel_order",
    description="Cancel a quotation or sale order. Two-step propose/confirm.",
    input_schema={
        "type": "object",
        "properties": {
            "order_id": {"type": "integer", "description": "sale.order id"},
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
    env["mcp.action.confirmation"].require("sales.cancel_order", arguments, preview)
    order.action_cancel()
    return {"cancelled": True, "order": _order_detail(env, order, with_pdf=False)}
