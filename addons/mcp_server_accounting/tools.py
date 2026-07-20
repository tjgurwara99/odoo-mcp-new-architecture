# -*- coding: utf-8 -*-
"""Curated MCP tools for the Invoicing/Accounting domain (``account.move``).

Thin wrappers over the standard ORM. Every tool executes as the authenticated
``res.users`` (the ``env`` handed in), so Accounting ACLs / record rules apply -
no ``sudo`` anywhere. The whole tool set is only *visible* to users holding the
Invoicing access group (real ACL enforcement still happens per call). Write
tools use the shared propose/confirm contract. Invoice PDF report links are
produced via the core ``mcp.report.link`` facility (tokenized, short-lived,
rendered as the requesting user).
"""
from odoo import fields

from odoo.addons.mcp_server.mcp import constants
from odoo.addons.mcp_server.mcp.exceptions import ToolExecutionError
from odoo.addons.mcp_server.mcp.registry import tool

# Visible only to MCP users who also have Invoicing access. Actual Accounting
# access is still enforced per call by the ORM.
_GROUPS = ["mcp_server.group_mcp_user", "account.group_account_invoice"]

_INVOICE_REPORT = "account.account_invoices"

# Customer & vendor invoices + credit notes (excludes plain journal entries).
_INVOICE_TYPES = ("out_invoice", "out_refund", "in_invoice", "in_refund")
_MOVE_TYPE_ENUM = list(_INVOICE_TYPES)
_STATE_ENUM = ["draft", "posted", "cancel"]
_PAYMENT_STATE_ENUM = [
    "not_paid",
    "in_payment",
    "paid",
    "partial",
    "reversed",
    "invoicing_legacy",
]

_INVOICE_FIELDS = [
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
    "journal_id",
    "invoice_user_id",
    "company_id",
    "narration",
    "create_date",
]

_LINE_FIELDS = [
    "id",
    "product_id",
    "name",
    "quantity",
    "product_uom_id",
    "price_unit",
    "discount",
    "tax_ids",
    "account_id",
    "price_subtotal",
    "price_total",
]

_PRODUCT_FIELDS = [
    "id",
    "name",
    "default_code",
    "list_price",
    "standard_price",
    "uom_id",
    "type",
    "categ_id",
    "barcode",
]

# Human labels for report/preview text.
_TYPE_LABELS = {
    "out_invoice": "Customer Invoice",
    "out_refund": "Customer Credit Note",
    "in_invoice": "Vendor Bill",
    "in_refund": "Vendor Credit Note",
    "entry": "Journal Entry",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _get_invoice(env, invoice_id, allowed_types=_INVOICE_TYPES):
    move = env["account.move"].browse(int(invoice_id))
    if not move.exists():
        raise ToolExecutionError("Invoice %s not found" % invoice_id)
    if allowed_types and move.move_type not in allowed_types:
        raise ToolExecutionError(
            "Record %s is a %s, not an invoice/bill."
            % (move.name or move.id, _TYPE_LABELS.get(move.move_type, move.move_type))
        )
    return move


def _type_label(move):
    return _TYPE_LABELS.get(move.move_type, move.move_type)


def _pdf_filename(move):
    return "%s - %s.pdf" % (_type_label(move), move.name or move.id)


def _invoice_pdf_url(env, move):
    """Mint a downloadable PDF link (raises ToolExecutionError if unavailable)."""
    return env["mcp.report.link"].mint(
        _INVOICE_REPORT, move, filename=_pdf_filename(move)
    )


def _invoice_detail(env, move, with_pdf=True):
    data = move.read(_INVOICE_FIELDS)[0]
    data["type_label"] = _type_label(move)
    data["invoice_lines"] = move.invoice_line_ids.read(_LINE_FIELDS)
    if with_pdf:
        try:
            data["pdf_url"] = _invoice_pdf_url(env, move)
        except ToolExecutionError as exc:
            data["pdf_url"] = None
            data["pdf_note"] = str(exc)
    return data


def _line_command(env, line):
    """Build an ``invoice_line_ids`` create command from a tool line dict."""
    vals = {}
    if line.get("product_id"):
        product = env["product.product"].browse(int(line["product_id"]))
        if not product.exists():
            raise ToolExecutionError("Product %s not found" % line["product_id"])
        vals["product_id"] = product.id
    if line.get("description"):
        vals["name"] = line["description"]
    elif not line.get("product_id"):
        raise ToolExecutionError(
            "Each invoice line needs a 'product_id' or a 'description'."
        )
    if line.get("quantity") is not None:
        vals["quantity"] = float(line["quantity"])
    if line.get("price_unit") is not None:
        vals["price_unit"] = float(line["price_unit"])
    return (0, 0, vals)


_INVOICE_LINE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "product_id": {
                "type": "integer",
                "description": "product.product id (drives account, taxes, price)",
            },
            "description": {
                "type": "string",
                "description": "Line label; required if no product_id is given",
            },
            "quantity": {"type": "number", "minimum": 0},
            "price_unit": {
                "type": "number",
                "description": "Optional unit price override; defaults to the "
                "product's sale price",
            },
        },
    },
}


