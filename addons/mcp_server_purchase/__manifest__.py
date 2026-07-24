# -*- coding: utf-8 -*-
{
    "name": "MCP Server - Purchase",
    "version": "16.0.1.0.0",
    "category": "Technical",
    "summary": "Curated MCP tools for Odoo Purchase: search/read RFQs & "
    "purchase orders, build and confirm RFQs, list purchasable products, and "
    "generate downloadable PDF report links.",
    "description": """
MCP Server - Purchase
=====================
Domain add-on for the **MCP Server (Core)** module. Registers a curated set of
tools a purchaser would expect, all executed as the authenticated
``res.users`` (standard Purchase ACLs and record rules apply):

Read tools
----------
* ``purchase.search_orders`` - find RFQs / purchase orders with filters.
* ``purchase.get_order`` - full order detail incl. lines and a PDF link.
* ``purchase.list_products`` - look up purchasable products and costs.
* ``purchase.get_order_pdf`` - get a short-lived, downloadable PDF link for an
  RFQ / purchase order (rendered on demand, no Odoo login needed to open it).

Write tools (two-step propose/confirm)
--------------------------------------
* ``purchase.create_rfq`` - create a draft RFQ with order lines.
* ``purchase.add_order_line`` - add a product line to a draft/sent RFQ.
* ``purchase.update_order`` - update header fields (vendor ref, planned date, note).
* ``purchase.send_rfq`` - mark an RFQ as sent to the vendor.
* ``purchase.confirm_order`` - confirm an RFQ into a purchase order.
* ``purchase.create_vendor_bill`` - generate a draft vendor bill from a
  confirmed PO so a supplier's invoice can be recorded against it.
* ``purchase.cancel_order`` - cancel an RFQ / purchase order.

PDF links are produced through the core ``mcp.report.link`` facility: a
tokenized, short-lived URL that renders the report as the requesting user.
""",
    "author": "Taj Singh <tjgurwara99@gmail.com>",
    "website": "https://github.com/tjgurwara99/odoo-mcp-new-architecture",
    "license": "OPL-1",
    "depends": ["mcp_server", "purchase", "account"],
    "data": [],
    "assets": {},
    "installable": True,
    "application": False,
    "auto_install": False,
}
