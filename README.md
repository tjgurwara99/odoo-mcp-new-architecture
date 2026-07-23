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
- `addons/mcp_server_reports_designer` — expose GTECH Report Designer
  (`reports.designer`) custom reports as short-lived PDF download links.

## Status

**Core module `mcp_server` implemented** (PLAN.md Phases 1–4):

- MCP Streamable HTTP transport (JSON-RPC 2.0) at `POST /mcp`, `DELETE /mcp`
  session teardown, pinned protocol version `2025-06-18` (no batching).
- Two error channels: protocol errors as JSON-RPC `error`; tool errors as
  successful results with `isError: true`.
- OAuth 2.1 AS + Resource Server: DCR, Authorization Code + PKCE (S256),
  refresh-token rotation, RFC 8707 resource-indicator binding, RFC 7009 revoke,
  `.well-known` metadata. Secrets hashed at rest.
- Import-time tool/resource registry for domain add-ons.
- Generic model engine (`odoo.*`) gated by the `mcp.model.access` allowlist plus
  real Odoo ACL/record rules.
- Propose → confirm workflow for all writes (single-use, user-bound tokens).
- Full audit log with pivot/graph views and sensitive-action alerting.
- Admin UI (Settings + dedicated MCP app menu) and cron GC jobs.
- Test suite (`addons/mcp_server/tests/`) covering JSON-RPC, schema, protocol
  error channels, confirmation flow, generic engine, OAuth/PKCE, and audit.

Run tests:

```bash
odoo -d <db> -i mcp_server --test-enable --stop-after-init
```

Domain add-ons (`mcp_server_sales`, `_inventory`, `_contacts`) follow the same
template; `mcp_server_accounting` is implemented (PLAN.md Phase 5).
