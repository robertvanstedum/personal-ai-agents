"""The v2 envelope: closed fields, closed payloads, helper-only facts, and the frozen intent hash (v0.6 section 5, appendix)."""
from __future__ import annotations

import copy

import pytest

from workshop_journal.conftest import envelope, progress
from core.workshop_journal import schema, strictjson

W, STREAM = "workshop-neubau", "workshop-neubau.local"
FIXED = {"event_id": "10000000-0000-4000-8000-000000000001", "at": "2026-10-08T20:00:00Z"}


def norm(env=None, **over):
    env = {**(env or envelope()), **FIXED, **over}
    return schema.normalize_intent(env, workshop=W, stream=STREAM, actors=schema.DEFAULT_ACTORS, resolved_at=None, event_id=None)


def refused(env_over: dict | None = None, **kw):
    with pytest.raises(schema.SchemaError) as info:
        norm(**(kw or {}), **({} if env_over is None else {}))
    return info.value


# ── the frozen fixture ─────────────────────────────────────────────────────────────────────────────────────────────
def test_the_intent_hash_is_frozen():
    """If this value changes, retries that straddle the change would see a conflict. Change it only with a new version."""
    assert schema.intent_hash(norm()) == "e812746735d712560263805661712eccc2b6a2befd9285aaf6fe8c5c30c87cf5"


def test_a_null_model_hashes_as_null_and_a_present_model_hashes_only_provider_and_requested():
    base = schema.intent_hash(norm(model=None))
    with_model = schema.intent_hash(norm(model={"provider": "openclaw", "requested": "workshop-routine"}))
    assert base != with_model
    outcome = {"provider": "openclaw", "requested": "workshop-routine", "actual": "other-model", "basis": "claimed",
               "evidence_ref": "run 42"}
    after_run = schema.intent_hash(norm(env=progress(), model=outcome))
    same_request = schema.intent_hash(norm(env=progress(), model={"provider": "openclaw", "requested": "workshop-routine"}))
    assert after_run == same_request                      # what actually ran never changes the identity of the event
    assert schema.intent_hash(norm(model={"provider": "openclaw", "requested": "workshop-strong"})) != with_model


def test_a_retry_that_omits_the_time_resolves_to_the_same_intent():
    full = norm()
    resolved = schema.normalize_intent({k: v for k, v in {**envelope(), **FIXED}.items() if k != "at"}, workshop=W,
                                       stream=STREAM, actors=schema.DEFAULT_ACTORS, resolved_at="2026-10-08T20:00:00Z",
                                       event_id=None)
    assert schema.intent_hash(full) == schema.intent_hash(resolved)


# ── helper-only facts and closed fields ────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("field", ["seq", "recorded_at", "origin", "intent_hash", "stream"])
def test_a_caller_cannot_supply_what_only_the_helper_knows(field):
    with pytest.raises(schema.SchemaError) as info:
        norm(**{field: "x" if field != "seq" else 5})
    assert info.value.reason == "helper_only_field"


def test_unknown_fields_and_unrecognised_versions_are_refused():
    with pytest.raises(schema.SchemaError):
        norm(secret="x")
    with pytest.raises(schema.SchemaError) as info:
        norm(v=3)
    assert info.value.reason == "unrecognized_schema_version"
    assert norm(v=2)["kind"] == "request"


@pytest.mark.parametrize("bad", [
    {"kind": "launched"}, {"kind": "review"}, {"kind": "cancel"}, {"kind": "withdraw"},        # not in the beta set
    {"actor": "someone"}, {"actor": "Codex"}, {"item": "anything"}, {"item": "pr:"}, {"topic": "Has Spaces"},
    {"stage": "later"}, {"text": ""}, {"text": "x" * 2001}, {"text": "bad\x00text"}, {"text": "a" * 5 + "\x07"},
    {"text": "token sk-abcdefghijklmnop"}, {"text": "see https://user:pass@example.com"},
    {"event_id": "10000000-0000-4000-8000-00000000000G"}, {"event_id": "ABCDEF00-0000-4000-8000-000000000001"},
    {"exchange_id": "not-a-uuid"}, {"in_reply_to": 5}, {"at": "2026-10-08T20:00:00"}, {"at": "tomorrow"},
    {"recipients": []}, {"recipients": ["codex", "codex"]}, {"recipients": ["nobody"]},
    {"recipients": ["codex"] * 2}, {"workshop": "someone-else"}, {"authority_ref": {"type": "x", "ref": "y"}},
    {"authority_ref": "robert"}, {"refs": [{"type": "artifact", "id": "x", "availability": "retained"}]},
    {"refs": [{"type": "shelf", "id": "x"}]}, {"refs": [{"type": "artifact", "id": "x"}] * 17},
    {"refs": [{"type": "url", "id": "x"}]}, {"model": {"provider": "p"}}, {"model": {"provider": "p", "requested": "r", "actual": "a",
                                                                                      "basis": "claimed"}},
])
def test_bad_envelopes_are_refused(bad):
    with pytest.raises(schema.SchemaError):
        norm(**bad)


