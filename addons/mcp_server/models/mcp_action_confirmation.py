# -*- coding: utf-8 -*-
import hashlib
import json

from odoo import fields, models

from . import mcp_token_utils as tok
from odoo.addons.mcp_server.mcp.exceptions import (
    ConfirmationRequired,
    ToolExecutionError,
)


class McpActionConfirmation(models.Model):
    """Shared propose -> confirm token service for all write tools.

    A write tool computes a read-only preview, then calls ``require(...)``:
    * If the caller supplied a valid ``confirmation_token`` bound to the same
      user + tool + arguments, it is consumed and the tool proceeds.
    * Otherwise a pending record is created and ``ConfirmationRequired`` is
      raised carrying the preview + fresh token, which the protocol layer turns
      into a (successful) tool result asking the client to re-call with the
      token.

    Tokens are single-use, user-bound, and expire (cron GC).
    """

    _name = "mcp.action.confirmation"
    _description = "MCP Action Confirmation Token"
    _order = "create_date desc"
    _rec_name = "tool_name"

    token_hash = fields.Char(required=True, index=True, readonly=True)
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade")
    tool_name = fields.Char(required=True)
    arguments_json = fields.Text()
    arguments_hash = fields.Char(index=True)
    preview = fields.Text()
    expires_at = fields.Datetime(required=True, index=True)
    consumed = fields.Boolean(default=False, index=True)
    consumed_at = fields.Datetime()

    def _ttl(self):
        val = self.env["ir.config_parameter"].sudo().get_param(
            "mcp_server.confirmation_ttl", 300
        )
        try:
            return int(val)
        except (TypeError, ValueError):
            return 300

    @staticmethod
    def _hash_args(tool_name, arguments):
        clean = {k: v for k, v in (arguments or {}).items() if k != "confirmation_token"}
        payload = json.dumps({"tool": tool_name, "args": clean}, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def require(self, tool_name, arguments, preview):
        """Enforce the propose/confirm gate for a write tool.

        Uses ``sudo`` for its own bookkeeping (creating/consuming the token) —
        this is the sanctioned plumbing elevation described in PLAN.md §3.1; it
        never widens the caller's data access, it only writes internal records.
        """
        arguments = arguments or {}
        supplied = arguments.get("confirmation_token")
        args_hash = self._hash_args(tool_name, arguments)
        user = self.env.user

        if supplied:
            rec = self.sudo().search(
                [("token_hash", "=", tok.hash_secret(supplied))], limit=1
            )
            if not rec:
                raise ToolExecutionError("Invalid or unknown confirmation token.")
            if rec.consumed:
                raise ToolExecutionError("Confirmation token already used.")
            if rec.expires_at < fields.Datetime.now():
                raise ToolExecutionError("Confirmation token has expired.")
            if rec.user_id.id != user.id:
                raise ToolExecutionError("Confirmation token does not belong to you.")
            if rec.tool_name != tool_name:
                raise ToolExecutionError("Confirmation token is for a different tool.")
            if rec.arguments_hash != args_hash:
                raise ToolExecutionError(
                    "Arguments changed since proposal; request a new confirmation."
                )
            rec.sudo().write({"consumed": True, "consumed_at": fields.Datetime.now()})
            return rec  # proceed

        # No token supplied -> create a pending proposal and stop.
        raw = tok.generate_token(24)
        self.sudo().create({
            "token_hash": tok.hash_secret(raw),
            "user_id": user.id,
            "tool_name": tool_name,
            "arguments_json": json.dumps(
                {k: v for k, v in arguments.items() if k != "confirmation_token"},
                default=str,
            ),
            "arguments_hash": args_hash,
            "preview": preview,
            "expires_at": fields.Datetime.add(
                fields.Datetime.now(), seconds=self._ttl()
            ),
        })
        raise ConfirmationRequired(preview, raw, self._ttl())

    def _gc(self):
        """Cron: delete expired/consumed tokens."""
        limit = fields.Datetime.now()
        self.sudo().search([
            "|", ("expires_at", "<", limit), ("consumed", "=", True)
        ]).unlink()
