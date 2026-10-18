#!/usr/bin/env python3
"""Export the owner's kept Master Craftsman conversations for the Vault (watcher kind ``mc``, core/memory_shelf/mc.py).

    python3 /app/scripts/vault/export_mc_conversations.py --out DIR --principal USER --instance production
    venv/bin/python scripts/vault/export_mc_conversations.py --out DIR --principal USER --instance dev \\
        --guild-dir ~/minimoi-staging/data/guild
    ... --dry-run                       counts only (conversations and notes by month); writes nothing
    python3 /app/scripts/vault/export_mc_conversations.py --stdout-tar --principal USER --instance production
                                        the same files as one streamed tar on stdout; writes nothing anywhere

Runs by path, without PYTHONPATH: inside the portal container (production) and on the Mac against the Dev stores.

**Read-only on both stores.** The conversation records are read straight from ``<guild dir>/conversations/`` (no store
lock is taken, no legacy record is migrated: atomic replaces make each file whole), and the notes through the floor
store's own read (``FloorStores.list_notes``, paged) with the portal's ``DATABASE_URL``. Nothing in either is changed.

**What is exported.** One file per conversation of ``--principal`` (archived ones included and flagged ``archived``):
id, title, created/updated, scope, and every kept note of its own floor in store order (owner notes, agent replies,
platform notes), with note id, time, author kind and a machine-safe author id. Off the record nothing is kept by the
portal (no conversation, no note: see core/memory_shelf/mc.py), so nothing off the record can be exported. Owner notes
written by another account (possible only on the shared Shop floor thread) are dropped and counted, never exported.

**Incremental and bounded.** The file is canonical JSON with no export time inside it: an unchanged conversation gives
the same bytes, is compared with the file already there by sha256 and is not rewritten (so the watcher's size + mtime
check skips it too). A changed one is replaced atomically (temp file, fsync, rename; folder 0700, files 0600). A
conversation is never truncated: past MAX_NOTES notes or MAX_BYTES bytes it is refused (``too_large``) and counted, and
a floor that cannot be read is ``notes_unavailable``; its earlier file, if any, stays. Files are never deleted.

**``--stdout-tar`` (the production capture path, 17 Oct 2026).** The EC2 host's ``vault-mc-export`` job runs this script with
``docker exec`` and reads its stdout straight into the host's own root-only scratch, so no plaintext export ever lands in the
portal container's filesystem. The script then writes NOTHING to any folder (no ``--out``, no status file, no temp file):
stdout is a streamed tar (``tarfile`` mode ``w|``, never seekable, never buffered on disk) of one member per exported
conversation, ``<instance>--<id>.json`` with exactly the canonical bytes the folder mode writes (0600, mtime 0, so the same
conversation always streams the same member), then ``EXPORT.json`` LAST: ``{"status", "counts"}``. A stream cut short (a
crash, a kill, a broken pipe) has no ``EXPORT.json`` and the reader refuses it whole. Every selection rule is the folder
mode's own (this principal's on-record conversations only, other principals and off-record notes never, ``too_large``
refused, ``notes_unavailable`` counted); there is no "unchanged" comparison, so each conversation counts ``exported`` and
the host compares hashes itself. The counts line goes to stderr. Before anything else runs, the real stdout descriptor is
duplicated for the tar alone and descriptor 1 (and ``sys.stdout``) is pointed at stderr for the rest of the process, so a stray
``print`` or a library writing to fd 1 can never corrupt the stream (adversarial review LOW-3).

**Empty conversations** (``empty``, never exported): no turns, or turns whose texts are all blank (``text.strip() == ""``). That
is the shelf reader's own rule (core/memory_shelf/mc.py skips a blank turn, and a file with no turn left is excluded as
``no_turns``): a file the shelf can never record would otherwise be sent, never land in the Vault, and be re-sent forever.

Output is one line of counts and fixed codes: never a title, a name or a note's text.
Exit codes: 0 ok; 1 some conversations could not be exported (counted); 2 usage or configuration.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import sys
import tarfile
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SCHEMA = "minimoi.mc-conversation/1"          # core/memory_shelf/mc.py SCHEMA (kept equal by a test)
MAX_NOTES = 20000
MAX_BYTES = 48 * 1024 * 1024                  # core/memory_shelf/mc.py MAX_BYTES
PAGE = 200
STATUS_FILE = ".export-status.json"
EXPORT_DOC = "EXPORT.json"                     # the --stdout-tar stream's last member (core/memory_sync/mc_export.py reads it)
INSTANCE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
TOKEN = re.compile(r"^[A-Za-z0-9_:.+-]{1,64}$")
LEGACY_FILE = re.compile(r"^legacy-([0-9a-f]{16})\.json$")
CONVERSATION_FILE = re.compile(r"^(c-[0-9a-f]{12})\.json$")
RECORD_MAX = 4 * 1024 * 1024
OK, PARTIAL, USAGE = 0, 1, 2


class ExportError(RuntimeError):
    """A fixed code; never carries content."""


# ── reading the conversation records (read-only, no lock, no migration) ──────

def _read_regular(path: Path, limit: int) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb") as handle:
            fd = None
            data = handle.read(limit + 1)
    finally:
        if fd is not None:
            os.close(fd)
    return data if len(data) <= limit else None


def conversations(guild_dir: Path, principal: str, counts: Counter) -> list[tuple[str, dict]]:
    """``[(export id, record)]`` for every conversation record of ``principal``. The export id is the record's own
    file stem (``c-<12 hex>`` or ``legacy-<16 hex>``: one Shop floor thread per owner account)."""
    folder = Path(guild_dir) / "conversations"
    if not folder.is_dir():
        raise ExportError("conversations_unavailable")
    from minimoi_portal.guild_ui.conversations import LEGACY_ID
    out = []
    for name in sorted(os.listdir(folder)):
        legacy, plain = LEGACY_FILE.match(name), CONVERSATION_FILE.match(name)
        if not (legacy or plain):
            continue
        raw = _read_regular(folder / name, RECORD_MAX)
        try:
            conv = json.loads(raw.decode("utf-8")) if raw is not None else None
        except (ValueError, UnicodeDecodeError):
            conv = None
        want = LEGACY_ID if legacy else plain.group(1)
        if not isinstance(conv, dict) or conv.get("id") != want:
            counts["unreadable_record"] += 1
            continue
        if conv.get("principal") != principal:
            counts["other_principal"] += 1                 # never read, never counted further
            continue
        if conv.get("record_mode", "on_record") != "on_record":
            counts["off_record"] += 1                      # no record carries one today; fail closed if one ever does
            continue
        if legacy and legacy.group(1) != hashlib.sha256(principal.encode()).hexdigest()[:16]:
            counts["unreadable_record"] += 1               # a legacy file must be this owner's own (conversations._path)
            continue
        out.append((name[:-5], conv))
    return out


def _floor_ok(export_id: str, conv: dict) -> bool:
    floor = conv.get("notes_floor")
    if not isinstance(floor, str) or not floor:
        return False
    if conv.get("legacy"):
        return "/" not in floor                            # the Shop floor thread keeps the floor's own key
    return floor.endswith("/" + export_id) and floor.count("/") == 1


# ── reading the notes (the floor store's own read, paged) ────────────────────

def read_notes(floor_store) -> tuple[list[dict] | None, str | None]:
    """``(every kept note of one floor, oldest first, None)``, or ``(None, "notes_unavailable" | "too_large")``."""
    notes, before = [], None
    while True:
        result = floor_store.list_notes(before=before, limit=PAGE)
        if not result.ok or not isinstance(result.data, dict):
            return None, "notes_unavailable"
        page = result.data.get("notes") or []
        notes[:0] = page
        if len(notes) > MAX_NOTES:
            return None, "too_large"
        if not result.data.get("more") or not page:
            return notes, None
        before = min(int(n["id"]) for n in page)


def _voice(note: dict) -> str | None:
    """Which side of a retained voice transcript a platform note is (mc_voice_transcripts.HEADER), from the platform's own
    author id, page and header: the owner cannot write a platform note."""
    from minimoi_portal.guild_ui.mc_voice_transcripts import AUTHOR_PREFIX, HEADER, PAGE as VOICE_PAGE
    if note.get("author_kind") != "platform" or not str(note.get("who") or "").startswith(AUTHOR_PREFIX):
        return None
    if (note.get("context") or {}).get("page") != VOICE_PAGE:
        return None
    match = HEADER.match(note.get("text") or "")
    if not match:
        return None
    return "owner_asr" if match["speaker"] == "owner ASR" else "assistant_generated"


def _who(note: dict, principal: str) -> str:
    who = str(note.get("who") or "")
    if note.get("author_kind") == "owner" and who == principal:
        return "owner"                                     # the account name never reaches the file
    return who if TOKEN.match(who) else "id-" + hashlib.sha256(who.encode()).hexdigest()[:16]


def reply_key(request_id: str) -> str:
    """The MC reply note's key for an owner note (guild_ui.api._reply_key)."""
    return "mc-" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:40]


