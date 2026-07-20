# MCP Server - Sales

Domain add-on for **MCP Server (Core)** (`mcp_server`). Registers curated MCP
tools for the day-to-day work of a salesperson, plus downloadable PDF report
links for quotations and sale orders.

## Tools

| Tool | Kind | Description |
| --- | --- | --- |
| `sales.search_orders` | read | Search quotations/orders by text, state, customer, salesperson, date range. |
| `sales.get_order` | read | Full order detail incl. lines and a PDF link. |
| `sales.list_products` | read | Look up sellable products and prices. |
| `sales.get_order_pdf` | read | Short-lived, tokenized PDF download link for an order. |
| `sales.create_quotation` | write | Create a draft quotation with order lines. |
| `sales.add_order_line` | write | Add a product line to a draft/sent quotation. |
| `sales.update_order` | write | Update reference / validity / note. |
| `sales.set_quotation_sent` | write | Mark a quotation as sent. |
| `sales.confirm_order` | write | Confirm a quotation into a sale order. |
| `sales.cancel_order` | write | Cancel a quotation / order. |

Tool names reach MCP clients in wire-safe form (e.g. `sales_create_quotation`).

## PDF report links

`sales.get_order` and `sales.get_order_pdf` return a `pdf_url` produced by the
core `mcp.report.link` facility:

- The URL contains a high-entropy opaque token (only its SHA-256 hash is stored).
- Opening it renders `sale.action_report_saleorder` **as the user who
  requested it** — no Odoo login required to open the link, but record rules
  and ACLs are still enforced at render time.
- Links expire after `mcp_server.report_link_ttl` (default 1h) and are GC'd by
  cron.

> Requires **Public Base URL** to be set in *Settings > MCP Server* (used to
> build absolute link URLs). Without it, read tools return `pdf_url: null` with
> a note, and `sales.get_order_pdf` returns a tool error explaining the fix.

## Design

- **No transport code** beyond the shared core endpoint; tools only register
  into the core registry at import time.
- **Runs as the user** — no `sudo` in tool logic; Sales ACL/record rules apply.
- **Visible to MCP users** (`mcp_server.group_mcp_user`).
- **Writes are gated** via the shared propose/confirm token workflow; editing
  tools refuse orders that are not in `draft`/`sent`.

## Install / test

```bash
odoo -d <db> -i mcp_server_sales --stop-after-init
odoo -d <db> -i mcp_server_sales --test-enable --test-tags /mcp_server_sales --stop-after-init
```
