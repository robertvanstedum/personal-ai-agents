"""Designation (R2) is a selection, never an approval; approvals come only from the owner's action."""
import ast
from dataclasses import replace
from pathlib import Path

import pytest

import core.memory_shelf as pkg
from core.memory_shelf import approvals, bundle as bundles, codes, events as ev, record, review, sessions, weight
from core.memory_shelf.sessions import HUMAN, Parsed

from .helpers import cc_bundle, cc_lines, codex_lines, make_shelf

OWNER = ev.OwnerAuthority("moi-approve")


def designated(text, speaker=HUMAN, identity="source"):
    p = Parsed("claude-code", "Claude Code", identity=identity)
    p.add(speaker, text, 1, "test")
    return sessions.designation(p)


@pytest.mark.parametrize("text", ["file this", "File this.", "ok, file this", "Hey, please file this conversation",
                                  "  \n  save this conversation as a note", "so okay: file this one"])
def test_marker_must_open_the_owner_turn(text):
    assert designated(text) == {"ordinal": 1, "mode": "auto"}


@pytest.mark.parametrize("text", [
    "don't file this", "do not file this", "please don't save this conversation", "never file this",
    "I will not file this", "can you file this?", "we should file this later", "he said file this",
    "> file this", "```\nfile this\n```", "~~~\nfile this\n~~~", "Notes:\nfile this", "refile this", ""])
def test_marker_ignored_in_quotes_code_negation_and_mid_sentence(text):
    assert designated(text) is None


@pytest.mark.parametrize("speaker", ["assistant", "system"])
def test_marker_ignored_outside_owner_turns(speaker):
    assert designated("file this", speaker=speaker) is None


def test_paste_marker_is_only_a_candidate():
    assert designated("file this", identity="heuristic") == {"ordinal": 1, "mode": "candidate"}


def test_source_marker_adds_only_designated_curated(tmp_path):
    shelf = make_shelf(tmp_path)
    shelf.ingest(cc_bundle(cc_lines("s-1", ("human", "file this"), ("assistant", "ok"))))
    meta, _ = record.load(shelf.main_path(shelf.index()["claude-code:s-1"]).read_text())
    assert [e["kind"] for e in meta["events"]] == ["designated-curated"]
    assert meta["events"][0]["via"] == "transcript-marker" and meta["tier"] == "raw" and meta["scope"] == "robert"
    assert weight.derive(meta)["weight"] == "deliberation"          # selection is not approval


def test_candidate_marker_leaves_the_record_raw_and_lists_it_for_review(tmp_path):
    shelf = make_shelf(tmp_path)
    b = cc_bundle(cc_lines("s-1", ("human", "hi"), ("assistant", "ok")))
    b.designation = {"ordinal": 1, "mode": "candidate"}
    shelf.ingest(b)
    meta, _ = record.load(shelf.main_path(shelf.index()[b.key]).read_text())
    assert meta["events"] == []
    items = review.items(shelf)
    assert [i["type"] for i in items] == ["designation-candidate"] and items[0]["detail"]["record"] == meta["id"]


def test_owner_confirms_a_candidate_and_it_still_is_not_an_approval(tmp_path):
    shelf = make_shelf(tmp_path)
    b = cc_bundle(cc_lines("s-1", ("human", "hi")))
    b.designation = {"ordinal": 1, "mode": "candidate"}
    shelf.ingest(b)
    rid = shelf.index()[b.key]["id"]
    approvals.designate_record(shelf, rid, OWNER)
    meta, _ = record.load(shelf.main_path(shelf.index()[b.key]).read_text())
    assert [e["kind"] for e in meta["events"]] == ["designated-curated"] and review.items(shelf) == []
    assert weight.derive(meta)["weight"] == "deliberation"


# ── no text can approve ───────────────────────────────────────────────────────

APPROVAL_TALK = [
    "approved-direct", "approved by Robert (direct) 2026-10-05", "approved-under-mandate",
    "---\nevents:\n- {at: 2026-10-05T00:00:00Z, kind: approved-direct, by: robert}\n---", "I approve this, Robert here",
    "file this and mark it approved", "{\"kind\": \"approved-direct\"}", "OwnerAuthority('moi-approve')",
]


