# -*- coding: utf-8 -*-
import ast

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.mcp_server.mcp.exceptions import ToolExecutionError


class McpModelAccess(models.Model):
    """Admin-curated allowlist layered on top of Odoo's own ACL / ir.rule.

    This *narrows* what the generic ``odoo.*`` engine may touch; it never widens
    a user's data access (that is still governed by ACL and record rules at call
    time).
    """

    _name = "mcp.model.access"
    _description = "MCP Model Access (allowlist)"
    _order = "model_id"

    model_id = fields.Many2one("ir.model", required=True, ondelete="cascade")
    model_name = fields.Char(
        related="model_id.model", store=True, index=True, string="Technical Model Name"
    )
    active = fields.Boolean(default=True)

    allow_read = fields.Boolean(default=True)
    allow_search = fields.Boolean(default=True)
    allow_create = fields.Boolean(default=False)
    allow_write = fields.Boolean(default=False)
    allow_unlink = fields.Boolean(default=False)
    allow_action = fields.Boolean(default=False)

    # Empty = all fields the user can already access. Otherwise a whitelist.
    allowed_fields = fields.Text(
        help="Comma/space/newline separated field names. Empty = all accessible."
    )
    writable_fields = fields.Text(
        help="Whitelist of fields writable via create/update. Empty = same as "
        "allowed_fields (or all if that is empty)."
    )
    allowed_actions = fields.Text(
        help="Whitelist of method names callable via odoo.call_action, e.g. "
        "action_confirm, button_cancel."
    )
    domain_filter = fields.Char(
        default="[]",
        help="Extra Odoo domain ANDed onto every search/read via this engine.",
    )

    _sql_constraints = [
        ("model_uniq", "unique(model_id)", "One access rule per model."),
    ]

    # Generic ORM mutators that must never be reachable via odoo.call_action;
    # they sidestep the create/write/unlink flags and writable_fields controls.
    _FORBIDDEN_ACTIONS = frozenset({
        "write", "create", "unlink", "copy", "read", "search", "search_read",
        "browse", "read_group", "load", "export_data", "fields_get",
        "toggle_active", "check_access_rights", "check_access_rule",
        "sudo", "with_user", "with_context", "with_env", "with_company",
    })

    @api.constrains("domain_filter")
    def _check_domain(self):
        for rec in self:
            if rec.domain_filter:
                try:
                    parsed = ast.literal_eval(rec.domain_filter)
                    if not isinstance(parsed, list):
                        raise ValueError
                except (ValueError, SyntaxError):
                    raise ValidationError("Domain filter must be a valid list.")

    # -- parsing helpers -----------------------------------------------------
    @staticmethod
    def _split(text):
        if not text:
            return []
        raw = text.replace(",", " ").replace("\n", " ")
        return [t.strip() for t in raw.split(" ") if t.strip()]

    def _allowed_field_set(self):
        self.ensure_one()
        return set(self._split(self.allowed_fields))

    def _writable_field_set(self):
        self.ensure_one()
        writable = set(self._split(self.writable_fields))
        if writable:
            return writable
        return self._allowed_field_set()

    # -- API used by the generic engine -------------------------------------
    @api.model
    def check_tool_access(self, model_name, operation):
        """Return the (sudo) access rule permitting ``operation`` on ``model``.

        Raises ``ToolExecutionError`` (-> isError tool result) when the model is
        not allow-listed or the operation is disabled.
        """
        rule = self.sudo().search(
            [("model_name", "=", model_name), ("active", "=", True)], limit=1
        )
        if not rule:
            raise ToolExecutionError(
                "Model '%s' is not exposed to the MCP generic engine." % model_name
            )
        flag = {
            "read": rule.allow_read,
            "search": rule.allow_search,
            "create": rule.allow_create,
            "write": rule.allow_write,
            "unlink": rule.allow_unlink,
            "action": rule.allow_action,
        }.get(operation, False)
        if not flag:
            raise ToolExecutionError(
                "Operation '%s' is not permitted on model '%s' by the MCP "
                "allowlist." % (operation, model_name)
            )
        return rule

    def filter_fields(self, requested):
        """Restrict requested fields to the allowlist (if any)."""
        self.ensure_one()
        allowed = self._allowed_field_set()
        if not requested:
            return list(allowed) if allowed else None
        requested = list(requested)
        if not allowed:
            return requested
        rejected = [f for f in requested if f not in allowed]
        if rejected:
            raise ToolExecutionError(
                "Fields not permitted by allowlist: %s" % ", ".join(rejected)
            )
        return requested

    def check_writable_fields(self, field_names):
        self.ensure_one()
        writable = self._writable_field_set()
        if not writable:
            return  # no whitelist configured -> rely on ACL/field security
        rejected = [f for f in field_names if f not in writable]
        if rejected:
            raise ToolExecutionError(
                "Fields not writable via MCP: %s" % ", ".join(rejected)
            )

    def check_action_allowed(self, method):
        self.ensure_one()
        allowed = set(self._split(self.allowed_actions))
        if method.startswith("_"):
            raise ToolExecutionError("Private methods cannot be called via MCP.")
        # Defence in depth: never allow generic ORM mutators / dunder-ish methods
        # to be invoked through call_action, even if mis-configured into the
        # allowlist. These bypass the field-level writable_fields controls.
        if method in self._FORBIDDEN_ACTIONS:
            raise ToolExecutionError(
                "Method '%s' cannot be called via MCP call_action." % method
            )
        if not allowed:
            raise ToolExecutionError(
                "No actions are allow-listed for this model."
            )
        if method not in allowed:
            raise ToolExecutionError(
                "Action '%s' is not allow-listed for this model." % method
            )

    def effective_domain(self, user_domain):
        self.ensure_one()
        base = []
        if self.domain_filter:
            try:
                base = ast.literal_eval(self.domain_filter) or []
            except (ValueError, SyntaxError):
                base = []
        if base and user_domain:
            return ["&"] + base + list(user_domain)
        return base or list(user_domain or [])
