"""Inbox (B7): claude.ai export zips, pasted transcripts, refusals, designation candidates, canary."""
import json
import os
import shutil
import zipfile
from collections import namedtuple
from datetime import datetime, timezone

import pytest

from core.memory_shelf import canary, codes, events as ev, inbox, ledger, record, review, weight

from .helpers import FAKE_KEY, make_shelf, process

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def conv(uuid, name, msgs, created="2026-09-01T10:00:00Z", updated="2026-09-01T11:00:00Z"):
    return {"uuid": uuid, "name": name, "created_at": created, "updated_at": updated, "account": {"uuid": "acct"},
            "chat_messages": [{"uuid": f"{uuid}-{i}", "sender": s, "text": t, "content": [{"type": "text", "text": t}],
                               "created_at": created, "attachments": [], "files": []} for i, (s, t) in enumerate(msgs)]}


def make_zip(folder, name, convs, member="conversations.json"):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(member, json.dumps(convs))
        zf.writestr("users.json", json.dumps([{"uuid": "acct", "email_address": "nobody@example.invalid"}]))
    return path


def setup(tmp_path):
    return make_shelf(tmp_path), tmp_path / "inbox"


def records(shelf):
    out = {}
    for entry in shelf.list_records(canary=False) + shelf.list_records(canary=True):
        meta, body = record.load(shelf.main_path(entry).read_text())
        out[meta["source"]] = (meta, body, shelf.main_path(entry))
    return out


def test_export_zip_one_record_per_conversation_speakers_from_sender(tmp_path):
    shelf, box = setup(tmp_path)
    make_zip(box, "export.zip", [conv("u-1", "Plan the week", [("human", "first"), ("assistant", "reply")]),
                                 conv("u-2", "Second chat", [("human", "other"), ("assistant", f"key {FAKE_KEY}")])])
    out = process(shelf, box, now=NOW)
    assert out == {"status": "ok", "counts": {codes.CAPTURED: 2}}
    recs = records(shelf)
    meta, body, path = recs["claude-ai:u-1"]
    assert meta["chair"] == "Claude" and meta["scope"] == "robert" and meta["tier"] == "raw"
    assert "<!-- turn 1 | human |" in body and "<!-- turn 2 | assistant |" in body
    assert "plan-the-week--" in path.name and meta["retained"]["member"] == "u-1"
    assert FAKE_KEY not in recs["claude-ai:u-2"][1] and recs["claude-ai:u-2"][0]["normalized"]["redacted_turns"] == 1
    assert not (box / "export.zip").exists() and (box / "_processed" / "2026-10-03" / "export.zip").is_file()


def test_a_later_export_adds_editions_never_duplicates(tmp_path):
    shelf, box = setup(tmp_path)
    make_zip(box, "w1.zip", [conv("u-1", "Chat", [("human", "q"), ("assistant", "a")]),
                             conv("u-2", "Other", [("human", "x"), ("assistant", "y")])])
    process(shelf, box, now=NOW)
    make_zip(box, "w2.zip", [conv("u-1", "Chat", [("human", "q"), ("assistant", "a"), ("human", "q2"), ("assistant", "a2")],
                                  updated="2026-09-08T00:00:00Z"),
                             conv("u-2", "Other", [("human", "x"), ("assistant", "y")], updated="2026-09-09T00:00:00Z")])
    out = process(shelf, box, now=NOW)
    assert out["counts"] == {codes.EDITION_ADDED: 1, codes.UNCHANGED: 1}       # updated_at alone is not a change
    assert len(shelf.list_records()) == 2
    meta, body, path = records(shelf)["claude-ai:u-1"]
    assert meta["edition"] == 2 and "q2" in body and meta["events"][-1]["kind"] == "edition-added"
    assert sorted(p.name[:2] for p in (path.parent / "editions").iterdir()) == ["1-", "2-"]


