# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase

from odoo.addons.mcp_server.mcp import jsonrpc, exceptions


class TestJsonRpc(TransactionCase):
    def test_parse_valid_object(self):
        msg = jsonrpc.parse_message('{"jsonrpc":"2.0","method":"ping","id":1}')
        self.assertEqual(msg["method"], "ping")

    def test_parse_malformed_raises_parse_error(self):
        with self.assertRaises(exceptions.ParseError):
            jsonrpc.parse_message("{not json")

    def test_parse_batch_rejected(self):
        # Batching was removed in 2025-06-18 -> invalid request.
        with self.assertRaises(exceptions.InvalidRequest):
            jsonrpc.parse_message('[{"jsonrpc":"2.0","method":"ping","id":1}]')

    def test_parse_non_object_rejected(self):
        with self.assertRaises(exceptions.InvalidRequest):
            jsonrpc.parse_message('"a string"')

    def test_validate_missing_version(self):
        with self.assertRaises(exceptions.InvalidRequest):
            jsonrpc.validate_request({"method": "ping", "id": 1})

    def test_validate_missing_method(self):
        with self.assertRaises(exceptions.InvalidRequest):
            jsonrpc.validate_request({"jsonrpc": "2.0", "id": 1})

    def test_validate_notification_has_no_id(self):
        method, params, msg_id, is_notification = jsonrpc.validate_request(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        self.assertTrue(is_notification)
        self.assertIsNone(msg_id)

    def test_validate_request_with_id(self):
        method, params, msg_id, is_notification = jsonrpc.validate_request(
            {"jsonrpc": "2.0", "method": "ping", "id": 7, "params": {}}
        )
        self.assertFalse(is_notification)
        self.assertEqual(msg_id, 7)
        self.assertEqual(method, "ping")

    def test_make_result_and_error(self):
        res = jsonrpc.make_result(1, {"ok": True})
        self.assertEqual(res["result"], {"ok": True})
        err = jsonrpc.make_error(1, exceptions.MethodNotFound("x"))
        self.assertEqual(err["error"]["code"], exceptions.METHOD_NOT_FOUND)
