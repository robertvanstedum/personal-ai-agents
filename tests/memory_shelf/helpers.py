"""Synthetic fixtures for the shelf tests. No real conversation is ever read."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from core.memory_shelf import bundle as bundles
from core.memory_shelf import inbox, sessions
from core.memory_shelf.shelf import Shelf

FAKE_KEY = "sk-ant-FAKEFAKEFAKE12345"
T0 = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)


def make_shelf(tmp_path: Path, name: str = "shelf", **kw) -> Shelf:
    kw.setdefault("min_free_bytes", 0)
    shelf = Shelf(tmp_path / name, **kw)
    shelf.init_layout()
    return shelf


def cc_lines(session_id: str, *turns: tuple[str, str]) -> str:
    """A Claude Code session file: ('human'|'assistant', text) turns plus a tool_result line."""
    rows = [{"type": "queue-operation", "sessionId": session_id}]
    for who, text in turns:
        if who == "human":
            rows.append({"type": "user", "sessionId": session_id, "timestamp": "2026-10-03T10:00:00Z",
                         "message": {"role": "user", "content": text}})
        else:
            rows.append({"type": "assistant", "sessionId": session_id,
                         "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})
    return "\n".join(json.dumps(r) for r in rows) + "\n"


def codex_lines(session_id: str, *turns: tuple[str, str]) -> str:
    rows = [{"type": "session_meta", "timestamp": "2026-10-03T11:00:00Z", "payload": {"id": session_id}}]
    for who, text in turns:
        kind = "UserMessage" if who == "human" else "AgentMessage"
        rows.append({"type": "event_msg", "timestamp": "2026-10-03T11:00:01Z",
                     "payload": {"type": "item_completed", "item": {"type": kind, "content": [{"type": "text", "text": text}]}}})
    return "\n".join(json.dumps(r) for r in rows) + "\n"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def cc_bundle(session_text: str, *, origin: str = "watcher:claude-code", title: str = "claude-code session") -> bundles.Bundle:
    parsed = sessions.parse_claude_code(session_text.splitlines())
    raw = session_text.encode()
    return bundles.from_parsed(parsed, hashlib.sha256(raw).hexdigest(), len(raw), key=f"claude-code:{parsed.source_id}",
                               title=title, origin=origin, created=bundles.utc(parsed.started),
                               ledger_key=bundles.ledger_key_for("claude-code", parsed.source_id or "?"))


def write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def process(shelf, box, **kw):
    """One inbox pass with every file present approved (the gate itself is tested in test_inbox_gate)."""
    entries = inbox.scan(box, kw.get("never_copy", ()))
    return inbox.process(shelf, box, approved=inbox.approved_pairs(entries), **kw)
