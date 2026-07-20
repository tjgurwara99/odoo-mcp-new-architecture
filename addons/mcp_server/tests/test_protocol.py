# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase

from odoo.addons.mcp_server.mcp import constants, exceptions
from odoo.addons.mcp_server.mcp.registry import registry, ToolDefinition
from odoo.addons.mcp_server.mcp.protocol import ExecutionContext, MCPProtocol


def _echo(env, arguments):
    return {"echoed": arguments}


def _boom(env, arguments):
    raise exceptions.ToolExecutionError("business rule failed", details={"x": 1})


def _crash(env, arguments):
    raise ValueError("unexpected explosion")


class TestProtocol(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        registry.add_tool(ToolDefinition(
            "test.echo", "echo", {"type": "object", "properties": {}}, _echo,
            category=constants.CATEGORY_READ))
        registry.add_tool(ToolDefinition(
            "test.boom", "boom", {"type": "object", "properties": {}}, _boom,
            category=constants.CATEGORY_READ))
        registry.add_tool(ToolDefinition(
            "test.crash", "crash", {"type": "object", "properties": {}}, _crash,
            category=constants.CATEGORY_READ))

    def _protocol(self):
        ctx = ExecutionContext(env=self.env)
        return MCPProtocol(ctx)

    # -- lifecycle -----------------------------------------------------------
    def test_initialize_pins_protocol_version(self):
        result = self._protocol().dispatch("initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(result["protocolVersion"], constants.PROTOCOL_VERSION)
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], constants.SERVER_NAME)

    def test_initialize_negotiates_unknown_version_down(self):
        result = self._protocol().dispatch("initialize", {"protocolVersion": "1999-01-01"})
        self.assertEqual(result["protocolVersion"], constants.PROTOCOL_VERSION)

    def test_ping(self):
        self.assertEqual(self._protocol().dispatch("ping", {}), {})

    def test_unknown_method_is_protocol_error(self):
        with self.assertRaises(exceptions.MethodNotFound):
            self._protocol().dispatch("does/not/exist", {})

    # -- tools ---------------------------------------------------------------
    def test_tools_list_contains_registered(self):
        result = self._protocol().dispatch("tools/list", {})
        names = {t["name"] for t in result["tools"]}
        # Names are exposed in wire-safe form (dots -> underscores).
        self.assertIn("test_echo", names)
        self.assertIn("odoo_search_records", names)
        self.assertNotIn("test.echo", names)

    def test_tools_call_accepts_wire_name(self):
        # Claude calls back with the sanitised name; it must resolve.
        result = self._protocol().dispatch(
            "tools/call", {"name": "test_echo", "arguments": {"a": 1}})
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["echoed"], {"a": 1})

    def test_tools_call_success(self):
        result = self._protocol().dispatch(
            "tools/call", {"name": "test.echo", "arguments": {"a": 1}})
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["echoed"], {"a": 1})

    def test_tool_execution_error_is_result_not_jsonrpc_error(self):
        # PLAN.md §3.1: tool errors come back as a *successful* result with
        # isError=true, NEVER as a JSON-RPC error.
        result = self._protocol().dispatch(
            "tools/call", {"name": "test.boom", "arguments": {}})
        self.assertTrue(result["isError"])
        self.assertIn("business rule failed", result["content"][0]["text"])

    def test_unexpected_exception_becomes_tool_error(self):
        result = self._protocol().dispatch(
            "tools/call", {"name": "test.crash", "arguments": {}})
        self.assertTrue(result["isError"])

    def test_unknown_tool_is_protocol_error(self):
        with self.assertRaises(exceptions.MethodNotFound):
            self._protocol().dispatch(
                "tools/call", {"name": "nope.nope", "arguments": {}})

    def test_tools_call_missing_name_is_invalid_params(self):
        with self.assertRaises(exceptions.InvalidParams):
            self._protocol().dispatch("tools/call", {"arguments": {}})

    def test_resources_templates_list(self):
        result = self._protocol().dispatch("resources/templates/list", {})
        self.assertIn("resourceTemplates", result)
