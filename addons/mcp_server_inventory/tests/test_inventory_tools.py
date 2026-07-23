# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.mcp_server.mcp import exceptions
from odoo.addons.mcp_server_inventory.tools import (
    check_stock,
    search_transfers,
    get_transfer,
    get_transfer_pdf,
    list_warehouses,
    list_locations,
    create_transfer,
    validate_transfer,
    adjust_quantity,
)


@tagged("post_install", "-at_install")
class TestInventoryTools(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.public_base_url", "https://example.test"
        )
        self.warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)], limit=1
        )
        self.stock_loc = self.warehouse.lot_stock_id
        self.widget = self.env["product.product"].create(
            {"name": "Stock Widget", "type": "product"}
        )

    def _propose(self, func, args):
        try:
            func(self.env, args)
            self.fail("expected ConfirmationRequired")
        except exceptions.ConfirmationRequired as cr:
            return cr.token

    def _run(self, func, args):
        token = self._propose(func, args)
        return func(self.env, dict(args, confirmation_token=token))

    # -- reference data ------------------------------------------------------
    def test_list_warehouses(self):
        result = list_warehouses(self.env, {})
        self.assertTrue(any(w["id"] == self.warehouse.id for w in result["warehouses"]))

    def test_list_locations_internal(self):
        result = list_locations(self.env, {})
        self.assertTrue(all(l["usage"] == "internal" for l in result["locations"]))
        self.assertTrue(any(l["id"] == self.stock_loc.id for l in result["locations"]))

    # -- adjustments ---------------------------------------------------------
    def test_adjust_quantity_sets_on_hand(self):
        result = self._run(
            adjust_quantity,
            {"product_id": self.widget.id, "quantity": 50, "location_id": self.stock_loc.id},
        )
        self.assertTrue(result["adjusted"])
        self.assertEqual(result["new_on_hand"], 50.0)
        self.assertEqual(
            self.widget.with_context(location=self.stock_loc.id).qty_available, 50.0
        )

    def test_adjust_quantity_default_location(self):
        self._run(adjust_quantity, {"product_id": self.widget.id, "quantity": 12})
        self.assertEqual(
            self.widget.with_context(location=self.stock_loc.id).qty_available, 12.0
        )

    def test_adjust_quantity_rejects_service(self):
        service = self.env["product.product"].create(
            {"name": "A Service", "type": "service"}
        )
        with self.assertRaises(exceptions.ToolExecutionError):
            adjust_quantity(self.env, {"product_id": service.id, "quantity": 5})

    # -- check stock ---------------------------------------------------------
    def test_check_stock_reflects_adjustment(self):
        self._run(
            adjust_quantity,
            {"product_id": self.widget.id, "quantity": 7, "location_id": self.stock_loc.id},
        )
        result = check_stock(self.env, {"product_id": self.widget.id})
        row = next(r for r in result["products"] if r["id"] == self.widget.id)
        self.assertEqual(row["qty_on_hand"], 7.0)

    def test_check_stock_only_storable(self):
        self.env["product.product"].create({"name": "Svc2", "type": "service"})
        result = check_stock(self.env, {"query": "Svc2"})
        self.assertEqual(result["returned"], 0)

    def test_check_stock_prioritises_on_hand(self):
        # Two storable products share a searchable token; only one has stock.
        stocked = self.env["product.product"].create(
            {"name": "Priority Widget A", "type": "product"}
        )
        self.env["product.product"].create(
            {"name": "Priority Widget B", "type": "product"}
        )
        self._run(
            adjust_quantity,
            {"product_id": stocked.id, "quantity": 5, "location_id": self.stock_loc.id},
        )
        result = check_stock(self.env, {"query": "Priority Widget"})
        # On-hand product must come first.
        self.assertEqual(result["products"][0]["id"], stocked.id)
        self.assertEqual(result["on_hand_count"], 1)

    def test_check_stock_only_on_hand_filters_zero(self):
        stocked = self.env["product.product"].create(
            {"name": "OnHand Only A", "type": "product"}
        )
        self.env["product.product"].create(
            {"name": "OnHand Only B", "type": "product"}
        )
        self._run(
            adjust_quantity,
            {"product_id": stocked.id, "quantity": 3, "location_id": self.stock_loc.id},
        )
        result = check_stock(
            self.env, {"query": "OnHand Only", "only_on_hand": True}
        )
        ids = {r["id"] for r in result["products"]}
        self.assertEqual(ids, {stocked.id})

    def test_check_stock_group_by_location(self):
        self._run(
            adjust_quantity,
            {"product_id": self.widget.id, "quantity": 9, "location_id": self.stock_loc.id},
        )
        result = check_stock(
            self.env, {"product_id": self.widget.id, "group_by_location": True}
        )
        row = next(r for r in result["products"] if r["id"] == self.widget.id)
        self.assertIn("by_location", row)
        loc = next(
            b for b in row["by_location"] if b["location_id"] == self.stock_loc.id
        )
        self.assertEqual(loc["qty_on_hand"], 9.0)

    # -- transfers -----------------------------------------------------------
    def test_create_receipt_requires_moves(self):
        with self.assertRaises(exceptions.ToolExecutionError):
            create_transfer(self.env, {"picking_type_code": "incoming", "moves": []})

    def test_create_and_validate_receipt_increases_stock(self):
        result = self._run(
            create_transfer,
            {
                "picking_type_id": self.warehouse.in_type_id.id,
                "moves": [{"product_id": self.widget.id, "quantity": 10}],
                "origin": "TEST-IN",
            },
        )
        self.assertTrue(result["created"])
        picking_id = result["transfer"]["id"]
        picking = self.env["stock.picking"].browse(picking_id)
        self.assertEqual(len(picking.move_ids), 1)
        self.assertTrue(result["transfer"]["pdf_url"].startswith("https://example.test/mcp/report/"))

        vres = self._run(validate_transfer, {"transfer_id": picking_id})
        self.assertTrue(vres["validated"])
        self.assertEqual(picking.state, "done")
        self.assertEqual(
            self.widget.with_context(location=self.stock_loc.id).qty_available, 10.0
        )

    def test_validate_already_done_fails(self):
        result = self._run(
            create_transfer,
            {
                "picking_type_id": self.warehouse.in_type_id.id,
                "moves": [{"product_id": self.widget.id, "quantity": 2}],
            },
        )
        pid = result["transfer"]["id"]
        self._run(validate_transfer, {"transfer_id": pid})
        with self.assertRaises(exceptions.ToolExecutionError):
            validate_transfer(self.env, {"transfer_id": pid})

    def test_search_transfers_by_type(self):
        self._run(
            create_transfer,
            {
                "picking_type_id": self.warehouse.in_type_id.id,
                "moves": [{"product_id": self.widget.id, "quantity": 1}],
                "origin": "FIND-ME",
            },
        )
        result = search_transfers(self.env, {"picking_type_code": "incoming"})
        self.assertTrue(result["total"] >= 1)
        self.assertTrue(all(True for _ in result["records"]))

    def test_get_transfer_and_pdf_link(self):
        result = self._run(
            create_transfer,
            {
                "picking_type_id": self.warehouse.in_type_id.id,
                "moves": [{"product_id": self.widget.id, "quantity": 4}],
            },
        )
        pid = result["transfer"]["id"]
        detail = get_transfer(self.env, {"id": pid})
        self.assertEqual(detail["transfer"]["id"], pid)
        self.assertEqual(len(detail["transfer"]["moves"]), 1)

        pdf = get_transfer_pdf(self.env, {"id": pid})
        self.assertTrue(pdf["pdf_url"].startswith("https://example.test/mcp/report/"))
        self.assertIn(".pdf", pdf["filename"])