def document(instance: str, export_id: str, conv: dict, notes: list[dict], principal: str, counts: Counter) -> dict:
    # The legacy Shop floor thread is keyed by the floor alone, so any account may have written there (typed, spoken or
    # replied to) whether or not one of its typed notes is in view. On EVERY legacy floor an agent or platform note
    # belongs to the owner only when it is MC's reply to one of the owner's own notes; every other one (another
    # account's replies or voice transcripts, which carry no owner) is left out and counted, so nobody else's words
    # are ever attributed to the owner.
    shared = bool(conv.get("legacy"))
    own_replies = {reply_key(str(n["request_id"])) for n in notes
                   if n.get("author_kind") == "owner" and n.get("who") == principal and n.get("request_id")}
    turns = []
    for note in sorted(notes, key=lambda n: int(n["id"])):
        if note.get("record_mode", "on_record") != "on_record":
            counts["off_record_note"] += 1
            continue
        if note.get("author_kind") == "owner" and note.get("who") != principal:
            counts["other_owner_note"] += 1
            continue
        if shared and note.get("author_kind") != "owner" and not (
                note.get("author_kind") == "agent" and note.get("request_id") in own_replies):
            counts["shared_floor_unattributed_note"] += 1
            continue
        turns.append({"note_id": int(note["id"]), "created_at": str(note.get("created_at") or ""),
                      "author_kind": str(note.get("author_kind") or ""), "who": _who(note, principal),
                      "label": str(note.get("author_label") or ""), "voice": _voice(note),
                      "page": (note.get("context") or {}).get("page"), "area": (note.get("context") or {}).get("area"),
                      "text": str(note.get("text") or "")})
    scope = conv.get("scope")
    return {"schema": SCHEMA, "instance": instance, "conversation_id": export_id,
            "source_id": f"{instance}:{export_id}", "legacy": bool(conv.get("legacy")),
            "archived": bool(conv.get("archived")), "archived_at": conv.get("archived_at"),
            "scope": scope if isinstance(scope, str) and TOKEN.match(scope) else None, "scope_day": conv.get("scope_day"),
            "title": str(conv.get("title") or ""), "title_source": conv.get("title_source"),
            "created_at": str(conv.get("created_at") or ""), "updated_at": str(conv.get("updated_at") or ""),
            "turns": turns}


