# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase

from odoo.addons.mcp_server.mcp import exceptions


class TestConfirmation(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Confirm = self.env["mcp.action.confirmation"]

    def test_propose_raises_confirmation_required(self):
        with self.assertRaises(exceptions.ConfirmationRequired) as cm:
            self.Confirm.require("odoo.create_record", {"model": "res.partner"}, "preview text")
        self.assertEqual(cm.exception.preview, "preview text")
        self.assertTrue(cm.exception.token)

    def test_confirm_with_token_proceeds(self):
        args = {"model": "res.partner", "values": {"name": "X"}}
        try:
            self.Confirm.require("odoo.create_record", args, "prev")
            self.fail("should have raised")
        except exceptions.ConfirmationRequired as cr:
            token = cr.token
        args2 = dict(args, confirmation_token=token)
        rec = self.Confirm.require("odoo.create_record", args2, "prev")
        self.assertTrue(rec)

    def test_token_single_use(self):
        args = {"model": "res.partner", "values": {"name": "X"}}
        try:
            self.Confirm.require("odoo.create_record", args, "prev")
        except exceptions.ConfirmationRequired as cr:
            token = cr.token
        args2 = dict(args, confirmation_token=token)
        self.Confirm.require("odoo.create_record", args2, "prev")
        with self.assertRaises(exceptions.ToolExecutionError):
            self.Confirm.require("odoo.create_record", args2, "prev")

    def test_token_bound_to_arguments(self):
        args = {"model": "res.partner", "values": {"name": "X"}}
        try:
            self.Confirm.require("odoo.create_record", args, "prev")
        except exceptions.ConfirmationRequired as cr:
            token = cr.token
        tampered = {"model": "res.partner", "values": {"name": "EVIL"},
                    "confirmation_token": token}
        with self.assertRaises(exceptions.ToolExecutionError):
            self.Confirm.require("odoo.create_record", tampered, "prev")

    def test_token_bound_to_tool(self):
        args = {"model": "res.partner"}
        try:
            self.Confirm.require("odoo.delete_record", args, "prev")
        except exceptions.ConfirmationRequired as cr:
            token = cr.token
        with self.assertRaises(exceptions.ToolExecutionError):
            self.Confirm.require(
                "odoo.create_record", dict(args, confirmation_token=token), "prev")

    def test_invalid_token_rejected(self):
        with self.assertRaises(exceptions.ToolExecutionError):
            self.Confirm.require(
                "odoo.create_record",
                {"model": "res.partner", "confirmation_token": "bogus"},
                "prev",
            )
