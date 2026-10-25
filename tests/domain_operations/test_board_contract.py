"""Operation descriptions must remain consistent without becoming authority."""
from dataclasses import replace
import json

import pytest
from jsonschema import Draft202012Validator

from core.domain_operations.definitions import checked, merge_tools, tool_entries
from core.domain_operations.projections import artifacts, generate, openapi
from domains.guild.board_operations import operations


def test_all_schemas_valid_and_only_seven_routes_six_model_tools():
    rows = operations()
    assert len(rows) == 7 and len(tool_entries(rows)) == 6
    for op in rows:
        for schema in (op.arguments, op.result, op.model_arguments):
            if schema is not None:
                Draft202012Validator.check_schema(schema)
    doc = openapi(rows)
    assert sum(len(x) for x in doc["paths"].values()) == 7
    assert all(x["security"] for path in doc["paths"].values() for x in path.values())


def test_model_cannot_supply_authority_or_revision_but_can_use_unicode_and_clear_fields():
    by = {o.id: o for o in operations()}
    create = Draft202012Validator(by["guild.board.note.create"].model_arguments)
    create.validate({"text": "Morgen 👨‍👩‍👧‍👦\nBewerbung", "label": None})
    for key in ("principal", "owner", "executor", "environment", "operation_id", "expected_revision", "url"):
        assert not create.is_valid({"text": "A note", key: "injected"})
    patch = Draft202012Validator(by["guild.board.note.update"].model_arguments)
    patch.validate({"note_ref": "server-handle", "changes": {"done": False, "item_ref": None}})
    assert not patch.is_valid({"note_ref": "server-handle", "changes": {}})
    assert not patch.is_valid({"note_ref": "server-handle", "changes": {"command": "anything"}})


def test_http_update_requires_revision_and_query_is_a_bounded_read():
    by = {o.id: o for o in operations()}
    patch = Draft202012Validator(by["guild.board.note.update"].arguments)
    assert not patch.is_valid({"changes": {"text": "updated"}})
    patch.validate({"expected_revision": 1, "changes": {"text": "updated"}})
    query = by["guild.board.notes.query"]
    assert query.method == "POST" and query.effect == "read"
    assert not Draft202012Validator(query.arguments).is_valid({"limit": 21})


def test_registration_rejects_id_route_tool_and_handler_collisions():
    a, b = operations()[:2]
    for conflicting in (replace(b, id=a.id), replace(b, path=a.path, method=a.method),
                        replace(b, tool=a.tool), replace(b, handler=a.handler)):
        with pytest.raises(ValueError, match="collision"):
            checked([a, conflicting])
    with pytest.raises(ValueError, match="collision"):
        merge_tools(tool_entries([a]), [a])


def test_outputs_cannot_enable_tools_or_mutate_existing_catalogue():
    rows = operations()
    data = artifacts(rows)
    for name in ("tools.json", "plugin-declarations.json", "discovery.json"):
        assert data[name]["enabled"] is False
    assert not any("policy" in key or "authority" in key for key in data)
    existing = [{"name": "minimoi_old", "parameters": {"test": []}}]
    merged = merge_tools(existing, rows)
    merged[0]["parameters"]["test"].append(1)
    assert existing[0]["parameters"]["test"] == []
    data["tools.json"]["tools"][0]["parameters"]["properties"].clear()
    assert tool_entries(operations())[0]["parameters"]["properties"]


def test_generated_files_are_current_and_drift_is_detected(tmp_path):
    assert generate() == []
    generate(tmp_path, write=True)
    assert generate(tmp_path) == []
    (tmp_path / "tools.json").write_text(json.dumps({"tools": []}))
    assert generate(tmp_path) == ["tools.json"]


def test_runtime_board_projections_match_the_canonical_registry():
    from core.domain_operations.conversation import TOOL
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]
    expected={t['name']:t for t in tool_entries(operations())+[TOOL]}
    paths=('core/mc_tools/tools.json','services/mc_operations/plugins/minimoi-tools/tools.json')
    for path in paths:
        actual={t['name']:t for t in json.loads((root/path).read_text())['tools'] if t['name'] in expected}
        assert actual==expected
