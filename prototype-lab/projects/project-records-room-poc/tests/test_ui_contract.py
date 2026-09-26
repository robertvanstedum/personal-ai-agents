"""Package U: shared UI contract schemas, simulated fixtures and the opt-in preview route.

Validates every fixture against the JSON Schema it declares (Draft 2020-12), checks that the schemas
encode the honesty rules, and that live API responses never carry the fixture-only "simulated" key.
"""
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
V2 = ROOT / "static" / "v2"
FIXTURES = V2 / "fixtures"


def load(path):
    return json.loads(path.read_text())


SCHEMAS = {path.name: load(path) for path in sorted(CONTRACTS.glob("*.schema.json"))}
INDEX = load(CONTRACTS / "index.json")
REGISTRY = Registry().with_resources((schema["$id"], Resource.from_contents(schema)) for schema in SCHEMAS.values())
FIXTURE_FILES = sorted(FIXTURES.glob("*.json"))


def validator_for(schema_id):
    schema = SCHEMAS[INDEX["contracts"][schema_id]]
    return Draft202012Validator(schema, registry=REGISTRY, format_checker=Draft202012Validator.FORMAT_CHECKER)


def errors(document):
    return sorted(validator_for(document["schema"]).iter_errors(document), key=lambda e: list(e.absolute_path))


def fixture(name):
    return load(FIXTURES / name)


def keys_anywhere(value):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from keys_anywhere(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from keys_anywhere(inner)


# ------------------------------------------------------------------ schemas

def test_expected_contract_files_exist():
    for name in ("overview", "attempt_detail", "record", "interpretation", "session_view", "action_result", "common"):
        assert f"{name}.schema.json" in SCHEMAS
    assert set(INDEX["contracts"].values()) | set(INDEX["shared"]) == set(SCHEMAS)


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_schema_is_valid_draft_2020_12(name):
    schema = SCHEMAS[name]
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].startswith("urn:minimoi:contract:")
    Draft202012Validator.check_schema(schema)


def test_roster_shape_matches_plan_section_5():
    roster = SCHEMAS["overview.schema.json"]["$defs"]["roster"]
    assert roster["properties"]["schema"] == {"const": "minimoi.work.roster/1"}
    assert {"observed_at", "refresh_s", "monitor", "agents", "attempts", "completed_recent"} <= set(roster["required"])
    attempt = SCHEMAS["overview.schema.json"]["$defs"]["attempt"]["properties"]
    for field in ("attempt_id", "assignment_id", "work_ref", "agent", "adapter_kind", "execution", "wait_reason", "step",
                  "started_at", "elapsed_s", "last_contact_at", "last_contact_source", "last_progress_at", "freshness",
                  "next_owner", "trace_id"):
        assert field in attempt


# ------------------------------------------------------------------ fixtures

def test_fixture_set_is_not_empty():
    assert len(FIXTURE_FILES) >= 20


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.name)
def test_fixture_validates_against_its_declared_schema(path):
    document = load(path)
    assert document.get("schema") in INDEX["contracts"], f"{path.name} declares no known schema"
    problems = errors(document)
    assert not problems, "\n".join(f"{list(e.absolute_path)}: {e.message}" for e in problems[:5])


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.name)
def test_every_fixture_is_marked_simulated(path):
    assert load(path).get("simulated") is True


def test_every_fixture_is_used_by_the_fixture_adapter_and_every_reference_exists():
    source = (V2 / "adapter_fixture.js").read_text()
    referenced = set(re.findall(r"'([a-z0-9_.-]+\.json)'", source))
    assert referenced == {path.name for path in FIXTURE_FILES}


def test_record_segment_hashes_match_their_text():
    for path in FIXTURES.glob("record.rec-*.json"):
        document = load(path)
        for segment in document["segments"]:
            assert segment["sha256"] == hashlib.sha256(segment["text"].encode()).hexdigest(), (path.name, segment["ordinal"])


def test_fixtures_use_synthetic_neutral_content_only():
    banned = re.compile(r"(/Users/|/home/|@[a-z0-9-]+\.(com|org|net)|password|secret|token|api[_-]?key)", re.IGNORECASE)
    for path in FIXTURE_FILES:
        text = path.read_text()
        assert not banned.search(text), f"{path.name}: {banned.search(text).group(0)}"


