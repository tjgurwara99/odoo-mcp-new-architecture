# -*- coding: utf-8 -*-
{
    "name": "MCP Server - Inventory",
    "version": "16.0.1.0.0",
    "category": "Technical",
    "summary": "Curated MCP tools for Odoo Inventory (stock): check stock, "
    "search/read transfers, create & validate transfers, inventory "
    "adjustments, and delivery-slip PDF links.",
    "description": """
MCP Server - Inventory
======================
Domain add-on for the **MCP Server (Core)** module. Registers a curated set of
warehouse tools, all executed as the authenticated ``res.users`` (standard
Inventory ACLs and record rules apply):

Read tools
----------
* ``inventory.check_stock`` - on-hand / forecast / available quantities for
  products, optionally per warehouse or location.
* ``inventory.search_transfers`` - find pickings (receipts, deliveries,
  internal transfers) with filters.
* ``inventory.get_transfer`` - full transfer detail incl. moves and a
  delivery-slip PDF link.
* ``inventory.get_transfer_pdf`` - short-lived downloadable delivery-slip link.
* ``inventory.list_warehouses`` / ``inventory.list_locations`` - reference data
  for building transfers and adjustments.

Write tools (two-step propose/confirm)
--------------------------------------
* ``inventory.create_transfer`` - create a receipt / delivery / internal
  transfer with product moves.
* ``inventory.validate_transfer`` - validate (complete) a transfer.
* ``inventory.adjust_quantity`` - set the on-hand quantity of a product at a
  location (inventory adjustment).

Delivery-slip PDFs are produced through the core ``mcp.report.link`` facility
(tokenized, short-lived, rendered as the requesting user).
""",
    "author": "Taj Singh <tjgurwara99@gmail.com>",
    "website": "https://github.com/tjgurwara99/odoo-mcp-new-architecture",
    "license": "OPL-1",
    "depends": ["mcp_server", "stock"],
    "data": [],
    "assets": {},
    "installable": True,
    "application": False,
    "auto_install": False,
}
