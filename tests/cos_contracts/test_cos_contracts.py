"""B0 of the CoS-first 1.2 spec: the frozen contracts in contracts/cos/v1 and their fixtures.

Every schema is valid Draft 2020-12; every valid fixture passes and every invalid one fails; digests follow the
canonical rule; command replay pairs behave as section 4.5 says; and the data files keep their invariants
(one store writer, no standing spend, the model can't name its seat).
"""
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts" / "cos" / "v1"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEMA_FILES = sorted(CONTRACTS.glob("*.schema.json"))


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _registry():
    resources = [(doc["$id"], Resource.from_contents(doc)) for doc in map(_load, SCHEMA_FILES)]
    return Registry().with_resources(resources)


REGISTRY = _registry()


def _validator(stem):
    schema = _load(CONTRACTS / f"{stem}.schema.json")
    return Draft202012Validator(schema, registry=REGISTRY, format_checker=Draft202012Validator.FORMAT_CHECKER)


def _branch_validator(stem, doc):
    """The validator for the one $def whose schema constant the document names, so a failure is that branch's own.

    Falls back to the whole file when the file has a single top-level shape."""
    schema = _load(CONTRACTS / f"{stem}.schema.json")
    for name, branch in schema.get("$defs", {}).items():
        const = branch.get("properties", {}).get("schema", {}).get("const")
        if const is not None and const == doc.get("schema"):
            return Draft202012Validator({"$ref": f"{schema['$id']}#/$defs/{name}"}, registry=REGISTRY,
                                        format_checker=Draft202012Validator.FORMAT_CHECKER)
    return _validator(stem)


def canonical_digest(obj):
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _cases(kind):
    out = []
    for group in sorted(p for p in FIXTURES.iterdir() if p.is_dir()):
        for path in sorted((group / kind).glob("*.json")):
            out.append(pytest.param(group.name, path, id=f"{group.name}/{path.stem}"))
    return out


FROZEN_FILES = (
    "common.schema.json", "activity-record.schema.json", "store-records.schema.json", "store-command.schema.json",
    "read-surface.schema.json", "incident.schema.json", "consultation.schema.json", "issue-operation.schema.json",
    "intake.schema.json", "conversation.schema.json", "seat-binding.schema.json",
    "access-matrix.json", "capabilities.json", "legacy-mapping.json", "loop-inventory.json", "index.json",
)


def test_the_frozen_file_set_is_exactly_what_b0_froze():
    # Adding or removing a contract file is a spec change, not a coding choice. Naming each file here also lets CI's
    # focused test selection (scripts/ci/select_tests.py) find this module when any one of them changes.
    assert sorted(p.name for p in CONTRACTS.glob("*.json")) == sorted(FROZEN_FILES)


@pytest.mark.parametrize("path", SCHEMA_FILES, ids=lambda p: p.name)
def test_every_schema_is_valid_2020_12(path):
    Draft202012Validator.check_schema(_load(path))


@pytest.mark.parametrize("path", sorted(CONTRACTS.glob("*.json")), ids=lambda p: p.name)
def test_no_contract_file_repeats_a_key(path):
    # A repeated key (two "allOf" in one object) silently drops the first one; B0 caught exactly that once.
    def refuse_duplicates(pairs):
        keys = [k for k, _ in pairs]
        assert len(keys) == len(set(keys)), f"repeated keys in {path.name}: {sorted({k for k in keys if keys.count(k) > 1})}"
        return dict(pairs)
    json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=refuse_duplicates)


def test_index_names_every_schema_file_and_every_fixture_group_has_a_schema():
    index = _load(CONTRACTS / "index.json")
    named = set(index["contracts"].values()) | set(index["shared"])
    assert named == {p.name for p in SCHEMA_FILES}
    for group in (p for p in FIXTURES.iterdir() if p.is_dir()):
        assert (CONTRACTS / f"{group.name}.schema.json").exists(), group.name
        assert any((group / "valid").glob("*.json")) and any((group / "invalid").glob("*.json")), group.name


@pytest.mark.parametrize("group,path", _cases("valid"))
def test_valid_fixtures_pass(group, path):
    errors = sorted(_validator(group).iter_errors(_load(path)), key=lambda e: list(e.path))
    assert not errors, [f"{list(e.path)}: {e.message}" for e in errors[:5]]


@pytest.mark.parametrize("group,path", _cases("invalid"))
def test_invalid_fixtures_fail(group, path):
    doc = _load(path)
    assert not _validator(group).is_valid(doc)
    # And they fail inside the branch they claim to be, not merely by matching no branch at all.
    assert not _branch_validator(group, doc).is_valid(doc)


@pytest.mark.parametrize("path", sorted((FIXTURES / "activity-record").glob("*/*.json")), ids=lambda p: p.stem)
def test_activity_digest_follows_the_canonical_rule(path):
    record = _load(path)
    body = {k: v for k, v in record.items() if k not in ("seq", "digest", "published_at")}
    assert record["digest"] == canonical_digest(body)


