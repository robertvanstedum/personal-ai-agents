"""The manual-submission inbox, ``~/minimoi-inbox/`` (v0.5 B7, amendment R2/R3).

Accepts, at the top level of the folder:

* a **claude.ai export zip** (``conversations.json``): speakers come from each
  message's ``sender`` (``human`` / ``assistant``) -- a source-identified format.
  Any other shape is refused whole (``unknown_schema``); nothing is guessed;
* a **pasted transcript** (``.txt``/``.md``): turn boundaries are found by speaker
  labels (heuristic identity). Text before the first label is kept as an
  unattributed ``system`` turn, never dropped. A marker in a paste never designates:
  it becomes a *candidate* for ``moi review``;
* a **canary** file (see ``canary.py``).

Anything else, or anything unparseable, is **refused visibly**: moved to
``_refused/`` with a row in ``_refused/listing.jsonl`` (name, reason code, size,
sha256: no content) and counted in the ledger. Processed files move to
``_processed/<date>/``; **nothing is ever deleted**. A file marked private
(``selection.private_marked``) or matching ``never_copy`` is excluded whole.
Conversations are normalized (scrubbed, D8), staged in the outbox and applied to
the shelf; one export carries many conversations and each is matched to its record
**only by its exact conversation uuid** (a later export adds an edition, a changed
conversation never becomes a duplicate record).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.memory_shelf import canary as canary_mod
from core.memory_shelf import codes, fsio, ledger, selection
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


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")


def _message_text(msg: dict, parsed: Parsed, ordinal_line: int) -> str:
    text = msg.get("text")
    content = msg.get("content")
    blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
    for block in blocks:
        if block.get("type") != "text":
            parsed.skip(f"content:{str(block.get('type', 'unknown'))[:40]}", ordinal_line)
    for field_name in ("attachments", "files"):
        for _ in msg.get(field_name) or []:
            parsed.skip(field_name, ordinal_line)
    if isinstance(text, str) and text.strip():
        return text
    return "\n\n".join(b.get("text", "") for b in blocks if b.get("type") == "text" and isinstance(b.get("text"), str))


def parse_claude_ai_export(data: object) -> list[Item]:
    """Validate the whole export first; one unknown shape refuses the file (never guessed)."""
    if not isinstance(data, list):
        raise Refusal(codes.UNKNOWN_SCHEMA)
    if not data:
        raise Refusal(codes.NO_TURNS)
    for conv in data:
        if not (isinstance(conv, dict) and isinstance(conv.get("uuid"), str) and conv["uuid"]
                and isinstance(conv.get("chat_messages"), list)):
            raise Refusal(codes.UNKNOWN_SCHEMA)
        for msg in conv["chat_messages"]:
            if not (isinstance(msg, dict) and msg.get("sender") in EXPORT_SENDERS
                    and (isinstance(msg.get("text"), str) or isinstance(msg.get("content"), list))):
                raise Refusal(codes.UNKNOWN_SCHEMA)
    items = []
    for conv in data:
        parsed = Parsed("claude-ai", "Claude", source_id=conv["uuid"], started=conv.get("created_at"), identity="source")
        for number, msg in enumerate(conv["chat_messages"], 1):
            parsed.lines = number
            parsed.add(EXPORT_SENDERS[msg["sender"]], _message_text(msg, parsed, number), number,
                       f"sender:{msg['sender']}")
        # the hash covers the conversation's content, not export-run metadata such as updated_at
        core = {k: conv.get(k) for k in ("uuid", "name", "created_at", "chat_messages")}
        raw = _canonical(core)
        name = conv.get("name") if isinstance(conv.get("name"), str) and conv["name"].strip() else "claude-ai conversation"
        items.append(Item(parsed, hashlib.sha256(raw).hexdigest(), len(raw), f"claude-ai:{conv['uuid']}", name,
                          bundles.utc(conv.get("created_at")), member=conv["uuid"]))
    return items


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


def read_export_zip(path: Path) -> object:
    try:
        with zipfile.ZipFile(path) as zf:
            members = [i for i in zf.infolist() if not i.is_dir()
                       and i.filename.replace("\\", "/").split("/")[-1] == "conversations.json"
                       and i.filename.count("/") <= 1 and ".." not in i.filename.split("/") and not i.filename.startswith("/")]
            if not members:
                raise Refusal(codes.UNKNOWN_SCHEMA)
            info = members[0]
            if info.file_size > MAX_EXPORT_BYTES:
                raise Refusal(codes.TOO_LARGE)
            with zf.open(info) as handle:
                raw = handle.read(MAX_EXPORT_BYTES + 1)
            if len(raw) > MAX_EXPORT_BYTES:
                raise Refusal(codes.ZIP_UNSAFE)             # the header lied about the size
    except zipfile.BadZipFile:
        raise Refusal(codes.UNPARSEABLE) from None
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise Refusal(codes.UNPARSEABLE) from None


def load_items(path: Path, fallback: datetime) -> list[Item]:
    name, size = path.name, path.stat().st_size
    if size == 0:
        raise Refusal(codes.EMPTY)
    if name.endswith(".zip"):
        return parse_claude_ai_export(read_export_zip(path))
    if name.endswith(".canary.json"):
        return [parse_canary(path.read_bytes())]
    if name == "conversations.json":
        try:
            return parse_claude_ai_export(json.loads(path.read_bytes()))
        except (ValueError, UnicodeDecodeError):
            raise Refusal(codes.UNPARSEABLE) from None
    if name.endswith((".txt", ".md", ".text")):
        if size > MAX_PASTE_BYTES:
            raise Refusal(codes.TOO_LARGE)
        return [parse_paste(path.read_bytes(), name=name, fallback=fallback)]
    raise Refusal(codes.UNSUPPORTED_TYPE)


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


def process(shelf, inbox_root: str | Path, *, now: datetime | None = None, settle_seconds: float = 0,
            never_copy: tuple[str, ...] = ()) -> dict:
    """One inbox pass. Returns counts; never raises on a bad file."""
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
    for path in sorted(inbox_root.iterdir()):
        if path.name.startswith(("_", ".")) or path.is_symlink() or not path.is_file():
            continue
        if settle_seconds and now.timestamp() - path.stat().st_mtime < settle_seconds:
            tally(codes.UNSTABLE)
            continue
        if never_copied(path.name, never_copy):
            refuse(shelf, inbox_root, path, codes.NEVER_COPY, now, outcome=codes.EXCLUDED)
            tally(codes.EXCLUDED)
            continue
        try:
            if selection.private_marked(path.name, selection.first_line_of(path) if path.suffix in (".txt", ".md", ".text") else ""):
                refuse(shelf, inbox_root, path, codes.PRIVATE, now, outcome=codes.EXCLUDED)
                tally(codes.EXCLUDED)
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
        for item in items:
            if not item.parsed.turns:
                ledger.record(shelf, "inbox", bundles.ledger_key_for("inbox", item.key), codes.EXCLUDED,
                              reason=codes.NO_TURNS, canary=item.kind == "canary", now=now)
                tally(codes.EXCLUDED)
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
        if ok:
            fsio.ensure_dir(dest.parent)
            os.replace(path, dest)                           # moved, never deleted
    return {"status": "ok", "counts": counts}
