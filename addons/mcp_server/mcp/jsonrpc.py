# -*- coding: utf-8 -*-
"""Minimal, dependency-free JSON-RPC 2.0 helpers.

We deliberately do NOT implement JSON-RPC batching: the target protocol version
(2025-06-18) removed it. A batch (top-level JSON array) is rejected as an invalid
request per PLAN.md §3.1.
"""
import json

from . import exceptions


def parse_message(raw_body):
    """Parse a raw request body into a single JSON-RPC message dict.

    Raises ``ParseError`` for malformed JSON and ``InvalidRequest`` for a batch
    (array) or a non-object payload.
    """
    if isinstance(raw_body, (bytes, bytearray)):
        try:
            raw_body = raw_body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise exceptions.ParseError("Body is not valid UTF-8") from exc
    try:
        message = json.loads(raw_body)
    except (ValueError, TypeError) as exc:
        raise exceptions.ParseError(str(exc)) from exc

    if isinstance(message, list):
        # Batching removed in 2025-06-18 — reject explicitly.
        raise exceptions.InvalidRequest(
            "JSON-RPC batching is not supported by this protocol version"
        )
    if not isinstance(message, dict):
        raise exceptions.InvalidRequest("Message must be a JSON object")
    return message


def validate_request(message):
    """Validate the envelope of a decoded JSON-RPC message.

    Returns a tuple ``(method, params, msg_id, is_notification)``.
    Raises ``InvalidRequest`` / ``InvalidParams`` on malformed envelopes.
    """
    if message.get("jsonrpc") != "2.0":
        raise exceptions.InvalidRequest("Missing or invalid 'jsonrpc' version")

    method = message.get("method")
    if not isinstance(method, str) or not method:
        raise exceptions.InvalidRequest("Missing or invalid 'method'")

    params = message.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, (dict, list)):
        raise exceptions.InvalidParams("'params' must be an object or array")

    # Absence of "id" (per spec) marks a notification. id may be str/int/null.
    is_notification = "id" not in message
    msg_id = message.get("id")
    if not is_notification and not isinstance(msg_id, (str, int)) and msg_id is not None:
        raise exceptions.InvalidRequest("'id' must be a string, number, or null")

    return method, params, msg_id, is_notification


def make_result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def make_error(msg_id, error):
    """``error`` may be a ``JsonRpcError`` or a plain dict."""
    if isinstance(error, exceptions.JsonRpcError):
        error = error.to_dict()
    return {"jsonrpc": "2.0", "id": msg_id, "error": error}
