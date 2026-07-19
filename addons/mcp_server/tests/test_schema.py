# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase

from odoo.addons.mcp_server.mcp.schema import validate_arguments


class TestSchema(TransactionCase):
    SCHEMA = {
        "type": "object",
        "properties": {
            "model": {"type": "string"},
            "id": {"type": "integer"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            "mode": {"type": "string", "enum": ["a", "b"]},
        },
        "required": ["model"],
    }

    def test_valid(self):
        self.assertIsNone(validate_arguments({"model": "res.partner"}, self.SCHEMA))

    def test_missing_required(self):
        self.assertIn("required", validate_arguments({"id": 1}, self.SCHEMA))

    def test_wrong_type(self):
        self.assertIn("type", validate_arguments({"model": 3}, self.SCHEMA))

    def test_enum(self):
        err = validate_arguments({"model": "x", "mode": "c"}, self.SCHEMA)
        self.assertIn("one of", err)

    def test_minimum(self):
        err = validate_arguments({"model": "x", "limit": 0}, self.SCHEMA)
        self.assertIn("minimum", err)

    def test_confirmation_token_always_tolerated(self):
        schema = {"type": "object", "properties": {"a": {"type": "string"}},
                  "additionalProperties": False}
        self.assertIsNone(
            validate_arguments({"a": "x", "confirmation_token": "tok"}, schema)
        )
