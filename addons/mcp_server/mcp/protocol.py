# -*- coding: utf-8 -*-
"""MCP lifecycle + method dispatch.

Given an already-authenticated, user-scoped ``env`` and a request context, this
module executes a single JSON-RPC request and returns a JSON-serialisable result
(or raises a ``JsonRpcError`` for protocol-level failures).

Tool execution errors are converted here into successful results carrying
``isError: true`` — never into JSON-RPC errors (PLAN.md §3.1).
"""
import json
import logging
import time

from . import constants, exceptions
from .registry import registry
from .schema import validate_arguments

_logger = logging.getLogger(__name__)


class ExecutionContext:
    """Everything the protocol needs about the current request."""

    def __init__(
        self,
        env,
        session=None,
        oauth_client=None,
        ip=None,
        user_agent=None,
    ):
        self.env = env
        self.session = session
        self.oauth_client = oauth_client
        self.ip = ip
        self.user_agent = user_agent


def _server_capabilities():
    return {
        "tools": {"listChanged": False},
        "resources": {"listChanged": False, "subscribe": False},
        "logging": {},
    }


def _to_tool_result(value):
    """Normalise a tool return value into an MCP tool result dict."""
    if isinstance(value, dict) and "content" in value:
        # Tool returned an already-formed MCP result (may set isError itself).
        return value
    text = json.dumps(value, default=str, ensure_ascii=False, indent=2)
    result = {"content": [{"type": "text", "text": text}], "isError": False}
    if isinstance(value, dict):
        result["structuredContent"] = value
    return result


def _error_tool_result(message, details=None):
    payload = {"error": message}
    if details is not None:
        payload["details"] = details
    return {
        "content": [{"type": "text", "text": message}],
        "structuredContent": payload,
        "isError": True,
    }


