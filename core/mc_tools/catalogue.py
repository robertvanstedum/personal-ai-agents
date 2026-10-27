"""The static tool catalogue (tools.json) and strict argument checks.

A tool exists only if it is listed here and in the plugin's operator configuration. The model
supplies references and reasons only: no identity, turn, grant, approval or operation id. Every
argument object is checked against its schema again here, whatever the runtime already checked.
"""
from __future__ import annotations

from copy import deepcopy
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


# Fields that hold written text (an issue body, a comment) may span lines: a line break and a tab are allowed there and
# nowhere else (#395: every issue MC proposed in prod was refused for its line breaks). Mirrored in the minimoi-tools plugin.
MULTILINE = frozenset({("minimoi_issue_create", "body"), ("minimoi_issue_update", "body"),
                       ("minimoi_issue_comment", "summary")})


def _text_ok(value: str, multiline: bool = False) -> bool:
    return not any(unicodedata.category(c) in ("Cc", "Cf", "Co", "Cs") and not (multiline and c in "\n\t")
                   for c in value)


def _check(spec: dict, value, multiline: bool = False) -> None:
    kind = spec["type"]
    if kind == "string":
        if type(value) is not str or not _text_ok(value, multiline) or value != value.strip():
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
    if name.startswith("minimoi_board_") or name in ("minimoi_clarify_action", "minimoi_working_write"):
        from jsonschema import Draft202012Validator
        if list(Draft202012Validator(schema).iter_errors(arguments)):
            raise ArgumentsRefused("tool_arguments_invalid")
        def valid_strings(value):
            if isinstance(value, str):
                return not any(0xD800 <= ord(c) <= 0xDFFF for c in value)
            if isinstance(value, dict):
                return all(valid_strings(k) and valid_strings(v) for k, v in value.items())
            if isinstance(value, list):
                return all(valid_strings(v) for v in value)
            return True
        if not valid_strings(arguments):
            raise ArgumentsRefused("tool_argument_invalid")
        return deepcopy(arguments)
    props = schema["properties"]
    if set(arguments) - set(props) or set(schema.get("required", ())) - set(arguments):
        raise ArgumentsRefused("tool_arguments_invalid")
    for key, value in arguments.items():
        _check(props[key], value, (name, key) in MULTILINE)
    return dict(arguments)


def capability(name: str) -> str:
    return TOOLS[name]["capability"]
