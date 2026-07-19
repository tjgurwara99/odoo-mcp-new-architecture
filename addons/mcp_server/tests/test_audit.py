# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase

from odoo.addons.mcp_server.mcp import constants, exceptions
from odoo.addons.mcp_server.mcp.registry import registry, ToolDefinition
from odoo.addons.mcp_server.mcp.protocol import ExecutionContext, MCPProtocol


def _ok(env, arguments):
    return {"ok": True}


def _fail(env, arguments):
    raise exceptions.ToolExecutionError("nope")


class TestAudit(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        registry.add_tool(ToolDefinition(
            "audit.ok", "ok", {"type": "object", "properties": {}}, _ok))
        registry.add_tool(ToolDefinition(
            "audit.fail", "fail", {"type": "object", "properties": {}}, _fail))

    def _count(self):
        return self.env["mcp.audit.log"].search_count([])

    def _call(self, name, args=None):
        proto = MCPProtocol(ExecutionContext(env=self.env))
        return proto.dispatch("tools/call", {"name": name, "arguments": args or {}})

    def test_success_writes_one_record(self):
        before = self._count()
        self._call("audit.ok")
        self.assertEqual(self._count(), before + 1)
        log = self.env["mcp.audit.log"].search([], order="id desc", limit=1)
        self.assertEqual(log.tool_name, "audit.ok")
        self.assertFalse(log.is_error)

    def test_tool_error_writes_one_record_flagged_error(self):
        before = self._count()
        self._call("audit.fail")
        self.assertEqual(self._count(), before + 1)
        log = self.env["mcp.audit.log"].search([], order="id desc", limit=1)
        self.assertEqual(log.tool_name, "audit.fail")
        self.assertTrue(log.is_error)

    def test_schema_validation_error_is_audited(self):
        registry.add_tool(ToolDefinition(
            "audit.needs_arg", "x",
            {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
            _ok))
        before = self._count()
        result = self._call("audit.needs_arg", {})
        self.assertTrue(result["isError"])
        self.assertEqual(self._count(), before + 1)

    def test_sensitive_args_redacted(self):
        self._call("audit.ok", {"password": "supersecret", "keep": "visible"})
        log = self.env["mcp.audit.log"].search([], order="id desc", limit=1)
        self.assertNotIn("supersecret", log.arguments)
        self.assertIn("visible", log.arguments)
