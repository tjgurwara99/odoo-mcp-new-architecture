# -*- coding: utf-8 -*-
{
    "name": "MCP Server - Sales",
    "version": "16.0.1.0.0",
    "category": "Technical",
    "summary": "Curated MCP tools for Odoo Sales: search/read quotations & "
    "orders, build and confirm quotations, list products, and generate "
    "downloadable PDF report links.",
    "description": """
MCP Server - Sales
==================
Domain add-on for the **MCP Server (Core)** module. Registers a curated set of
tools a salesperson would expect, all executed as the authenticated
``res.users`` (standard Sales ACLs and record rules apply):

Read tools
----------
* ``sales.search_orders`` - find quotations / sale orders with filters.
* ``sales.get_order`` - full order detail incl. lines and a PDF link.
* ``sales.list_products`` - look up sellable products and prices.
* ``sales.get_order_pdf`` - get a short-lived, downloadable PDF link for an
  order/quotation (rendered on demand, no Odoo login needed to open it).

Write tools (two-step propose/confirm)
--------------------------------------
* ``sales.create_quotation`` - create a draft quotation with order lines.
* ``sales.add_order_line`` - add a product line to a draft/sent quotation.
* ``sales.update_order`` - update header fields (reference, validity, note).
* ``sales.set_quotation_sent`` - mark a quotation as sent.
* ``sales.confirm_order`` - confirm a quotation into a sale order.
* ``sales.cancel_order`` - cancel a quotation / order.

PDF links are produced through the core ``mcp.report.link`` facility: a
tokenized, short-lived URL that renders the report as the requesting user.
""",
    "author": "Taj Singh <tjgurwara99@gmail.com>",
    "website": "https://github.com/tjgurwara99/odoo-mcp-new-architecture",
    "license": "OPL-1",
    "depends": ["mcp_server", "sale"],
    "data": [],
    "assets": {},
    "installable": True,
    "application": False,
    "auto_install": False,
}