def test_a_request_needs_a_recipient_and_software_kinds_are_not_general_input():
    with pytest.raises(schema.SchemaError) as info:
        norm(recipients=[])
    assert info.value.reason == "required_on_request"
    for kind, payload in (("delivery", {"request_id": FIXED["event_id"], "recipient": "codex", "attempt_id": FIXED["event_id"],
                                        "adapter": "file", "status": "queued"}),
                          ("recovery", {"manifest_hash": "a" * 64, "affected_byte_offset": 0, "action": "completed_lf"})):
        with pytest.raises(schema.SchemaError) as e2:
            norm(env=envelope(kind=kind, recipients=[], payload=payload, item="host:journal", actor="host"))
        assert e2.value.reason == "software_entry_point_only"


def test_times_are_normalised_to_utc():
    assert norm(at="2026-10-08T15:00:00-05:00")["at"] == "2026-10-08T20:00:00Z"
    assert norm(at="2026-10-08T20:00:00.5+00:00")["at"] == "2026-10-08T20:00:00.500000Z"


def test_references_are_inert_and_bounded():
    ok = [{"type": "artifact", "id": "a" * 20, "sha256": "a" * 64, "availability": "retained", "locator": "../../etc/passwd"},
          {"type": "shelf", "id": "rec", "edition": 2, "availability": "pointer_only"}]
    refs = norm(refs=ok)["refs"]
    assert refs[0]["locator"] == "../../etc/passwd"                    # inert display text, never read or fetched here
    assert refs[1]["edition"] == 2 and refs[1]["sha256"] is None


# ── payloads: every kind, closed keys ──────────────────────────────────────────────────────────────────────────────
U1, U2 = "20000000-0000-4000-8000-000000000002", "30000000-0000-4000-8000-000000000003"
VALID = {
    "request": envelope()["payload"],
    "receipt": {"request_id": U1, "recipient": "codex"},
    "claim": {"request_id": U1, "resource": "workshop-build", "generation": 0, "claimant": "claude-code"},
    "release": {"claim_id": U1, "generation": 0, "stopped": True, "reason": "work finished"},
    "started": {"action": "unit 1"}, "progress": {"action": "unit 1", "percent": 40},
    "result": {"request_id": U1, "recipient": "codex", "outcome": "completed", "limitations": ["not run on Linux"],
               "test_summary": {"run": 10, "passed": 10, "failed": 0}},
    "needs_you": {"reason_code": "decision_needed", "requested_action": "Choose the base.", "incident_id": U1},
    "decision": {"record_event_kind": "proposed", "resolves": [U1], "reason": "Proposal only."},
    "blocked": {"reason_code": "waiting_for_review"}, "done": {"outcome": "reported_completed"},
    "next": {"next_actor": "codex", "action": "review the diff"},
    "health": {"component": "journal", "status": "ok", "observed_at": "2026-10-08T20:00:00Z", "counts": {"events": 3}},
}
SOFTWARE = {"delivery": {"request_id": U1, "recipient": "codex", "attempt_id": U2, "adapter": "file", "status": "queued"},
            "recovery": {"manifest_hash": "b" * 64, "affected_byte_offset": 10, "action": "quarantined_truncated"}}


def _env(kind, payload):
    return envelope(kind=kind, payload=payload, recipients=["codex"] if kind == "request" else [], item="queue:146")


@pytest.mark.parametrize("kind", sorted(VALID))
def test_every_beta_kind_accepts_its_payload_and_refuses_unknown_keys_and_missing_required_ones(kind):
    required, _ = schema.PAYLOADS[kind]
    good = schema.normalize_intent({**_env(kind, VALID[kind]), **FIXED}, workshop=W, stream=STREAM, actors=schema.DEFAULT_ACTORS,
                                   resolved_at=None, event_id=None)
    assert set(good["payload"]) >= set(required)
    with pytest.raises(schema.SchemaError):
        schema.normalize_intent({**_env(kind, {**VALID[kind], "surprise": 1}), **FIXED}, workshop=W, stream=STREAM,
                                actors=schema.DEFAULT_ACTORS, resolved_at=None, event_id=None)
    for key in required:
        broken = {k: v for k, v in VALID[kind].items() if k != key}
        with pytest.raises(schema.SchemaError):
            schema.normalize_intent({**_env(kind, broken), **FIXED}, workshop=W, stream=STREAM, actors=schema.DEFAULT_ACTORS,
                                    resolved_at=None, event_id=None)


