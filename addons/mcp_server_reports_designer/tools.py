# -*- coding: utf-8 -*-
"""Curated MCP tools for the GTECH *Report Designer* (``reports.designer``).

These tools let an MCP client discover the custom reports a customer has built
with the Report Designer module and generate them as PDF. Every tool executes
as the authenticated ``res.users`` (the ``env`` handed in), so the Report
Designer ACLs / record rules apply — no ``sudo`` on the request path.

Generation flow (mirrors the module's own ``export_pdf`` wizard action):

1. Build the ``datas`` payload the report engine expects (records + parameter
   values keyed by the dynamic wizard fields).
2. ``reports_designer_gen.create_xls(...)`` produces an XLSX ``ir.attachment``.
3. The XLSX is converted to PDF via ``reports.scheduler.convert_excel_to_pdf``
   (LibreOffice), stored as a fresh ``ir.attachment``.
4. A tokenized, short-lived download URL is minted for that PDF through the core
   ``mcp.report.link.mint_attachment`` facility.
"""
import base64
import logging
import os
import shutil
import tempfile

from odoo.addons.mcp_server.mcp import constants
from odoo.addons.mcp_server.mcp.exceptions import ToolExecutionError
from odoo.addons.mcp_server.mcp.registry import tool

# The Report Designer wizard exposes a shared CellUtil helper the engine needs.
from odoo.addons.reports_designer.wizard.reports_designer_wizard import CellUtil

_logger = logging.getLogger(__name__)

# Visible only to MCP users. Actual Report Designer access is enforced per call.
_GROUPS = ["mcp_server.group_mcp_user"]

_REPORT_FIELDS = [
    "id",
    "name",
    "description",
    "description_report",
    "root_model_id",
    "send_email",
    "active",
]

_PARAM_FIELDS = ["code", "name", "type_param", "param_required", "param_ir_model_id"]

