# Odoo MCP Connector — Project Plan

**Author:** Taj Singh <tjgurwara99@gmail.com>
**Target platform:** Odoo 16 (Community/Enterprise-compatible), on-premise self-managed deployment
**Goal:** A production-grade set of Odoo add-ons that expose Odoo business functionality to Claude (and any MCP-compliant client) as a **remote MCP Connector** over HTTP, with OAuth 2.1 authentication, per-user permission enforcement, write-confirmation safety, and a full audit trail.

---

## 1. Decisions Locked In (from requirements discussion)

| Topic | Decision |
|---|---|
| Odoo version | 16 |
| MCP protocol implementation | Hand-rolled inside Odoo (no official Python MCP SDK dependency). **Primary rationale:** the MCP Python SDK is asyncio-based, whereas Odoo controllers run in a synchronous, threaded/prefork WSGI model — integrating an async SDK into Odoo's worker model is awkward and error-prone. (Note: the older "Odoo 16 runs on 3.8/3.9" rationale is unreliable — Odoo 16's official requirement is Python 3.10+, so the SDK would often be version-compatible anyway. Verify the target instance's actual Python version early, but the async/threaded mismatch is the deciding factor regardless.) We implement JSON-RPC 2.0 + the MCP **Streamable HTTP** transport ourselves as Odoo HTTP controllers. |
| Transport | HTTP (Streamable HTTP transport per current MCP spec), not stdio — this is a remote Claude "Connector", not a Desktop-config subprocess |
| Auth between Claude and Odoo | Minimal OAuth 2.1 Authorization Server built into the module (Authorization Code + PKCE), so Claude's remote-connector OAuth flow works out of the box. Maps OAuth tokens 1:1 to real Odoo `res.users` accounts. |
| Tool design | Curated, hand-written domain tools (predictable schemas, safe scoped actions) **plus** a generic, admin-configurable engine that can expose arbitrary allow-listed models/fields/actions for a given user — both gated by the same permission layer |
| Domains in scope (v1) | Sales (orders/quotations), Invoicing/Accounting (conditional on user's access), Inventory/Stock, Contacts/Partners, plus the generic "any model I'm allowed to see" engine |
| Permission model | Tool calls execute strictly as the authenticated Odoo user — normal ORM, `ir.rule`, ACL, field-level security all apply. No `sudo()` in tool execution paths. |
| Write safety | All create/write/unlink/state-changing calls follow a two-step **propose → confirm** flow: first call returns a preview + short-lived confirmation token, second call commits using that token. |
| Multi-company | Single database, multi-company aware — tools accept/derive `company_id` context and respect the calling user's allowed companies |
| Audit | Full audit trail stored as Odoo records (who, tool, arguments, result/error, timestamp, linked OAuth client), with an admin UI (list/search/filter) and configurable alerting on sensitive tool categories |
| MCP capabilities | Tools + Resources (v1). Prompts deferred to a later phase. |
| Target MCP protocol version | Pin an explicit `protocolVersion` (target **2025-06-18**) and negotiate it in `initialize`. This is not cosmetic — behaviors differ across versions: JSON-RPC **batching was removed** in 2025-06-18 (was mandatory in 2025-03-26), OAuth **Resource Indicators (RFC 8707) are required**, and **elicitation** was added. Implement to one pinned version and reject/negotiate others explicitly. |
| Packaging | Core module `mcp_server` (protocol engine, OAuth provider, audit, generic model engine, admin config UI) + separate thin domain add-ons: `mcp_server_sales`, `mcp_server_accounting`, `mcp_server_inventory`, `mcp_server_contacts`, each auto-registering its tools/resources into the core registry |
| Deployment | On-premise, self-managed nginx/Apache reverse proxy doing TLS termination in front of Odoo |
| Dev/test | Existing Odoo 16 dev instance available (connection details to be provided later) |
| License | Custom (to be supplied by you — placeholder `LICENSE` file for now) |
| Repo | Git-initialized in this folder from the start, with structured commits per milestone |

---

## 2. High-Level Architecture

```
                          HTTPS (TLS via nginx/Apache)
                                    │
                     ┌──────────────┴───────────────┐
                     │        Odoo 16 Server          │
                     │  ┌───────────────────────────┐ │
Claude  ───────────► │  │  mcp_server (core module)  │ │
(remote MCP          │  │                            │ │
 connector)          │  │  - OAuth 2.1 AS endpoints  │ │
                     │  │  - MCP HTTP endpoint       │ │
                     │  │    (JSON-RPC 2.0, single-  │ │
                     │  │     JSON responses; SSE    │ │
                     │  │     only when streaming)   │ │
                     │  │  - Tool/Resource registry  │ │
                     │  │  - Confirmation-token svc  │ │
                     │  │  - Audit log models + UI   │ │
                     │  │  - Generic model engine    │ │
                     │  │    (allow-listed CRUD)     │ │
                     │  └─────────────┬─────────────┘ │
                     │                │ registers tools │
                     │   ┌────────────┼────────────┐   │
                     │   ▼            ▼            ▼   │
                     │ mcp_server_ mcp_server_  mcp_server_
                     │  sales     accounting    inventory  ...
                     │   │            │            │
                     │   ▼            ▼            ▼
                     │        Odoo ORM (as the authenticated user, ACL/ir.rule enforced)
                     └───────────────────────────────┘
```

Each domain add-on is a normal Odoo module that, on load, calls a registration API exposed by `mcp_server` (similar to how Odoo modules register `ir.actions` or report handlers) to add its tool/resource definitions to the shared registry. No domain module talks HTTP directly — `mcp_server` alone owns the protocol/transport/auth layer.

---

## 3. Module Breakdown

### 3.1 `mcp_server` (core)

**Responsibilities:**
1. **MCP protocol/transport layer**
   - HTTP controller(s) implementing the MCP **Streamable HTTP** transport: a single `POST /mcp` endpoint accepting JSON-RPC 2.0 requests/batches, optionally upgrading to SSE for streamed responses/server-initiated messages, plus session management (`Mcp-Session-Id` header) per spec.
   - Implements the required MCP lifecycle: `initialize`, `initialized` notification, capability negotiation, `tools/list`, `tools/call`, `resources/list`, `resources/read`, `resources/templates/list`, `ping`. **Note: there is no `shutdown` JSON-RPC method in MCP** — session termination is a transport concern, handled via `HTTP DELETE /mcp` carrying the `Mcp-Session-Id` header. Implement DELETE-based session teardown, not a `shutdown` method.
   - **Two distinct error channels (must not be conflated):**
     - *Protocol errors* (unknown method, invalid params, malformed JSON-RPC, auth failure) → JSON-RPC `error` object with standard codes.
     - *Tool execution errors* (business logic failed, permission denied inside a tool, validation failure) → a **successful** JSON-RPC result with `isError: true` and the error described in `content`. These are NOT JSON-RPC errors. Getting this wrong prevents Claude from reasoning about failed tool calls; both paths get explicit tests in Phase 1.
   - **Transport preference:** respond to `POST /mcp` with a single plain JSON body for ordinary request/response tool calls (the spec allows this). Reserve SSE for genuine streaming / server-initiated messages only — see the worker-model caveat in §3.1 note below.
   - **Batching:** for target version 2025-06-18, do NOT implement JSON-RPC batch handling (it was removed from the spec). If a client sends a batch, reject per spec.

2. **OAuth 2.1 Authorization Server**
   - Endpoints: `/mcp/oauth/authorize`, `/mcp/oauth/token`, `/mcp/oauth/register` (Dynamic Client Registration — Claude's remote connector flow uses this), `.well-known/oauth-authorization-server` metadata document, `.well-known/oauth-protected-resource`.
   - PKCE (S256) mandatory, short-lived authorization codes, refresh tokens with rotation, access-token TTL configurable.
   - **Resource Indicators (RFC 8707), required by MCP 2025-06-18:** clients send a `resource` parameter binding tokens to this MCP server. The server MUST validate the token's intended audience/resource on every request and reject tokens not scoped to it — this prevents confused-deputy / token-passthrough attacks. Treat this as a Phase 2 requirement with explicit tests, not an optional extra.
   - This module acts as **both** the OAuth Authorization Server and the Resource Server (combined, single-module). That is permitted; document the assumption so a future split (external AS) stays possible.
   - New models: `mcp.oauth.client` (registered client apps), `mcp.oauth.token` (access/refresh tokens, hashed at rest), `mcp.oauth.auth.code` (one-time codes).
   - The `/authorize` step reuses Odoo's own login screen (leveraging existing `res.users` session auth) + a consent screen showing what the client will be able to do, then issues a code tied to that logged-in `res.users` record.
   - Token introspection used by the MCP controller on every request to resolve `access_token → res.users`, then all ORM calls in **tool execution paths** run with that user's uid (`request.env(user=uid)`), never `sudo()`.
   - **`sudo` boundary clarification:** the "no `sudo()`" rule applies to *tool business logic only*. Framework bookkeeping — writing `mcp.audit.log`, creating/consuming confirmation and OAuth tokens — must succeed regardless of the calling user's create rights on those internal models, so it legitimately uses controlled elevated writes (`sudo()`/dedicated system context). The rule is: tool logic never widens the user's data access; plumbing may write internal records the user can't. This distinction is enforced by keeping bookkeeping out of tool callables.

3. **Tool & Resource Registry**
   - A Python-level registration API (e.g. `mcp_server.registry.register_tool(...)`, `register_resource_provider(...)`) that domain modules call.
   - **Registration timing:** prefer **import-time registration via decorators** (module import order is deterministic through manifest `depends`) or `_register_hook`, rather than the manifest `post_load` — `post_load` runs at bootstrap before the registry/env is fully ready and is easy to get wrong. Pin one pattern in Phase 1 so every domain module follows it identically.
   - The in-memory registry is per-worker (each prefork worker builds it at load) — this is fine because it is derived deterministically from installed modules. Do **not** store request/session state here (see the session-store note below).
   - Registry entries carry: name, JSON-schema for input, human description, category (for audit/alerting), whether it's a "safe read" or "write requiring confirmation", the Python callable, and the minimal Odoo group/permission expected (defense-in-depth on top of ACL).
   - `tools/list` and `resources/list` are computed **per authenticated user** — i.e., a tool only appears if the user's Odoo permissions plausibly allow it (e.g., hide `sales.create_quotation` from a user with no Sales access), in addition to real ACL enforcement at call time.

4. **Generic Model Engine** (admin-configurable, ACL-gated)
   - New model `mcp.model.access` (admin UI: pick model, allowed fields, allowed operations [read/search/create/write/unlink], optional domain filter) — effectively a curated allowlist layered on top of normal Odoo `ir.model.access`/`ir.rule`.
   - Exposes generic tools: `odoo.search_records`, `odoo.read_record`, `odoo.create_record`, `odoo.update_record`, `odoo.delete_record`, `odoo.call_action` — but every call is checked against `mcp.model.access` **and** real ACL/record rules; nothing bypasses standard Odoo security.
   - This is what satisfies "admin can expose all models that a particular user is allowed to work with" without turning the MCP surface into unrestricted ORM access.

5. **Confirmation-Token Workflow**
   - Shared service (`mcp.action.confirmation` model) used by any write tool (curated or generic):
     - Step 1 (`propose`): tool validates input, computes a preview/diff (e.g., "Will create Sale Order for Partner X, 3 lines, total $Y"), stores the pending action + serialized args with a random token and short TTL (e.g. 5 minutes), returns the preview + token to Claude.
     - Step 2 (`confirm`): a second tool call (`odoo.confirm_action` or domain-specific `confirm_*`) with the token executes the actual ORM write inside a transaction, marks the token consumed, and logs the result.
   - Tokens are single-use, user-bound, and expire automatically (cron cleanup).
   - **Design tradeoffs / MCP-native layering:** the propose→confirm token pattern is robust *server-side* enforcement, but it doubles round-trips and LLMs sometimes mishandle two-step flows. Layer MCP-native signals on top: set tool annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`) so clients like Claude surface intent and prompt for approval; optionally evaluate **elicitation** (2025-06-18) as an alternative confirm channel where client support exists. Keep the token workflow as the authoritative enforcement mechanism regardless of client behavior.

6. **Audit Log**
   - Model `mcp.audit.log`: timestamp, user, oauth client, tool name, input args (sanitized/truncated), result summary/error, duration, confirmation-token linkage, IP/user agent.
   - Every `tools/call` (and confirm step) writes exactly one audit entry, success or failure.
   - Admin menu (under Settings/Technical) with searchable/filterable list + pivot/graph view for usage patterns; sensitive-category tool calls (configurable list, e.g. `unlink`, `accounting.*`) can trigger an activity/email alert to configured admins via Odoo's existing `mail` infrastructure.

7. **Resources**
   - Core resource types: read-only record snapshot (`odoo://<model>/<id>`), report/PDF resources (`odoo://report/<report_name>/<res_id>`), leveraging Odoo's existing QWeb/report rendering.
   - These are **templated** URIs — expose them via `resources/templates/list` (parameterized templates), not by trying to enumerate every record in a static `resources/list`. Static `resources/list` is reserved for a small, enumerable set (if any).
   - Domain modules can register additional resource providers (e.g. accounting invoice PDF, stock delivery slip).

8. **Admin configuration UI**
   - Settings page: enable/disable MCP server, manage OAuth clients, manage `mcp.model.access` allowlist, view audit log, configure token TTLs, configure sensitive-action alert recipients.

**Security posture:** everything funnels through Odoo's normal `env.user`/ACL/`ir.rule` machinery; the module adds *narrowing* layers (registry visibility, `mcp.model.access` allowlist, confirmation tokens, audit), never *widening* ones.

**Worker-model & state caveats (critical for correctness under Odoo prefork):**
- **SSE ties up a worker.** A long-lived SSE connection occupies an Odoo HTTP worker for its whole lifetime; with a small fixed worker count, a handful of open MCP sessions can starve the server. Mitigations: (a) default to single-JSON-body responses for `POST /mcp` tool calls and only open SSE when streaming/server-initiated messages are actually required; (b) if SSE is kept, size workers accordingly and evaluate the gevent/longpolling worker; (c) cover this in Phase 7 load testing.
- **Session & token state must be cross-worker.** The next request for a given `Mcp-Session-Id` may land on a different worker/process, so MCP session state, confirmation tokens, and OAuth tokens must live in the DB (or Redis), never in worker memory. (Confirmation/OAuth tokens are already modeled as DB records — extend the same rule to MCP session state.)

### 3.2 Domain Add-ons

Each is a small module depending on `mcp_server` + the relevant Odoo business module (`sale`, `account`, `stock`, `contacts`). Pattern for each: a `tools.py` with one class per logical tool group, decorated/registered via the core registry API, pure business logic that calls the standard ORM as `self.env` under the current user.

- **`mcp_server_sales`** (depends on `sale`)
  - Read tools: `sales.list_quotations`, `sales.get_order`, `sales.search_orders_by_customer`, `sales.get_order_lines`
  - Write tools (confirmation-gated): `sales.create_quotation`, `sales.add_order_line`, `sales.confirm_order`, `sales.cancel_order`
  - Resource: order PDF quotation report

- **`mcp_server_accounting`** (depends on `account`)
  - Visibility gated further by whether the user has `account.group_account_invoice`/`account.group_account_user` etc. (tool list should simply not appear otherwise)
  - Read tools: `accounting.get_invoice`, `accounting.list_invoices`, `accounting.get_invoice_status`, `accounting.get_customer_balance`, `accounting.list_overdue_invoices`
  - Write tools (confirmation-gated): `accounting.create_customer_invoice`, `accounting.post_invoice`, `accounting.register_payment`
  - Resource: invoice PDF

- **`mcp_server_inventory`** (depends on `stock`)
  - Read tools: `inventory.get_product_quantity`, `inventory.list_stock_moves`, `inventory.get_delivery_status`
  - Write tools (confirmation-gated): `inventory.create_transfer`, `inventory.validate_delivery`, `inventory.adjust_quantity`
  - Resource: delivery slip / picking report PDF

- **`mcp_server_contacts`** (depends on `contacts`/`base`)
  - Read tools: `contacts.search_partners`, `contacts.get_partner`, `contacts.get_partner_activities`
  - Write tools (confirmation-gated): `contacts.create_partner`, `contacts.update_partner`

Additional domains (Project/Tasks, Helpdesk, HR) can follow the same pattern in later phases.

---

## 4. Repository Layout

```
odoo-mcp-new-architecture/
├── LICENSE                       # placeholder, custom license to be added
├── README.md
├── PLAN.md                       # this document
├── .gitignore
├── .pre-commit-config.yaml       # black/isort/flake8/pylint-odoo
├── pyproject.toml / setup.cfg    # lint/test config
├── docker/                       # optional local dev/test scaffold (docker-compose w/ postgres+odoo)
├── addons/
│   ├── mcp_server/
│   │   ├── __manifest__.py
│   │   ├── __init__.py
│   │   ├── controllers/          # http.py: MCP endpoint, OAuth endpoints, well-known docs
│   │   ├── models/                # oauth client/token, audit log, model access, confirmation
│   │   ├── mcp/                    # protocol engine: jsonrpc.py, registry.py, session.py, sse.py
│   │   ├── security/               # ir.model.access.csv, groups, record rules
│   │   ├── views/                  # admin UI xml
│   │   ├── data/                   # default OAuth scopes, cron jobs (token/code cleanup)
│   │   ├── static/description/
│   │   └── tests/
│   ├── mcp_server_sales/
│   ├── mcp_server_accounting/
│   ├── mcp_server_inventory/
│   └── mcp_server_contacts/
└── docs/
    ├── architecture.md
    ├── oauth-setup.md            # how to register Claude as a connector, redirect URIs, etc.
    ├── tool-catalog.md           # generated/maintained list of all tools + schemas
    └── security.md
```

---

## 5. Phased Delivery Plan

**Phase 0 — Scaffolding & environment**
- Git repo hygiene: `.gitignore`, LICENSE placeholder, README, this PLAN.md (committed now).
- `addons/mcp_server` skeleton module installs cleanly on the dev Odoo 16 instance (empty manifest, boots, shows in Apps).
- Lint/test tooling wired (pylint-odoo, black, a `tests/` package using Odoo's `TransactionCase`/`HttpCase`).
- CI (if desired later): GitHub Actions running module tests against a Postgres service container.

**Phase 1 — Core protocol engine (no auth yet, dev-only)**
- Implement JSON-RPC 2.0 handling + MCP lifecycle (`initialize` with pinned `protocolVersion`, `initialized`, `tools/list`, `tools/call`, `ping`, `HTTP DELETE /mcp` session teardown) behind a controller reachable only by an already-authenticated Odoo session (temporary, for local testing with e.g. `mcp-inspector`).
- Tool/resource registry API (import-time decorator registration pattern, fixed here for all modules).
- DB/Redis-backed MCP session store (not worker memory); single-JSON-body responses by default, SSE path stubbed.
- Unit tests for protocol correctness: malformed requests, unknown methods, capability negotiation, and crucially the **two error channels** — protocol errors as JSON-RPC `error` vs. tool-execution errors as results with `isError: true`.

**Phase 2 — OAuth 2.1 Authorization Server**
- Dynamic client registration, `/authorize` (reusing Odoo login + consent screen), `/token` (auth code + refresh, PKCE mandatory), metadata `.well-known` documents.
- Token ↔ `res.users` resolution wired into the MCP controller; remove the temporary dev-only auth from Phase 1.
- Security tests: PKCE required, expired/replayed codes rejected, token scoping, token revocation, and **Resource Indicator / audience validation** (RFC 8707) — tokens not scoped to this MCP server are rejected.

**Phase 3 — Generic model engine + admin allowlist UI**
- `mcp.model.access` model + Settings UI.
- Generic tools (`odoo.search_records`, etc.) with strict allowlist + real ACL/`ir.rule` enforcement.
- Confirmation-token service (shared, reusable by domain modules later).
- Tests: attempting to touch a non-allow-listed model/field fails; attempting to bypass another user's `ir.rule`-scoped records fails; confirm-token replay/expiry fails safely.

**Phase 4 — Audit log + alerting**
- `mcp.audit.log` model, admin list/pivot views, automatic logging wrapper around every tool call.
- Sensitive-category alerting via `mail.activity`/email.
- Tests: every call path (success, validation error, permission error) produces exactly one audit record.

**Phase 5 — Domain add-ons, one at a time**
- `mcp_server_contacts` first (simplest, good template).
- `mcp_server_sales`, `mcp_server_accounting`, `mcp_server_inventory` following the same template.
- Each ships: tools, resources, its own tests, and an entry in `docs/tool-catalog.md`.

**Phase 6 — Resources polish + docs**
- Report/PDF resource providers per domain.
- `docs/oauth-setup.md` walkthrough for registering the connector in Claude, reverse-proxy config sample (nginx) for TLS + streaming (SSE needs `proxy_buffering off`, long read timeouts, `Connection` header handling).

**Phase 7 — Hardening & production readiness**
- Rate limiting per OAuth client/user (simple token-bucket stored in Odoo or Redis if available).
- Load/soak testing of the HTTP endpoint under concurrent sessions.
- Security review pass: token storage hashing, HTTPS-only cookie/redirect checks, CSRF on the consent screen, input size limits, timeouts on long-running tool calls.
- Upgrade/migration notes, versioned manifest, changelog.

**Phase 8 — End-to-end validation with Claude**
- Register the deployed server as a Claude remote Connector, run through the OAuth flow, exercise each tool domain live, validate audit entries, tune tool descriptions/schemas based on real model behavior.

---

## 6. Open Items to Resolve Before/At Phase 0–1 Start

These are smaller, can be decided as we start coding rather than blocking the plan:
- Exact dev instance connection details (URL, DB name, admin creds) — you mentioned you'll provide these.
- Whether Redis is available in the deployment (affects rate-limiting/session-store choice) or we stay Postgres-only.
- Preferred reverse proxy (nginx vs Apache) so we can write the exact sample config in `docs/oauth-setup.md`.
- The actual custom license text for `LICENSE`.
- Odoo 16 is approaching end-of-support; keep a forward-port path to 17/18 in mind (avoid 16-only APIs where a cheap portable alternative exists). Not a blocker for v1.

---

## 7. Next Step

If this plan looks good, I'll start with **Phase 0**: scaffold the repo (`.gitignore`, README, `addons/mcp_server` skeleton with manifest/security/empty controller), commit it, and confirm it installs cleanly on your dev instance once you share connection details — then proceed into Phase 1 (protocol engine).
