# MCP Server - Contacts

Domain add-on for **MCP Server (Core)** (`mcp_server`). It registers a small,
curated set of MCP tools for working with Odoo contacts (`res.partner`).

## Tools

| Tool | Kind | Description |
| --- | --- | --- |
| `contacts.search_partners` | read | Fuzzy search across name/email/phone/mobile/ref/VAT, with optional company and country filters. |
| `contacts.get_partner` | read | Full detail for one contact, including its child contacts. |
| `contacts.get_partner_activities` | read | Scheduled activities planned on a contact, by due date. |
| `contacts.create_partner` | write | Create a contact from curated fields. Two-step propose/confirm. |
| `contacts.update_partner` | write | Update curated fields on a contact. Two-step propose/confirm. |

> Tool names are exposed to MCP clients in wire-safe form (e.g.
> `contacts_search_partners`) because clients require `^[a-zA-Z0-9_-]{1,64}$`.
> The core registry maps them back automatically on `tools/call`.

## Design

- **No transport code.** This module only registers tool definitions into the
  core registry (`odoo.addons.mcp_server.mcp.registry.tool`) at import time.
- **Runs as the user.** Every tool executes with the authenticated
  `res.users`' `env` — standard ACLs, record rules and field security apply.
  No `sudo` anywhere in tool logic.
- **Visibility gate.** Tools are only listed for members of the *MCP User*
  group (`mcp_server.group_mcp_user`).
- **Writes are gated.** `create_partner` / `update_partner` use the shared
  propose/confirm workflow: the first call returns a preview + a single-use
  `confirmation_token`; the client re-calls with the same arguments plus the
  token to commit.

## Curated write fields

`name`, `email`, `phone`, `mobile`, `is_company`, `street`, `street2`, `city`,
`zip`, `website`, `vat`, `function`, `comment`, `parent_id`, and `country_code`
(ISO code or country name, resolved to `country_id`).

## Install

```bash
odoo -d <db> -i mcp_server_contacts --stop-after-init
```

## Test

```bash
odoo -d <db> -i mcp_server_contacts --test-enable --test-tags /mcp_server_contacts --stop-after-init
```
