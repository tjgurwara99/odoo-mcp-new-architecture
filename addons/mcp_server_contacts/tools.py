# -*- coding: utf-8 -*-
"""Curated MCP tools for the Contacts (``res.partner``) domain.

These tools register into the core ``mcp_server`` registry at import time. They
are deliberately thin wrappers over the standard ORM: every call executes as the
authenticated ``res.users`` (the ``env`` handed to each tool), so normal Odoo
ACLs, record rules and field-level security apply. Nothing here uses ``sudo``.

Write tools use the shared propose/confirm contract via
``env['mcp.action.confirmation'].require(...)``: the first call returns a
preview + ``confirmation_token``; the client re-calls with that token to commit.
"""
from odoo.addons.mcp_server.mcp import constants
from odoo.addons.mcp_server.mcp.exceptions import ToolExecutionError
from odoo.addons.mcp_server.mcp.registry import tool

# Only members of the MCP User group ever see these tools (real data access is
# still governed by res.partner ACL / record rules at call time).
_GROUPS = ["mcp_server.group_mcp_user"]

# Curated field set returned by read tools. Kept to base/contacts fields so the
# add-on does not implicitly depend on sale/account.
_READ_FIELDS = [
    "id",
    "name",
    "display_name",
    "is_company",
    "company_type",
    "email",
    "phone",
    "mobile",
    "street",
    "street2",
    "city",
    "zip",
    "state_id",
    "country_id",
    "vat",
    "website",
    "function",
    "title",
    "parent_id",
    "category_id",
    "comment",
    "active",
    "create_date",
]

# Fields a client may set via create/update. ``country_code`` is handled
# specially (resolved to ``country_id``); everything else maps 1:1.
_SIMPLE_WRITABLE = (
    "name",
    "email",
    "phone",
    "mobile",
    "is_company",
    "street",
    "street2",
    "city",
    "zip",
    "website",
    "vat",
    "function",
    "comment",
    "parent_id",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _resolve_country(env, code):
    """Resolve a country by ISO code (``US``) or name (``United States``).

    Uses exact matching: Odoo's ``res.country`` treats ``code =ilike`` as a
    prefix match (so 'Nowhereland' would spuriously match Norway's 'NO'), which
    we must avoid.
    """
    if not code:
        return None
    code = code.strip()
    Country = env["res.country"]
    country = Country.browse()
    # ``res.country.code`` is size=2, so the ORM truncates the compared value to
    # two chars; only match by code when the input really is a 2-letter code.
    if len(code) == 2:
        country = Country.search([("code", "=", code.upper())], limit=1)
    if not country:
        country = Country.search([("name", "=ilike", code)], limit=1)
    if not country:
        raise ToolExecutionError("Unknown country: %r" % code)
    return country.id


def _build_write_values(env, arguments):
    """Extract curated write values from tool arguments (present keys only)."""
    values = {}
    for key in _SIMPLE_WRITABLE:
        if key in arguments and arguments[key] is not None:
            values[key] = arguments[key]
    if arguments.get("country_code"):
        values["country_id"] = _resolve_country(env, arguments["country_code"])
    return values


def _partner_read(record, fields=None):
    """Read curated fields for a partner recordset -> list of dicts."""
    return record.read(fields or _READ_FIELDS)


# JSON-schema fragment shared by create/update for the curated writable fields.
_WRITE_PROPERTIES = {
    "name": {"type": "string", "description": "Contact / company name"},
    "email": {"type": "string"},
    "phone": {"type": "string"},
    "mobile": {"type": "string"},
    "is_company": {
        "type": "boolean",
        "description": "True for a company, False for an individual",
    },
    "street": {"type": "string"},
    "street2": {"type": "string"},
    "city": {"type": "string"},
    "zip": {"type": "string"},
    "country_code": {
        "type": "string",
        "description": "ISO country code (e.g. 'US') or country name",
    },
    "website": {"type": "string"},
    "vat": {"type": "string", "description": "Tax ID / VAT number"},
    "function": {"type": "string", "description": "Job position"},
    "comment": {"type": "string", "description": "Internal notes"},
    "parent_id": {
        "type": "integer",
        "description": "Id of the parent company/contact this record belongs to",
    },
}


# ---------------------------------------------------------------------------
# read tools
# ---------------------------------------------------------------------------
@tool(
    name="contacts.search_partners",
    description="Search Odoo contacts (people and companies). Provide a free-text "
    "query to match across name, email, phone, mobile, reference and VAT. "
    "Runs with the calling user's permissions.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Free text matched against name/email/phone/ref/VAT",
            },
            "is_company": {
                "type": "boolean",
                "description": "Filter to companies (true) or individuals (false)",
            },
            "country_code": {
                "type": "string",
                "description": "Restrict to a country (ISO code or name)",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "offset": {"type": "integer", "minimum": 0},
        },
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Search contacts"},
)
def search_partners(env, arguments):
    domain = []
    query = (arguments.get("query") or "").strip()
    if query:
        domain += [
            "|", "|", "|", "|", "|",
            ("name", "ilike", query),
            ("email", "ilike", query),
            ("phone", "ilike", query),
            ("mobile", "ilike", query),
            ("ref", "ilike", query),
            ("vat", "ilike", query),
        ]
    if arguments.get("is_company") is not None:
        domain.append(("is_company", "=", bool(arguments["is_company"])))
    if arguments.get("country_code"):
        domain.append(("country_id", "=", _resolve_country(env, arguments["country_code"])))

    limit = min(int(arguments.get("limit") or 20), 100)
    offset = int(arguments.get("offset") or 0)
    records = env["res.partner"].search_read(
        domain, _READ_FIELDS, limit=limit, offset=offset, order="display_name"
    )
    total = env["res.partner"].search_count(domain)
    return {
        "records": records,
        "returned": len(records),
        "total": total,
        "offset": offset,
    }


