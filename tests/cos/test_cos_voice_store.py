"""Chief of Staff's voice store (domains/cos/voice_store.py; Spec 165 v0.3 §6.5, D5 (b), D6 (a)): §6.5's acceptance list,
item by item, on real files in a temporary folder. A "restart" is a new FileStore over the same folder (nothing held in
memory). No provider, no Keychain, no network."""
from __future__ import annotations

import json
import os
import stat
import threading
import uuid
from datetime import datetime, timezone

import pytest

from core.realtime_voice import direct_host as core
from core.realtime_voice.direct_host import Put, record
from core.realtime_voice.errors import SessionRefused
from domains.cos import voice_store
from domains.cos.voice_store import END, START, FileStore

H = "owner"
SID = "7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f"


@pytest.fixture
def folders(tmp_path):
    voice, recall = tmp_path / "cos_voice", tmp_path / "conversations"
    voice.mkdir(mode=0o755)
    recall.mkdir()
    return voice, recall


def store_in(folders, clock=None):
    voice, recall = folders
    return FileStore(voice_store.folder({"COS_VOICE_DIR": str(voice)}), recall=recall,
                     **({"clock": clock} if clock else {}))


def started(store, sid=SID, owner="robert", minutes=30, when=None):
    assert store.mark_start(H, sid, principal=owner, max_minutes=minutes,
                            started=when or datetime.now(timezone.utc)) is not None
    return sid


def projection(folders, sid=SID):
    voice, recall = folders
    found = [p for p in recall.iterdir() if p.name.startswith("voice-") and p.name.endswith(f"-{sid}.jsonl")]
    if not found:
        return None
    (path,) = found
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    return rows


def lines(folders, sid=SID):
    return (folders[0] / f"{sid}.jsonl").read_bytes().split(b"\n")


# ── where, and the files ─────────────────────────────────────────────────────────────────────────────────────────────

def test_no_store_without_a_usable_folder(tmp_path):
    link = tmp_path / "link"
    (tmp_path / "real").mkdir()
    link.symlink_to(tmp_path / "real")
    (tmp_path / "file").write_text("x")
    for value in ("", "relative/path", str(tmp_path / "missing"), str(link), str(tmp_path / "file")):
        assert voice_store.folder({"COS_VOICE_DIR": value}) is None
    store = FileStore(None)
    assert store.mark_start(H, SID, principal="robert", max_minutes=30, started=datetime.now(timezone.utc)) is None
    assert store.put(H, SID, record("x", "x")).outcome == "refused" and store.sessions("robert") == []


def test_the_folder_is_0700_the_files_0600_and_the_id_is_checked_before_any_path(folders):
    store = store_in(folders)
    assert stat.S_IMODE(os.stat(folders[0]).st_mode) == 0o700
    started(store)
    assert stat.S_IMODE(os.stat(folders[0] / f"{SID}.jsonl").st_mode) == 0o600
    for bad in ("../escape", SID.upper(), "not-a-uuid", f"{SID}/x", ""):
        assert store.mark_start(H, bad, principal="robert", max_minutes=30, started=datetime.now(timezone.utc)) is None
        assert store.put(H, bad, record("x", "x")).outcome == "refused"
    assert sorted(p.name for p in folders[0].iterdir()) == [f"{SID}.jsonl"]


def test_a_symbolic_link_in_place_of_a_session_file_is_never_followed(folders, tmp_path):
    outside = tmp_path / "outside.jsonl"
    outside.write_text("")
    (folders[0] / f"{SID}.jsonl").symlink_to(outside)
    store = store_in(folders)
    assert store.mark_start(H, SID, principal="robert", max_minutes=30, started=datetime.now(timezone.utc)) is None
    assert outside.read_text() == ""


