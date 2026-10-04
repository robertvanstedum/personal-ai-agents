"""The manual-submission inbox, ``~/minimoi-inbox/`` (v0.5 B7, amendment R2/R3).

Accepts, at the top level of the folder:

* a **claude.ai export**: either the older single zip (``conversations.json`` at its
  root) or the newer manifest plus category zips ``<category>-<NNN>.zip``. Categories:
  ``conversations`` is imported; ``light_metadata`` (login history, account data) is
  **excluded whole** (``account_metadata``) and left in place; ``memories``,
  ``projects`` and ``frames`` are **held** (``unsupported_kind``: listed, left
  untouched, not refused, so a later version can import them); the manifest JSON is
  ignored (``export_manifest``: it carries expiring download links). Speakers come from
  each message's ``sender``: a source-identified format. Any other shape is refused
  whole (``unknown_schema``); nothing is guessed. Conversations are trees: see
  ``branches.py`` (main line first, every other message kept as a branch);
* a **Grok (xAI) account export**: a ``<uuid>.zip`` recognised by its ``prod-grok-backend.json``
  member. Its conversations import (speakers from ``sender`` over a closed set; a conversation with
  any other sender is refused, ``unknown_sender``; one with nothing to say is skipped, ``empty``); its two
  account files are excluded (``account_metadata``, never opened); asset files, projects, tasks and
  media_posts are held (``unsupported_kind``, counted; the zip is kept whole under ``_processed/``);
* a **pasted transcript** (``.txt``/``.md``): turn boundaries are found by speaker
  labels (heuristic identity). Text before the first label is kept as an
  unattributed ``system`` turn, never dropped. A marker in a paste never designates:
  it becomes a *candidate* for ``moi review``;
* a **canary** file (see ``canary.py``).

**Approval gate (fail closed).** ``moi dry-run inbox`` lists every file (name, size,
sha256, kind, category, decision, and for conversation exports counts and a date
range only: never a title, name, summary or any text). ``moi approve-source inbox``
binds an approval to the **exact set of (name, sha256) pairs** in that listing;
``moi inbox`` processes nothing unless the files present now are exactly that set. A
new, changed or renamed file makes the approval ``stale`` until a new dry run is
approved; each file's sha256 is checked again at the moment it is processed. The
canary is the one exemption: its file must equal the fixed synthetic conversation
byte for byte in content (``canary.valid``), so it can carry nothing real; it keeps
travelling without an approval.

Anything else, or anything unparseable, is **refused visibly**: moved to
``_refused/`` with a row in ``_refused/listing.jsonl`` (name, reason code, size,
sha256: no content) and counted in the ledger. Processed files move to
``_processed/<date>/``; **nothing is ever deleted**. A file marked private
(``selection.private_marked``) or matching ``never_copy`` is excluded whole.
Conversations are normalized (scrubbed, D8), staged in the outbox and applied to
the shelf; one export carries many conversations and each is matched to its record
**only by its exact conversation uuid** (a later export adds an edition, a changed
conversation never becomes a duplicate record). ``conversations.json`` is read as a
stream, one conversation at a time (it is tens of MB and grows).
"""
from __future__ import annotations

import codecs
import hashlib
import json
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.memory_shelf import approvals, branches
from core.memory_shelf import canary as canary_mod
from core.memory_shelf import codes, fsio, ledger, render, selection, sessions
from core.memory_shelf import bundle as bundles
from core.memory_shelf.config import never_copied
from core.memory_shelf.sessions import ASSISTANT, HUMAN, SYSTEM, Parsed

MAX_EXPORT_BYTES = 256 * 1024 * 1024      # conversations.json, uncompressed
MAX_PASTE_BYTES = 64 * 1024 * 1024
EXPORT_SENDERS = {"human": HUMAN, "assistant": ASSISTANT}
_LABEL = re.compile(r"^\s*(?:\*\*)?(human|user|you|me|robert|assistant|claude|chatgpt|gpt|grok|codex|ai)"
                    r"(?:\s+(?:said|responded|wrote))?(?:\*\*)?\s*:\s*(.*)$", re.IGNORECASE)
_HUMAN_LABELS = {"human", "user", "you", "me", "robert"}
_CHAIR_FOR = {"claude": "Claude", "grok": "Grok", "codex": "Codex"}


