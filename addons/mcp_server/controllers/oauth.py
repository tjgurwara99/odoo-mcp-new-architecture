# -*- coding: utf-8 -*-
"""OAuth 2.1 Authorization Server + Protected Resource metadata.

Implements the minimal AS that Claude's remote-connector flow needs:
Authorization Code + PKCE (S256 mandatory), Dynamic Client Registration,
refresh-token rotation, and RFC 8707 resource-indicator binding.

This module is *both* the Authorization Server and the Resource Server
(combined single-module, permitted by the MCP spec — see PLAN.md §3.1).
"""
import base64
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


def _base_url():
    param = request.env["ir.config_parameter"].sudo().get_param(
        "mcp_server.public_base_url"
    )
    return (param or request.httprequest.host_url).rstrip("/")


def _json(payload, status=200, headers=None):
    hdrs = [("Content-Type", "application/json"), ("Cache-Control", "no-store")]
    if headers:
        hdrs.extend(headers.items())
    return request.make_response(json.dumps(payload, default=str), headers=hdrs, status=status)


def _oauth_error(error, description=None, status=400):
    payload = {"error": error}
    if description:
        payload["error_description"] = description
    return _json(payload, status=status)


class OAuthController(http.Controller):

    # ------------------------------------------------------------------
    # Discovery metadata
    # ------------------------------------------------------------------
    @http.route(
        "/.well-known/oauth-authorization-server",
        type="http", auth="none", methods=["GET"], csrf=False,
    )
    def as_metadata(self, **kw):
        base = _base_url()
        return _json({
            "issuer": base,
            "authorization_endpoint": base + "/mcp/oauth/authorize",
            "token_endpoint": base + "/mcp/oauth/token",
            "registration_endpoint": base + "/mcp/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": [
                "none", "client_secret_post", "client_secret_basic",
            ],
            "scopes_supported": ["mcp"],
        })

    @http.route(
        "/.well-known/oauth-protected-resource",
        type="http", auth="none", methods=["GET"], csrf=False,
    )
    def pr_metadata(self, **kw):
        base = _base_url()
        return _json({
            "resource": base + "/mcp",
            "authorization_servers": [base],
            "scopes_supported": ["mcp"],
            "bearer_methods_supported": ["header"],
        })

    # ------------------------------------------------------------------
    # Dynamic Client Registration (RFC 7591)
    # ------------------------------------------------------------------
    @http.route("/mcp/oauth/register", type="http", auth="none", methods=["POST"], csrf=False)
    def register(self, **kw):
        icp = request.env["ir.config_parameter"].sudo()
        if icp.get_param("mcp_server.dcr_open", "True") not in ("True", "1", "true"):
            return _oauth_error("access_denied", "Dynamic registration disabled", 403)
        try:
            metadata = json.loads(request.httprequest.get_data(cache=False) or "{}")
        except ValueError:
            return _oauth_error("invalid_client_metadata", "Body must be JSON")
        if not metadata.get("redirect_uris"):
            return _oauth_error("invalid_redirect_uri", "redirect_uris required")
        record, raw_secret = request.env["mcp.oauth.client"].sudo().register_client(metadata)
        return _json(record.to_registration_response(raw_secret), status=201)

    # ------------------------------------------------------------------
    # Authorization endpoint (consent) — reuses Odoo login (auth="user")
    # ------------------------------------------------------------------
    @http.route("/mcp/oauth/authorize", type="http", auth="user", methods=["GET"], csrf=False)
    def authorize(self, **params):
        error = self._validate_authorize_params(params)
        if error:
            return request.render("mcp_server.oauth_error", {"message": error})
        client = request.env["mcp.oauth.client"].sudo().search(
            [("client_id", "=", params.get("client_id")), ("active", "=", True)], limit=1
        )
        # Render consent screen (user already authenticated via Odoo login).
        return request.render("mcp_server.oauth_consent", {
            "client": client,
            "params": params,
            "scope": params.get("scope") or client.scope or "mcp",
            "user": request.env.user,
        })

    @http.route("/mcp/oauth/authorize/decision", type="http", auth="user", methods=["POST"], csrf=True)
    def authorize_decision(self, **params):
        decision = params.get("decision")
        redirect_uri = params.get("redirect_uri")
        state = params.get("state")
        if decision != "allow":
            return self._redirect_error(redirect_uri, "access_denied", state)

        error = self._validate_authorize_params(params)
        if error:
            return self._redirect_error(redirect_uri, "invalid_request", state)

        client = request.env["mcp.oauth.client"].sudo().search(
            [("client_id", "=", params.get("client_id")), ("active", "=", True)], limit=1
        )
        raw_code = request.env["mcp.oauth.auth.code"].sudo()._new_code(
            client=client,
            user=request.env.user,
            redirect_uri=redirect_uri,
            scope=params.get("scope") or client.scope or "mcp",
            resource=params.get("resource") or (_base_url() + "/mcp"),
            code_challenge=params.get("code_challenge"),
            code_challenge_method=params.get("code_challenge_method") or "S256",
        )
        sep = "&" if "?" in redirect_uri else "?"
        location = "%s%scode=%s" % (redirect_uri, sep, raw_code)
        if state:
            location += "&state=%s" % state
        return request.redirect(location, local=False)

    def _validate_authorize_params(self, params):
        if params.get("response_type") != "code":
            return "Only response_type=code is supported."
        client = request.env["mcp.oauth.client"].sudo().search(
            [("client_id", "=", params.get("client_id")), ("active", "=", True)], limit=1
        )
        if not client:
            return "Unknown client_id."
        redirect_uri = params.get("redirect_uri")
        if not redirect_uri or not client.is_redirect_allowed(redirect_uri):
            return "Invalid redirect_uri for this client."
        if not params.get("code_challenge"):
            return "PKCE code_challenge is required."
        if (params.get("code_challenge_method") or "S256") != "S256":
            return "Only S256 PKCE is supported."
        return None

    def _redirect_error(self, redirect_uri, error, state=None):
        if not redirect_uri:
            return request.render("mcp_server.oauth_error", {"message": error})
        sep = "&" if "?" in redirect_uri else "?"
        location = "%s%serror=%s" % (redirect_uri, sep, error)
        if state:
            location += "&state=%s" % state
        return request.redirect(location, local=False)

    # ------------------------------------------------------------------
    # Token endpoint
    # ------------------------------------------------------------------
    @http.route("/mcp/oauth/token", type="http", auth="none", methods=["POST"], csrf=False)
    def token(self, **params):
        grant_type = params.get("grant_type")
        if grant_type == "authorization_code":
            return self._grant_authorization_code(params)
        if grant_type == "refresh_token":
            return self._grant_refresh_token(params)
        return _oauth_error("unsupported_grant_type", grant_type)

    def _authenticate_client(self, params):
        """Return the client record or None. Handles public + confidential."""
        client_id = params.get("client_id")
        client_secret = params.get("client_secret")
        # HTTP Basic auth support (client_secret_basic).
        auth_header = request.httprequest.headers.get("Authorization", "")
        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                basic_id, basic_secret = decoded.split(":", 1)
                client_id = client_id or basic_id
                client_secret = client_secret or basic_secret
            except Exception:  # noqa: BLE001
                return None
        client = request.env["mcp.oauth.client"].sudo().search(
            [("client_id", "=", client_id), ("active", "=", True)], limit=1
        )
        if not client:
            return None
        if not client.verify_secret(client_secret):
            return None
        return client

    def _grant_authorization_code(self, params):
        client = self._authenticate_client(params)
        if not client:
            return _oauth_error("invalid_client", "Client authentication failed", 401)

        code_rec = request.env["mcp.oauth.auth.code"].sudo()._find_valid(params.get("code"))
        if not code_rec:
            return _oauth_error("invalid_grant", "Invalid or expired code")
        if code_rec.client_id_ref.id != client.id:
            return _oauth_error("invalid_grant", "Code was issued to another client")
        if code_rec.redirect_uri != params.get("redirect_uri"):
            return _oauth_error("invalid_grant", "redirect_uri mismatch")

        # PKCE verification (mandatory).
        if not request.env["mcp.oauth.auth.code"].verify_pkce(
            params.get("code_verifier"),
            code_rec.code_challenge,
            code_rec.code_challenge_method,
        ):
            return _oauth_error("invalid_grant", "PKCE verification failed")

        # RFC 8707: if the client sends a resource, it must match the code's.
        requested_resource = params.get("resource")
        if requested_resource and code_rec.resource and \
                requested_resource.rstrip("/") != code_rec.resource.rstrip("/"):
            return _oauth_error("invalid_target", "resource mismatch")

        code_rec.consume()
        token, access_raw, refresh_raw = request.env["mcp.oauth.token"].sudo().issue(
            client=client,
            user=code_rec.user_id,
            scope=code_rec.scope,
            resource=code_rec.resource,
        )
        return _json(token.to_token_response(access_raw, refresh_raw))

    def _grant_refresh_token(self, params):
        client = self._authenticate_client(params)
        if not client:
            return _oauth_error("invalid_client", "Client authentication failed", 401)
        old = request.env["mcp.oauth.token"].sudo()._resolve_refresh(
            params.get("refresh_token")
        )
        if not old or old.client_id_ref.id != client.id:
            return _oauth_error("invalid_grant", "Invalid refresh token")
        # Optional narrowing of resource on refresh (must stay within original).
        new_token, access_raw, refresh_raw = old.rotate_refresh()
        return _json(new_token.to_token_response(access_raw, refresh_raw))

    # ------------------------------------------------------------------
    # Token revocation (RFC 7009)
    # ------------------------------------------------------------------
    @http.route("/mcp/oauth/revoke", type="http", auth="none", methods=["POST"], csrf=False)
    def revoke(self, **params):
        client = self._authenticate_client(params)
        if not client:
            return _oauth_error("invalid_client", status=401)
        raw = params.get("token")
        Token = request.env["mcp.oauth.token"].sudo()
        rec = Token._resolve_access(raw) or Token._resolve_refresh(raw)
        if rec and rec.client_id_ref.id == client.id:
            rec.revoke()
        # RFC 7009: always 200 regardless.
        return _json({}, status=200)
