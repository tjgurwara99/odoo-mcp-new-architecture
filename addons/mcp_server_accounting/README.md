# MCP Server - Accounting

Domain add-on for **MCP Server (Core)** (`mcp_server`). Registers curated MCP
tools for Invoicing / Accounting, plus downloadable invoice PDF links.

## Tools

| Tool | Kind | Description |
| --- | --- | --- |
| `accounting.search_invoices` | read | Find customer/vendor invoices & credit notes by text, type, state, payment state, partner, date range. |
| `accounting.get_invoice` | read | Full invoice detail incl. lines and a PDF link. |
| `accounting.get_invoice_status` | read | Quick state / payment-state / amount-due summary. |
| `accounting.get_customer_balance` | read | A partner's receivable, total due and overdue plus open-invoice count. |
| `accounting.list_overdue_invoices` | read | Posted, unpaid/partial customer invoices past due date. |
| `accounting.list_products` | read | Products with sales & cost price for building invoices. |
| `accounting.get_invoice_pdf` | read | Short-lived, tokenized invoice PDF link. |
| `accounting.create_customer_invoice` | write | Create a draft customer invoice with lines. |
| `accounting.create_vendor_bill` | write | Create a standalone draft vendor bill with lines. |
| `accounting.post_invoice` | write | Post (validate) a draft invoice. |
| `accounting.register_payment` | write | Register a payment against a posted invoice. |

Tool names reach MCP clients in wire-safe form (e.g. `accounting_get_invoice`).

## Notes

- **Runs as the user** — no `sudo`; Accounting ACL / record rules apply. Posting
  invoices and registering payments require the corresponding Accounting rights.
- **Visible to invoicing users** — the tool set only appears for users holding
  both `mcp_server.group_mcp_user` and `account.group_account_invoice`; real ACL
  enforcement still happens per call.
- **Writes are gated** via the shared propose/confirm token workflow (single-use,
  user-bound tokens).
- `create_customer_invoice` builds `out_invoice` moves; each line takes a
  `product_id` (drives account/taxes/price) or a free-text `description`, with an
  optional `quantity` / `price_unit` override.
- `create_vendor_bill` builds `in_invoice` moves the same way, for supplier
  invoices not tied to a purchase order (for a PO-driven bill, use
  `purchase.create_vendor_bill` in the Purchase add-on).
- `register_payment` drives the standard `account.payment.register` wizard,
  defaulting to the full residual amount and the default bank/cash journal.
- Invoice PDF links use the core `mcp.report.link` facility
  (`account.account_invoices`) and require **Public Base URL** to be set in
  *Settings > MCP Server*.

## Install / test

```bash
odoo -d <db> -i mcp_server_accounting --stop-after-init
odoo -d <db> -i mcp_server_accounting --test-enable --test-tags /mcp_server_accounting --stop-after-init
```
