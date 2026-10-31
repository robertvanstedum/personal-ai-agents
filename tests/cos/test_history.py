"""B1a: the canonical CoS history (spec v0.3 section 4.19) and the robert_source verifier (section 4.2).

Each test names the guarantee it holds. Every record written is validated against
contracts/cos/v1/conversation.schema.json at runtime, so a change to that contract runs these tests too.
"""
from __future__ import annotations

import json
import stat

import pytest

from domains.cos import history as history_module
from domains.cos.history import (
    BEFORE_1_2,
    DEFAULT_CONVERSATION_ID,
    History,
    HistoryRefused,
    backend_session,
    turn_id_for,
    turn_ref,
)
from domains.cos.store import StoreWriter
from domains.cos.store import contracts
from domains.cos.store.canonical import digest

ENV = "dev"
WORDS = "Nag me on doing a german one everyday since I said I want to learn german."


class Clock:
    def __init__(self):
        self.minute = 0

    def __call__(self):
        self.minute += 1
        return f"2026-10-31T09:{self.minute:02d}:00+00:00"


@pytest.fixture
def hist(tmp_path):
    folder = tmp_path / "conversations"
    folder.mkdir()
    return History(folder, ENV, clock=Clock())


def send(cid, tid="t-0a1b2c3d4e5f6a", text=WORDS, channel="web"):
    return {"schema": "minimoi.cos.send/1", "conversation_id": cid, "turn_id": tid, "text": text, "channel": channel}


def lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


# ---- conversations and the first one ----------------------------------------------------------------------------

def test_the_first_conversation_keeps_the_owner_session_so_nothing_restarts(hist):
    doc = hist.ensure_default()
    assert doc["conversation_id"] == DEFAULT_CONVERSATION_ID and doc["title"] == "CoS" and doc["read_only"] is False
    assert backend_session(DEFAULT_CONVERSATION_ID) == "owner"
    assert hist.ensure_default() == doc                      # written once
    new = hist.create("German plan")
    assert backend_session(new["conversation_id"]) == new["conversation_id"]
    assert not contracts.errors("conversation", "conversation", new)


def test_conversations_list_newest_activity_first_and_paginates(hist):
    first = hist.ensure_default()["conversation_id"]
    second = hist.create("Second")["conversation_id"]
    hist.begin(send(first, "t-000000000001"))
    page = hist.conversations(limit=1)
    assert [c["conversation_id"] for c in page["items"]] == [first] and page["next"] == "1"
    rest = hist.conversations(limit=1, cursor=page["next"])
    assert [c["conversation_id"] for c in rest["items"]] == [second] and rest["next"] is None
    assert page["items"][0]["turn_count"] == 1


# ---- sends: durable, idempotent, honest -------------------------------------------------------------------------

def test_a_send_is_saved_durably_before_anything_else_and_validates(hist):
    cid = hist.ensure_default()["conversation_id"]
    turn, replayed = hist.begin(send(cid))
    assert replayed is False and turn["status"] == "pending" and turn["durable_ack_at"] and turn["seq"] == 1
    on_disk = lines(hist.root / "turns" / f"{cid}.jsonl")
    assert on_disk == [turn] and not contracts.errors("conversation", "turn", turn)


def test_a_repeated_send_returns_the_turn_and_a_changed_one_is_refused(hist):
    cid = hist.ensure_default()["conversation_id"]
    hist.begin(send(cid))
    again, replayed = hist.begin(send(cid))
    assert replayed is True and again["status"] == "pending"
    with pytest.raises(HistoryRefused) as exc:
        hist.begin(send(cid, text="something else"))
    assert exc.value.code == "turn_id_reused"
    other = hist.create("Other")["conversation_id"]
    with pytest.raises(HistoryRefused) as exc:
        hist.begin(send(other))
    assert exc.value.code == "turn_id_reused"
    assert len(lines(hist.root / "turns" / f"{cid}.jsonl")) == 1


def test_the_before_1_2_conversation_takes_no_new_turns(hist, tmp_path):
    hist.import_monthly_logs()
    with pytest.raises(HistoryRefused) as exc:
        hist.begin(send(BEFORE_1_2))
    assert exc.value.code == "read_only"


def test_an_unknown_conversation_or_bad_send_is_refused(hist):
    with pytest.raises(HistoryRefused) as exc:
        hist.begin(send("cosc-ffffffffffff"))
    assert exc.value.code == "conversation_not_found"
    with pytest.raises(HistoryRefused) as exc:
        hist.begin(send(DEFAULT_CONVERSATION_ID, tid="not-a-turn-id"))
    assert exc.value.code == "payload_invalid"


