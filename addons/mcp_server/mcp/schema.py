# -*- coding: utf-8 -*-
"""A small, dependency-free JSON-Schema validator.

We only support the subset used by tool ``inputSchema`` declarations so we do not
pull in ``jsonschema`` as a hard dependency. Returns a human-readable error
string, or ``None`` when the value is valid.
"""

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _check(value, schema, path):
    if not isinstance(schema, dict):
        return None

    expected = schema.get("type")
    if expected:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_TYPE_CHECKS.get(t, lambda v: True)(value) for t in types):
            return "%s: expected type %s" % (path or "value", "/".join(types))

    if "enum" in schema and value not in schema["enum"]:
        return "%s: must be one of %r" % (path or "value", schema["enum"])

    if isinstance(value, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in value:
                return "%s: missing required property '%s'" % (path or "root", req)
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props and key != "confirmation_token":
                    return "%s: unexpected property '%s'" % (path or "root", key)
        for key, subschema in props.items():
            if key in value:
                err = _check(value[key], subschema, _join(path, key))
                if err:
                    return err

    if isinstance(value, list):
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(value):
                err = _check(item, item_schema, "%s[%d]" % (path or "", i))
                if err:
                    return err

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            return "%s: shorter than %d" % (path or "value", schema["minLength"])
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return "%s: longer than %d" % (path or "value", schema["maxLength"])

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return "%s: below minimum %s" % (path or "value", schema["minimum"])
        if "maximum" in schema and value > schema["maximum"]:
            return "%s: above maximum %s" % (path or "value", schema["maximum"])

    return None


def _join(path, key):
    return "%s.%s" % (path, key) if path else key


def validate_arguments(arguments, schema):
    """Validate ``arguments`` against ``schema``; return error string or None.

    A ``confirmation_token`` key is always tolerated so the propose/confirm
    round-trip does not require every tool to declare it in its schema.
    """
    if not schema:
        return None
    return _check(arguments, schema, "")
