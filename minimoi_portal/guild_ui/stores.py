"""The Shop floor's own server data: notes, post-its with a kept bin, Continue.

Storage (B1 brief (c), #233). Three Postgres tables in the portal's existing
database, created by ``sql/001_floor_b1.sql``. The SQL lives in this package,
so a change here redeploys the portal only.

Many writers, one database. Every change is a single SQL statement that
checks and changes in one step (a conditional UPDATE, an INSERT with ON
CONFLICT, an upsert), so two writers at once can never lose each other's
change and no write reads first and writes later:
- binning a post-it only succeeds while it is still on the board;
  a second remover is told "already in the bin";
- a note is keyed by its request id, so a retried or double-sent note is
  kept once;
- Continue is one row per (floor, principal), replaced by an upsert.
Every write also carries an idempotency key, claimed in the same transaction
(``guild.floor_requests``), so a retried request is applied once and a
replayed old request changes nothing.
The bin is kept and restore puts a post-it back; since Guild 1.1 slice 3
the only deletion is Empty trash (board.py), which deletes exactly the binned
rows the owner confirmed and keeps a purge receipt.

Scoping (review S6). Rows carry ``floor`` (whose floor it is) and, apart
from that, their author. Reads filter by floor, never by the writer, so a
post-it written by Master Craftsman is on Robert's floor, and either can
bin it. Continue is per (floor, principal): each participant has their own
place, shared by every front end they use.

Failure (binding rule B2, review S9). Nothing here runs at registration.
A read that fails is *unknown* ("unavailable"), never an empty list or a
zero; a write that fails raises ``FloorStoreUnavailable`` and nothing is
kept. No database configured reads as *not instrumented*.

Payment details are stripped by the API before any text reaches this module
(payment_scrub.py); this module never logs text.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .adapters.contract import READ_FAILED, SourceResult, live_ok, live_unknown, not_configured
from .board import BoardMixin

EVIDENCE = "guild.floor_messages, guild.floor_postits, guild.floor_continue (read)"
DEFAULT_FLOOR = "guild"
POSTIT_MAX = 280            # Guild 1.1 slice 3 (spec §5.1); the database allows 400
NOTE_MAX = 2000
RAIL_CAP = 4
BIN_PAGE = 100
NOTES_PAGE = 50


@dataclass(frozen=True)
class Author:
    id: str          # the writing principal: a username, "master_craftsman", "guild_platform"
    kind: str        # owner | agent | platform
    label: str       # shown beside the text


MASTER_CRAFTSMAN = Author("master_craftsman", "agent", "Master Craftsman")
CHIEF_OF_STAFF = Author("chief_of_staff", "agent", "Chief of Staff")
GUILD_PLATFORM = Author("guild_platform", "platform", "Guild platform")


@dataclass
class WriteResult:
    outcome: str     # added, binned, already_binned, restored, already_active, not_found, kept, set,
                     # idempotency_mismatch
    value: dict | None
    repeated: bool


class FloorStoreUnavailable(Exception):
    """The floor database could not be reached or used; nothing was kept."""


class FloorStoreNotConfigured(FloorStoreUnavailable):
    """No floor database is configured on this portal."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def iso(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        if getattr(value, "tzinfo", None) is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _default_connect(url: str):
    import psycopg2
    return psycopg2.connect(url, connect_timeout=2, options="-c statement_timeout=2000")


_POSTIT_COLS = ("id, text, author, author_kind, author_label, created_at, "
                "binned_at, binned_by, binned_by_label, restored_at, restored_by, restored_by_label, "
                # Guild 1.1 slice 3 (sql/002_board.sql)
                "done_at, done_by, label, item_ref, kind, asset_id, sort_key, version")
_NOTE_COLS = "id, request_id, author, author_kind, author_label, text, area, item_ref, page, created_at"
_CONTINUE_COLS = "kind, ref, label, updated_at"