class MCPProtocol:
    def __init__(self, context):
        self.ctx = context
        self.env = context.env

    # -- top-level dispatch --------------------------------------------------
    def dispatch(self, method, params):
        handler = self._METHODS.get(method)
        if handler is None:
            raise exceptions.MethodNotFound("Unknown method: %s" % method)
        return handler(self, params)

    # -- lifecycle -----------------------------------------------------------
    def _initialize(self, params):
        if not isinstance(params, dict):
            raise exceptions.InvalidParams("initialize params must be an object")
        requested = params.get("protocolVersion")
        # Negotiate: we only implement one version. If the client asked for a
        # version we don't support, we still reply with ours (spec allows the
        # client to then decide to disconnect).
        negotiated = constants.PROTOCOL_VERSION
        if requested and requested not in constants.SUPPORTED_PROTOCOL_VERSIONS:
            _logger.info(
                "MCP client requested unsupported protocolVersion %r; "
                "responding with %r",
                requested,
                negotiated,
            )
        client_info = params.get("clientInfo") or {}
        if self.ctx.session:
            self.ctx.session.sudo().write(
                {
                    "protocol_version": negotiated,
                    "client_name": client_info.get("name"),
                    "client_version": client_info.get("version"),
                    "state": "initializing",
                }
            )
        return {
            "protocolVersion": negotiated,
            "capabilities": _server_capabilities(),
            "serverInfo": {
                "name": constants.SERVER_NAME,
                "version": constants.SERVER_VERSION,
            },
        }

    def _initialized(self, params):
        # Notification: no response body. Mark the session ready.
        if self.ctx.session:
            self.ctx.session.sudo().write({"state": "ready"})
        return None

    def _ping(self, params):
        return {}

    # -- tools ---------------------------------------------------------------
    def _tools_list(self, params):
        tools = [t.to_mcp() for t in registry.visible_tools(self.env)]
        tools.sort(key=lambda t: t["name"])
        return {"tools": tools}

    def _tools_call(self, params):
        if not isinstance(params, dict):
            raise exceptions.InvalidParams("tools/call params must be an object")
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise exceptions.InvalidParams("Missing tool 'name'")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise exceptions.InvalidParams("'arguments' must be an object")

        definition = registry.get_tool(name)
        if definition is None:
            # Unknown tool is a protocol error, not a tool error.
            raise exceptions.MethodNotFound("Unknown tool: %s" % name)

        # Group pre-check (defence in depth; real ACL enforced inside the tool).
        if definition.required_groups and not registry._user_has_groups(
            self.env, definition.required_groups
        ):
            return self._audited_result(
                definition,
                arguments,
                _error_tool_result(
                    "You do not have the required permissions for this tool."
                ),
                is_error=True,
                error="permission_denied",
            )

        # Validate arguments against the declared JSON schema.
        schema_error = validate_arguments(arguments, definition.input_schema)
        if schema_error:
            return self._audited_result(
                definition,
                arguments,
                _error_tool_result("Invalid arguments: %s" % schema_error),
                is_error=True,
                error=schema_error,
            )

        return self._run_tool(definition, arguments)

    def _run_tool(self, definition, arguments):
        start = time.time()
        try:
            value = definition.callable(self.env, arguments)
            result = _to_tool_result(value)
            is_error = bool(result.get("isError"))
            return self._audited_result(
                definition,
                arguments,
                result,
                is_error=is_error,
                duration_ms=int((time.time() - start) * 1000),
            )
        except exceptions.ConfirmationRequired as cr:
            result = {
                "content": [{"type": "text", "text": cr.preview}],
                "structuredContent": {
                    "status": "confirmation_required",
                    "preview": cr.preview,
                    "confirmation_token": cr.token,
                    "expires_in": cr.expires_in,
                    "instructions": (
                        "Re-call this tool with the same arguments plus "
                        "'confirmation_token' to execute the change."
                    ),
                },
                "isError": False,
            }
            return self._audited_result(
                definition,
                arguments,
                result,
                is_error=False,
                category=constants.CATEGORY_WRITE,
                confirmation_token=cr.token,
                duration_ms=int((time.time() - start) * 1000),
                result_note="confirmation_required",
            )
        except exceptions.ToolExecutionError as exc:
            return self._audited_result(
                definition,
                arguments,
                _error_tool_result(exc.message, exc.details),
                is_error=True,
                error=exc.message,
                duration_ms=int((time.time() - start) * 1000),
            )
        except exceptions.JsonRpcError:
            # Let genuine protocol errors propagate.
            raise
        except Exception as exc:  # noqa: BLE001 - convert to tool error
            _logger.exception("Unhandled error in tool %s", definition.name)
            # Odoo access errors etc. become tool errors so the client can react.
            return self._audited_result(
                definition,
                arguments,
                _error_tool_result("Tool execution failed: %s" % exc),
                is_error=True,
                error=str(exc),
                duration_ms=int((time.time() - start) * 1000),
            )

    # -- resources -----------------------------------------------------------
    def _resources_list(self, params):
        # v1 exposes resources via templates; static list is intentionally empty
        # (see PLAN.md §3.1.7). Domain modules may add enumerable resources here
        # in future by extending the registry.
        return {"resources": []}

    def _resources_templates_list(self, params):
        templates = [
            t.to_mcp() for t in registry.visible_resource_templates(self.env)
        ]
        templates.sort(key=lambda t: t["uriTemplate"])
        return {"resourceTemplates": templates}

    def _resources_read(self, params):
        if not isinstance(params, dict):
            raise exceptions.InvalidParams("resources/read params must be object")
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri:
            raise exceptions.InvalidParams("Missing resource 'uri'")
        definition, uri_params = registry.match_resource(uri)
        if definition is None:
            raise exceptions.JsonRpcError(
                exceptions.RESOURCE_NOT_FOUND, "Resource not found: %s" % uri
            )
        if definition.required_groups and not registry._user_has_groups(
            self.env, definition.required_groups
        ):
            raise exceptions.JsonRpcError(
                exceptions.FORBIDDEN, "Not permitted to read this resource"
            )
        try:
            contents = definition.callable(self.env, uri_params)
        except exceptions.ToolExecutionError as exc:
            raise exceptions.JsonRpcError(
                exceptions.RESOURCE_NOT_FOUND, exc.message
            )
        if isinstance(contents, dict):
            contents = [contents]
        # Ensure each content carries the uri.
        for c in contents:
            c.setdefault("uri", uri)
            c.setdefault("mimeType", definition.mime_type)
        return {"contents": contents}

    # -- audit plumbing ------------------------------------------------------
    def _audited_result(
        self,
        definition,
        arguments,
        result,
        is_error=False,
        error=None,
        category=None,
        confirmation_token=None,
        duration_ms=0,
        result_note=None,
    ):
        try:
            self.env["mcp.audit.log"].sudo().log_call(
                user=self.env.user,
                tool_name=definition.name,
                category=category or definition.category,
                arguments=arguments,
                result=result,
                is_error=is_error,
                error=error,
                duration_ms=duration_ms,
                oauth_client=self.ctx.oauth_client,
                session=self.ctx.session,
                ip=self.ctx.ip,
                user_agent=self.ctx.user_agent,
                confirmation_token=confirmation_token,
                result_note=result_note,
            )
        except Exception:  # noqa: BLE001 - never let audit failure break a call
            _logger.exception("Failed to write MCP audit log for %s", definition.name)
        return result

    _METHODS = {
        "initialize": _initialize,
        "notifications/initialized": _initialized,
        "ping": _ping,
        "tools/list": _tools_list,
        "tools/call": _tools_call,
        "resources/list": _resources_list,
        "resources/templates/list": _resources_templates_list,
        "resources/read": _resources_read,
    }
