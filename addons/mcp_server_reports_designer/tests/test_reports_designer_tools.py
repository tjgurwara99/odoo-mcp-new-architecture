# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.mcp_server.mcp.exceptions import ToolExecutionError
from odoo.addons.mcp_server_reports_designer.tools import (
    list_reports,
    get_report,
    generate_report,
    _build_param_data,
    _coerce_param_value,
)


@tagged("post_install", "-at_install")
class TestReportsDesignerTools(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.public_base_url", "https://example.test"
        )
        self.partner_model = self.env["ir.model"]._get("res.partner")
        # A minimal report definition over res.partner with two parameters.
        self.report = self.env["reports.designer"].create(
            {
                "name": "Partner Digest",
                "root_model_id": self.partner_model.id,
                "reports_designer_param_ids": [
                    (0, 0, {"name": "Date From", "code": "date_from",
                            "type_param": "date"}),
                    (0, 0, {"name": "Category", "code": "categ",
                            "type_param": "many2one",
                            "param_ir_model_id":
                                self.env["ir.model"]._get(
                                    "res.partner.category").id}),
                ],
            }
        )

    # -- discovery tools -----------------------------------------------------
    def test_list_reports(self):
        result = list_reports(self.env, {"query": "Partner Digest"})
        names = {r["name"] for r in result["records"]}
        self.assertIn("Partner Digest", names)

    def test_get_report_exposes_parameters(self):
        result = get_report(self.env, {"id": self.report.id})
        codes = {p["code"] for p in result["report"]["parameters"]}
        self.assertEqual(codes, {"date_from", "categ"})
        self.assertEqual(result["report"]["root_model"], "res.partner")

    def test_get_report_unknown(self):
        with self.assertRaises(ToolExecutionError):
            get_report(self.env, {"id": 0})

    # -- parameter mapping ---------------------------------------------------
    def test_coerce_param_value(self):
        m2o = self.report.reports_designer_param_ids.filtered(
            lambda p: p.code == "categ"
        )
        self.assertEqual(_coerce_param_value(m2o, "5"), 5)

    def test_build_param_data_maps_codes_to_wizard_fields(self):
        data = _build_param_data(self.report, {"date_from": "2024-01-01"})
        field = self.report.reports_designer_param_ids.filtered(
            lambda p: p.code == "date_from"
        ).wizard_param_ir_model_field_id
        self.assertIn(field.name, data)
        self.assertEqual(data[field.name], "2024-01-01")

    def test_build_param_data_rejects_unknown_code(self):
        with self.assertRaises(ToolExecutionError):
            _build_param_data(self.report, {"nope": 1})

    def test_generate_report_unknown_report(self):
        with self.assertRaises(ToolExecutionError):
            generate_report(self.env, {"id": 0})
