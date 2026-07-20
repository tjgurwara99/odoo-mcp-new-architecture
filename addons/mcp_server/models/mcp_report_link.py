# -*- coding: utf-8 -*-
"""Short-lived, tokenized report download links.

Domain add-ons (sales/accounting/inventory) need to hand the MCP client a
*clickable link* to a rendered PDF (quotation, invoice, delivery slip, ...).
Serving that link requires an HTTP endpoint, which only the core ``mcp_server``
module owns. Rather than let each domain module talk HTTP, they call
``env['mcp.report.link'].mint(report_ref, records)`` to obtain an absolute URL.

Design / security:

* The URL carries a high-entropy opaque token; only its SHA-256 hash is stored.
* Each link is bound to the *user who minted it*, a specific report, and a
  specific set of record ids. When the link is downloaded, the PDF is rendered
  in an environment scoped to that user, so normal ACL / record rules apply
  (the link never widens access).
* Links expire (``mcp_server.report_link_ttl``, default 1h) and are GC'd by
  cron. They are bearer URLs (like Odoo share links) — short TTL is the control.
* ``mint`` performs an up-front ``check_access_rule('read')`` as the caller, so
  a link is only ever issued for records the user may already read.
"""
import json

from odoo import fields, models

from . import mcp_token_utils as tok
from odoo.addons.mcp_server.mcp.exceptions import ToolExecutionError


class McpReportLink(models.Model):
    _name = "mcp.report.link"
    _description = "MCP Report Download Link"
    _order = "create_date desc"

    token_hash = fields.Char(required=True, index=True, readonly=True)
    user_id = fields.Many2one(
        "res.users", required=True, ondelete="cascade", readonly=True
    )
    report_ref = fields.Char(
        required=True, readonly=True,
        help="Report xmlid or report_name passed to ir.actions.report.",
    )
    model_name = fields.Char(required=True, readonly=True)
    res_ids_json = fields.Text(required=True, readonly=True)
    filename = fields.Char(readonly=True)
    expires_at = fields.Datetime(required=True, index=True, readonly=True)
    download_count = fields.Integer(default=0, readonly=True)
    last_download_at = fields.Datetime(readonly=True)

    # -- config --------------------------------------------------------------
    def _ttl(self):
        val = self.env["ir.config_parameter"].sudo().get_param(
            "mcp_server.report_link_ttl", 3600
        )
        try:
            return int(val)
        except (TypeError, ValueError):
            return 3600

    def _base_url(self):
        param = self.env["ir.config_parameter"].sudo().get_param(
            "mcp_server.public_base_url"
        )
        if not param:
            param = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        return (param or "").rstrip("/")

    # -- minting -------------------------------------------------------------
    def mint(self, report_ref, records, filename=None, ttl=None):
        """Create a download link for ``records`` and return its absolute URL.

        ``records`` must be a non-empty recordset the *current* user can read.
        Raises ``ToolExecutionError`` on empty input, missing base URL, or if
        the user is not allowed to read the records.
        """
        if not records:
            raise ToolExecutionError("No records to render.")
        # Enforce read access as the calling user before issuing any link.
        records.check_access_rights("read")
        records.check_access_rule("read")

        base = self._base_url()
        if not base:
            raise ToolExecutionError(
                "Public Base URL is not configured; cannot build a report link. "
                "Set it in Settings > MCP Server."
            )

        raw = tok.generate_token()
        ttl = int(ttl) if ttl else self._ttl()
        self.sudo().create(
            {
                "token_hash": tok.hash_secret(raw),
                "user_id": self.env.uid,
                "report_ref": report_ref,
                "model_name": records._name,
                "res_ids_json": json.dumps(records.ids),
                "filename": filename or "report.pdf",
                "expires_at": fields.Datetime.add(
                    fields.Datetime.now(), seconds=ttl
                ),
            }
        )
        return "%s/mcp/report/%s" % (base, raw)

    # -- resolution (used by the download controller) ------------------------
    def _resolve(self, raw):
        if not raw:
            return self.browse()
        rec = self.sudo().search(
            [("token_hash", "=", tok.hash_secret(raw))], limit=1
        )
        if not rec:
            return self.browse()
        if rec.expires_at < fields.Datetime.now():
            return self.browse()
        return rec

    def res_ids(self):
        self.ensure_one()
        try:
            return json.loads(self.res_ids_json) or []
        except (TypeError, ValueError):
            return []

    def mark_downloaded(self):
        self.sudo().write(
            {
                "download_count": self.download_count + 1,
                "last_download_at": fields.Datetime.now(),
            }
        )

    # -- housekeeping --------------------------------------------------------
    def _gc(self):
        """Cron: delete expired links."""
        self.sudo().search([("expires_at", "<", fields.Datetime.now())]).unlink()