# ---------------------------------------------------------------------------
# read tools
# ---------------------------------------------------------------------------
@tool(
    name="accounting.search_invoices",
    description="Search customer/vendor invoices and credit notes. Filter by "
    "free text (invoice number / reference / origin), move type, state, payment "
    "state, partner and invoice-date range.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Matched against invoice number, reference and origin",
            },
            "move_type": {
                "type": "string",
                "enum": _MOVE_TYPE_ENUM,
                "description": "out_invoice=customer invoice, out_refund=customer "
                "credit note, in_invoice=vendor bill, in_refund=vendor credit note",
            },
            "state": {"type": "string", "enum": _STATE_ENUM},
            "payment_state": {"type": "string", "enum": _PAYMENT_STATE_ENUM},
            "partner_id": {"type": "integer", "description": "res.partner id"},
            "date_from": {
                "type": "string",
                "description": "Invoice date >= (YYYY-MM-DD)",
            },
            "date_to": {"type": "string", "description": "Invoice date <= (YYYY-MM-DD)"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "offset": {"type": "integer", "minimum": 0},
        },
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Search invoices"},
)
def search_invoices(env, arguments):
    domain = [("move_type", "in", list(_INVOICE_TYPES))]
    query = (arguments.get("query") or "").strip()
    if query:
        domain += [
            "|",
            "|",
            ("name", "ilike", query),
            ("ref", "ilike", query),
            ("invoice_origin", "ilike", query),
        ]
    if arguments.get("move_type"):
        domain.append(("move_type", "=", arguments["move_type"]))
    if arguments.get("state"):
        domain.append(("state", "=", arguments["state"]))
    if arguments.get("payment_state"):
        domain.append(("payment_state", "=", arguments["payment_state"]))
    if arguments.get("partner_id"):
        domain.append(("partner_id", "=", int(arguments["partner_id"])))
    if arguments.get("date_from"):
        domain.append(("invoice_date", ">=", arguments["date_from"]))
    if arguments.get("date_to"):
        domain.append(("invoice_date", "<=", arguments["date_to"]))

    limit = min(int(arguments.get("limit") or 20), 100)
    offset = int(arguments.get("offset") or 0)
    records = env["account.move"].search_read(
        domain, _INVOICE_FIELDS, limit=limit, offset=offset, order="invoice_date desc, id desc"
    )
    total = env["account.move"].search_count(domain)
    return {
        "records": records,
        "returned": len(records),
        "total": total,
        "offset": offset,
    }


@tool(
    name="accounting.get_invoice",
    description="Fetch full detail for one invoice / bill / credit note, "
    "including its lines and a downloadable PDF link.",
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "integer", "description": "account.move id"}},
        "required": ["id"],
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Get invoice detail"},
)
def get_invoice(env, arguments):
    move = _get_invoice(env, arguments["id"])
    move.check_access_rule("read")
    return {"invoice": _invoice_detail(env, move)}


@tool(
    name="accounting.get_invoice_status",
    description="Quick status summary for an invoice: its state (draft/posted/"
    "cancel), payment state, total and amount still due.",
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "integer", "description": "account.move id"}},
        "required": ["id"],
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Get invoice status"},
)
def get_invoice_status(env, arguments):
    move = _get_invoice(env, arguments["id"])
    move.check_access_rule("read")
    return {
        "id": move.id,
        "name": move.name,
        "type_label": _type_label(move),
        "state": move.state,
        "payment_state": move.payment_state,
        "partner": move.partner_id.display_name,
        "invoice_date": move.invoice_date and str(move.invoice_date) or None,
        "invoice_date_due": move.invoice_date_due and str(move.invoice_date_due) or None,
        "amount_total": move.amount_total,
        "amount_residual": move.amount_residual,
        "currency": move.currency_id.name,
    }


