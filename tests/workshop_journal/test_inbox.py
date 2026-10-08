"""Chat drop-off (v0.6 section 9; acceptance rows I01, I02 and the document side of A01)."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

import pytest

from conftest import WORKSHOP, envelope, new_id, progress
from core.workshop_journal import inbox as ib
from core.workshop_journal.journal import Journal

HEADER = """---
workshop: workshop-neubau
sender: claude-chat
kind: handoff
topic: local-workshop-design
session_started: 2026-10-08T11:59:00-05:00
session_ended: 2026-10-08T14:30:00-05:00
reply_to: null
supersedes: null
recipients: [robert, codex]
artifacts:
  - LOCAL_WORKSHOP_BACKEND_DESIGN_2026-10-08.md
captured: handoff
---
Decided: build the journal first.
Open: where the backup goes.
Who acts next: codex.
"""
NAME = "HANDOFF_claude-chat_local-workshop-design_2026-10-08_1430.md"


def inbox_dir(root) -> Path:
    return Path(root) / WORKSHOP / "inbox"


@pytest.fixture
def box(root):
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    assert j.append(progress("seed")).committed
    folder = inbox_dir(root)
    folder.mkdir(mode=0o700)
    return ib.Inbox(j, settle=0), folder, j


def drop(folder, name=NAME, text=HEADER):
    path = folder / name
    path.write_text(text) if isinstance(text, str) else path.write_bytes(text)
    return path


# ── frozen identity rule ───────────────────────────────────────────────────────────────────────────────────────────
def test_the_drop_off_namespace_and_a_known_id_are_frozen():
    assert str(ib.NAMESPACE) == "1c292254-b439-5774-b166-604b2585071f"                                              # never a random namespace per installation
    assert ib.dropoff_id("workshop-neubau", "a" * 64, "b" * 64) == "795965a2-0dbf-5511-9d32-28940665a937"
    eid = ib.dropoff_id("workshop-neubau", "a" * 64, "b" * 64)
    assert eid == str(uuid.uuid5(ib.NAMESPACE, json.dumps(["workshop-neubau", "a" * 64, "b" * 64, "1"], separators=(",", ":"))))
    assert ib.dropoff_id("workshop-neubau", "a" * 64, "b" * 64) == eid != ib.dropoff_id("other", "a" * 64, "b" * 64)


# ── a handoff becomes one entry and one retained document ─────────────────────────────────────────────────────────
def test_a_valid_handoff_becomes_one_progress_entry_citing_the_retained_original(box):
    inbox, folder, j = box
    drop(folder)
    [dry] = inbox.scan()
    assert dry.status == "would_ingest" and len(j.read().events) == 1                               # a dry run changes nothing
    [out] = inbox.scan(apply=True)
    assert out.status == "ingested" and out.event_id == ib.dropoff_id(WORKSHOP, out.original_sha256, out.retained_sha256)
    ev = j.get(out.event_id).evidence["event"]
    assert (ev["kind"], ev["actor"], ev["item"], ev["topic"]) == ("progress", "claude-chat", "topic:local-workshop-design", "local-workshop-design")
    assert ev["recipients"] == ["robert", "codex"] and ev["payload"]["action"] == "handoff_received"
    assert ev["origin"]["adapter"] == "inbox" and ev["origin"]["basis"] == "claimed"            # custody, not identity
    assert ev["refs"][0]["sha256"] == out.retained_sha256 and ev["refs"][0]["availability"] == "retained"
    assert (ev["refs"][1]["id"], ev["refs"][1]["availability"], ev["refs"][1]["sha256"]) == ("LOCAL_WORKSHOP_BACKEND_DESIGN_2026-10-08.md", "pointer_only", None)
    assert j.open_artifact(out.retained_sha256) == HEADER.encode()                                 # exact original bytes
    assert (folder / NAME).read_text() == HEADER                                                   # the file itself is untouched
    manifest = json.loads((folder / "_processed" / f"{out.original_sha256}.json").read_text())
    assert manifest["event_id"] == out.event_id and manifest["adapter"] == "inbox" and manifest["filenames"] == [NAME]
    assert ev["refs"][0]["id"] == f"dropoff:{out.original_sha256[:16]}" and ev["refs"][0]["locator"] is None   # no filename in the identity


def test_I01_the_same_bytes_under_another_name_are_the_same_entry(box):
    inbox, folder, j = box
    drop(folder)
    first = inbox.scan(apply=True)[0]
    drop(folder, "HANDOFF_claude-chat_local-workshop-design_2026-10-08_1431.md")                    # renamed: also a header mismatch? no, same sender/topic
    again = inbox.scan(apply=True)
    assert [o.status for o in again] == ["duplicate", "duplicate"] and {o.event_id for o in again} == {first.event_id}
    assert sum(1 for e in j.read().events if e.get("origin", {}).get("adapter") == "inbox") == 1


def test_I01_the_same_bytes_posted_again_after_a_restart_are_a_duplicate_not_a_conflict(box, root):
    inbox, folder, j = box
    drop(folder)
    first = inbox.scan(apply=True)[0]
    for sub in ("_processed",):                                                                    # lose the manifests: the journal still decides
        for f in (folder / sub).iterdir():
            f.unlink()
    again = ib.Inbox(Journal(root, WORKSHOP, lock_timeout=0.3), settle=0).scan(apply=True)[0]
    assert again.status == "duplicate" and again.event_id == first.event_id


def test_I01_changed_bytes_under_the_same_name_are_a_separate_retained_entry(box):
    inbox, folder, j = box
    drop(folder)
    first = inbox.scan(apply=True)[0]
    drop(folder, text=HEADER.replace("Decided: build the journal first.", "Decided: build the inbox first."))
    second = inbox.scan(apply=True)[0]
    assert second.status == "ingested" and second.event_id != first.event_id and second.retained_sha256 != first.retained_sha256
    assert j.open_artifact(first.retained_sha256) == HEADER.encode()                               # the first revision is still there


def test_credentials_in_a_handoff_are_scrubbed_before_it_is_kept(box, root):
    inbox, folder, j = box
    secret = "sk-abcdefghijklmnopqrstuvwxyz0123"
    drop(folder, text=HEADER + f"Use {secret} to test.\n")
    out = inbox.scan(apply=True)[0]
    assert out.status == "ingested" and out.redactions >= 1
    for path in (Path(root) / WORKSHOP).rglob("*"):
        if path.is_file() and path.parent != folder:
            assert secret.encode() not in path.read_bytes(), path


# ── kinds ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_question_for_robert_becomes_an_open_question_that_an_agent_cannot_close(box):
    inbox, folder, j = box
    name = "HANDOFF_grok-chat_pricing_2026-10-08_1500.md"
    drop(folder, name, HEADER.replace("sender: claude-chat", "sender: grok-chat").replace("kind: handoff", "kind: question_for_robert")
         .replace("local-workshop-design", "pricing").replace("recipients: [robert, codex]\n", ""))
    out = inbox.scan(apply=True)[0]
    assert out.status == "ingested"
    ev = j.get(out.event_id).evidence["event"]
    assert ev["kind"] == "needs_you" and ev["recipients"] == ["robert"] and ev["payload"]["reason_code"] == "chat_question"
    assert len(j.state()[0]["needs_you"]) == 1


def test_a_header_decision_is_only_a_proposal_that_resolves_nothing(box):
    inbox, folder, j = box
    drop(folder, text=HEADER.replace("kind: handoff", "kind: decision") + "\nRobert approved this in the chat.\n")
    out = inbox.scan(apply=True)[0]
    ev = j.get(out.event_id).evidence["event"]
    assert ev["kind"] == "decision" and (ev["payload"]["record_event_kind"], ev["payload"]["resolves"]) == ("proposed", [])
    assert ev["actor"] == "claude-chat"


def test_a_receipt_needs_a_real_request_addressed_to_the_sender(box):
    inbox, folder, j = box
    text = HEADER.replace("kind: handoff", "kind: receipt")
    drop(folder, text=text.replace("reply_to: null", f"reply_to: {new_id()}"))
    assert inbox.scan(apply=True)[0].reason == "unresolved_reference"
    req = j.append(envelope(recipients=["claude-chat"], event_id=new_id()))
    good = "HANDOFF_claude-chat_local-workshop-design_2026-10-08_1501.md"
    drop(folder, good, text.replace("reply_to: null", f"reply_to: {req.event_id}"))
    wrong = j.append(envelope(recipients=["codex"], event_id=new_id(), text="addressed elsewhere"))
    other = "HANDOFF_claude-chat_local-workshop-design_2026-10-08_1502.md"
    drop(folder, other, text.replace("reply_to: null", f"reply_to: {wrong.event_id}"))
    results = {o.name: o for o in inbox.scan(apply=True)}
    assert results[good].status == "ingested"
    assert j.get(results[good].event_id).evidence["event"]["payload"]["request_id"] == req.event_id
    assert results[other].reason == "receipt_needs_an_addressed_request"


def test_a_conversation_is_held_as_a_memory_source_never_archived_by_this_route(box):
    inbox, folder, j = box
    name = "CONVERSATION_claude-chat_local-workshop-design_2026-10-08.md"
    drop(folder, name, HEADER.replace("kind: handoff", "kind: conversation").replace("captured: handoff", "captured: conversation") + "Robert: hi\n")
    out = inbox.scan(apply=True)[0]
    assert (out.status, out.reason) == ("source_approval_required", "source_approval_required")
    assert len(j.read().events) == 1 and not (Path(inbox.path()) / "_processed").exists()
    assert not (Path(j.dir) / "artifacts").exists()                                                 # nothing was retained either


# ── I02: refusals have no authority and capture nothing ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,reason", [
    ("no header at all\n", "no_header"),
    (HEADER.replace("---\nDecided", "Decided").replace("captured: handoff\n---\n", "captured: handoff\n"), "header_not_closed"),
    (HEADER.replace("topic: local-workshop-design", "topic: local-workshop-design\ntopic: other"), "duplicate_key"),
    (HEADER.replace("sender: claude-chat", "sender: &a claude-chat"), "anchors_and_aliases_refused"),
    (HEADER.replace("sender: claude-chat", "sender: *a"), "anchors_and_aliases_refused"),
    (HEADER.replace("sender: claude-chat", "sender: !!python/object/apply:os.system [x]"), "tags_refused"),
    (HEADER.replace("sender: claude-chat", "sender: !!str claude-chat"), "tags_refused"),
    (HEADER.replace("kind: handoff", "kind: [handoff"), "bad_yaml"),
    (HEADER.replace("captured: handoff", "captured: handoff\nextra: 1"), "unknown_header_field"),
    (HEADER.replace("workshop: workshop-neubau", "workshop: elsewhere"), "wrong_workshop"),
    (HEADER.replace("sender: claude-chat", "sender: robert"), "unknown_sender"),
    (HEADER.replace("sender: claude-chat", "sender: yes"), "unknown_sender"),
    (HEADER.replace("kind: handoff", "kind: approval"), "unknown_kind"),
    (HEADER.replace("topic: local-workshop-design", "topic: Bad Topic"), "bad_topic"),
    (HEADER.replace("2026-10-08T14:30:00-05:00", "2026-10-08T10:30:00-05:00"), "session_ends_before_it_starts"),
    (HEADER.replace("2026-10-08T14:30:00-05:00", "2026-10-08 14:30"), "bad_session_ended"),
    (HEADER.replace("reply_to: null", "reply_to: not-a-uuid"), "bad_reply_to"),
    (HEADER.replace("recipients: [robert, codex]", "recipients: [robert, nobody]"), "bad_recipients"),
    (HEADER.replace("  - LOCAL_WORKSHOP_BACKEND_DESIGN_2026-10-08.md", "  - ../../etc/passwd"), "bad_artifacts"),
    (HEADER.replace("captured: handoff", "captured: handoff\nclassification: private"), "excluded_source"),
    (HEADER.replace("kind: handoff", "kind: handoff\nkind2: x"), "unknown_header_field"),
])
def test_I02_a_bad_header_is_held_with_no_entry_and_no_retained_copy(box, root, text, reason):
    inbox, folder, j = box
    drop(folder, text=text)
    [out] = inbox.scan(apply=True)
    assert out.status == "held" and out.reason == reason, (out.to_json())
    assert len(j.read().events) == 1 and not (Path(j.dir) / "artifacts").exists()
    held = json.loads((folder / "_held" / f"{out.original_sha256}.json").read_text())
    assert held["reason"] == reason and "sender" not in held and "Decided" not in json.dumps(held)


def test_I02_a_header_that_is_too_deep_or_too_large_is_refused():
    deep = "---\n" + "a:\n" + "".join("  " * i + "b:\n" for i in range(1, 8)) + "  " * 8 + "c: 1\n---\nbody\n"
    with pytest.raises(ib.HeaderError) as caught:
        ib.parse_header(ib.split_header(deep.encode())[0])
    assert caught.value.reason == "header_too_deep"
    with pytest.raises(ib.HeaderError) as caught:
        ib.split_header(b"---\n" + b"a: " + b"x" * (ib.MAX_HEADER_BYTES + 1) + b"\n---\n")
    assert caught.value.reason == "header_too_large"
    many = "---\n" + "".join(f"k{i}: v\n" for i in range(500)) + "---\n"
    with pytest.raises(ib.HeaderError) as caught:
        ib.parse_header(ib.split_header(many.encode())[0])
    assert caught.value.reason == "header_too_complex"


def test_I02_header_values_stay_text_so_yaml_one_point_one_booleans_do_not_coerce():
    doc = ib.parse_header(b"a: yes\nb: 2026-10-08\nc: 0x10\nd: null\ne: ''\nf: ~\ng: 'null'\n")
    assert doc == {"a": "yes", "b": "2026-10-08", "c": "0x10", "d": None, "e": "", "f": None, "g": "null"}


def test_I02_a_second_document_in_the_header_is_refused():
    with pytest.raises(ib.HeaderError):
        ib.parse_header(b"a: 1\n---\nb: 2\n")


def test_I02_filename_and_header_must_agree(box):
    inbox, folder, j = box
    drop(folder, "HANDOFF_grok-chat_local-workshop-design_2026-10-08_1430.md")
    drop(folder, "HANDOFF_claude-chat_other-topic_2026-10-08_1430.md")
    drop(folder, "CONVERSATION_claude-chat_local-workshop-design_2026-10-08.md")                    # header says handoff
    drop(folder, "notes.md")
    got = {o.name: o.reason for o in inbox.scan(apply=True)}
    assert set(got.values()) == {"filename_header_mismatch", "filename_not_in_the_standard_form"} and len(got) == 4
    assert len(j.read().events) == 1


def test_I02_a_binary_body_or_an_oversized_handoff_is_held(box):
    inbox, folder, j = box
    drop(folder, NAME, HEADER.encode() + b"\xff\xfe")
    big = "HANDOFF_claude-chat_local-workshop-design_2026-10-08_1501.md"
    drop(folder, big, HEADER.encode() + b"x" * (ib.MAX_HANDOFF_BYTES + 1))
    got = {o.name: o.reason for o in inbox.scan(apply=True)}
    assert got == {NAME: "not_text", big: "too_large"} and len(j.read().events) == 1


# ── A01 on the landing folder ──────────────────────────────────────────────────────────────────────────────────────
def test_A01_links_folders_and_hidden_files_in_the_landing_folder_are_never_followed(box, tmp_path):
    inbox, folder, j = box
    outside = tmp_path / "secret.md"
    outside.write_text(HEADER)
    (folder / NAME).symlink_to(outside)
    (folder / "a-folder").mkdir()
    (folder / ".partial-upload").write_text(HEADER)
    os.link(outside, folder / "HANDOFF_claude-chat_local-workshop-design_2026-10-08_1500.md")
    got = {o.name: (o.status, o.reason) for o in inbox.scan(apply=True)}
    assert got[NAME] == ("held", "file_is_a_link") and got["a-folder"] == ("held", "folders_are_not_ingested")
    assert got["HANDOFF_claude-chat_local-workshop-design_2026-10-08_1500.md"][1] == "hard_linked"
    assert ".partial-upload" not in got and len(j.read().events) == 1


def test_A01_a_file_still_being_written_is_reported_unstable_not_read(box):
    inbox, folder, j = box
    path = drop(folder)
    inbox.settle = 0.01
    inbox._sleep = lambda s: path.write_text(HEADER + "more arrived while we waited\n")        # changed between the two observations
    [out] = inbox.scan(apply=True)
    assert out.status == "unstable" and out.reason == "source_changed" and len(j.read().events) == 1
    inbox._sleep = lambda s: None                                                                # now it has stopped changing
    assert inbox.scan(apply=True)[0].status == "ingested"


def test_A01_a_file_that_changes_during_the_read_is_unstable(box, monkeypatch):
    from core.workshop_journal import fsutil
    inbox, folder, j = box
    path = drop(folder)
    real, calls = fsutil.read_all, []

    def changing(fd):
        data = real(fd)
        calls.append(1)
        if len(calls) == 1:
            with open(path, "ab") as handle:
                handle.write(b"x")
        return data
    monkeypatch.setattr(fsutil, "read_all", changing)
    assert inbox.scan(apply=True)[0].status == "unstable"


def test_R5_a_crash_after_the_commit_then_a_renamed_file_is_the_same_entry(box, monkeypatch):
    """Codex R5: the entry committed, the manifest was lost to a crash, then the same bytes arrived under another name."""
    inbox, folder, j = box
    drop(folder)

    def crash(self, *a, **k):
        raise OSError(5, "crash after the journal commit")
    monkeypatch.setattr(ib.Inbox, "_write_processed", crash)
    with pytest.raises(OSError):
        inbox.scan(apply=True)
    monkeypatch.undo()
    assert not (folder / "_processed").exists() and len(j.read().events) == 2                       # committed, nothing remembered
    (folder / NAME).rename(folder / "HANDOFF_claude-chat_local-workshop-design_2026-10-08_1431.md")
    [out] = inbox.scan(apply=True)
    assert out.status == "duplicate" and out.reason != "journal_id_conflict" and len(j.read().events) == 2
    manifest = json.loads((folder / "_processed" / f"{out.original_sha256}.json").read_text())
    assert manifest["filenames"] == ["HANDOFF_claude-chat_local-workshop-design_2026-10-08_1431.md"]    # the journal stayed authoritative


def test_R5_every_name_the_content_was_seen_under_is_remembered(box):
    inbox, folder, j = box
    drop(folder)
    drop(folder, "HANDOFF_claude-chat_local-workshop-design_2026-10-08_1431.md")
    out = inbox.scan(apply=True)
    manifest = json.loads((folder / "_processed" / f"{out[0].original_sha256}.json").read_text())
    assert manifest["filenames"] == sorted([NAME, "HANDOFF_claude-chat_local-workshop-design_2026-10-08_1431.md"])


def test_a_manifest_without_its_event_does_not_make_a_duplicate(box, root):
    inbox, folder, j = box
    drop(folder)
    first = inbox.scan(apply=True)[0]
    fresh = Journal(root + "-other", WORKSHOP, lock_timeout=0.3)                                      # a restored journal that lacks the event
    os.makedirs(root + "-other", mode=0o700, exist_ok=True)
    fresh.append(progress("seed"))
    (Path(root + "-other") / WORKSHOP / "inbox").mkdir(mode=0o700)
    import shutil
    shutil.copytree(folder / "_processed", Path(root + "-other") / WORKSHOP / "inbox" / "_processed")
    shutil.copy(folder / NAME, Path(root + "-other") / WORKSHOP / "inbox" / NAME)
    again = ib.Inbox(fresh, settle=0).scan(apply=True)[0]
    assert again.status == "ingested" and again.event_id == first.event_id
