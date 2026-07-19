# -*- coding: utf-8 -*-
from odoo import api, fields, models

from . import mcp_token_utils as tok


class McpOauthClient(models.Model):
    _name = "mcp.oauth.client"
    _description = "MCP OAuth Client"
    _order = "create_date desc"

    name = fields.Char(string="Client Name", required=True)
    client_id = fields.Char(
        required=True, index=True, copy=False, readonly=True, default=lambda s: tok.generate_token(16)
    )
    client_secret_hash = fields.Char(readonly=True, groups="mcp_server.group_mcp_admin")
    # public clients (PKCE, no secret) vs confidential
    is_confidential = fields.Boolean(string="Confidential Client", default=False)
    token_endpoint_auth_method = fields.Selection(
        [("none", "none (public)"), ("client_secret_post", "client_secret_post"),
         ("client_secret_basic", "client_secret_basic")],
        default="none",
        required=True,
    )
    redirect_uris = fields.Text(
        string="Redirect URIs", help="One URI per line.", required=True
    )
    grant_types = fields.Char(default="authorization_code refresh_token")
    response_types = fields.Char(default="code")
    scope = fields.Char(default="mcp")
    active = fields.Boolean(default=True)
    software_id = fields.Char()
    software_version = fields.Char()

    token_ids = fields.One2many("mcp.oauth.token", "client_id_ref", string="Tokens")
    token_count = fields.Integer(compute="_compute_token_count")

    _sql_constraints = [
        ("client_id_uniq", "unique(client_id)", "client_id must be unique"),
    ]

    def _compute_token_count(self):
        for rec in self:
            rec.token_count = len(rec.token_ids)

    # -- redirect URI handling ----------------------------------------------
    def get_redirect_uris(self):
        self.ensure_one()
        return [u.strip() for u in (self.redirect_uris or "").splitlines() if u.strip()]

    def is_redirect_allowed(self, redirect_uri):
        self.ensure_one()
        return redirect_uri in self.get_redirect_uris()

    # -- secret handling -----------------------------------------------------
    def set_secret(self, raw_secret):
        self.sudo().client_secret_hash = tok.hash_secret(raw_secret)

    def verify_secret(self, raw_secret):
        self.ensure_one()
        if not self.is_confidential:
            # public client: no secret required
            return True
        return tok.verify_secret(raw_secret, self.sudo().client_secret_hash)

    # -- Dynamic Client Registration ----------------------------------------
    @api.model
    def register_client(self, metadata):
        """RFC 7591 dynamic client registration.

        ``metadata`` is the parsed JSON body from POST /register. Returns a
        ``(record, raw_secret_or_None)`` tuple.
        """
        redirect_uris = metadata.get("redirect_uris") or []
        auth_method = metadata.get("token_endpoint_auth_method", "none")
        is_confidential = auth_method not in ("none",)
        vals = {
            "name": metadata.get("client_name") or "MCP Client",
            "redirect_uris": "\n".join(redirect_uris),
            "token_endpoint_auth_method": auth_method,
            "is_confidential": is_confidential,
            "grant_types": " ".join(
                metadata.get("grant_types", ["authorization_code", "refresh_token"])
            ),
            "response_types": " ".join(metadata.get("response_types", ["code"])),
            "scope": metadata.get("scope", "mcp"),
            "software_id": metadata.get("software_id"),
            "software_version": metadata.get("software_version"),
        }
        record = self.sudo().create(vals)
        raw_secret = None
        if is_confidential:
            raw_secret = tok.generate_token(24)
            record.set_secret(raw_secret)
        return record, raw_secret

    def to_registration_response(self, raw_secret=None):
        self.ensure_one()
        resp = {
            "client_id": self.client_id,
            "client_id_issued_at": int(self.create_date.timestamp()),
            "redirect_uris": self.get_redirect_uris(),
            "token_endpoint_auth_method": self.token_endpoint_auth_method,
            "grant_types": (self.grant_types or "").split(),
            "response_types": (self.response_types or "").split(),
            "scope": self.scope,
            "client_name": self.name,
        }
        if raw_secret:
            resp["client_secret"] = raw_secret
            resp["client_secret_expires_at"] = 0  # never expires
        return resp
