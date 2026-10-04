"""Format-only parsers for chair conversations (v0.5 B3, B6; amendment R2).

The normalizer converts a session file into ordered turns **without summarising,
inferring or dropping**. The speaker comes only from the source's own structure,
never from what the text says:

* Claude Code (``~/.claude/projects/*/<session>.jsonl``): a human turn is a
  ``type: user`` line that is not a tool result, has no ``origin.kind`` (a
  ``task-notification`` is system), is not ``isMeta``, not ``isCompactSummary``
  and not a sidechain.
* Codex (``~/.codex/sessions/**/rollout-*.jsonl``): ``event_msg/item_completed`` ``UserMessage`` /
  ``AgentMessage`` is the primary source (human / assistant). A ``response_item`` message is its copy;
  where no item matches it (same role, same text, same task window, else anywhere in the file) it is
  a turn of its own, so a session written only as ``response_item`` is still captured and nothing is
  counted twice. Injected context (``<environment_context>``, ``# AGENTS.md instructions`` ...) is a
  pointer, never a human turn; ``<heartbeat>`` is a system turn; a leading tag the reader does not know
  makes a ``response_item`` message a system turn (fail closed). See ``NORMALIZER_VERSION``.

Everything that is not a turn (thinking, tool calls and results, images,
bookkeeping lines) is **counted by kind and pointed at by source line number**;
it stays in the source file. A line that cannot be parsed is counted as
``malformed`` and flagged, never skipped silently. Files are read line by line
(sessions reach hundreds of MB), keeping only turn text.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Iterable

HUMAN, ASSISTANT, SYSTEM = "human", "assistant", "system"
MARK = "@mark"

# Bump a parser's version when a change to it can change the turn sequence of an unchanged source file.
# The watcher re-reads such files and, when the turns differ, adds an edition (note ``normalizer:<old>-><new>``).
# 2 = Codex reads response_item messages too, with reconcile (injected context pointers, heartbeat as system).
NORMALIZER_VERSION = {"claude-code": 1, "codex": 2}
DEFAULT_NORMALIZER = 1                      # a record or state entry written before versions existed


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
    # (after turn ordinal, kind, source line, detail); kind "@mark" is a rendered header, not an omission
    pointers: list[tuple[int, str, int, str | None]] = field(default_factory=list)
    malformed: int = 0
    lines: int = 0
    manifest: dict = field(default_factory=dict)      # format-level counts (branches, attachments, ...) for the record
    normalizer: int = DEFAULT_NORMALIZER              # which parser version produced the turns
    coverage: dict = field(default_factory=dict)      # the coverage check's counts (coverage.py); counts only

    def add(self, speaker: str, text: str, line: int, basis: str) -> None:
        if text.strip():
            self.turns.append(Turn(len(self.turns) + 1, speaker, text, line, basis))

    def skip(self, kind: str, line: int, detail: str | None = None) -> None:
        self.omitted[kind] += 1
        self.pointers.append((len(self.turns), kind, line, detail))

    def mark(self, text: str) -> None:
        """A one-line header between turns (a branch heading). Not an omission, so not counted as one."""
        self.pointers.append((len(self.turns), MARK, 0, text))


def _json(raw: bytes | str):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def parse_claude_code(lines: Iterable[bytes | str]) -> Parsed:
    out = Parsed("claude-code", "Claude Code", normalizer=NORMALIZER_VERSION["claude-code"])
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


# Codex's own injected-context markers: kept as pointers (the source file keeps the text), never a turn.
CODEX_INJECTED_TAGS = frozenset({
    "environment_context", "guardian_tool_descriptions", "recommended_plugins", "external_codex_apps_open_page",
    "codex_delegation", "in-app-browser-context", "app-context",
    "image", "/image",                                      # placeholders around an attached image
})
CODEX_AGENTS_PREFIX = "# AGENTS.md instructions"
CODEX_HEARTBEAT = "heartbeat"
CODEX_HUMAN_TAGS = frozenset({"send_user_message_question_reply"})
_TAG = re.compile(r"^<(/?[A-Za-z][A-Za-z0-9_:-]*)")
_TEXT_PARTS = ("text", "input_text", "output_text")


def leading_tag(text: str) -> str | None:
    """The tag a text opens with (``environment_context``), or ``AGENTS.md`` for that file's header, else None."""
    head = text.lstrip()
    if head.startswith(CODEX_AGENTS_PREFIX):
        return "AGENTS.md"
    m = _TAG.match(head)
    return m.group(1)[:40] if m else None


def _is_injected(tag: str | None) -> bool:
    return tag == "AGENTS.md" or tag in CODEX_INJECTED_TAGS


@dataclass
class _Msg:
    line: int
    source: str                                  # "item" (event_msg/item_completed) | "copy" (response_item)
    role: str                                    # user | assistant
    phase: str | None
    window: int                                  # task window: the count of task_started lines before it
    text: str = ""                               # the kept text (injected parts removed)
    injected: list = field(default_factory=list)
    other: list = field(default_factory=list)    # non-text parts (images ...), by type
    tag: str | None = None                       # leading tag of the kept text
    matched: bool = False
    digest: str | None = None