def _postit(row) -> dict:
    (pid, text, author, kind, label, created, binned, binned_by, binned_by_label, restored,
     restored_by, restored_by_label, done_at, done_by, board_label, item_ref, note_kind, asset_id,
     sort_key, version) = row
    # State precedence (spec §5.1): Trash wins over Done; otherwise active.
    state = "binned" if binned is not None else ("done" if done_at is not None else "active")
    return {"id": int(pid), "text": text, "author": author, "author_kind": kind, "author_label": label,
            "created_at": iso(created), "binned_at": iso(binned), "binned_by": binned_by,
            "binned_by_label": binned_by_label, "restored_at": iso(restored), "restored_by": restored_by,
            "restored_by_label": restored_by_label,
            "done_at": iso(done_at), "done_by": done_by, "label": board_label,
            "item_ref": int(item_ref) if item_ref is not None else None, "kind": note_kind or "note",
            "asset_id": str(asset_id) if asset_id is not None else None,
            "sort_key": int(sort_key) if sort_key is not None else None, "version": int(version or 1),
            "state": state}


def _note(row) -> dict:
    (nid, request_id, author, kind, label, text, area, item_ref, page, created) = row
    return {"id": int(nid), "request_id": request_id, "who": author, "author_kind": kind, "author_label": label,
            "text": text, "context": {"area": area, "item_ref": item_ref, "page": page},
            "created_at": iso(created), "record_mode": "on_record"}


def _continue(row) -> dict | None:
    if row is None:
        return None
    kind, ref, label, updated = row
    return {"kind": kind, "ref": ref, "label": label, "updated_at": iso(updated)}


