# -*- coding: utf-8 -*-
"""Protocol-level constants pinned for this server."""

# The single MCP protocol version this server implements and negotiates.
# See PLAN.md §1: behaviours differ across versions (batching removal, RFC 8707
# resource indicators, elicitation) so we implement to one pinned version.
PROTOCOL_VERSION = "2025-06-18"

# Versions we are willing to accept in `initialize` if a client asks for them.
# We only truly implement PROTOCOL_VERSION, but tolerate a client echoing an
# older/newer supported string and negotiate down/across to our version.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18",)

# Session header per the Streamable HTTP transport.
SESSION_HEADER = "Mcp-Session-Id"
PROTOCOL_VERSION_HEADER = "MCP-Protocol-Version"

SERVER_NAME = "odoo-mcp-server"
SERVER_VERSION = "16.0.1.0.0"

# Tool categories used for audit + alerting classification.
CATEGORY_READ = "read"
CATEGORY_WRITE = "write"
CATEGORY_CONFIRM = "confirm"
CATEGORY_ADMIN = "admin"
