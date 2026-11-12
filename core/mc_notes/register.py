"""The note register (12 Nov 2026): short notes among Robert, the agents and MC, which MC takes into its memory.

When Robert settles something in a direct chat (with Claude Code, Codex, Grok, or MC itself), whoever was in the chat writes a note
here. The register is the formal, protected, lasting record of the many small things that would otherwise be lost. A single note is
light (a few lines, no ceremony) and may be replaced later; MC builds on the register (memory, reminders, its to-do list, end-of-day
summaries, and GitHub items when something deserves one).

    <root>/notes-<YYYY-MM>-<host>.jsonl     one JSON note per line, append-only; one file per month per writing host

* **Append-only.** A note is one line, appended under an exclusive lock and fsynced. Nothing here rewrites or removes a line.
* **Tamper-evident.** Each line carries ``prev``: the sha256 of the previous line's bytes in the same file ("" for the first line).
  ``verify`` walks every chain; an edited, removed or reordered line breaks it.
* **Monthly archive.** Writing into a new month seals this host's earlier month files read-only (0400). Sealed files are read, never
  written.
* **One file per host** (``$MINIMOI_MC_NOTES_HOST``, default ``mac``; production MC writes as ``prod``), so two hosts never append to
  the same file and copies can be merged by file. Reading merges every file, oldest first.
* **A change of mind** is a new note that names the notes it ``replaces``; both stay. The current view is every note no later note
  replaces.

Plain files: no model, no network, no database. ``by`` and ``said_by`` are the writer's claim; the register does not prove identity.
Text is data, bounded and stripped of control characters.

Root: ``--root``, else ``$MINIMOI_MC_NOTES_DIR``, else ``~/minimoi-staging/data/guild/mc-notes``.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

WRITERS = ("claude-code", "codex", "grok", "mc", "robert")
SPEAKERS = ("robert", "claude-code", "codex", "grok", "mc")
FOR = ("remember", "remind", "act")      # what MC should do: keep it in memory, remind people of it, or put it on its to-do list
TEXT_MAX = 2000
TOPIC_MAX = 80
REF_MAX = 200
REPLACES_MAX = 10
LINE_MAX = 16 * 1024
DEFAULT_ROOT = "~/minimoi-staging/data/guild/mc-notes"
DEFAULT_HOST = "mac"

_ID = re.compile(r"^n-\d{8}T\d{6}Z-[0-9a-f]{6}$")
_HOST = re.compile(r"^[a-z][a-z0-9-]{0,15}$")
_FILE = re.compile(r"^notes-(\d{4}-\d{2})-([a-z][a-z0-9-]{0,15})\.jsonl$")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class Refused(ValueError):
    """A note the register will not take. ``code`` is a short machine word; the message is plain."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def root_dir(root: str | None = None) -> Path:
    return Path(os.path.expanduser(root or os.environ.get("MINIMOI_MC_NOTES_DIR") or DEFAULT_ROOT))


def host_name(host: str | None = None) -> str:
    value = host or os.environ.get("MINIMOI_MC_NOTES_HOST") or DEFAULT_HOST
    if not _HOST.match(value):
        raise Refused("bad_host", "The host name is lowercase letters, digits and dashes, at most 16 characters.")
    return value


def valid_id(value) -> bool:
    return isinstance(value, str) and bool(_ID.match(value))


