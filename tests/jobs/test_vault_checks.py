"""WS3 C1.4: Vault correctness checks and the canary contract, on synthetic fixtures only (no live query)."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.jobs import vault_checks as V

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 28, 20, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)
KEYWORD = {"keyword_only": "1", "updated_at": "2026-11-28T19:00:00Z"}
MEANING = {"embedder_digest": "sha256:abc", "updated_at": "2026-11-28T19:00:00Z"}
MC_FILTER = {"profile": "mc", "career_filter_digest": "d9ce", "career_filter_version": "1"}
FULL = {"desired_mode": "keyword", "objective": "usable_meaning_search", "max_age_s": 86400, "career_filter": "none"}
MC = {"desired_mode": "keyword", "objective": "usable_meaning_search", "max_age_s": 7200, "career_filter": "required"}


def index(path, meta):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    con.executemany("INSERT INTO meta VALUES (?, ?)", list(meta.items()))
    con.commit()
    con.close()
    return path


def test_the_shipped_desired_state_matches_production_truth_and_leaves_the_canary_unset():
    doc = V.load_desired(REPO / "config/desired_state.json")
    assert doc["services"]["vault-ask"]["desired_mode"] == "keyword"          # promotion not yet approved
    assert doc["services"]["vault-ask-mc"]["career_filter"] == "required"
    assert doc["canary"] == {"vault-ask": None, "vault-ask-mc": None}            # the owner has not chosen
    assert V.canary_spec(doc, "vault-ask") is None


def test_read_meta_missing_corrupt_and_ok(tmp_path):
    assert V.read_meta(tmp_path / "none.sqlite") == ("missing", {})
    bad = tmp_path / "bad.sqlite"
    bad.write_text("not a database")
    assert V.read_meta(bad) == ("corrupt", {})
    nometa = tmp_path / "nometa.sqlite"
    sqlite3.connect(nometa).close()
    assert V.read_meta(nometa)[0] == "corrupt"
    assert V.read_meta(index(tmp_path / "ok.sqlite", KEYWORD)) == ("ok", KEYWORD)


def test_keyword_serving_matches_the_approved_mode_but_the_objective_stays_pending():
    r = V.check_service(FULL, "ok", KEYWORD, NOW)
    assert r["mode_match"] == "match" and r["objective"] == "pending" and r["meta"] == "ok"


def test_meaning_serving_without_a_passing_canary_is_unverified_never_met():
    approved = dict(FULL, desired_mode="meaning")
    assert V.check_service(approved, "ok", MEANING, NOW)["objective"] == "unverified"
    assert V.check_service(approved, "ok", MEANING, NOW, canary="fallback")["objective"] == "unverified"
    assert V.check_service(approved, "ok", MEANING, NOW, canary="pass")["objective"] == "met"


def test_a_mode_mismatch_is_reported_both_ways():
    assert V.check_service(FULL, "ok", MEANING, NOW)["mode_match"] == "mismatch"           # promoted without approval
    assert V.check_service(dict(FULL, desired_mode="meaning"), "ok", KEYWORD, NOW)["mode_match"] == "mismatch"


def test_missing_corrupt_and_inconsistent_metadata_are_unknown_not_fine():
    for state in ("missing", "corrupt"):
        r = V.check_service(FULL, state, {}, NOW)
        assert r["meta"] == state and r["mode_match"] == "unknown" and r["objective"] == "unknown"
    both = dict(KEYWORD, embedder_digest="sha256:abc")
    r = V.check_service(FULL, "ok", both, NOW)
    assert r["meta"] == "inconsistent" and r["mode_match"] == "unknown"
    r = V.check_service(FULL, "ok", {"updated_at": "2026-11-28T19:00:00Z"}, NOW)
    assert r["meta"] == "inconsistent"
    future = dict(KEYWORD, updated_at="2026-12-28T19:00:00Z")
    assert V.check_service(FULL, "ok", future, NOW)["meta"] == "inconsistent"
    nostamp = {"keyword_only": "1"}
    assert V.check_service(FULL, "ok", nostamp, NOW)["meta"] == "inconsistent"


def test_a_stale_index_is_flagged_and_still_judged():
    old = dict(KEYWORD, updated_at="2026-11-26T00:00:00Z")
    r = V.check_service(FULL, "ok", old, NOW)
    assert r["meta"] == "stale" and r["mode_match"] == "match"


def test_no_approved_state_is_unknown():
    r = V.check_service(None, "ok", KEYWORD, NOW)
    assert r["mode_match"] == "unknown" and r["objective"] == "unknown"
    assert V.check_service(dict(FULL, desired_mode="hybrid"), "ok", KEYWORD, NOW)["mode_match"] == "unknown"


def test_mc_privacy_filter():
    assert V.check_service(MC, "ok", dict(KEYWORD, **MC_FILTER), NOW)["privacy"] == "ok"
    assert V.check_service(MC, "ok", KEYWORD, NOW)["privacy"] == "violated"                 # filter stamp missing
    partial = dict(KEYWORD, profile="mc", career_filter_digest="d9ce")
    assert V.check_service(MC, "ok", partial, NOW)["privacy"] == "violated"
    pinned = dict(MC, career_filter_digest="ffff")
    assert V.check_service(pinned, "ok", dict(KEYWORD, **MC_FILTER), NOW)["privacy"] == "violated"
    assert V.check_service(FULL, "ok", KEYWORD, NOW)["privacy"] == "not_required"
    assert V.check_service(MC, "corrupt", {}, NOW)["privacy"] == "unknown"                  # cannot read: unknown


SPEC = V.CanarySpec("vault-ask", "rec-ref-1", "q-ref-1")


def test_the_canary_codes():
    assert V.run_canary(None, lambda s: {}) == "not_configured"
    assert V.run_canary(SPEC, lambda s: {"mode": "meaning", "record_refs": ["rec-ref-1"]}) == "pass"
    assert V.run_canary(SPEC, lambda s: {"mode": "meaning", "record_refs": ["other"]}) == "miss"
    assert V.run_canary(SPEC, lambda s: {"mode": "keyword", "record_refs": ["rec-ref-1"]}) == "fallback"
    assert V.run_canary(SPEC, lambda s: {"mode": "keyword", "record_refs": []}) == "miss"
    assert V.run_canary(SPEC, lambda s: {"mode": "meaning"}) == "error"
    def boom(s):
        raise TimeoutError("service down")
    assert V.run_canary(SPEC, boom) == "error"


def test_a_canary_spec_needs_both_refs():
    doc = {"canary": {"vault-ask": {"record_ref": "r", "question_ref": "q"}, "vault-ask-mc": {"record_ref": "r"}}}
    assert V.canary_spec(doc, "vault-ask") == V.CanarySpec("vault-ask", "r", "q")
    assert V.canary_spec(doc, "vault-ask-mc") is None


def test_the_exporter_publishes_fixed_codes_and_no_content(tmp_path):
    spec = importlib.util.spec_from_file_location("exporter_v", REPO / "scripts/jobs/exporter.py")
    ex = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ex)
    from core.jobs import registry
    full = index(tmp_path / "full.sqlite", KEYWORD)
    mc = index(tmp_path / "mc.sqlite", dict(KEYWORD, **MC_FILTER))
    desired = {"schema_version": 1, "services": {"vault-ask": dict(FULL, index_path=str(full)),
                                                  "vault-ask-mc": dict(MC, index_path=str(mc))},
               "canary": {"vault-ask": {"record_ref": "secret-record", "question_ref": "secret-question"}}}
    dpath = tmp_path / "desired.json"
    dpath.write_text(json.dumps(desired))
    reg = registry.parse({"schema_version": 1, "jobs": []})
    text = ex.build(reg, tmp_path / "h", {"jobs": str(tmp_path)}, "ec2", ex.Aliases.load(), NOW, desired_path=dpath)
    for line in ('minimoi_vault_check{check="mode_match",result="match",service="vault-ask"} 1',
                 'minimoi_vault_check{check="objective",result="pending",service="vault-ask"} 1',
                 'minimoi_vault_check{check="privacy",result="ok",service="vault-ask-mc"} 1',
                 'minimoi_vault_check{check="canary",result="not_configured",service="vault-ask-mc"} 1',
                 'minimoi_vault_check{check="canary",result="unknown",service="vault-ask"} 1'):
        assert line in text, line
    assert "secret" not in text and "sqlite" not in text and "minimoi_exporter_dropped 0" in text
    blind = ex.build(reg, tmp_path / "h", {"jobs": str(tmp_path)}, "ec2", ex.Aliases.load(), NOW,
                     desired_path=tmp_path / "missing.json")
    assert 'minimoi_vault_check{check="mode_match",result="unknown",service="vault-ask"} 1' in blind