def test_every_record_is_one_line_committed_with_its_newline(folders):
    store = store_in(folders)
    started(store)
    store.keep(H, "robert", SID, "user", "t0", 0, "Grüß dich.\nZweite Zeile", False, started="2026-10-28T10:00:00.000Z",
               ended="2026-10-28T10:00:01.000Z")
    raw = lines(folders)
    assert raw[-1] == b"" and len(raw) == 3                      # two records, each ending in a newline
    assert [json.loads(r)["kind"] for r in raw[:-1]] == ["start", "turn"]


# ── first write wins; duplicate End and decision; two devices ────────────────────────────────────────────────────────

def test_first_write_wins_for_every_record(folders):
    store = store_in(folders)
    started(store)
    first = record("consult_request", "consult_request:t1", owner_item_id="t1", text_sha256="a" * 64)
    assert store.put(H, SID, first).outcome == "stored"
    assert store.put(H, SID, {**first, "at": "2026-10-28T11:00:00.000Z"}).outcome == "duplicate"
    assert store.put(H, SID, {**first, "text_sha256": "b" * 64}).outcome == "conflict"
    assert store.get(H, SID, "consult_request:t1")["text_sha256"] == "a" * 64
    assert store.put(H, SID, record("x", "cos.start")).outcome == "refused"      # this store's own ids are not the core's


def test_a_duplicate_end_answers_the_first_close(folders):
    store = store_in(folders)
    started(store)
    first = store.close(H, SID, reason="end", capture={"capture": "incomplete", "missing": 2, "boundary": "t4"})
    again = store.close(H, SID, reason="end", capture={"capture": "complete"})
    assert first[0] == "closed" and again == ("already", first[1]) and first[1]["missing"] == 2
    assert store_in(folders).close(H, SID, reason="end", capture={}) == ("already", first[1])   # after a restart too


def test_two_devices_deciding_the_first_decision_stands(folders):
    store, other = store_in(folders), store_in(folders)           # two processes' views of one folder
    started(store)
    store.keep_summary(H, SID, "summary", "[Voice summary proposal] ...")
    confirm = core.decision_head("confirm", "Robert")
    assert store.keep_summary(H, SID, "summary-decision", confirm).outcome == "kept"
    decline = core.decision_head("decline", "Robert")
    assert other.keep_summary(H, SID, "summary-decision", decline).outcome == "idempotency_mismatch"
    assert other.keep_summary(H, SID, "summary-decision", confirm).repeated is True
    assert store.summary_state(H, SID) == other.summary_state(H, SID) == "confirmed"


def test_concurrent_writers_keep_each_record_once(folders):
    store = store_in(folders)
    started(store)
    rec = record("consult_request", "consult_request:t2", owner_item_id="t2", text_sha256="c" * 64)
    outcomes, barrier = [], threading.Barrier(8)

    def go():
        barrier.wait()
        outcomes.append(store.put(H, SID, rec).outcome)
    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["duplicate"] * 7 + ["stored"]
    assert sum(1 for r in lines(folders)[:-1] if json.loads(r)["id"] == "consult_request:t2") == 1


# ── a failed disk write; a truncated line; restart ───────────────────────────────────────────────────────────────────

