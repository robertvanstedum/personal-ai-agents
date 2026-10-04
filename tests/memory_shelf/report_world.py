"""A synthetic shelf for the capture report: real pipelines, invented content. Shared by the tests and the sample generator."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core.memory_shelf import approvals, events as ev, watchers
from core.memory_shelf.config import Config, SourceCfg

from .helpers import make_shelf, write_tree

OWNER = ev.OwnerAuthority("moi-approve")
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
FIXED_RUN = "20261004T120000Z-abc123"


def jl(*rows):
    return [json.dumps(r) for r in rows]


def meta(thread_source="user", sid="s"):
    return {"type": "session_meta", "timestamp": "2026-10-04T10:00:00Z", "payload": {"id": sid, "thread_source": thread_source}}


def item(kind, text, phase=None):
    row = {"type": "item_completed", "item": {"type": kind, "content": [{"type": "text", "text": text}]}}
    if phase:
        row["item"]["phase"] = phase
    return {"type": "event_msg", "payload": row}


def copy(role, text):
    return {"type": "response_item", "payload": {"type": "message", "role": role,
                                                 "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}]}}


def handoff(*parts):
    return {"type": "response_item", "timestamp": "2026-10-04T10:05:00Z",
            "payload": {"type": "agent_message", "author": "/a", "recipient": "/b", "content": list(parts)}}


def cc_user(text, origin=None):
    row = {"type": "user", "sessionId": "cc-1", "timestamp": "2026-10-04T10:00:00Z", "message": {"role": "user", "content": text}}
    if origin:
        row["origin"] = origin
    return row


def build_world(tmp_path: Path, *, approve=True, run=True, now=NOW):
    """claude-code (human, peer, task-notification, assistant) and codex (a dialogue session with a duplicate copy and a
    handoff, a guardian session): approved and captured at ``now``. Returns (shelf, cfg, sources)."""
    shelf = make_shelf(tmp_path, clock=lambda: now)                   # a fixed clock: the ledger's times are part of the payload
    cx, cc = tmp_path / "codex", tmp_path / "cc"
    write_tree(cx, {
        "2026/10/04/rollout-a-0199aaaa-0000-0000-0000-000000000001.jsonl": "\n".join(jl(
            meta("user", "cx-1"), {"type": "event_msg", "payload": {"type": "task_started"}}, item("UserMessage", "ordinary question"),
            copy("user", "ordinary question"), item("AgentMessage", "ordinary answer", "final_answer"),
            handoff({"type": "input_text", "text": "NEW_TASK x"}, {"type": "encrypted_content", "encrypted_content": "zz"}))) + "\n",
        "2026/10/04/rollout-b-0199aaaa-0000-0000-0000-000000000002.jsonl": "\n".join(jl(
            meta("guardian_review", "cx-2"), {"type": "event_msg", "payload": {"type": "task_started"}},
            item("UserMessage", "review prompt"), item("AgentMessage", "decision", "final_answer"))) + "\n"})
    write_tree(cc, {"proj/cc-1.jsonl": "\n".join(jl(
        cc_user("human words", {"kind": "human"}), cc_user("peer words", {"kind": "peer", "from": "x"}),
        cc_user("task done", {"kind": "task-notification"}),
        {"type": "assistant", "sessionId": "cc-1", "message": {"content": [{"type": "text", "text": "assistant words"},
                                                                          {"type": "tool_use", "name": "Bash"}]}},
        {"type": "last-prompt", "sessionId": "cc-1"})) + "\n"})
    sources = {"claude-code": SourceCfg("claude-code", "claude-code", cc), "codex": SourceCfg("codex", "codex", cx)}
    cfg = Config(shelf_root=shelf.root, inbox_root=tmp_path / "inbox", sources=sources)
    if run:
        for src in sources.values():
            watchers.run_source(shelf, src, now=now)
            if approve:
                approvals.approve_source(shelf, src.name, src.fingerprint(), OWNER)
                watchers.run_source(shelf, src, now=now)
    return shelf, cfg, sources