class Refusal(Exception):
    """An inbox file that cannot be taken. ``code`` is one of the fixed refusal codes."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class Item:
    parsed: Parsed
    source_sha256: str
    size: int
    key: str
    title: str
    created: str
    member: str | None = None
    kind: str = "session"
    skip: str | None = None                     # a conversation that is not taken: its reason code
    skip_outcome: str = codes.EXCLUDED          # ... and how the ledger counts it (excluded, or refused)


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")



# ── claude.ai conversations ───────────────────────────────────────────────────

SOURCE = "inbox"
CATEGORY_ZIP = re.compile(r"^(conversations|memories|projects|frames|light_metadata)-\d+\.zip$")
HELD_CATEGORIES = ("memories", "projects", "frames")


def _cap(text: object, limit: int = 40) -> str:
    return render.one_line(str(text) if text is not None else "?", limit) or "?"


def _message_text(msg: dict, parsed: Parsed, line: int) -> str:
    """One message's text. Content blocks are authoritative; ``text`` is used only when there are none.

    ``text`` blocks are kept verbatim; thinking, tool calls and results, injected prompt blocks, files and
    **attachments** become one-line pointers (the reference the source gave: name, type, size). An attachment's
    extracted text is **not** copied into the dialogue (owner boundary, 4 Oct 2026: documents would swamp useful memory;
    the original stays where it is); the characters left out are counted. Text the owner typed or pasted into the
    message itself is dialogue and is kept verbatim like any other.
    """
    content, text = msg.get("content"), msg.get("text")
    if isinstance(content, list) and content:
        parts = []
        for block in content:
            kind = block.get("type") if isinstance(block, dict) else None
            if kind == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif kind == "thinking":
                parsed.skip("thinking", line)
            elif kind == "tool_use":
                parsed.skip(f"tool_use:{_cap(block.get('name'))}", line)
            elif kind == "tool_result":
                parsed.skip(f"tool_result:{_cap(block.get('name'))}", line)
            elif kind == "injected_prompt_block":
                parsed.skip("injected_prompt_block", line)
            else:
                parsed.skip(f"content:{_cap(kind or 'unknown')}", line)
        body = "\n\n".join(parts)
    else:
        body = text if isinstance(text, str) else ""
    attachments = msg.get("attachments")
    if attachments is not None and not isinstance(attachments, list):
        parsed.skip("attachments", line)
        attachments = []
    for att in attachments or []:
        parsed.manifest["attachments"] += 1
        extracted = att.get("extracted_content") if isinstance(att, dict) else None
        name = att.get("file_name") if isinstance(att, dict) and isinstance(att.get("file_name"), str) else None
        if isinstance(extracted, str) and extracted.strip():
            ftype = att.get("file_type") if isinstance(att.get("file_type"), str) and att["file_type"] else "unknown type"
            size = att.get("file_size")
            size_text = f"{int(size)} bytes" if isinstance(size, (int, float)) and not isinstance(size, bool) else "size unknown"
            parsed.manifest["attachment_chars_excluded"] = parsed.manifest.get("attachment_chars_excluded", 0) + len(extracted)
            parsed.skip("attachment_content_excluded", line, f"{name or 'unnamed'} ({ftype}, {size_text})")
        else:
            parsed.skip("attachment_no_text", line, name)
    files = msg.get("files")
    if files is not None and not isinstance(files, list):
        parsed.skip("files", line)
        files = []
    for entry in files or []:
        name = entry.get("file_name") if isinstance(entry, dict) and isinstance(entry.get("file_name"), str) else None
        parsed.skip("file", line, name)
    return body if body.strip() else ""


def check_conversation(conv: object) -> dict:
    """The shape every conversation must have; one unknown shape refuses the whole file (never guessed)."""
    if not (isinstance(conv, dict) and isinstance(conv.get("uuid"), str) and conv["uuid"]
            and isinstance(conv.get("chat_messages"), list)):
        raise Refusal(codes.UNKNOWN_SCHEMA)
    for msg in conv["chat_messages"]:
        if not (isinstance(msg, dict) and msg.get("sender") in EXPORT_SENDERS
                and (isinstance(msg.get("text"), str) or isinstance(msg.get("content"), list))):
            raise Refusal(codes.UNKNOWN_SCHEMA)
    return conv


def render_tree(parsed: Parsed, main, segments, add) -> None:
    """Emit the main line, then every branch segment under its heading (``branches.py``).

    ``add(index, branch_number)`` appends the turns of one message. A heading says which turn the
    branch leaves from (the nearest turn at or above the fork) and how many messages it holds.
    """
    parsed.manifest.update({"branches": len(segments), "branch_messages": sum(len(s.indexes) for s in segments)})
    for reason in (codes.ORPHAN_PARENT, codes.EXTRA_ROOT):
        count = sum(1 for s in segments if s.reason == reason)
        if count:
            parsed.manifest[reason] = count
    turn_at: dict[int, int] = {}                    # message index -> ordinal of the nearest turn at or above it

    def emit(path, last: int, branch_no: int) -> None:
        for idx in path:
            before = len(parsed.turns)
            parsed.lines = idx + 1
            add(idx, branch_no)
            if len(parsed.turns) > before:
                last = len(parsed.turns)
            turn_at[idx] = last
    emit(main, 0, 0)
    for number, seg in enumerate(segments, 1):
        count = len(seg.indexes)
        if seg.reason == codes.ORPHAN_PARENT:
            parsed.mark(f"=== Orphan branch, parent message not in the export ({count} messages) ===")
        elif seg.reason == codes.EXTRA_ROOT:
            parsed.mark(f"=== Additional conversation root ({count} messages) ===")
        else:
            parsed.mark(f"=== Branch from turn {turn_at.get(seg.fork, 0)} ({count} messages) ===")
        emit(seg.indexes, turn_at.get(seg.fork, 0) if seg.fork is not None else 0, number)


def parse_conversation(conv: dict) -> Item:
    """One validated conversation to turns: the main line in order, then every other branch (``branches.py``)."""
    msgs = conv["chat_messages"]
    main, segments = branches.plan(msgs)
    parsed = Parsed("claude-ai", "Claude", source_id=conv["uuid"], started=conv.get("created_at"), identity="source",
                    normalizer=sessions.NORMALIZER_VERSION["claude-ai"])    # 2: attachment text is a reference, not dialogue
    parsed.manifest.update({"attachments": 0})

    def add(idx: int, branch_no: int) -> None:
        msg = msgs[idx]
        basis = f"sender:{msg['sender']}" + (f",branch:{branch_no}" if branch_no else "")
        parsed.add(EXPORT_SENDERS[msg["sender"]], _message_text(msg, parsed, idx + 1), idx + 1, basis)
    render_tree(parsed, main, segments, add)
    # the hash covers the conversation's content, not export-run metadata such as updated_at
    core = {k: conv.get(k) for k in ("uuid", "name", "created_at", "chat_messages")}
    raw = _canonical(core)
    name = conv.get("name") if isinstance(conv.get("name"), str) and conv["name"].strip() else "claude-ai conversation"
    return Item(parsed, hashlib.sha256(raw).hexdigest(), len(raw), f"claude-ai:{conv['uuid']}", name,
                bundles.utc(conv.get("created_at")), member=conv["uuid"])


class NotAList(ValueError):
    """The JSON is valid but is not the array (or object) the format needs."""


class _Json:
    """Incremental JSON reading over ``read(n) -> str``: one value at a time, a chunk of lookahead."""

    def __init__(self, read, chunk: int = 1 << 20):
        self.read, self.chunk = read, chunk
        self.buf, self.pos, self.eof = "", 0, False
        self.decoder = json.JSONDecoder()

    def more(self, size: int) -> bool:
        data = self.read(size)
        if not data:
            self.eof = True
            return False
        self.buf, self.pos = self.buf[self.pos:] + data, 0
        return True

    def peek(self) -> str:
        """The next non-space character ("" at the end of the input)."""
        while True:
            while self.pos < len(self.buf) and self.buf[self.pos] in " \t\r\n":
                self.pos += 1
            if self.pos < len(self.buf):
                return self.buf[self.pos]
            if not self.more(self.chunk):
                return ""

    def not_structured(self) -> None:
        """The input does not start with the expected bracket: malformed -> ValueError, valid -> NotAList."""
        pieces, total = [self.buf[self.pos:]], len(self.buf) - self.pos
        while True:
            data = self.read(self.chunk)
            if not data:
                break
            pieces.append(data)
            total += len(data)
            if total > MAX_EXPORT_BYTES:
                raise ValueError("too large")
        json.loads("".join(pieces))
        raise NotAList

    def value(self):
        """Decode one complete value at the cursor (growing the read-ahead until it is whole)."""
        if self.peek() == "":
            raise ValueError("truncated")
        size = self.chunk
        while True:
            try:
                item, end = self.decoder.raw_decode(self.buf, self.pos)
                if end < len(self.buf) or self.eof:
                    break                         # a number at the very end of the buffer may be cut short
            except json.JSONDecodeError:
                if self.eof:
                    raise ValueError("malformed") from None
            self.more(size)
            size *= 2
        self.pos = end
        return item

    def array(self):
        """Yield the items of the array at the cursor."""
        self.pos += 1                             # the "["
        if self.peek() == "]":
            self.pos += 1
            return
        while True:
            yield self.value()
            sep = self.peek()
            self.pos += 1
            if sep == "]":
                return
            if sep != ",":
                raise ValueError("malformed")

    def finish(self) -> None:
        if self.peek() != "":
            raise ValueError("trailing data")


def iter_json_array(read, *, chunk: int = 1 << 20):
    """Yield the items of a top-level JSON array from ``read(n) -> str``, one at a time (bounded memory).

    Only one item (plus a chunk) is ever held, so a 74 MB ``conversations.json`` costs about one
    conversation of memory. Raises ``ValueError`` for malformed JSON and ``NotAList`` otherwise.
    """
    reader = _Json(read, chunk)
    first = reader.peek()
    if first == "":
        raise ValueError("empty")
    if first != "[":
        reader.not_structured()
    yield from reader.array()
    reader.finish()


def iter_json_object(read, stream_key: str, *, chunk: int = 1 << 20):
    """Walk a top-level JSON object. The array under ``stream_key`` is yielded item by item as
    ``("item", obj)`` (after one ``("start", key)``); every other key is decoded whole and yielded as ``("value", key, obj)``
    (the other keys are small in the formats that use this). ``NotAList`` if it is not an object."""
    reader = _Json(read, chunk)
    first = reader.peek()
    if first == "":
        raise ValueError("empty")
    if first != "{":
        reader.not_structured()
    reader.pos += 1
    if reader.peek() == "}":
        reader.pos += 1
    else:
        while True:
            if reader.peek() != '"':
                raise ValueError("malformed")
            key = reader.value()
            if reader.peek() != ":":
                raise ValueError("malformed")
            reader.pos += 1
            if key == stream_key and reader.peek() == "[":
                yield "start", key
                for item in reader.array():
                    yield "item", item
            else:
                yield "value", key, reader.value()
            sep = reader.peek()
            reader.pos += 1
            if sep == "}":
                break
            if sep != ",":
                raise ValueError("malformed")
    reader.finish()


class _Capped:
    """Text reader over a binary handle that refuses to read more than ``limit`` bytes (a zip header can lie)."""

    def __init__(self, handle, limit: int):
        self.handle, self.limit, self.total = handle, limit, 0
        self.decoder = codecs.getincrementaldecoder("utf-8-sig")()

    def __call__(self, size: int) -> str:
        raw = self.handle.read(size)
        self.total += len(raw)
        if self.total > self.limit:
            raise Refusal(codes.ZIP_UNSAFE)
        return self.decoder.decode(raw, final=not raw)


def _export_member(zf: zipfile.ZipFile) -> zipfile.ZipInfo:
    members = [i for i in zf.infolist() if not i.is_dir()
               and i.filename.replace("\\", "/").split("/")[-1] == "conversations.json"
               and i.filename.count("/") <= 1 and ".." not in i.filename.split("/") and not i.filename.startswith("/")]
    if not members:
        raise Refusal(codes.UNKNOWN_SCHEMA)
    if members[0].file_size > MAX_EXPORT_BYTES:
        raise Refusal(codes.TOO_LARGE)
    return members[0]


def stream_conversations(path: Path):
    """Yield the raw conversations of an export (a zip with ``conversations.json``, or the file itself).

    Opened afresh each call, so a second pass costs a second read, not memory. Refusals (``Refusal``)
    are raised as the stream reaches them; callers that need the whole file judged first run a
    validation pass (``load_items``).
    """
    try:
        if path.name.endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                info = _export_member(zf)
                with zf.open(info) as handle:
                    yield from _stream(_Capped(handle, MAX_EXPORT_BYTES))
        else:
            if path.stat().st_size > MAX_EXPORT_BYTES:
                raise Refusal(codes.TOO_LARGE)
            with open(path, "rb") as handle:
                yield from _stream(_Capped(handle, MAX_EXPORT_BYTES))
    except zipfile.BadZipFile:
        raise Refusal(codes.UNPARSEABLE) from None


def _stream(read):
    try:
        yield from iter_json_array(read)
    except NotAList:
        raise Refusal(codes.UNKNOWN_SCHEMA) from None
    except (ValueError, UnicodeDecodeError):
        raise Refusal(codes.UNPARSEABLE) from None


def validate_export(path: Path) -> int:
    """Judge the whole export before anything is taken: returns the conversation count or raises ``Refusal``."""
    count = 0
    for conv in stream_conversations(path):
        check_conversation(conv)
        count += 1
    if not count:
        raise Refusal(codes.NO_TURNS)
    return count


def parse_claude_ai_export(data: object) -> list[Item]:
    """In-memory form (tests and small exports): the same validation and parsing as the streamed path."""
    if not isinstance(data, list):
        raise Refusal(codes.UNKNOWN_SCHEMA)
    if not data:
        raise Refusal(codes.NO_TURNS)
    return [parse_conversation(check_conversation(conv)) for conv in data]


def parse_paste(raw: bytes, *, name: str, fallback: datetime) -> Item:
    """Heuristic turn boundaries from speaker labels. Fewer than two speakers is not a transcript."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Refusal(codes.NOT_UTF8) from None
    sha = hashlib.sha256(raw).hexdigest()
    parsed = Parsed("paste", "Robert", source_id=sha, identity="heuristic")
    current: tuple[str, list[str], int] | None = None
    preamble: list[str] = []
    assistant_label = None
    labelled = []

    def close():
        if current:
            labelled.append(current)
    for number, line in enumerate(text.splitlines(), 1):
        parsed.lines = number
        m = _LABEL.match(line)
        if m:
            close()
            label = m.group(1).lower()
            if label not in _HUMAN_LABELS and assistant_label is None:
                assistant_label = label
            current = (HUMAN if label in _HUMAN_LABELS else ASSISTANT, [m.group(2)], number)
        elif current:
            current[1].append(line)
        else:
            preamble.append(line)
    close()
    if {s for s, _, _ in labelled} != {HUMAN, ASSISTANT}:
        raise Refusal(codes.UNPARSEABLE)
    if any(l.strip() for l in preamble):
        parsed.add(SYSTEM, "\n".join(preamble).strip("\n"), 1, "paste:unattributed")
    for speaker, lines, number in labelled:
        parsed.add(speaker, "\n".join(lines).strip("\n"), number, f"label:{speaker}")
    if not parsed.turns:
        raise Refusal(codes.NO_TURNS)
    parsed.chair = _CHAIR_FOR.get(assistant_label or "", "Robert")
    stem = Path(name).stem
    return Item(parsed, sha, len(raw), f"paste:{sha}", stem, bundles.utc(None, fallback))