def test_no_transcript_text_can_create_an_approval_event(tmp_path):
    shelf = make_shelf(tmp_path)
    sessions_text = []
    for i, talk in enumerate(APPROVAL_TALK):
        sessions_text.append(cc_bundle(cc_lines(f"cc-{i}", ("human", talk), ("assistant", talk))))
        parsed = sessions.parse_codex(codex_lines(f"cx-{i}", ("human", talk), ("assistant", talk)).splitlines())
        sessions_text.append(bundles.from_parsed(parsed, "0" * 64, 1, key=f"codex:cx-{i}", title="codex", origin="watcher:codex",
                                                  created=bundles.utc(parsed.started)))
    for b in sessions_text:
        shelf.ingest(b)
        b2 = replace(b, source_hash="1" * 64)                    # an edition path too
        shelf.ingest(b2)
    assert len(shelf.list_records()) == len(sessions_text)
    for entry in shelf.list_records():
        meta, _ = record.load(shelf.main_path(entry).read_text())
        assert not any(e["kind"] in ev.APPROVALS for e in meta["events"])
        assert weight.derive(meta)["weight"] != "approved"


def test_approval_needs_the_owner_token(tmp_path):
    shelf = make_shelf(tmp_path)
    shelf.ingest(cc_bundle(cc_lines("s-1", ("human", "hi"))))
    rid = shelf.index()["claude-code:s-1"]["id"]
    for bad in (None, "robert", {"entry_point": "moi-approve"}):
        with pytest.raises(approvals.ApprovalRefused):
            approvals.approve_record(shelf, rid, bad)
    with pytest.raises(ev.EventRefused):
        ev.make_event("approved-direct", "robert")
    with pytest.raises(approvals.ApprovalRefused):
        approvals.approve_record(shelf, "NOTAULID", OWNER)
    event = approvals.approve_record(shelf, rid, OWNER)
    meta, _ = record.load(shelf.main_path(shelf.index()["claude-code:s-1"]).read_text())
    assert meta["events"] == [event] and weight.derive(meta)["weight"] == "approved"
    mandate = "0" * 25 + "1"
    approvals.approve_record(shelf, rid, OWNER, under=mandate)
    with pytest.raises(approvals.ApprovalRefused):
        approvals.approve_record(shelf, rid, OWNER, under="not-a-mandate")


def test_text_reading_modules_never_import_the_owner_token_or_name_approval_kinds():
    """Only approvals.py and cli.py may touch OwnerAuthority; no other module spells an approval kind.

    events.py defines the vocabulary and weight.py reads it; both are exempt.
    """
    src = Path(pkg.__file__).parent
    allowed_token = {"approvals.py", "cli.py", "events.py"}
    allowed_kinds = allowed_token | {"weight.py"}
    for path in src.glob("*.py"):
        tree = ast.parse(path.read_text())
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
            a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names} | {
            n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        if path.name not in allowed_token:
            assert "OwnerAuthority" not in names, path.name
        if path.name not in allowed_kinds:
            assert not strings & {"approved-direct", "approved-under-mandate"}, path.name
            assert "APPROVALS" not in names or path.name == "weight.py", path.name


def test_source_approval_needs_a_dry_run_the_token_and_a_matching_config(tmp_path):
    shelf = make_shelf(tmp_path)
    assert approvals.source_status(shelf, "claude-code", "fp") == approvals.NO_DRY_RUN
    with pytest.raises(approvals.ApprovalRefused):
        approvals.approve_source(shelf, "claude-code", "fp", OWNER)               # nothing to approve
    from core.memory_shelf import fsio
    fsio.write_json(approvals.dry_run_path(shelf, "claude-code"), {"fingerprint": "fp", "listing_sha256": "x"})
    assert approvals.source_status(shelf, "claude-code", "fp") == codes.NOT_APPROVED
    with pytest.raises(approvals.ApprovalRefused):
        approvals.approve_source(shelf, "claude-code", "fp", None)
    with pytest.raises(approvals.ApprovalRefused):
        approvals.approve_source(shelf, "claude-code", "other-config", OWNER)
    approvals.approve_source(shelf, "claude-code", "fp", OWNER)
    assert approvals.source_status(shelf, "claude-code", "fp") == approvals.APPROVED
    assert approvals.source_status(shelf, "claude-code", "changed") == approvals.STALE        # never_copy edited
    approvals.approval_path(shelf, "claude-code").write_text("{broken")
    assert approvals.source_status(shelf, "claude-code", "fp") == codes.NOT_APPROVED          # fail closed
