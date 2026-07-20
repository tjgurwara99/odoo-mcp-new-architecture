# -*- coding: utf-8 -*-
"""Tokenized report download endpoint.

Serves ``GET /mcp/report/<token>`` -> the rendered PDF for a link previously
minted via ``mcp.report.link.mint(...)``. No Odoo session is required (the token
is the credential), but the PDF is rendered in an environment scoped to the user
who minted the link, so record rules and ACLs still apply.
"""
import logging

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request

_logger = logging.getLogger(__name__)


class MCPReportController(http.Controller):
    @http.route(
        "/mcp/report/<string:token>",
        type="http",
        auth="none",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def download_report(self, token, **kwargs):
        link = request.env["mcp.report.link"]._resolve(token)
        if not link:
            return request.make_response(
                "Link not found or expired.",
                headers=[("Content-Type", "text/plain")],
                status=404,
            )

        # Render as the user who minted the link (enforces ACL / record rules).
        env = request.env(user=link.user_id.id)
        res_ids = link.res_ids()
        records = env[link.model_name].browse(res_ids)
        try:
            records.check_access_rights("read")
            records.check_access_rule("read")
        except AccessError:
            return request.make_response(
                "You are not allowed to access this document.",
                headers=[("Content-Type", "text/plain")],
                status=403,
            )

        try:
            pdf, _ = env["ir.actions.report"]._render_qweb_pdf(
                link.report_ref, res_ids
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("MCP report render failed for %s", link.report_ref)
            return request.make_response(
                "Failed to render report: %s" % exc,
                headers=[("Content-Type", "text/plain")],
                status=500,
            )

        link.mark_downloaded()
        filename = link.filename or "report.pdf"
        return request.make_response(
            pdf,
            headers=[
                ("Content-Type", "application/pdf"),
                ("Content-Length", str(len(pdf))),
                ("Content-Disposition", 'inline; filename="%s"' % filename),
            ],
        )
