# -*- coding: utf-8 -*-
"""In-memory tool/resource registry.

Per PLAN.md §3.1:

* Registration happens at *import time* through the ``tool`` /
  ``resource_template`` decorators (deterministic via manifest ``depends``).
* The registry is per-worker and derived deterministically from installed
  modules — it holds NO request/session/user state. Never store per-request
  data here.
* ``tools/list`` is computed *per authenticated user*: a tool is only listed if
  the user plausibly has the required Odoo groups. Real ACL/ir.rule enforcement
  still happens at call time inside the tool.

A tool callable has signature ``callable(env, arguments) -> result`` where:
* ``env`` is an ``odoo.api.Environment`` already scoped to the authenticated
  user (never sudo — see the sudo boundary note in PLAN.md §3.1).
* ``arguments`` is the validated ``dict`` from ``tools/call``.
* ``result`` is either a JSON-serialisable value (wrapped into MCP ``content``),
  or a fully-formed MCP tool result dict containing a ``content`` key.
"""
import logging
import re

from . import constants

_logger = logging.getLogger(__name__)

# Claude (and the MCP tool schema) require tool names to match
# ^[a-zA-Z0-9_-]{1,64}$ — dots are NOT allowed. We keep readable dotted names
# internally (registry keys, audit log) and expose a sanitised "wire" name to
# clients, mapping back on tools/call via an explicit reverse index.
_WIRE_INVALID_RE = re.compile(r"[^a-zA-Z0-9_-]")


def sanitize_tool_name(name):
    wire = _WIRE_INVALID_RE.sub("_", name or "")
    return wire[:64]


