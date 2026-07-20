# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase

from odoo.addons.mcp_server.mcp import exceptions
from odoo.addons.mcp_server_contacts.tools import (
    search_partners,
    get_partner,
    get_partner_activities,
    create_partner,
    update_partner,
)


class TestContactsTools(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Partner = self.env["res.partner"]
        self.acme = self.Partner.create(
            {"name": "ACME Corp", "is_company": True, "email": "info@acme.test"}
        )
        self.jane = self.Partner.create(
            {"name": "Jane Doe", "email": "jane@acme.test", "parent_id": self.acme.id}
        )

    def _propose(self, func, args):
        """Run a write tool's propose step and return its confirmation token.

        Avoids ``assertRaises`` (which Odoo wraps in a savepoint that would roll
        back the pending confirmation record before the confirm call).
        """
        try:
            func(self.env, args)
            self.fail("expected ConfirmationRequired")
        except exceptions.ConfirmationRequired as cr:
            return cr.token

    # -- search --------------------------------------------------------------
    def test_search_by_name(self):
        result = search_partners(self.env, {"query": "ACME"})
        names = {r["name"] for r in result["records"]}
        self.assertIn("ACME Corp", names)
        self.assertGreaterEqual(result["total"], 1)

    def test_search_by_email(self):
        result = search_partners(self.env, {"query": "jane@acme.test"})
        self.assertTrue(any(r["id"] == self.jane.id for r in result["records"]))

    def test_search_company_filter(self):
        result = search_partners(self.env, {"query": "acme", "is_company": True})
        self.assertTrue(all(r["is_company"] for r in result["records"]))
        self.assertTrue(any(r["id"] == self.acme.id for r in result["records"]))

    def test_search_respects_limit(self):
        for i in range(5):
            self.Partner.create({"name": "Bulk %s" % i})
        result = search_partners(self.env, {"query": "Bulk", "limit": 2})
        self.assertEqual(result["returned"], 2)
        self.assertGreaterEqual(result["total"], 5)

    # -- get_partner ---------------------------------------------------------
    def test_get_partner_includes_children(self):
        result = get_partner(self.env, {"id": self.acme.id})
        self.assertEqual(result["partner"]["name"], "ACME Corp")
        child_ids = {c["id"] for c in result["partner"]["child_contacts"]}
        self.assertIn(self.jane.id, child_ids)

    def test_get_partner_missing(self):
        with self.assertRaises(exceptions.ToolExecutionError):
            get_partner(self.env, {"id": 999999})

    # -- activities ----------------------------------------------------------
    def test_get_partner_activities_empty(self):
        result = get_partner_activities(self.env, {"partner_id": self.acme.id})
        self.assertEqual(result["activities"], [])

    def test_get_partner_activities_lists(self):
        self.env["mail.activity"].create(
            {
                "res_model_id": self.env.ref("base.model_res_partner").id,
                "res_id": self.acme.id,
                "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
                "summary": "Follow up",
                "date_deadline": "2030-01-01",
            }
        )
        result = get_partner_activities(self.env, {"partner_id": self.acme.id})
        self.assertEqual(result["returned"], 1)
        self.assertEqual(result["activities"][0]["summary"], "Follow up")

    # -- create (propose/confirm) -------------------------------------------
    def test_create_requires_confirmation(self):
        token = self._propose(create_partner, {"name": "New Person", "email": "n@x.test"})
        self.assertTrue(token)
        # Nothing created yet during propose.
        self.assertFalse(self.Partner.search([("name", "=", "New Person")]))

    def test_create_commits_with_token(self):
        args = {"name": "New Person", "email": "n@x.test", "country_code": "US"}
        token = self._propose(create_partner, args)
        result = create_partner(self.env, dict(args, confirmation_token=token))
        self.assertTrue(result["created"])
        partner = self.Partner.browse(result["partner"]["id"])
        self.assertEqual(partner.name, "New Person")
        self.assertEqual(partner.country_id.code, "US")

    def test_create_requires_name(self):
        with self.assertRaises(exceptions.ToolExecutionError):
            create_partner(self.env, {"email": "no-name@x.test"})

    def test_create_unknown_country(self):
        with self.assertRaises(exceptions.ToolExecutionError):
            create_partner(self.env, {"name": "X", "country_code": "Nowhereland"})

    # -- update (propose/confirm) -------------------------------------------
    def test_update_commits_with_token(self):
        args = {"id": self.jane.id, "phone": "+1 555 0100", "function": "CFO"}
        token = self._propose(update_partner, args)
        update_partner(self.env, dict(args, confirmation_token=token))
        self.jane.invalidate_recordset()
        self.assertEqual(self.jane.phone, "+1 555 0100")
        self.assertEqual(self.jane.function, "CFO")

    def test_update_requires_a_field(self):
        with self.assertRaises(exceptions.ToolExecutionError):
            update_partner(self.env, {"id": self.jane.id})

    def test_update_missing_record(self):
        with self.assertRaises(exceptions.ToolExecutionError):
            update_partner(self.env, {"id": 999999, "phone": "123"})

    def test_confirmation_token_bound_to_arguments(self):
        token = self._propose(update_partner, {"id": self.jane.id, "phone": "111"})
        # Re-using the token with different arguments must be rejected.
        with self.assertRaises(exceptions.ToolExecutionError):
            update_partner(
                self.env,
                {"id": self.jane.id, "phone": "222", "confirmation_token": token},
            )
