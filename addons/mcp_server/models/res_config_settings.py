# -*- coding: utf-8 -*-
from odoo import fields, models
from odoo.tools import config


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    mcp_enabled = fields.Boolean(
        string="Enable MCP Server",
        config_parameter="mcp_server.enabled",
        default=True,
    )
    mcp_server_url = fields.Char(
        string="Public MCP Base URL",
        config_parameter="mcp_server.public_base_url",
        help="External HTTPS base URL of this Odoo (as seen by clients), used "
        "for OAuth metadata and RFC 8707 resource indicator validation. "
        "e.g. https://erp.example.com",
    )
    mcp_require_resource_indicator = fields.Boolean(
        string="Require RFC 8707 Resource Indicator",
        config_parameter="mcp_server.require_resource",
        default=True,
    )
    mcp_access_token_ttl = fields.Integer(
        string="Access Token TTL (s)",
        config_parameter="mcp_server.access_token_ttl",
        default=3600,
    )
    mcp_refresh_token_ttl = fields.Integer(
        string="Refresh Token TTL (s)",
        config_parameter="mcp_server.refresh_token_ttl",
        default=60 * 60 * 24 * 30,
    )
    mcp_confirmation_ttl = fields.Integer(
        string="Confirmation Token TTL (s)",
        config_parameter="mcp_server.confirmation_ttl",
        default=300,
    )
    mcp_report_link_ttl = fields.Integer(
        string="Report Link TTL (s)",
        config_parameter="mcp_server.report_link_ttl",
        default=3600,
    )
    mcp_enable_alerts = fields.Boolean(
        string="Alert on Sensitive Actions",
        config_parameter="mcp_server.enable_alerts",
        default=False,
    )
    mcp_sensitive_categories = fields.Char(
        string="Sensitive Categories",
        config_parameter="mcp_server.sensitive_categories",
        default="write,admin",
    )
    mcp_sensitive_tool_prefixes = fields.Char(
        string="Sensitive Tool Prefixes",
        config_parameter="mcp_server.sensitive_tool_prefixes",
        default="accounting.,odoo.delete_record",
    )
    mcp_dcr_open = fields.Boolean(
        string="Allow Open Dynamic Client Registration",
        config_parameter="mcp_server.dcr_open",
        default=True,
        help="If enabled, unauthenticated clients may self-register via "
        "/mcp/oauth/register (required by Claude's connector flow).",
    )