def test_activity_digest_ignores_seq_and_published_at_but_not_content():
    record = _load(FIXTURES / "activity-record" / "valid" / "german_complete_final_day_precision.json")
    body = lambda r: {k: v for k, v in r.items() if k not in ("seq", "digest", "published_at")}
    moved = dict(record, seq=99, published_at="2026-11-01T00:00:00-05:00")
    assert canonical_digest(body(moved)) == record["digest"]
    changed = dict(record, count=2)
    assert canonical_digest(body(changed)) != record["digest"]


@pytest.mark.parametrize("path", sorted((FIXTURES / "store-command").glob("valid/*.json")), ids=lambda p: p.stem)
def test_command_payload_digest_matches_payload(path):
    doc = _load(path)
    if doc["schema"] == "minimoi.cos.store_command/1":
        assert doc["payload_digest"] == canonical_digest(doc["payload"])


@pytest.mark.parametrize("path", sorted((FIXTURES / "store-command" / "pairs").glob("*.json")), ids=lambda p: p.stem)
def test_command_pairs_replay_or_refuse(path):
    pair = _load(path)
    first, second = pair["first"], pair["second"]
    validator = _validator("store-command")
    assert validator.is_valid(first) and validator.is_valid(second)
    assert first["command_id"] == second["command_id"]
    same = first["payload_digest"] == second["payload_digest"]
    assert pair["expect"] == ("replay" if same else "refused")
    assert second["payload_digest"] == canonical_digest(second["payload"])


def test_access_matrix_one_store_writer_and_agent_read_only():
    matrix = _load(CONTRACTS / "access-matrix.json")
    rows = [(s["service"], a) for s in matrix["services"] for a in s["access"] if "host_path" in a]
    store_writers = {svc for svc, a in rows if a["host_path"].startswith("cos/store") and a["mode"] == "rw"}
    assert store_writers == {"cos-scheduler"}
    agent = {a["host_path"]: a["mode"] for svc, a in rows if svc == "cos-agent-a"}
    assert agent == {"cos/store/": "ro", "cos/conversations/": "ro", "data/": "ro"}
    assert not any(svc == "cos-bot" and a["host_path"].startswith("cos/store") for svc, a in rows)
    writers = {}
    for svc, a in rows:
        if a["host_path"].startswith("data/activity/") and a["mode"] == "rw":
            writers.setdefault(a["host_path"], set()).add(svc)
    assert writers.pop("data/activity/guild-build/") == {"supervisor", "portal"}
    assert all(len(v) == 1 for v in writers.values()) and set(writers) == {
        "data/activity/german/", "data/activity/portuguese/", "data/activity/curator/"}


def test_capabilities_cost_nothing_and_issue_create_is_exact():
    cat = _load(CONTRACTS / "capabilities.json")
    by = {c["id"]: c for c in cat["capabilities"]}
    assert cat["seat"] == "cos"
    assert all(c["spend"] == 0 for c in cat["capabilities"])
    assert by["cos.issue.create"]["tier"] == "exact"
    assert by["cos.outside_scope"]["tier"] == "exact"
    assert all("scheduled" not in c["origins"] for c in cat["capabilities"] if c["id"] == "cos.intake.submit")


def test_model_arguments_can_never_carry_authority():
    validator = _validator("seat-binding")
    base = {"schema": "minimoi.cos.tool_arguments/1", "tool": "cos.store.command", "arguments": {"what": "x"}}
    assert validator.is_valid(base)
    for key in ("seat", "actor", "origin", "grant_ref", "data_scope", "environment", "command_id",
                "expected_revision", "provenance", "approval", "basis_ref", "operation_id"):
        assert not validator.is_valid(dict(base, arguments={"what": "x", key: "injected"})), key


def test_only_personal_scope_is_written_and_reserved_names_are_refused_everywhere():
    for group in (p for p in FIXTURES.iterdir() if p.is_dir()):
        validator = _validator(group.name)
        for path in (group / "valid").glob("*.json"):
            doc = _load(path)
            if "data_scope" not in doc:
                continue
            assert doc["data_scope"] == "personal"
            for reserved in ("company:rvs", "client:acme"):
                assert not validator.is_valid(dict(doc, data_scope=reserved)), (group.name, path.stem, reserved)


def test_legacy_mapping_and_loop_inventory_cover_the_spec():
    mapping = _load(CONTRACTS / "legacy-mapping.json")["mappings"]
    assert mapping == [{"data_scope": "personal", "vault_scope": "robert", "harness_principal_id": "robert",
                        "rooms_database": mapping[0]["rooms_database"]}]
    loops = {row["id"] for row in _load(CONTRACTS / "loop-inventory.json")["loops"]}
    assert {"loop_b", "loop_c", "loop_h", "loop_r"} <= loops
