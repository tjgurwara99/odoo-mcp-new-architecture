# MCP Server - Purchase

Domain add-on for **MCP Server (Core)** (`mcp_server`). Registers curated MCP
tools for the day-to-day work of a purchaser, plus downloadable PDF report
links for RFQs and purchase orders.

## Tools

| Tool | Kind | Description |
| --- | --- | --- |
| `purchase.search_orders` | read | Search RFQs/orders by text, state, vendor, purchaser, date range. |
| `purchase.get_order` | read | Full order detail incl. lines and a PDF link. |
| `purchase.list_products` | read | Look up purchasable products and cost prices. |
| `purchase.get_order_pdf` | read | Short-lived, tokenized PDF download link for an order. |
| `purchase.create_rfq` | write | Create a draft RFQ with order lines. |
| `purchase.add_order_line` | write | Add a product line to a draft/sent RFQ. |
| `purchase.update_order` | write | Update vendor reference / planned date / note. |
| `purchase.send_rfq` | write | Mark an RFQ as sent to the vendor. |
| `purchase.confirm_order` | write | Confirm an RFQ into a purchase order. |
| `purchase.create_vendor_bill` | write | Generate a draft vendor bill from a confirmed PO (records the supplier invoice). |
| `purchase.cancel_order` | write | Cancel a RFQ / purchase order. |

Tool names reach MCP clients in wire-safe form (e.g. `purchase_create_rfq`).

## PDF report links

`purchase.get_order` and `purchase.get_order_pdf` return a `pdf_url` produced by
the core `mcp.report.link` facility:

- The URL contains a high-entropy opaque token (only its SHA-256 hash is stored).
- Opening it renders `purchase.action_report_purchase_order` **as the user who
  requested it** — no Odoo login required to open the link, but record rules
  and ACLs are still enforced at render time.
- Links expire after `mcp_server.report_link_ttl` (default 1h) and are GC'd by
  cron.

> Requires **Public Base URL** to be set in *Settings > MCP Server* (used to
> build absolute link URLs). Without it, read tools return `pdf_url: null` with
> a note, and `purchase.get_order_pdf` returns a tool error explaining the fix.

## Design

- **No transport code** beyond the shared core endpoint; tools only register
  into the core registry at import time.
- **Runs as the user** — no `sudo` in tool logic; Purchase ACL/record rules apply.
- **Visible to MCP users** (`mcp_server.group_mcp_user`).
- **Writes are gated** via the shared propose/confirm token workflow; editing
  tools refuse orders that are not in `draft`/`sent`.

## Install / test

```bash
odoo -d <db> -i mcp_server_purchase --stop-after-init
odoo -d <db> -i mcp_server_purchase --test-enable --test-tags /mcp_server_purchase --stop-after-init
```
