# MCP Server - Inventory

Domain add-on for **MCP Server (Core)** (`mcp_server`). Registers curated MCP
tools for warehouse / stock operations, plus downloadable delivery-slip PDF
links.

## Tools

| Tool | Kind | Description |
| --- | --- | --- |
| `inventory.check_stock` | read | On-hand / forecast / available quantities; **on-hand products ranked first**, optional on-hand-only filter and per-location breakdown. |
| `inventory.search_transfers` | read | Find receipts / deliveries / internal transfers. |
| `inventory.get_transfer` | read | Transfer detail incl. moves and a delivery-slip PDF link. |
| `inventory.get_transfer_pdf` | read | Short-lived, tokenized delivery-slip PDF link. |
| `inventory.list_warehouses` | read | Warehouses + main stock location. |
| `inventory.list_locations` | read | Stock locations (default: internal). |
| `inventory.create_transfer` | write | Create a receipt / delivery / internal transfer with moves. |
| `inventory.validate_transfer` | write | Validate (complete) a transfer. |
| `inventory.adjust_quantity` | write | Set on-hand quantity of a product at a location. |

Tool names reach MCP clients in wire-safe form (e.g. `inventory_check_stock`).

## Notes

- **Runs as the user** — no `sudo`; Inventory ACL / record rules apply. Inventory
  adjustments and validations require the corresponding Inventory rights.
- **Visible to MCP users** (`mcp_server.group_mcp_user`).
- **Writes are gated** via the shared propose/confirm token workflow.
- `create_transfer` derives source/destination from the picking type, falling
  back to the vendor/customer locations for receipts/deliveries. Pass
  `location_id` / `location_dest_id` to override.
- `check_stock` **prioritises products that are on hand**: results are driven by
  `stock.quant` and ordered with the most-stocked products first (within the
  requested location/warehouse scope), then any zero-stock catalogue matches.
  - `only_on_hand: true` returns only products with a positive on-hand quantity
    in scope.
  - `group_by_location: true` adds a `by_location` breakdown per product
    (`location_id`, `qty_on_hand`, `qty_reserved`, `qty_available`).
  - `location_id` scopes to a location **and its sub-locations** (`child_of`);
    `warehouse_id` scopes to the warehouse's locations.
  - The response includes `on_hand_count` and `sorted_by: "on-hand first"`.
- `validate_transfer` fills any missing done-quantities to demand, then
  validates (handling immediate-transfer / backorder wizards defensively).
- `adjust_quantity` uses Odoo 16 inventory adjustments (`stock.quant`
  `inventory_quantity` + `action_apply_inventory`).
- Delivery-slip PDF links use the core `mcp.report.link` facility and require
  **Public Base URL** to be set in *Settings > MCP Server*.

## Install / test

```bash
odoo -d <db> -i mcp_server_inventory --stop-after-init
odoo -d <db> -i mcp_server_inventory --test-enable --test-tags /mcp_server_inventory --stop-after-init
```
