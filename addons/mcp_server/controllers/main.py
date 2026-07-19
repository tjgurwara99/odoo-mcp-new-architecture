# -*- coding: utf-8 -*-
"""MCP Streamable HTTP transport controller.

Implements ``POST /mcp`` (JSON-RPC request/response, single JSON body) and
``DELETE /mcp`` (session teardown). SSE is intentionally not opened for ordinary
request/response tool calls — see the worker-model caveat in PLAN.md §3.1: a
long-lived SSE connection pins a prefork worker for its lifetime.
"""
import json
import logging

from odoo import http
from odoo.http import request

from ..mcp import constants, exceptions, jsonrpc
from ..mcp.protocol import ExecutionContext, MCPProtocol

_logger = logging.getLogger(__name__)


def _json_response(payload, status=200, headers=None):
    body = json.dumps(payload, default=str)
    hdrs = [("Content-Type", "application/json")]
    if headers:
        hdrs.extend(headers.items())
    return request.make_response(body, headers=hdrs, status=status)


def _config(param, default=None):
    return request.env["ir.config_parameter"].sudo().get_param(param, default)


class MCPController(http.Controller):

    # ------------------------------------------------------------------
    # Auth resolution
    # ------------------------------------------------------------------
    def _expected_resource(self):
        base = _config("mcp_server.public_base_url") or request.httprequest.host_url
        return base.rstrip("/") + "/mcp"

    def _www_authenticate_header(self):
        base = _config("mcp_server.public_base_url") or request.httprequest.host_url
        metadata_url = base.rstrip("/") + "/.well-known/oauth-protected-resource"
        return {
            "WWW-Authenticate": (
                'Bearer realm="mcp", resource_metadata="%s"' % metadata_url
            )
        }

    def _resolve_user(self):
        """Resolve the request to an authenticated ``res.users``.

        Returns ``(user, token_record)`` or raises ``Unauthorized``.
        """
        auth = request.httprequest.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            raw = auth[len("Bearer "):].strip()
            token = request.env["mcp.oauth.token"].sudo()._resolve_access(raw)
            if not token:
                raise exceptions.Unauthorized("Invalid or expired access token")
            require_resource = _config("mcp_server.require_resource", "True") in (
                "True", "1", "true",
            )
            if require_resource and not token.validate_audience(self._expected_resource()):
                # RFC 8707: reject tokens not scoped to this resource.
                raise exceptions.Unauthorized(
                    "Token audience does not match this MCP resource"
                )
            token.touch()
            return token.user_id, token

        # Dev convenience: fall back to an authenticated Odoo web session only
        # when explicitly enabled (default off). Never enabled in production.
        if _config("mcp_server.allow_session_auth", "False") in ("True", "1", "true"):
            if request.session.uid:
                return request.env["res.users"].sudo().browse(request.session.uid), None

        raise exceptions.Unauthorized("Missing bearer token")

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------
    @http.route("/mcp", type="http", auth="none", methods=["POST"], csrf=False, save_session=False)
    def mcp_post(self, **kwargs):
        if _config("mcp_server.enabled", "True") not in ("True", "1", "true"):
            return _json_response(
                jsonrpc.make_error(None, exceptions.JsonRpcError(
                    exceptions.INTERNAL_ERROR, "MCP server is disabled").to_dict()),
                status=503,
            )

        # 1. Authenticate.
        try:
            user, token = self._resolve_user()
        except exceptions.Unauthorized as exc:
            return _json_response(
                jsonrpc.make_error(None, exc.to_dict()),
                status=401,
                headers=self._www_authenticate_header(),
            )

        # 2. Parse & validate the JSON-RPC envelope.
        raw_body = request.httprequest.get_data(cache=False)
        try:
            message = jsonrpc.parse_message(raw_body)
            method, params, msg_id, is_notification = jsonrpc.validate_request(message)
        except exceptions.JsonRpcError as exc:
            return _json_response(jsonrpc.make_error(None, exc), status=200)

        # 3. Build a user-scoped env (NEVER sudo for tool execution).
        env = request.env(user=user.id)

        # 4. Resolve or create the MCP session.
        session_header = request.httprequest.headers.get(constants.SESSION_HEADER)
        Session = env["mcp.session"].sudo()
        session = Session._resolve(session_header)
        response_headers = {}
        if method == "initialize":
            if not session:
                session = Session._open(user, token)
            response_headers[constants.SESSION_HEADER] = session.session_id
        elif session is None and session_header:
            # Client referenced an unknown/expired session.
            return _json_response(
                jsonrpc.make_error(msg_id, exceptions.JsonRpcError(
                    exceptions.SESSION_REQUIRED,
                    "Unknown or expired session; re-initialize").to_dict()),
                status=404,
            )
        if session:
            session.touch()

        # 5. Dispatch.
        ctx = ExecutionContext(
            env=env,
            session=session,
            oauth_client=token.client_id_ref if token else None,
            ip=request.httprequest.remote_addr,
            user_agent=request.httprequest.headers.get("User-Agent"),
        )
        protocol = MCPProtocol(ctx)
        try:
            result = protocol.dispatch(method, params)
        except exceptions.JsonRpcError as exc:
            if is_notification:
                # Notifications get no response body even on error.
                return request.make_response("", status=202)
            return _json_response(jsonrpc.make_error(msg_id, exc), status=200)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("Unhandled MCP dispatch error")
            if is_notification:
                return request.make_response("", status=202)
            return _json_response(
                jsonrpc.make_error(msg_id, exceptions.InternalError(str(exc))),
                status=200,
            )

        # 6. Notifications produce no JSON-RPC response.
        if is_notification:
            return request.make_response("", status=202, headers=list(response_headers.items()))

        return _json_response(
            jsonrpc.make_result(msg_id, result),
            status=200,
            headers=response_headers,
        )

    @http.route("/mcp", type="http", auth="none", methods=["DELETE"], csrf=False, save_session=False)
    def mcp_delete(self, **kwargs):
        """Session teardown (there is no JSON-RPC ``shutdown`` method)."""
        try:
            user, token = self._resolve_user()
        except exceptions.Unauthorized as exc:
            return _json_response(
                jsonrpc.make_error(None, exc.to_dict()),
                status=401,
                headers=self._www_authenticate_header(),
            )
        session_header = request.httprequest.headers.get(constants.SESSION_HEADER)
        session = request.env["mcp.session"].sudo()._resolve(session_header)
        if session and session.user_id.id == user.id:
            session.close()
            return request.make_response("", status=204)
        return request.make_response("", status=404)

    @http.route("/mcp", type="http", auth="none", methods=["GET"], csrf=False, save_session=False)
    def mcp_get(self, **kwargs):
        """SSE stream for server-initiated messages.

        v1 does not push server-initiated messages; we return 405 to signal the
        client should use plain POST request/response (allowed by the spec).
        """
        return request.make_response(
            "SSE stream not offered; use POST /mcp", status=405,
            headers=[("Allow", "POST, DELETE")],
        )