def canonical(doc: dict) -> bytes:
    return (json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")


# ── writing ──────────────────────────────────────────────────────────────────

def _ensure_out(out: Path) -> None:
    if out.is_symlink():
        raise ExportError("out_is_symlink")
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not out.is_dir():
        raise ExportError("out_not_a_directory")
    os.chmod(out, 0o700)


def write_atomic(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _tar_member(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = len(data), 0o600, 0
    tar.addfile(info, io.BytesIO(data))


def _month(value) -> str:
    return str(value or "")[:7] or "unknown"


def export(*, guild_dir: Path, out: Path | None, principal: str, instance: str, floor_for, dry_run: bool = False,
           now: datetime | None = None, tar_out=None) -> dict:
    """``{"status", "counts", "by_month"}``; counts and fixed codes only. ``floor_for(notes_floor)`` gives a read-only
    floor store for one floor key. ``tar_out``: a writable binary stream; the export goes there as a streamed tar
    (``--stdout-tar``) and nothing is written to any folder (``out`` must be None and ``dry_run`` False)."""
    if not INSTANCE.match(instance or ""):
        raise ExportError("bad_instance")
    if not principal:
        raise ExportError("no_principal")
    if tar_out is not None and (out is not None or dry_run):
        raise ExportError("stdout_tar_excludes_out")
    counts: Counter = Counter()
    by_month = {"conversations": Counter(), "notes": Counter()}
    rows = conversations(Path(guild_dir), principal, counts)
    # Opened only after the records were read: a refusal above leaves stdout empty, which the host reads as garbage.
    tar = tarfile.open(fileobj=tar_out, mode="w|", format=tarfile.PAX_FORMAT) if tar_out is not None else None
    if not dry_run and tar is None:
        out = Path(out)
        if Path(guild_dir).resolve() in (out.resolve(), *out.resolve().parents):
            raise ExportError("out_inside_guild_dir")
        _ensure_out(out)
    for export_id, conv in rows:
        if not _floor_ok(export_id, conv):
            counts["bad_floor"] += 1
            continue
        notes, refused = read_notes(floor_for(conv["notes_floor"]))
        if refused:
            counts[refused] += 1
            continue
        doc = document(instance, export_id, conv, notes, principal, counts)
        if not any(turn["text"].strip() for turn in doc["turns"]):         # mc.parse's own rule: no non-blank turn, no record
            counts["empty"] += 1
            continue
        counts["archived" if doc["archived"] else "active"] += 1
        by_month["conversations"][_month(doc["created_at"])] += 1
        for turn in doc["turns"]:
            by_month["notes"][_month(turn["created_at"])] += 1
        counts["notes"] += len(doc["turns"])
        data = canonical(doc)
        if len(data) > MAX_BYTES:
            counts["too_large"] += 1
            continue
        if dry_run:
            counts["would_export"] += 1
            continue
        if tar is not None:
            _tar_member(tar, f"{instance}--{export_id}.json", data)
            counts["exported"] += 1
            continue
        target = out / f"{instance}--{export_id}.json"
        have = _read_regular(target, MAX_BYTES) if target.exists() else None
        if have is not None and hashlib.sha256(have).digest() == hashlib.sha256(data).digest():
            counts["unchanged"] += 1
            continue
        write_atomic(target, data)
        counts["changed" if have is not None else "new"] += 1
    failed = counts["notes_unavailable"] + counts["too_large"] + counts["bad_floor"] + counts["unreadable_record"]
    result = {"status": "partial" if failed else "ok", "dry_run": dry_run, "counts": dict(sorted(counts.items())),
              "by_month": {k: dict(sorted(v.items())) for k, v in by_month.items()}}
    if tar is not None:
        _tar_member(tar, EXPORT_DOC, (json.dumps({"status": result["status"], "counts": result["counts"]}, sort_keys=True)
                                      + "\n").encode())
        tar.close()                                        # end-of-archive blocks; the caller flushes the stream
    elif not dry_run:
        stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
        write_atomic(out / STATUS_FILE, (json.dumps({"at": stamp, "instance": instance, **result}, sort_keys=True)
                                         + "\n").encode())
    return result


def database_floor(url_env: str = "DATABASE_URL"):
    from minimoi_portal.guild_ui.stores import FloorStores
    if not os.environ.get(url_env):
        raise ExportError("database_not_configured")
    base = FloorStores(lambda: os.environ.get(url_env))
    return base.for_floor


def tar_stdout():
    """The real stdout, for the tar alone: a duplicate of descriptor 1 is returned as a binary stream, then descriptor 1 and
    ``sys.stdout`` are pointed at stderr for the rest of the process, so nothing else (a stray print, a library, a child
    process inheriting fd 1) can write into the stream."""
    sys.stdout.flush()
    fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return os.fdopen(fd, "wb")


def main(argv=None, *, stream=None) -> int:
    """``stream``: where ``--stdout-tar`` writes the tar (default: the real stdout, isolated by ``tar_stdout``; tests pass a
    file and the process's descriptors are left alone). In that mode every line of text goes to stderr, so stdout carries
    the tar and nothing else."""
    parser = argparse.ArgumentParser(description="Export kept MC conversations for the Vault (counts only on stdout).")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--principal", required=True, help="the owner account whose conversations are exported")
    parser.add_argument("--instance", required=True, help="which portal this is (e.g. production, dev): part of each id")
    parser.add_argument("--guild-dir", type=Path, help="default: the folder of GUILD_QUEUE_PATH")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stdout-tar", action="store_true", help="stream the export as a tar on stdout; write nothing")
    args = parser.parse_args(argv)
    tar_out = (stream if stream is not None else tar_stdout()) if args.stdout_tar else None    # first: nothing else reaches the tar
    log = sys.stderr if args.stdout_tar else sys.stdout
    guild_dir = args.guild_dir or (Path(os.environ["GUILD_QUEUE_PATH"]).parent if os.environ.get("GUILD_QUEUE_PATH") else None)
    if guild_dir is None or (args.stdout_tar and (args.out is not None or args.dry_run)) \
            or (not args.stdout_tar and args.out is None and not args.dry_run):
        print("export\tusage\t{}", file=log, flush=True)
        return USAGE
    try:
        result = export(guild_dir=Path(guild_dir).expanduser(), out=args.out.expanduser() if args.out else None,
                        principal=args.principal, instance=args.instance, floor_for=database_floor(),
                        dry_run=args.dry_run, tar_out=tar_out)
        if tar_out is not None:
            tar_out.flush()
    except ExportError as exc:
        print(f"export\t{exc}\t{{}}", file=log, flush=True)
        return USAGE
    except (OSError, tarfile.TarError) as exc:
        print(f"export\twrite_failed:{type(exc).__name__}\t{{}}", file=log, flush=True)
        return USAGE
    print(f"export\t{result['status']}\t{json.dumps(result['counts'], sort_keys=True)}", file=log, flush=True)
    if args.dry_run:
        print(f"by_month\t{json.dumps(result['by_month'], sort_keys=True)}", flush=True)
    return OK if result["status"] == "ok" else PARTIAL


if __name__ == "__main__":
    sys.exit(main())