class FloorStores(BoardMixin):
    """Notes, post-its and Continue for one floor, in the portal's database.

    ``connect(url)`` returns a DB-API connection; ``paramstyle`` is "format"
    (psycopg2, the default) or "qmark". Every call opens its own connection,
    bounded to 2 s, so a slow database never holds a request for long.
    """

    def __init__(self, database_url: Callable[[], str | None], *, connect: Callable | None = None,
                 paramstyle: str = "format", floor: str = DEFAULT_FLOOR):
        self._database_url = database_url
        self._connect = connect or _default_connect
        self._qmark = paramstyle == "qmark"
        self.floor = floor

    def for_floor(self, floor: str) -> "FloorStores":
        """The same database under another floor key: a conversation's own
        notes (slice 2). Post-its and Continue stay on the floor's own key."""
        twin = FloorStores(self._database_url, connect=self._connect,
                           paramstyle="qmark" if self._qmark else "format", floor=floor)
        return twin

    # ── plumbing ────────────────────────────────────────────────────────
    def configured(self) -> bool:
        try:
            return bool(self._database_url())
        except Exception:
            return False

    def _sql(self, sql: str) -> str:
        return sql.replace("%s", "?") if self._qmark else sql

    def _open(self):
        url = self._database_url() if self.configured() else None
        if not url:
            raise FloorStoreNotConfigured("the floor database is not configured")
        try:
            return self._connect(url)
        except Exception as exc:
            raise FloorStoreUnavailable(f"connect failed: {type(exc).__name__}") from None

    def _run(self, work, *, write: bool):
        """Run ``work(q)`` on one connection; q(sql, params) returns all rows."""
        conn = self._open()
        try:
            if write and self._qmark:
                # SQLite (the tests): take the write lock at the start, so a
                # check-then-write under the board or library meta row is as
                # serial as Postgres's SELECT ... FOR UPDATE.
                conn.execute("BEGIN IMMEDIATE")

            def q(sql, params=()):
                cur = conn.cursor()
                try:
                    cur.execute(self._sql(sql), tuple(params))
                    return cur.fetchall() if cur.description else []
                finally:
                    cur.close()
            result = work(q)
            if write:
                conn.commit()
            return result
        except FloorStoreUnavailable:
            raise
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            raise FloorStoreUnavailable(f"{'write' if write else 'read'} failed: {type(exc).__name__}") from None
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _read(self, work, fresh_for_s=60) -> SourceResult:
        if not self.configured():
            return not_configured(EVIDENCE, "the floor database")
        try:
            return live_ok(self._run(work, write=False), EVIDENCE, fresh_for_s=fresh_for_s)
        except FloorStoreNotConfigured:
            return not_configured(EVIDENCE, "the floor database")
        except FloorStoreUnavailable as exc:
            return live_unknown(READ_FAILED, f"floor store {exc}", EVIDENCE)

    # ── reads ───────────────────────────────────────────────────────────
    def _active(self, q, limit=None):
        # Active: not binned and not done, in the Board's order; a note with no
        # sort_key yet (new, or written by older code) is on top, newest first.
        sql = (f"SELECT {_POSTIT_COLS} FROM guild.floor_postits WHERE floor = %s AND binned_at IS NULL "
               "AND done_at IS NULL ORDER BY sort_key ASC NULLS FIRST, id DESC")
        params = [self.floor]
        if limit is not None:
            sql += " LIMIT %s"
            params.append(int(limit))
        return [_postit(r) for r in q(sql, params)]

    def _count(self, q, binned: bool) -> int:
        cond = "IS NOT NULL" if binned else "IS NULL AND done_at IS NULL"
        return int(q(f"SELECT COUNT(*) FROM guild.floor_postits WHERE floor = %s AND binned_at {cond}",
                     [self.floor])[0][0])

    def _notes(self, q, *, before=None, limit=NOTES_PAGE):
        limit = max(1, min(int(limit), 200))
        if before is None:
            rows = q(f"SELECT {_NOTE_COLS} FROM guild.floor_messages WHERE floor = %s ORDER BY id DESC LIMIT %s",
                     [self.floor, limit + 1])
        else:
            rows = q(f"SELECT {_NOTE_COLS} FROM guild.floor_messages WHERE floor = %s AND id < %s "
                     "ORDER BY id DESC LIMIT %s", [self.floor, int(before), limit + 1])
        more = len(rows) > limit
        return [_note(r) for r in reversed(rows[:limit])], more

    def _get_continue(self, q, principal):
        rows = q(f"SELECT {_CONTINUE_COLS} FROM guild.floor_continue WHERE floor = %s AND principal = %s",
                 [self.floor, principal])
        return _continue(rows[0] if rows else None)

    def summary(self, principal: str, *, rail_cap: int = RAIL_CAP, notes_limit: int = 0) -> SourceResult:
        """Everything the floor shows, on one connection."""
        def work(q):
            out = {
                "postits": self._active(q, rail_cap),
                "active_total": self._count(q, binned=False),
                "bin_total": self._count(q, binned=True),
                "continue": self._get_continue(q, principal),
            }
            if notes_limit:
                out["notes"], out["notes_more"] = self._notes(q, limit=notes_limit)
            return out
        return self._read(work)

    def get_note(self, request_id: str) -> dict | None:
        """One kept note by its request id on this floor, or None. Raises
        FloorStoreUnavailable / FloorStoreNotConfigured like every write."""
        def work(q):
            rows = q(f"SELECT {_NOTE_COLS} FROM guild.floor_messages WHERE floor = %s AND request_id = %s",
                     [self.floor, request_id])
            return _note(rows[0]) if rows else None
        return self._run(work, write=False)

    def list_postits(self) -> SourceResult:
        return self._read(lambda q: {"postits": self._active(q), "bin_total": self._count(q, binned=True)})

    def list_bin(self, limit: int = BIN_PAGE) -> SourceResult:
        limit = max(1, min(int(limit), 500))

        def work(q):
            rows = q(f"SELECT {_POSTIT_COLS} FROM guild.floor_postits WHERE floor = %s AND binned_at IS NOT NULL "
                     "ORDER BY binned_at DESC, id DESC LIMIT %s", [self.floor, limit])
            return {"bin": [_postit(r) for r in rows], "total": self._count(q, binned=True)}
        return self._read(work)

    def list_notes(self, *, before: int | None = None, limit: int = NOTES_PAGE) -> SourceResult:
        def work(q):
            notes, more = self._notes(q, before=before, limit=limit)
            return {"notes": notes, "more": more}
        return self._read(work)

    def get_continue(self, principal: str) -> SourceResult:
        return self._read(lambda q: self._get_continue(q, principal))

    # ── writes ──────────────────────────────────────────────────────────
    # Each write runs in one transaction: claim the idempotency key, make the
    # change with one conditional statement, record the outcome, commit. A
    # repeated key returns the first outcome and changes nothing; the same key
    # for a different change is refused ("idempotency_mismatch").
    def _claim(self, q, key: str, principal: str, op: str, target: str, now: str):
        rows = q("INSERT INTO guild.floor_requests (floor, idempotency_key, principal, op, target, created_at) "
                 "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (floor, idempotency_key) DO NOTHING "
                 "RETURNING idempotency_key", [self.floor, key, principal, op, target, now])
        if rows:
            return None
        rows = q("SELECT op, target, outcome, result_ref, principal FROM guild.floor_requests "
                 "WHERE floor = %s AND idempotency_key = %s", [self.floor, key])
        if not rows:
            raise FloorStoreUnavailable("idempotency key neither claimed nor found")
        op0, target0, outcome, ref, principal0 = rows[0]
        return {"same": op0 == op and target0 == target and principal0 == principal, "outcome": outcome, "ref": ref}

    def _settle(self, q, key: str, outcome: str, ref) -> None:
        q("UPDATE guild.floor_requests SET outcome = %s, result_ref = %s WHERE floor = %s AND idempotency_key = %s",
          [outcome, None if ref is None else str(ref), self.floor, key])

    def _postit_by_id(self, q, postit_id):
        rows = q(f"SELECT {_POSTIT_COLS} FROM guild.floor_postits WHERE id = %s AND floor = %s",
                 [int(postit_id), self.floor])
        return _postit(rows[0]) if rows else None

    def add_postit(self, text: str, author: Author, *, idempotency_key: str, label: str | None = None,
                   item_ref: int | None = None) -> WriteResult:
        """A note on top of the Board (optionally labelled and linked to a
        queue item; linking never changes the item). Bumps order_rev."""
        now = utc_now()
        target = hashlib.sha256(f"{text}\x00{label}\x00{item_ref}".encode("utf-8")).hexdigest()[:16]

        def work(q):
            prior = self._claim(q, idempotency_key, author.id, "postit.add", target, now)
            if prior is not None:
                if not prior["same"]:
                    return WriteResult("idempotency_mismatch", None, True)
                return WriteResult(prior["outcome"], self._postit_by_id(q, prior["ref"]), True)
            self._meta(q)
            rows = q(f"INSERT INTO guild.floor_postits (floor, text, author, author_kind, author_label, created_at, "
                     f"label, item_ref) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_POSTIT_COLS}",
                     [self.floor, text, author.id, author.kind, author.label, now, label, item_ref])
            postit = _postit(rows[0])
            self._bump(q, order=True)
            self._settle(q, idempotency_key, "added", postit["id"])
            return WriteResult("added", postit, False)
        return self._run(work, write=True)

    def _move(self, postit_id: int, by: Author, idempotency_key: str, *, to_bin: bool) -> WriteResult:
        now = utc_now()
        op = "postit.bin" if to_bin else "postit.restore"

        def work(q):
            prior = self._claim(q, idempotency_key, by.id, op, str(int(postit_id)), now)
            if prior is not None:
                if not prior["same"]:
                    return WriteResult("idempotency_mismatch", None, True)
                return WriteResult(prior["outcome"], self._postit_by_id(q, postit_id), True)
            self._meta(q)
            if to_bin:
                rows = q(f"UPDATE guild.floor_postits SET binned_at = %s, binned_by = %s, binned_by_label = %s, "
                         f"version = version + 1 "
                         f"WHERE id = %s AND floor = %s AND binned_at IS NULL RETURNING {_POSTIT_COLS}",
                         [now, by.id, by.label, int(postit_id), self.floor])
                done, already = "binned", "already_binned"
            else:
                # A photo's asset row is locked first, so a restore can never
                # race a library purge (spec §5.2). Its reference stayed live
                # while the photo was in the Trash.
                self._lock_asset_of(q, postit_id)
                # binned_by stays: who last binned it is kept beside who restored it.
                # done_at stays too: Restore returns the note to its prior state.
                rows = q(f"UPDATE guild.floor_postits SET binned_at = NULL, restored_at = %s, restored_by = %s, "
                         f"restored_by_label = %s, version = version + 1 WHERE id = %s AND floor = %s "
                         f"AND binned_at IS NOT NULL RETURNING {_POSTIT_COLS}",
                         [now, by.id, by.label, int(postit_id), self.floor])
                done, already = "restored", "already_active"
            if rows:
                self._bump(q, order=True, trash=True)
                outcome, postit = done, _postit(rows[0])
            else:
                postit = self._postit_by_id(q, postit_id)
                outcome = already if postit else "not_found"
            self._settle(q, idempotency_key, outcome, postit_id)
            return WriteResult(outcome, postit, False)
        return self._run(work, write=True)

    def bin_postit(self, postit_id: int, by: Author, *, idempotency_key: str) -> WriteResult:
        """Outcome "binned", "already_binned" or "not_found"."""
        return self._move(postit_id, by, idempotency_key, to_bin=True)

    def restore_postit(self, postit_id: int, by: Author, *, idempotency_key: str) -> WriteResult:
        """Outcome "restored", "already_active" or "not_found"."""
        return self._move(postit_id, by, idempotency_key, to_bin=False)

    def add_note(self, request_id: str, text: str, author: Author, *, area: str | None = None,
                 item_ref: int | None = None, page: str | None = None) -> WriteResult:
        """Keep one on-record note. The request id is its idempotency key: a
        repeated id keeps it once, and a different text under it is refused."""
        now = utc_now()

        def work(q):
            rows = q(f"INSERT INTO guild.floor_messages (floor, request_id, author, author_kind, author_label, "
                     f"text, area, item_ref, page, record_mode, created_at) "
                     f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'on_record', %s) "
                     f"ON CONFLICT (floor, request_id) DO NOTHING RETURNING {_NOTE_COLS}",
                     [self.floor, request_id, author.id, author.kind, author.label, text, area, item_ref, page, now])
            if rows:
                return WriteResult("kept", _note(rows[0]), False)
            rows = q(f"SELECT {_NOTE_COLS} FROM guild.floor_messages WHERE floor = %s AND request_id = %s",
                     [self.floor, request_id])
            if not rows:
                raise FloorStoreUnavailable("note neither kept nor found")
            note = _note(rows[0])
            if note["text"] != text or note["who"] != author.id:
                return WriteResult("idempotency_mismatch", None, True)
            return WriteResult("kept", note, True)
        return self._run(work, write=True)

    def set_continue(self, principal: str, *, kind: str, ref: str, label: str, idempotency_key: str) -> WriteResult:
        now = utc_now()

        def work(q):
            prior = self._claim(q, idempotency_key, principal, "continue.set", f"{kind}:{ref}", now)
            if prior is not None:
                if not prior["same"]:
                    return WriteResult("idempotency_mismatch", None, True)
                return WriteResult(prior["outcome"], self._get_continue(q, principal), True)
            rows = q(f"INSERT INTO guild.floor_continue (floor, principal, kind, ref, label, updated_at) "
                     f"VALUES (%s, %s, %s, %s, %s, %s) "
                     f"ON CONFLICT (floor, principal) DO UPDATE SET kind = excluded.kind, ref = excluded.ref, "
                     f"label = excluded.label, updated_at = excluded.updated_at "
                     f"RETURNING {_CONTINUE_COLS}",
                     [self.floor, principal, kind, str(ref), label, now])
            self._settle(q, idempotency_key, "set", ref)
            return WriteResult("set", _continue(rows[0]), False)
        return self._run(work, write=True)


__all__ = ["Author", "WriteResult", "FloorStores", "FloorStoreUnavailable", "FloorStoreNotConfigured", "MASTER_CRAFTSMAN", "CHIEF_OF_STAFF",
           "GUILD_PLATFORM", "POSTIT_MAX", "NOTE_MAX", "RAIL_CAP", "DEFAULT_FLOOR", "utc_now"]
