# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.mcp_server.mcp import exceptions
from odoo.addons.mcp_server_sales.tools import (
    search_orders,
    get_order,
    list_products,
    get_order_pdf,
    create_quotation,
    add_order_line,
    update_order,
    set_quotation_sent,
    confirm_order,
    cancel_order,
)


# Run after the full registry is loaded: sale.order.warehouse_id is added by the
# auto-installed ``sale_stock`` bridge, which loads after this module. Running
# post_install guarantees those extensions are present.
@tagged("post_install", "-at_install")
class TestSalesTools(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.public_base_url", "https://example.test"
        )
        self.partner = self.env["res.partner"].create({"name": "Big Customer"})
        self.widget = self.env["product.product"].create(
            {"name": "Widget", "list_price": 100.0, "sale_ok": True}
        )
        self.gadget = self.env["product.product"].create(
            {"name": "Gadget", "list_price": 40.0, "sale_ok": True}
        )

    def _propose(self, func, args):
        try:
            func(self.env, args)
            self.fail("expected ConfirmationRequired")
        except exceptions.ConfirmationRequired as cr:
            return cr.token

    def _new_quotation(self, lines=None):
        args = {
            "partner_id": self.partner.id,
            "order_lines": lines or [{"product_id": self.widget.id, "quantity": 2}],
        }
        token = self._propose(create_quotation, args)
        result = create_quotation(self.env, dict(args, confirmation_token=token))
        return self.env["sale.order"].browse(result["order"]["id"]), result

    # -- products ------------------------------------------------------------
    def test_list_products(self):
        result = list_products(self.env, {"query": "Widget"})
        names = {r["name"] for r in result["records"]}
        self.assertIn("Widget", names)

    # -- create quotation ----------------------------------------------------
    def test_create_quotation_computes_price_and_pdf(self):
        order, result = self._new_quotation()
        self.assertTrue(result["created"])
        self.assertEqual(order.state, "draft")
        self.assertEqual(len(order.order_line), 1)
        self.assertEqual(order.order_line.product_uom_qty, 2)
        self.assertEqual(order.order_line.price_unit, 100.0)
        self.assertTrue(
            result["order"]["pdf_url"].startswith("https://example.test/mcp/report/")
        )

    def test_create_quotation_explicit_price(self):
        order, _ = self._new_quotation(
            [{"product_id": self.widget.id, "quantity": 1, "price_unit": 80.0}]
        )
        self.assertEqual(order.order_line.price_unit, 80.0)

    def test_create_requires_a_line(self):
        with self.assertRaises(exceptions.ToolExecutionError):
            create_quotation(self.env, {"partner_id": self.partner.id, "order_lines": []})

    def test_create_bad_partner(self):
        with self.assertRaises(exceptions.ToolExecutionError):
            create_quotation(
                self.env,
                {"partner_id": 999999, "order_lines": [{"product_id": self.widget.id}]},
            )

    # -- add line / update ---------------------------------------------------
    def test_add_order_line(self):
        order, _ = self._new_quotation()
        args = {"order_id": order.id, "product_id": self.gadget.id, "quantity": 3}
        token = self._propose(add_order_line, args)
        add_order_line(self.env, dict(args, confirmation_token=token))
        self.assertEqual(len(order.order_line), 2)
        self.assertIn(self.gadget, order.order_line.product_id)

    def test_update_order(self):
        order, _ = self._new_quotation()
        args = {"order_id": order.id, "client_order_ref": "PO-123"}
        token = self._propose(update_order, args)
        update_order(self.env, dict(args, confirmation_token=token))
        order.invalidate_recordset()
        self.assertEqual(order.client_order_ref, "PO-123")

    def test_update_requires_field(self):
        order, _ = self._new_quotation()
        with self.assertRaises(exceptions.ToolExecutionError):
            update_order(self.env, {"order_id": order.id})

    # -- lifecycle -----------------------------------------------------------
    def test_set_quotation_sent(self):
        order, _ = self._new_quotation()
        args = {"order_id": order.id}
        token = self._propose(set_quotation_sent, args)
        set_quotation_sent(self.env, dict(args, confirmation_token=token))
        self.assertEqual(order.state, "sent")

    def test_confirm_order(self):
        order, _ = self._new_quotation()
        args = {"order_id": order.id}
        token = self._propose(confirm_order, args)
        result = confirm_order(self.env, dict(args, confirmation_token=token))
        self.assertTrue(result["confirmed"])
        self.assertEqual(order.state, "sale")

    def test_cannot_edit_confirmed_order(self):
        order, _ = self._new_quotation()
        token = self._propose(confirm_order, {"order_id": order.id})
        confirm_order(self.env, {"order_id": order.id, "confirmation_token": token})
        # Adding a line to a confirmed order must be refused.
        with self.assertRaises(exceptions.ToolExecutionError):
            add_order_line(
                self.env, {"order_id": order.id, "product_id": self.gadget.id}
            )

    def test_cancel_order(self):
        order, _ = self._new_quotation()
        args = {"order_id": order.id}
        token = self._propose(cancel_order, args)
        cancel_order(self.env, dict(args, confirmation_token=token))
        self.assertEqual(order.state, "cancel")

    # -- search & read -------------------------------------------------------
    def test_search_orders(self):
        order, _ = self._new_quotation()
        result = search_orders(self.env, {"partner_id": self.partner.id})
        self.assertTrue(any(r["id"] == order.id for r in result["records"]))

    def test_search_by_state(self):
        order, _ = self._new_quotation()
        result = search_orders(self.env, {"state": "draft"})
        self.assertTrue(all(r["state"] == "draft" for r in result["records"]))

    def test_get_order_includes_lines_and_pdf(self):
        order, _ = self._new_quotation()
        result = get_order(self.env, {"id": order.id})
        self.assertEqual(result["order"]["id"], order.id)
        self.assertEqual(len(result["order"]["order_lines"]), 1)
        self.assertTrue(result["order"]["pdf_url"])

    # -- pdf link ------------------------------------------------------------
    def test_get_order_pdf_link(self):
        order, _ = self._new_quotation()
        result = get_order_pdf(self.env, {"id": order.id})
        self.assertTrue(result["pdf_url"].startswith("https://example.test/mcp/report/"))
        self.assertIn(".pdf", result["filename"])

    def test_get_order_pdf_requires_base_url(self):
        # Clear both the MCP base URL and web.base.url fallback.
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.public_base_url", "")
        self.env["ir.config_parameter"].sudo().set_param("web.base.url", "")
        order, _ = self._new_quotation()
        with self.assertRaises(exceptions.ToolExecutionError):
            get_order_pdf(self.env, {"id": order.id})
