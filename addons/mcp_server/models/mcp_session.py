# -*- coding: utf-8 -*-
import secrets

from odoo import fields, models


class McpSession(models.Model):
    """Cross-worker MCP session state.

    Stored in the DB (never worker memory) because the next request for a given
    ``Mcp-Session-Id`` may land on a different prefork worker/process
    (PLAN.md §3.1 worker-model caveats).
    """

    _name = "mcp.session"
    _description = "MCP Session"
    _order = "create_date desc"
    _rec_name = "session_id"

    session_id = fields.Char(required=True, index=True, copy=False, readonly=True)
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade")
    token_id = fields.Many2one("mcp.oauth.token", ondelete="set null")
    protocol_version = fields.Char()
    client_name = fields.Char()
    client_version = fields.Char()
    state = fields.Selection(
        [("new", "New"), ("initializing", "Initializing"),
         ("ready", "Ready"), ("closed", "Closed")],
        default="new",
    )
    last_seen = fields.Datetime(default=fields.Datetime.now)

    _sql_constraints = [
        ("session_id_uniq", "unique(session_id)", "session_id must be unique"),
    ]

    def _open(self, user, token=None):
        """Create a new session and return the record."""
        return self.sudo().create({
            "session_id": secrets.token_urlsafe(24),
            "user_id": user.id,
            "token_id": token.id if token else False,
            "state": "new",
        })

    def _resolve(self, session_id):
        if not session_id:
            return None
        rec = self.sudo().search(
            [("session_id", "=", session_id), ("state", "!=", "closed")], limit=1
        )
        return rec or None

    def touch(self):
        self.sudo().last_seen = fields.Datetime.now()

    def close(self):
        self.sudo().write({"state": "closed"})

    def _gc(self, max_age_hours=24):
        """Cron: close stale sessions."""
        limit = fields.Datetime.subtract(fields.Datetime.now(), hours=max_age_hours)
        self.sudo().search([
            ("last_seen", "<", limit), ("state", "!=", "closed")
        ]).write({"state": "closed"})
