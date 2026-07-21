# -*- coding: utf-8 -*-
import json
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Arguments/results are truncated before storage to bound row size.
_MAX_FIELD_LEN = 8000
_SENSITIVE_KEYS = {"password", "secret", "token", "confirmation_token", "api_key"}


class McpAuditLog(models.Model):
    _name = "mcp.audit.log"
    _description = "MCP Audit Log"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"
    _rec_name = "tool_name"

    # create_date/create_uid provide timestamp + technical writer.
    user_id = fields.Many2one("res.users", index=True, string="Acting User")
    oauth_client_id = fields.Many2one("mcp.oauth.client", index=True)
    session_id = fields.Many2one("mcp.session", ondelete="set null")
    tool_name = fields.Char(index=True)
    category = fields.Char(index=True)
    arguments = fields.Text()
    result_summary = fields.Text()
    result_note = fields.Char()
    is_error = fields.Boolean(index=True)
    error = fields.Text()
    duration_ms = fields.Integer()
    confirmation_token = fields.Char(help="Linked confirmation token (redacted).")
    ip = fields.Char()
    user_agent = fields.Char()

    # -- redaction helpers ---------------------------------------------------
    @classmethod
    def _redact(cls, value):
        """Recursively redact sensitive keys anywhere in the structure."""
        if isinstance(value, dict):
            return {
                k: ("***" if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS
                    else cls._redact(v))
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._redact(v) for v in value]
        return value

    @classmethod
    def _sanitize(cls, value):
        try:
            clean = cls._redact(value)
            text = json.dumps(clean, default=str, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            text = str(value)
        if len(text) > _MAX_FIELD_LEN:
            text = text[:_MAX_FIELD_LEN] + "…(truncated)"
        return text

    @staticmethod
    def _summarize_result(result):
        try:
            structured = result.get("structuredContent") if isinstance(result, dict) else None
            if structured is not None:
                text = json.dumps(structured, default=str, ensure_ascii=False)
            else:
                text = json.dumps(result, default=str, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            text = str(result)
        if len(text) > _MAX_FIELD_LEN:
            text = text[:_MAX_FIELD_LEN] + "…(truncated)"
        return text

    # -- main entry point ----------------------------------------------------
    @api.model
    def log_call(
        self,
        user,
        tool_name,
        category,
        arguments,
        result,
        is_error=False,
        error=None,
        duration_ms=0,
        oauth_client=None,
        session=None,
        ip=None,
        user_agent=None,
        confirmation_token=None,
        result_note=None,
    ):
        """Write exactly one audit record for a tool call. Uses sudo (plumbing).

        Returns the created record.
        """
        record = self.sudo().create({
            "user_id": user.id if user else False,
            "oauth_client_id": oauth_client.id if oauth_client else False,
            "session_id": session.id if session else False,
            "tool_name": tool_name,
            "category": category,
            "arguments": self._sanitize(arguments),
            "result_summary": self._summarize_result(result),
            "result_note": result_note,
            "is_error": is_error,
            "error": (error or "")[:_MAX_FIELD_LEN] if error else False,
            "duration_ms": duration_ms,
            "confirmation_token": "***" if confirmation_token else False,
            "ip": ip,
            "user_agent": (user_agent or "")[:512] if user_agent else False,
        })
        record._maybe_alert()
        return record

    # -- sensitive-action alerting ------------------------------------------
    def _sensitive_categories(self):
        param = self.env["ir.config_parameter"].sudo().get_param(
            "mcp_server.sensitive_categories", "write,admin"
        )
        return {c.strip() for c in (param or "").split(",") if c.strip()}

    def _sensitive_prefixes(self):
        param = self.env["ir.config_parameter"].sudo().get_param(
            "mcp_server.sensitive_tool_prefixes", "accounting.,odoo.delete_record"
        )
        return [c.strip() for c in (param or "").split(",") if c.strip()]

    def _is_sensitive(self):
        self.ensure_one()
        if self.category in self._sensitive_categories():
            return True
        return any((self.tool_name or "").startswith(p) for p in self._sensitive_prefixes())

    def _alert_recipients(self):
        group = self.env.ref("mcp_server.group_mcp_admin", raise_if_not_found=False)
        if not group:
            return self.env["res.users"]
        return group.users

    def _maybe_alert(self):
        for rec in self:
            enabled = self.env["ir.config_parameter"].sudo().get_param(
                "mcp_server.enable_alerts", "False"
            )
            if enabled not in ("True", "1", "true"):
                continue
            if not rec._is_sensitive():
                continue
            try:
                rec._post_alert()
            except Exception:  # noqa: BLE001
                _logger.exception("Failed to post MCP sensitive-action alert")

    def _post_alert(self):
        self.ensure_one()
        recipients = self._alert_recipients()
        if not recipients:
            return
        body = (
            "Sensitive MCP tool call: <b>%s</b><br/>User: %s<br/>"
            "Error: %s<br/>Args: %s"
        ) % (
            self.tool_name,
            self.user_id.name if self.user_id else "?",
            self.is_error,
            (self.arguments or "")[:500],
        )
        activity_type = self.env.ref(
            "mail.mail_activity_data_todo", raise_if_not_found=False
        )
        if not activity_type:
            return
        for user in recipients:
            self.sudo().activity_schedule(
                act_type_xmlid="mail.mail_activity_data_todo",
                user_id=user.id,
                summary="Sensitive MCP action: %s" % self.tool_name,
                note=body,
            )