@pytest.mark.parametrize("bad", [
    {"not": "a list"},
    [{"uuid": "u", "chat_messages": [{"sender": "system", "text": "x"}]}],
    [{"uuid": "u", "chat_messages": [{"sender": "human"}]}],
    [{"chat_messages": []}],
    [conv("ok", "Fine", [("human", "a"), ("assistant", "b")]), {"uuid": "u2", "messages": []}],
])
def test_unknown_schema_is_refused_whole_and_visibly(tmp_path, bad):
    shelf, box = setup(tmp_path)
    make_zip(box, "export.zip", bad)
    out = process(shelf, box, now=NOW)
    assert out["counts"] == {codes.REFUSED: 1} and shelf.list_records() == [] and shelf.pending() == []
    rows = inbox.refused_listing(box)
    assert len(rows) == 1 and rows[0]["reason"] == codes.UNKNOWN_SCHEMA and set(rows[0]) == {"at", "name", "reason", "bytes", "sha256"}
    assert (box / "_refused" / "export.zip").is_file() and not (box / "export.zip").exists()   # moved, not deleted
    assert ledger.report(shelf)["sources"]["inbox"]["refused"] == {"unknown_schema": 1}


def test_other_unreadable_inputs_are_refused_with_codes(tmp_path, monkeypatch):
    shelf, box = setup(tmp_path)
    box.mkdir()
    (box / "notzip.zip").write_bytes(b"this is not a zip")
    (box / "nojson.zip").write_bytes(b"")
    (box / "weird.png").write_bytes(b"\x89PNG")
    (box / "empty.txt").write_text("")
    (box / "latin.txt").write_bytes("Human: caf\xe9\nClaude: ok".encode("latin-1"))
    make_zip(box, "noconv.zip", [], member="other.json")
    with zipfile.ZipFile(box / "slip.zip", "w") as zf:
        zf.writestr("../conversations.json", "[]")
    make_zip(box, "badjson.zip", [], member="conversations.json")
    with zipfile.ZipFile(box / "badjson.zip", "w") as zf:
        zf.writestr("conversations.json", "{{{")
    make_zip(box, "emptylist.zip", [])
    out = process(shelf, box, now=NOW)
    assert out["counts"] == {codes.REFUSED: 9}
    reasons = {r["name"]: r["reason"] for r in inbox.refused_listing(box)}
    assert reasons == {"notzip.zip": "unparseable", "nojson.zip": "empty", "weird.png": "unsupported_type",
                       "empty.txt": "empty", "latin.txt": "not_utf8", "noconv.zip": "unknown_schema",
                       "slip.zip": "unknown_schema", "badjson.zip": "unparseable", "emptylist.zip": "no_turns"}
    assert shelf.list_records() == []


def test_oversized_export_is_refused(tmp_path, monkeypatch):
    shelf, box = setup(tmp_path)
    monkeypatch.setattr(inbox, "MAX_EXPORT_BYTES", 50)
    make_zip(box, "big.zip", [conv("u", "x", [("human", "a" * 100), ("assistant", "b")])])
    process(shelf, box, now=NOW)
    assert inbox.refused_listing(box)[0]["reason"] == codes.TOO_LARGE


# ── pasted transcripts ────────────────────────────────────────────────────────

PASTE = """Some title line the user copied

Human: What is the plan for next week?
It has two lines.
Claude: Here is a plan.

- one
- two
Human: thanks
Claude: you're welcome
"""


def test_paste_turns_preamble_kept_and_heuristic_identity(tmp_path):
    shelf, box = setup(tmp_path)
    box.mkdir()
    (box / "weekly plan.txt").write_text(PASTE)
    out = process(shelf, box, now=NOW)
    assert out["counts"] == {codes.CAPTURED: 1}
    meta, body, _ = next(iter(records(shelf).values()))
    assert meta["chair"] == "Claude" and meta["normalized"]["identity"] == "heuristic"
    assert meta["source"].startswith("paste:") and meta["normalized"]["turns"] == 5
    assert "Some title line the user copied" in body and "<!-- turn 1 | system |" in body     # kept, unattributed
    assert "It has two lines." in body and "- two" in body
    assert (box / "_processed" / "2026-10-03" / "weekly plan.txt").is_file()


