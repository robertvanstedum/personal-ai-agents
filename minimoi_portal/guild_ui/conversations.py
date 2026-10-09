"""Master Craftsman conversations on the Shop floor (Guild 1.1 dev, slice 2).

File first, server owned, one owner: one JSON file per conversation in
``<guild data folder>/conversations/`` (next to the build queue and the turn
trace), written atomically under a lock. No database table; the notes of a
conversation stay in the floor store, scoped by their own floor key.

Each conversation has an id, a title (from its first message, never from a
model), created and updated times, pinned, archived, and an optional work-item
link (which feeds the rail's "what this conversation is about").

"Remove from list" archives: it is a view action, never an erasure. Nothing
here calls a model.

Migration: today's Shop floor thread is the first conversation
(``LEGACY_ID``). Its notes keep the floor's own key, so the old thread opens
exactly as before; a new conversation's notes use ``<floor>/<id>``.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

LEGACY_ID = "shop-floor-thread"
LEGACY_TITLE = "Shop floor thread"
DEFAULT_TITLE = "New conversation"
TITLE_MAX = 80
ID_RE = re.compile(r"^(shop-floor-thread|c-[0-9a-f]{12})$")
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
FOLDER = "conversations"
CHAT_SCOPES = frozenset({"operate", "workshop", "board", "improve", "prototype", "build"})


def local_day() -> str:
    return datetime.now(ZoneInfo("America/Chicago")).date().isoformat()



class ConversationStoreUnavailable(RuntimeError):
    """The conversations folder cannot be read or written."""


class ConversationNotFound(LookupError):
    pass


class TooManyAttachments(RuntimeError):
    """A conversation keeps at most ATTACHMENTS_MAX files."""


ATTACHMENTS_MAX = 50
DOCUMENTS_MAX = 20
ORIGINAL_READ_MAX = 5 * 1024 * 1024 + 1024   # an original is never bigger than the upload limit
TURN_FILES_MAX = 100
DOC_ID_RE = re.compile(r"^d-[0-9a-f]{16}$")
DOCS_FOLDER = "docs"
ORIGINALS_TOTAL_MAX = 500 * 1024 * 1024        # all stored originals together; past this a document is still read, its original is not kept


class TooManyDocuments(RuntimeError):
    """A conversation keeps at most DOCUMENTS_MAX documents."""


class DocumentGone(LookupError):
    """The document is not in this conversation, or its saved text is missing."""
ASSET_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def clean_file_name(value) -> str:
    """A display name: the last path part, no control characters, at most 120 characters."""
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]+", " ", name).strip()
    return (name or "image")[:120]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")   # orders actions within a second


_MD_MARKS = re.compile(r"[*_`#>\[\]()!|~]+")


def title_from(text: str) -> str:
    """A title from the first message: its first line, Markdown marks and
    extra spaces removed, cut at a word near TITLE_MAX. No model."""
    first = next((line for line in (text or "").splitlines() if line.strip()), "")
    clean = re.sub(r"\s+", " ", _MD_MARKS.sub(" ", first)).strip()
    if not clean:
        return DEFAULT_TITLE
    if len(clean) <= TITLE_MAX:
        return clean
    cut = clean[:TITLE_MAX].rsplit(" ", 1)[0]
    return (cut if len(cut) > TITLE_MAX // 2 else clean[:TITLE_MAX]).rstrip(" ,.;:") + "…"


def clean_title(value) -> str | None:
    if not isinstance(value, str):
        return None
    title = re.sub(r"\s+", " ", value).strip()
    return title[:TITLE_MAX] if title else None


class ConversationStore:
    def __init__(self, folder: str | None, *, base_floor: str):
        self.dir = os.path.join(folder, FOLDER) if folder else None
        self.base_floor = base_floor
        self.unreadable = 0          # files skipped by the last list (#265 review F3)

    # ── files ──────────────────────────────────────────────────────────
    def _ensure_dir(self):
        if not self.dir:
            raise ConversationStoreUnavailable("no guild data folder")
        try:
            os.makedirs(self.dir, mode=0o700, exist_ok=True)
        except OSError as exc:
            raise ConversationStoreUnavailable(type(exc).__name__) from exc

    @contextmanager
    def _lock(self):
        self._ensure_dir()
        try:
            fd = os.open(os.path.join(self.dir, ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise ConversationStoreUnavailable(type(exc).__name__) from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _path(self, cid: str, principal: str | None = None) -> str:
        if cid == LEGACY_ID:
            # Each owner account has its own record of the Shop floor thread
            # (#265 review F2); the thread's notes are the floor's, as before.
            return os.path.join(self.dir, f"legacy-{hashlib.sha256((principal or '').encode()).hexdigest()[:16]}.json")
        return os.path.join(self.dir, f"{cid}.json")

    def _read(self, cid: str, principal: str | None = None, *, path: str | None = None) -> dict | None:
        try:
            with open(path or self._path(cid, principal), encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise ConversationStoreUnavailable(f"{cid}: {type(exc).__name__}") from exc
        return data if isinstance(data, dict) and data.get("id") == cid else None

    def _write(self, conv: dict) -> dict:
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(conv, f, sort_keys=True, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path(conv["id"], conv.get("principal")))
        except OSError as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise ConversationStoreUnavailable(type(exc).__name__) from exc
        return conv

    def _all(self) -> list[dict]:
        """Every readable record. One unreadable file is skipped and counted
        (self.unreadable), never a reason to hide the rest (#265 review F3)."""
        self._ensure_dir()
        try:
            names = [n for n in os.listdir(self.dir) if n.endswith(".json") and not n.startswith(".")]
        except OSError as exc:
            raise ConversationStoreUnavailable(type(exc).__name__) from exc
        out, bad = [], 0
        for name in names:
            stem = name[:-5]
            cid = LEGACY_ID if re.fullmatch(r"legacy-[0-9a-f]{16}", stem) else stem
            if not ID_RE.fullmatch(cid):
                continue
            try:
                conv = self._read(cid, path=os.path.join(self.dir, name))
            except ConversationStoreUnavailable:
                conv = None
            if conv:
                out.append(conv)
            else:
                bad += 1
        self.unreadable = bad
        return out

    def _legacy(self, principal: str) -> dict:
        return {"id": LEGACY_ID, "principal": principal, "title": LEGACY_TITLE, "title_source": "legacy",
                "created_at": now_iso(), "updated_at": now_iso(), "pinned": False, "archived": False,
                "archived_at": None, "work_item": None, "legacy": True, "notes_floor": self.base_floor,
                "created_key": None}

    def _migrate(self, principal: str):
        """Today's Shop floor thread becomes the first conversation (once per owner account)."""
        try:
            existing = self._read(LEGACY_ID, principal)
        except ConversationStoreUnavailable:
            existing = None                      # unreadable: rewritten, the thread's notes are untouched
        if existing is None:
            self._write(self._legacy(principal))

    # ── reads ──────────────────────────────────────────────────────────
    def list(self, principal: str, *, archived: bool = False) -> list[dict]:
        """Newest first, pinned on top; archived ones only in the Archive view."""
        with self._lock():
            self._migrate(principal)
            rows = [c for c in self._all() if c.get("principal") == principal and bool(c.get("archived")) == archived]
        rows.sort(key=lambda c: c.get("updated_at") or "", reverse=True)
        rows.sort(key=lambda c: not c.get("pinned"))
        return rows

    def get(self, cid: str, principal: str) -> dict:
        if not isinstance(cid, str) or not ID_RE.fullmatch(cid):
            raise ConversationNotFound(cid)
        with self._lock():
            if cid == LEGACY_ID:
                self._migrate(principal)
            conv = self._read(cid, principal)
        if conv is None or conv.get("principal") != principal:
            raise ConversationNotFound(cid)
        return conv

    def current(self, principal: str, requested: str | None = None) -> dict:
        """The conversation a page shows: the requested one, else the most
        recently updated one in the list (the migrated thread at first)."""
        if requested:
            return self.get(requested, principal)
        rows = [c for c in self.list(principal) if not c.get("scope")]
        return max(rows, key=lambda c: c.get("updated_at") or "") if rows else self.get(LEGACY_ID, principal)

    def current_for_scope(self, principal: str, scope: str) -> dict:
        """Today's area chat. Selection and creation share a lock across tabs.

        Old chats and their notes remain available through the full history.
        The daily boundary selects a different chat; it never clears a session.
        """
        if scope not in CHAT_SCOPES:
            raise ValueError("unknown chat scope")
        day = local_day()
        with self._lock():
            self._migrate(principal)
            rows = [c for c in self._all() if c.get("principal") == principal
                    and c.get("scope") == scope and c.get("scope_day") == day
                    and not c.get("archived")]
            if rows:
                return max(rows, key=lambda c: c.get("updated_at") or "")
            return self._new(principal, key=None, scope=scope, day=day)

    def _new(self, principal, *, key, work_item=None, scope=None, day=None):
        # Caller holds the store lock.
        cid = "c-" + secrets.token_hex(6)
        now = now_iso()
        conv = {"id": cid, "principal": principal, "title": DEFAULT_TITLE, "title_source": "default",
                "created_at": now, "updated_at": now, "pinned": False, "archived": False, "archived_at": None,
                "work_item": work_item, "legacy": False, "notes_floor": f"{self.base_floor}/{cid}",
                "created_key": key, "scope": scope, "scope_day": day}
        return self._write(conv)

    # ── writes (no model call anywhere) ────────────────────────────────
    def create(self, principal: str, *, key: str, work_item: dict | None = None, scope: str | None = None) -> tuple[dict, bool]:
        """(conversation, repeated): one conversation per idempotency key."""
        with self._lock():
            self._migrate(principal)
            for conv in self._all():
                if conv.get("created_key") == key and conv.get("principal") == principal:
                    return conv, True
            return self._new(principal, key=key, work_item=work_item, scope=scope,
                             day=local_day() if scope else None), False

    def _change(self, cid: str, principal: str, fn) -> dict:
        if not isinstance(cid, str) or not ID_RE.fullmatch(cid):
            raise ConversationNotFound(cid)
        with self._lock():
            if cid == LEGACY_ID:
                self._migrate(principal)
            conv = self._read(cid, principal)
            if conv is None or conv.get("principal") != principal:
                raise ConversationNotFound(cid)
            fn(conv)
            return self._write(conv)

    def rename(self, cid, principal, title: str) -> dict:
        def fn(c):
            c["title"], c["title_source"] = title, "renamed"
        return self._change(cid, principal, fn)

    def set_pinned(self, cid, principal, pinned: bool) -> dict:
        def fn(c):
            c["pinned"] = bool(pinned)
        return self._change(cid, principal, fn)

    def set_archived(self, cid, principal, archived: bool) -> dict:
        """Remove from list (archive) and Restore: a view action; nothing is erased."""
        def fn(c):
            c["archived"] = bool(archived)
            c["archived_at"] = now_iso() if archived else None
            if archived:
                c["pinned"] = False
        return self._change(cid, principal, fn)

    def set_work_item(self, cid, principal, work_item: dict | None) -> dict:
        """Attach, replace or clear the linked work. Only this changes it: no
        page view, note or browsing ever sets it."""
        def fn(c):
            c["work_item"] = work_item
        return self._change(cid, principal, fn)

    # ── kept images: the whole lifecycle for one conversation runs under this store's lock ──
    #
    # A kept image lives in two places: this conversation's file (the pointer) and the Media index (the
    # hold that stops a purge). They are different stores, so one lock, the same cross-process file lock
    # every write here takes, covers BOTH writes: attach, remove and retry for a conversation are
    # serial across workers, and an old remove can never release a hold a newer attach just took.
    #
    # Attach: hold first, then the pointer; if the pointer cannot be kept, the hold is given back.
    # A crash in between leaves a hold with no pointer (a purge is blocked: the safe direction).
    # Remove: one durable write removes the pointer AND records "release pending"; then the hold is
    # released; then the marker is cleared. A failed or interrupted release leaves the marker, which the
    # rail shows with a Retry; the same Remove call retries it. A pointer is never kept without a hold
    # except in the instant between two writes inside this lock.

    def _locked_conversation(self, cid: str, principal: str) -> dict:
        """Caller holds the lock."""
        if not isinstance(cid, str) or not ID_RE.fullmatch(cid):
            raise ConversationNotFound(cid)
        if cid == LEGACY_ID:
            self._migrate(principal)
        conv = self._read(cid, principal)
        if conv is None or conv.get("principal") != principal:
            raise ConversationNotFound(cid)
        return conv

    def attach_held(self, cid, principal, asset_id: str, name: str, hold, release):
        """(conversation, hold_result). ``hold()`` takes the Media hold and returns its result; an outcome other
        than "attached" or "already" changes nothing and is returned for the caller to explain.
        ``release()`` gives a hold back and returns True when it did."""
        with self._lock():
            conv = self._locked_conversation(cid, principal)
            held = hold()
            if held.outcome not in ("attached", "already"):
                return conv, held
            try:
                rows = conv.setdefault("attachments", [])
                if not any(a.get("asset_id") == asset_id for a in rows):
                    if len(rows) >= ATTACHMENTS_MAX:
                        raise TooManyAttachments(ATTACHMENTS_MAX)
                    rows.append({"asset_id": asset_id, "name": clean_file_name(name), "added_at": now_iso()})
                conv["release_pending"] = [p for p in conv.get("release_pending") or [] if p.get("asset_id") != asset_id]
                self._write(conv)
            except (TooManyAttachments, ConversationStoreUnavailable):
                if held.outcome == "attached":
                    try:
                        release()
                    except Exception:
                        pass                 # a stale hold only blocks a purge: the safe direction
                raise
            return conv, held

    # ── documents: the portal keeps only the text it read, as a private file beside the conversation ──
    def _doc_path(self, cid: str, doc_id: str) -> str:
        return os.path.join(self.dir, DOCS_FOLDER, cid, f"{doc_id}.txt")

    def originals_bytes(self) -> int:
        """Total size of every stored original (for the quota). Missing or unreadable folders count as zero."""
        total = 0
        base = os.path.join(self.dir, DOCS_FOLDER) if self.dir else None
        if not base or not os.path.isdir(base):
            return 0
        for root, _dirs, files in os.walk(base):
            for f in files:
                if f.endswith(".orig"):
                    try:
                        total += os.lstat(os.path.join(root, f)).st_size
                    except OSError:
                        pass
        return total

    def _original_path(self, cid: str, doc_id: str) -> str:
        return os.path.join(self.dir, DOCS_FOLDER, cid, f"{doc_id}.orig")

    def add_document(self, cid, principal, meta: dict, text: str, original: bytes | None = None):
        """Keep a document's read text with this conversation, and (when given) its ORIGINAL bytes beside it, both
        private files (0600). ``meta`` is {name, kind, bytes, chars_total, pages_read, pages_total, truncated, sha256};
        the id and time are set here. The original is skipped, and the entry says why, when the stored originals would
        pass the quota. Returns (conversation, the kept entry)."""
        with self._lock():
            conv = self._locked_conversation(cid, principal)
            rows = conv.setdefault("documents", [])
            if len(rows) >= DOCUMENTS_MAX:
                raise TooManyDocuments(DOCUMENTS_MAX)
            entry = {"id": "d-" + secrets.token_hex(8), "name": clean_file_name(meta.get("name")),
                     "kind": meta.get("kind"), "bytes": int(meta.get("bytes") or 0),
                     "chars": len(text), "chars_total": int(meta.get("chars_total") or len(text)),
                     "pages_read": meta.get("pages_read"), "pages_total": meta.get("pages_total"),
                     "truncated": bool(meta.get("truncated")), "sha256": str(meta.get("sha256") or "")[:12],
                     "added_at": now_iso()}
            path = self._doc_path(cid, entry["id"])
            opath = self._original_path(cid, entry["id"])
            keep_original = original is not None and len(original) > 0
            if keep_original and self.originals_bytes() + len(original) > ORIGINALS_TOTAL_MAX:
                keep_original, entry["original_skipped"] = False, "quota"
            if keep_original:
                entry.update({"has_original": True, "original_bytes": len(original),
                              "original_sha256": hashlib.sha256(original).hexdigest()})
            try:
                os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                    f.flush()
                    os.fsync(f.fileno())
                if keep_original:
                    fd = os.open(opath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as f:
                        f.write(original)
                        f.flush()
                        os.fsync(f.fileno())
                rows.append(entry)
                self._write(conv)
            except (OSError, ConversationStoreUnavailable) as exc:
                for leftover in (path, opath):
                    try:
                        os.unlink(leftover)
                    except OSError:
                        pass
                if isinstance(exc, ConversationStoreUnavailable):
                    raise
                raise ConversationStoreUnavailable(type(exc).__name__) from exc
            return conv, entry

    def remove_document(self, cid, principal, doc_id: str):
        """Take a document out and delete its saved text. The text goes first: a failure leaves the entry listed so
        Remove can be tried again, never a text file nobody can see. Returns (conversation, removed)."""
        if not isinstance(doc_id, str) or not DOC_ID_RE.fullmatch(doc_id):
            raise DocumentGone(doc_id)
        with self._lock():
            conv = self._locked_conversation(cid, principal)
            rows = conv.get("documents") or []
            if not any(d.get("id") == doc_id for d in rows):
                return conv, False
            for target in (self._doc_path(cid, doc_id), self._original_path(cid, doc_id)):
                try:
                    os.unlink(target)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise ConversationStoreUnavailable(type(exc).__name__) from exc
            conv["documents"] = [d for d in rows if d.get("id") != doc_id]
            self._write(conv)
            return conv, True

    def document_text(self, cid, principal, doc_id: str) -> str:
        """The saved text of one of this owner's documents, or DocumentGone."""
        conv = self.get(cid, principal)
        if not isinstance(doc_id, str) or not DOC_ID_RE.fullmatch(doc_id) \
                or not any(d.get("id") == doc_id for d in conv.get("documents") or []):
            raise DocumentGone(doc_id)
        try:
            with open(self._doc_path(cid, doc_id), encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            raise DocumentGone(doc_id) from None
        except OSError as exc:
            raise ConversationStoreUnavailable(type(exc).__name__) from exc

    def read_original(self, cid, principal, doc_id: str) -> tuple[dict, bytes]:
        """(entry, bytes) of one of this owner's kept originals, or DocumentGone. The id is resolved against this
        owner's conversation, never from a path; a symlink or anything that is not a regular file is refused, and the
        bytes must still hash to what was stored."""
        conv = self.get(cid, principal)
        entry = next((d for d in conv.get("documents") or [] if d.get("id") == doc_id), None)
        if entry is None or not DOC_ID_RE.fullmatch(str(doc_id)) or not entry.get("has_original"):
            raise DocumentGone(doc_id)
        path = self._original_path(cid, doc_id)
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except (FileNotFoundError, NotADirectoryError):
            raise DocumentGone(doc_id) from None
        except OSError as exc:                      # ELOOP: a symlink was put where the file should be
            raise DocumentGone(doc_id) from exc
        try:
            import stat as _stat
            if not _stat.S_ISREG(os.fstat(fd).st_mode):
                raise DocumentGone(doc_id)
            with os.fdopen(fd, "rb") as f:
                fd = None
                data = f.read(ORIGINAL_READ_MAX + 1)
        finally:
            if fd is not None:
                os.close(fd)
        if len(data) > ORIGINAL_READ_MAX or hashlib.sha256(data).hexdigest() != entry.get("original_sha256"):
            raise DocumentGone(doc_id)              # tampered or damaged: never served as the owner's file
        return entry, data

    def record_turn_files(self, cid, principal, note_id: str, report: list):
        """Remember which files went with the message of ``note_id`` (what the server put in the request), so a reload
        shows the same line. Only the report is kept, never file text; the newest TURN_FILES_MAX are kept."""
        with self._lock():
            conv = self._locked_conversation(cid, principal)
            turns = conv.setdefault("turn_files", {})
            turns.pop(note_id, None)
            turns[note_id] = report
            while len(turns) > TURN_FILES_MAX:
                turns.pop(next(iter(turns)))
            self._write(conv)

    def retry_release(self, cid, principal, asset_id: str, release):
        """(conversation, outcome). Finishes a removal whose release did not complete, and ONLY that. It never removes
        a pointer: if the image is attached again in the meantime the stale marker is dropped and the image is left
        alone ("current"). Outcomes: "released", "pending" (still not released), "current" (attached again), "nothing_pending"."""
        with self._lock():
            conv = self._locked_conversation(cid, principal)
            attached = any(a.get("asset_id") == asset_id for a in conv.get("attachments") or [])
            marks = conv.get("release_pending") or []
            has_mark = any(p.get("asset_id") == asset_id for p in marks)
            if attached:
                if has_mark:
                    conv["release_pending"] = [p for p in marks if p.get("asset_id") != asset_id]
                    self._write(conv)
                return conv, "current"
            if not has_mark:
                return conv, "nothing_pending"
            try:
                released = bool(release())
            except Exception:
                released = False
            if not released:
                return conv, "pending"
            conv["release_pending"] = [p for p in marks if p.get("asset_id") != asset_id]
            try:
                self._write(conv)
            except ConversationStoreUnavailable:
                pass                       # the marker stays; the next retry finds the hold gone and clears it
            return conv, "released"

    def detach_held(self, cid, principal, asset_id: str, release):
        """(conversation, released). Takes the image out of this conversation and lets go of its hold. Safe to
        repeat: a second call retries a release that did not finish. ``released`` is True only if the hold is
        really gone; otherwise the conversation carries a durable "release pending" entry."""
        with self._lock():
            conv = self._locked_conversation(cid, principal)
            gone = [a for a in conv.get("attachments") or [] if a.get("asset_id") == asset_id]
            conv["attachments"] = [a for a in conv.get("attachments") or [] if a.get("asset_id") != asset_id]
            pending = [p for p in conv.get("release_pending") or [] if p.get("asset_id") != asset_id]
            known = gone[0].get("name") if gone else next((p.get("name") for p in conv.get("release_pending") or []
                                                           if p.get("asset_id") == asset_id), None)
            pending.append({"asset_id": asset_id, "name": clean_file_name(known or "image"), "since": now_iso()})
            conv["release_pending"] = pending
            self._write(conv)                         # durable first: the pointer is gone and the intent is recorded
            try:
                released = bool(release())
            except Exception:
                released = False
            if released:
                conv["release_pending"] = [p for p in pending if p.get("asset_id") != asset_id]
                try:
                    self._write(conv)
                except ConversationStoreUnavailable:
                    pass                               # the marker stays; a retry finds nothing to release and clears it
            return conv, released

    def touch(self, cid, principal, *, first_text: str | None = None) -> dict:
        """A note or reply was kept: bump updated_at; the first message titles it."""
        def fn(c):
            c["updated_at"] = now_iso()
            if first_text and c.get("title_source") == "default":
                c["title"], c["title_source"] = title_from(first_text), "first_message"
        return self._change(cid, principal, fn)


def public(conv: dict) -> dict:
    """What a browser may see of a conversation (no keys, no floor keys)."""
    return {k: conv.get(k) for k in ("id", "title", "created_at", "updated_at", "pinned", "archived",
                                     "archived_at", "work_item", "legacy")} | {
        "attachments": [dict(a) for a in conv.get("attachments") or []],
        "release_pending": [{"asset_id": p.get("asset_id"), "name": p.get("name")}
                            for p in conv.get("release_pending") or []],
        "documents": [{k: d.get(k) for k in ("id", "name", "kind", "bytes", "chars", "chars_total", "pages_read",
                                             "pages_total", "truncated", "added_at", "has_original", "original_bytes",
                                             "original_skipped")}
                      for d in conv.get("documents") or []],
        "turn_files": dict(conv.get("turn_files") or {})}


def conversations_of(services) -> ConversationStore:
    store = getattr(services, "store", None)
    folder = getattr(store, "folder", None) if store is not None and getattr(store, "path", None) else None
    floor = getattr(getattr(services, "floor", None), "floor", "shop")
    return ConversationStore(folder, base_floor=floor)


def session_conversation_id(conv: dict, principal: str, *, day: str | None = None) -> str:
    """The id the MC backend maps to its own session (MiniMoi's OpenClaw
    adapter hashes it into the `user` field, from which OpenClaw derives its
    session key; another backend may map it differently).

    Context policy (Robert to confirm, #265 review F1):
    * the Shop floor thread keeps its daily rollover exactly as before slice 2
      (``<floor>:<principal>:<UTC day>``): it starts fresh every day;
    * a new conversation is one session for its whole life: it keeps its
      context until Robert archives it (OpenClaw compacts older turns past
      its window: docker/mc-agent/openclaw.json).
    """
    if conv.get("legacy"):
        day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"{conv.get('notes_floor')}:{principal}:{day}"
    return f"guild:{principal}:{conv['id']}"


__all__ = ["ConversationStore", "ConversationStoreUnavailable", "ConversationNotFound", "TooManyAttachments",
           "ASSET_ID_RE", "ATTACHMENTS_MAX", "clean_file_name", "LEGACY_ID",
           "title_from", "clean_title", "public", "conversations_of", "session_conversation_id", "KEY_RE"]