def parse_canary(raw: bytes) -> Item:
    try:
        doc = json.loads(raw)
    except ValueError:
        raise Refusal(codes.UNPARSEABLE) from None
    if not canary_mod.valid(doc):
        raise Refusal(codes.UNKNOWN_SCHEMA)
    parsed = Parsed("canary", "Claude Code", source_id=doc["id"], identity="source",
                    started=doc["id"].removeprefix("canary-") + "T00:00:00Z")
    for number, turn in enumerate(doc["turns"], 1):
        parsed.lines = number
        parsed.add(turn["speaker"], turn["text"], number, f"canary:{turn['speaker']}")
    return Item(parsed, hashlib.sha256(raw).hexdigest(), len(raw), f"canary:{doc['id']}", doc["id"],
                bundles.utc(parsed.started), kind="canary")




# ── Grok (xAI) account export ─────────────────────────────────────────────────
#
# A zip named <uuid>.zip whose members sit under ttl/<n>d/export_data/<uuid>/. Recognised by the
# ``prod-grok-backend.json`` member (a dict: conversations, projects, tasks, media_posts); anything else
# in the zip is not a conversation: the two account files are excluded (``account_metadata``) and never
# opened, the asset-server files, projects, tasks and media_posts are held (``unsupported_kind``).

GROK_BACKEND = "prod-grok-backend.json"
GROK_ACCOUNT_FILES = frozenset({"prod-mc-auth-mgmt-api.json", "prod-mc-billing.json"})
GROK_ASSET_DIR = "prod-mc-asset-server"
GROK_HELD_KEYS = ("projects", "tasks", "media_posts")