@tool(
    name="accounting.get_customer_balance",
    description="Get a partner's accounting balance: total receivable, total "
    "amount due and total overdue, plus the count of open customer invoices.",
    input_schema={
        "type": "object",
        "properties": {
            "partner_id": {"type": "integer", "description": "res.partner id"}
        },
        "required": ["partner_id"],
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Get customer balance"},
)
def get_customer_balance(env, arguments):
    partner = env["res.partner"].browse(int(arguments["partner_id"]))
    if not partner.exists():
        raise ToolExecutionError("Partner %s not found" % arguments["partner_id"])
    partner.check_access_rule("read")
    open_domain = [
        ("partner_id", "=", partner.id),
        ("move_type", "in", ["out_invoice", "out_refund"]),
        ("state", "=", "posted"),
        ("payment_state", "in", ["not_paid", "partial"]),
    ]
    return {
        "partner_id": partner.id,
        "partner": partner.display_name,
        "currency": env.company.currency_id.name,
        "total_receivable": partner.credit,
        "total_payable": partner.debit,
        "total_invoiced": partner.total_invoiced,
        "total_due": getattr(partner, "total_due", None),
        "total_overdue": getattr(partner, "total_overdue", None),
        "open_customer_invoices": env["account.move"].search_count(open_domain),
    }


@tool(
    name="accounting.list_overdue_invoices",
    description="List posted customer invoices that are unpaid (or partially "
    "paid) and past their due date. Optionally scope to one customer.",
    input_schema={
        "type": "object",
        "properties": {
            "partner_id": {
                "type": "integer",
                "description": "Optional res.partner id to scope to one customer",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "offset": {"type": "integer", "minimum": 0},
        },
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "List overdue invoices"},
)
def list_overdue_invoices(env, arguments):
    today = fields.Date.context_today(env["account.move"])
    domain = [
        ("move_type", "=", "out_invoice"),
        ("state", "=", "posted"),
        ("payment_state", "in", ["not_paid", "partial"]),
        ("invoice_date_due", "<", today),
    ]
    if arguments.get("partner_id"):
        domain.append(("partner_id", "=", int(arguments["partner_id"])))
    limit = min(int(arguments.get("limit") or 20), 100)
    offset = int(arguments.get("offset") or 0)
    records = env["account.move"].search_read(
        domain, _INVOICE_FIELDS, limit=limit, offset=offset, order="invoice_date_due asc"
    )
    total = env["account.move"].search_count(domain)
    total_overdue = sum(r["amount_residual"] for r in records)
    return {
        "records": records,
        "returned": len(records),
        "total": total,
        "offset": offset,
        "overdue_amount_in_page": total_overdue,
    }


@tool(
    name="accounting.list_products",
    description="Look up products with their sales and cost price for building "
    "invoices. Search by name, internal reference or barcode.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "List products"},
)
def list_products(env, arguments):
    domain = []
    query = (arguments.get("query") or "").strip()
    if query:
        domain += [
            "|",
            "|",
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
    name="accounting.get_invoice_pdf",
    description="Get a short-lived, downloadable PDF link for an invoice / bill / "
    "credit note. The link renders the report on demand and does not require an "
    "Odoo login to open.",
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "integer", "description": "account.move id"}},
        "required": ["id"],
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Get invoice PDF link"},
)
def get_invoice_pdf(env, arguments):
    move = _get_invoice(env, arguments["id"])
    move.check_access_rule("read")
    url = _invoice_pdf_url(env, move)
    return {
        "invoice_id": move.id,
        "invoice_name": move.name,
        "pdf_url": url,
        "filename": _pdf_filename(move),
        "expires_in": env["mcp.report.link"]._ttl(),
    }