# ---- replies land on their own turn -----------------------------------------------------------------------------

def test_a_reply_lands_on_its_own_turn_in_its_own_conversation(hist):
    first = hist.ensure_default()["conversation_id"]
    second = hist.create("Second")["conversation_id"]
    hist.begin(send(first, "t-000000000001"))
    hist.begin(send(second, "t-000000000002", text="other words"))
    done = hist.answer(first, "t-000000000001", "Gern.")
    assert done["status"] == "answered" and done["reply_text"] == "Gern." and done["answered_at"]
    assert hist.turn(second, "t-000000000002")["status"] == "pending"
    with pytest.raises(HistoryRefused) as exc:
        hist.answer(second, "t-000000000001", "wrong place")
    assert exc.value.code == "turn_not_found"


def test_answering_twice_is_idempotent_and_a_different_answer_is_refused(hist):
    cid = hist.ensure_default()["conversation_id"]
    hist.begin(send(cid))
    hist.answer(cid, "t-0a1b2c3d4e5f6a", "Yes.")
    assert hist.answer(cid, "t-0a1b2c3d4e5f6a", "Yes.")["status"] == "answered"
    with pytest.raises(HistoryRefused):
        hist.answer(cid, "t-0a1b2c3d4e5f6a", "No.")
    assert len(lines(hist.root / "turns" / f"{cid}.jsonl")) == 2


def test_a_late_reply_to_a_failed_turn_is_recovered(hist):
    cid = hist.ensure_default()["conversation_id"]
    hist.begin(send(cid))
    failed = hist.fail(cid, "t-0a1b2c3d4e5f6a", "backend_timeout")
    assert failed["status"] == "failed" and failed["failure_code"] == "backend_timeout"
    late = hist.answer(cid, "t-0a1b2c3d4e5f6a", "Here it is after all.")
    assert late["status"] == "recovered" and late["reply_text"] == "Here it is after all."
    with pytest.raises(HistoryRefused):
        hist.fail(cid, "t-0a1b2c3d4e5f6a", "too_late")


def test_a_very_long_reply_is_cut_with_a_visible_marker_not_silently(hist):
    cid = hist.ensure_default()["conversation_id"]
    hist.begin(send(cid))
    turn = hist.answer(cid, "t-0a1b2c3d4e5f6a", "x" * 50_000)
    assert len(turn["reply_text"]) == history_module.MAX_REPLY_CHARS
    assert turn["reply_text"].endswith("[Cut here: the full reply was 50,000 characters.]")


# ---- reloads ----------------------------------------------------------------------------------------------------

def test_reload_pages_backwards_oldest_first(hist):
    cid = hist.ensure_default()["conversation_id"]
    for n in range(1, 6):
        hist.begin(send(cid, f"t-00000000000{n}", text=f"turn {n}"))
    page = hist.turns(cid, limit=2)
    assert [t["seq"] for t in page["items"]] == [4, 5] and page["next_before_seq"] == 4
    page = hist.turns(cid, limit=2, before_seq=page["next_before_seq"])
    assert [t["seq"] for t in page["items"]] == [2, 3]
    page = hist.turns(cid, limit=2, before_seq=page["next_before_seq"])
    assert [t["seq"] for t in page["items"]] == [1] and page["next_before_seq"] is None


# ---- recovery ---------------------------------------------------------------------------------------------------

def test_a_restart_marks_orphaned_turns_interrupted_and_quarantines_a_torn_tail(tmp_path):
    folder = tmp_path / "conversations"
    folder.mkdir()
    first = History(folder, ENV, clock=Clock())
    cid = first.ensure_default()["conversation_id"]
    first.begin(send(cid))
    path = first.root / "turns" / f"{cid}.jsonl"
    with open(path, "ab") as handle:
        handle.write(b'{"torn": ')
    restarted = History(folder, ENV, clock=Clock())
    assert restarted.recover() == {"quarantined": 1, "interrupted": 1}
    turn = restarted.turn(cid, "t-0a1b2c3d4e5f6a")
    assert turn["status"] == "failed" and turn["failure_code"] == "interrupted"
    assert list(restarted.root.glob("quarantine-*.bin"))
    assert restarted.answer(cid, "t-0a1b2c3d4e5f6a", "Sorry, here.")["status"] == "recovered"


