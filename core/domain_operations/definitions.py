"""Code-owned operation descriptions, never executable authority or dynamic imports.

This module deliberately coexists with the older local operations inventory.
It does not provision routes, keys, tools, policies or handlers on import.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Operation:
    id: str
    capability: str
    version: int
    method: str
    path: str
    description: str
    arguments: dict
    result: dict
    handler: str
    dependencies: tuple[str, ...]
    effect: str = "read"
    tool: str | None = None
    model_arguments: dict | None = None
    undo: str | None = None

    def __post_init__(self):
        for value in (self.id, self.capability):
            if not re.fullmatch(r"[a-z][a-z0-9_.]{0,95}", value):
                raise ValueError("operation_identifier_invalid")
        if self.version != 1 or self.method not in {"GET", "POST", "PATCH"}:
            raise ValueError("operation_route_invalid")
        if not re.fullmatch(r"/internal/domain-api/v1/[a-z0-9_/{}/-]+", self.path):
            raise ValueError("operation_route_invalid")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.handler):
            raise ValueError("operation_handler_invalid")
        if self.effect not in {"read", "private_write"}:
            raise ValueError("operation_effect_invalid")
        if self.tool and not re.fullmatch(r"minimoi_[a-z][a-z0-9_]{0,63}", self.tool):
            raise ValueError("operation_tool_invalid")
        if bool(self.tool) != (self.model_arguments is not None):
            raise ValueError("operation_tool_schema_missing")


def checked(operations):
    """Reject collisions before producing any projection, including aliases."""
    rows = tuple(operations)
    for values in ([o.id for o in rows], [(o.method, o.path) for o in rows],
                   [o.tool for o in rows if o.tool], [o.handler for o in rows]):
        if len(values) != len(set(values)):
            raise ValueError("operation_collision")
    return rows


def tool_entries(operations):
    return [{"name": o.tool, "capability": o.capability,
             "description": o.description, "parameters": deepcopy(o.model_arguments)}
            for o in checked(operations) if o.tool]


def merge_tools(existing, operations):
    """Return a fresh catalogue. No replacement of a preexisting tool allowed."""
    added = tool_entries(operations)
    names = [x["name"] for x in existing] + [x["name"] for x in added]
    if len(names) != len(set(names)):
        raise ValueError("tool_projection_collision")
    return deepcopy(existing) + added
