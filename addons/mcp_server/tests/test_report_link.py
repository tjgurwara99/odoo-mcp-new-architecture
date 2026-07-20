# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase

from odoo.addons.mcp_server.mcp.exceptions import ToolExecutionError
from odoo.addons.mcp_server.models import mcp_token_utils as tok


class TestReportLink(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Link = self.env["mcp.report.link"]
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.public_base_url", "https://example.test"
        )
        self.partner = self.env["res.partner"].create({"name": "Doc Target"})

    def test_mint_returns_absolute_url(self):
        url = self.Link.mint("dummy.report", self.partner, filename="x.pdf")
        self.assertTrue(url.startswith("https://example.test/mcp/report/"))

    def test_mint_and_resolve_roundtrip(self):
        url = self.Link.mint("dummy.report", self.partner)
        raw = url.rsplit("/", 1)[1]
        link = self.Link._resolve(raw)
        self.assertTrue(link)
        self.assertEqual(link.user_id, self.env.user)
        self.assertEqual(link.model_name, "res.partner")
        self.assertEqual(link.res_ids(), [self.partner.id])

    def test_resolve_unknown_token(self):
        self.assertFalse(self.Link._resolve("nope"))

    def test_resolve_expired(self):
        url = self.Link.mint("dummy.report", self.partner, ttl=1)
        raw = url.rsplit("/", 1)[1]
        rec = self.Link.sudo().search(
            [("token_hash", "=", tok.hash_secret(raw))], limit=1
        )
        rec.write({"expires_at": "2000-01-01 00:00:00"})
        self.assertFalse(self.Link._resolve(raw))

    def test_mint_requires_base_url(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_server.public_base_url", ""
        )
        # web.base.url may still be set by base; force both empty to assert guard.
        self.env["ir.config_parameter"].sudo().set_param("web.base.url", "")
        with self.assertRaises(ToolExecutionError):
            self.Link.mint("dummy.report", self.partner)

    def test_mint_requires_records(self):
        with self.assertRaises(ToolExecutionError):
            self.Link.mint("dummy.report", self.env["res.partner"])

    def test_gc_removes_expired(self):
        url = self.Link.mint("dummy.report", self.partner)
        raw = url.rsplit("/", 1)[1]
        rec = self.Link.sudo().search(
            [("token_hash", "=", tok.hash_secret(raw))], limit=1
        )
        rec.write({"expires_at": "2000-01-01 00:00:00"})
        self.Link._gc()
        self.assertFalse(rec.exists())
