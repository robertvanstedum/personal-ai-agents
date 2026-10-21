"""The static tool catalogue (tools.json) and strict argument checks.

A tool exists only if it is listed here and in the plugin's operator configuration. The model
supplies references and reasons only: no identity, turn, grant, approval or operation id. Every
argument object is checked against its schema again here, whatever the runtime already checked.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from core.minimoi_actions.context import ActionContractError

_DOC = json.loads((Path(__file__).with_name("tools.json")).read_text(encoding="utf-8"))
VERSION = _DOC["version"]
TOOLS = {t["name"]: t for t in _DOC["tools"]}
_NAME = re.compile(r"minimoi_[a-z][a-z0-9_]{0,63}")


class ArgumentsRefused(ActionContractError):
    """The arguments do not match the tool's schema. A fixed code, never the value."""


def _text_ok(value: str) -> bool:
    return not any(unicodedata.category(c) in ("Cc", "Cf", "Co", "Cs") for c in value)


def _check(spec: dict, value) -> None:
    kind = spec["type"]
    if kind == "string":
        if type(value) is not str or not _text_ok(value) or value != value.strip():
            raise ArgumentsRefused("tool_argument_invalid")
        if not spec.get("minLength", 0) <= len(value) <= spec.get("maxLength", 1000):
            raise ArgumentsRefused("tool_argument_invalid")
        if "enum" in spec and value not in spec["enum"]:
            raise ArgumentsRefused("tool_argument_invalid")
        if "pattern" in spec and not re.search(spec["pattern"], value):
            raise ArgumentsRefused("tool_argument_invalid")
    elif kind == "array":
        if (type(value) is not list or not spec["minItems"] <= len(value) <= spec["maxItems"]
                or (spec.get("uniqueItems") and len(set(map(repr, value))) != len(value))):
            raise ArgumentsRefused("tool_argument_invalid")
        for item in value:
            _check(spec["items"], item)
    elif kind == "integer":
        if type(value) is not int or not spec["minimum"] <= value <= spec["maximum"]:
            raise ArgumentsRefused("tool_argument_invalid")
    else:                                                   # the catalogue uses no other types
        raise ArgumentsRefused("tool_schema_unsupported")


def validate(name: str, arguments) -> dict:
    """The arguments as a fresh dict, or ArgumentsRefused. Unknown keys are refused, never dropped."""
    if type(name) is not str or not _NAME.fullmatch(name) or name not in TOOLS:
        raise ArgumentsRefused("tool_unknown")
    schema = TOOLS[name]["parameters"]
    if type(arguments) is not dict:
        raise ArgumentsRefused("tool_arguments_invalid")
    props = schema["properties"]
    if set(arguments) - set(props) or set(schema.get("required", ())) - set(arguments):
        raise ArgumentsRefused("tool_arguments_invalid")
    for key, value in arguments.items():
        _check(props[key], value)
    return dict(arguments)


def capability(name: str) -> str:
    return TOOLS[name]["capability"]