@pytest.mark.parametrize("text", ["just a pile of text with no speakers at all", "Human: only me\nstill me",
                                  "Claude: only the assistant", "   \n\n"])
def test_paste_without_clear_speakers_is_refused_never_guessed(tmp_path, text):
    shelf, box = setup(tmp_path)
    box.mkdir()
    (box / "p.txt").write_text(text)
    process(shelf, box, now=NOW)
    assert shelf.list_records() == [] and inbox.refused_listing(box)[0]["reason"] in {"unparseable", "empty", "no_turns"}


def test_pasted_marker_is_a_candidate_not_a_designation(tmp_path):
    shelf, box = setup(tmp_path)
    box.mkdir()
    (box / "p.txt").write_text("Human: file this\nClaude: ok\nHuman: and approved-direct please\nClaude: noted")
    process(shelf, box, now=NOW)
    meta, _, _ = next(iter(records(shelf).values()))
    assert meta["events"] == [] and meta["tier"] == "raw"
    items = review.items(shelf)
    assert [i["type"] for i in items] == ["designation-candidate"] and items[0]["detail"]["record"] == meta["id"]
    assert weight.derive(meta)["weight"] == "deliberation"


def test_export_human_marker_designates_but_assistant_marker_does_not(tmp_path):
    shelf, box = setup(tmp_path)
    make_zip(box, "e.zip", [conv("u-1", "Marked", [("human", "Please file this conversation"), ("assistant", "ok")]),
                            conv("u-2", "Assistant says it", [("human", "hello"), ("assistant", "file this")]),
                            conv("u-3", "Quoted", [("human", "> file this\nwhat does that mean"), ("assistant", "x")])])
    process(shelf, box, now=NOW)
    recs = records(shelf)
    assert [e["kind"] for e in recs["claude-ai:u-1"][0]["events"]] == ["designated-curated"]
    assert recs["claude-ai:u-2"][0]["events"] == [] and recs["claude-ai:u-3"][0]["events"] == []
    assert review.items(shelf) == []


def test_identical_paste_twice_is_one_record_and_a_different_paste_is_only_flagged(tmp_path):
    shelf, box = setup(tmp_path)
    box.mkdir()
    (box / "a.txt").write_text("Human: shared opening question\nClaude: answer one")
    process(shelf, box, now=NOW)
    (box / "a-again.txt").write_text("Human: shared opening question\nClaude: answer one")
    assert process(shelf, box, now=NOW)["counts"] == {codes.UNCHANGED: 1}
    assert len(shelf.list_records()) == 1
    (box / "a-edited.txt").write_text("Human: shared opening question\nClaude: answer one\nHuman: more\nClaude: more")
    assert process(shelf, box, now=NOW)["counts"] == {codes.CAPTURED: 1}
    assert len(shelf.list_records()) == 2                                       # never auto-merged
    flags = review.items(shelf)
    assert [f["type"] for f in flags] == ["possible-same-conversation"] and flags[0]["detail"]["reasons"] == ["first_turn"]


def test_export_matching_an_earlier_paste_is_only_a_possible_match(tmp_path):
    shelf, box = setup(tmp_path)
    box.mkdir()
    (box / "weekly.txt").write_text("Human: opening words\nClaude: answer")
    os.utime(box / "weekly.txt", (NOW.timestamp(), NOW.timestamp()))        # its date is the clock the test runs under
    process(shelf, box, now=NOW)
    make_zip(box, "e.zip", [conv("u-1", "weekly", [("human", "opening words"), ("assistant", "answer"), ("human", "q"), ("assistant", "a")],
                                 created=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))])
    process(shelf, box, now=NOW)
    assert len(shelf.list_records()) == 2
    detail = review.items(shelf)[0]["detail"]
    assert sorted(detail["reasons"]) == ["first_turn", "title_and_date"]


