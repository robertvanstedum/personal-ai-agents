"""WS3 C1.4: Vault correctness checks and the canary contract, on synthetic fixtures only (no live query).

Includes Codex's C1.4 review cases (OPS_VAULT_CORRECTNESS_REVIEW_v0.1): wrong MC profile/version, objective gated on
prerequisites, a metadata-only file is not a served index, malformed nested policy fails safe per service.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.jobs import vault_checks as V

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 28, 20, 0, tzinfo=timezone.utc)
FRESH = "2026-11-28T19:00:00Z"
KEYWORD = {"keyword_only": "1", "updated_at": FRESH, "schema_version": "2"}
MEANING = {"embedder_digest": "sha256:abc", "updated_at": FRESH, "schema_version": "2"}
MC_FILTER = {"profile": "mc", "career_filter_digest": "d9ce", "career_filter_version": "1"}
FULL = {"desired_mode": "keyword", "objective": "usable_meaning_search", "max_age_s": 86400, "career_filter": "none",
        "index_path": "/x", "career_filter_digest": None}
MC = dict(FULL, max_age_s=7200, career_filter="required")


def index(path, meta, *, tables=("records", "chunks", "fts"), vec=None, rows=True):
    """A real minimal index: the store's tables (fts as a real FTS5 table), optional vec, optional rows."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    con.executemany("INSERT INTO meta VALUES (?, ?)", list(meta.items()))
    if "records" in tables:
        con.execute("CREATE TABLE records (record_id TEXT PRIMARY KEY, state TEXT NOT NULL)")
    if "chunks" in tables:
        con.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, record_id TEXT NOT NULL)")
    if "fts" in tables:
        con.execute("CREATE VIRTUAL TABLE fts USING fts5(text)")
    if vec if vec is not None else bool(meta.get("embedder_digest")):
        con.execute("CREATE TABLE vec (id INTEGER PRIMARY KEY)")     # vec0 needs an extension; presence is what is checked
    if rows and "records" in tables and "chunks" in tables:
        con.execute("INSERT INTO records VALUES ('r1', 'indexed')")
        con.execute("INSERT INTO chunks VALUES (1, 'r1')")
    con.commit()
    con.close()
    return path


def judge(policy, meta, structure="ok", canary=None):
    return V.check_service(policy, "ok", meta, structure, NOW, canary)


def test_pinned_constants_match_the_index_code():
    from core.memory_index import career_filter, store
    assert (V.MC_PROFILE, V.MC_FILTER_VERSION) == (career_filter.PROFILE, str(career_filter.FILTER_VERSION))
    assert V.INDEX_SCHEMA_VERSION == str(store.SCHEMA_VERSION)


def test_the_shipped_desired_state_is_valid_matches_production_and_leaves_the_canary_unset():
    doc = V.load_desired(REPO / "config/desired_state.json")
    for service in ("vault-ask", "vault-ask-mc"):
        policy = V.validate_service(doc["services"][service])
        assert isinstance(policy, dict) and policy["desired_mode"] == "keyword"
        assert V.validate_canary(doc, service) is None
    assert doc["canary"] == {"vault-ask": None, "vault-ask-mc": None}


def test_inspect_missing_corrupt_and_a_real_minimal_index(tmp_path):
    assert V.inspect_index(tmp_path / "none.sqlite") == ("missing", {}, "unknown")
    bad = tmp_path / "bad.sqlite"
    bad.write_text("not a database")
    assert V.inspect_index(bad)[0] == "corrupt"
    nometa = tmp_path / "nometa.sqlite"
    sqlite3.connect(nometa).close()
    assert V.inspect_index(nometa)[0] == "corrupt"
    good = index(tmp_path / "ok.sqlite", KEYWORD)
    assert V.inspect_index(good) == ("ok", KEYWORD, "ok")
    before = good.read_bytes()
    V.inspect_index(good)
    assert good.read_bytes() == before                                   # read-only


def test_a_metadata_only_file_is_not_a_served_index(tmp_path):
    """Codex C1.4 review 3."""
    only = index(tmp_path / "only.sqlite", MEANING, tables=(), vec=False)
    meta_state, meta, structure = V.inspect_index(only)
    assert structure == "missing_tables"
    r = V.check_service(dict(FULL, desired_mode="meaning"), meta_state, meta, structure, NOW, "pass")
    assert r["mode"] == "meaning" and r["mode_match"] == "unknown" and r["objective"] == "blocked"


def test_structure_cases(tmp_path):
    assert V.inspect_index(index(tmp_path / "novec.sqlite", MEANING, vec=False))[2] == "missing_tables"
    assert V.inspect_index(index(tmp_path / "old.sqlite", dict(KEYWORD, schema_version="1")))[2] == "incompatible"
    assert V.inspect_index(index(tmp_path / "empty.sqlite", KEYWORD, rows=False))[2] == "empty"
    assert V.inspect_index(index(tmp_path / "vec.sqlite", MEANING))[2] == "ok"


