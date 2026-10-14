"""Counts-only migration preview and inbox reprocessing (capture-fixes slice). Synthetic records written by 'legacy' parsers."""
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.memory_shelf import approvals, cli, codes, editions, events as ev, migration, record, render, sessions, watchers
from core.memory_shelf.config import Config, SourceCfg

from .helpers import make_shelf, write_tree
from .test_capture_attribution import (APPROVAL_JSON, APPROVAL_PROMPT, cc_user, handoff, item, jl, meta, started)
from .test_claude_ai_tree import ROOT, conversation, ingest as ingest_export, m
from .test_cli import NOW

OWNER = ev.OwnerAuthority("moi-approve")


def legacy_codex(lines):
    """Codex as the shelf stored it before the revision: roles straight from the item type, handoffs and classes unknown."""
    out = sessions.Parsed("codex", "Codex", normalizer=2)
    for number, raw in enumerate(lines, 1):
        row = json.loads(raw)
        payload = row.get("payload") or {}
        if row.get("type") == "session_meta":
            out.source_id = payload.get("id")
        if row.get("type") == "event_msg" and payload.get("type") == "item_completed":
            it = payload["item"]
            user = it["type"] == "UserMessage"
            out.add("human" if user else "assistant", it["content"][0]["text"], number,
                    "item:UserMessage" if user else "item:AgentMessage:final_answer")
        elif row.get("type") == "response_item" and payload.get("type") == "agent_message":
            out.skip("response_item:agent_message", number)
    return out


def legacy_claude_code(lines):
    out = sessions.Parsed("claude-code", "Claude Code", normalizer=1)
    for number, raw in enumerate(lines, 1):
        row = json.loads(raw)
        if row.get("type") != "user":
            continue
        origin = (row.get("origin") or {}).get("kind")
        out.source_id = out.source_id or row.get("sessionId")
        out.add("system" if origin else "human", row["message"]["content"], number, f"origin:{origin}" if origin else "role:user,no-origin")
    return out


def world(tmp_path, monkeypatch):
    shelf = make_shelf(tmp_path)
    cx = tmp_path / "codex"
    write_tree(cx, {
        "2026/10/04/rollout-a-0199aaaa-0000-0000-0000-000000000001.jsonl": "\n".join(jl(
            meta("guardian_review", sid="id-a"), started(), item("UserMessage", APPROVAL_PROMPT), item("AgentMessage", APPROVAL_JSON, "final_answer"))) + "\n",
        "2026/10/04/rollout-b-0199aaaa-0000-0000-0000-000000000002.jsonl": "\n".join(jl(
            meta("user", sid="id-b"), started(), item("UserMessage", "ordinary question"), item("AgentMessage", "ordinary answer", "final_answer"),
            handoff("/a", "/b", {"type": "input_text", "text": "NEW_TASK x"}))) + "\n",
        "2026/10/04/rollout-c-0199aaaa-0000-0000-0000-000000000003.jsonl": "\n".join(jl(
            meta("user", sid="id-c"), started(), item("UserMessage", "only plain"), item("AgentMessage", "plain reply", "final_answer"))) + "\n"})
    cc = tmp_path / "cc"
    write_tree(cc, {"proj/s-1.jsonl": "\n".join(jl(
        cc_user("explicit human words", {"kind": "human"}), cc_user("a peer says hi", {"kind": "peer", "from": "x"}),
        cc_user("task finished", {"kind": "task-notification"}))) + "\n"})
    sources = {"codex": SourceCfg("codex", "codex", cx), "claude-code": SourceCfg("claude-code", "claude-code", cc)}
    cfg = Config(shelf_root=shelf.root, inbox_root=tmp_path / "inbox", sources=sources)
    real = dict(watchers.PARSERS)
    monkeypatch.setitem(watchers.PARSERS, "codex", legacy_codex)
    monkeypatch.setitem(watchers.PARSERS, "claude-code", legacy_claude_code)
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 2)
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "claude-code", 1)
    for src in sources.values():
        watchers.run_source(shelf, src, now=NOW)
        approvals.approve_source(shelf, src.name, src.fingerprint(), OWNER)
        watchers.run_source(shelf, src, now=NOW)
    monkeypatch.setitem(watchers.PARSERS, "codex", real["codex"])
    monkeypatch.setitem(watchers.PARSERS, "claude-code", real["claude-code"])
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 3)
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "claude-code", 2)
    return shelf, cfg, sources


