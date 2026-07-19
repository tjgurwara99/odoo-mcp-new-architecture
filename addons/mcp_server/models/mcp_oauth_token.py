# -*- coding: utf-8 -*-
from odoo import fields, models

from . import mcp_token_utils as tok


class McpOauthToken(models.Model):
    _name = "mcp.oauth.token"
    _description = "MCP OAuth Token"
    _order = "create_date desc"
    _rec_name = "user_id"

    access_token_hash = fields.Char(index=True, readonly=True)
    refresh_token_hash = fields.Char(index=True, readonly=True)
    client_id_ref = fields.Many2one("mcp.oauth.client", required=True, ondelete="cascade")
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade")
    scope = fields.Char()
    resource = fields.Char(help="RFC 8707 audience this token is bound to")
    access_expires_at = fields.Datetime(index=True)
    refresh_expires_at = fields.Datetime(index=True)
    revoked = fields.Boolean(default=False, index=True)
    last_used_at = fields.Datetime()

    # -- TTL config ----------------------------------------------------------
    def _get_int_param(self, key, default):
        val = self.env["ir.config_parameter"].sudo().get_param(key, default)
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

    def _access_ttl(self):
        return self._get_int_param("mcp_server.access_token_ttl", 3600)

    def _refresh_ttl(self):
        return self._get_int_param("mcp_server.refresh_token_ttl", 60 * 60 * 24 * 30)

    # -- issuance ------------------------------------------------------------
    def issue(self, client, user, scope, resource, with_refresh=True):
        """Create a token pair. Returns ``(record, access_raw, refresh_raw)``."""
        now = fields.Datetime.now()
        access_raw = tok.generate_token(32)
        refresh_raw = tok.generate_token(32) if with_refresh else None
        vals = {
            "access_token_hash": tok.hash_secret(access_raw),
            "client_id_ref": client.id,
            "user_id": user.id,
            "scope": scope,
            "resource": resource,
            "access_expires_at": fields.Datetime.add(now, seconds=self._access_ttl()),
        }
        if refresh_raw:
            vals["refresh_token_hash"] = tok.hash_secret(refresh_raw)
            vals["refresh_expires_at"] = fields.Datetime.add(
                now, seconds=self._refresh_ttl()
            )
        record = self.sudo().create(vals)
        return record, access_raw, refresh_raw

    def to_token_response(self, access_raw, refresh_raw=None):
        self.ensure_one()
        resp = {
            "access_token": access_raw,
            "token_type": "Bearer",
            "expires_in": self._access_ttl(),
            "scope": self.scope or "",
        }
        if refresh_raw:
            resp["refresh_token"] = refresh_raw
        return resp

    # -- resolution ----------------------------------------------------------
    def _resolve_access(self, raw_access):
        """Resolve a bearer access token to a valid, non-expired record."""
        if not raw_access:
            return None
        rec = self.sudo().search(
            [("access_token_hash", "=", tok.hash_secret(raw_access))], limit=1
        )
        if not rec or rec.revoked:
            return None
        if rec.access_expires_at and rec.access_expires_at < fields.Datetime.now():
            return None
        return rec

    def _resolve_refresh(self, raw_refresh):
        if not raw_refresh:
            return None
        rec = self.sudo().search(
            [("refresh_token_hash", "=", tok.hash_secret(raw_refresh))], limit=1
        )
        if not rec or rec.revoked:
            return None
        if rec.refresh_expires_at and rec.refresh_expires_at < fields.Datetime.now():
            return None
        return rec

    def validate_audience(self, expected_resource):
        """RFC 8707: the token must be bound to this MCP server's resource.

        Returns True if the token's ``resource`` matches. If the token carries no
        resource (legacy), we accept only when the server didn't require one.
        """
        self.ensure_one()
        if not expected_resource:
            return True
        if not self.resource:
            return False
        return tok.constant_time_equals(
            self.resource.rstrip("/"), expected_resource.rstrip("/")
        )

    def rotate_refresh(self):
        """Refresh-token rotation: revoke this token and issue a fresh pair."""
        self.ensure_one()
        self.sudo().revoked = True
        return self.issue(
            self.client_id_ref, self.user_id, self.scope, self.resource
        )

    def touch(self):
        self.sudo().last_used_at = fields.Datetime.now()

    def revoke(self):
        self.sudo().write({"revoked": True})

    def _gc(self):
        """Cron: purge expired refresh tokens (access already useless)."""
        limit = fields.Datetime.now()
        self.sudo().search([
            "|",
            ("revoked", "=", True),
            ("refresh_expires_at", "<", limit),
        ]).unlink()
