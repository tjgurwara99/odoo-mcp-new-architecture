# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.mcp_server.mcp import exceptions
from odoo.addons.mcp_server_purchase.tools import (
    search_orders,
    get_order,
    list_products,
    get_order_pdf,
    create_rfq,
    add_order_line,
    update_order,
    send_rfq,
    confirm_order,
    cancel_order,
    create_vendor_bill,
)


# Run post_install so any auto-installed purchase bridges (e.g. purchase_stock)
# and their field extensions are present before the tools execute.
@tagged("post_install", "-at_install")
class TestPurchaseTools(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.public_base_url", "https://example.test"
        )
        self.vendor = self.env["res.partner"].create(
            {"name": "Big Vendor", "supplier_rank": 1}
        )
        self.widget = self.env["product.product"].create(
            {"name": "Widget", "standard_price": 60.0, "purchase_ok": True}
        )
        self.gadget = self.env["product.product"].create(
            {"name": "Gadget", "standard_price": 25.0, "purchase_ok": True}
        )
        # Billed on ordered quantities => a confirmed PO is immediately billable
        # without needing a stock receipt.
        self.billable = self.env["product.product"].create(
            {
                "name": "Billable",
                "standard_price": 30.0,
                "purchase_ok": True,
                "purchase_method": "purchase",
            }
        )

    def _propose(self, func, args):
        try:
            func(self.env, args)
            self.fail("expected ConfirmationRequired")
        except exceptions.ConfirmationRequired as cr:
            return cr.token

    def _new_rfq(self, lines=None):
        args = {
            "partner_id": self.vendor.id,
            "order_lines": lines or [{"product_id": self.widget.id, "quantity": 2}],
        }
        token = self._propose(create_rfq, args)
        result = create_rfq(self.env, dict(args, confirmation_token=token))
        return self.env["purchase.order"].browse(result["order"]["id"]), result

    # -- products ------------------------------------------------------------
    def test_list_products(self):
        result = list_products(self.env, {"query": "Widget"})
        names = {r["name"] for r in result["records"]}
        self.assertIn("Widget", names)

    # -- create rfq ----------------------------------------------------------
    def test_create_rfq_and_pdf(self):
        order, result = self._new_rfq()
        self.assertTrue(result["created"])
        self.assertEqual(order.state, "draft")
        self.assertEqual(len(order.order_line), 1)
        self.assertEqual(order.order_line.product_qty, 2)
        self.assertTrue(
            result["order"]["pdf_url"].startswith("https://example.test/mcp/report/")
        )

    def test_create_rfq_explicit_price(self):
        order, _ = self._new_rfq(
            [{"product_id": self.widget.id, "quantity": 1, "price_unit": 55.0}]
        )
        self.assertEqual(order.order_line.price_unit, 55.0)

    def test_create_requires_a_line(self):
        with self.assertRaises(exceptions.ToolExecutionError):
            create_rfq(self.env, {"partner_id": self.vendor.id, "order_lines": []})

    def test_create_bad_partner(self):
        with self.assertRaises(exceptions.ToolExecutionError):
            create_rfq(
                self.env,
                {"partner_id": 999999, "order_lines": [{"product_id": self.widget.id}]},
            )

    # -- add line / update ---------------------------------------------------
    def test_add_order_line(self):
        order, _ = self._new_rfq()
        args = {"order_id": order.id, "product_id": self.gadget.id, "quantity": 3}
        token = self._propose(add_order_line, args)
        add_order_line(self.env, dict(args, confirmation_token=token))
        self.assertEqual(len(order.order_line), 2)
        self.assertIn(self.gadget, order.order_line.product_id)

    def test_update_order(self):
        order, _ = self._new_rfq()
        args = {"order_id": order.id, "partner_ref": "VEND-123"}
        token = self._propose(update_order, args)
        update_order(self.env, dict(args, confirmation_token=token))
        order.invalidate_recordset()
        self.assertEqual(order.partner_ref, "VEND-123")

    def test_update_requires_field(self):
        order, _ = self._new_rfq()
        with self.assertRaises(exceptions.ToolExecutionError):
            update_order(self.env, {"order_id": order.id})

    # -- lifecycle -----------------------------------------------------------
    def test_send_rfq(self):
        order, _ = self._new_rfq()
        args = {"order_id": order.id}
        token = self._propose(send_rfq, args)
        send_rfq(self.env, dict(args, confirmation_token=token))
        self.assertEqual(order.state, "sent")

    def test_confirm_order(self):
        order, _ = self._new_rfq()
        args = {"order_id": order.id}
        token = self._propose(confirm_order, args)
        result = confirm_order(self.env, dict(args, confirmation_token=token))
        self.assertTrue(result["confirmed"])
        self.assertEqual(order.state, "purchase")

    def test_cannot_edit_confirmed_order(self):
        order, _ = self._new_rfq()
        token = self._propose(confirm_order, {"order_id": order.id})
        confirm_order(self.env, {"order_id": order.id, "confirmation_token": token})
        with self.assertRaises(exceptions.ToolExecutionError):
            add_order_line(
                self.env, {"order_id": order.id, "product_id": self.gadget.id}
            )

    def test_cancel_order(self):
        order, _ = self._new_rfq()
        args = {"order_id": order.id}
        token = self._propose(cancel_order, args)
        cancel_order(self.env, dict(args, confirmation_token=token))
        self.assertEqual(order.state, "cancel")

    # -- vendor bill from PO -------------------------------------------------
    def _confirm(self, order):
        token = self._propose(confirm_order, {"order_id": order.id})
        confirm_order(self.env, {"order_id": order.id, "confirmation_token": token})

    def test_create_vendor_bill_from_po(self):
        order, _ = self._new_rfq([{"product_id": self.billable.id, "quantity": 4}])
        self._confirm(order)
        self.assertEqual(order.invoice_status, "to invoice")
        args = {"order_id": order.id, "ref": "BILL-9", "invoice_date": "2024-01-15"}
        token = self._propose(create_vendor_bill, args)
        result = create_vendor_bill(self.env, dict(args, confirmation_token=token))
        self.assertTrue(result["created"])
        self.assertEqual(result["bill"]["move_type"], "in_invoice")
        self.assertEqual(result["bill"]["state"], "draft")
        self.assertEqual(result["bill"]["ref"], "BILL-9")
        self.assertEqual(str(result["bill"]["invoice_date"]), "2024-01-15")
        bill = self.env["account.move"].browse(result["bill"]["id"])
        self.assertIn(bill, order.invoice_ids)

    def test_create_vendor_bill_requires_confirmed_po(self):
        order, _ = self._new_rfq([{"product_id": self.billable.id, "quantity": 1}])
        # Still a draft RFQ -> cannot bill.
        with self.assertRaises(exceptions.ToolExecutionError):
            create_vendor_bill(self.env, {"order_id": order.id})

    def test_create_vendor_bill_nothing_to_invoice(self):
        # Default control policy 'receive': confirmed but nothing received yet.
        order, _ = self._new_rfq([{"product_id": self.widget.id, "quantity": 2}])
        self._confirm(order)
        self.assertNotEqual(order.invoice_status, "to invoice")
        with self.assertRaises(exceptions.ToolExecutionError):
            create_vendor_bill(self.env, {"order_id": order.id})

    # -- search & read -------------------------------------------------------
    def test_search_orders(self):
        order, _ = self._new_rfq()
        result = search_orders(self.env, {"partner_id": self.vendor.id})
        self.assertTrue(any(r["id"] == order.id for r in result["records"]))

    def test_search_by_state(self):
        order, _ = self._new_rfq()
        result = search_orders(self.env, {"state": "draft"})
        self.assertTrue(all(r["state"] == "draft" for r in result["records"]))

    def test_get_order_includes_lines_and_pdf(self):
        order, _ = self._new_rfq()
        result = get_order(self.env, {"id": order.id})
        self.assertEqual(result["order"]["id"], order.id)
        self.assertEqual(len(result["order"]["order_lines"]), 1)
        self.assertTrue(result["order"]["pdf_url"])

    # -- pdf link ------------------------------------------------------------
    def test_get_order_pdf_link(self):
        order, _ = self._new_rfq()
        result = get_order_pdf(self.env, {"id": order.id})
        self.assertTrue(result["pdf_url"].startswith("https://example.test/mcp/report/"))
        self.assertIn(".pdf", result["filename"])

    def test_get_order_pdf_requires_base_url(self):
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.public_base_url", "")
        self.env["ir.config_parameter"].sudo().set_param("web.base.url", "")
        order, _ = self._new_rfq()
        with self.assertRaises(exceptions.ToolExecutionError):
            get_order_pdf(self.env, {"id": order.id})
