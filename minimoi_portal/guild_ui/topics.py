"""Topic workshop store (Workshop W1, 7 Oct 2026; WORKSHOP_BUILD_SPEC.md sections 2 and 4).

File first, server owned, one owner, no database: plain files under ``<guild data folder>/topics/``.

    topics/<tid>/topic.json                    the topic (title, summary, linked conversation, owner)
    topics/<tid>/items/<iid>/item.json         one card (note, document, design or request) and its revision list
    topics/<tid>/items/<iid>/rev/<REV>.md      the text of a note, document or request revision
    topics/<tid>/items/<iid>/rev/<REV>.<ext>   a design revision's sanitised image, with <REV>.thumb.webp beside it
    topics/<tid>/items/<iid>/comments.jsonl    append-only events: comment, resolve, disposition
    topics/<tid>/journal.jsonl                 append-only collaboration record (who said what, with links to sources)
    topics/<tid>/layout.<owner>.json           the owner's card order, wider cards and last view

Rules (each one tested): every id is made by the server and matched against a regex before it touches a path; every write is
under one lock and atomic (temp file, fsync, rename; appends are fsynced); nothing is ever deleted (Put away sets a flag and
a change is a new revision); sizes are capped; an unreadable file is skipped and counted, never a reason to hide the rest.
Nothing here calls a model, a network or a shell.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone

from .payment_scrub import scrub as _payment_scrub

FOLDER = "topics"
TOPIC_RE = re.compile(r"^t-[0-9a-f]{12}$")
ITEM_RE = re.compile(r"^i-[0-9a-f]{12}$")
COMMENT_RE = re.compile(r"^m-[0-9a-f]{12}$")
JOURNAL_RE = re.compile(r"^j-[0-9a-f]{12}$")
REV_RE = re.compile(r"^[A-Z]{1,2}$")
OWNER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
CONV_RE = re.compile(r"^(shop-floor-thread|c-[0-9a-f]{12})$")

KINDS = ("note", "document", "design", "request")
STAGES = ("queued", "delivered", "acknowledged", "working", "returned", "paused")
DISPOSITIONS = ("accepted", "changed", "declined", "deferred")
VIEWS = ("chat", "cards", "split")
JOURNAL_KINDS = ("note", "finding", "question", "alternative", "disagreement", "evidence", "decision", "decision_change",
                 "handoff", "review", "delegation", "capability_gap", "proposal")
# What an agent may contribute through the inbox. A decision is the owner's: an agent's "decision" is stored as a proposal.
INBOX_KINDS = ("note", "finding", "question", "alternative", "disagreement", "evidence", "proposal", "handoff", "review", "delegation", "capability_gap")
INBOX_AS_PROPOSAL = ("decision", "decision_change")
AGENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
INBOX_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{5,95}\.json$")
INBOX_FILE_MAX = 16_000
INBOX_BATCH_FILES = 50
INBOX_BATCH_BYTES = 400_000
INBOX_KEYS = {"v", "kind", "text", "refs", "model", "session_ref"}
AUTHOR_KINDS = ("owner", "agent", "mc")
VIA = ("ui", "inbox", "mc")
REF_TYPES = ("item", "revision", "comment", "file", "commit", "conversation", "url")

TITLE_MAX = 120
SUMMARY_MAX = 400
TEXT_MAX = 200_000          # a document
NOTE_MAX = 8_000            # a note or a request's description
COMMENT_MAX = 4_000
JOURNAL_MAX = 8_000
ITEMS_MAX = 2_000
COMMENTS_MAX = 2_000
JOURNAL_LINES_MAX = 20_000
QUOTE_MAX = 300


class TopicStoreUnavailable(RuntimeError):
    """The topics folder cannot be read or written."""


class TopicNotFound(LookupError):
    pass


class ItemNotFound(LookupError):
    pass


class Refused(ValueError):
    """The request is not allowed or not valid; ``code`` is stable, ``message`` is for the person."""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(6)}"


def _text(value, limit: int, what: str, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise Refused("invalid", f"The {what} must be text.")
    if len(value) > limit * 4 + 64:                    # refuse absurd input before scanning it
        raise Refused("too_long", f"The {what} is longer than {limit:,} characters.")
    value = _payment_scrub(value).strip()              # payment details are stripped here, for every stored text field, whoever calls
    if required and not value:
        raise Refused("invalid", f"The {what} is empty.")
    if len(value) > limit:
        raise Refused("too_long", f"The {what} is longer than {limit:,} characters.")
    return value


def clean_title(value) -> str:
    return " ".join(_text(value, TITLE_MAX, "title").split())


def next_rev(revs: list[dict]) -> str:
    n = len(revs)
    if n >= 26 * 27:
        raise Refused("too_many", "This item has too many revisions.")
    return chr(65 + n) if n < 26 else chr(65 + n // 26 - 1) + chr(65 + n % 26)


def clean_anchor(anchor, *, kind: str) -> dict:
    """An anchor says where on a revision a comment points. {"type":"item"} is the whole item; a document block is
    {"type":"block","index":n,"quote":"…"}; a design point is {"type":"point","x":0..1,"y":0..1,"w":<viewport px>}."""
    if anchor is None:
        return {"type": "item"}
    if not isinstance(anchor, dict):
        raise Refused("invalid", "The anchor must be an object.")
    t = anchor.get("type")
    if t == "item":
        return {"type": "item"}
    if t == "block":
        if kind not in ("document", "note", "request"):
            raise Refused("invalid", "Only text items have blocks to comment on.")
        idx = anchor.get("index")
        if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < 5000:
            raise Refused("invalid", "The block number is not valid.")
        quote = _text(anchor.get("quote", ""), QUOTE_MAX, "quoted text", required=False)
        return {"type": "block", "index": idx, "quote": quote}
    if t == "point":
        if kind != "design":
            raise Refused("invalid", "Only designs have points to comment on.")
        out = {}
        for k in ("x", "y"):
            v = anchor.get(k)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
                raise Refused("invalid", "A point is two numbers between 0 and 1.")
            out[k] = round(float(v), 4)
        w = anchor.get("w", 0)
        if isinstance(w, bool) or not isinstance(w, int) or not 0 <= w <= 10_000:
            raise Refused("invalid", "The viewport width is not valid.")
        return {"type": "point", **out, "w": w}
    raise Refused("invalid", "Unknown anchor type.")


class TopicStore:
    def __init__(self, folder: str | None):
        self.dir = os.path.join(folder, FOLDER) if folder else None
        self.unreadable = 0

    # ── files ──────────────────────────────────────────────────────────
    def _ensure(self):
        if not self.dir:
            raise TopicStoreUnavailable("no guild data folder")
        try:
            os.makedirs(self.dir, mode=0o700, exist_ok=True)
        except OSError as exc:
            raise TopicStoreUnavailable(type(exc).__name__) from exc

    @contextmanager
    def _lock(self):
        self._ensure()
        try:
            fd = os.open(os.path.join(self.dir, ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise TopicStoreUnavailable(type(exc).__name__) from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _tdir(self, tid: str) -> str:
        if not TOPIC_RE.fullmatch(tid or ""):
            raise TopicNotFound(tid)
        return os.path.join(self.dir, tid)

    def _idir(self, tid: str, iid: str) -> str:
        if not ITEM_RE.fullmatch(iid or ""):
            raise ItemNotFound(iid)
        return os.path.join(self._tdir(tid), "items", iid)

    @staticmethod
    def _set_aside(path: str):
        """A revision file that no item.json names is the leftover of a crash between the two writes. Keep it (renamed), never reuse
        or delete it, so the next revision can take its letter."""
        if os.path.exists(path):
            os.replace(path, f"{path}.orphan-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}")

    @staticmethod
    def _write_bytes(path: str, data: bytes, *, mode: int = 0o600, exclusive: bool = False):
        d = os.path.dirname(path)
        os.makedirs(d, mode=0o700, exist_ok=True)
        if exclusive and os.path.exists(path):
            raise Refused("exists", "That file already exists and is never replaced.", 409)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        except OSError as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise TopicStoreUnavailable(type(exc).__name__) from exc

    def _write_json(self, path: str, obj: dict):
        self._write_bytes(path, json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False).encode("utf-8"))

    @staticmethod
    def _read_json(path: str) -> dict | None:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise TopicStoreUnavailable(f"{os.path.basename(path)}: {type(exc).__name__}") from exc
        return data if isinstance(data, dict) else None

    @staticmethod
    def _append_line(path: str, obj: dict):
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        line = (json.dumps(obj, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                size = os.fstat(fd).st_size
                if size and os.pread(fd, 1, size - 1) != b"\n":      # a crash left half a line: end it, so the new entry is not lost with it
                    line = b"\n" + line
                os.write(fd, line)
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            raise TopicStoreUnavailable(type(exc).__name__) from exc

    def _read_lines(self, path: str) -> list[dict]:
        out, bad = [], 0
        try:
            with open(path, encoding="utf-8") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw)
                    except ValueError:
                        bad += 1
                        continue
                    if isinstance(obj, dict):
                        out.append(obj)
                    else:
                        bad += 1
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise TopicStoreUnavailable(type(exc).__name__) from exc
        self.unreadable += bad
        return out

    def _count_lines(self, path: str) -> int:
        try:
            with open(path, "rb") as f:
                return sum(1 for _ in f)
        except FileNotFoundError:
            return 0
        except OSError as exc:
            raise TopicStoreUnavailable(type(exc).__name__) from exc

    # ── topics ─────────────────────────────────────────────────────────
    def _topic(self, tid: str, owner: str) -> dict:
        self._ensure()
        t = self._read_json(os.path.join(self._tdir(tid), "topic.json"))
        if not t or t.get("id") != tid or t.get("owner") != owner:
            raise TopicNotFound(tid)
        return t

    def create_topic(self, owner: str, title, summary="", *, conversation_id: str | None = None) -> dict:
        if not OWNER_RE.fullmatch(owner or ""):
            raise Refused("invalid", "Unknown owner.", 403)
        title = clean_title(title)
        summary = _text(summary or "", SUMMARY_MAX, "summary", required=False)
        if conversation_id is not None and not CONV_RE.fullmatch(conversation_id):
            raise Refused("invalid", "That conversation id is not valid.")
        with self._lock():
            tid = _new_id("t")
            now = now_iso()
            topic = {"id": tid, "title": title, "summary": summary, "owner": owner, "conversation_id": conversation_id,
                     "created": now, "updated": now, "archived": False}
            self._write_json(os.path.join(self._tdir(tid), "topic.json"), topic)
            return topic

    def set_conversation(self, tid: str, owner: str, conversation_id: str) -> dict:
        if not CONV_RE.fullmatch(conversation_id or ""):
            raise Refused("invalid", "That conversation id is not valid.")
        with self._lock():
            t = self._topic(tid, owner)
            t["conversation_id"], t["updated"] = conversation_id, now_iso()
            self._write_json(os.path.join(self._tdir(tid), "topic.json"), t)
            return t

    def update_topic(self, tid: str, owner: str, *, title=None, summary=None, archived=None) -> dict:
        with self._lock():
            t = self._topic(tid, owner)
            if title is not None:
                t["title"] = clean_title(title)
            if summary is not None:
                t["summary"] = _text(summary, SUMMARY_MAX, "summary", required=False)
            if archived is not None:
                t["archived"] = bool(archived)
            t["updated"] = now_iso()
            self._write_json(os.path.join(self._tdir(tid), "topic.json"), t)
            return t

    def list_topics(self, owner: str, *, q: str | None = None, archived: bool = False) -> list[dict]:
        self._ensure()
        try:
            names = sorted(n for n in os.listdir(self.dir) if TOPIC_RE.fullmatch(n))
        except OSError as exc:
            raise TopicStoreUnavailable(type(exc).__name__) from exc
        words = [w for w in (q or "").lower().split() if w]
        rows, bad = [], 0
        for tid in names:
            try:
                t = self._topic(tid, owner)
            except (TopicNotFound, TopicStoreUnavailable):
                bad += 1
                continue
            if bool(t.get("archived")) != archived:
                continue
            items = self._items(tid)
            if words:
                hay = " ".join([t["title"], t.get("summary", "")] + [i["title"] for i in items]).lower()
                if not all(w in hay for w in words):
                    continue
            live = [i for i in items if not i["archived"]]
            rows.append({"id": tid, "title": t["title"], "summary": t.get("summary", ""), "updated": t["updated"],
                         "cards": len(live), "waiting": sum(1 for i in live if i["kind"] == "request" and i.get("request", {}).get("stage") in ("queued", "delivered", "acknowledged", "working")),
                         "conversation_id": t.get("conversation_id")})
        self.unreadable += bad
        rows.sort(key=lambda r: r["updated"], reverse=True)
        return rows

    def get_topic(self, tid: str, owner: str) -> dict:
        t = self._topic(tid, owner)
        items = self._items(tid)
        for it in items:                                   # how many comments still need an answer, for the quiet badge on a card
            it["comments_open"] = sum(1 for c in self.comments(tid, it["id"], owner) if c["status"] == "open" and not c["reply_to"])
        layout = self.get_layout(tid, owner)
        return {"topic": t, "items": items, "layout": layout}

    # ── items ──────────────────────────────────────────────────────────
    def _items(self, tid: str) -> list[dict]:
        base = os.path.join(self._tdir(tid), "items")
        try:
            names = sorted(n for n in os.listdir(base) if ITEM_RE.fullmatch(n))
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise TopicStoreUnavailable(type(exc).__name__) from exc
        out, bad = [], 0
        for iid in names:
            try:
                it = self._read_json(os.path.join(base, iid, "item.json"))
            except TopicStoreUnavailable:
                it = None
            if it and it.get("id") == iid:
                out.append(it)
            else:
                bad += 1
        self.unreadable += bad
        out.sort(key=lambda i: i["created"])
        return out

    def _item(self, tid: str, iid: str) -> dict:
        it = self._read_json(os.path.join(self._idir(tid, iid), "item.json"))
        if not it or it.get("id") != iid:
            raise ItemNotFound(iid)
        return it

    def _save_item(self, tid: str, item: dict):
        item["updated"] = now_iso()
        self._write_json(os.path.join(self._idir(tid, item["id"]), "item.json"), item)

    def _touch_topic(self, tid: str, t: dict):
        t["updated"] = now_iso()
        self._write_json(os.path.join(self._tdir(tid), "topic.json"), t)

    def _rev_path(self, tid: str, iid: str, rev: str, ext: str) -> str:
        if not REV_RE.fullmatch(rev or ""):
            raise Refused("invalid", "That revision is not valid.")
        return os.path.join(self._idir(tid, iid), "rev", f"{rev}.{ext}")

    def add_item(self, tid: str, owner: str, kind: str, title, *, text="", by=None, request: dict | None = None,
                 image=None, note: str = "") -> dict:
        """Create a card. note/document/request carry text (revision A); a design carries a sanitised image
        (``image`` = media.Sanitized) as revision A."""
        if kind not in KINDS:
            raise Refused("invalid", "Unknown item kind.")
        title = clean_title(title)
        if kind == "design":
            if image is None:
                raise Refused("invalid", "A design needs an image.")
            body = b""
        else:
            limit = TEXT_MAX if kind == "document" else NOTE_MAX
            body = _text(text, limit, "text", required=(kind != "request")).encode("utf-8")
        req = None
        if kind == "request":
            req = self._clean_request(request or {})
        with self._lock():
            t = self._topic(tid, owner)
            if len(self._items(tid)) >= ITEMS_MAX:
                raise Refused("too_many", f"A topic keeps at most {ITEMS_MAX:,} items.")
            iid = _new_id("i")
            now = now_iso()
            author = by or owner
            rev = {"rev": "A", "created": now, "by": author, "note": _text(note or "", 400, "note", required=False)}
            if kind == "design":
                ext = image.ext if re.fullmatch(r"[a-z0-9]{2,5}", image.ext or "") else "img"
                self._write_bytes(self._rev_path(tid, iid, "A", ext), image.data, exclusive=True)
                self._write_bytes(self._rev_path(tid, iid, "A", "thumb.webp"), image.thumb, exclusive=True)
                rev.update(file=f"A.{ext}", sha256=image.sha256, bytes=len(image.data), mime=image.mime, width=image.width, height=image.height)
            else:
                self._write_bytes(self._rev_path(tid, iid, "A", "md"), body, exclusive=True)
                rev.update(file="A.md", bytes=len(body))
            item = {"id": iid, "topic": tid, "kind": kind, "title": title, "by": author, "created": now, "updated": now,
                    "archived": False, "archived_at": None, "current_rev": "A", "revisions": [rev]}
            if req is not None:
                item["request"] = req
            self._write_json(os.path.join(self._idir(tid, iid), "item.json"), item)
            self._touch_topic(tid, t)
            return item

    @staticmethod
    def _clean_request(req: dict, *, existing: dict | None = None) -> dict:
        to = _text(req.get("to", (existing or {}).get("to", "")), 80, "recipient")
        stage = req.get("stage", (existing or {}).get("stage", "queued"))
        if stage not in STAGES:
            raise Refused("invalid", "Unknown stage.")
        return {"to": to, "stage": stage, "stage_source": (existing or {}).get("stage_source", "set by the owner"),
                "stage_at": (existing or {}).get("stage_at", now_iso()),
                "included": _text(req.get("included", (existing or {}).get("included", "")), 400, "what is included", required=False)}

    def get_item(self, tid: str, iid: str, owner: str, *, rev: str | None = None) -> dict:
        self._topic(tid, owner)
        item = self._item(tid, iid)
        rev = rev or item["current_rev"]
        meta = next((r for r in item["revisions"] if r["rev"] == rev), None)
        if meta is None:
            raise Refused("not_found", "That revision does not exist.", 404)
        out = dict(item)
        out["rev"] = meta
        if item["kind"] == "design":
            out["text"] = ""
        else:
            try:
                with open(self._rev_path(tid, iid, rev, "md"), encoding="utf-8") as f:
                    out["text"] = f.read()
            except FileNotFoundError:
                out["text"] = ""
            except OSError as exc:
                raise TopicStoreUnavailable(type(exc).__name__) from exc
        out["comments"] = self.comments(tid, iid, owner)
        return out

    def add_revision(self, tid: str, iid: str, owner: str, *, text=None, image=None, by=None, note: str = "") -> dict:
        with self._lock():
            t = self._topic(tid, owner)
            item = self._item(tid, iid)
            if item["archived"]:
                raise Refused("archived", "Bring this item back before changing it.", 409)
            rev = next_rev(item["revisions"])
            for ext in ("md", "thumb.webp", *(("png", "jpg", "jpeg", "webp", "gif", "img") if item["kind"] == "design" else ())):
                self._set_aside(self._rev_path(tid, iid, rev, ext))        # nothing names this letter yet, so any file with it is an orphan
            author = by or owner
            meta = {"rev": rev, "created": now_iso(), "by": author, "note": _text(note or "", 400, "note", required=False)}
            if item["kind"] == "design":
                if image is None:
                    raise Refused("invalid", "A design revision needs an image.")
                ext = image.ext if re.fullmatch(r"[a-z0-9]{2,5}", image.ext or "") else "img"
                self._write_bytes(self._rev_path(tid, iid, rev, ext), image.data, exclusive=True)
                self._write_bytes(self._rev_path(tid, iid, rev, "thumb.webp"), image.thumb, exclusive=True)
                meta.update(file=f"{rev}.{ext}", sha256=image.sha256, bytes=len(image.data), mime=image.mime, width=image.width, height=image.height)
            else:
                body = _text(text, TEXT_MAX if item["kind"] == "document" else NOTE_MAX, "text").encode("utf-8")
                self._write_bytes(self._rev_path(tid, iid, rev, "md"), body, exclusive=True)
                meta.update(file=f"{rev}.md", bytes=len(body))
            item["revisions"].append(meta)
            item["current_rev"] = rev
            self._save_item(tid, item)
            self._touch_topic(tid, t)
            return item

    def design_file(self, tid: str, iid: str, owner: str, rev: str | None, *, thumb: bool = False) -> tuple[str, str]:
        """(path, mime) of a design revision's image, for serving to the owner."""
        self._topic(tid, owner)
        item = self._item(tid, iid)
        if item["kind"] != "design":
            raise ItemNotFound(iid)
        rev = rev or item["current_rev"]
        meta = next((r for r in item["revisions"] if r["rev"] == rev), None)
        if meta is None:
            raise ItemNotFound(iid)
        if thumb:
            return self._rev_path(tid, iid, rev, "thumb.webp"), "image/webp"
        return os.path.join(self._idir(tid, iid), "rev", meta["file"]), meta.get("mime", "application/octet-stream")

    def set_archived(self, tid: str, iid: str, owner: str, archived: bool) -> dict:
        with self._lock():
            t = self._topic(tid, owner)
            item = self._item(tid, iid)
            item["archived"] = bool(archived)
            item["archived_at"] = now_iso() if archived else None
            self._save_item(tid, item)
            self._touch_topic(tid, t)
            return item

    def set_stage(self, tid: str, iid: str, owner: str, stage: str, *, recipient: str | None = None) -> dict:
        """The owner sets a request's stage or recipient by hand; the record says so. No agent reports are accepted in W1."""
        with self._lock():
            t = self._topic(tid, owner)
            item = self._item(tid, iid)
            if item["kind"] != "request":
                raise Refused("invalid", "Only a request has a delivery stage.")
            req = dict(item.get("request") or {})
            if stage is not None:
                if stage not in STAGES:
                    raise Refused("invalid", "Unknown stage.")
                req["stage"], req["stage_at"], req["stage_source"] = stage, now_iso(), "set by the owner"
            if recipient is not None:
                req["to"] = _text(recipient, 80, "recipient")
            item["request"] = req
            self._save_item(tid, item)
            self._touch_topic(tid, t)
            return item

    # ── layout ─────────────────────────────────────────────────────────
    def get_layout(self, tid: str, owner: str) -> dict:
        self._topic(tid, owner)
        lay = self._read_json(os.path.join(self._tdir(tid), f"layout.{owner}.json")) or {}
        return {"order": [i for i in lay.get("order", []) if ITEM_RE.fullmatch(str(i))],
                "wide": [i for i in lay.get("wide", []) if ITEM_RE.fullmatch(str(i))],
                "last_view": lay.get("last_view") if lay.get("last_view") in VIEWS else None}

    def set_layout(self, tid: str, owner: str, *, order=None, wide=None, last_view=None) -> dict:
        with self._lock():
            self._topic(tid, owner)
            known = {i["id"] for i in self._items(tid)}
            lay = self.get_layout(tid, owner)
            if order is not None:
                if not isinstance(order, list) or len(order) > ITEMS_MAX or any(i not in known for i in order) or len(set(order)) != len(order):
                    raise Refused("invalid", "The order must list each existing item once.")
                lay["order"] = order
            if wide is not None:
                if not isinstance(wide, list) or any(i not in known for i in wide):
                    raise Refused("invalid", "The wide list must name existing items.")
                lay["wide"] = list(dict.fromkeys(wide))
            if last_view is not None:
                if last_view not in VIEWS:
                    raise Refused("invalid", "Unknown view.")
                lay["last_view"] = last_view
            self._write_json(os.path.join(self._tdir(tid), f"layout.{owner}.json"), {**lay, "owner": owner})
            return lay

    # ── comments (append-only events) ─────────────────────────────────
    def comments(self, tid: str, iid: str, owner: str) -> list[dict]:
        self._topic(tid, owner)
        path = os.path.join(self._idir(tid, iid), "comments.jsonl")
        by_id: dict[str, dict] = {}
        order: list[str] = []
        for e in self._read_lines(path):
            t = e.get("t")
            if t == "comment" and COMMENT_RE.fullmatch(str(e.get("id"))):
                c = {k: e.get(k) for k in ("id", "item", "rev", "anchor", "by", "text", "at", "reply_to")}
                c.update(status="open", resolved_at=None, resolved_by=None, disposition=None)
                by_id[c["id"]] = c
                order.append(c["id"])
            elif t == "resolve" and e.get("id") in by_id:
                by_id[e["id"]].update(status="resolved" if e.get("resolved", True) else "open",
                                      resolved_at=e.get("at"), resolved_by=e.get("by"))
            elif t == "disposition" and e.get("id") in by_id:
                by_id[e["id"]]["disposition"] = {"value": e.get("value"), "by": e.get("by"), "at": e.get("at"), "rev": e.get("rev")}
        return [by_id[i] for i in order]

    def add_comment(self, tid: str, iid: str, owner: str, text, *, rev: str | None = None, anchor=None,
                    reply_to: str | None = None, by: dict | None = None) -> dict:
        with self._lock():
            t = self._topic(tid, owner)
            item = self._item(tid, iid)
            rev = rev or item["current_rev"]
            if not any(r["rev"] == rev for r in item["revisions"]):
                raise Refused("not_found", "That revision does not exist.", 404)
            path = os.path.join(self._idir(tid, iid), "comments.jsonl")
            existing = self.comments(tid, iid, owner)
            if len(existing) >= COMMENTS_MAX:
                raise Refused("too_many", f"An item keeps at most {COMMENTS_MAX:,} comments.")
            if reply_to is not None:
                parent = next((c for c in existing if c["id"] == reply_to), None)
                if parent is None:
                    raise Refused("not_found", "The comment being replied to does not exist.", 404)
                rev, anchor = parent["rev"], parent["anchor"]
            cid = _new_id("m")
            entry = {"t": "comment", "id": cid, "item": iid, "rev": rev, "anchor": clean_anchor(anchor, kind=item["kind"]),
                     "by": by or {"kind": "owner", "name": owner}, "text": _text(text, COMMENT_MAX, "comment"), "at": now_iso(),
                     "reply_to": reply_to}
            self._append_line(path, entry)
            self._touch_topic(tid, t)
            return {k: entry[k] for k in ("id", "item", "rev", "anchor", "by", "text", "at", "reply_to")} | {
                "status": "open", "resolved_at": None, "resolved_by": None, "disposition": None}

    def resolve_comment(self, tid: str, iid: str, owner: str, cid: str, *, resolved: bool = True) -> dict:
        if not COMMENT_RE.fullmatch(cid or ""):
            raise Refused("not_found", "No such comment.", 404)
        with self._lock():
            self._topic(tid, owner)
            self._item(tid, iid)
            if not any(c["id"] == cid for c in self.comments(tid, iid, owner)):
                raise Refused("not_found", "No such comment.", 404)
            self._append_line(os.path.join(self._idir(tid, iid), "comments.jsonl"),
                              {"t": "resolve", "id": cid, "resolved": bool(resolved), "by": owner, "at": now_iso()})
            return next(c for c in self.comments(tid, iid, owner) if c["id"] == cid)

    def set_disposition(self, tid: str, iid: str, owner: str, cid: str, value: str) -> dict:
        """Only the owner records a disposition; the record names the revision it was given on. Never inferred, never from an agent."""
        if value not in DISPOSITIONS:
            raise Refused("invalid", "Unknown disposition.")
        if not COMMENT_RE.fullmatch(cid or ""):
            raise Refused("not_found", "No such comment.", 404)
        with self._lock():
            self._topic(tid, owner)
            item = self._item(tid, iid)
            c = next((c for c in self.comments(tid, iid, owner) if c["id"] == cid), None)
            if c is None:
                raise Refused("not_found", "No such comment.", 404)
            self._append_line(os.path.join(self._idir(tid, iid), "comments.jsonl"),
                              {"t": "disposition", "id": cid, "value": value, "by": owner, "at": now_iso(), "rev": item["current_rev"]})
            return next(x for x in self.comments(tid, iid, owner) if x["id"] == cid)

    # ── journal (the collaboration record) ────────────────────────────
    def journal(self, tid: str, owner: str, *, limit: int = 200) -> list[dict]:
        self._topic(tid, owner)
        rows = self._read_lines(os.path.join(self._tdir(tid), "journal.jsonl"))
        return rows[-max(1, min(limit, 1000)):]

    def append_journal(self, tid: str, owner: str, *, author: dict, via: str, kind: str, text, refs=None,
                       verified: bool = False) -> dict:
        if kind not in JOURNAL_KINDS:
            raise Refused("invalid", "Unknown journal kind.")
        if via not in VIA:
            raise Refused("invalid", "Unknown source of the entry.")
        if not isinstance(author, dict) or author.get("kind") not in AUTHOR_KINDS:
            raise Refused("invalid", "The author is not valid.")
        name = _text(author.get("name", ""), 80, "author name")
        clean_author = {"kind": author["kind"], "name": name}
        for k in ("model", "session_ref"):
            if author.get(k):
                clean_author[k] = _text(str(author[k]), 120, k, required=False)
        clean_refs = []
        for r in (refs or [])[:20]:
            if not isinstance(r, dict) or r.get("type") not in REF_TYPES:
                raise Refused("invalid", "A reference needs a known type.")
            ref = {"type": r["type"], "ref": _text(str(r.get("ref", "")), 300, "reference")}
            if r.get("quote"):
                ref["quote"] = _text(str(r["quote"]), QUOTE_MAX, "quote", required=False)
            clean_refs.append(ref)
        with self._lock():
            t = self._topic(tid, owner)
            path = os.path.join(self._tdir(tid), "journal.jsonl")
            if self._count_lines(path) >= JOURNAL_LINES_MAX:
                raise Refused("too_many", "The journal is full.", 409)
            entry = {"id": _new_id("j"), "at": now_iso(), "author": clean_author, "via": via, "kind": kind,
                     "text": _text(text, JOURNAL_MAX, "entry"), "refs": clean_refs, "verified": bool(verified)}
            self._append_line(path, entry)
            self._touch_topic(tid, t)
            return entry

    # ── the inbox: untrusted contributions, imported only when asked ──────
    def put_inbox(self, tid: str, agent: str, *, kind: str, text: str, refs=None, model: str = "", session_ref: str = "") -> str:
        """What an agent's tool does: write ONE bounded JSON file into ``inbox/<agent>/`` (temp file, then rename). It touches nothing
        else. The folder name is the agent's CLAIM of who it is; nothing here or at import time proves it."""
        if not AGENT_RE.fullmatch(agent or ""):
            raise Refused("invalid", "The agent name must be lower-case letters, digits, - or _ (up to 32).")
        self._ensure()
        if not os.path.isdir(self._tdir(tid)):                         # the topic must exist; its id is regex-checked by _tdir
            raise TopicNotFound(tid)
        body = {"v": 1, "kind": kind, "text": text, "refs": refs or [], "model": model, "session_ref": session_ref}
        data = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(data) > INBOX_FILE_MAX:
            raise Refused("too_long", f"A contribution is at most {INBOX_FILE_MAX:,} bytes.")
        name = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(4)}.json"
        for folder in (os.path.join(self._tdir(tid), "inbox"), os.path.join(self._tdir(tid), "inbox", agent)):
            if os.path.islink(folder) or (os.path.exists(folder) and not os.path.isdir(folder)):
                raise Refused("invalid", "The inbox folder is a link or not a folder; nothing was written.", 409)
        self._write_bytes(os.path.join(self._tdir(tid), "inbox", agent, name), data, exclusive=True)
        return name

    # The inbox folders are opened one step at a time relative to the directory descriptor above (no-follow at every step), so a
    # link anywhere in the chain (inbox, agent folder, done/, rejected/) is refused and a folder swapped for a link while an import
    # runs cannot redirect a read or a move: the descriptors we already hold keep pointing at the folders we checked.
    _DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)

    def _open_dir(self, name: str, dir_fd: int | None, *, make: bool = False) -> int | None:
        """A directory descriptor for ``name`` below ``dir_fd`` (no link followed), or None if it is absent, a link or not a folder."""
        try:
            if make:
                try:
                    os.mkdir(name, 0o700, dir_fd=dir_fd)
                except FileExistsError:
                    pass
            return os.open(name, self._DIR_FLAGS, dir_fd=dir_fd)
        except OSError:
            return None

    def _open_inbox(self, tid: str) -> int | None:
        """The descriptor of topics/<tid>/inbox (a link at the topic or inbox step is refused), or None."""
        top = None
        try:
            top = os.open(self.dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))        # the configured data folder itself is trusted
            tfd = self._open_dir(tid, top)
        except OSError:
            return None
        finally:
            if top is not None:
                os.close(top)
        if tfd is None:
            return None
        try:
            return self._open_dir("inbox", tfd)
        finally:
            os.close(tfd)

    def inbox_waiting(self, tid: str, owner: str) -> dict:
        """Names and counts only (read-only): how many files wait per agent. Reading never imports anything and never follows a link."""
        self._topic(tid, owner)
        out: dict[str, int] = {}
        ifd = self._open_inbox(tid)
        if ifd is None:
            return out
        try:
            try:
                agents = sorted(a for a in os.listdir(ifd) if AGENT_RE.fullmatch(a))
            except OSError:
                return out
            for a in agents:
                afd = self._open_dir(a, ifd)
                if afd is None:
                    continue
                try:
                    for n in os.listdir(afd):
                        if INBOX_FILE_RE.fullmatch(n):
                            try:
                                if stat.S_ISREG(os.stat(n, dir_fd=afd, follow_symlinks=False).st_mode):
                                    out[a] = out.get(a, 0) + 1
                            except OSError:
                                pass
                except OSError:
                    pass
                finally:
                    os.close(afd)
        finally:
            os.close(ifd)
        return out

    def import_inbox(self, tid: str, owner: str, *, scrub=None) -> dict:
        """Import waiting contributions into the journal. Explicit (an owner action or a command), bounded, idempotent.

        Everything in a file is UNTRUSTED. The topic comes from the folder the file is in, the author name from the agent folder;
        the entry is stored ``via: inbox, verified: false``. Only these may come from a file: kind (an agent's decision is stored as a
        proposal), text, refs (only to items that exist in THIS topic, or plain file/commit/conversation/url text) and a claimed model
        and session. Any other key (verified, author, stage, disposition, owner, topic…) is ignored, and nothing can change an item,
        its order, status, a disposition or an approval. Payment details are removed in this method (and again by the store's text
        rules), whatever the caller passes. Folders and files are opened descriptor-relative with no link followed at any step: a
        link at the inbox, the agent folder, done/ or rejected/ is refused (and reported), a file that is not a small regular JSON
        file is rejected and set aside. The entry id comes from the file's bytes, so importing the same file again, or after a crash
        between the journal append and the move, adds nothing twice."""
        import hashlib
        result = {"imported": 0, "already_imported": 0, "rejected": [], "left_waiting": 0}
        extra = scrub or (lambda t: t)
        clean = lambda t: _payment_scrub(extra(t))
        with self._lock():
            self._topic(tid, owner)
            known_items = {i["id"] for i in self._items(tid)}
            journal_path = os.path.join(self._tdir(tid), "journal.jsonl")
            have = {e.get("id") for e in self._read_lines(journal_path)}
            ifd = self._open_inbox(tid)
            if ifd is None:
                if os.path.lexists(os.path.join(self._tdir(tid), "inbox")):
                    result["rejected"].append({"agent": "", "file": "inbox", "reason": "the inbox is a link or not a folder; nothing was read"})
                return result
            files_left, bytes_left = INBOX_BATCH_FILES, INBOX_BATCH_BYTES
            try:
                try:
                    entries = sorted(os.listdir(ifd))
                except OSError:
                    entries = []
                for agent in entries:
                    if not AGENT_RE.fullmatch(agent):
                        continue
                    afd = self._open_dir(agent, ifd)
                    if afd is None:
                        result["rejected"].append({"agent": agent[:32], "file": agent[:96], "reason": "the agent folder is a link or not a folder; nothing was read"})
                        continue
                    try:
                        self._import_agent(afd, agent, result, known_items, have, journal_path, clean, hashlib, limits=[files_left, bytes_left])
                        files_left, bytes_left = result.pop("_limits")
                    finally:
                        os.close(afd)
            finally:
                os.close(ifd)
            if result["imported"]:
                self._touch_topic(tid, self._topic(tid, owner))
        return result

    def _import_agent(self, afd: int, agent: str, result: dict, known_items: set, have: set, journal_path: str, clean, hashlib, *, limits: list):
        files_left, bytes_left = limits
        try:
            names = sorted(os.listdir(afd))
        except OSError:
            result["_limits"] = (files_left, bytes_left)
            return
        for name in names:
            if name in ("done", "rejected"):
                continue
            if files_left <= 0 or bytes_left <= 0:
                result["left_waiting"] += 1
                continue
            files_left -= 1

            def reject(why):
                result["rejected"].append({"agent": agent, "file": name[:96], "reason": why})
                self._set_aside_to(afd, name, "rejected", why, result)

            try:
                st = os.stat(name, dir_fd=afd, follow_symlinks=False)
            except OSError:
                continue
            if not INBOX_FILE_RE.fullmatch(name):
                reject("the file name is not an accepted pattern"); continue
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                reject("not a plain file (a link or a folder is never followed)"); continue
            if st.st_size > INBOX_FILE_MAX:
                reject(f"larger than {INBOX_FILE_MAX:,} bytes"); continue
            bytes_left -= st.st_size
            try:
                fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0), dir_fd=afd)
            except OSError:
                reject("could not be read (a link or a changed file)"); continue
            try:
                fst = os.fstat(fd)
                if not stat.S_ISREG(fst.st_mode) or (fst.st_ino, fst.st_dev) != (st.st_ino, st.st_dev):
                    os.close(fd); reject("not a plain file, or replaced while being read"); continue
                with os.fdopen(fd, "rb") as f:
                    raw = f.read(INBOX_FILE_MAX + 1)
            except OSError:
                reject("could not be read"); continue
            if len(raw) > INBOX_FILE_MAX:
                reject(f"larger than {INBOX_FILE_MAX:,} bytes"); continue
            try:
                body = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                reject("not valid JSON"); continue
            if not isinstance(body, dict) or body.get("v", 1) != 1:
                reject("not a version 1 contribution"); continue
            kind = body.get("kind")
            note = ""
            if kind in INBOX_AS_PROPOSAL:
                kind, note = "proposal", "[proposed as a " + str(body.get("kind")).replace("_", " ") + "; only the owner decides] "
            if kind not in INBOX_KINDS:
                reject("unknown kind of contribution"); continue
            refs, dropped = [], 0
            for r in (body.get("refs") if isinstance(body.get("refs"), list) else [])[:20]:
                if not isinstance(r, dict) or r.get("type") not in REF_TYPES or not isinstance(r.get("ref"), str):
                    dropped += 1; continue
                if r["type"] in ("item", "revision", "comment") and not any(i in r["ref"] for i in known_items):
                    dropped += 1; continue                                   # an item of another topic (or none) is not linked
                refs.append({k: clean(str(r[k]))[:300] for k in ("type", "ref", "quote") if r.get(k)})
            text = body.get("text")
            if not isinstance(text, str) or not text.strip():
                reject("no text"); continue
            eid = "j-" + hashlib.sha256(raw).hexdigest()[:12]
            if eid in have:
                result["already_imported"] += 1
                self._set_aside_to(afd, name, "done", "", result)
                continue
            try:
                entry = {"id": eid, "at": now_iso(), "author": {"kind": "agent", "name": agent,
                         **({"model": clean(str(body["model"]))[:120]} if body.get("model") else {}),
                         **({"session_ref": clean(str(body["session_ref"]))[:120]} if body.get("session_ref") else {})},
                         "via": "inbox", "kind": kind, "text": clean(note + text.strip())[:JOURNAL_MAX],
                         "refs": refs, "verified": False, "file": name[:96]}
                result["refs_dropped"] = result.get("refs_dropped", 0) + dropped
            except Exception:
                reject("could not be read"); continue
            if self._count_lines(journal_path) >= JOURNAL_LINES_MAX:
                reject("the journal is full"); continue
            self._append_line(journal_path, entry)
            have.add(eid)
            result["imported"] += 1
            self._set_aside_to(afd, name, "done", "", result)
        result["_limits"] = (files_left, bytes_left)

    def _set_aside_to(self, afd: int, name: str, where: str, why: str, result: dict | None = None):
        """Move a processed file into done/ or rejected/ (with the reason beside it), never deleting it. The destination is
        opened no-follow relative to the agent folder's descriptor; a destination that is a link means the file stays where it is
        (and is reported), so nothing is ever moved through a link."""
        dfd = self._open_dir(where, afd, make=True)
        if dfd is None:
            if result is not None:
                result["rejected"].append({"agent": "", "file": f"{where}/", "reason": f"the {where} folder is a link or not a folder; the file was left in place"})
            return
        try:
            target = name
            try:
                os.stat(target, dir_fd=dfd, follow_symlinks=False)
                target += f".{secrets.token_hex(3)}"
            except OSError:
                pass
            os.rename(name, target, src_dir_fd=afd, dst_dir_fd=dfd)
            if why:
                try:
                    rfd = os.open(target + ".reason.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=dfd)
                    with os.fdopen(rfd, "w", encoding="utf-8") as f:
                        f.write(why + "\n")
                except OSError:
                    pass
        except OSError:
            pass
        finally:
            os.close(dfd)

    # ── recovery ──────────────────────────────────────────────────────
    def verify(self, owner: str) -> dict:
        """A read-only consistency report for the owner's topics: what the files say that the records do not, and the reverse.
        Write order is revision file first, then item.json, then the topic's updated time; a journal or comment line is one
        fsynced append. So after a crash the only possible inconsistencies are an orphan revision file (set aside by the next
        revision), an item folder with no item.json, or one torn last line. Nothing here changes a file."""
        report = {"topics": 0, "items": 0, "orphan_revision_files": [], "items_without_record": [], "missing_revision_files": [],
                  "torn_or_malformed_lines": [], "unreadable_records": []}
        self._ensure()
        for tid in sorted(n for n in os.listdir(self.dir) if TOPIC_RE.fullmatch(n)):
            try:
                t = self._topic(tid, owner)
            except (TopicNotFound, TopicStoreUnavailable):
                report["unreadable_records"].append(tid)
                continue
            report["topics"] += 1
            base = os.path.join(self._tdir(tid), "items")
            for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
                if not ITEM_RE.fullmatch(name):
                    continue
                try:
                    item = self._read_json(os.path.join(base, name, "item.json"))
                except TopicStoreUnavailable:
                    report["unreadable_records"].append(f"{tid}/{name}")
                    continue
                if not item:
                    report["items_without_record"].append(f"{tid}/{name}")
                    continue
                report["items"] += 1
                named = {r.get("file") for r in item.get("revisions", [])} | {f"{r['rev']}.thumb.webp" for r in item.get("revisions", []) if item["kind"] == "design"}
                rev_dir = os.path.join(base, name, "rev")
                have = set(os.listdir(rev_dir)) if os.path.isdir(rev_dir) else set()
                report["orphan_revision_files"] += [f"{tid}/{name}/{f}" for f in sorted(have - named)]
                report["missing_revision_files"] += [f"{tid}/{name}/{f}" for f in sorted(named - have) if f]
                for jl in (os.path.join(base, name, "comments.jsonl"),):
                    before = self.unreadable
                    self._read_lines(jl)
                    if self.unreadable > before:
                        report["torn_or_malformed_lines"].append(f"{tid}/{name}/comments.jsonl")
            before = self.unreadable
            self._read_lines(os.path.join(self._tdir(tid), "journal.jsonl"))
            if self.unreadable > before:
                report["torn_or_malformed_lines"].append(f"{tid}/journal.jsonl")
        report["ok"] = not any(report[k] for k in ("orphan_revision_files", "items_without_record", "missing_revision_files", "torn_or_malformed_lines", "unreadable_records"))
        return report


def topics_of(services) -> TopicStore:
    store = getattr(services, "store", None)
    folder = getattr(store, "folder", None) if store is not None and getattr(store, "path", None) else None
    return TopicStore(folder)


__all__ = ["TopicStore", "TopicStoreUnavailable", "TopicNotFound", "ItemNotFound", "Refused", "topics_of", "KINDS", "STAGES",
           "DISPOSITIONS", "VIEWS", "JOURNAL_KINDS", "TOPIC_RE", "ITEM_RE", "COMMENT_RE", "REV_RE"]