def test_store_unavailable_scenario_is_unknown_not_zero():
    overview = fixture("overview.store_unavailable.json")
    assert overview["roster"]["code"] == "store_unavailable"
    assert overview["attention"]["status"] == "unknown"
    assert "exceptions" not in overview["attention"]
    for entry in overview["health"].values():
        assert entry["status"] in {"unavailable", "unknown"}
    for name in ("records", "record", "attempt", "interpretation", "session"):
        assert fixture(f"{name}.unavailable.json")["code"] == "store_unavailable"


def test_uploaded_transcript_approval_stays_reported():
    interpretation = fixture("interpretation.recovery-placement.json")
    reported = interpretation["body"]["reported_decisions"]
    assert any(r["summary"] == "Robert approved deployment" and r["uploaded_by"] == "robert" for r in reported)
    for decision in interpretation["body"]["owner_decisions"]:
        cited = decision["decision_record"]
        record = fixture(f"record.{cited['source_id']}.r{cited['revision']}.json")
        assert record["authorship"] == "direct" and record["kind"] == "owner_decision"
        assert record["submitter"]["route"] == "owner_decision"
    transcript = fixture("record.rec-2b90.r1.json")
    assert transcript["authorship"] == "imported" and transcript["submitter"]["principal"] == "robert"


# ------------------------------------------------------------------ schemas encode the honesty rules

def rejects(document):
    return bool(errors(document))


def test_schema_rejects_zero_where_unknown_is_required():
    overview = fixture("overview.store_unavailable.json")
    broken = copy.deepcopy(overview)
    broken["attention"] = {"status": "known", "observed_at": overview["observed_at"], "observer": "ops-monitor", "exceptions": [], "decisions": []}
    assert not rejects(broken)  # a real, observed zero is allowed …
    broken = copy.deepcopy(overview)
    broken["health"]["local_workshop"] = {"status": "available", "detail": "fine", "observed_at": None, "source": None}
    assert rejects(broken)  # … but "available" without an observation is not


def test_schema_rejects_owner_decision_without_direct_authorship():
    interpretation = fixture("interpretation.recovery-placement.json")
    for mutate in (lambda d: d.update(authorship="imported"), lambda d: d.pop("decided_by"), lambda d: d.pop("decided_at")):
        broken = copy.deepcopy(interpretation)
        mutate(broken["body"]["owner_decisions"][0])
        assert rejects(broken)


def test_schema_rejects_accepted_without_who_and_when():
    overview = fixture("overview.normal.json")
    broken = copy.deepcopy(overview)
    broken["roster"]["completed_recent"][0]["accepted"] = None
    assert rejects(broken)
    broken = copy.deepcopy(overview)
    broken["roster"]["attempts"][0]["assurance"] = "accepted"
    assert rejects(broken)
    detail = fixture("attempt.export-009-1.json")
    broken = copy.deepcopy(detail)
    broken["assurance"]["accepted"]["authority"] = "builder"
    assert rejects(broken)
    broken = copy.deepcopy(detail)
    broken["assurance"]["accepted"]["by"] = None
    assert rejects(broken)


def test_schema_rejects_self_report_passed_off_as_observation():
    detail = fixture("attempt.capture-014-1.json")
    broken = copy.deepcopy(detail)
    entry = next(e for e in broken["timeline"] if e["kind"] == "self_report")
    entry["basis"] = "observation"
    assert rejects(broken)


def test_schema_rejects_working_without_current_observed_attempt():
    session = fixture("session.normal.json")
    for mutate in (lambda w: w.update(freshness="stale"), lambda w: w.update(attempt_id=None), lambda w: w.update(observed_at=None)):
        broken = copy.deepcopy(session)
        mutate(next(p for p in broken["participants"] if p["working"]["state"] == "working")["working"])
        assert rejects(broken)


def test_schema_rejects_standing_read_as_membership():
    broken = copy.deepcopy(fixture("session.normal.json"))
    broken["standing_visibility"][0]["membership"] = True
    assert rejects(broken)


def test_schema_rejects_complete_excerpt_and_unverified_declared_speaker_as_submitter():
    record = fixture("record.rec-7f1c.r2.json")
    broken = copy.deepcopy(record)
    broken["coverage"] = {"state": "complete", "missing": [], "note": None}
    assert rejects(broken)
    broken = copy.deepcopy(record)
    broken["submitter"]["authenticated"] = False
    assert rejects(broken)


def test_schema_rejects_uncertain_outcome_without_recovery_and_resolution_without_evidence():
    detail = fixture("attempt.room-021-2.recovery.json")
    broken = copy.deepcopy(detail)
    broken["recovery"] = None
    assert rejects(broken)
    broken = copy.deepcopy(detail)
    broken["recovery"]["state"] = "resolved"
    assert rejects(broken)


