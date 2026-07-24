# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.mcp_server.mcp import exceptions
from odoo.addons.mcp_server_accounting.tools import (
    search_invoices,
    get_invoice,
    get_invoice_status,
    get_customer_balance,
    list_overdue_invoices,
    list_products,
    get_invoice_pdf,
    create_customer_invoice,
    create_vendor_bill,
    post_invoice,
    register_payment,
)


# Runs post_install so the accounting chart / journals installed by the base
# ``account`` module and any bridge modules are fully present.
@tagged("post_install", "-at_install")
class TestAccountingTools(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.public_base_url", "https://example.test"
        )
        self.partner = self.env["res.partner"].create({"name": "Acme Corp"})
        self.widget = self.env["product.product"].create(
            {"name": "Consulting", "list_price": 500.0, "sale_ok": True}
        )
        self.gadget = self.env["product.product"].create(
            {"name": "Support", "list_price": 150.0, "sale_ok": True}
        )

    def _propose(self, func, args):
        try:
            func(self.env, args)
            self.fail("expected ConfirmationRequired")
        except exceptions.ConfirmationRequired as cr:
            return cr.token

    def _new_invoice(self, lines=None):
        args = {
            "partner_id": self.partner.id,
            "invoice_lines": lines
            or [{"product_id": self.widget.id, "quantity": 2}],
        }
        token = self._propose(create_customer_invoice, args)
        result = create_customer_invoice(
            self.env, dict(args, confirmation_token=token)
        )
        move = self.env["account.move"].browse(result["invoice"]["id"])
        return move, result

    def _post(self, move):
        token = self._propose(post_invoice, {"invoice_id": move.id})
        return post_invoice(
            self.env, {"invoice_id": move.id, "confirmation_token": token}
        )

    def test_create_vendor_bill(self):
        args = {
            "partner_id": self.partner.id,
            "invoice_lines": [{"product_id": self.widget.id, "quantity": 3}],
            "ref": "INV-2024-42",
            "invoice_date": "2024-02-01",
        }
        token = self._propose(create_vendor_bill, args)
        result = create_vendor_bill(self.env, dict(args, confirmation_token=token))
        self.assertTrue(result["created"])
        move = self.env["account.move"].browse(result["invoice"]["id"])
        self.assertEqual(move.move_type, "in_invoice")
        self.assertEqual(move.state, "draft")
        self.assertEqual(move.ref, "INV-2024-42")
        # A created vendor bill can be posted through the shared post tool.
        self._post(move)
        self.assertEqual(move.state, "posted")

    def test_create_vendor_bill_requires_a_line(self):
        with self.assertRaises(exceptions.ToolExecutionError):
            create_vendor_bill(
                self.env, {"partner_id": self.partner.id, "invoice_lines": []}
            )

    # -- products ------------------------------------------------------------
    def test_list_products(self):
        result = list_products(self.env, {"query": "Consulting"})
        names = {r["name"] for r in result["records"]}
        self.assertIn("Consulting", names)

    # -- create invoice ------------------------------------------------------
    def test_create_invoice_draft(self):
        move, result = self._new_invoice()
        self.assertTrue(result["created"])
        self.assertEqual(move.state, "draft")
        self.assertEqual(move.move_type, "out_invoice")
        self.assertEqual(len(move.invoice_line_ids), 1)
        self.assertEqual(move.invoice_line_ids.quantity, 2)
        self.assertTrue(
            result["invoice"]["pdf_url"].startswith(
                "https://example.test/mcp/report/"
            )
        )

    def test_create_invoice_explicit_price(self):
        move, _ = self._new_invoice(
            [{"product_id": self.widget.id, "quantity": 1, "price_unit": 400.0}]
        )
        self.assertEqual(move.invoice_line_ids.price_unit, 400.0)

    def test_create_invoice_description_only_line(self):
        move, _ = self._new_invoice(
            [{"description": "Ad-hoc service", "quantity": 1, "price_unit": 99.0}]
        )
        self.assertEqual(len(move.invoice_line_ids), 1)
        self.assertEqual(move.invoice_line_ids.price_unit, 99.0)

    def test_create_requires_a_line(self):
        with self.assertRaises(exceptions.ToolExecutionError):
            create_customer_invoice(
                self.env, {"partner_id": self.partner.id, "invoice_lines": []}
            )

    def test_create_bad_partner(self):
        with self.assertRaises(exceptions.ToolExecutionError):
            create_customer_invoice(
                self.env,
                {
                    "partner_id": 999999,
                    "invoice_lines": [{"product_id": self.widget.id}],
                },
            )

    def test_line_without_product_or_description_fails(self):
        with self.assertRaises(exceptions.ToolExecutionError):
            create_customer_invoice(
                self.env,
                {"partner_id": self.partner.id, "invoice_lines": [{"quantity": 1}]},
            )

    # -- post ----------------------------------------------------------------
    def test_post_invoice(self):
        move, _ = self._new_invoice()
        result = self._post(move)
        self.assertTrue(result["posted"])
        self.assertEqual(move.state, "posted")
        self.assertTrue(move.name and move.name != "/")

    def test_cannot_post_twice(self):
        move, _ = self._new_invoice()
        self._post(move)
        with self.assertRaises(exceptions.ToolExecutionError):
            post_invoice(self.env, {"invoice_id": move.id})

    # -- register payment ----------------------------------------------------
    def test_register_payment_full(self):
        move, _ = self._new_invoice()
        self._post(move)
        self.assertEqual(move.payment_state, "not_paid")
        args = {"invoice_id": move.id}
        token = self._propose(register_payment, args)
        result = register_payment(self.env, dict(args, confirmation_token=token))
        self.assertTrue(result["paid"])
        self.assertTrue(result["payment_ids"])
        self.assertIn(move.payment_state, ("paid", "in_payment"))

    def test_cannot_pay_draft(self):
        move, _ = self._new_invoice()
        with self.assertRaises(exceptions.ToolExecutionError):
            register_payment(self.env, {"invoice_id": move.id})

    # -- search & read -------------------------------------------------------
    def test_search_invoices(self):
        move, _ = self._new_invoice()
        result = search_invoices(self.env, {"partner_id": self.partner.id})
        self.assertTrue(any(r["id"] == move.id for r in result["records"]))

    def test_search_by_type_and_state(self):
        self._new_invoice()
        result = search_invoices(
            self.env, {"move_type": "out_invoice", "state": "draft"}
        )
        self.assertTrue(all(r["move_type"] == "out_invoice" for r in result["records"]))
        self.assertTrue(all(r["state"] == "draft" for r in result["records"]))

    def test_get_invoice_includes_lines_and_pdf(self):
        move, _ = self._new_invoice()
        result = get_invoice(self.env, {"id": move.id})
        self.assertEqual(result["invoice"]["id"], move.id)
        self.assertEqual(len(result["invoice"]["invoice_lines"]), 1)
        self.assertTrue(result["invoice"]["pdf_url"])

    def test_get_invoice_status(self):
        move, _ = self._new_invoice()
        result = get_invoice_status(self.env, {"id": move.id})
        self.assertEqual(result["id"], move.id)
        self.assertEqual(result["state"], "draft")
        self.assertEqual(result["payment_state"], "not_paid")

    def test_get_invoice_rejects_non_invoice(self):
        entry = self.env["account.move"].create({"move_type": "entry"})
        with self.assertRaises(exceptions.ToolExecutionError):
            get_invoice(self.env, {"id": entry.id})

    # -- customer balance & overdue -----------------------------------------
    def test_customer_balance(self):
        move, _ = self._new_invoice()
        self._post(move)
        result = get_customer_balance(self.env, {"partner_id": self.partner.id})
        self.assertEqual(result["partner_id"], self.partner.id)
        self.assertGreaterEqual(result["open_customer_invoices"], 1)

    def test_customer_balance_bad_partner(self):
        with self.assertRaises(exceptions.ToolExecutionError):
            get_customer_balance(self.env, {"partner_id": 999999})

    def test_list_overdue_invoices(self):
        move, _ = self._new_invoice()
        move.invoice_date = "2000-01-01"
        move.invoice_date_due = "2000-01-15"
        self._post(move)
        result = list_overdue_invoices(self.env, {"partner_id": self.partner.id})
        self.assertTrue(any(r["id"] == move.id for r in result["records"]))

    # -- pdf link ------------------------------------------------------------
    def test_get_invoice_pdf_link(self):
        move, _ = self._new_invoice()
        result = get_invoice_pdf(self.env, {"id": move.id})
        self.assertTrue(
            result["pdf_url"].startswith("https://example.test/mcp/report/")
        )
        self.assertIn(".pdf", result["filename"])

    def test_get_invoice_pdf_requires_base_url(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.public_base_url", ""
        )
        self.env["ir.config_parameter"].sudo().set_param("web.base.url", "")
        move, _ = self._new_invoice()
        with self.assertRaises(exceptions.ToolExecutionError):
            get_invoice_pdf(self.env, {"id": move.id})