def tree_digest(root):
    import hashlib
    h = hashlib.sha256()
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode() + p.read_bytes())
    return h.hexdigest()


def test_the_preview_counts_the_role_changes_the_new_rules_would_make_and_writes_nothing(tmp_path, monkeypatch):
    shelf, cfg, _ = world(tmp_path, monkeypatch)
    before = tree_digest(shelf.root)
    res = migration.preview(shelf, cfg)
    assert tree_digest(shelf.root) == before                                   # read-only
    cx, cc = res["providers"]["codex"], res["providers"]["claude-code"]
    assert cx["records"] == 3 and cx["compared"] == 3 and cx["would_add_edition"] == 2 and cx["unchanged_after"] == 1
    assert cx["transitions"] == {"human->coordination": 1, "assistant->coordination": 1, "added:coordination": 1}
    assert cx["roles_before"] == {"human": 3, "assistant": 3} and cx["roles_after"] == {"assistant": 1 + 1, "human": 2, "coordination": 3}
    assert cx["classes_after"] == {"approval_review": 2, "handoff": 1} and cx["on_current_version"] == 0
    assert cc["compared"] == 1 and cc["transitions"] == {"system->human": 1, "system->coordination": 1}
    assert cc["roles_before"] == {"human": 0, "system": 3} or cc["roles_before"] == {"system": 3}
    assert cc["roles_after"] == {"human": 1, "coordination": 1, "system": 1}


def test_the_preview_prints_ids_and_counts_never_text_titles_or_paths(tmp_path, monkeypatch):
    shelf, cfg, _ = world(tmp_path, monkeypatch)
    text = json.dumps(migration.preview(shelf, cfg))
    for needle in ("ordinary question", "explicit human words", "NEW_TASK", "rollout", str(tmp_path), "proj"):
        assert needle not in text


def test_a_source_that_changed_or_vanished_since_capture_is_counted_apart_never_compared(tmp_path, monkeypatch):
    shelf, cfg, sources = world(tmp_path, monkeypatch)
    root = Path(sources["codex"].root)
    files = sorted(root.rglob("rollout-*.jsonl"))
    files[0].write_text(files[0].read_text() + json.dumps({"type": "event_msg", "payload": {"type": "token_count"}}) + "\n")
    files[1].unlink()
    row = migration.preview(shelf, cfg)["providers"]["codex"]
    assert row["source_changed"] == 1 and row["source_missing"] == 1 and row["compared"] == 1


def test_the_next_watch_pass_adds_corrected_editions_and_the_old_ones_stay(tmp_path, monkeypatch):
    shelf, cfg, sources = world(tmp_path, monkeypatch)
    old = {e["id"]: [x.path.read_bytes() for x in editions.list_editions(shelf.main_path(e).parent)] for e in shelf.list_records()}
    out = watchers.run_source(shelf, sources["codex"], now=NOW)["counts"]
    assert out == {codes.EDITION_ADDED: 2, codes.UNCHANGED: 1}, out
    for e in shelf.list_records():
        eds = editions.list_editions(shelf.main_path(e).parent)
        assert [x.path.read_bytes() for x in eds][:len(old[e["id"]])] == old[e["id"]]          # history untouched
    row = migration.preview(shelf, cfg)["providers"]["codex"]
    assert row["would_add_edition"] == 0 and row["on_current_version"] == 3                  # converged
    assert watchers.run_source(shelf, sources["codex"], now=NOW)["counts"] == {"skipped_unchanged": 3}


def test_a_current_edition_no_longer_carries_the_misattributed_turns_for_retrieval(tmp_path, monkeypatch):
    shelf, cfg, sources = world(tmp_path, monkeypatch)
    watchers.run_source(shelf, sources["codex"], now=NOW)
    for e in shelf.list_records():
        if e["provider"] != "codex":
            continue
        _, body = record.load(record.read(shelf.main_path(e)))
        turns = render.parse_body(body)
        assert not any(t.speaker == "human" and APPROVAL_PROMPT in t.text for t in turns)


