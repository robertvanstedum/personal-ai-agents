"""Deterministic descriptive projections; does not edit enabled runtime files.

python -m core.domain_operations.projections --check
python -m core.domain_operations.projections --write
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re

from .definitions import checked, tool_entries

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "contracts/domain-api/v1/guild-board"


def openapi(operations):
    paths = {}
    for op in checked(operations):
        params = [{"name": name, "in": "path", "required": True,
                   "schema": {"type": "string", "minLength": 1, "maxLength": 128}}
                  for name in re.findall(r"\{([a-z_]+)\}", op.path)]
        if op.effect == "private_write":
            params.append({"name": "Idempotency-Key", "in": "header", "required": True,
                           "schema": {"type": "string", "format": "uuid"},
                           "description": "Persist before dispatch; same identity and body on retry."})
        response = {"description": "Scoped application result; original receipt and current state are distinct.",
                    "content": {"application/json": {"schema": deepcopy(op.result)}}}
        responses = {"200": response}
        if op.id.endswith("note.create"):
            responses["201"] = deepcopy(response)
        for code in (400, 401, 403, 404, 409, 413, 422, 429, 503):
            responses[str(code)] = {"description": "Scoped refusal or uncertain result; never infer rollback from a transport error.",
                                   "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}
        route = {"operationId": op.id.replace(".", "_"), "description": op.description,
                 "parameters": params, "responses": responses, "security": [{"SignedRequest": []}],
                 "x-capability": op.capability, "x-operation-version": op.version,
                 "x-effect": op.effect, "x-enabled-by-this-document": False}
        if op.method != "GET":
            route["requestBody"] = {"required": True, "content": {
                "application/json": {"schema": deepcopy(op.arguments)}}}
        paths.setdefault(op.path, {})[op.method.lower()] = route
    return {"openapi": "3.1.0", "info": {"title": "MiniMoi internal Board API", "version": "1.0.0",
            "description": "Contract projection, not evidence of deployed routes. Internal signed callers only. "
                           "API-key notation represents the signature header, not bearer-token authentication; "
                           "shared domain API v0.2 exact method/path/body/nonce/time signing is mandatory. "
                           "Queries are signed JSON bodies; query strings are forbidden."},
            "paths": paths, "components": {"securitySchemes": {"SignedRequest": {
                "type": "apiKey", "in": "header", "name": "X-Minimoi-Signature",
                "description": "HMAC protocol; do not paste an application key here as a bearer token."}},
                "schemas": {"Error": {"type": "object", "required": ["state", "error"],
                    "additionalProperties": False, "properties": {
                        "state": {"enum": ["not_applied", "unknown"]},
                        "error": {"type": "string"}}}}}}


def artifacts(operations):
    rows = checked(operations)
    tools = tool_entries(rows)
    return {"openapi.json": openapi(rows),
            "tools.json": {"version": "board-tools/1", "enabled": False, "tools": tools},
            "plugin-declarations.json": {"version": 1, "enabled": False,
                "tool_names": [t["name"] for t in tools]},
            "discovery.json": {"version": 1, "enabled": False, "operations": [
                {"id": o.id, "version": o.version, "capability": o.capability,
                 "description": o.description, "handler": o.handler,
                 "dependencies": list(o.dependencies), "effect": o.effect, "undo": o.undo}
                for o in rows]}}


def serialized(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def generate(output=OUTPUT, *, write=False):
    from domains.guild.board_operations import operations
    expected = artifacts(operations())
    stale = []
    for name, value in expected.items():
        path = Path(output) / name
        content = serialized(value)
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        elif not path.is_file() or path.read_text(encoding="utf-8") != content:
            stale.append(name)
    return stale


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args()
    stale = generate(write=args.write)
    if stale:
        parser.exit(1, "Board operation projection drift: " + ", ".join(stale) + "\n")


if __name__ == "__main__":
    main()