def test_files_and_folders_are_readable_by_the_group_and_writable_only_by_the_backend(hist):
    cid = hist.ensure_default()["conversation_id"]
    hist.begin(send(cid))
    for path in [hist.root / "turns" / f"{cid}.jsonl", hist.root / "conversations" / f"{cid}.json"]:
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
    for folder in [hist.root, hist.root / "turns"]:
        assert stat.S_IMODE(folder.stat().st_mode) == 0o750


# ---- notes and Private ------------------------------------------------------------------------------------------

def note(cid, note_id="briefing:2026-10-31:1", text="Your morning briefing."):
    return {"schema": "minimoi.cos.stream_note/1", "note_id": note_id, "conversation_id": cid, "kind": "briefing",
            "created_at": "2026-10-31T07:00:00-05:00", "text": text}


def test_a_note_is_written_once_and_never_into_a_private_conversation(hist):
    cid = hist.ensure_default()["conversation_id"]
    assert hist.add_note(note(cid)) == (note(cid), False)
    assert hist.add_note(note(cid)) == (note(cid), True)
    with pytest.raises(HistoryRefused) as exc:
        hist.add_note(note(cid, text="changed"))
    assert exc.value.code == "note_id_reused"
    private = hist.create("Just thinking", private=True)["conversation_id"]
    with pytest.raises(HistoryRefused) as exc:
        hist.add_note(note(private))
    assert exc.value.code == "private_conversation"
    assert hist.turns(cid)["notes"] == [note(cid)]


# ---- selection --------------------------------------------------------------------------------------------------

def test_the_selected_conversation_is_held_per_owner_and_device(hist):
    default = hist.ensure_default()["conversation_id"]
    other = hist.create("Other")["conversation_id"]
    assert hist.selected(1, "phone") == default
    hist.select(1, "phone", other)
    assert hist.selected(1, "phone") == other
    assert hist.selected(1, "laptop") == default
    assert hist.selected(2, "phone") == default
    with pytest.raises(HistoryRefused) as exc:
        hist.select(None, "phone", other)
    assert exc.value.code == "identity_required"


# ---- the robert_source verifier ---------------------------------------------------------------------------------

def test_robert_source_verifies_only_a_verbatim_span_of_his_own_turn(hist):
    cid = hist.ensure_default()["conversation_id"]
    hist.begin(send(cid))
    ref = turn_ref(cid, "t-0a1b2c3d4e5f6a")
    assert hist.verify_robert_source(ref, WORDS)
    assert hist.verify_robert_source(ref, "I want to learn german")
    assert not hist.verify_robert_source(ref, "I want to learn German")          # not verbatim
    assert not hist.verify_robert_source(ref, "   ")
    assert not hist.verify_robert_source(turn_ref(cid, "t-ffffffffffff"), WORDS)
    assert not hist.verify_robert_source({"type": "mc.turn", "id": ref["id"]}, WORDS)


def test_the_store_accepts_robert_source_only_through_the_history_verifier(hist, tmp_path):
    cid = hist.ensure_default()["conversation_id"]
    hist.begin(send(cid))
    ref = turn_ref(cid, "t-0a1b2c3d4e5f6a")
    store = StoreWriter(tmp_path / "store", ENV, verify_robert_source=hist.verify_robert_source)

    def goal_command(text, command_id):
        payload = {"schema": "minimoi.cos.goal/1", "data_scope": "personal", "environment": ENV,
                   "id": "GOAL-german-b1", "revision": 0, "title": "German to B1",
                   "owner_words": [{"text": text, "provenance": "robert_source", "turn_ref": ref}],
                   "domains": ["german"], "plan_ref": None, "state": "active",
                   "created_at": "2026-10-31T09:00:00-05:00"}
        return {"schema": "minimoi.cos.store_command/1", "command_id": command_id, "kind": "goal.put",
                "target": {"type": "cos.goal", "id": "GOAL-german-b1"}, "expected_revision": None, "actor": "cos",
                "seat": "cos", "origin": "owner_turn", "basis_ref": ref, "payload": payload,
                "payload_digest": digest(payload), "data_scope": "personal", "environment": ENV}

    refused = store.submit(goal_command("I want to master German", "cmd-paraphrase"))
    assert refused["outcome"] == "refused" and refused["code"] == "robert_source_unverified"
    assert store.submit(goal_command("I want to learn german", "cmd-verbatim"))["outcome"] == "accepted"


# ---- the legacy import ------------------------------------------------------------------------------------------

