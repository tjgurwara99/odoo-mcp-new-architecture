# -*- coding: utf-8 -*-
{
    "name": "MCP Server - Contacts",
    "version": "16.0.1.0.0",
    "category": "Technical",
    "summary": "Curated MCP tools for Odoo Contacts (res.partner): search, "
    "read, activities, and confirmation-gated create/update.",
    "description": """
MCP Server - Contacts
=====================
Domain add-on for the **MCP Server (Core)** module. On install it registers a
small set of curated, business-friendly tools for working with contacts
(``res.partner``) through the MCP connector:

Read tools
----------
* ``contacts.search_partners`` - fuzzy search across name/email/phone/ref/VAT.
* ``contacts.get_partner`` - full detail for one contact, incl. child contacts.
* ``contacts.get_partner_activities`` - scheduled activities for a contact.

Write tools (two-step propose/confirm)
--------------------------------------
* ``contacts.create_partner`` - create a contact from curated fields.
* ``contacts.update_partner`` - update curated fields on a contact.

All tools run as the authenticated ``res.users`` account, so standard Odoo
ACLs, record rules and field-level security apply. Tools are only visible to
members of the *MCP User* group. This module owns no HTTP/transport code - it
only registers tool definitions into the core registry.
""",
    "author": "Taj Singh <tjgurwara99@gmail.com>",
    "website": "https://github.com/tjgurwara99/odoo-mcp-new-architecture",
    "license": "OPL-1",
    "depends": ["mcp_server", "contacts"],
    "data": [],
    "assets": {},
    "installable": True,
    "application": False,
    "auto_install": False,
}