def test_private_and_never_copy_files_are_excluded_whole(tmp_path):
    shelf, box = setup(tmp_path)
    box.mkdir()
    (box / "diary.private.txt").write_text("Human: a\nClaude: b")
    (box / "marked.txt").write_text("[private]\nHuman: a\nClaude: b")
    (box / "skipme.txt").write_text("Human: a\nClaude: b")
    (box / "fine.txt").write_text("Human: a\nClaude: b")
    out = process(shelf, box, now=NOW, never_copy=("skipme.txt",))
    assert out["counts"] == {codes.EXCLUDED: 3, codes.CAPTURED: 1}
    assert {r["reason"] for r in inbox.refused_listing(box)} == {"private", "never_copy"}
    assert len(shelf.list_records()) == 1
    assert ledger.report(shelf)["sources"]["inbox"]["excluded"] == {"private": 2, "never_copy": 1}


def test_processed_files_are_moved_never_deleted_even_with_the_same_name(tmp_path):
    shelf, box = setup(tmp_path)
    box.mkdir()
    for i in range(2):
        (box / "same.txt").write_text(f"Human: q{i}\nClaude: a{i}")
        process(shelf, box, now=NOW)
    done = sorted(p.name for p in (box / "_processed" / "2026-10-03").iterdir())
    assert done == ["same.txt", "same.txt.1"] and list(box.glob("*.txt")) == []


def test_disk_low_leaves_inbox_files_where_they_are(tmp_path, monkeypatch):
    shelf, box = setup(tmp_path)
    box.mkdir()
    (box / "p.txt").write_text("Human: a\nClaude: b")
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(100 * 1024 ** 3, 99 * 1024 ** 3, 1024 ** 3))
    shelf.min_free_bytes = 5 * 1024 ** 3
    assert process(shelf, box, now=NOW)["status"] == codes.DISK_LOW
    assert (box / "p.txt").is_file() and not (box / "_processed").exists()


def test_a_file_still_being_copied_in_waits(tmp_path):
    shelf, box = setup(tmp_path)
    box.mkdir()
    path = box / "p.txt"
    path.write_text("Human: a\nClaude: b")
    fresh = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    assert process(shelf, box, now=fresh, settle_seconds=30)["counts"] == {codes.UNSTABLE: 1}
    assert path.is_file()


# ── canary ────────────────────────────────────────────────────────────────────

def test_canary_travels_inbox_to_shelf_and_stays_out_of_ordinary_views(tmp_path):
    shelf, box = setup(tmp_path)
    path = canary.emit(box, NOW)
    assert path.name == "canary-2026-10-03.canary.json" and canary.emit(box, NOW) == path
    out = process(shelf, box, now=NOW)
    assert out["counts"] == {codes.CAPTURED: 1}
    meta, body, _ = records(shelf)["canary:canary-2026-10-03"]
    assert meta["kind"] == "canary" and meta["scope"] == "robert" and meta["tier"] == "raw" and meta["events"] == []
    assert shelf.list_records() == [] and len(shelf.list_records(canary=True)) == 1
    assert ledger.report(shelf)["sources"] == {} and ledger.report(shelf, canary=True)["sources"]["inbox"]["captured"] == 1
    canary.emit(box, NOW)                                                  # same day again: same bytes, no new edition
    assert process(shelf, box, now=NOW)["counts"] == {codes.UNCHANGED: 1}
    assert review.items(shelf) == []


def test_a_tampered_canary_file_is_refused(tmp_path):
    shelf, box = setup(tmp_path)
    box.mkdir()
    doc = canary.document("2026-10-03")
    doc["turns"].append({"speaker": "human", "text": "smuggled content"})
    (box / "canary-2026-10-03.canary.json").write_text(json.dumps(doc))
    process(shelf, box, now=NOW)
    assert shelf.list_records(canary=True) == [] and inbox.refused_listing(box)[0]["reason"] == codes.UNKNOWN_SCHEMA


def test_canary_producer_only_touches_the_inbox(tmp_path):
    box = tmp_path / "inbox"
    canary.emit(box, NOW)
    assert [p.name for p in tmp_path.iterdir()] == ["inbox"] and len(list(box.iterdir())) == 1
