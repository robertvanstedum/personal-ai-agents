"""The export seam (v0.6 section 11; v0.7 Unit 4). Fixture only: nothing is registered in, or written to, a live shelf."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from workshop_journal.conftest import REPO, WORKSHOP, progress
from core.workshop_journal import vault_export as vx
from core.workshop_journal.journal import Journal

UTC = timezone.utc
SHELF_TREE = Path(os.environ.get("WORKSHOP_SHELF_TREE", os.path.expanduser("~/.worktrees/memory-fixes")))


def journal_with_clock(root, start):
    clock = {"now": start}
    return Journal(root, WORKSHOP, lock_timeout=0.3, clock=lambda: clock["now"]), clock


def say(j, clock, text, *, kind="progress", actor="claude-code", step=30, **over):
    clock["now"] += timedelta(seconds=step)
    payload = {"progress": {"action": "working"}, "request": {"action": "review", "expected_result": "Findings."},
               "result": None}[kind]
    env = {"actor": actor, "kind": kind, "item": "topic:scenario", "topic": "scenario", "text": text, "payload": payload,
           "recipients": ["codex"] if kind == "request" else [], **over}
    r = j.append(env)
    assert r.ok, r.to_json()
    return r


# ── day keys ───────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("utc,day", [
    ("2026-10-08T04:59:59Z", "2026-10-07"), ("2026-10-08T05:00:00Z", "2026-10-08"),          # CDT is UTC-5: local midnight is 05:00Z
    ("2026-11-01T04:59:59Z", "2026-10-31"), ("2026-11-01T05:00:00Z", "2026-11-01"),          # the day DST ends
    ("2026-11-01T06:30:00Z", "2026-11-01"), ("2026-11-01T07:30:00Z", "2026-11-01"),          # 01:30 CDT, then 01:30 CST: same local day
    ("2026-11-02T05:59:59Z", "2026-11-01"), ("2026-11-02T06:00:00Z", "2026-11-02"),          # that day is 25 hours long (CST is UTC-6)
    ("2026-03-08T05:59:59Z", "2026-03-07"), ("2026-03-08T06:00:00Z", "2026-03-08"),          # spring forward: CST until 08:00Z
    ("2026-03-09T04:59:59Z", "2026-03-08"), ("2026-03-09T05:00:00Z", "2026-03-09"),          # that day is 23 hours long
])
def test_G7_the_day_is_the_local_chicago_date_of_the_helpers_time(utc, day):
    assert vx.day_key(utc) == day


def test_G7_the_repeated_hour_at_the_end_of_daylight_saving_keeps_journal_order(root):
    j, clock = journal_with_clock(root, datetime(2026, 11, 1, 6, 29, 0, tzinfo=UTC))             # 01:29 CDT
    say(j, clock, "first 01:30", step=60)                                                         # 06:30Z = 01:30 CDT
    clock["now"] = datetime(2026, 11, 1, 7, 29, 0, tzinfo=UTC)                                    # 01:29 CST, an hour later on the wall clock
    say(j, clock, "second 01:30", step=60)                                                        # 07:30Z = 01:30 CST
    export = vx.day_export(j.read().events, "2026-11-01")
    assert [r["text"] for r in export.rows] == ["first 01:30", "second 01:30"] and [r["seq"] for r in export.rows] == [1, 2]
    wall = [datetime.fromisoformat(r["recorded_at"].replace("Z", "+00:00")).astimezone(vx.ZoneInfo(vx.TIMEZONE)).strftime("%H:%M") for r in export.rows]
    assert wall == ["01:30", "01:30"]                                                             # same wall time; sequence is what orders them


def test_the_day_uses_helper_time_not_the_callers(root):
    j, clock = journal_with_clock(root, datetime(2026, 10, 8, 3, 0, tzinfo=UTC))                  # 22:00 on the 7th, Chicago
    r = say(j, clock, "late evening", at="2026-10-09T12:00:00Z")                                   # the caller claims another day
    export = vx.day_export(j.read().events, "2026-10-07")
    assert [x["event_id"] for x in export.rows] == [r.event_id] and vx.day_export(j.read().events, "2026-10-09").rows == []


# ── what a day contains ──────────────────────────────────────────────────────────────────────────────────────────
def test_prefix_identity_is_the_hash_of_the_exact_canonical_bytes_up_to_a_sequence(root):
    j, clock = journal_with_clock(root, datetime(2026, 10, 8, 15, 0, tzinfo=UTC))
    for n in range(4):
        say(j, clock, f"event {n}")
    events = j.read().events
    whole, part = vx.day_export(events, "2026-10-08"), vx.day_export(events, "2026-10-08", through_seq=2)
    assert whole.through_seq == 4 and part.through_seq == 2 and whole.prefix.startswith(part.prefix) and whole.prefix != part.prefix
    import hashlib
    assert whole.prefix_sha256 == hashlib.sha256(whole.prefix).hexdigest()
    assert vx.day_export(events, "2026-10-08").prefix_sha256 == whole.prefix_sha256               # deterministic


def test_health_and_delivery_are_counted_not_exported_and_excluded_references_are_dropped_and_counted(root):
    j, clock = journal_with_clock(root, datetime(2026, 10, 8, 15, 0, tzinfo=UTC))
    say(j, clock, "kept")
    j.append({"actor": "host", "kind": "health", "item": "host:mac", "text": "ok", "payload": {"component": "disk", "status": "ok",
              "observed_at": "2026-10-08T15:01:00Z"}}, software=True)
    ref = {"type": "source", "id": "private-notes", "availability": "excluded"}
    say(j, clock, "cites something excluded", refs=[ref])
    export = vx.day_export(j.read().events, "2026-10-08")
    assert [r["text"] for r in export.rows] == ["kept", "cites something excluded"]
    assert export.excluded == {"kind:health": 1, "excluded_reference": 1}
    assert all(not r["refs"] for r in export.rows if r["text"].startswith("cites"))


def test_the_counts_only_listing_carries_no_narrative(root):
    j, clock = journal_with_clock(root, datetime(2026, 10, 8, 15, 0, tzinfo=UTC))
    say(j, clock, "a very specific confidential sentence")
    listing = vx.day_export(j.read().events, "2026-10-08").listing()
    assert set(listing) == {"day", "first_seq", "through_seq", "events", "prefix_bytes", "prefix_sha256", "artifacts", "excluded"}
    assert "confidential" not in json.dumps(listing) and listing["events"] == 1


def test_the_approval_fingerprint_binds_scope_and_privacy_but_not_the_parser_version():
    base = dict(root="/ws", workshop_id="workshop-neubau", stream="workshop-neubau.local")
    a = vx.approval_fingerprint(**base)
    assert a == vx.approval_fingerprint(**base) and len(a) == 64
    for change in ({"root": "/other"}, {"workshop_id": "other"}, {"stream": "x.local"}, {"selection_policy_id": "workshop-day-v2"},
                   {"never_copy": ("secrets",)}):
        assert vx.approval_fingerprint(**{**base, **change}) != a, change
    before = vx.NORMALIZER
    try:
        vx.NORMALIZER = before + 1                                                      # a format-only parser upgrade
        assert vx.approval_fingerprint(**base) == a
    finally:
        vx.NORMALIZER = before


def test_the_registration_the_shelf_needs_is_listed_not_applied():
    assert "Workshop" in vx.REGISTRATION["record.CHAIRS"] and vx.REGISTRATION["sessions.NORMALIZER_VERSION"] == {"workshop": 1}
    assert vx.source_key("workshop-neubau", "workshop-neubau.local", "2026-10-08") == "workshop:workshop-neubau:workshop-neubau.local:2026-10-08"


# ── cadence (G10) ──────────────────────────────────────────────────────────────────────────────────────────────────
def rows(*specs, day="2026-10-08"):
    """DayExport rows from (seq, kind, 'HH:MM:SS' UTC) triples, for the pure cadence rules."""
    out = vx.DayExport(day)
    for seq, kind, hms in specs:
        out.rows.append({"seq": seq, "kind": kind, "recorded_at": f"{day}T{hms}Z"})
    out.through_seq, out.first_seq, out.prefix_sha256 = specs[-1][0], specs[0][0], f"h{specs[-1][0]}"
    return out


def at(hms, day="2026-10-08"):
    return datetime.fromisoformat(f"{day}T{hms}+00:00")


def test_G10_a_material_event_waits_for_the_coalescing_window_then_exports():
    export = rows((1, "request", "15:00:00"))
    assert vx.decide(export, vx.DayState(), at("15:00:30")) == ("wait", "coalescing")
    assert vx.decide(export, vx.DayState(), at("15:01:00")) == ("export", "material_boundary")


def test_G10_receipts_and_progress_wait_for_the_fifteen_minute_reconciliation():
    export = rows((1, "progress", "15:00:00"), (2, "receipt", "15:00:10"))
    state = vx.DayState(0, "", 1, at("15:00:00"))
    assert vx.decide(export, state, at("15:10:00")) == ("wait", "reconciliation_not_due")
    assert vx.decide(export, state, at("15:15:00")) == ("export", "reconciliation")
    assert vx.decide(export, vx.DayState(), at("15:00:10")) == ("export", "reconciliation")      # the first export of a day is not delayed


def test_G10_an_unchanged_day_is_never_exported_again():
    export = rows((1, "request", "15:00:00"))
    assert vx.decide(export, vx.DayState(1, "h1", 1, at("15:05:00")), at("16:00:00")) == ("wait", "unchanged")


def test_G10_the_daily_edition_budget_defers_and_a_closed_day_still_finishes():
    export = rows((5, "request", "15:00:00"))
    full = vx.DayState(4, "h4", vx.MAX_EDITIONS_PER_DAY, at("14:00:00"))
    assert vx.decide(export, full, at("16:00:00")) == ("deferred", "export_deferred_budget")
    assert vx.decide(export, full, at("16:00:00"), closed=True) == ("export", "day_closed")


def test_G10_continuous_activity_cannot_starve_a_material_export():
    export = rows((1, "request", "15:00:00"), (2, "progress", "15:00:30"), (3, "progress", "15:01:00"), (4, "progress", "15:01:25"))
    assert vx.decide(export, vx.DayState(), at("15:01:30")) == ("export", "material_boundary")             # not "wait for quiet"


def synthetic_day(events: int, step_seconds: int = 30, material_every: int = 20, day: str = "2026-10-08"):
    """Journal-shaped rows without a journal, so the 24-hour fixture runs in seconds."""
    start = datetime(2026, 10, 8, 5, 0, 0, tzinfo=UTC)
    out = []
    for n in range(1, events + 1):
        stamp = (start + timedelta(seconds=step_seconds * n)).strftime("%Y-%m-%dT%H:%M:%SZ")
        out.append({"v": 2, "seq": n, "event_id": f"00000000-0000-4000-8000-{n:012d}", "recorded_at": stamp, "at": stamp,
                    "actor": "codex", "kind": "request" if n % material_every == 0 else "progress", "text": f"event {n} " + "x" * 150, "refs": []})
    return out


def test_G10_a_busy_day_stays_inside_the_edition_budget_and_the_growth_is_measured():
    """24 hours of one event a minute with a request every ten minutes, driven by the real decision rules and looked at every four
    minutes. Every request would earn its own edition (144 a day); the ceiling holds at 96 and the report says what was deferred."""
    all_rows = synthetic_day(1440, step_seconds=60, material_every=10)
    state, editions, total_bytes, deferred, looks = vx.DayState(), 0, 0, 0, 0
    for n in range(4, 1441, 4):
        now = datetime.fromisoformat(all_rows[n - 1]["recorded_at"].replace("Z", "+00:00"))
        export = vx.day_export(all_rows[:n], "2026-10-08")
        looks += 1
        action, reason = vx.decide(export, state, now)
        if action == "export":
            editions += 1
            total_bytes += len(export.prefix)
            state = vx.DayState(export.through_seq, export.prefix_sha256, editions, now)
        elif action == "deferred":
            deferred += 1
    final = len(vx.day_export(all_rows, "2026-10-08").prefix)
    print(f"24h fixture: 1440 events, {looks} looks, {editions} editions, {deferred} deferred looks, final prefix {final} bytes, "
          f"{total_bytes} bytes across editions ({total_bytes / final:.1f}x the final day)")
    assert 20 < editions <= vx.MAX_EDITIONS_PER_DAY and deferred > 0                                    # busy enough to hit the ceiling
    assert total_bytes < final * vx.MAX_EDITIONS_PER_DAY


def test_G10_a_quiet_day_exports_far_less_than_a_busy_one():
    rows_ = synthetic_day(2880, material_every=10_000)                                                  # progress only, no material boundary
    state, editions = vx.DayState(), 0
    for n in range(20, 2881, 20):
        now = datetime.fromisoformat(rows_[n - 1]["recorded_at"].replace("Z", "+00:00"))
        export = vx.day_export(rows_[:n], "2026-10-08")
        if vx.decide(export, state, now)[0] == "export":
            editions += 1
            state = vx.DayState(export.through_seq, export.prefix_sha256, editions, now)
    assert editions <= 24 * 4 + 1                                                                         # at most one per 15 minutes


# ── the real consumer, in a merged tree ───────────────────────────────────────────────────────────────────────────
@pytest.mark.skipif(not (SHELF_TREE / "core" / "memory_shelf").is_dir(), reason="the memory branch's shelf code is not checked out here")
def test_the_real_shelf_consumer_accepts_the_bundle_and_keeps_coordination_out_of_dialogue(tmp_path):
    merged = tmp_path / "merged"
    (merged / "core").mkdir(parents=True)
    for entry in (SHELF_TREE / "core").iterdir():
        if entry.name not in ("workshop_journal", "__pycache__"):
            (merged / "core" / entry.name).symlink_to(entry)
    (merged / "core" / "workshop_journal").symlink_to(REPO / "core" / "workshop_journal")
    (merged / "utils").symlink_to(SHELF_TREE / "utils")
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(merged), "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(tmp_path)}
    run = subprocess.run([sys.executable, str(REPO / "tests/workshop_journal/shelf_fixture_scenario.py")], cwd=merged, env=env,
                         capture_output=True, text=True, timeout=180)
    assert run.returncode == 0, run.stderr[-2000:]
    out = json.loads(run.stdout.strip().splitlines()[-1])
    # unknown chair and unknown provider fail the consumer until registration; nothing was loosened
    assert out["unregistered_bundle"].startswith("refused:") and out["gate_before_floor"] == "unknown_provider"
    assert out["gate_after_floor"] is None
    # stage -> drain -> record validation -> exact edition lookup
    assert out["stage"] == [True, None] and out["drain"][0][0] == "captured"
    rec = out["record"]
    assert (rec["chair"], rec["kind"], rec["tier"], rec["scope"], rec["edition"]) == ("Workshop", "session", "raw", "robert", 1)
    assert rec["source"] == "workshop:workshop-neubau:workshop-neubau.local:2026-10-08" and "class:handoff" in rec["tags"]
    assert out["edition_lookup"] == {"found": True, "number": 1, "hash_matches_record": True}
    assert out["turns"]["speakers"] == ["coordination"] and out["turns"]["who"] == ["claude-code", "codex"] and out["turns"]["seqs"] == ["1", "2"]
    # coordination is excluded from ordinary retrieval while permitted dialogue still indexes
    assert out["retrievable_workshop_turns"] == 0 and out["retrievable_dialogue_turns"] == 2
    assert out["listing"]["excluded"] == {"kind:health": 1}
    # Codex R9: the edition alone rebuilds the exact canonical prefix, including payload-only values
    assert out["restore"] == {"sha_matches_source_hash": True, "same_as_exported_prefix": True, "payload_only_value": "Findings.", "rows": 2,
                              "authority_and_item_kept": True, "retained_kind": "workshop-journal"}
    # a later prefix adds an immutable edition; a repeat is a no-op; an older prefix changes nothing
    assert out["second_drain"][0][0] == "edition-added" and out["repeat_drain"][0][0] == "unchanged"
    assert out["older_drain"][0][0] == "unchanged"
    assert out["after"]["edition"] == 2 and out["after"]["revision"] == 6 and out["after"]["editions_on_disk"] == 2
    assert out["first_edition_still_exact"] is True


# ── Codex R9: the structured journal survives the trip, and documents travel by an explicit package ──────────────
def test_R9_the_prefix_is_recoverable_from_the_edition_alone_and_a_damaged_edition_is_refused(root):
    j, clock = journal_with_clock(root, datetime(2026, 10, 8, 15, 0, tzinfo=UTC))
    say(j, clock, "ask", kind="request")
    say(j, clock, "reply", actor="codex")
    export = vx.day_export(j.read().events, "2026-10-08")
    try:
        bundle = vx.build_bundle(WORKSHOP, j.stream, export)
    except vx.ShelfUnavailable:
        pytest.skip("the shelf code is not importable in this tree; the merged-tree test covers it")
    restored = vx.restore_prefix(bundle.edition)
    assert restored.prefix == export.prefix and restored.sha256 == bundle.source_hash == export.prefix_sha256
    assert restored.rows[0]["payload"]["expected_result"] == "Findings." and restored.rows[0]["recipients"] == ["codex"]
    for damage in (lambda b: b.replace(b"Findings.", b"Different."), lambda b: b"", lambda b: b.split(b"\n")[0] + b"\n"):
        with pytest.raises(vx.NotRestorable):
            vx.restore_prefix(damage(bundle.edition))


def test_R9_documents_travel_by_a_verified_package_and_problems_are_listed_not_dropped(root):
    from core.workshop_journal import artifacts as art
    j, clock = journal_with_clock(root, datetime(2026, 10, 8, 15, 0, tzinfo=UTC))
    kept = art.prepare(b"# kept document\n")
    gone = art.prepare(b"# soon missing\n")
    bad = art.prepare(b"# soon damaged\n")
    for n, item in enumerate((kept, gone, bad)):
        clock["now"] += timedelta(seconds=30)
        assert j.append({"actor": "claude-code", "kind": "progress", "item": "topic:scenario", "topic": "scenario", "text": f"doc {n}",
                         "refs": [item.ref(f"doc-{n}")], "payload": {"action": "w"}}, artifacts=[item]).committed
    base = Path(root) / WORKSHOP / "artifacts" / "sha256"
    (base / gone.retained_sha256).unlink()
    (base / bad.retained_sha256).write_bytes(b"tampered")
    export = vx.day_export(j.read().events, "2026-10-08")
    package = vx.transfer_package(j, export)
    assert package["objects"] == {kept.retained_sha256: kept.data} and package["missing"] == [gone.retained_sha256]
    assert package["damaged"] == [bad.retained_sha256] and package["bytes"] == len(kept.data)
    with pytest.raises(vx.PackageTooLarge):
        vx.transfer_package(j, export, limit=5)


def hand_edition(rows: list[dict]) -> bytes:
    """An edition shaped like the shelf renderer's (header line, then one line per turn carrying the whole event), built without
    the shelf so the restore rules are tested in every tree."""
    prefix = b"".join(vx.strictjson.canonical_bytes(r) + b"\n" for r in rows)
    digest = hashlib.sha256(prefix).hexdigest()
    head = {"format": "minimoi-session-turns/1", "provider": "workshop", "source_sha256": digest, "manifest": {"prefix_sha256": digest}}
    lines = [json.dumps(head, sort_keys=True)] + [json.dumps({"ordinal": n, "speaker": "coordination", "attrs": {"event": r}}, sort_keys=True)
                                                  for n, r in enumerate(rows, 1)]
    return ("\n".join(lines) + "\n").encode()


def test_R9_restore_rebuilds_the_prefix_and_refuses_every_kind_of_damage_in_any_tree(root):
    j, clock = journal_with_clock(root, datetime(2026, 10, 8, 15, 0, tzinfo=UTC))
    say(j, clock, "ask", kind="request")
    say(j, clock, "reply", actor="codex")
    export = vx.day_export(j.read().events, "2026-10-08")
    edition = hand_edition(export.rows)
    restored = vx.restore_prefix(edition)
    assert restored.prefix == export.prefix and restored.rows[0]["payload"]["expected_result"] == "Findings."
    lines = edition.split(b"\n")
    for damage in (edition.replace(b"Findings.", b"Different."), b"", lines[0] + b"\n", b"\n".join(lines[:1] + lines[2:]),
                   edition + b"not json\n"):
        with pytest.raises(vx.NotRestorable):
            vx.restore_prefix(damage)