def test_keyword_serving_matches_the_approved_mode_but_the_objective_stays_pending():
    r = judge(FULL, KEYWORD)
    assert (r["mode_match"], r["objective"], r["meta"]) == ("match", "pending", "ok")


def test_meaning_objective_needs_every_prerequisite_and_a_passing_canary():
    approved = dict(FULL, desired_mode="meaning")
    assert judge(approved, MEANING)["objective"] == "unverified"
    assert judge(approved, MEANING, canary="fallback")["objective"] == "unverified"
    assert judge(approved, MEANING, canary="pass")["objective"] == "met"


@pytest.mark.parametrize("meta, which, structure", [
    (dict(MEANING, updated_at="2026-12-28T19:00:00Z"), "full", "ok"),     # future stamp: inconsistent
    (dict(MEANING, updated_at="2026-11-20T00:00:00Z"), "full", "ok"),     # stale
    (MEANING, "mc", "ok"),                                                # MC filter stamp missing: privacy violated
    (MEANING, "full", "empty"),
    (MEANING, "full", "missing_tables"),
])
def test_a_passing_canary_never_meets_the_objective_past_a_failed_prerequisite(meta, which, structure):
    """Codex C1.4 review 2."""
    base = dict(FULL if which == "full" else MC, desired_mode="meaning")
    assert judge(base, meta, structure, canary="pass")["objective"] == "blocked"


def test_a_mode_mismatch_is_reported_both_ways():
    assert judge(FULL, MEANING)["mode_match"] == "mismatch"
    assert judge(dict(FULL, desired_mode="meaning"), KEYWORD)["mode_match"] == "mismatch"


def test_missing_corrupt_and_inconsistent_metadata_are_unknown_not_fine():
    for state in ("missing", "corrupt"):
        r = V.check_service(FULL, state, {}, "unknown", NOW)
        assert r["meta"] == state and r["mode_match"] == "unknown" and r["objective"] == "unknown"
    r = judge(FULL, dict(KEYWORD, embedder_digest="sha256:abc"))
    assert r["meta"] == "inconsistent" and r["mode_match"] == "unknown"
    assert judge(FULL, {"updated_at": FRESH})["meta"] == "inconsistent"
    assert judge(FULL, {"keyword_only": "1"})["meta"] == "inconsistent"


def test_a_stale_index_is_flagged_and_still_matched():
    r = judge(FULL, dict(KEYWORD, updated_at="2026-11-26T00:00:00Z"))
    assert r["meta"] == "stale" and r["mode_match"] == "match"


def test_mc_privacy_needs_the_exact_profile_and_version():
    """Codex C1.4 review 1: wrong profile or version with any digest was 'ok'."""
    assert judge(MC, dict(KEYWORD, **MC_FILTER))["privacy"] == "stamp_ok"
    assert judge(MC, dict(KEYWORD, **dict(MC_FILTER, profile="unfiltered")))["privacy"] == "violated"
    assert judge(MC, dict(KEYWORD, **dict(MC_FILTER, career_filter_version="2")))["privacy"] == "violated"
    assert judge(MC, dict(KEYWORD, **dict(MC_FILTER, career_filter_digest="")))["privacy"] == "violated"
    assert judge(MC, KEYWORD)["privacy"] == "violated"
    assert judge(dict(MC, career_filter_digest="d9ce"), dict(KEYWORD, **MC_FILTER))["privacy"] == "verified"
    assert judge(dict(MC, career_filter_digest="ffff"), dict(KEYWORD, **MC_FILTER))["privacy"] == "violated"
    assert judge(FULL, KEYWORD)["privacy"] == "not_required"
    assert V.check_service(MC, "corrupt", {}, "unknown", NOW)["privacy"] == "unknown"


@pytest.mark.parametrize("change", [
    {"max_age_s": "REVIEW_MARKER"}, {"max_age_s": True}, {"max_age_s": -5}, {"max_age_s": 10 ** 9},
    {"desired_mode": "hybrid"}, {"objective": "anything"}, {"career_filter": "maybe"}, {"index_path": "relative"},
    {"career_filter_digest": 7}, {"career_filter_digest": "bad digest with spaces"},
])
def test_a_malformed_service_policy_is_invalid(change):
    assert V.validate_service(dict(FULL, **change)) == "invalid"
    assert V.validate_service(None) == "invalid" and V.validate_service([]) == "invalid"


