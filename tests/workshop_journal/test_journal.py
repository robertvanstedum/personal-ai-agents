"""Append, idempotency, reads, state and the v1/v2 dual reader (v0.6 section 6; acceptance rows J01, J02, J06)."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from conftest import REPO, WORKSHOP, envelope, new_id, progress, run_child
from core.workshop_journal import schema, strictjson
from core.workshop_journal.journal import Journal
from minimoi_portal.workshop.record import Workshop


def journal_path(root) -> Path:
    return Path(root) / WORKSHOP / "events.jsonl"


def tree(root) -> dict:
    out = {}
    for path in sorted(Path(root).rglob("*")):
        st = path.lstat()
        out[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns, stat.S_IMODE(st.st_mode))
    return out


# ── a first append ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_an_append_writes_one_canonical_private_line_and_derives_state(journal, root):
    result = journal.append(envelope())
    assert (result.ok, result.status, result.seq, result.committed, result.exit_code) == (True, "committed", 1, True, 0)
    assert result.evidence["durability"] in ("full", "degraded")
    lines = journal_path(root).read_bytes().splitlines()
    assert len(lines) == 1
    event = strictjson.loads(lines[0])
    assert schema.validate_persisted(event) == event
    assert event["seq"] == 1 and event["stream"] == "workshop-neubau.local" and event["origin"]["basis"] == "claimed"
    assert stat.S_IMODE(journal_path(root).stat().st_mode) == 0o600
    assert stat.S_IMODE((Path(root) / WORKSHOP).stat().st_mode) == 0o700
    state = json.loads((Path(root) / WORKSHOP / "state.json").read_text())
    assert state["watermark"]["last_seq"] == 1 and state["events"] == 1


def test_sequence_numbers_follow_the_file_not_the_clock(journal):
    for i in range(5):
        assert journal.append(progress(f"step {i}")).seq == i + 1
    ordered = [e["seq"] for e in journal.read().events]
    assert ordered == [1, 2, 3, 4, 5]


def test_software_kinds_are_not_accepted_through_the_general_door(journal):
    env = envelope(kind="recovery", recipients=[], actor="host", item="host:journal",
                   payload={"manifest_hash": "a" * 64, "affected_byte_offset": 0, "action": "completed_lf"})
    refused = journal.append(env)
    assert (refused.ok, refused.status, refused.exit_code) == (False, "invalid_input", 2)
    assert refused.reason == "kind:software_entry_point_only"
    assert not journal_path(journal.root).exists() or journal_path(journal.root).read_bytes() == b""


def test_invalid_input_never_touches_the_journal(journal, root):
    journal.append(envelope())
    before = journal_path(root).read_bytes()
    for bad in ({"kind": "launched"}, {"text": ""}, {"seq": 7}, {"surprise": 1}):
        result = journal.append(envelope(**bad))
        assert result.status == "invalid_input" and result.exit_code == 2 and not result.committed
    assert journal_path(root).read_bytes() == before


# ── J01: two processes, 100 events each ────────────────────────────────────────────────────────────────────────────
CHILD = """
import sys
from core.workshop_journal.journal import Journal
root, tag = sys.argv[1], sys.argv[2]
j = Journal(root, 'workshop-neubau', lock_timeout=60)
for i in range(100):
    r = j.append({'actor': 'codex', 'kind': 'progress', 'item': 'queue:146', 'text': f'{tag} {i}', 'payload': {'action': 'x'}})
    assert r.ok, r.to_json()