def _clean(value, limit: int, what: str, *, multiline: bool, required: bool = True) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise Refused("invalid", f"The {what} must be text.")
    out = _CTRL.sub("", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if not multiline:
        out = re.sub(r"\s+", " ", out)
    if required and not out:
        raise Refused("empty", f"The {what} is empty.")
    if len(out) > limit:
        raise Refused("too_long", f"The {what} is longer than {limit} characters.")
    return out


def _sha(line: bytes) -> str:
    return hashlib.sha256(line).hexdigest()


def _last_line(fd: int) -> bytes:
    size = os.fstat(fd).st_size
    if size == 0:
        return b""
    pos, chunk, buf = size, 4096, b""
    while pos > 0:
        step = min(chunk, pos)
        pos -= step
        buf = os.pread(fd, step, pos) + buf
        lines = buf.rstrip(b"\n").split(b"\n")
        if len(lines) > 1 or pos == 0:
            return lines[-1] + b"\n"
    return buf


def _seal_older(root: Path, host: str, month: str) -> None:
    for p in root.glob(f"notes-*-{host}.jsonl"):
        m = _FILE.match(p.name)
        if m and m.group(2) == host and m.group(1) < month and (p.stat().st_mode & 0o222):
            os.chmod(p, 0o400)


def _append(root: Path, host: str, note: dict) -> dict:
    """Append one prepared note to this host's file for the note's month, chaining it to the previous line."""
    month = note["at"][:7]
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / f"notes-{month}-{host}.jsonl"
    if path.exists() and not (path.stat().st_mode & 0o200):
        raise Refused("sealed", f"{path.name} is sealed; notes are only added to the current month.")
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        last = _last_line(fd)
        doc = {**note, "prev": _sha(last) if last else ""}
        line = (json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        if len(line) > LINE_MAX:
            raise Refused("too_long", "The note is too long to store as one line.")
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)
    _seal_older(root, host, month)
    return doc


def add(root: str | None, *, by: str, text: str, said_by: str = "robert", topic: str = "", what_for: str = "remember",
        replaces: list[str] | None = None, ref: str = "", host: str | None = None, now: datetime | None = None) -> dict:
    """Write one note and return it. ``replaces`` may name notes this copy does not hold yet (they may arrive from another host)."""
    if by not in WRITERS:
        raise Refused("unknown_writer", f"A note is written by one of: {', '.join(WRITERS)}.")
    if said_by not in SPEAKERS:
        raise Refused("unknown_speaker", f"A note is said by one of: {', '.join(SPEAKERS)}.")
    if what_for not in FOR:
        raise Refused("unknown_for", f"A note is for one of: {', '.join(FOR)}.")
    replaces = list(dict.fromkeys(replaces or []))
    if len(replaces) > REPLACES_MAX or not all(valid_id(r) for r in replaces):
        raise Refused("bad_replaces", "replaces takes up to 10 note ids like n-20261112T150000Z-1a2b3c.")
    host = host_name(host)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    note = {
        "schema": 2,
        "id": f"n-{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}",
        "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host": host,
        "by": by,
        "said_by": said_by,
        "for": what_for,
        "topic": _clean(topic, TOPIC_MAX, "topic", multiline=False, required=False),
        "text": _clean(text, TEXT_MAX, "note", multiline=True),
        "replaces": replaces,
        "ref": _clean(ref, REF_MAX, "reference", multiline=False, required=False),
    }
    return _append(root_dir(root), host, note)


def _files(root: Path) -> list[Path]:
    return sorted(p for p in root.glob("notes-*.jsonl") if _FILE.match(p.name)) if root.is_dir() else []


def all_notes(root: str | None = None) -> list[dict]:
    """Every readable note from every month and host, oldest first. A bad line is skipped, never fatal (``verify`` reports it)."""
    seen, notes = set(), []
    for p in _files(root_dir(root)):
        for raw in p.read_bytes().splitlines():
            try:
                doc = json.loads(raw)
            except ValueError:
                continue
            if isinstance(doc, dict) and valid_id(doc.get("id")) and doc["id"] not in seen:
                seen.add(doc["id"])
                notes.append(doc)
    return sorted(notes, key=lambda n: (n.get("at", ""), n["id"]))


def current(notes: list[dict]) -> list[dict]:
    """The notes no other note replaces, oldest first."""
    replaced = {r for n in notes for r in n.get("replaces") or []}
    return [n for n in notes if n["id"] not in replaced]


def get(root: str | None, note_id: str) -> dict | None:
    return next((n for n in all_notes(root) if n["id"] == note_id), None) if valid_id(note_id) else None


def verify(root: str | None = None) -> dict:
    """Check every file's chain. Returns {"ok": bool, "files": {name: {"lines": n, "sealed": bool, "problems": [...]}}}."""
    report = {"ok": True, "files": {}}
    for p in _files(root_dir(root)):
        prev, problems, count = "", [], 0
        for i, raw in enumerate(p.read_bytes().splitlines(keepends=True), 1):
            count = i
            try:
                doc = json.loads(raw)
            except ValueError:
                problems.append(f"line {i}: not JSON")
                prev = _sha(raw)
                continue
            if doc.get("prev") != prev:
                problems.append(f"line {i}: chain broken")
            if not valid_id(doc.get("id")):
                problems.append(f"line {i}: bad id")
            prev = _sha(raw)
        report["files"][p.name] = {"lines": count, "sealed": not (p.stat().st_mode & 0o222), "problems": problems}
        report["ok"] = report["ok"] and not problems
    return report
