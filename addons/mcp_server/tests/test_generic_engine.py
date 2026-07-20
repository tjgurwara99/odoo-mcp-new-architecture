# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase

from odoo.addons.mcp_server.mcp import exceptions
from odoo.addons.mcp_server.mcp.generic_tools import (
    search_records, read_record, create_record, update_record, delete_record,
)


class TestGenericEngine(TransactionCase):
    def setUp(self):
        super().setUp()
        self.partner_model = self.env.ref("base.model_res_partner")
        self.env["mcp.model.access"].search([]).unlink()

    def _allow(self, **kw):
        vals = {
            "model_id": self.partner_model.id,
            "allow_read": True, "allow_search": True,
            "allow_create": False, "allow_write": False, "allow_unlink": False,
        }
        vals.update(kw)
        return self.env["mcp.model.access"].create(vals)

    # -- allowlist gate ------------------------------------------------------
    def test_non_allowlisted_model_blocked(self):
        with self.assertRaises(exceptions.ToolExecutionError):
            search_records(self.env, {"model": "res.partner"})

    def test_search_allowed_model(self):
        self._allow()
        result = search_records(self.env, {"model": "res.partner", "limit": 5})
        self.assertEqual(result["model"], "res.partner")
        self.assertIn("records", result)

    def test_operation_not_permitted(self):
        self._allow(allow_create=False)
        with self.assertRaises(exceptions.ToolExecutionError):
            create_record(self.env, {"model": "res.partner", "values": {"name": "X"}})

    # -- field whitelist -----------------------------------------------------
    def test_field_whitelist_enforced_on_read(self):
        self._allow(allowed_fields="name,email")
        with self.assertRaises(exceptions.ToolExecutionError):
            search_records(self.env, {"model": "res.partner", "fields": ["name", "credit_limit"]})

    def test_writable_field_whitelist(self):
        self._allow(allow_create=True, writable_fields="name")
        with self.assertRaises(exceptions.ToolExecutionError):
            create_record(self.env, {
                "model": "res.partner", "values": {"name": "X", "email": "a@b.c"}})

    def _propose(self, func, args):
        """Run a write tool's propose step and return the confirmation token.

        We deliberately avoid ``assertRaises`` here: Odoo wraps that in a
        savepoint and rolls back on the expected exception, which would undo the
        pending confirmation record created during propose (in production the
        propose and confirm are two separate request transactions).
        """
        try:
            func(self.env, args)
            self.fail("expected ConfirmationRequired")
        except exceptions.ConfirmationRequired as cr:
            return cr.token

    # -- propose/confirm on writes -------------------------------------------
    def test_create_requires_confirmation_then_commits(self):
        self._allow(allow_create=True)
        token = self._propose(
            create_record, {"model": "res.partner", "values": {"name": "ACME"}})
        result = create_record(self.env, {
            "model": "res.partner", "values": {"name": "ACME"},
            "confirmation_token": token})
        self.assertTrue(result["created"])
        partner = self.env["res.partner"].browse(result["id"])
        self.assertEqual(partner.name, "ACME")

    def test_delete_requires_confirmation(self):
        self._allow(allow_unlink=True)
        partner = self.env["res.partner"].create({"name": "ToDelete"})
        token = self._propose(
            delete_record, {"model": "res.partner", "id": partner.id})
        result = delete_record(self.env, {
            "model": "res.partner", "id": partner.id, "confirmation_token": token})
        self.assertTrue(result["deleted"])
        self.assertFalse(partner.exists())

    def test_update_commits_with_token(self):
        self._allow(allow_write=True)
        partner = self.env["res.partner"].create({"name": "Old"})
        args = {"model": "res.partner", "id": partner.id, "values": {"name": "New"}}
        token = self._propose(update_record, args)
        args["confirmation_token"] = token
        update_record(self.env, args)
        self.assertEqual(partner.name, "New")
