# -*- coding: utf-8 -*-
"""MCP protocol engine.

This package is import-safe: importing it must not touch the Odoo registry or
database. Domain add-ons register their tools/resources at *import time* via the
decorators exposed here (``tool``, ``resource_template``), which is deterministic
because module import order follows manifest ``depends``.
"""
from . import constants
from . import exceptions
from . import jsonrpc
from . import schema
from . import registry
from . import protocol

# Importing generic_tools registers the core "odoo.*" generic engine tools.
from . import generic_tools  # noqa: F401,E402