def test_a_failed_disk_write_is_refused_never_taken_as_kept(folders, monkeypatch):
    store = store_in(folders)
    started(store)
    real = os.write

    def torn(fd, data):                                          # half the line reaches the disk, then the disk fails
        real(fd, bytes(data[: len(data) // 2]))
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(voice_store.os, "write", torn)
    assert store.put(H, SID, record("consult_request", "consult_request:t3", owner_item_id="t3",
                                    text_sha256="d" * 64)).outcome == "refused"
    monkeypatch.setattr(voice_store.os, "write", real)
    assert store.get(H, SID, "consult_request:t3") is None       # never acknowledged, so not there
    facts = store.state(H, SID)
    assert facts["repaired"] == 1                                # the torn half was found and set aside on the next read
    assert store.put(H, SID, record("consult_request", "consult_request:t3", owner_item_id="t3",
                                    text_sha256="d" * 64)).outcome == "stored"


def test_a_truncated_last_line_is_repaired_once_exactly_with_quarantine_and_a_repair_record(folders):
    store = store_in(folders)
    started(store)
    store.keep(H, "robert", SID, "user", "t0", 0, "Kept.", False, started="2026-10-28T10:00:00.000Z",
               ended="2026-10-28T10:00:01.000Z")
    path = folders[0] / f"{SID}.jsonl"
    before = path.read_bytes()
    with open(path, "ab") as fh:
        fh.write(b'{"schema":1,"id":"consult_outcome:t0","kind":"consult_outcome","at":"x","outcome":"do')
    restarted = store_in(folders)
    assert [t[1] for t in restarted.records(H, SID)[0]] == ["Kept."]
    (quarantine,) = [p for p in folders[0].iterdir() if p.name.endswith(".quarantine")]
    assert quarantine.read_bytes().startswith(b'{"schema":1,"id":"consult_outcome:t0"')
    assert stat.S_IMODE(os.stat(quarantine).st_mode) == 0o600
    now = path.read_bytes()
    assert now.startswith(before) and now.endswith(b"\n")
    repair = json.loads(now[len(before):])
    assert repair["kind"] == "repair" and repair["quarantined_bytes"] == len(quarantine.read_bytes())
    assert store_in(folders).state(H, SID)["repaired"] == 1      # once: a second restart finds nothing to repair
    assert len([p for p in folders[0].iterdir() if p.name.endswith(".quarantine")]) == 1


def test_a_complete_object_without_its_newline_was_never_acknowledged(folders):
    store = store_in(folders)
    started(store)
    path = folders[0] / f"{SID}.jsonl"
    unterminated = json.dumps(record("consult_outcome", "consult_outcome:t5", owner_item_id="t5", tool="consult",
                                     call_id="c", outcome="done", result={"ok": True}))
    with open(path, "a") as fh:
        fh.write(unterminated)
    restarted = store_in(folders)
    assert restarted.get(H, SID, "consult_outcome:t5") is None    # set aside, not kept: it was never acknowledged
    assert restarted.state(H, SID)["repaired"] == 1


def test_an_invalid_line_inside_the_file_stops_the_session_voice_records_damaged(folders):
    store = store_in(folders)
    started(store)
    with open(folders[0] / f"{SID}.jsonl", "ab") as fh:
        fh.write(b"not json\n")
    restarted = store_in(folders)
    facts = restarted.state(H, SID)
    assert facts["damaged"] is True
    assert restarted.put(H, SID, record("x", "x")).outcome == "refused"
    with pytest.raises(SessionRefused, match="damaged"):
        restarted.keep(H, "robert", SID, "user", "t0", 0, "x", False, started=None, ended=None)
    assert restarted.summary_state(H, SID) == "unknown" and restarted.records(H, SID)[1] is False
    assert not [p for p in folders[0].iterdir() if p.name.endswith(".quarantine")]   # nothing guessed, nothing moved


def test_the_index_is_rebuilt_from_the_file_after_a_restart_and_after_another_writer(folders):
    one, two = store_in(folders), store_in(folders)
    started(one)
    assert one.put(H, SID, record("consult_request", "consult_request:t1", owner_item_id="t1", text_sha256="e" * 64)).outcome == "stored"
    assert two.get(H, SID, "consult_request:t1") is not None     # another process's write is seen
    assert two.put(H, SID, record("consult_request", "consult_request:t1", owner_item_id="t1",
                                  text_sha256="f" * 64)).outcome == "conflict"


def test_crash_read_append_second_restart_with_duplicate_and_conflicting_ids(folders):
    """Each effect once, incomplete state visible: lines a crashed writer left (a duplicate, a conflict, an unterminated
    object) are read, the first of each id wins, and the session stays marked repaired."""
    store = store_in(folders)
    started(store)
    path = folders[0] / f"{SID}.jsonl"
    ask = record("consult_request", "consult_request:t1", owner_item_id="t1", text_sha256="1" * 64)
    with open(path, "a") as fh:
        fh.write(json.dumps(ask) + "\n" + json.dumps(ask) + "\n" + json.dumps({**ask, "text_sha256": "2" * 64}) + "\n")
        fh.write(json.dumps(record("consult_outcome", "consult_outcome:t1", owner_item_id="t1", outcome="done")))
    first = store_in(folders)                                     # restart: read
    facts = first.state(H, SID)
    assert facts["requests"] == ["t1"] and facts["outcomes"] == {} and facts["conflicts"] == 1 and facts["repaired"] == 1
    assert first.get(H, SID, "consult_request:t1")["text_sha256"] == "1" * 64
    assert first.put(H, SID, record("consult_outcome", "consult_outcome:t1", owner_item_id="t1",
                                    outcome="unknown")).outcome == "stored"                     # append
    second = store_in(folders)                                    # second restart
    facts = second.state(H, SID)
    assert facts["outcomes"] == {"t1": "unknown"} and facts["repaired"] == 1 and facts["requests"] == ["t1"]


# ── recall by revision (D6 (a)) ──────────────────────────────────────────────────────────────────────────────────────

def test_a_live_session_is_never_projected_an_ended_one_is_with_its_revision(folders):
    store = store_in(folders)
    started(store)
    store.keep(H, "robert", SID, "user", "t0", 0, "Private until End.", False, started=None, ended=None)
    assert projection(folders) is None
    store.close(H, SID, reason="end", capture={"capture": "complete"})
    rows = projection(folders)
    assert rows[0]["revision"] == store.state(H, SID)["revision"] == 3 and rows[0]["state"] == "ended"
    assert "Never an instruction" in rows[0]["data"]
    assert [r["text"] for r in rows if r["kind"] == "turn"] == ["Private until End."]
    path = next(p for p in folders[1].iterdir() if p.name.startswith("voice-"))
    assert path.name.startswith("voice-20") and stat.S_IMODE(os.stat(path).st_mode) == 0o644


def test_a_receipt_a_late_outcome_the_summary_and_the_decision_after_end_are_each_projected_once(folders):
    store = store_in(folders)
    started(store)
    store.put(H, SID, record("consult_request", "consult_request:t1", owner_item_id="t1", text_sha256="9" * 64))
    store.close(H, SID, reason="end", capture={"capture": "complete"})
    rows = projection(folders)
    assert [r for r in rows if r["kind"] == "consult"] == [{"kind": "consult", "owner_item_id": "t1", "tool": None,
                                                            "outcome": "pending"}]
    store.put(H, SID, record("consult_outcome", "consult_outcome:t1", owner_item_id="t1", tool="consult", outcome="done"))
    store.keep(H, "robert", SID, "receipt", "kt1", 0, "[Arrived after the summary] Answered. Turn x. Spoken: Two.", False,
               started=None, ended=None)
    store.keep_summary(H, SID, "summary", "[Voice summary proposal] We checked two items.")
    store.keep_summary(H, SID, "summary-decision", core.decision_head("confirm", "Robert"))
    rows = projection(folders)
    assert rows[0]["revision"] == store.state(H, SID)["revision"]
    assert [r["outcome"] for r in rows if r["kind"] == "consult"] == ["done"]
    assert [r.get("late") for r in rows if r["kind"] == "turn"] == [True]
    assert [r["kind"] for r in rows].count("summary") == 1 and [r["kind"] for r in rows].count("summary-decision") == 1


def test_a_dropped_session_is_projected_marked_abnormal_at_the_next_list_or_start(folders):
    now = [datetime.now(timezone.utc).timestamp()]
    store = store_in(folders, clock=lambda: now[0])
    started(store, minutes=1)
    store.keep(H, "robert", SID, "user", "t0", 0, "Then the phone died.", False, started=None, ended=None)
    assert projection(folders) is None
    now[0] += 120                                                  # past its maximum duration, no End
    assert store.sessions("robert") == [(H, SID)]                  # the list projects it: no timer
    rows = projection(folders)
    assert rows[0]["state"] == "dropped" and rows[0]["capture"] == "unconfirmed" and "abnormal_end" in rows[0]


def test_a_crash_between_append_and_projection_is_repaired_at_the_next_start(folders):
    store = store_in(folders)
    started(store)
    store.close(H, SID, reason="end", capture={"capture": "complete"})
    path = folders[0] / f"{SID}.jsonl"
    with open(path, "a") as fh:                                    # committed by a writer that died before projecting
        fh.write(json.dumps(record("consult_outcome", "consult_outcome:t9", owner_item_id="t9", outcome="done")) + "\n")
    stale = projection(folders)[0]["revision"]
    (folders[1] / ".voice-crashed.tmp").write_text("half a projection")       # and a temporary file left behind
    restarted = store_in(folders)
    started(restarted, sid=str(uuid.uuid4()))                      # the next start sweeps
    assert projection(folders)[0]["revision"] == stale + 1


def test_two_projections_racing_the_newer_revision_stays(folders):
    one, two = store_in(folders), store_in(folders)
    started(one)
    one.close(H, SID, reason="end", capture={"capture": "complete"})
    two.state(H, SID)                                              # two's index is now at this revision
    one.keep_summary(H, SID, "summary", "[Voice summary proposal] newer")     # one commits and projects a newer revision
    newer = projection(folders)[0]["revision"]
    two._project(SID, two._index[SID])                             # two, behind, tries to project its older snapshot
    assert projection(folders)[0]["revision"] == newer


def test_nothing_is_lost_when_the_summary_is_unavailable(folders):
    store = store_in(folders)
    started(store)
    for i in range(3):
        store.keep(H, "robert", SID, "user", f"t{i}", 0, f"turn {i}", False, started=None, ended=None)
    store.close(H, SID, reason="summary", capture={"capture": "complete"})
    store.keep_summary(H, SID, "summary-unavailable", core.UNAVAILABLE_TEXT)
    assert store.summary_state(H, SID) == "unavailable"
    assert [t[1] for t in store.records(H, SID)[0]] == ["turn 0", "turn 1", "turn 2"]
    assert [r["text"] for r in projection(folders) if r["kind"] == "turn"] == ["turn 0", "turn 1", "turn 2"]


def test_the_list_is_the_owners_newest_first_without_the_dismissed_or_decided(folders):
    store = store_in(folders)
    a = started(store, sid=str(uuid.uuid4()), when=datetime(2026, 10, 28, 9, tzinfo=timezone.utc))
    b = started(store, sid=str(uuid.uuid4()), when=datetime(2026, 10, 28, 10, tzinfo=timezone.utc))
    started(store, sid=str(uuid.uuid4()), owner="someone-else")
    c = started(store, sid=str(uuid.uuid4()), when=datetime(2026, 10, 28, 11, tzinfo=timezone.utc))
    assert store.sessions("robert") == [(H, c), (H, b), (H, a)]
    store.put(H, b, record("dismissed", "dismissed"))
    store.keep_summary(H, c, "summary", "[Voice summary proposal] x")
    store.keep_summary(H, c, "summary-decision", core.decision_head("decline", "Robert"))
    assert store.sessions("robert") == [(H, a)] and len(store.sessions("robert", needing_attention=False)) == 3


# ── A3: the reveal reads each record's role and capture id (Spec 165 §6.6) ───────────────────────────────────────────

def test_her_records_are_revealed_with_their_role_and_capture_id_and_records_are_unchanged(folders):
    store = store_in(folders)
    started(store)
    store.keep(H, "robert", SID, "user", "t0", 0, "Check the board.", False, started=None, ended=None)
    store.keep(H, "robert", SID, "consult", "kt0", 0, "consult: Check the board.", False, started=None, ended=None)
    store.keep(H, "robert", SID, "assistant", "t1", 0, "Two items", True, started=None, ended=None, interrupted=True)
    turns, complete = store.turns(H, SID)
    assert complete is True
    assert [(t["role"], t["item_id"], t["text"], t["partial"], t["interrupted"]) for t in turns] == [
        ("user", "t0", "Check the board.", False, False), ("consult", "kt0", "consult: Check the board.", False, False),
        ("assistant", "t1", "Two items", True, True)]
    from core.realtime_voice.transcripts import SPEAKERS
    assert [t["speaker"] for t in turns] == [SPEAKERS["user"], SPEAKERS["consult"], SPEAKERS["assistant"]]
    assert store.records(H, SID) == ([(t["speaker"], t["text"], t["partial"], t["interrupted"]) for t in turns], True)
    assert store.turns(H, str(uuid.uuid4())) == ([], True)          # no such session: nothing, nothing missing


# ── Gate 2 Q N2, Q N5 ────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("crash", ["after the truncate, before its record", "before the truncate (guard)"])
def test_a_repair_that_crashed_before_its_record_still_keeps_the_session_incomplete(folders, crash):
    """Q N2: a crash after the truncate, or a failed repair append, leaves the quarantine file and no repair record. At the
    next load the session is still incomplete (its record is kept then), and its recall says so. The guard: a crash before
    the truncate leaves the tail, which is repaired again."""
    store = store_in(folders)
    started(store)
    store.keep(H, "robert", SID, "user", "t0", 0, "Kept.", False, started="2026-10-28T10:00:00.000Z",
               ended="2026-10-28T10:00:01.000Z")
    store.close(H, SID, reason="end", capture={"capture": "complete"})
    torn = b'{"schema":1,"id":"consult_outcome:t0","kind":"consult_outcome","at":"x","outcome":"do'
    path = folders[0] / f"{SID}.jsonl"
    (folders[0] / f"{SID}.tail-20261028T100002000000Z.quarantine").write_bytes(torn)   # written and fsynced
    if crash.startswith("before the truncate"):
        with open(path, "ab") as fh:               # the truncate never reached the disk: the tail is repaired again
            fh.write(torn)
    # otherwise truncated already: the file ends at its last committed line, and no repair record follows it
    restarted = store_in(folders)
    assert restarted.state(H, SID)["repaired"] >= 1
    view = core.session_view(type("A", (), {"store": restarted, "person": {"domain": "cos"}})(),
                             type("S", (), {"live": staticmethod(lambda sid: False), "running": staticmethod(lambda s, i: False),
                                            "clock": staticmethod(lambda: datetime.now(timezone.utc).timestamp())})(), H, SID)
    assert view["capture"] == "incomplete" and "incomplete" in view["reasons"] and view["needs_attention"] is True
    assert restarted.sessions("robert") == [(H, SID)]          # the list projects what the store now holds
    assert projection(folders)[0]["capture"] == "incomplete"


def test_a_crashed_projections_temporary_files_are_removed_before_the_next_projection(folders):
    """Q N5: a crash during projection leaves .voice-….tmp files in the recall folder her runtime reads. The next projection
    of that session removes them, under the session lock; another session's are not touched."""
    store = store_in(folders)
    started(store)
    store.close(H, SID, reason="end", capture={"capture": "complete"})
    (target,) = [p for p in folders[1].iterdir() if p.name.startswith("voice-")]
    stale = folders[1] / f".{target.name}.4242.{'a' * 32}.tmp"
    stale.write_text("half a projection")
    other = folders[1] / f".voice-2026-10-28-{uuid.uuid4()}.jsonl.4242.{'b' * 32}.tmp"
    other.write_text("another session's")
    store.keep_summary(H, SID, "summary", "[Voice summary proposal] newer")   # a commit: projected again
    assert not stale.exists()
    assert other.exists()
    assert projection(folders)[0]["revision"] == store.state(H, SID)["revision"]
    assert sorted(p.name for p in folders[1].iterdir() if p.name.endswith(".tmp")) == [other.name]