@tool(
    name="contacts.get_partner",
    description="Fetch full detail for a single contact by id, including its "
    "child contacts (e.g. the people at a company).",
    input_schema={
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "res.partner id"},
        },
        "required": ["id"],
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Get contact detail"},
)
def get_partner(env, arguments):
    partner = env["res.partner"].browse(int(arguments["id"]))
    if not partner.exists():
        raise ToolExecutionError("Contact %s not found" % arguments["id"])
    # check_access_rule raises AccessError (-> tool error) if not permitted.
    partner.check_access_rule("read")
    data = _partner_read(partner)[0]
    children = partner.child_ids.read(
        ["id", "name", "function", "email", "phone", "type"]
    )
    data["child_contacts"] = children
    return {"partner": data}


@tool(
    name="contacts.get_partner_activities",
    description="List scheduled activities (to-dos, calls, meetings) planned on a "
    "contact, ordered by due date.",
    input_schema={
        "type": "object",
        "properties": {
            "partner_id": {"type": "integer", "description": "res.partner id"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "required": ["partner_id"],
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Get contact activities"},
)
def get_partner_activities(env, arguments):
    partner = env["res.partner"].browse(int(arguments["partner_id"]))
    if not partner.exists():
        raise ToolExecutionError("Contact %s not found" % arguments["partner_id"])
    partner.check_access_rule("read")
    limit = min(int(arguments.get("limit") or 50), 100)
    activities = env["mail.activity"].search_read(
        [("res_model", "=", "res.partner"), ("res_id", "=", partner.id)],
        ["id", "activity_type_id", "summary", "note", "date_deadline", "user_id", "state"],
        limit=limit,
        order="date_deadline",
    )
    return {
        "partner_id": partner.id,
        "partner_name": partner.display_name,
        "activities": activities,
        "returned": len(activities),
    }


# ---------------------------------------------------------------------------
# write tools (propose/confirm)
# ---------------------------------------------------------------------------
@tool(
    name="contacts.create_partner",
    description="Create a new contact (person or company). Two-step: the first "
    "call returns a preview + confirmation_token; call again with the same "
    "arguments plus that token to actually create the record.",
    input_schema={
        "type": "object",
        "properties": dict(
            _WRITE_PROPERTIES,
            confirmation_token={"type": "string"},
        ),
        "required": ["name"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    required_groups=_GROUPS,
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "title": "Create a contact",
    },
)
def create_partner(env, arguments):
    values = _build_write_values(env, arguments)
    if not values.get("name"):
        raise ToolExecutionError("A 'name' is required to create a contact.")
    preview = "Will CREATE a contact with: %s" % values
    env["mcp.action.confirmation"].require("contacts.create_partner", arguments, preview)
    partner = env["res.partner"].create(values)
    return {"created": True, "partner": _partner_read(partner)[0]}


@tool(
    name="contacts.update_partner",
    description="Update curated fields on an existing contact. Two-step "
    "propose/confirm: first call previews the change and returns a "
    "confirmation_token; re-call with the same arguments plus the token to save.",
    input_schema={
        "type": "object",
        "properties": dict(
            _WRITE_PROPERTIES,
            id={"type": "integer", "description": "res.partner id to update"},
            confirmation_token={"type": "string"},
        ),
        "required": ["id"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    required_groups=_GROUPS,
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "title": "Update a contact",
    },
)
def update_partner(env, arguments):
    partner = env["res.partner"].browse(int(arguments["id"]))
    if not partner.exists():
        raise ToolExecutionError("Contact %s not found" % arguments["id"])
    values = _build_write_values(env, arguments)
    if not values:
        raise ToolExecutionError(
            "No updatable fields supplied. Provide at least one of: %s"
            % ", ".join(_SIMPLE_WRITABLE + ("country_code",))
        )
    preview = "Will UPDATE contact %s (%s) with: %s" % (
        partner.id,
        partner.display_name,
        values,
    )
    env["mcp.action.confirmation"].require("contacts.update_partner", arguments, preview)
    partner.write(values)
    return {"updated": True, "partner": _partner_read(partner)[0]}
