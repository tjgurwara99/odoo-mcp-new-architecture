# -*- coding: utf-8 -*-
"""Generic, admin-configurable model access engine.

Registers the ``odoo.*`` tools. Every call is checked against the
``mcp.model.access`` allowlist *and* real Odoo ACL / record rules (because it all
runs as the authenticated user). Nothing here bypasses standard security.

Write tools use the inline propose/confirm contract: they call
``env['mcp.action.confirmation'].require(...)`` which either consumes a supplied
``confirmation_token`` (and proceeds) or raises ``ConfirmationRequired`` carrying
a preview + fresh token.
"""
from . import constants
from .registry import tool

_MODEL_ARG = {"type": "string", "description": "Technical model name, e.g. res.partner"}


@tool(
    name="odoo.search_records",
    description="Search records of an allow-listed model and return selected "
    "fields. Runs with the calling user's permissions.",
    input_schema={
        "type": "object",
        "properties": {
            "model": _MODEL_ARG,
            "domain": {
                "type": "array",
                "description": "Odoo search domain, e.g. [[\"name\",\"ilike\",\"acme\"]]",
            },
            "fields": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            "offset": {"type": "integer", "minimum": 0},
            "order": {"type": "string"},
        },
        "required": ["model"],
    },
    category=constants.CATEGORY_READ,
    annotations={"readOnlyHint": True, "title": "Search Odoo records"},
)
def search_records(env, arguments):
    model = arguments["model"]
    access = env["mcp.model.access"].check_tool_access(model, "read")
    fields = access.filter_fields(arguments.get("fields"))
    domain = access.effective_domain(arguments.get("domain") or [])
    limit = min(int(arguments.get("limit") or 50), 200)
    offset = int(arguments.get("offset") or 0)
    order = arguments.get("order")
    records = env[model].search_read(
        domain, fields or None, limit=limit, offset=offset, order=order
    )
    total = env[model].search_count(domain)
    return {"model": model, "records": records, "returned": len(records), "total": total}


@tool(
    name="odoo.read_record",
    description="Read a single record of an allow-listed model by id.",
    input_schema={
        "type": "object",
        "properties": {
            "model": _MODEL_ARG,
            "id": {"type": "integer"},
            "fields": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["model", "id"],
    },
    category=constants.CATEGORY_READ,
    annotations={"readOnlyHint": True, "title": "Read an Odoo record"},
)
def read_record(env, arguments):
    model = arguments["model"]
    access = env["mcp.model.access"].check_tool_access(model, "read")
    fields = access.filter_fields(arguments.get("fields"))
    record = env[model].browse(int(arguments["id"]))
    record.check_access_rights("read")
    record.check_access_rule("read")
    if not record.exists():
        from .exceptions import ToolExecutionError

        raise ToolExecutionError("Record %s(%s) not found" % (model, arguments["id"]))
    data = record.read(fields or None)
    return {"model": model, "record": data[0] if data else None}


@tool(
    name="odoo.create_record",
    description="Create a record of an allow-listed model. Two-step: the first "
    "call returns a preview + confirmation_token; call again with that token to "
    "commit.",
    input_schema={
        "type": "object",
        "properties": {
            "model": _MODEL_ARG,
            "values": {"type": "object"},
            "confirmation_token": {"type": "string"},
        },
        "required": ["model", "values"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "title": "Create an Odoo record",
    },
)
def create_record(env, arguments):
    model = arguments["model"]
    values = arguments.get("values") or {}
    access = env["mcp.model.access"].check_tool_access(model, "create")
    access.check_writable_fields(values.keys())
    preview = "Will CREATE a %s record with values: %s" % (model, values)
    env["mcp.action.confirmation"].require("odoo.create_record", arguments, preview)
    record = env[model].create(values)
    return {"model": model, "id": record.id, "created": True}


@tool(
    name="odoo.update_record",
    description="Update an allow-listed record. Two-step propose/confirm.",
    input_schema={
        "type": "object",
        "properties": {
            "model": _MODEL_ARG,
            "id": {"type": "integer"},
            "values": {"type": "object"},
            "confirmation_token": {"type": "string"},
        },
        "required": ["model", "id", "values"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "title": "Update an Odoo record",
    },
)
def update_record(env, arguments):
    from .exceptions import ToolExecutionError

    model = arguments["model"]
    values = arguments.get("values") or {}
    access = env["mcp.model.access"].check_tool_access(model, "write")
    access.check_writable_fields(values.keys())
    record = env[model].browse(int(arguments["id"]))
    if not record.exists():
        raise ToolExecutionError("Record %s(%s) not found" % (model, arguments["id"]))
    preview = "Will UPDATE %s(%s) with values: %s" % (model, arguments["id"], values)
    env["mcp.action.confirmation"].require("odoo.update_record", arguments, preview)
    record.write(values)
    return {"model": model, "id": record.id, "updated": True}


@tool(
    name="odoo.delete_record",
    description="Delete an allow-listed record. Two-step propose/confirm. "
    "Destructive.",
    input_schema={
        "type": "object",
        "properties": {
            "model": _MODEL_ARG,
            "id": {"type": "integer"},
            "confirmation_token": {"type": "string"},
        },
        "required": ["model", "id"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "title": "Delete an Odoo record",
    },
)
def delete_record(env, arguments):
    from .exceptions import ToolExecutionError

    model = arguments["model"]
    env["mcp.model.access"].check_tool_access(model, "unlink")
    record = env[model].browse(int(arguments["id"]))
    if not record.exists():
        raise ToolExecutionError("Record %s(%s) not found" % (model, arguments["id"]))
    display = record.display_name
    preview = "Will DELETE %s(%s): %s" % (model, arguments["id"], display)
    env["mcp.action.confirmation"].require("odoo.delete_record", arguments, preview)
    record.unlink()
    return {"model": model, "id": int(arguments["id"]), "deleted": True}


@tool(
    name="odoo.call_action",
    description="Call an allow-listed method (button/action) on a record, e.g. "
    "action_confirm. Two-step propose/confirm.",
    input_schema={
        "type": "object",
        "properties": {
            "model": _MODEL_ARG,
            "id": {"type": "integer"},
            "method": {"type": "string"},
            "confirmation_token": {"type": "string"},
        },
        "required": ["model", "id", "method"],
    },
    category=constants.CATEGORY_WRITE,
    is_write=True,
    annotations={"readOnlyHint": False, "destructiveHint": True, "title": "Call an action"},
)
def call_action(env, arguments):
    from .exceptions import ToolExecutionError

    model = arguments["model"]
    method = arguments["method"]
    access = env["mcp.model.access"].check_tool_access(model, "action")
    access.check_action_allowed(method)
    record = env[model].browse(int(arguments["id"]))
    if not record.exists():
        raise ToolExecutionError("Record %s(%s) not found" % (model, arguments["id"]))
    preview = "Will CALL %s.%s() on %s(%s): %s" % (
        model,
        method,
        model,
        arguments["id"],
        record.display_name,
    )
    env["mcp.action.confirmation"].require("odoo.call_action", arguments, preview)
    func = getattr(record, method, None)
    if func is None or not callable(func):
        raise ToolExecutionError("Method %s not callable on %s" % (method, model))
    func()
    return {"model": model, "id": record.id, "method": method, "called": True}