def write_monthly(folder, name, rows):
    (folder / name).write_text("".join(json.dumps(r) + "\n" for r in rows))


def legacy(at, robert, cos="Reply.", turn_id="11111111-2222-4333-8444-555555555555", **extra):
    return {"at": at, "conversation_id": "owner", "turn_id": turn_id, "channel": "html_text", "robert": robert,
            "attachments": [], "cos": cos, "downloads": [], **extra}


def test_the_monthly_logs_import_once_as_the_read_only_before_1_2_conversation(hist):
    write_monthly(hist.base, "2026-09.jsonl", [legacy("2026-09-30T10:00:00+00:00", "September words",
                                                      turn_id="aaaaaaaa-0000-4000-8000-000000000001")])
    write_monthly(hist.base, "2026-10.jsonl", [
        legacy("2026-10-01T10:00:00+00:00", "October words", turn_id="aaaaaaaa-0000-4000-8000-000000000002"),
        legacy("2026-10-02T10:00:00+00:00", "", turn_id="aaaaaaaa-0000-4000-8000-000000000003"),
        legacy("2026-10-03T10:00:00+00:00", "From the new history", turn_id="aaaaaaaa-0000-4000-8000-000000000004",
               projection=True),
    ])
    counts = hist.import_monthly_logs()
    assert counts == {"read": 3, "imported": 2, "already": 0, "skipped_empty": 1}
    meta = hist.conversation(BEFORE_1_2)
    assert meta["read_only"] is True and meta["title"] == "Before 1.2"
    assert meta["created_at"] == "2026-09-30T10:00:00+00:00"
    page = hist.turns(BEFORE_1_2)["items"]
    assert [t["user_text"] for t in page] == ["September words", "October words"]
    assert page[0]["turn_id"] == "t-aaaaaaaa000040008000000000000001" and page[0]["status"] == "answered"
    assert hist.import_monthly_logs() == {"read": 3, "imported": 0, "already": 2, "skipped_empty": 1}
    assert hist.verify_robert_source(turn_ref(BEFORE_1_2, page[1]["turn_id"]), "October")


# ---- ids and switches -------------------------------------------------------------------------------------------

def test_turn_ids_come_from_the_client_when_it_sends_one():
    assert turn_id_for("t-0a1b2c3d4e5f6a") == "t-0a1b2c3d4e5f6a"
    assert turn_id_for("aaaaaaaa-0000-4000-8000-000000000001") == "t-aaaaaaaa000040008000000000000001"
    assert turn_id_for(None).startswith("t-") and turn_id_for(None) != turn_id_for(None)


def test_the_history_is_off_unless_the_folder_and_environment_are_set(tmp_path):
    assert history_module.from_environ({}) is None
    assert history_module.from_environ({"COS_CONVERSATIONS_DIR": str(tmp_path)}) is None
    assert history_module.from_environ({"COS_CONVERSATIONS_DIR": str(tmp_path / "missing"),
                                        "COS_STORE_ENVIRONMENT": "dev"}) is None
    on = history_module.from_environ({"COS_CONVERSATIONS_DIR": str(tmp_path), "COS_STORE_ENVIRONMENT": "staging"})
    assert isinstance(on, History) and on.environment == "staging"


def test_new_folders_keep_the_setgid_bit_so_files_stay_in_the_reader_group(tmp_path):
    from domains.cos.store import fsio
    root = tmp_path / "conversations"
    root.mkdir()
    (root / "history").mkdir()
    (root / "history").chmod(0o2750)                    # what cos_prod.sh prepare sets, as root:1000
    History(root, ENV, clock=Clock()).ensure_default()
    assert stat.S_IMODE((root / "history" / "conversations").stat().st_mode) == 0o2750
    fsio.ensure_dir(root / "history" / "a" / "b")
    assert stat.S_IMODE((root / "history" / "a" / "b").stat().st_mode) == 0o2750
    plain = tmp_path / "plain"
    fsio.ensure_dir(plain / "x")
    assert stat.S_IMODE((plain / "x").stat().st_mode) == 0o750


def test_a_later_recovery_leaves_live_turns_alone(hist):
    # Review S6 (#462): only the first recovery of a process interrupts pending turns; those belonged to a gone process.
    cid = hist.ensure_default()["conversation_id"]
    hist.begin(send(cid))
    hist._needs_recovery = True                       # e.g. after a read-back mismatch
    hist.ensure_default()                             # any locked call runs recovery again
    assert hist.turn(cid, "t-0a1b2c3d4e5f6a")["status"] == "pending"