def test_schema_rejects_zero_cost_for_unknown_usage():
    broken = copy.deepcopy(fixture("interpretation.recovery-placement.json"))
    broken["usage"] = {"status": "unavailable", "cost_usd": 0}
    assert rejects(broken)


# ------------------------------------------------------------------ preview route

@pytest.fixture
def app_without_preview(tmp_path):
    return create_app(tmp_path / "plain", testing=True)


@pytest.fixture
def preview_app(tmp_path):
    return create_app(tmp_path / "preview", testing=True, ui_preview=True)


def test_preview_is_404_without_the_flag(app_without_preview):
    client = app_without_preview.test_client()
    for path in ("/preview/", "/preview", "/static/v2/preview.html", "/static/v2/main.js", "/static/v2/fixtures/overview.normal.json"):
        assert client.get(path).status_code == 404, path
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_preview_is_served_with_the_flag_and_needs_no_login(preview_app):
    client = preview_app.test_client()
    response = client.get("/preview/")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Preview · simulated data · actions change nothing" in page
    assert "/static/v2/main.js" in page and "adapter_http" not in page
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert client.get("/preview").status_code in {301, 308}
    assert client.get("/static/v2/fixtures/overview.normal.json").json["simulated"] is True


def test_preview_does_not_read_the_records_store(preview_app, monkeypatch):
    store = preview_app.extensions["records_store"]
    def forbidden(*args, **kwargs):
        raise AssertionError("the preview must not touch the Records store")
    for name in ("connect", "authenticate", "rooms", "room"):
        monkeypatch.setattr(store, name, forbidden)
    monkeypatch.setattr(store.platform_access, "authenticate", forbidden)
    client = preview_app.test_client()
    assert client.get("/preview/").status_code == 200
    assert client.get("/static/v2/adapter_fixture.js").status_code == 200


def test_existing_routes_unchanged_with_the_flag(preview_app):
    client = preview_app.test_client()
    assert client.get("/api/v1/rooms").status_code == 401
    assert client.get("/health").json["production_connected"] is False
    assert client.get("/preview/", headers={"Host": "attacker.example"}).status_code == 403
    assert client.get("/preview/", headers={"Origin": "https://attacker.example"}).status_code == 403


def test_live_api_responses_never_carry_simulated(preview_app):
    store = preview_app.extensions["records_store"]
    room = store.create_room("robert", str(uuid4()), dict(title="Synthetic contract check", purpose="Check live payloads",
                                                        mode="meeting", recording_acknowledged=True))["result"]["id"]
    store.append("robert", str(uuid4()), room, dict(body="A synthetic contribution"))
    client = preview_app.test_client()
    assert client.post("/api/login", json={"token": store.owner_key}).status_code == 200
    created = client.post("/api/v1/rooms", json=dict(title="Second synthetic room", purpose="Payload check", mode="meeting",
                                                     recording_acknowledged=True), headers={"Idempotency-Key": "u-contract"})
    assert created.status_code == 201
    payloads = [created.json]
    for path in ("/api/v1/me", "/api/v1/status", "/api/v1/rooms", "/api/v2/rooms", f"/api/v1/rooms/{room}",
                 f"/api/v2/sessions/{room}", "/api/v1/coordination/inbox", f"/api/v1/rooms/{room}/coordination",
                 "/api/v1/search?q=synthetic", "/api/v1/principals", "/api/v1/platform/credentials", "/api/v1/artifact-history"):
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code)
        payloads.append(response.json)
    for payload in payloads:
        assert "simulated" not in set(keys_anywhere(payload))


def test_http_adapter_refuses_simulated_payloads_and_is_not_wired_into_the_preview():
    http_source = (V2 / "adapter_http.js").read_text()
    assert "A4 — not wired in U" in http_source
    assert re.search(r"hasOwnProperty\.call\(payload, 'simulated'\)\) throw new SimulatedPayloadError", http_source)
    assert "rejectSimulated(await response.json()" in http_source
    for path in [V2 / "preview.html", *V2.glob("*.js"), *(V2 / "views").glob("*.js")]:
        if path.name == "adapter_http.js":
            continue
        text = path.read_text()
        assert not re.search(r"(import|from)[^;\n]*adapter_http|import\([^)]*adapter_http", text), path.name
        assert not re.search(r"fetch\w*\([^)]*/api/", text), path.name
