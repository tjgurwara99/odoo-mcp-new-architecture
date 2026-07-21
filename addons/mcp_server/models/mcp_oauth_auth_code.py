# -*- coding: utf-8 -*-
import base64
import hashlib

from odoo import fields, models

from . import mcp_token_utils as tok


class McpOauthAuthCode(models.Model):
    _name = "mcp.oauth.auth.code"
    _description = "MCP OAuth Authorization Code"
    _order = "create_date desc"
    _rec_name = "client_id_ref"

    code_hash = fields.Char(required=True, index=True, readonly=True)
    client_id_ref = fields.Many2one("mcp.oauth.client", required=True, ondelete="cascade")
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade")
    redirect_uri = fields.Char(required=True)
    scope = fields.Char()
    resource = fields.Char(help="RFC 8707 resource indicator (token audience)")
    code_challenge = fields.Char(required=True)
    code_challenge_method = fields.Char(default="S256")
    expires_at = fields.Datetime(required=True, index=True)
    used = fields.Boolean(default=False, index=True)

    def _default_ttl(self):
        val = self.env["ir.config_parameter"].sudo().get_param(
            "mcp_server.auth_code_ttl", 60
        )
        try:
            return int(val)
        except (TypeError, ValueError):
            return 60

    def _new_code(self, client, user, redirect_uri, scope, resource,
                  code_challenge, code_challenge_method):
        """Create a one-time code; returns the raw code string."""
        raw = tok.generate_token(32)
        self.sudo().create({
            "code_hash": tok.hash_secret(raw),
            "client_id_ref": client.id,
            "user_id": user.id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "resource": resource,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method or "S256",
            "expires_at": fields.Datetime.add(
                fields.Datetime.now(), seconds=self._default_ttl()
            ),
        })
        return raw

    def _find_valid(self, raw_code):
        rec = self.sudo().search([("code_hash", "=", tok.hash_secret(raw_code))], limit=1)
        if not rec:
            return None
        if rec.used or rec.expires_at < fields.Datetime.now():
            return None
        return rec

    @staticmethod
    def verify_pkce(code_verifier, code_challenge, method="S256"):
        """Return True if ``code_verifier`` matches ``code_challenge``.

        Only S256 is supported. The insecure ``plain`` method is intentionally
        rejected to prevent any downgrade path.
        """
        if not code_verifier or not code_challenge:
            return False
        if method and method != "S256":
            return False
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return tok.constant_time_equals(computed, code_challenge)

    def consume(self):
        """Mark the code used (single-use)."""
        self.sudo().write({"used": True})

    def _gc(self):
        """Cron: delete expired/used codes."""
        limit = fields.Datetime.now()
        self.sudo().search([
            "|", ("expires_at", "<", limit), ("used", "=", True)
        ]).unlink()