# JSON-schema type per Report Designer parameter type.
_PARAM_JSON_TYPE = {
    "char": "string",
    "integer": "integer",
    "float": "number",
    "boolean": "boolean",
    "date": "string",
    "datetime": "string",
    "many2one": "integer",
    "many2many": "array",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _get_report(env, report_id):
    report = env["reports.designer"].browse(int(report_id))
    if not report.exists():
        raise ToolExecutionError("Report %s not found" % report_id)
    report.check_access_rule("read")
    return report


def _param_summary(param):
    return {
        "code": param.code,
        "name": param.name,
        "type": param.type_param,
        "required": param.param_required,
        "model": param.param_ir_model_id.model or None,
    }


def _report_summary(env, report):
    return {
        "id": report.id,
        "name": report.name,
        "description": report.description or report.description_report or None,
        "root_model": report.root_model_id.model or None,
        "root_model_name": report.root_model_id.name or None,
        "parameters": [_param_summary(p) for p in report.reports_designer_param_ids],
    }


def _coerce_param_value(param, value):
    """Coerce an MCP-supplied value into what the report engine expects."""
    ptype = param.type_param
    if value is None:
        return False
    if ptype == "integer" or ptype == "many2one":
        return int(value)
    if ptype == "float":
        return float(value)
    if ptype == "boolean":
        return bool(value)
    if ptype == "many2many":
        if not isinstance(value, (list, tuple)):
            raise ToolExecutionError(
                "Parameter '%s' expects a list of ids." % param.code
            )
        return [int(v) for v in value]
    # char / date / datetime are passed through as-is (strings).
    return value


def _build_param_data(report, params):
    """Map ``{code: value}`` -> ``{wizard_field_name: coerced_value}``.

    The report engine reads parameter values out of the transient wizard's
    serialized ``data`` dict, keyed by the auto-generated ``x_param_*`` field
    bound to each parameter.
    """
    params = params or {}
    by_code = {p.code: p for p in report.reports_designer_param_ids}
    unknown = [c for c in params if c not in by_code]
    if unknown:
        raise ToolExecutionError(
            "Unknown parameter code(s): %s. Use reports_designer.get_report to "
            "list valid codes." % ", ".join(sorted(unknown))
        )

    data = {}
    for code, param in by_code.items():
        provided = code in params
        if not provided:
            if param.param_required:
                raise ToolExecutionError(
                    "Missing required parameter '%s'." % code
                )
            continue
        field = param.wizard_param_ir_model_field_id
        if not field:
            # Parameter has no bound wizard field (mis-configured report); skip.
            continue
        data[field.name] = _coerce_param_value(param, params[code])
    return data


def _generate_pdf_attachment(env, report, res_ids, params):
    """Generate the report as PDF and return the resulting ``ir.attachment``."""
    active_model = report.root_model_id.model
    if not active_model:
        raise ToolExecutionError(
            "Report '%s' has no root model configured." % report.name
        )

    res_ids = [int(i) for i in (res_ids or [])]
    if res_ids:
        # Enforce read access on the business records as the calling user.
        records = env[active_model].browse(res_ids)
        missing = [i for i, r in zip(res_ids, records) if not r.exists()]
        if missing:
            raise ToolExecutionError(
                "%s record(s) not found: %s" % (active_model, missing)
            )
        records.check_access_rights("read")
        records.check_access_rule("read")

    datas = {
        "ids": res_ids,
        "active_model": active_model,
        "form": {
            "report_conf": (report.id, report.name),
            "report_conf_id": report.id,
            "data": _build_param_data(report, params),
        },
        "send_by_email": False,
    }

    action = env["reports_designer_gen"].create_xls(datas, CellUtil)
    if not isinstance(action, dict) or not action.get("url"):
        raise ToolExecutionError("Report generation did not produce a document.")
    try:
        xlsx_attach_id = int(action["url"].split("=")[1])
    except (IndexError, ValueError):
        raise ToolExecutionError("Could not resolve the generated document.")

    xlsx_attach = env["ir.attachment"].sudo().browse(xlsx_attach_id)
    if not xlsx_attach.exists() or not xlsx_attach.store_fname:
        raise ToolExecutionError("Generated workbook is unavailable.")

    return _convert_to_pdf(env, report, xlsx_attach)


def _convert_to_pdf(env, report, xlsx_attach):
    """Convert an XLSX attachment to a PDF attachment (LibreOffice)."""
    src_path = xlsx_attach._full_path(xlsx_attach.store_fname)
    file_dir = tempfile.gettempdir() + "/"
    safe_name = (xlsx_attach.name or "report.xlsx").replace(" ", "_")
    excel_file_path = file_dir + safe_name
    base, _ext = os.path.splitext(safe_name)
    pdf_file_name = base + ".pdf"
    pdf_file_path = file_dir + pdf_file_name

    shutil.copyfile(src_path, excel_file_path)
    try:
        env["reports.scheduler"].sudo().convert_excel_to_pdf(
            excel_file_path, file_dir
        )
        if not os.path.exists(pdf_file_path):
            raise ToolExecutionError(
                "PDF conversion failed (is LibreOffice/soffice installed on the "
                "server?)."
            )
        with open(pdf_file_path, "rb") as pdf_file:
            pdf_data = base64.b64encode(pdf_file.read())
    finally:
        for path in (excel_file_path, pdf_file_path):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                _logger.warning("Could not clean up temp file %s", path)

    pdf_attachment = env["ir.attachment"].sudo().create(
        {
            "name": pdf_file_name,
            "type": "binary",
            "datas": pdf_data,
            "mimetype": "application/pdf",
            "res_model": "reports.designer",
            "res_id": report.id,
        }
    )
    return pdf_attachment


# ---------------------------------------------------------------------------
# read tools
# ---------------------------------------------------------------------------
@tool(
    name="reports_designer.list_reports",
    description="List the custom reports built with the Report Designer that can "
    "be generated over MCP. Optionally filter by name.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Matched against report name"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "List Report Designer reports"},
)
def list_reports(env, arguments):
    domain = [("active", "=", True)]
    query = (arguments.get("query") or "").strip()
    if query:
        domain.append(("name", "ilike", query))
    limit = min(int(arguments.get("limit") or 50), 100)
    reports = env["reports.designer"].search(domain, limit=limit, order="name")
    return {
        "records": [_report_summary(env, r) for r in reports],
        "returned": len(reports),
    }


@tool(
    name="reports_designer.get_report",
    description="Fetch full detail for one Report Designer report, including the "
    "parameters it accepts (their codes, types and whether they are required).",
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "integer", "description": "reports.designer id"}},
        "required": ["id"],
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Get Report Designer report"},
)
def get_report(env, arguments):
    report = _get_report(env, arguments["id"])
    return {"report": _report_summary(env, report)}


@tool(
    name="reports_designer.generate_report",
    description="Generate a Report Designer report as PDF and return a "
    "short-lived, downloadable link. Supply the report id, optionally the ids "
    "of the root-model records to include, and any parameter values keyed by "
    "parameter code (see reports_designer.get_report). The link renders on "
    "demand and does not require an Odoo login to open; it expires after a "
    "short TTL.",
    input_schema={
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "reports.designer id"},
            "record_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Ids of the report's root-model records to include. "
                "Optional for reports driven solely by parameters.",
            },
            "params": {
                "type": "object",
                "description": "Parameter values keyed by parameter code.",
                "additionalProperties": True,
            },
        },
        "required": ["id"],
    },
    category=constants.CATEGORY_READ,
    required_groups=_GROUPS,
    annotations={"readOnlyHint": True, "title": "Generate report PDF link"},
)
def generate_report(env, arguments):
    report = _get_report(env, arguments["id"])
    res_ids = arguments.get("record_ids") or []
    params = arguments.get("params") or {}

    pdf_attachment = _generate_pdf_attachment(env, report, res_ids, params)
    url = env["mcp.report.link"].mint_attachment(
        pdf_attachment, filename=pdf_attachment.name
    )
    return {
        "report_id": report.id,
        "report_name": report.name,
        "pdf_url": url,
        "filename": pdf_attachment.name,
        "expires_in": env["mcp.report.link"]._ttl(),
    }