def grok_speaker(sender: object) -> str | None:
    """From the format only: ``human``; ``assistant``, ``model`` and ``grok-*`` (any case) are Grok. Else None."""
    if not isinstance(sender, str):
        return None
    low = sender.strip().lower()
    if low == "human":
        return HUMAN
    if low in ("assistant", "model") or (low.startswith("grok-") and len(low) > 5):
        return ASSISTANT
    return None


def _basename(name: str) -> str:
    return name.replace("\\", "/").rstrip("/").split("/")[-1]


def _safe_member(info: zipfile.ZipInfo) -> bool:
    return not info.is_dir() and ".." not in info.filename.split("/") and not info.filename.startswith("/")


def is_grok_zip(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            return any(_safe_member(i) and _basename(i.filename) == GROK_BACKEND for i in zf.infolist())
    except (zipfile.BadZipFile, OSError):
        return False


def grok_layout(path: Path) -> dict:
    """Counts of the zip's non-conversation members, from its directory only (no member is opened)."""
    assets = account = other = 0
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if not _safe_member(info):
                continue
            base, parts = _basename(info.filename), info.filename.split("/")
            if base == GROK_BACKEND:
                continue
            if base in GROK_ACCOUNT_FILES:
                account += 1
            elif GROK_ASSET_DIR in parts:
                assets += 1
            else:
                other += 1
    return {"assets": assets, "account_files": account, "other_members": other}


def _grok_stream(read):
    try:
        yield from iter_json_object(read, "conversations")
    except NotAList:
        raise Refusal(codes.UNKNOWN_SCHEMA) from None
    except (ValueError, UnicodeDecodeError):
        raise Refusal(codes.UNPARSEABLE) from None


def stream_grok(path: Path):
    """Yield ``("conversation", conv)`` for each conversation and ``("held", key, count)`` for projects, tasks,
    media_posts and any other top-level key. A backend without a ``conversations`` array is ``unknown_schema``."""
    seen = False
    try:
        with zipfile.ZipFile(path) as zf:
            infos = [i for i in zf.infolist() if _safe_member(i) and _basename(i.filename) == GROK_BACKEND]
            if not infos:
                raise Refusal(codes.UNKNOWN_SCHEMA)
            if infos[0].file_size > MAX_EXPORT_BYTES:
                raise Refusal(codes.TOO_LARGE)
            with zf.open(infos[0]) as handle:
                for event in _grok_stream(_Capped(handle, MAX_EXPORT_BYTES)):
                    if event[0] == "start":
                        seen = True
                    elif event[0] == "item":
                        yield "conversation", event[1]
                    elif event[1] == "conversations":
                        raise Refusal(codes.UNKNOWN_SCHEMA)                # present but not an array
                    else:
                        yield "held", str(event[1])[:40], len(event[2]) if isinstance(event[2], (list, dict)) else 1
    except zipfile.BadZipFile:
        raise Refusal(codes.UNPARSEABLE) from None
    if not seen:
        raise Refusal(codes.UNKNOWN_SCHEMA)                                # no conversations array at all


def check_grok_conversation(conv: object) -> dict:
    """The shape each conversation must have (ids and the response list); a bad shape refuses the whole file."""
    if not (isinstance(conv, dict) and isinstance(conv.get("conversation"), dict)
            and isinstance(conv["conversation"].get("id"), str) and conv["conversation"]["id"]
            and isinstance(conv.get("responses"), list)):
        raise Refusal(codes.UNKNOWN_SCHEMA)
    for entry in conv["responses"]:
        if not (isinstance(entry, dict) and isinstance(entry.get("response"), dict)
                and isinstance(entry["response"].get("_id"), str) and entry["response"]["_id"]):
            raise Refusal(codes.UNKNOWN_SCHEMA)
    return conv


def _grok_nodes(conv: dict) -> tuple[list[dict], list[dict], int | None]:
    """(responses, tree nodes for ``branches.plan``, index of the named leaf if it resolves)."""
    responses = [e["response"] for e in conv["responses"]]
    linked = any("parent_response_id" in r for r in responses)
    nodes = []
    for r in responses:
        node = {"uuid": r["_id"], "created_at": r.get("create_time")}
        if linked:
            node["parent_message_uuid"] = r.get("parent_response_id")
        nodes.append(node)
    leaf_id = conv["conversation"].get("leaf_response_id")
    leaf = next((i for i, r in enumerate(responses) if r["_id"] == leaf_id), None) if isinstance(leaf_id, str) else None
    return responses, nodes, leaf


def _count(value: object) -> int:
    return len(value) if isinstance(value, (list, dict)) else (1 if value else 0)


def _grok_pointers(r: dict, parsed: Parsed, line: int) -> None:
    """One-line pointers for what is not the owner's or Grok's words: counts and lengths only, never content."""
    if r.get("thinking_trace"):
        parsed.skip("thinking_trace", line)
    if r.get("agent_thinking_traces"):
        parsed.skip("agent_thinking_traces", line, f"{_count(r['agent_thinking_traces'])} items")
    for key, unit in (("steps", "items"), ("web_search_results", "results"), ("cited_web_search_results", "results")):
        if r.get(key):
            parsed.skip(key, line, f"{_count(r[key])} {unit}")
    if r.get("card_attachments_json"):
        card = r["card_attachments_json"]
        parsed.skip("card_attachments_json", line, f"{len(card)} chars" if isinstance(card, str) else f"{_count(card)} items")
    if isinstance(r.get("query"), str) and r["query"].strip():
        parsed.skip("query", line, f"{len(r['query'])} chars")                # the model-side query, not copied
    attachments = r.get("file_attachments")
    for asset in (attachments if isinstance(attachments, list) else [attachments] if attachments else []):
        parsed.manifest["file_attachments"] += 1
        parsed.skip("file_attachment", line, str(asset)[:80])
    if r.get("generated_image_urls"):
        n = _count(r["generated_image_urls"])
        parsed.manifest["generated_image_urls"] += n
        parsed.skip("generated_image_urls", line, f"{n} urls")
    if r.get("error"):
        parsed.manifest["error_responses"] += 1
    if r.get("partial"):
        parsed.manifest["partial_responses"] += 1


def parse_grok_conversation(conv: dict) -> Item:
    """One Grok conversation to turns (``message`` verbatim). A conversation with a sender outside the closed
    set is refused (``unknown_sender``) and one with nothing to say is skipped (``empty``): both as items that
    say so, never silently."""
    head = conv["conversation"]
    responses, nodes, leaf = _grok_nodes(conv)
    cid = head["id"]
    title = head.get("title") if isinstance(head.get("title"), str) and head["title"].strip() else "grok conversation"
    core = {"id": cid, "title": head.get("title"), "create_time": head.get("create_time"),
            "responses": [{"id": r["_id"], "parent": r.get("parent_response_id"), "sender": r.get("sender"),
                           "message": r.get("message")} for r in responses]}
    raw = _canonical(core)
    created = bundles.utc(head.get("create_time"))
    parsed = Parsed("grok", "Grok", source_id=cid, started=head.get("create_time") if isinstance(head.get("create_time"), str) else None,
                    identity="source")
    sha, size, key = hashlib.sha256(raw).hexdigest(), len(raw), f"grok:{cid}"
    if any(grok_speaker(r.get("sender")) is None for r in responses):
        return Item(parsed, sha, size, key, title, created, member=cid, skip=codes.UNKNOWN_SENDER, skip_outcome=codes.REFUSED)
    main, segments = branches.plan(nodes, prefer_leaf=leaf)
    parsed.manifest.update({"file_attachments": 0, "generated_image_urls": 0, "error_responses": 0, "partial_responses": 0})

    def add(idx: int, branch_no: int) -> None:
        r = responses[idx]
        message = r.get("message")
        _grok_pointers(r, parsed, idx + 1)
        if message is not None and not isinstance(message, str):
            parsed.skip("message_not_text", idx + 1)
            message = ""
        basis = f"sender:{str(r.get('sender')).strip().lower()}" + (f",branch:{branch_no}" if branch_no else "")
        parsed.add(grok_speaker(r.get("sender")), message or "", idx + 1, basis)
    render_tree(parsed, main, segments, add)
    return Item(parsed, sha, size, key, title, created, member=cid, skip=None if parsed.turns else codes.EMPTY)


class ItemStream:
    """Items produced lazily, plus ``parts``: what the file held that is not a conversation (counted, not read)."""

    def __init__(self, items, parts: dict | None = None):
        self._items, self.parts = items, parts or {}

    def __iter__(self):
        return iter(self._items)


def validate_grok(path: Path) -> tuple[int, dict]:
    """Judge a Grok export whole before taking anything: ``(conversation count, parts)`` or ``Refusal``."""
    count, held = 0, {}
    for event in stream_grok(path):
        if event[0] == "conversation":
            check_grok_conversation(event[1])
            count += 1
        else:
            held[event[1]] = held.get(event[1], 0) + event[2]
    if not count:
        raise Refusal(codes.NO_TURNS)
    layout = grok_layout(path)
    held = {k: v for k, v in {"assets": layout["assets"], **held, "other_members": layout["other_members"]}.items() if v}
    return count, {"excluded": {codes.ACCOUNT_METADATA: layout["account_files"]} if layout["account_files"] else {},
                   "held": held}


def _grok_items(path: Path, only: str | None):
    for event in stream_grok(path):
        if event[0] != "conversation":
            continue
        conv = event[1]
        if only is not None and not (isinstance(conv, dict) and isinstance(conv.get("conversation"), dict)
                                     and conv["conversation"].get("id") == only):
            continue
        yield parse_grok_conversation(check_grok_conversation(conv))


def grok_stats(path: Path) -> dict:
    """Counts and a date range for a Grok export. Never a title, summary or any text."""
    convs = resp = branched = empty = unknown = files = 0
    first = last = None
    held: dict[str, int] = {}
    for event in stream_grok(path):
        if event[0] == "held":
            held[event[1]] = held.get(event[1], 0) + event[2]
            continue
        conv = check_grok_conversation(event[1])
        convs += 1
        responses, nodes, leaf = _grok_nodes(conv)
        resp += len(responses)
        if branches.plan(nodes, prefer_leaf=leaf)[1]:
            branched += 1
        if any(grok_speaker(r.get("sender")) is None for r in responses):
            unknown += 1
        elif not any(isinstance(r.get("message"), str) and r["message"].strip() for r in responses):
            empty += 1
        for r in responses:
            files += _count(r.get("file_attachments"))
        day = _day(conv["conversation"].get("create_time"))
        if day:
            first, last = min(first or day, day), max(last or day, day)
    layout = grok_layout(path)
    return {"conversations": convs, "messages": resp, "created_first": first, "created_last": last,
            "conversations_with_branches": branched, "empty_conversations": empty, "unknown_sender_conversations": unknown,
            "file_attachments": files,
            "held": {k: v for k, v in {"assets": layout["assets"], **held, "other_members": layout["other_members"]}.items() if v},
            "excluded": {codes.ACCOUNT_METADATA: layout["account_files"]} if layout["account_files"] else {}}


def classify(name: str, path: Path | None = None) -> tuple[str, str | None]:
    """``(kind, category)`` from the file name; for a zip with an unlisted name, ``path`` lets the zip's own
    directory (never a member's content) tell a Grok export from the older claude.ai single zip."""
    lower = name.lower()
    if name.endswith(".canary.json"):
        return "canary", None
    m = CATEGORY_ZIP.match(name)
    if m:
        return "claude-ai-export", m.group(1)
    if lower.endswith(".zip"):
        if path is not None and is_grok_zip(path):
            return "grok-export", None
        return "claude-ai-export", "conversations"              # the older single zip
    if name == "conversations.json":
        return "claude-ai-export", "conversations"
    if lower.endswith(".json") and "manifest" in lower:
        return "export-manifest", None
    if lower.endswith((".txt", ".md", ".text")):
        return "paste", None
    return "other", None


def load_items(path: Path, fallback: datetime, only: str | None = None):
    """Items for one inbox file. Exports are validated whole first (eagerly, so a refusal is raised here),
    then parsed lazily one conversation at a time. ``only`` limits a re-read to one conversation uuid."""
    name, size = path.name, path.stat().st_size
    if size == 0:
        raise Refusal(codes.EMPTY)
    kind, _ = classify(name, path)
    if kind == "canary":
        return [parse_canary(path.read_bytes())]
    if kind == "grok-export":
        if only is None:
            count, parts = validate_grok(path)
            return ItemStream(_grok_items(path, None), parts)
        return ItemStream(_grok_items(path, only))
    if kind == "claude-ai-export":
        if only is None:
            validate_export(path)
        return _export_items(path, only)
    if kind == "paste":
        if size > MAX_PASTE_BYTES:
            raise Refusal(codes.TOO_LARGE)
        return [parse_paste(path.read_bytes(), name=name, fallback=fallback)]
    raise Refusal(codes.UNSUPPORTED_TYPE)


def _export_items(path: Path, only: str | None):
    for conv in stream_conversations(path):
        if only is not None and not (isinstance(conv, dict) and conv.get("uuid") == only):
            continue
        yield parse_conversation(check_conversation(conv))


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _unique(folder: Path, name: str) -> Path:
    target, n = folder / name, 0
    while target.exists():
        n += 1
        target = folder / f"{name}.{n}"
    return target


def _move(src: Path, folder: Path) -> Path:
    fsio.ensure_dir(folder)
    target = _unique(folder, src.name)
    os.replace(src, target)
    return target


def refuse(shelf, inbox_root: Path, path: Path, code: str, now: datetime, *, outcome: str = codes.REFUSED) -> None:
    """Move to ``_refused/``, list it (code and hashes only), count it in the ledger."""
    sha, size = _sha_file(path), path.stat().st_size
    name = path.name
    _move(path, inbox_root / "_refused")
    fsio.append_jsonl(inbox_root / "_refused" / "listing.jsonl",
                      {"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "name": name, "reason": code, "bytes": size,
                       "sha256": sha})
    ledger.record(shelf, "inbox", bundles.ledger_key_for("inbox", f"file:{sha}:{name}"), outcome, reason=code, now=now)


def refused_listing(inbox_root: Path) -> list[dict]:
    return fsio.read_jsonl(Path(inbox_root) / "_refused" / "listing.jsonl")




# ── the approval gate and the dry run ─────────────────────────────────────────

@dataclass
class Entry:
    path: Path
    name: str
    size: int
    kind: str
    category: str | None
    sha: str | None                      # None when never_copy says the file is not even hashed


def candidates(inbox_root: Path) -> list[Path]:
    if not inbox_root.is_dir():
        return []
    return [p for p in sorted(inbox_root.iterdir())
            if not p.name.startswith(("_", ".")) and not p.is_symlink() and p.is_file()]


def scan(inbox_root: str | Path, never_copy: tuple[str, ...] = ()) -> list[Entry]:
    entries = []
    for path in candidates(Path(inbox_root)):
        kind, category = classify(path.name, path)
        sha = None if never_copied(path.name, never_copy) else _sha_file(path)
        entries.append(Entry(path, path.name, path.stat().st_size, kind, category, sha))
    return entries


def approved_pairs(entries: list[Entry]) -> frozenset[tuple[str, str]]:
    """The (name, sha256) pairs an approval covers: every hashed file except the canary exemption."""
    return frozenset((e.name, e.sha) for e in entries if e.sha and e.kind != "canary")


def fingerprint_of(entries: list[Entry], never_copy: tuple[str, ...] = ()) -> str:
    doc = {"files": sorted(approved_pairs(entries)), "never_copy": sorted(never_copy)}
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()


def fingerprint(inbox_root: str | Path, never_copy: tuple[str, ...] = ()) -> str:
    """What ``moi approve-source inbox`` binds to: the exact (name, sha256) set now in the inbox."""
    return fingerprint_of(scan(inbox_root, never_copy), never_copy)


def decide(entry: Entry, never_copy: tuple[str, ...]) -> tuple[str, str | None]:
    """The decision for one file, from its name (and the owner's private mark), never from its content."""
    if never_copied(entry.name, never_copy):
        return "excluded", codes.NEVER_COPY
    first = selection.first_line_of(entry.path) if entry.kind == "paste" else ""
    if entry.kind != "canary" and selection.private_marked(entry.name, first):
        return "excluded", codes.PRIVATE
    if entry.size == 0:
        return "would_refuse", codes.EMPTY
    if entry.kind == "canary":
        return "canary", None
    if entry.kind == "export-manifest":
        return "ignored", codes.EXPORT_MANIFEST
    if entry.kind == "claude-ai-export" and entry.category == "light_metadata":
        return "excluded", codes.ACCOUNT_METADATA
    if entry.kind == "claude-ai-export" and entry.category in HELD_CATEGORIES:
        return "held", codes.UNSUPPORTED_KIND
    if entry.kind in ("claude-ai-export", "grok-export", "paste"):
        return "would_import", None
    return "would_refuse", codes.UNSUPPORTED_TYPE


def _day(text: object) -> str | None:
    if isinstance(text, str):
        try:
            return datetime.fromisoformat(text.strip().replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


def export_stats(path: Path) -> dict:
    """Counts and a date range for a conversation export. Never a title, name, summary or any text."""
    convs = msgs = branched = atts = att_bytes = 0
    first = last = None
    for conv in stream_conversations(path):
        check_conversation(conv)
        convs += 1
        messages = conv["chat_messages"]
        msgs += len(messages)
        if branches.plan(messages)[1]:
            branched += 1
        for msg in messages:
            for att in msg.get("attachments") or []:
                atts += 1
                size = att.get("file_size") if isinstance(att, dict) else None
                att_bytes += int(size) if isinstance(size, (int, float)) and not isinstance(size, bool) else 0
        day = _day(conv.get("created_at"))
        if day:
            first, last = min(first or day, day), max(last or day, day)
    if not convs:
        raise Refusal(codes.NO_TURNS)
    return {"conversations": convs, "messages": msgs, "created_first": first, "created_last": last,
            "conversations_with_branches": branched, "attachments": atts, "attachment_bytes": att_bytes}


def build_listing(inbox_root: str | Path, never_copy: tuple[str, ...] = (), now: datetime | None = None) -> dict:
    """Per-file metadata only: name, size, sha256, kind, category, decision, and export counts."""
    entries = scan(inbox_root, never_copy)
    rows, counts = [], {"would_import": 0, "held": {}, "excluded": {}, "ignored": 0, "would_refuse": {}, "canary": 0,
                        "files": len(entries), "bytes": 0, "conversations": 0, "messages": 0}
    for e in entries:
        decision, reason = decide(e, never_copy)
        row = {"name": e.name, "bytes": e.size, "sha256": e.sha, "kind": e.kind, "category": e.category,
               "decision": decision}
        if reason:
            row["reason"] = reason
        if decision == "would_import" and e.kind in ("claude-ai-export", "grok-export"):
            try:
                row["export"] = (grok_stats if e.kind == "grok-export" else export_stats)(e.path)
                counts["conversations"] += row["export"]["conversations"]
                counts["messages"] += row["export"]["messages"]
            except Refusal as exc:
                row["decision"], row["reason"] = "would_refuse", exc.code
            except Exception:                                    # noqa: BLE001 - fixed code only
                row["decision"], row["reason"] = "would_refuse", codes.UNPARSEABLE
        decision = row["decision"]
        if decision in ("held", "excluded", "would_refuse"):
            bucket = counts[decision]
            bucket[row["reason"]] = bucket.get(row["reason"], 0) + 1
        else:
            counts[decision] += 1
        counts["bytes"] += e.size
        rows.append(row)
    digest = hashlib.sha256("\n".join(f"{r['name']}\t{r['sha256']}" for r in rows).encode()).hexdigest()
    return {"source": SOURCE, "kind": "inbox", "fingerprint": fingerprint_of(entries, never_copy),
            "generated_at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "counts": counts, "files": rows, "listing_sha256": digest}


def dry_run(shelf, inbox_root: str | Path, never_copy: tuple[str, ...] = (), now: datetime | None = None) -> dict:
    shelf.init_layout()
    listing = build_listing(inbox_root, never_copy, now)
    fsio.write_json(approvals.dry_run_path(shelf, SOURCE), listing)
    return listing


def run(shelf, inbox_root: str | Path, *, now: datetime | None = None, settle_seconds: float = 0,
        never_copy: tuple[str, ...] = ()) -> dict:
    """The ``moi inbox`` pass: processes nothing unless an approval binds to exactly the files now present.

    Without one, only the canary (fixed synthetic content) travels; the first such run writes the
    dry-run listing for the owner. Returns ``{"status", "counts"}``.
    """
    now = now or datetime.now(timezone.utc)
    inbox_root = Path(inbox_root)
    if not inbox_root.is_dir():
        return {"status": "no_inbox", "counts": {}}
    shelf.init_layout()
    entries = scan(inbox_root, never_copy)
    fp = fingerprint_of(entries, never_copy)
    status = approvals.source_status(shelf, SOURCE, fp)
    if status == approvals.APPROVED:
        return process(shelf, inbox_root, now=now, settle_seconds=settle_seconds, never_copy=never_copy,
                       approved=approved_pairs(entries))
    listing = fsio.read_json(approvals.dry_run_path(shelf, SOURCE)) if status != approvals.NO_DRY_RUN else None
    if not isinstance(listing, dict) or listing.get("fingerprint") != fp:
        dry_run(shelf, inbox_root, never_copy, now)
        status = "dry_run_only"
    res = process(shelf, inbox_root, now=now, settle_seconds=settle_seconds, never_copy=never_copy, approved=None)
    return {"status": status, "counts": res["counts"]}


def _note(shelf, latest: dict, name: str, sha: str, outcome: str, reason: str, now: datetime) -> None:
    """A ledger row for a file that stays where it is; written once per state, not once per run."""
    key = bundles.ledger_key_for(SOURCE, f"file:{sha}:{name}")
    prev = latest.get((SOURCE, key))
    if prev and prev.get("outcome") == outcome and prev.get("reason") == reason:
        return
    ledger.record(shelf, SOURCE, key, outcome, reason=reason, now=now)


def process(shelf, inbox_root: str | Path, *, now: datetime | None = None, settle_seconds: float = 0,
            never_copy: tuple[str, ...] = (), approved: frozenset | None = None) -> dict:
    """One inbox pass over the files. Returns counts; never raises on a bad file.

    ``approved`` is the set of ``(name, sha256)`` pairs the owner approved (``run`` supplies it); ``None``
    means no approval, and then only canary files are taken. Each file's sha256 is checked here again, at
    the moment it is processed, so a file swapped after the check is not taken.
    """
    now = now or datetime.now(timezone.utc)
    inbox_root = Path(inbox_root)
    counts: dict[str, int] = {}

    def tally(key: str) -> None:
        counts[key] = counts.get(key, 0) + 1
    if not inbox_root.is_dir():
        return {"status": "no_inbox", "counts": counts}
    shelf.init_layout()
    if shelf.headroom():
        return {"status": codes.DISK_LOW, "counts": counts}
    latest = ledger.latest(shelf)
    for path in candidates(inbox_root):
        if settle_seconds and now.timestamp() - path.stat().st_mtime < settle_seconds:
            tally(codes.UNSTABLE)
            continue
        kind, category = classify(path.name, path)
        canary_file = kind == "canary"
        if never_copied(path.name, never_copy):
            if approved is None and not canary_file:
                tally(codes.NOT_APPROVED)
                continue
            refuse(shelf, inbox_root, path, codes.NEVER_COPY, now, outcome=codes.EXCLUDED)
            tally(codes.EXCLUDED)
            continue
        sha = None
        if not canary_file:
            sha = _sha_file(path) if approved is not None else None
            if approved is None or (path.name, sha) not in approved:
                tally(codes.NOT_APPROVED)
                continue
        try:
            first = selection.first_line_of(path) if kind == "paste" else ""
            if not canary_file and selection.private_marked(path.name, first):
                refuse(shelf, inbox_root, path, codes.PRIVATE, now, outcome=codes.EXCLUDED)
                tally(codes.EXCLUDED)
                continue
            if path.stat().st_size:
                stays = (
                    (codes.EXCLUDED, codes.EXPORT_MANIFEST) if kind == "export-manifest" else
                    (codes.EXCLUDED, codes.ACCOUNT_METADATA) if kind == "claude-ai-export" and category == "light_metadata" else
                    (codes.HELD, codes.UNSUPPORTED_KIND) if kind == "claude-ai-export" and category in HELD_CATEGORIES else None)
                if stays:                                   # listed and counted, left in place untouched
                    _note(shelf, latest, path.name, sha, stays[0], stays[1], now)
                    tally(stays[0])
                    continue
            fallback = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            items = load_items(path, fallback)
        except Refusal as exc:
            refuse(shelf, inbox_root, path, exc.code, now)
            tally(codes.REFUSED)
            continue
        except Exception:                                    # noqa: BLE001 - fixed code only
            refuse(shelf, inbox_root, path, codes.UNPARSEABLE, now)
            tally(codes.REFUSED)
            continue
        dest = _unique(inbox_root / "_processed" / now.strftime("%Y-%m-%d"), path.name)
        ok = True
        try:
            for item in items:
                if item.skip or not item.parsed.turns:
                    outcome = item.skip_outcome if item.skip else codes.EXCLUDED
                    ledger.record(shelf, "inbox", bundles.ledger_key_for("inbox", item.key), outcome,
                                  reason=item.skip or codes.NO_TURNS, canary=item.kind == "canary", now=now)
                    tally(outcome)
                    continue
                lkey = bundles.ledger_key_for("inbox", item.key)
                ledger.record(shelf, "inbox", lkey, codes.DISCOVERED, canary=item.kind == "canary", now=now)
                retained = {"kind": "inbox-file", "rel": dest.relative_to(inbox_root).as_posix(), "member": item.member,
                            "format": item.parsed.provider}
                bundle = bundles.from_parsed(item.parsed, item.source_sha256, item.size, key=item.key, title=item.title,
                                             origin="inbox", created=item.created, retained=retained, ledger_key=lkey,
                                             kind=item.kind)
                _path, code = shelf.stage(bundle)
                if code:
                    ok = False
                    ledger.record(shelf, "inbox", lkey, codes.DISK_LOW, canary=item.kind == "canary", now=now)
                    tally(codes.DISK_LOW)
                    continue
                result = next((r for r in shelf.drain() if r.key == bundle.key), None)
                outcome = result.outcome if result else codes.FAILED
                ok = ok and outcome in codes.OK_OUTCOMES
                tally(outcome)
        except Exception:                                    # noqa: BLE001 - the file stays where it is; fixed code only
            ok = False
            tally(codes.FAILED)
        if ok:
            parts = getattr(items, "parts", None) or {}
            for part, n in parts.get("excluded", {}).items():        # what the file held that is not a conversation
                _note(shelf, latest, f"{path.name}#{part}", sha, codes.EXCLUDED, part, now)
                tally(codes.EXCLUDED)
            for part, n in parts.get("held", {}).items():
                _note(shelf, latest, f"{path.name}#{part}", sha, codes.HELD, codes.UNSUPPORTED_KIND, now)
                tally(codes.HELD)
            fsio.ensure_dir(dest.parent)
            os.replace(path, dest)                           # moved, never deleted
    return {"status": "ok", "counts": counts}
