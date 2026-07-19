# -*- coding: utf-8 -*-
"""Exceptions used across the MCP engine.

Two distinct error channels (PLAN.md §3.1):

* ``JsonRpcError`` -> serialised as a JSON-RPC ``error`` object. Used for
  protocol-level failures (bad method, invalid params, auth failure).
* ``ToolExecutionError`` -> serialised as a *successful* JSON-RPC result with
  ``isError: true``. Used for business/permission/validation failures *inside*
  a tool. These must NOT become JSON-RPC errors, otherwise the client cannot
  reason about a failed tool call.
"""


# --- Standard JSON-RPC 2.0 error codes ---------------------------------------
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# --- Implementation-defined codes (reserved range -32000..-32099) ------------
UNAUTHORIZED = -32001
FORBIDDEN = -32002
SESSION_REQUIRED = -32003
NOT_INITIALIZED = -32004
UNSUPPORTED_PROTOCOL_VERSION = -32005
RESOURCE_NOT_FOUND = -32006


class JsonRpcError(Exception):
    """A protocol-level error -> JSON-RPC ``error`` object."""

    def __init__(self, code, message, data=None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)

    def to_dict(self):
        err = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return err


class ParseError(JsonRpcError):
    def __init__(self, message="Parse error", data=None):
        super().__init__(PARSE_ERROR, message, data)


class InvalidRequest(JsonRpcError):
    def __init__(self, message="Invalid Request", data=None):
        super().__init__(INVALID_REQUEST, message, data)


class MethodNotFound(JsonRpcError):
    def __init__(self, message="Method not found", data=None):
        super().__init__(METHOD_NOT_FOUND, message, data)


class InvalidParams(JsonRpcError):
    def __init__(self, message="Invalid params", data=None):
        super().__init__(INVALID_PARAMS, message, data)


class InternalError(JsonRpcError):
    def __init__(self, message="Internal error", data=None):
        super().__init__(INTERNAL_ERROR, message, data)


class Unauthorized(JsonRpcError):
    def __init__(self, message="Unauthorized", data=None):
        super().__init__(UNAUTHORIZED, message, data)


class ToolExecutionError(Exception):
    """A business/permission/validation error *inside* a tool.

    Serialised as a successful JSON-RPC result with ``isError: true`` and the
    message placed in ``content``.
    """

    def __init__(self, message, details=None):
        self.message = message
        self.details = details
        super().__init__(message)


class ConfirmationRequired(Exception):
    """Raised (or returned) when a write tool needs a confirmation token.

    Carried through the tool result rather than as an error.
    """

    def __init__(self, preview, token, expires_in):
        self.preview = preview
        self.token = token
        self.expires_in = expires_in
        super().__init__("Confirmation required")