class ToolDefinition:
    __slots__ = (
        "name",
        "wire_name",
        "description",
        "input_schema",
        "callable",
        "category",
        "is_write",
        "required_groups",
        "annotations",
        "module",
    )

    def __init__(
        self,
        name,
        description,
        input_schema,
        func,
        category=constants.CATEGORY_READ,
        is_write=False,
        required_groups=None,
        annotations=None,
        module=None,
    ):
        self.name = name
        self.wire_name = sanitize_tool_name(name)
        self.description = description
        self.input_schema = input_schema or {"type": "object", "properties": {}}
        self.callable = func
        self.category = category
        self.is_write = is_write
        self.required_groups = tuple(required_groups or ())
        self.annotations = annotations or {}
        self.module = module

    def to_mcp(self):
        """Serialise for ``tools/list`` (uses the wire-safe name)."""
        entry = {
            "name": self.wire_name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
        if self.annotations:
            entry["annotations"] = dict(self.annotations)
        return entry


class ResourceTemplateDefinition:
    __slots__ = (
        "uri_template",
        "name",
        "description",
        "mime_type",
        "callable",
        "required_groups",
        "module",
    )

    def __init__(
        self,
        uri_template,
        name,
        func,
        description="",
        mime_type="application/json",
        required_groups=None,
        module=None,
    ):
        self.uri_template = uri_template
        self.name = name
        self.callable = func
        self.description = description
        self.mime_type = mime_type
        self.required_groups = tuple(required_groups or ())
        self.module = module

    def to_mcp(self):
        return {
            "uriTemplate": self.uri_template,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }


class Registry:
    """Process-wide singleton holding all registered tools/resources."""

    def __init__(self):
        self._tools = {}
        self._wire_index = {}
        self._resource_templates = {}

    # -- registration --------------------------------------------------------
    def add_tool(self, definition):
        if definition.name in self._tools:
            _logger.warning(
                "MCP tool %r already registered (overwriting; module=%s)",
                definition.name,
                definition.module,
            )
        existing = self._wire_index.get(definition.wire_name)
        if existing and existing != definition.name:
            _logger.warning(
                "MCP tool wire-name collision: %r and %r both map to %r",
                existing,
                definition.name,
                definition.wire_name,
            )
        self._tools[definition.name] = definition
        self._wire_index[definition.wire_name] = definition.name
        _logger.debug("Registered MCP tool %r (wire %r)", definition.name,
                      definition.wire_name)

    def add_resource_template(self, definition):
        self._resource_templates[definition.uri_template] = definition

    # -- lookup --------------------------------------------------------------
    def get_tool(self, name):
        """Resolve a tool by canonical (dotted) or wire (sanitised) name."""
        if name in self._tools:
            return self._tools[name]
        canonical = self._wire_index.get(name)
        if canonical:
            return self._tools.get(canonical)
        return None

    def all_tools(self):
        return list(self._tools.values())

    def all_resource_templates(self):
        return list(self._resource_templates.values())

    def match_resource(self, uri):
        """Return ``(definition, params)`` for the first template matching uri.

        Templates use RFC 6570-ish simple ``{name}`` placeholders which we
        translate to a segment-matching pattern.
        """
        import re

        for tmpl in self._resource_templates.values():
            pattern = re.escape(tmpl.uri_template)
            pattern = pattern.replace(r"\{", "{").replace(r"\}", "}")
            pattern = re.sub(r"\{([a-zA-Z0-9_]+)\}", r"(?P<\1>[^/]+)", pattern)
            match = re.fullmatch(pattern, uri)
            if match:
                return tmpl, match.groupdict()
        return None, None

    # -- per-user visibility -------------------------------------------------
    @staticmethod
    def _user_has_groups(env, group_xmlids):
        if not group_xmlids:
            return True
        for xmlid in group_xmlids:
            try:
                if not env.user.has_group(xmlid):
                    return False
            except (ValueError, KeyError):
                # Group xmlid does not resolve (e.g. its module isn't
                # installed) -> treat the tool as not visible, never crash.
                return False
        return True

    def visible_tools(self, env):
        """Tools the given user may plausibly use (group pre-filter)."""
        return [
            t
            for t in self._tools.values()
            if self._user_has_groups(env, t.required_groups)
        ]

    def visible_resource_templates(self, env):
        return [
            r
            for r in self._resource_templates.values()
            if self._user_has_groups(env, r.required_groups)
        ]


# The one process-wide registry instance.
registry = Registry()


# --- decorators used by domain add-ons ---------------------------------------
def tool(
    name,
    description="",
    input_schema=None,
    category=constants.CATEGORY_READ,
    is_write=False,
    required_groups=None,
    annotations=None,
):
    """Decorator registering a function as an MCP tool.

    Example::

        @tool(
            name="contacts.get_partner",
            description="Fetch a partner by id",
            input_schema={"type": "object",
                          "properties": {"id": {"type": "integer"}},
                          "required": ["id"]},
            annotations={"readOnlyHint": True},
        )
        def get_partner(env, arguments):
            partner = env["res.partner"].browse(arguments["id"])
            return partner.read(["name", "email"])
    """

    def decorator(func):
        module = getattr(func, "__module__", None)
        definition = ToolDefinition(
            name=name,
            description=description or (func.__doc__ or "").strip(),
            input_schema=input_schema,
            func=func,
            category=category,
            is_write=is_write,
            required_groups=required_groups,
            annotations=annotations,
            module=module,
        )
        registry.add_tool(definition)
        return func

    return decorator


def resource_template(
    uri_template,
    name,
    description="",
    mime_type="application/json",
    required_groups=None,
):
    """Decorator registering a resource template provider.

    The callable signature is ``callable(env, params) -> list[content]`` where
    ``params`` are the parsed URI-template placeholders.
    """

    def decorator(func):
        definition = ResourceTemplateDefinition(
            uri_template=uri_template,
            name=name,
            func=func,
            description=description or (func.__doc__ or "").strip(),
            mime_type=mime_type,
            required_groups=required_groups,
            module=getattr(func, "__module__", None),
        )
        registry.add_resource_template(definition)
        return func

    return decorator
