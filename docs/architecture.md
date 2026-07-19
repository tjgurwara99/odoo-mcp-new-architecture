# Architecture — `mcp_server` core

This document summarises the implemented core module. See `PLAN.md` for the full
project plan and rationale.

## Request lifecycle (`POST /mcp`)

```
Client → nginx (TLS) → Odoo HTTP worker
                         │
   controllers/main.py   ▼
   1. check mcp_server.enabled
   2. resolve Bearer token → mcp.oauth.token → res.users
      - RFC 8707 audience check (token.resource == <base>/mcp)
   3. parse + validate JSON-RPC envelope (no batching — 2025-06-18)
   4. env = request.env(user=uid)         # NEVER sudo for tool logic
   5. resolve/open mcp.session (DB-backed, cross-worker)
   6. mcp/protocol.py MCPProtocol.dispatch(method, params)
   7. single JSON body response (SSE reserved for real streaming)
```

## Two error channels (do not conflate)

| Failure | Wire form |
|---|---|
| Bad method / invalid params / malformed / auth | JSON-RPC `error` object |
| Business / permission / validation *inside a tool* | successful result, `isError: true`, message in `content` |

Implemented in `mcp/protocol.py` (`_run_tool`) + `mcp/exceptions.py`
(`JsonRpcError` vs `ToolExecutionError`).

## Registry

`mcp/registry.py` exposes import-time decorators `@tool(...)` and
`@resource_template(...)`. Registration is deterministic via manifest `depends`.
The registry is per-worker, derived from installed modules, and holds **no**
request/session state. `tools/list` is filtered per-user by declared
`required_groups`; real ACL is still enforced at call time.

## Generic model engine

`mcp/generic_tools.py` registers `odoo.search_records`, `odoo.read_record`,
`odoo.create_record`, `odoo.update_record`, `odoo.delete_record`,
`odoo.call_action`. Every call is gated by the admin `mcp.model.access` allowlist
**and** normal Odoo ACL/record rules. Writes go through propose/confirm.

## Propose → confirm

`mcp.action.confirmation.require(tool, args, preview)`:
* no `confirmation_token` in args → create pending token, raise
  `ConfirmationRequired` (surfaced as a successful result with the preview +
  token);
* valid token bound to same user + tool + args hash → consume and proceed.

Tokens are single-use, user-bound, TTL-expired (cron GC). Bookkeeping uses
`sudo` — the sanctioned plumbing elevation; tool *logic* never uses sudo.

## OAuth 2.1 AS + Resource Server

`controllers/oauth.py` + `models/mcp_oauth_*`:
* `.well-known/oauth-authorization-server`, `.well-known/oauth-protected-resource`
* `/mcp/oauth/register` (RFC 7591 DCR)
* `/mcp/oauth/authorize` (reuses Odoo login, consent screen) + `/decision`
* `/mcp/oauth/token` (authorization_code + PKCE S256, refresh with rotation)
* `/mcp/oauth/revoke` (RFC 7009)

All secrets hashed at rest (SHA-256). Tokens carry a `resource` (RFC 8707)
validated on every MCP request.

## Audit

`mcp.audit.log` — one record per tool call (success, tool error, validation
error). Sensitive args redacted, payloads truncated. Pivot/graph views +
optional `mail.activity` alerts on sensitive categories/prefixes.

## Security posture

Only *narrowing* layers are added (registry visibility, `mcp.model.access`,
confirmation tokens, audit). Nothing widens a user's data access.
