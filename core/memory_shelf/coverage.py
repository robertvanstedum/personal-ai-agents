"""The coverage check (amendment §10): did the reader take every message the file shows?

The first real Codex capture silently missed turns in about 25 files, because nothing compared what the
parser took with what the file contains. This is that comparison, run on **every watcher pass**.

* **An independent count.** A counter that shares no logic with the parsers reads the same lines (it is fed
  by the one read the parser makes, via ``tee``) and counts messages per role in **every** representation a
  file has: ``item_completed`` messages and ``response_item`` copies for Codex; text parts of user and
  assistant lines for Claude Code. The only thing it shares with the parser is the list of Codex's injected
  context markers, because those are not messages anybody wrote.
* **possible_gap.** If the turns taken fall short of the messages visible, per role, by more than
  ``TOLERANCE``, the record and the ledger carry ``possible_gap`` with **counts only**.
* **unknown_kind.** A line, event, item or part type this check has never seen (a format drift) is counted
  by its type name (a short identifier from the file's own structure, never message text).

Nothing here keeps or prints message text.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Iterable, Iterator

from core.memory_shelf import sessions
from core.memory_shelf.sessions import Parsed

TOLERANCE = 0                       # messages per role the reader may fall short by before it is flagged
POSSIBLE_GAP, UNKNOWN_KIND = "possible_gap", "unknown_kind"
ROLES = ("user", "assistant")
_NAME = re.compile(r"[^A-Za-z0-9_.:/-]")

CODEX_LINES = frozenset({"session_meta", "event_msg", "response_item", "turn_context", "token_usage_record",
                         "world_state", "inter_agent_communication_metadata", "compacted"})
CODEX_EVENTS = frozenset({"item_completed", "token_count", "task_started", "task_complete",
                          "thread_settings_applied", "turn_aborted"})
CODEX_ITEMS = frozenset({"AgentMessage", "UserMessage", "Reasoning", "CommandExecution", "FileChange", "McpToolCall",
                         "ImageView", "Extension", "WebSearch", "SubAgentActivity", "ContextCompaction",
                         "FunctionCallOutput", "CollabAgentToolCall"})
CODEX_COPIES = frozenset({"message", "reasoning", "custom_tool_call", "custom_tool_call_output", "function_call",
                          "function_call_output", "agent_message", "compaction", "tool_search_call",
                          "tool_search_output"})
CODEX_ROLES = frozenset({"user", "assistant", "developer"})
CODEX_PARTS = frozenset({"input_text", "output_text", "input_image", "text", "local_image", "image"})

CC_LINES = frozenset({"user", "assistant", "attachment", "queue-operation", "last-prompt", "mode", "ai-title",
                      "custom-title", "system", "pr-link", "atis-latch", "agent-name", "bridge-session",
                      "file-history-snapshot", "file-history-delta", "frame-link", "artifact-autoreact-ledger",
                      "artifact-comment-monitor", "cost-state", "summary", "progress"})
CC_ORIGINS = frozenset({"human", "peer", "task-notification"})
CC_PARTS = frozenset({"text", "tool_use", "tool_result", "thinking", "redacted_thinking", "image", "document"})


def _name(value: object) -> str:
    return _NAME.sub("_", str(value))[:40] or "?"


def _row(raw: bytes | str) -> dict | None:
    try:
        row = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    return row if isinstance(row, dict) else None


class _Counter:
    """Counts as lines pass. ``seen`` and ``unknown`` are read after the parser has consumed the file."""

    def __init__(self) -> None:
        self.unknown: Counter = Counter()
        self.items: Counter = Counter()          # messages per role from the primary representation
        self.copies: Counter = Counter()         # messages per role from the copies

    def feed(self, raw: bytes | str) -> None:        # pragma: no cover - overridden
        raise NotImplementedError

    def tee(self, lines: Iterable[bytes | str]) -> Iterator[bytes | str]:
        for raw in lines:
            if raw.strip():
                self.feed(raw)
            yield raw

    def unseen(self, kind: str, name: object, known: frozenset) -> None:
        if name not in known:
            self.unknown[f"{kind}:{_name(name)}"] += 1

    def seen(self) -> dict[str, int]:
        return {r: max(self.items[r], self.copies[r]) for r in ROLES}

    def note_message(self, representation: Counter, role: str, text: str) -> None:        # pragma: no cover - overridden
        representation[role] += 1


class CodexCounter(_Counter):
    """Messages are kept as a multiset per representation, keyed by (role, text with whitespace collapsed). The
    messages visible in a file are, per key, whichever representation has more of them: so a copy that no item
    covers counts, a copy that an item covers does not, and the same words said twice count twice."""

    def __init__(self) -> None:
        super().__init__()
        self.item_keys: Counter = Counter()
        self.copy_keys: Counter = Counter()

    def seen(self) -> dict[str, int]:
        total: Counter = Counter()
        for key in self.item_keys.keys() | self.copy_keys.keys():
            total[key[0]] += max(self.item_keys[key], self.copy_keys[key])
        return {r: total[r] for r in ROLES}

    def feed(self, raw):
        row = _row(raw)
        if row is None:
            return
        kind, payload = row.get("type"), row.get("payload")
        self.unseen("line", kind, CODEX_LINES)
        if not isinstance(payload, dict):
            return
        sub = payload.get("type")
        if kind == "event_msg":
            self.unseen("event", sub, CODEX_EVENTS)
            if sub == "item_completed":
                item = payload.get("item") or {}
                itype = item.get("type") if isinstance(item, dict) else None
                self.unseen("item", itype, CODEX_ITEMS)
                if itype in ("UserMessage", "AgentMessage"):
                    self._count(self.item_keys, "user" if itype == "UserMessage" else "assistant", item.get("content"))
        elif kind == "response_item":
            self.unseen("copy", sub, CODEX_COPIES)
            if sub == "message":
                role = payload.get("role")
                self.unseen("role", role, CODEX_ROLES)
                if role in ROLES:
                    self._count(self.copy_keys, role, payload.get("content"))

    def _count(self, into: Counter, role: str, parts) -> None:
        """Count a message that has text beyond injected context (tags the reader treats as pointers)."""
        words = []
        for part in parts or []:
            ptype = str(part.get("type", "")).lower() if isinstance(part, dict) else ""
            self.unseen("part", ptype, CODEX_PARTS)
            text = part.get("text") if isinstance(part, dict) else None
            if ptype in ("text", "input_text", "output_text") and isinstance(text, str) and text.strip():
                if sessions.leading_tag(text) in (*sessions.CODEX_INJECTED_TAGS, "AGENTS.md"):
                    continue
                words.extend(text.split())
        if words:
            into[(role, hashlib.sha256(" ".join(words).encode("utf-8")).hexdigest()[:20])] += 1


class ClaudeCodeCounter(_Counter):
    def feed(self, raw):
        row = _row(raw)
        if row is None:
            return
        kind = row.get("type")
        self.unseen("line", kind, CC_LINES)
        if kind not in ("user", "assistant") or row.get("isSidechain"):
            return
        origin = row.get("origin")
        if kind == "user" and origin is not None:
            self.unseen("origin", origin.get("kind") if isinstance(origin, dict) else "unreadable", CC_ORIGINS)
        content = (row.get("message") or {}).get("content")
        parts = [{"type": "text", "text": content}] if isinstance(content, str) else (content if isinstance(content, list) else [])
        for part in parts:
            ptype = part.get("type") if isinstance(part, dict) else None
            self.unseen("part", ptype, CC_PARTS)
            text = part.get("text") if isinstance(part, dict) else None
            if ptype == "text" and isinstance(text, str) and text.strip():
                self.items[kind] += 1


COUNTERS = {"codex": CodexCounter, "claude-code": ClaudeCodeCounter}


def taken_by_role(parsed: Parsed) -> dict[str, int]:
    """Turns by the role of the line they came from (a system turn counts under the role that wrote it)."""
    taken: Counter = Counter()
    for turn in parsed.turns:
        if turn.basis == "response_item:agent_message":
            continue                                      # a handoff is its own representation, not a role message
        user = turn.basis.startswith(("item:UserMessage", "response_item:user")) if parsed.provider == "codex" \
            else turn.basis != "role:assistant"
        taken["user" if user else "assistant"] += 1
    return {r: taken[r] for r in ROLES}


def assess(parsed: Parsed, counter: _Counter) -> dict:
    """The coverage record: counts only. ``flags`` is empty when the reader took everything it was shown."""
    seen, taken = counter.seen(), taken_by_role(parsed)
    gap = {r: max(0, seen[r] - taken[r]) for r in ROLES}
    flags = []
    if any(g > TOLERANCE for g in gap.values()):
        flags.append(POSSIBLE_GAP)
    if counter.unknown:
        flags.append(UNKNOWN_KIND)
    doc = {"seen": seen, "taken": taken, "gap": gap, "flags": flags}
    if counter.unknown:
        doc["unknown"] = dict(sorted(counter.unknown.items()))
    return doc


def parse_with_coverage(kind: str, lines: Iterable[bytes | str], parsers: dict) -> Parsed:
    """Parse a session file and attach the coverage check, in one read."""
    counter = COUNTERS[kind]()
    parsed = parsers[kind](counter.tee(lines))
    parsed.coverage = assess(parsed, counter)
    return parsed


__all__ = ["parse_with_coverage", "assess", "taken_by_role", "POSSIBLE_GAP", "UNKNOWN_KIND", "TOLERANCE"]