# ---------------------------------------------------------------------------
# write tools (propose/confirm)
# ---------------------------------------------------------------------------
@tool(
    name="accounting.create_customer_invoice",
    description="Create a draft customer invoice for a partner with one or more "
    "lines. Two-step: first call previews and returns a confirmation_token; "
    "re-call with the same arguments plus the token to create it.",
    input_schema={
        "type": "object",
        "properties": {
            "partner_id": {"type": "integer", "description": "Customer res.partner id"},
            "invoice_lines": _INVOICE_LINE_SCHEMA,
            "invoice_date": {
                "type": "string",
                "description": "Invoice date (YYYY-MM-DD); defaults to today on post",
            },
            "invoice_date_due": {"type": "string", "description": "Due date (YYYY-MM-DD)"},
            "ref": {"type": "string", "description": "Customer/payment reference"},
            "invoice_origin": {"type": "string", "description": "Source document"},
            "narration": {"type": "string", "description": "Terms & conditions / note"},
            "confirmation_token": {"type": "string"},
        },
        "required": ["partner_id", "invoice_lines"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    required_groups=_GROUPS,
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "title": "Create a customer invoice",
    },
)
def create_customer_invoice(env, arguments):
    partner = env["res.partner"].browse(int(arguments["partner_id"]))
    if not partner.exists():
        raise ToolExecutionError("Customer %s not found" % arguments["partner_id"])
    lines_in = arguments.get("invoice_lines") or []
    if not lines_in:
        raise ToolExecutionError("Provide at least one invoice line.")
    commands = [_line_command(env, li) for li in lines_in]

    move_vals = {
        "move_type": "out_invoice",
        "partner_id": partner.id,
        "invoice_line_ids": commands,
    }
    for key in ("invoice_date", "invoice_date_due", "ref", "invoice_origin", "narration"):
        if arguments.get(key):
            move_vals[key] = arguments[key]

    preview = "Will CREATE a draft customer invoice for %s with %d line(s)." % (
        partner.display_name,
        len(commands),
    )
    env["mcp.action.confirmation"].require(
        "accounting.create_customer_invoice", arguments, preview
    )

    move = env["account.move"].create(move_vals)
    return {"created": True, "invoice": _invoice_detail(env, move)}


@tool(
    name="accounting.post_invoice",
    description="Post (validate) a draft invoice / bill / credit note, moving it "
    "from draft to posted. Two-step propose/confirm.",
    input_schema={
        "type": "object",
        "properties": {
            "invoice_id": {"type": "integer", "description": "account.move id"},
            "confirmation_token": {"type": "string"},
        },
        "required": ["invoice_id"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    required_groups=_GROUPS,
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "title": "Post invoice",
    },
)
def post_invoice(env, arguments):
    move = _get_invoice(env, arguments["invoice_id"])
    if move.state != "draft":
        raise ToolExecutionError(
            "Only a draft invoice can be posted (state=%s)." % move.state
        )
    if not move.invoice_line_ids:
        raise ToolExecutionError("Cannot post an invoice with no lines.")
    preview = "Will POST %s %s for %s totalling %s %s." % (
        _type_label(move),
        move.name or "(draft)",
        move.partner_id.display_name,
        move.amount_total,
        move.currency_id.name or "",
    )
    env["mcp.action.confirmation"].require("accounting.post_invoice", arguments, preview)
    move.action_post()
    return {"posted": True, "invoice": _invoice_detail(env, move)}


@tool(
    name="accounting.register_payment",
    description="Register a payment against a posted, unpaid customer invoice / "
    "vendor bill. Two-step propose/confirm. By default pays the full residual "
    "amount using the given (or default) journal.",
    input_schema={
        "type": "object",
        "properties": {
            "invoice_id": {"type": "integer", "description": "account.move id"},
            "amount": {
                "type": "number",
                "minimum": 0,
                "description": "Optional payment amount; defaults to the full "
                "amount still due",
            },
            "payment_date": {
                "type": "string",
                "description": "Payment date (YYYY-MM-DD); defaults to today",
            },
            "journal_id": {
                "type": "integer",
                "description": "Optional account.journal id (bank/cash) to pay from",
            },
            "payment_ref": {"type": "string", "description": "Payment memo/reference"},
            "confirmation_token": {"type": "string"},
        },
        "required": ["invoice_id"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    required_groups=_GROUPS,
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "title": "Register a payment",
    },
)
def register_payment(env, arguments):
    move = _get_invoice(env, arguments["invoice_id"])
    if move.state != "posted":
        raise ToolExecutionError(
            "Only a posted invoice can be paid (state=%s)." % move.state
        )
    if move.payment_state in ("paid", "in_payment"):
        raise ToolExecutionError(
            "Invoice %s is already settled (payment_state=%s)."
            % (move.name, move.payment_state)
        )

    wizard_vals = {}
    if arguments.get("amount") is not None:
        wizard_vals["amount"] = float(arguments["amount"])
    if arguments.get("payment_date"):
        wizard_vals["payment_date"] = arguments["payment_date"]
    if arguments.get("journal_id"):
        wizard_vals["journal_id"] = int(arguments["journal_id"])
    if arguments.get("payment_ref"):
        wizard_vals["communication"] = arguments["payment_ref"]

    amount_label = wizard_vals.get("amount", move.amount_residual)
    preview = "Will REGISTER a payment of %s %s against %s for %s." % (
        amount_label,
        move.currency_id.name or "",
        move.name,
        move.partner_id.display_name,
    )
    env["mcp.action.confirmation"].require(
        "accounting.register_payment", arguments, preview
    )

    register = (
        env["account.payment.register"]
        .with_context(active_model="account.move", active_ids=move.ids)
        .create(wizard_vals)
    )
    payments = register._create_payments()
    move.invalidate_recordset(["payment_state", "amount_residual"])
    return {
        "paid": True,
        "payment_ids": payments.ids,
        "invoice": _invoice_detail(env, move, with_pdf=False),
    }
