# -*- coding: utf-8 -*-
{
    "name": "MCP Server - Accounting",
    "version": "16.0.1.0.0",
    "category": "Technical",
    "summary": "Curated MCP tools for Odoo Invoicing/Accounting: search/read "
    "customer & vendor invoices, check status and customer balances, list "
    "overdue invoices, create/post invoices, register payments, and generate "
    "downloadable invoice PDF links.",
    "description": """
MCP Server - Accounting
=======================
Domain add-on for the **MCP Server (Core)** module. Registers a curated set of
Invoicing / Accounting tools, all executed as the authenticated ``res.users``
(standard Accounting ACLs and record rules apply). The whole tool set is only
visible to users holding the Invoicing access group
(``account.group_account_invoice``); real ACL enforcement still happens per
call.

Read tools
----------
* ``accounting.search_invoices`` - find customer/vendor invoices & credit notes.
* ``accounting.get_invoice`` - full invoice detail incl. lines and a PDF link.
* ``accounting.get_invoice_status`` - quick state / payment-state summary.
* ``accounting.get_customer_balance`` - a partner's receivable / due / overdue.
* ``accounting.list_overdue_invoices`` - unpaid posted invoices past due date.
* ``accounting.get_invoice_pdf`` - short-lived, downloadable invoice PDF link.

Write tools (two-step propose/confirm)
--------------------------------------
* ``accounting.create_customer_invoice`` - draft customer invoice with lines.
* ``accounting.post_invoice`` - post (validate) a draft invoice.
* ``accounting.register_payment`` - register a payment against a posted invoice.

PDF links are produced through the core ``mcp.report.link`` facility: a
tokenized, short-lived URL that renders the report as the requesting user.
""",
    "author": "Taj Singh <tjgurwara99@gmail.com>",
    "website": "https://github.com/tjgurwara99/odoo-mcp-new-architecture",
    "license": "OPL-1",
    "depends": ["mcp_server", "account"],
    "data": [],
    "assets": {},
    "installable": True,
    "application": False,
    "auto_install": False,
}
