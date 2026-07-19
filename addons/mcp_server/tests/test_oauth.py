# -*- coding: utf-8 -*-
import base64
import hashlib

from odoo.tests.common import TransactionCase


def _pkce_pair():
    verifier = "test-verifier-abcdefghijklmnopqrstuvwxyz-0123456789"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class TestOAuth(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Client = self.env["mcp.oauth.client"]
        self.Code = self.env["mcp.oauth.auth.code"]
        self.Token = self.env["mcp.oauth.token"]
        self.user = self.env.ref("base.user_admin")

    def _client(self, confidential=False):
        record, secret = self.Client.register_client({
            "client_name": "Test",
            "redirect_uris": ["https://claude.ai/callback"],
            "token_endpoint_auth_method": "client_secret_post" if confidential else "none",
        })
        return record, secret

    # -- DCR -----------------------------------------------------------------
    def test_register_public_client(self):
        client, secret = self._client()
        self.assertTrue(client.client_id)
        self.assertIsNone(secret)
        self.assertFalse(client.is_confidential)

    def test_register_confidential_client_has_secret(self):
        client, secret = self._client(confidential=True)
        self.assertTrue(secret)
        self.assertTrue(client.verify_secret(secret))
        self.assertFalse(client.verify_secret("wrong"))

    def test_redirect_uri_allowlist(self):
        client, _ = self._client()
        self.assertTrue(client.is_redirect_allowed("https://claude.ai/callback"))
        self.assertFalse(client.is_redirect_allowed("https://evil.example/cb"))

    # -- PKCE ----------------------------------------------------------------
    def test_pkce_success(self):
        verifier, challenge = _pkce_pair()
        self.assertTrue(self.Code.verify_pkce(verifier, challenge, "S256"))

    def test_pkce_failure(self):
        _, challenge = _pkce_pair()
        self.assertFalse(self.Code.verify_pkce("wrong-verifier", challenge, "S256"))

    # -- code lifecycle ------------------------------------------------------
    def test_auth_code_single_use(self):
        client, _ = self._client()
        _, challenge = _pkce_pair()
        raw = self.Code._new_code(
            client, self.user, "https://claude.ai/callback", "mcp",
            "https://erp.example.com/mcp", challenge, "S256")
        rec = self.Code._find_valid(raw)
        self.assertTrue(rec)
        rec.consume()
        self.assertIsNone(self.Code._find_valid(raw))

    # -- token lifecycle -----------------------------------------------------
    def test_issue_and_resolve_token(self):
        client, _ = self._client()
        token, access_raw, refresh_raw = self.Token.issue(
            client, self.user, "mcp", "https://erp.example.com/mcp")
        self.assertTrue(access_raw)
        self.assertTrue(refresh_raw)
        resolved = self.Token._resolve_access(access_raw)
        self.assertEqual(resolved.id, token.id)

    def test_resource_audience_validation(self):
        client, _ = self._client()
        token, access_raw, _ = self.Token.issue(
            client, self.user, "mcp", "https://erp.example.com/mcp")
        self.assertTrue(token.validate_audience("https://erp.example.com/mcp"))
        self.assertTrue(token.validate_audience("https://erp.example.com/mcp/"))
        self.assertFalse(token.validate_audience("https://other.example.com/mcp"))

    def test_refresh_rotation_revokes_old(self):
        client, _ = self._client()
        token, _, refresh_raw = self.Token.issue(
            client, self.user, "mcp", "https://erp.example.com/mcp")
        new_token, new_access, new_refresh = token.rotate_refresh()
        self.assertTrue(token.revoked)
        self.assertNotEqual(new_token.id, token.id)
        self.assertTrue(self.Token._resolve_access(new_access))

    def test_revoked_token_not_resolvable(self):
        client, _ = self._client()
        token, access_raw, _ = self.Token.issue(
            client, self.user, "mcp", "https://erp.example.com/mcp")
        token.revoke()
        self.assertIsNone(self.Token._resolve_access(access_raw))
