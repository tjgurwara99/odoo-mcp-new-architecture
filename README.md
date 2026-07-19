# Odoo MCP Connector

Production-grade Odoo add-ons that expose Odoo business functionality to
Claude (and other MCP-compliant clients) as a remote Model Context Protocol
(MCP) connector over HTTP, with OAuth 2.1 auth, per-user Odoo permission
enforcement, write-confirmation safety, and full audit logging.

See [PLAN.md](./PLAN.md) for the full architecture and delivery plan.

## Modules

- `addons/mcp_server` — core: MCP protocol engine, OAuth 2.1 provider,
  generic model access engine, confirmation-token workflow, audit log.
- `addons/mcp_server_sales` — Sales orders/quotations tools.
- `addons/mcp_server_accounting` — Invoicing/Accounting tools.
- `addons/mcp_server_inventory` — Inventory/Stock tools.
- `addons/mcp_server_contacts` — Contacts/Partners tools.

## Status

Planning complete. Implementation starting at Phase 0 (scaffolding).