@pytest.mark.parametrize("kind", sorted(SOFTWARE))
def test_software_kinds_validate_when_a_software_entry_point_writes_them(kind):
    out = schema.normalize_intent({**_env(kind, SOFTWARE[kind]), **FIXED, "actor": "host", "item": "host:journal"}, workshop=W,
                                  stream=STREAM, actors=schema.DEFAULT_ACTORS, resolved_at=None, event_id=None, allow_software=True)
    assert out["kind"] == kind


def test_decision_payload_is_a_proposal_vocabulary_not_an_approval_by_label():
    out = norm(env=_env("decision", {**VALID["decision"], "record_event_kind": "approved-direct"}))
    assert out["payload"]["record_event_kind"] == "approved-direct"       # allowed to be written; only resolvers decide what closes
    with pytest.raises(schema.SchemaError):
        norm(env=_env("decision", {**VALID["decision"], "record_event_kind": "approved"}))


def test_counts_and_percent_must_be_finite_and_in_range():
    for bad in ({"percent": 101}, {"percent": -1}, {"percent": float("inf")}, {"percent": True}):
        with pytest.raises(schema.SchemaError):
            norm(env=_env("progress", {"action": "x", **bad}))
    with pytest.raises(schema.SchemaError):
        norm(env=_env("health", {**VALID["health"], "counts": {"events": float("nan")}}))


# ── events: size, persisted validation ─────────────────────────────────────────────────────────────────────────────
def test_an_event_over_32_kib_is_refused_not_truncated():
    big = ["😀" * 500] * 32                                    # four-byte characters: 64 KB of limitations
    intent = norm(env=_env("result", {**VALID["result"], "limitations": big}))
    with pytest.raises(schema.SchemaError) as info:
        schema.build_event(intent, seq=1, recorded_at="2026-10-08T20:00:00Z", origin=schema.make_origin("local-helper"))
    assert info.value.reason == "too_large"


def test_a_persisted_event_must_be_canonical_and_match_its_own_intent_hash():
    intent = norm()
    event = schema.build_event(intent, seq=1, recorded_at="2026-10-08T20:00:01Z", origin=schema.make_origin("local-helper"))
    assert schema.validate_persisted(copy.deepcopy(event)) == event
    tampered = copy.deepcopy(event)
    tampered["text"] = "A different sentence."                      # valid JSON, valid shape, wrong intent
    with pytest.raises(schema.SchemaError) as info:
        schema.validate_persisted(tampered)
    assert info.value.reason == "mismatch"
    extra = {**event, "extra": 1}
    with pytest.raises(schema.SchemaError):
        schema.validate_persisted(extra)


# ── strict JSON ────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,reason", [
    (b'{"a":1,"a":2}', "duplicate_key"), (b'{"a":NaN}', "non_finite_number"), (b'{"a":Infinity}', "non_finite_number"),
    (b'{"a":-Infinity}', "non_finite_number"), (b'{"a":1e999}', "non_finite_number"), (b'{"a":"\xff"}', "invalid_utf8"),
    (b'\xef\xbb\xbf{"a":1}', "byte_order_mark"), ("﻿{}", "byte_order_mark"), (b"{", "invalid_json"), (b'{"a":}', "invalid_json"),
])
def test_strict_json_refuses_what_two_readers_could_disagree_about(raw, reason):
    with pytest.raises(strictjson.StrictJSONError) as info:
        strictjson.loads(raw)
    assert info.value.reason == reason


def test_a_lone_surrogate_cannot_be_written():
    doc = strictjson.loads(b'{"a":"\\ud800"}')                      # JSON allows the escape; the journal does not
    with pytest.raises(strictjson.StrictJSONError) as info:
        strictjson.canonical_bytes(doc)
    assert info.value.reason == "invalid_utf8"


def test_canonical_bytes_are_stable():
    assert strictjson.canonical_bytes({"b": 1, "a": [1, "é"]}) == '{"a":[1,"é"],"b":1}'.encode()
