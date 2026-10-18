"""The Vault reconciliation: every source item gets exactly one state, and production is compared by key-set digest.
Synthetic shelf, ledger and session files only."""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("vault_reconcile", ROOT / "scripts/vault/reconcile.py")
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

U = ["1" * 8 + "-1111-1111-1111-" + "1" * 12, "2" * 8 + "-2222-2222-2222-" + "2" * 12,
     "3" * 8 + "-3333-3333-3333-" + "3" * 12, "4" * 8 + "-4444-4444-4444-" + "4" * 12, "5" * 8 + "-5555-5555-5555-" + "5" * 12]


def setup(tmp_path):
    shelf, codex = tmp_path / "shelf", tmp_path / "codex"
    (shelf / "_ledger").mkdir(parents=True)
    day = codex / "2026" / "10" / "16"
    day.mkdir(parents=True)
    names = [f"rollout-2026-10-16T10-00-0{i}-{u}.jsonl" for i, u in enumerate(U)]
    for n in names[:4]:
        (day / n).write_text("{}\n")
    (day / (names[4] + ".zst")).write_bytes(b"x")                     # compressed only: counted once, by its plain name
    index = {f"codex:{U[0]}": {}, f"codex:{U[1]}": {}, "grok:old-export-only": {}}
    rel = lambda n: f"2026/10/16/{n}"
    rows = [{"key": R.ledger_key("codex", rel(names[1])), "outcome": "failed", "reason": "source_too_large"},  # older edition kept
            {"key": R.ledger_key("codex", rel(names[2])), "outcome": "excluded", "reason": "no_turns"},
            {"key": R.ledger_key("codex", rel(names[3])), "outcome": "failed", "reason": "write_failed"}]
    (shelf / "_ledger" / "source_index.json").write_text(json.dumps(index))
    (shelf / "_ledger" / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return shelf, codex, index


def test_every_session_gets_one_state_and_gaps_carry_no_ids(tmp_path):
    shelf, codex, _ = setup(tmp_path)
    r = R.reconcile(shelf, [], None, codex, None, "2026")
    s = r["sources"]["codex"]
    assert s["source_items"] == 5
    assert s["states"] == {"on_shelf": 1, "failed": 2, "excluded": 1, "not_seen": 1}
    assert s["reasons"] == {"failed:source_too_large": 1, "excluded:no_turns": 1, "failed:write_failed": 1}
    assert s["months_on_shelf"] == {"2026-10": 1}
    assert all(len(g["key"]) == 12 and U[0][:8] not in json.dumps(g) for g in s["gaps"])
    assert r["shelf_only"] == {"grok": 1}                                # an older export's record: listed, not an error


def test_production_is_compared_by_key_set_digest(tmp_path):
    shelf, codex, index = setup(tmp_path)
    codex_keys = {k for k in index if k.startswith("codex:")}
    installed = tmp_path / "installed.json"
    installed.write_text(json.dumps({"counts": {"codex": 2, "grok": 1},
                                     "key_digests": {"codex": R.key_digest(codex_keys), "grok": R.key_digest({"grok:other"})},
                                     "key_digest_all": R.key_digest(set(index))}))
    p = R.reconcile(shelf, [], None, codex, installed, "2026")["production"]
    assert p["codex"]["identical_set"] is True and p["grok"]["identical_set"] is False and p["_all"]["identical_set"] is True


def test_claude_code_month_comes_from_the_first_timestamp(tmp_path):
    proj = tmp_path / "projects" / "p"
    proj.mkdir(parents=True)
    (proj / f"{U[0]}.jsonl").write_text('{"type":"x"}\n{"timestamp":"2026-05-28T10:00:00Z"}\n')
    assert [m for _, m, _ in R.session_items("claude-code", tmp_path / "projects")] == ["2026-05"]


def test_a_shelf_record_is_fresh_only_after_a_confirmed_capture(tmp_path):
    """On the shelf is not enough: unless the latest ledger outcome confirms the newest content was captured, the item is
    ``stale`` and listed as a gap with its outcome as the reason. Failed stays failed."""
    shelf, codex = tmp_path / "shelf", tmp_path / "codex"
    (shelf / "_ledger").mkdir(parents=True)
    day = codex / "2026" / "10" / "16"
    day.mkdir(parents=True)
    outcomes = [("captured", None), ("edition-added", None), ("unchanged", None), ("unstable", None), ("discovered", None),
                ("disk_low", None), ("excluded", "never_copy"), ("failed", "parse_failed")]
    ids = [f"{i:08x}-0000-0000-0000-{i:012x}" for i in range(len(outcomes))]
    rows, index = [], {}
    for i, (uid, (outcome, reason)) in enumerate(zip(ids, outcomes)):
        name = f"rollout-2026-10-16T10-00-{i:02d}-{uid}.jsonl"
        (day / name).write_text("{}\n")
        index[f"codex:{uid}"] = {}
        key = R.ledger_key("codex", f"2026/10/16/{name}")
        rows.append({"key": key, "outcome": "captured"})                          # an earlier, confirmed capture ...
        rows.append({"key": key, "outcome": outcome, **({"reason": reason} if reason else {})})  # ... then the latest
    (shelf / "_ledger" / "source_index.json").write_text(json.dumps(index))
    (shelf / "_ledger" / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    s = R.reconcile(shelf, [], None, codex, None, "2026")["sources"]["codex"]
    assert s["states"] == {"on_shelf": 3, "stale": 4, "failed": 1}
    assert s["reasons"] == {"stale:unstable": 1, "stale:discovered": 1, "stale:disk_low": 1,
                            "stale:excluded:never_copy": 1, "failed:parse_failed": 1}
    assert s["months_on_shelf"] == {"2026-10": 3}                              # only confirmed items count as on the shelf
    assert sorted((g["state"], g["reason"]) for g in s["gaps"]) == [
        ("failed", "parse_failed"), ("stale", "discovered"), ("stale", "disk_low"),
        ("stale", "excluded:never_copy"), ("stale", "unstable")]
    assert all(len(g["key"]) == 12 for g in s["gaps"])


# ── production inventory (incremental sync, 17 Oct 2026) ──
def _real_shelf(tmp_path):
    from tests.memory_index.helpers import add_record, make_shelf
    shelf = make_shelf(tmp_path)
    ids = [add_record(shelf, "grok", f"g-{i}", [("human", f"question {i}"), ("assistant", f"answer {i}")], created=f"2026-0{i + 4}-02T10:00:00Z")
           for i in range(3)]
    ids.append(add_record(shelf, "codex", "c-0", [("human", "codex question"), ("assistant", "codex answer")]))
    return shelf, ids


def _inventory(shelf_root, versions):
    from core.memory_sync import versions as V
    return V.inventory_doc(shelf_root, versions, generated_utc="2026-10-17T10:00:00Z", through_batch="20261017T090000Z-00000000", release="r")


def test_the_real_installed_receipt_shape_is_read(tmp_path):
    """installed.json carries ``source_keys.by_provider``; r3's reconcile looked for keys it never has, so identical_set was always False."""
    from core.memory_backup.production import source_key_facts
    shelf, codex, index = setup(tmp_path)
    installed = tmp_path / "installed.json"
    installed.write_text(json.dumps({"source_keys": source_key_facts(shelf)}))
    p = R.reconcile(shelf, [], None, codex, installed, "2026")["production"]
    assert p["codex"] == {"shelf_records": 2, "production_records": 2, "identical_set": True}
    assert p["grok"]["identical_set"] is True and p["_all"]["identical_set"] is True


def test_an_identical_production_inventory_reports_identical_version_and_key_sets(tmp_path):
    from core.memory_sync import versions as V
    shelf, _ = _real_shelf(tmp_path)
    got, _ = V.record_versions(shelf.root)
    inv = tmp_path / "inventory.json"
    inv.write_text(json.dumps(_inventory(shelf.root, got)))
    r = R.reconcile(shelf.root, [], None, None, None, "2026", inventory=inv, sync_state=tmp_path / "none", sync_status=tmp_path / "none.json")
    v = r["production"]["versions"]
    assert v["identical_set"] is True and v["gaps"] == 0 and v["shelf_versions"] == v["production_versions"] == 4
    assert r["production"]["grok"]["identical_set"] is True and r["production"]["_all"]["identical_set"] is True
    assert r["production"]["gaps"] == [] and r["sync"]["through_batch"] == "20261017T090000Z-00000000"


def test_versions_production_lacks_are_gaps_unless_pending_and_gaps_carry_provider_and_month_only(tmp_path):
    from core.memory_sync import versions as V
    from tests.memory_index.helpers import add_record
    shelf, ids = _real_shelf(tmp_path)
    got, _ = V.record_versions(shelf.root)
    prod = {rid: v for rid, v in got.items() if rid not in (ids[0], ids[1])}           # production lost two records
    inv = tmp_path / "inventory.json"
    inv.write_text(json.dumps(_inventory(shelf.root, prod)))
    add_record(shelf, "codex", "c-0", [("human", "codex question"), ("assistant", "codex answer, newer")])   # older in production
    state = tmp_path / "state"
    state.mkdir()
    (state / "pending.json").write_text(json.dumps({"pending": {ids[1]: {"digest": "x"}},
                                                     "backlog_since": {ids[1]: "2026-10-17T08:00:00Z", ids[3]: "2026-10-17T09:30:00Z"}}))
    now = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
    r = R.reconcile(shelf.root, [], None, None, None, "2026", inventory=inv, sync_state=state, sync_status=tmp_path / "none.json", now=now)
    v = r["production"]["versions"]
    assert v["identical_set"] is False
    assert (v["missing_in_production"], v["older_in_production"], v["pending"], v["gaps"]) == (2, 1, 1, 2)
    assert v["gaps_by_provider_month"] == {"grok:2026-04": 1, "codex:2026-10": 1}
    assert all(set(g) == {"provider", "month", "key", "state"} and len(g["key"]) == 12 for g in r["production"]["gaps"])
    assert ids[0] not in json.dumps(r)
    assert r["sync"]["backlog_records"] == 2 and r["sync"]["oldest_pending"] == "2026-10-17T08:00:00Z"
    assert r["sync"]["oldest_pending_age_s"] == 7200


def test_last_sync_comes_from_the_vault_sync_status(tmp_path):
    from core.jobs import registry, status
    from core.memory_sync import versions as V
    shelf, _ = _real_shelf(tmp_path)
    got, _ = V.record_versions(shelf.root)
    inv = tmp_path / "inventory.json"
    inv.write_text(json.dumps(_inventory(shelf.root, got)))
    job = registry.parse({"schema_version": 1, "jobs": [{
        "id": "vault-sync", "name": "x", "host": "mac", "schedule": {"text": "every 2 h", "kind": "interval", "every_s": 7200},
        "expected_every_s": 7200, "missed_after_s": 21600, "stuck_after_s": 3600, "status_file": "vault-sync.json",
        "owner": "robert", "runbook": "x"}]}).jobs[0]
    t = datetime(2026, 10, 17, 9, tzinfo=timezone.utc)
    jobs = tmp_path / "jobs"
    run = status.start_run(jobs, job, "r1", t)
    status.finish_run(jobs, job, run, state="ok", now=t, exit_code=0, summary="backlog 0", results={"backlog_records": "0"}, next_due_by=None)
    r = R.reconcile(shelf.root, [], None, None, None, "2026", inventory=inv, sync_state=tmp_path / "none", sync_status=jobs / "vault-sync.json")
    assert r["sync"]["last_sync_at"] == status.iso(t) and r["sync"]["backlog_records_reported"] == "0"


def test_export_coverage_windows_are_accepted_limits_not_gaps(tmp_path, monkeypatch):
    shelf, codex, index = setup(tmp_path)
    items = [("grok:old-export-only", "2026-10-03", None, None), ("grok:not-captured", "2026-09-01", None, None)]
    monkeypatch.setattr(R, "export_items", lambda path: iter(items))
    r = R.reconcile(shelf, ["export.zip"], None, None, None, "2026")
    assert r["coverage"]["grok"]["export_through"] == "2026-10-03" and r["coverage"]["grok"]["accepted_limit"] is True
    assert r["sources"]["grok"]["states"] == {"on_shelf": 1, "not_seen": 1}
