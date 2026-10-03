"""Format-only parsers for chair conversations (v0.5 B3, B6; amendment R2).

The normalizer converts a session file into ordered turns **without summarising,
inferring or dropping**. The speaker comes only from the source's own structure,
never from what the text says:

* Claude Code (``~/.claude/projects/*/<session>.jsonl``): a human turn is a
  ``type: user`` line that is not a tool result, has no ``origin.kind`` (a
  ``task-notification`` is system), is not ``isMeta``, not ``isCompactSummary``
  and not a sidechain.
* Codex (``~/.codex/sessions/**/rollout-*.jsonl``): a human turn is an
  ``event_msg/item_completed`` ``UserMessage``; the assistant is ``AgentMessage``.
  The matching ``response_item`` copies (which also carry injected context) are
  counted, not turned into turns, so nothing is counted twice.

Everything that is not a turn (thinking, tool calls and results, images,
bookkeeping lines) is **counted by kind and pointed at by source line number**;
it stays in the source file. A line that cannot be parsed is counted as
``malformed`` and flagged, never skipped silently. Files are read line by line
(sessions reach hundreds of MB), keeping only turn text.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

HUMAN, ASSISTANT, SYSTEM = "human", "assistant", "system"


@dataclass(frozen=True)
class Turn:
    ordinal: int
    speaker: str
    text: str
    line: int                 # 1-based line in the source file
    basis: str                # which structural fact decided the speaker


@dataclass
class Parsed:
    provider: str             # claude-code | codex
    chair: str                # Claude Code | Codex
    source_id: str | None = None
    started: str | None = None
    identity: str = "source"  # "source": speakers come from the file's own structure; "heuristic": a paste
    turns: list[Turn] = field(default_factory=list)
    omitted: Counter = field(default_factory=Counter)
    pointers: list[tuple[int, str, int]] = field(default_factory=list)   # (after turn ordinal, kind, source line)
    malformed: int = 0
    lines: int = 0

    def add(self, speaker: str, text: str, line: int, basis: str) -> None:
        if text.strip():
            self.turns.append(Turn(len(self.turns) + 1, speaker, text, line, basis))

    def skip(self, kind: str, line: int) -> None:
        self.omitted[kind] += 1
        self.pointers.append((len(self.turns), kind, line))


def _json(raw: bytes | str):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def parse_claude_code(lines: Iterable[bytes | str]) -> Parsed:
    out = Parsed("claude-code", "Claude Code")
    for number, raw in enumerate(lines, 1):
        out.lines = number
        if not raw.strip():
            continue
        row = _json(raw)
        if row is None:
            out.malformed += 1
            continue
        out.source_id = out.source_id or row.get("sessionId")
        out.started = out.started or row.get("timestamp")
        kind = row.get("type")
        if kind not in ("user", "assistant"):
            out.skip(f"line:{kind}", number)
            continue
        if row.get("isSidechain"):
            out.skip("sidechain", number)
            continue
        content = (row.get("message") or {}).get("content")
        parts = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
        if kind == "assistant":
            for part in parts:
                ptype = part.get("type") if isinstance(part, dict) else None
                if ptype == "text":
                    out.add(ASSISTANT, part.get("text", ""), number, "role:assistant")
                elif ptype == "tool_use":
                    out.skip(f"tool_use:{str(part.get('name', '?'))[:40]}", number)
                else:
                    out.skip(ptype or "unknown", number)          # thinking and anything new
            continue
        origin = (row.get("origin") or {}).get("kind")
        system_why = ("origin:" + origin) if origin else "meta" if row.get("isMeta") else \
            "compaction" if row.get("isCompactSummary") else None
        for part in parts:
            ptype = part.get("type") if isinstance(part, dict) else None
            if ptype == "text":
                if system_why:
                    out.add(SYSTEM, part.get("text", ""), number, system_why)
                else:
                    out.add(HUMAN, part.get("text", ""), number, "role:user,no-origin")
            else:
                out.skip(ptype or "unknown", number)              # tool_result, image, document
    return out


def parse_codex(lines: Iterable[bytes | str]) -> Parsed:
    out = Parsed("codex", "Codex")
    for number, raw in enumerate(lines, 1):
        out.lines = number
        if not raw.strip():
            continue
        row = _json(raw)
        if row is None:
            out.malformed += 1
            continue
        out.started = out.started or row.get("timestamp")
        kind, payload = row.get("type"), row.get("payload") or {}
        if not isinstance(payload, dict):
            out.skip(f"line:{kind}", number)
            continue
        if kind == "session_meta":
            out.source_id = out.source_id or payload.get("id")
            continue
        if kind == "event_msg" and payload.get("type") == "item_completed":
            item = payload.get("item") or {}
            itype = item.get("type")
            if itype in ("UserMessage", "AgentMessage"):
                speaker = HUMAN if itype == "UserMessage" else ASSISTANT
                basis = "item:UserMessage" if itype == "UserMessage" else f"item:AgentMessage:{item.get('phase') or '?'}"
                text = []
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("type", "").lower() == "text":
                        text.append(part.get("text", ""))
                    elif isinstance(part, dict):
                        out.skip(str(part.get("type", "unknown")), number)
                out.add(speaker, "\n\n".join(text), number, basis)
            else:
                out.skip(f"item:{itype}", number)
            continue
        sub = payload.get("type")
        out.skip(f"{kind}:{sub}" if sub else f"line:{kind}", number)       # includes the response_item copies
    return out


_MARKER = re.compile(r"^\s*(?:(?:ok|okay|please|hey|hi|so)\b[,.!:\s]*)*(file this|save this conversation)\b", re.IGNORECASE)


def designation(parsed: Parsed) -> dict | None:
    """The first owner turn that opens with "file this" / "save this conversation" (B7, R2).

    Only a **human** turn whose speaker came from the source's structure counts; a
    quote (line starting ">" or inside a code fence), a negation ("don't file this"),
    an assistant's recommendation, or a phrase in the middle of a sentence never does.
    A paste (heuristic speakers) can only produce a *candidate* for Robert to confirm;
    it never designates by itself (fail closed). Designation is a **selection**, not an
    approval: it can add ``designated-curated`` and nothing else.
    """
    for turn in parsed.turns:
        if turn.speaker != HUMAN:
            continue
        first = next((l for l in turn.text.splitlines() if l.strip()), "")
        if first.lstrip().startswith((">", "```", "~~~")) or not _MARKER.match(first):
            continue
        return {"ordinal": turn.ordinal, "mode": "auto" if parsed.identity == "source" else "candidate"}
    return None


__all__ = ["Turn", "Parsed", "parse_claude_code", "parse_codex", "designation", "HUMAN", "ASSISTANT", "SYSTEM"]