"""


def test_J01_two_processes_append_one_hundred_events_each_without_loss_or_interleave(root):
    import subprocess
    env = {**os.environ, "PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1"}
    procs = [subprocess.Popen([sys.executable, "-c", CHILD, root, tag], env=env, stderr=subprocess.PIPE, text=True) for tag in "AB"]
    for p in procs:
        _, err = p.communicate(timeout=180)
        assert p.returncode == 0, err
    j = Journal(root, WORKSHOP)
    read = j.read()
    assert read.status == "ok" and len(read.events) == 200
    assert [e["seq"] for e in read.events] == list(range(1, 201))
    assert len({e["event_id"] for e in read.events}) == 200
    assert {e["text"].split()[0] for e in read.events} == {"A", "B"}
    assert sorted(int(e["text"].split()[1]) for e in read.events if e["text"].startswith("A")) == list(range(100))
    assert j.verify()["ok"] is True


# ── J02: the same ID, across a restart, then a changed intent ──────────────────────────────────────────────────────
def test_J02_same_id_same_intent_is_one_event_across_restart_and_a_changed_intent_conflicts(root):
    eid = new_id()
    first = Journal(root, WORKSHOP).append(envelope(event_id=eid))
    assert first.status == "committed"
    before = journal_path(root).read_bytes()
    again = Journal(root, WORKSHOP).append(envelope(event_id=eid))                 # a fresh object: a restart
    assert (again.ok, again.status, again.seq, again.committed) == (True, "duplicate", 1, True)
    changed = Journal(root, WORKSHOP).append(envelope(event_id=eid, text="A different request entirely."))
    assert (changed.ok, changed.status, changed.exit_code, changed.committed) == (False, "id_conflict", 3, False)
    assert journal_path(root).read_bytes() == before                                # nothing was added or overwritten


def test_J02_partial_intent_retries_cannot_hide_a_changed_payload(journal, root):
    eid = new_id()
    assert journal.append(envelope(event_id=eid)).committed                          # the time was resolved once and kept
    same = journal.append(envelope(event_id=eid))                                    # omits ``at``: resolves from the receipt
    assert same.status == "duplicate"
    changed = journal.append(envelope(event_id=eid, payload={"action": "review", "expected_result": "Something else."}))
    assert changed.status == "id_conflict"
    other_time = journal.append(envelope(event_id=eid, at="2026-01-01T00:00:00Z"))   # supplying a different time is a change too
    assert other_time.status == "id_conflict"


def test_prepare_fixes_the_id_and_time_without_touching_the_journal(journal, root):
    prepared = journal.prepare(envelope())
    assert prepared.ok and prepared.status == "prepared" and not journal_path(root).exists()
    resubmit = prepared.evidence["envelope"]
    assert resubmit["event_id"] == prepared.event_id and resubmit["at"].endswith("Z")
    assert (Path(root) / WORKSHOP / "prepared" / f"{prepared.event_id}.json").is_file()
    committed = journal.append(resubmit)
    assert committed.status == "committed" and committed.event_id == prepared.event_id
    assert journal.read().events[0]["at"] == resubmit["at"]
    again = journal.prepare(resubmit)                                                # idempotent
    assert again.ok and again.event_id == prepared.event_id and again.evidence["intent_hash"] == prepared.evidence["intent_hash"]
    clash = journal.prepare({**resubmit, "text": "changed"})
    assert clash.status == "id_conflict"


# ── v1 and v2 together ─────────────────────────────────────────────────────────────────────────────────────────────
def v1(text="legacy health", kind="progress", item="queue:146"):
    return {"actor": "claude-code", "kind": kind, "item": item, "text": text}


def test_v2_sequence_starts_after_the_legacy_rows_and_legacy_bytes_never_change(root):
    ws = Workshop(root, WORKSHOP)
    ws.append(v1("one"))
    ws.append(v1("two"))
    legacy_bytes = journal_path(root).read_bytes()
    j = Journal(root, WORKSHOP)
    result = j.append(envelope())
    assert result.seq == 3
    assert journal_path(root).read_bytes().startswith(legacy_bytes)                  # v1 rows byte-identical
    read = j.read()
    assert read.status == "ok" and [e["v"] for e in read.events] == [1, 1, 2]
    assert [e["text"] for e in ws.events()][:2] == ["one", "two"]                     # the v1 API reads both versions
    with pytest.raises(OSError, match="legacy_after_v2"):
        ws.append(v1("three"))                                                        # no v1 line after a v2 row
    assert j.verify()["ok"] is True


def test_a_v1_stream_stays_a_v1_stream_through_the_new_writer(root):
    ws = Workshop(root, WORKSHOP)
    for i in range(3):
        ws.append(v1(f"n{i}"))
    j = Journal(root, WORKSHOP)
    assert j.verify()["legacy"] == 3 and j.verify()["v2"] == 0
    assert not (Path(root) / WORKSHOP / "prepared").exists()                         # legacy appends create no receipt folder


# ── J06: reads mutate nothing; state loses to the journal ──────────────────────────────────────────────────────────
def test_J06_reads_create_and_change_nothing(journal, root):
    journal.append(envelope())
    journal.append(progress())
    before = tree(root)
    journal.read(); journal.read(deep=True); journal.verify(); journal.state(); journal.repair()
    journal.get(journal.read().events[0]["event_id"])
    assert tree(root) == before


def test_J06_deleting_state_changes_nothing_but_the_source_of_the_answer(journal, root):
    journal.append(envelope(kind="needs_you", recipients=[], payload={"reason_code": "decision_needed",
                                                                       "requested_action": "Pick one.", "incident_id": new_id()}))
    state_file = Path(root) / WORKSHOP / "state.json"
    good, source = journal.state()
    assert source == "file" and len(good["needs_you"]) == 1
    state_file.unlink()
    rebuilt, source = journal.state()
    assert source == "rebuilt" and rebuilt["needs_you"] == good["needs_you"] and not state_file.exists()   # a read writes nothing


def test_state_json_always_loses_to_the_journal(journal, root):
    journal.append(envelope(kind="needs_you", recipients=[], payload={"reason_code": "decision_needed",
                                                                       "requested_action": "Pick one.", "incident_id": new_id()}))
    state_file = Path(root) / WORKSHOP / "state.json"
    forged = json.loads(state_file.read_text())
    forged["needs_you"] = []                                                          # a stale or forged "all clear"
    state_file.write_text(json.dumps(forged))
    state, source = journal.state()
    assert source == "rebuilt" and len(state["needs_you"]) == 1
    state_file.write_text("{ not json")
    assert journal.state() == (None, "unreadable")


def test_a_damaged_journal_refuses_instead_of_reporting_a_stale_as_of(journal, root):
    journal.append(progress("one"))
    journal.append(progress("two"))
    lines = journal_path(root).read_bytes().split(b"\n")
    journal_path(root).write_bytes(b"\n".join([lines[0], b"{ broken", *lines[1:]]))
    assert journal.state() == (None, "refused")


def test_a_missing_workshop_reports_missing_and_creates_nothing(tmp_path):
    root = str(tmp_path / "never-made")
    j = Journal(root, WORKSHOP)
    assert j.read().status == "missing" and j.get(new_id()).status == "missing" and j.state() == (None, "missing")
    assert j.verify()["status"] == "missing" and not os.path.exists(root)


def test_a_journal_without_a_lock_file_means_the_writer_protocol_is_unknown(journal, root):
    journal.append(envelope())
    (Path(root) / WORKSHOP / ".lock").unlink()
    assert journal.read().status == "unsupported_writer"
    assert not (Path(root) / WORKSHOP / ".lock").exists()                             # a read does not create it


def test_get_finds_an_event_by_id_and_reports_what_it_cannot(journal):
    done = journal.append(envelope())
    found = journal.get(done.event_id)
    assert found.status == "found" and found.evidence["event"]["seq"] == 1
    assert journal.get(new_id()).status == "not_found"
    assert journal.get("not-a-uuid").exit_code == 2


def test_a_state_that_could_not_be_refreshed_is_a_success_with_a_warning(journal, monkeypatch):
    monkeypatch.setattr(Journal, "_publish_state", lambda self, s: (_ for _ in ()).throw(OSError("disk")))
    result = journal.append(envelope())
    assert (result.ok, result.status, result.committed, result.exit_code) == (True, "committed_state_stale", True, 0)
    assert journal.get(result.event_id).status == "found"                             # the event is retrievable
    monkeypatch.undo()
    assert journal.append(progress()).status == "committed"
    assert journal.state()[1] == "file"                                               # the next append refreshed it


# ── Codex R17: an unusable-looking old line is damage for the authoritative journal; the old-screen adapter shows it as incomplete ──
def bad_legacy_lines(root):
    path = Path(root) / "mac" / "events.jsonl"
    for bad in ({"v": 1, "at": "2026-10-09T01:00:00+00:00", "actor": "codex", "kind": "needs_you", "text": "an owner question with no item"},
                {"v": 1, "at": 1700000000, "actor": "codex", "kind": "progress", "item": "queue:12", "text": "numeric time"},
                {"v": 1, "at": "2026-10-09T01:00:00+00:00", "actor": "codex", "item": "queue:12", "text": "no kind"}):
        with open(path, "a") as handle:
            handle.write(json.dumps(bad) + "\n")
    return path


def test_R17_a_malformed_legacy_owner_question_holds_writes_and_every_authoritative_view_says_so(root):
    from core.workshop_journal import brief as brief_view
    ws = Workshop(root, "mac")
    ws.append({"workshop": "mac", "actor": "codex", "kind": "started", "item": "queue:12", "text": "Building"})
    path = bad_legacy_lines(root)
    before = path.read_bytes()
    j = Journal(root, "mac")
    read = j.read()
    assert read.status == "corrupt" and read.scan.problems[0]["reason"] == "bad_legacy_row"
    refused = j.append(progress("a v2 event after the damage"))
    assert (refused.status, refused.committed) == ("corrupt", False)
    assert j.append_legacy({"v": 1, "at": "2026-10-09T02:00:00+00:00", "actor": "codex", "kind": "progress", "item": "queue:12", "text": "x"}).status == "corrupt"
    brief = j.brief()
    assert (brief["status"], brief["reason"]) == ("refused", "journal_damaged")
    hist = j.history()
    assert hist["status"] == "refused" and hist["events"] == []
    report = j.verify()
    assert report["ok"] is False and report["problems"][0]["reason"] == "bad_legacy_row"
    assert j.state() == (None, "refused")
    assert path.read_bytes() == before                                                                   # no silent repair, every byte kept


def test_R17_the_old_screens_adapter_still_shows_the_valid_records_but_labels_them_incomplete_with_the_count(root):
    from minimoi_portal.workshop.record import Workshop, load_state
    ws = Workshop(root, "mac")
    ws.append({"workshop": "mac", "actor": "codex", "kind": "started", "item": "queue:12", "text": "Building"})
    bad_legacy_lines(root)
    ws2 = Workshop(root, "mac")
    assert [e["text"] for e in ws2.events()] == ["Building"]
    state, status = load_state(root, "mac")
    assert state["items"]["queue:12"]["text"] == "Building"                                              # valid records are still shown
    assert state["incomplete"] == {"reason": "legacy_rows_skipped", "skipped": 3} and status in ("ok", "stale")
    assert Journal(root, "mac").read(tolerate_legacy=True).scan.skipped == 3
    # only the adapter is tolerant: the writer and the authoritative views are not
    with pytest.raises(OSError):
        ws2.append({"workshop": "mac", "actor": "codex", "kind": "progress", "item": "queue:12", "text": "after"})
    with open(Path(root) / "mac" / "events.jsonl", "a") as handle:
        handle.write("this is not json\n")
    assert load_state(root, "mac") == (None, "unreadable")                                               # real damage still blanks the adapter
