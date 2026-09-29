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

LEGACY_ID = "shop-floor-thread"
LEGACY_TITLE = "Shop floor thread"
DEFAULT_TITLE = "New conversation"
TITLE_MAX = 80
ID_RE = re.compile(r"^(shop-floor-thread|c-[0-9a-f]{12})$")
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
FOLDER = "conversations"


class ConversationStoreUnavailable(RuntimeError):
    """The conversations folder cannot be read or written."""


class ConversationNotFound(LookupError):
    pass


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
        rows = self.list(principal)
        return max(rows, key=lambda c: c.get("updated_at") or "") if rows else self.get(LEGACY_ID, principal)

    # ── writes (no model call anywhere) ────────────────────────────────
    def create(self, principal: str, *, key: str, work_item: dict | None = None) -> tuple[dict, bool]:
        """(conversation, repeated): one conversation per idempotency key."""
        with self._lock():
            self._migrate(principal)
            for conv in self._all():
                if conv.get("created_key") == key and conv.get("principal") == principal:
                    return conv, True
            cid = "c-" + secrets.token_hex(6)
            now = now_iso()
            conv = {"id": cid, "principal": principal, "title": DEFAULT_TITLE, "title_source": "default",
                    "created_at": now, "updated_at": now, "pinned": False, "archived": False, "archived_at": None,
                    "work_item": work_item, "legacy": False, "notes_floor": f"{self.base_floor}/{cid}",
                    "created_key": key}
            return self._write(conv), False

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
                                     "archived_at", "work_item", "legacy")}


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


__all__ = ["ConversationStore", "ConversationStoreUnavailable", "ConversationNotFound", "LEGACY_ID",
           "title_from", "clean_title", "public", "conversations_of", "session_conversation_id", "KEY_RE"]