def test_canary_section_validation():
    assert V.validate_canary({"canary": {"vault-ask": {"record_ref": "r", "question_ref": "q"}}}, "vault-ask") == \
        V.CanarySpec("vault-ask", "r", "q")
    assert V.validate_canary({"canary": {"vault-ask": None}}, "vault-ask") is None
    assert V.validate_canary({}, "vault-ask") is None
    for bad in ({"canary": []}, {"canary": {"vault-ask": "x"}}, {"canary": {"vault-ask": {"record_ref": "r"}}},
                {"canary": {"vault-ask": {"record_ref": "r", "question_ref": "two words"}}}):
        assert V.validate_canary(bad, "vault-ask") == "invalid"


SPEC = V.CanarySpec("vault-ask", "rec-ref-1", "q-ref-1")


def test_the_canary_codes():
    assert V.run_canary(None, lambda s: {}) == "not_configured"
    assert V.run_canary(SPEC, lambda s: {"mode": "meaning", "record_refs": ["rec-ref-1"]}) == "pass"
    assert V.run_canary(SPEC, lambda s: {"mode": "meaning", "record_refs": ["other"]}) == "miss"
    assert V.run_canary(SPEC, lambda s: {"mode": "keyword", "record_refs": ["rec-ref-1"]}) == "fallback"
    assert V.run_canary(SPEC, lambda s: {"mode": "meaning"}) == "error"
    def boom(s):
        raise TimeoutError("down")
    assert V.run_canary(SPEC, boom) == "error"


def _exporter():
    spec = importlib.util.spec_from_file_location("exporter_v", REPO / "scripts/jobs/exporter.py")
    ex = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ex)
    return ex


def _build(tmp_path, desired):
    from core.jobs import registry
    ex = _exporter()
    dpath = tmp_path / "desired.json"
    dpath.write_text(json.dumps(desired))
    return ex.build(registry.parse({"schema_version": 1, "jobs": []}), tmp_path / "h", {"jobs": str(tmp_path)}, "ec2",
                    ex.Aliases.load(), NOW, desired_path=dpath)


def test_the_exporter_publishes_fixed_codes_and_no_content(tmp_path):
    full = index(tmp_path / "full.sqlite", KEYWORD)
    mc = index(tmp_path / "mc.sqlite", dict(KEYWORD, **MC_FILTER))
    text = _build(tmp_path, {"schema_version": 1,
                             "services": {"vault-ask": dict(FULL, index_path=str(full)),
                                          "vault-ask-mc": dict(MC, index_path=str(mc))},
                             "canary": {"vault-ask": {"record_ref": "secret-record", "question_ref": "secret-question"}}})
    for line in ('minimoi_vault_check{check="mode_match",result="match",service="vault-ask"} 1',
                 'minimoi_vault_check{check="objective",result="pending",service="vault-ask"} 1',
                 'minimoi_vault_check{check="privacy",result="stamp_ok",service="vault-ask-mc"} 1',
                 'minimoi_vault_check{check="structure",result="ok",service="vault-ask"} 1',
                 'minimoi_vault_check{check="canary",result="not_configured",service="vault-ask-mc"} 1',
                 'minimoi_vault_check{check="canary",result="unknown",service="vault-ask"} 1'):
        assert line in text, line
    assert "secret" not in text and "sqlite" not in text and "minimoi_exporter_dropped 0" in text


def test_a_malformed_nested_policy_fails_safe_per_service_and_echoes_nothing(tmp_path):
    """Codex C1.4 review 4: a bad max_age_s aborted the whole exporter and echoed the value."""
    full = index(tmp_path / "full.sqlite", KEYWORD)
    text = _build(tmp_path, {"schema_version": 1,
                             "services": {"vault-ask": dict(FULL, index_path=str(full), max_age_s="REVIEW_MARKER"),
                                          "vault-ask-mc": dict(MC, index_path=str(full))},
                             "canary": ["REVIEW_MARKER"]})
    assert 'minimoi_vault_check{check="policy",result="invalid",service="vault-ask"} 1' in text
    assert 'minimoi_vault_check{check="policy",result="ok",service="vault-ask-mc"} 1' in text
    assert 'minimoi_vault_check{check="canary",result="invalid",service="vault-ask"} 1' in text
    assert "REVIEW_MARKER" not in text and "minimoi_exporter_snapshot_timestamp_seconds" in text


def test_a_missing_desired_file_is_unknown_for_both_services(tmp_path):
    from core.jobs import registry
    ex = _exporter()
    text = ex.build(registry.parse({"schema_version": 1, "jobs": []}), tmp_path / "h", {"jobs": str(tmp_path)}, "ec2",
                    ex.Aliases.load(), NOW, desired_path=tmp_path / "missing.json")
    assert 'minimoi_vault_check{check="policy",result="unknown",service="vault-ask"} 1' in text
    assert 'minimoi_vault_check{check="mode_match",result="unknown",service="vault-ask-mc"} 1' in text