def _codex_message(line: int, source: str, role: str, phase, window: int, parts) -> _Msg:
    msg, kept = _Msg(line, source, role, phase if isinstance(phase, str) else None, window), []
    for part in parts or []:
        ptype = str(part.get("type", "")).lower() if isinstance(part, dict) else ""
        if ptype in _TEXT_PARTS:
            text = part.get("text")
            text = text if isinstance(text, str) else ""
            tag = leading_tag(text)
            if text.strip() and _is_injected(tag):
                msg.injected.append(tag)
            elif text.strip():
                kept.append(text)
        else:
            msg.other.append(ptype or "unknown")
    msg.text = "\n\n".join(kept)
    msg.tag = leading_tag(msg.text) if msg.text else None
    # Compared with whitespace collapsed: the two representations differ in line endings and blank lines.
    msg.digest = sha256(" ".join(msg.text.split()).encode("utf-8")).hexdigest() if msg.text.strip() else None
    return msg


def _codex_speaker(msg: _Msg) -> tuple[str, str]:
    """(speaker, basis) for a kept message. A structural item is human/assistant; only a heartbeat or, for a
    response_item copy, a tag the reader does not know, is system."""
    base = "item:UserMessage" if (msg.source == "item" and msg.role == "user") else \
        f"item:AgentMessage:{msg.phase or '?'}" if msg.source == "item" else \
        "response_item:user" if msg.role == "user" else f"response_item:assistant:{msg.phase or '?'}"
    speaker = HUMAN if msg.role == "user" else ASSISTANT
    if msg.tag == CODEX_HEARTBEAT:
        return SYSTEM, f"{base}:system:heartbeat"
    if msg.source == "copy" and msg.tag and msg.tag not in CODEX_HUMAN_TAGS:
        return SYSTEM, f"{base}:system:{msg.tag}"
    return speaker, base


def parse_codex(lines: Iterable[bytes | str]) -> Parsed:
    out = Parsed("codex", "Codex", normalizer=NORMALIZER_VERSION["codex"])
    events: list[tuple] = []                     # (line, "skip", kind) | (line, "msg", _Msg), in file order
    window = 0
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
            events.append((number, "skip", f"line:{kind}"))
            continue
        if kind == "session_meta":
            out.source_id = out.source_id or payload.get("id")
            continue
        sub = payload.get("type")
        if kind == "event_msg" and sub == "item_completed":
            item = payload.get("item") or {}
            itype = item.get("type")
            if itype in ("UserMessage", "AgentMessage"):
                role = "user" if itype == "UserMessage" else "assistant"
                events.append((number, "msg", _codex_message(number, "item", role, item.get("phase"), window,
                                                             item.get("content"))))
            else:
                events.append((number, "skip", f"item:{itype}"))
            continue
        if kind == "event_msg" and sub == "task_started":
            window += 1
        if kind == "response_item" and sub == "message":
            role = payload.get("role")
            if role in ("user", "assistant"):
                events.append((number, "msg", _codex_message(number, "copy", role, payload.get("phase"), window,
                                                             payload.get("content"))))
            else:
                events.append((number, "skip", f"injected:{str(role)[:20]}"))       # developer-role instructions
            continue
        events.append((number, "skip", f"{kind}:{sub}" if sub else f"line:{kind}"))

    _reconcile([e[2] for e in events if e[1] == "msg"])
    for number, what, value in events:
        if what == "skip":
            out.skip(value, number)
            continue
        msg = value
        if msg.source == "copy" and (msg.matched or not (msg.text.strip() or msg.injected)):
            out.skip("response_item:message", number)              # a copy of a turn already taken, or an empty one
            continue
        for tag in msg.injected:
            out.skip(f"injected:{tag}", number)
        for ptype in msg.other:
            out.skip(ptype, number)
        if msg.text.strip():
            speaker, basis = _codex_speaker(msg)
            out.add(speaker, msg.text, number, basis)
    return out


def _reconcile(messages: list[_Msg]) -> None:
    """Mark each ``response_item`` copy that an item already covers (same role and text; first within the same
    task window, then anywhere in the file). What stays unmatched is a turn only the copy carries."""
    items = [m for m in messages if m.source == "item" and m.digest]
    free_window, free_any = defaultdict(list), defaultdict(list)
    for m in items:
        free_window[(m.window, m.role, m.digest)].append(m)
        free_any[(m.role, m.digest)].append(m)

    def take(bucket: list) -> bool:
        while bucket and bucket[0].matched:
            bucket.pop(0)
        if not bucket:
            return False
        bucket.pop(0).matched = True
        return True

    copies = [m for m in messages if m.source == "copy" and m.digest]
    unmatched = []
    for m in copies:
        if take(free_window[(m.window, m.role, m.digest)]):
            m.matched = True
        else:
            unmatched.append(m)
    for m in unmatched:
        if take(free_any[(m.role, m.digest)]):
            m.matched = True


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


__all__ = ["Turn", "Parsed", "parse_claude_code", "parse_codex", "designation", "HUMAN", "ASSISTANT", "SYSTEM",
           "NORMALIZER_VERSION", "DEFAULT_NORMALIZER", "CODEX_INJECTED_TAGS", "leading_tag"]