def test_the_cli_commands(tmp_path, monkeypatch):
    shelf, cfg, sources = world(tmp_path, monkeypatch)
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"schema_version": 1, "shelf_root": str(shelf.root), "inbox_root": str(tmp_path / "inbox"),
                                "repo_root": str(tmp_path), "headroom": {"min_free_bytes": 0},
                                "sources": {n: {"kind": s.kind, "root": str(s.root)} for n, s in sources.items()}}))
    out = io.StringIO()
    assert cli.main(["--config", str(path), "migrate-preview", "codex"], out=out) == cli.OK
    text = out.getvalue()
    assert "migrate-preview\tcodex\t" in text and "ordinary question" not in text and "read_only" in text
    out = io.StringIO()
    assert cli.main(["--config", str(path), "reprocess", "inbox"], out=out, confirm=lambda p: False, is_tty=lambda: False) == cli.REFUSED


# ── the inbox: claude.ai attachment text leaves the dialogue by a new edition ───────────────────────────

def legacy_inbox_shelf(tmp_path, atts):
    """A shelf holding the record as the old rules stored it (extracted attachment text in the human turn, version 1), whose
    kept export file is the real one. Returns (legacy shelf, inbox root, the record's main path)."""
    from dataclasses import replace
    from core.memory_shelf import bundle as bundles, inbox
    current, box, _ = ingest_export(tmp_path, [conversation("c1", [m("r", ROOT, "human", "my question", 1, attachments=atts),
                                                                      m("a", "r", "assistant", "answer", 2)])])
    meta_, _ = record.load(record.read(current.main_path(current.index()["claude-ai:c1"])))
    ret = meta_["retained"]
    item = next(iter(inbox.load_items(box / ret["rel"], datetime.now(timezone.utc), only=ret["member"])))
    turns = [replace(t, text="my question\n\nAttachment: doc.md (text/markdown, 9 bytes)\nDOCUMENT-BODY") if t.speaker == "human" else t
             for t in item.parsed.turns]
    legacy = replace(item.parsed, turns=turns, normalizer=1)
    shelf = make_shelf(tmp_path, "legacy")
    bundle = bundles.from_parsed(legacy, item.source_sha256, item.size, key=item.key, title=item.title, origin="inbox",
                                 created=item.created, retained=ret, ledger_key=bundles.ledger_key_for("inbox", item.key))
    assert shelf.ingest(bundle).outcome == codes.CAPTURED
    return shelf, box, shelf.main_path(shelf.index()["claude-ai:c1"])


def test_reprocessing_the_inbox_adds_an_edition_without_the_extracted_text_and_keeps_the_old_one(tmp_path):
    atts = [{"file_name": "doc.md", "file_size": 9, "file_type": "text/markdown", "extracted_content": "DOCUMENT-BODY"}]
    shelf, box, main = legacy_inbox_shelf(tmp_path, atts)
    assert "DOCUMENT-BODY" in record.load(record.read(main))[1]
    cfg = Config(shelf_root=shelf.root, inbox_root=box, sources={})
    assert migration.preview(shelf, cfg)["providers"]["claude-ai"]["transitions"] == {"text_changed": 1}
    first = [e.path.read_bytes() for e in editions.list_editions(main.parent)]
    assert migration.reprocess_inbox(shelf, cfg, now=NOW) == {codes.EDITION_ADDED: 1}
    meta_, body = record.load(record.read(main))
    assert "DOCUMENT-BODY" not in body and "my question" in body and meta_["normalized"]["normalizer"] == 2
    kept = [e.path.read_bytes() for e in editions.list_editions(main.parent)]
    assert kept[0] == first[0] and len(kept) == 2 and b"DOCUMENT-BODY" in kept[0] and b"DOCUMENT-BODY" not in kept[1]
    assert migration.reprocess_inbox(shelf, cfg, now=NOW) == {"already_current": 1}


def test_reprocessing_leaves_a_record_whose_export_file_is_gone_alone(tmp_path):
    shelf, box, _ = ingest_export(tmp_path, [conversation("c1", [m("r", ROOT, "human", "q", 1), m("a", "r", "assistant", "a", 2)])])
    main = shelf.main_path(shelf.index()["claude-ai:c1"])
    meta_, body = record.load(record.read(main))
    meta_["normalized"]["normalizer"] = 1
    record.write(main, meta_, body)
    import shutil
    shutil.rmtree(box / "_processed")
    cfg = Config(shelf_root=shelf.root, inbox_root=box, sources={})
    assert migration.reprocess_inbox(shelf, cfg, now=NOW) == {"source_missing": 1}
