"""Inbox dry run, the exact-file approval gate (fail closed), and category handling of the new export layout."""
import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest

from core.memory_shelf import approvals, cli, codes, events as ev, inbox, ledger

from .helpers import FAKE_KEY
from .test_cli import NO, NOW, World, YES
from .test_inbox import conv, make_zip

SECRETS = ("SECRET-TITLE", "SECRET-SUMMARY", "SECRET-BODY", "SECRET-ATTACHMENT", "SECRET-FILENAME", "SECRET-REPLY")
MANIFEST_URL = "https://download.example.invalid/expiring/ABC123SIGNATURE"


def secret_conv(uuid="c-1", created="2026-09-01T10:00:00Z"):
    c = conv(uuid, "SECRET-TITLE", [("human", "SECRET-BODY"), ("assistant", "SECRET-REPLY")], created=created)
    c["summary"] = "SECRET-SUMMARY"
    c["chat_messages"][0]["attachments"] = [{"file_name": "SECRET-FILENAME.pdf", "file_size": 1234, "file_type": "pdf",
                                             "extracted_content": "SECRET-ATTACHMENT text"}]
    return c


def zip_of(folder: Path, name: str, members: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(folder / name, "w") as zf:
        for member, text in members.items():
            zf.writestr(member, text)
    return folder / name


def new_layout(box: Path) -> None:
    """A synthetic copy of the new export layout: manifest plus category zips."""
    box.mkdir(parents=True, exist_ok=True)
    zip_of(box, "conversations-000.zip", {"conversations.json": json.dumps(
        [secret_conv("c-1"), secret_conv("c-2", "2026-09-05T10:00:00Z")])})
    zip_of(box, "light_metadata-000.zip", {"users.json": '[{"email_address": "nobody@example.invalid"}]',
                                           "login_history.json": '[{"ip": "203.0.113.9"}]'})
    zip_of(box, "memories-000.zip", {"memories/m-1.json": '{"memory": "SECRET-BODY"}'})
    zip_of(box, "projects-000.zip", {"projects/p-1.json": '{"name": "SECRET-TITLE"}'})
    zip_of(box, "frames-000.zip", {"artifacts/a-1/versions/1-1/index.md": "# SECRET-BODY"})
    (box / "data-export-manifest.json").write_text(json.dumps({"files": [{"name": "conversations-000.zip", "url": MANIFEST_URL}]}))


def tree_bytes(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def all_text(root: Path) -> str:
    return "\n".join(p.read_bytes().decode("utf-8", "replace") for p in root.rglob("*") if p.is_file())


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


# ── dry run ───────────────────────────────────────────────────────────────────

def test_dry_run_lists_files_and_counts_but_never_a_title_name_summary_or_text(w):
    new_layout(w.inbox)
    code, text = w.run("dry-run", "inbox")
    assert code == 0
    listing = json.loads((w.shelf.status_dir / "dry-runs" / "inbox.json").read_text())
    blob = json.dumps(listing) + text
    for secret in (*SECRETS, MANIFEST_URL, "nobody@example.invalid", "203.0.113.9"):
        assert secret not in blob
    rows = {r["name"]: r for r in listing["files"]}
    conv_row = rows["conversations-000.zip"]
    assert conv_row["kind"] == "claude-ai-export" and conv_row["category"] == "conversations" and conv_row["decision"] == "would_import"
    assert conv_row["sha256"] == hashlib.sha256((w.inbox / "conversations-000.zip").read_bytes()).hexdigest()
    assert conv_row["bytes"] == (w.inbox / "conversations-000.zip").stat().st_size
    assert conv_row["export"] == {"conversations": 2, "messages": 4, "created_first": "2026-09-01", "created_last": "2026-09-05",
                                  "conversations_with_branches": 0, "attachments": 2, "attachment_bytes": 2468}
    assert (rows["light_metadata-000.zip"]["decision"], rows["light_metadata-000.zip"]["reason"]) == ("excluded", "account_metadata")
    for held in ("memories-000.zip", "projects-000.zip", "frames-000.zip"):
        assert (rows[held]["decision"], rows[held]["reason"]) == ("held", "unsupported_kind")
    assert (rows["data-export-manifest.json"]["decision"], rows["data-export-manifest.json"]["reason"]) == ("ignored", "export_manifest")
    assert listing["counts"]["would_import"] == 1 and listing["counts"]["held"] == {"unsupported_kind": 3}
    assert w.shelf.list_records() == [] and (w.inbox / "conversations-000.zip").exists()       # nothing moved


def test_dry_run_counts_branches_and_orphans(w):
    c = conv("b-1", "t", [("human", "a"), ("assistant", "b"), ("assistant", "b-retry")])
    for i, m in enumerate(c["chat_messages"]):
        m["parent_message_uuid"] = "00000000-0000-4000-8000-000000000000" if i == 0 else "b-1-0"
    make_zip(w.inbox, "e.zip", [c, conv("b-2", "t2", [("human", "x"), ("assistant", "y")])])
    w.run("dry-run", "inbox")
    row = json.loads((w.shelf.status_dir / "dry-runs" / "inbox.json").read_text())["files"][0]
    assert row["export"]["conversations_with_branches"] == 1


def test_dry_run_notes_a_bad_export_with_a_reason_code_only(w):
    make_zip(w.inbox, "bad.zip", {"not": "a list"})
    w.run("dry-run", "inbox")
    row = json.loads((w.shelf.status_dir / "dry-runs" / "inbox.json").read_text())["files"][0]
    assert (row["decision"], row["reason"]) == ("would_refuse", "unknown_schema") and "export" not in row


def test_dry_run_does_not_hash_never_copy_files(w):
    new_layout(w.inbox)
    cfg = json.loads(w.cfg_path.read_text())
    cfg["never_copy"] = ["memories-000.zip"]
    w.cfg_path.write_text(json.dumps(cfg))
    w.run("dry-run", "inbox")
    rows = {r["name"]: r for r in json.loads((w.shelf.status_dir / "dry-runs" / "inbox.json").read_text())["files"]}
    assert rows["memories-000.zip"]["sha256"] is None and rows["memories-000.zip"]["reason"] == "never_copy"


# ── the gate ──────────────────────────────────────────────────────────────────

def test_inbox_processes_nothing_without_an_approval(w):
    new_layout(w.inbox)
    before = tree_bytes(w.inbox)
    code, text = w.run("inbox")
    assert code == 0 and text.startswith("inbox\tdry_run_only") and '"not_approved": 6' in text
    assert w.shelf.list_records() == [] and w.shelf.pending() == [] and tree_bytes(w.inbox) == before
    code, text = w.run("inbox")                                       # a dry run exists now, still no approval
    assert text.startswith("inbox\tnot_approved") and w.shelf.list_records() == [] and tree_bytes(w.inbox) == before
    assert "dry-run\tinbox\tnot_approved" in w.run("review")[1]


def test_approval_is_tty_only_and_needs_a_dry_run_first(w):
    new_layout(w.inbox)
    code, text = w.run("approve-source", "inbox")
    assert code == cli.REFUSED and "no dry-run listing" in text
    w.run("dry-run", "inbox")
    assert w.run("approve-source", "inbox", confirm=NO)[0] == cli.REFUSED
    assert w.run("inbox")[1].startswith("inbox\tnot_approved") and w.shelf.list_records() == []


def test_approved_inbox_imports_conversations_and_leaves_everything_else_untouched(w):
    new_layout(w.inbox)
    others = ("light_metadata-000.zip", "memories-000.zip", "projects-000.zip", "frames-000.zip", "data-export-manifest.json")
    before = {n: (w.inbox / n).read_bytes() for n in others}
    w.run("dry-run", "inbox")
    assert w.run("approve-source", "inbox")[0] == 0
    code, text = w.run("inbox")
    assert code == 0 and text.startswith("inbox\tok") and '"captured": 2' in text and '"held": 3' in text and '"excluded": 2' in text
    assert len(w.shelf.list_records()) == 2
    for name, data in before.items():                                 # left in place, same bytes, not in _refused
        assert (w.inbox / name).read_bytes() == data
    assert not (w.inbox / "_refused").exists() and not (w.inbox / "conversations-000.zip").exists()
    assert (w.inbox / "_processed" / "2026-10-03" / "conversations-000.zip").is_file()
    inbox_ledger = ledger.report(w.shelf)["sources"]["inbox"]
    assert inbox_ledger["excluded"] == {"account_metadata": 1, "export_manifest": 1} and inbox_ledger["held"] == {"unsupported_kind": 3}
    assert inbox_ledger["missing"] == {} and ledger.report(w.shelf)["ok"]
    assert "held={\"unsupported_kind\": 3}" in w.run("ledger")[1]


def test_nothing_from_account_metadata_or_the_manifest_reaches_the_shelf_or_ledger(w):
    new_layout(w.inbox)
    w.run("dry-run", "inbox")
    w.run("approve-source", "inbox")
    w.run("inbox")
    w.run("inbox")
    text = all_text(w.shelf.root)
    for leak in ("nobody@example.invalid", "203.0.113.9", MANIFEST_URL, "ABC123SIGNATURE"):
        assert leak not in text
    assert "login_history" not in text and "users.json" not in text


def test_held_and_excluded_rows_are_written_once_not_per_run(w):
    new_layout(w.inbox)
    w.run("dry-run", "inbox")
    w.run("approve-source", "inbox")
    w.run("inbox")
    ledger_file = w.shelf.ledger_dir / "ledger.jsonl"
    rows = len(ledger_file.read_text().splitlines())
    w.run("dry-run", "inbox")                    # the conversations moved away, the rest stayed: approve that set again
    w.run("approve-source", "inbox")
    w.run("inbox")
    assert len(ledger_file.read_text().splitlines()) == rows


def test_an_approval_does_not_survive_a_changed_file(w):
    new_layout(w.inbox)
    w.run("dry-run", "inbox")
    w.run("approve-source", "inbox")
    zip_of(w.inbox, "conversations-000.zip", {"conversations.json": json.dumps([secret_conv("c-9")])})   # same name, new bytes
    code, text = w.run("inbox")
    assert text.startswith("inbox\tdry_run_only") and w.shelf.list_records() == []
    assert (w.inbox / "conversations-000.zip").exists() and not (w.inbox / "_processed").exists()
    assert approvals.source_status(w.shelf, "inbox", inbox.fingerprint(w.inbox, ())) == approvals.STALE
    # and only a fresh dry run + owner approval brings it back
    assert w.run("approve-source", "inbox")[0] == 0
    assert '"captured": 1' in w.run("inbox")[1]


def test_a_new_or_renamed_file_makes_the_approval_stale(w):
    new_layout(w.inbox)
    w.run("dry-run", "inbox")
    w.run("approve-source", "inbox")
    (w.inbox / "extra.txt").write_text("Human: hello\nClaude: hi")
    assert w.run("inbox")[1].startswith("inbox\tdry_run_only") and w.shelf.list_records() == []
    w.run("dry-run", "inbox")
    w.run("approve-source", "inbox")
    (w.inbox / "extra.txt").rename(w.inbox / "renamed.txt")
    assert w.run("inbox")[1].startswith("inbox\tdry_run_only") and w.shelf.list_records() == []


def test_approving_after_the_inbox_changed_since_the_dry_run_is_refused(w):
    new_layout(w.inbox)
    w.run("dry-run", "inbox")
    (w.inbox / "late.txt").write_text("Human: a\nClaude: b")
    code, text = w.run("approve-source", "inbox")
    assert code == cli.REFUSED and "different configuration" in text


def test_a_file_swapped_after_the_check_is_not_taken(w, tmp_path):
    box = w.inbox
    box.mkdir()
    (box / "p.txt").write_text("Human: a\nClaude: b")
    pairs = inbox.approved_pairs(inbox.scan(box))
    (box / "p.txt").write_text("Human: changed\nClaude: after the check")
    out = inbox.process(w.shelf, box, now=NOW, approved=pairs)
    assert out["counts"] == {codes.NOT_APPROVED: 1} and w.shelf.list_records() == []
    assert inbox.process(w.shelf, box, now=NOW)["counts"] == {codes.NOT_APPROVED: 1}       # approved=None: nothing at all


def test_the_gate_holds_for_pastes_and_never_copy_too(w):
    box = w.inbox
    box.mkdir()
    (box / "p.txt").write_text("Human: a\nClaude: b")
    (box / "skip.txt").write_text("Human: a\nClaude: b")
    cfg = json.loads(w.cfg_path.read_text())
    cfg["never_copy"] = ["skip.txt"]
    w.cfg_path.write_text(json.dumps(cfg))
    code, text = w.run("inbox")
    assert w.shelf.list_records() == [] and (box / "skip.txt").exists() and (box / "p.txt").exists() and not (box / "_refused").exists()
    w.run("dry-run", "inbox")
    w.run("approve-source", "inbox")
    w.run("inbox")
    assert len(w.shelf.list_records()) == 1 and (box / "_refused" / "skip.txt").exists()


def test_the_canary_keeps_its_path_and_needs_no_approval(w):
    w.run("canary", "emit")
    new_layout(w.inbox)
    code, text = w.run("inbox")
    assert text.startswith("inbox\tdry_run_only") and '"captured": 1' in text and '"not_approved": 6' in text
    assert len(w.shelf.list_records(canary=True)) == 1 and w.shelf.list_records() == []
    assert (w.inbox / "conversations-000.zip").exists()


def test_a_canary_file_carrying_anything_else_is_still_refused_without_approval(w):
    from core.memory_shelf import canary
    w.inbox.mkdir()
    doc = canary.document("2026-10-03")
    doc["turns"].append({"speaker": "human", "text": "smuggled"})
    (w.inbox / "x.canary.json").write_text(json.dumps(doc))
    w.run("inbox")
    assert w.shelf.list_records(canary=True) == [] and inbox.refused_listing(w.inbox)[0]["reason"] == codes.UNKNOWN_SCHEMA


def test_the_older_single_zip_layout_still_works_under_the_gate(w):
    make_zip(w.inbox, "export.zip", [conv("u-1", "Plan", [("human", "q"), ("assistant", "a")])])
    assert w.run("inbox")[0] == 0 and w.shelf.list_records() == []
    w.run("dry-run", "inbox")
    w.run("approve-source", "inbox")
    assert '"captured": 1' in w.run("inbox")[1]


def test_a_non_tty_shell_cannot_approve_the_inbox(w):
    new_layout(w.inbox)
    w.run("dry-run", "inbox")
    import subprocess
    import sys
    run = subprocess.run([sys.executable, "-m", "core.memory_shelf.cli", "--config", str(w.cfg_path), "approve-source", "inbox"],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True, cwd=Path(__file__).resolve().parents[2],
                         env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])})
    assert run.returncode == cli.REFUSED and "not_interactive_or_declined" in run.stdout
    assert w.run("inbox")[1].startswith("inbox\tnot_approved")
