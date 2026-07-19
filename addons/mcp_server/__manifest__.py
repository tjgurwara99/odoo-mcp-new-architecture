# -*- coding: utf-8 -*-
{
    "name": "MCP Server (Core)",
    "version": "16.0.1.0.0",
    "category": "Technical",
    "summary": "Model Context Protocol server for Odoo: protocol engine, "
    "OAuth 2.1, generic model engine, confirmation workflow, audit log.",
    "description": """
MCP Server — Core
=================
Exposes Odoo business functionality to Claude (and any MCP-compliant client)
as a remote MCP connector over HTTP.

Provides:
* MCP Streamable HTTP transport (JSON-RPC 2.0) at ``POST /mcp``.
* OAuth 2.1 Authorization Server (Authorization Code + PKCE, DCR).
* Tool/Resource registry that domain add-ons register into.
* Generic, admin-configurable model access engine (allow-listed CRUD).
* Two-step propose/confirm workflow for all write operations.
* Full audit trail with admin UI and sensitive-action alerting.

All tool execution runs as the authenticated ``res.users`` account — normal
ACL, ``ir.rule`` and field-level security apply. The module only ever adds
*narrowing* security layers, never widening ones.
""",
    "author": "Taj Singh <tjgurwara99@gmail.com>",
    "website": "https://github.com/tjgurwara99/odoo-mcp-new-architecture",
    "license": "OPL-1",
    "depends": ["base", "base_setup", "mail", "web"],
    "data": [
        "security/mcp_security.xml",
        "security/ir.model.access.csv",
        "security/mcp_record_rules.xml",
        "data/mcp_data.xml",
        "data/ir_cron.xml",
        "views/mcp_oauth_client_views.xml",
        "views/mcp_oauth_token_views.xml",
        "views/mcp_model_access_views.xml",
        "views/mcp_confirmation_views.xml",
        "views/mcp_audit_log_views.xml",
        "views/mcp_session_views.xml",
        "views/res_config_settings_views.xml",
        "views/mcp_consent_templates.xml",
        "views/mcp_menus.xml",
    ],
    "assets": {},
    "installable": True,
    "application": True,
    "auto_install": False,
}
