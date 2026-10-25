"""The Board's writes (Guild 1.1 slice 3, spec §5.1), mixed into FloorStores.

Every write runs in one transaction on the floor's database:
claim the idempotency key -> lock the board meta row (and, where it matters,
the post-it or the media asset row) -> check the expected version or
revision -> change -> settle the key. A repeated key returns the first
outcome; the same key for another change is refused.

* Done / undone, label and link check the post-it's ``version`` (a stale one
  is a ``conflict`` carrying the current post-it). Linking only records the
  queue id: linked work is never changed by any Board action.
* Order (R3): ``reorder`` checks ``order_rev`` under the meta lock, puts the
  note at the midpoint of its new neighbours, and renumbers the whole active
  order in the same transaction when no gap is left.
* Empty trash (R2): ``empty_trash`` checks ``trash_rev`` and that the Trash is
  exactly the ``{id, version}`` set the owner confirmed; anything else is a
  ``conflict`` that deletes nothing. It deletes exactly those rows, releases
  their media references, keeps a purge receipt under the idempotency key,
  and a retry returns that receipt.
* Photos: placing one locks the board meta row, then the media asset row
  (the order restore uses too), so a placement can never race a library
  purge or deadlock a restore, and refuses an asset in the library Trash.

On SQLite (the tests) the transaction takes the write lock at its start
(stores._run), which serialises the same check-then-write.
"""
from __future__ import annotations

import hashlib
import uuid
import json

from . import stores as S

LABELS = ("decide", "blocked", "remember", "followup", "idea", "fyi")
STEP = 1024                      # the gap between renumbered sort keys
BOARD_W, BOARD_MAX_Y = 1200, 20000   # the logical board: 1200 wide, growing downward (sql/004_board_place.sql)
NOTE_W, NOTE_H = 180, 170            # the footprint of every note and photo on it
SIZE_MIN_W, SIZE_MIN_H, SIZE_MAX_W, SIZE_MAX_H = 140, 130, 480, 450
MAX_MOVES = 200
PHOTO_CAPTION_MAX = 140


class BoardMixin:
    # ── locks and revisions ──────────────────────────────────────────────
    @property
    def _for_update(self) -> str:
        return "" if self._qmark else " FOR UPDATE"

    def _meta(self, q) -> tuple[int, int]:
        """Lock (creating if needed) this floor's board meta row; (order_rev, trash_rev)."""
        q("INSERT INTO guild.floor_board_meta (floor) VALUES (%s) ON CONFLICT (floor) DO NOTHING", [self.floor])
        rows = q(f"SELECT order_rev, trash_rev FROM guild.floor_board_meta WHERE floor = %s{self._for_update}",
                 [self.floor])
        return int(rows[0][0]), int(rows[0][1])

    def _bump(self, q, *, order: bool = False, trash: bool = False) -> None:
        sets = []
        if order:
            sets.append("order_rev = order_rev + 1")
        if trash:
            sets.append("trash_rev = trash_rev + 1")
        if sets:
            q(f"UPDATE guild.floor_board_meta SET {', '.join(sets)} WHERE floor = %s", [self.floor])

    def _revs(self, q) -> tuple[int, int]:
        rows = q("SELECT order_rev, trash_rev FROM guild.floor_board_meta WHERE floor = %s", [self.floor])
        return (int(rows[0][0]), int(rows[0][1])) if rows else (0, 0)

    def _lock_asset_of(self, q, postit_id) -> None:
        rows = q("SELECT asset_id FROM guild.floor_postits WHERE id = %s AND floor = %s", [int(postit_id), self.floor])
        if rows and rows[0][0] is not None:
            q(f"SELECT id FROM media.assets WHERE id = %s{self._for_update}", [str(rows[0][0])])

    def _locked_postit(self, q, postit_id):
        rows = q(f"SELECT {S._POSTIT_COLS} FROM guild.floor_postits WHERE id = %s AND floor = %s{self._for_update}",
                 [int(postit_id), self.floor])
        return S._postit(rows[0]) if rows else None

    # ── reads ────────────────────────────────────────────────────────────
    def _trash_rows(self, q, limit=None):
        sql = (f"SELECT {S._POSTIT_COLS} FROM guild.floor_postits WHERE floor = %s AND binned_at IS NOT NULL "
               "ORDER BY binned_at DESC, id DESC")
        params = [self.floor]
        if limit is not None:
            sql += " LIMIT %s"
            params.append(int(limit))
        return [S._postit(r) for r in q(sql, params)]

    def _done_rows(self, q, limit=200):
        rows = q(f"SELECT {S._POSTIT_COLS} FROM guild.floor_postits WHERE floor = %s AND binned_at IS NULL "
                 "AND done_at IS NOT NULL ORDER BY done_at DESC, id DESC LIMIT %s", [self.floor, int(limit)])
        return [S._postit(r) for r in rows]

    def board(self):
        """Active (in order), Done and the whole Trash, with both revisions."""
        def work(q):
            order_rev, trash_rev = self._revs(q)
            active, done, trash = self._active(q), self._done_rows(q), self._trash_rows(q)
            done_total = int(q("SELECT COUNT(*) FROM guild.floor_postits WHERE floor = %s AND binned_at IS NULL "
                               "AND done_at IS NOT NULL", [self.floor])[0][0])
            return {"active": active, "done": done, "trash": trash, "order_rev": order_rev, "trash_rev": trash_rev,
                    "counts": {"active": len(active), "done": done_total, "trash": len(trash)}}
        return self._read(work, fresh_for_s=30)

    def referenced_postits(self, ids):
        """Read at most three explicit IDs on this floor; no cross-floor fallback."""
        ids = list(dict.fromkeys(ids))
        if len(ids) > 3 or any(type(n) is not int or n < 1 for n in ids):
            raise ValueError("At most three positive Board IDs required")
        def work(q):
            if not ids:
                return {"postits": []}
            marks = ", ".join(["%s"] * len(ids))
            rows = q(f"SELECT {S._POSTIT_COLS} FROM guild.floor_postits "
                     f"WHERE floor = %s AND id IN ({marks})", [self.floor, *ids])
            return {"postits": [S._postit(r) for r in rows]}
        return self._read(work, fresh_for_s=0)

    def outcome_receipt(self, source_id: int, *, turn_key: str):
        """Read a durable historical outcome receipt; never retry a mutation."""
        key = "mc-outcome-" + hashlib.sha256(f"{turn_key}\0{source_id}".encode()).hexdigest()[:40]
        def work(q):
            rows = q("SELECT outcome, result_ref FROM guild.floor_requests "
                     "WHERE floor = %s AND idempotency_key = %s AND op = %s AND principal = %s",
                     [self.floor, key, "postit.outcome", S.MASTER_CRAFTSMAN.id])
            if not rows:
                return {"state":"not_recorded"}
            outcome, ref = rows[0]
            if outcome == "added" and ref:
                return {"state":"saved", "source_id":source_id, "postit_id":int(ref)}
            return {"state":"not_saved", "reason":outcome}
        return self._run(work, write=False)

    def add_outcome(self, source_id: int, expect_version: int, text: str, *, turn_key: str):
        """Platform-mediated MC outcome; preserve source and serialize against edits.

        turn_key is the authenticated owner note's stable request ID, never model
        selected. One outcome per owner turn/source; content changes conflict.
        The transaction's retained idempotency row is the durable write receipt.
        """
        if (type(source_id) is not int or source_id < 1 or type(expect_version) is not int
                or expect_version < 1 or not isinstance(text, str) or not text.strip()
                or len(text) > S.POSTIT_MAX or not isinstance(turn_key, str)
                or not turn_key or len(turn_key) > 200):
            raise ValueError("Invalid Board outcome")
        key = "mc-outcome-" + hashlib.sha256(f"{turn_key}\0{source_id}".encode()).hexdigest()[:40]
        target = hashlib.sha256(json.dumps([source_id, expect_version, text], ensure_ascii=False).encode()).hexdigest()
        now = S.utc_now()
        by = S.MASTER_CRAFTSMAN
        def work(q):
            prior = self._claim(q, key, by.id, "postit.outcome", target, now)
            if prior is not None:
                if not prior["same"]:
                    return S.WriteResult("idempotency_mismatch", None, True)
                value = self._postit_by_id(q, prior["ref"]) if prior["ref"] else None
                return S.WriteResult(prior["outcome"], value, True)
            self._meta(q)
            source = self._locked_postit(q, source_id)
            if source is None:
                self._settle(q, key, "not_found", None)
                return S.WriteResult("not_found", None, False)
            if source["version"] != expect_version or source["state"] == "binned":
                self._settle(q, key, "conflict", None)
                return S.WriteResult("conflict", None, False)
            rows = q(f"INSERT INTO guild.floor_postits (floor, text, author, author_kind, author_label, created_at, label) "
                     f"VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING {S._POSTIT_COLS}",
                     [self.floor, text, by.id, by.kind, by.label, now, "fyi"])
            value = S._postit(rows[0])
            self._bump(q, order=True)
            self._settle(q, key, "added", value["id"])
            return S.WriteResult("added", value, False)
        return self._run(work, write=True)

    def note_text(self, note_id: int) -> dict | None:
        """A stored, on-the-record note on this floor or one of its
        conversations (``<floor>/<id>``), by id: {id, text, floor}, or None."""
        def work(q):
            rows = q("SELECT id, text, floor, record_mode, author_label, author_kind, created_at "
                     "FROM guild.floor_messages WHERE id = %s "
                     "AND (floor = %s OR floor LIKE %s)", [int(note_id), self.floor, f"{self.floor}/%"])
            if not rows or rows[0][3] != "on_record":
                return None
            r = rows[0]
            return {"id": int(r[0]), "text": r[1], "floor": r[2], "author_label": r[4], "author_kind": r[5],
                    "created_at": S.iso(r[6])}
        return self._run(work, write=False)

    # ── one-post-it changes behind its version ───────────────────────────
    def _versioned(self, postit_id, by, key, *, op: str, value, expect_version: int, change):
        """Claim, lock, check the version, apply ``change(q, current)`` which
        returns (outcome, new_row or None, bump_order)."""
        now = S.utc_now()
        target = f"{int(postit_id)}:{int(expect_version)}:{json.dumps(value, sort_keys=True)}"

        def work(q):
            prior = self._claim(q, key, by.id, op, target, now)
            if prior is not None:
                if not prior["same"]:
                    return S.WriteResult("idempotency_mismatch", None, True)
                return S.WriteResult(prior["outcome"], self._postit_by_id(q, postit_id), True)
            self._meta(q)
            current = self._locked_postit(q, postit_id)
            if current is None:
                outcome, row, bump = "not_found", None, False
            elif current["version"] != int(expect_version):
                outcome, row, bump = "conflict", current, False
            else:
                outcome, row, bump = change(q, current, now)
            if bump:
                self._bump(q, order=True)
            self._settle(q, key, outcome, postit_id)
            return S.WriteResult(outcome, row, False)
        return self._run(work, write=True)

    def _update_row(self, q, postit_id, sets: str, params: list):
        rows = q(f"UPDATE guild.floor_postits SET {sets}, version = version + 1, semantic_revision = semantic_revision + 1 WHERE id = %s AND floor = %s "
                 f"RETURNING {S._POSTIT_COLS}", params + [int(postit_id), self.floor])
        return S._postit(rows[0])

    def set_done(self, postit_id: int, done: bool, by, *, expect_version: int, idempotency_key: str):
        """Done leaves Active at once; Undone brings it back. A note in the
        Trash is neither: restore it first ("in_trash")."""
        def change(q, cur, now):
            if cur["state"] == "binned":
                return "in_trash", cur, False
            if done and cur["done_at"]:
                return "already_done", cur, False
            if not done and not cur["done_at"]:
                return "already_active", cur, False
            if done:
                return "done", self._update_row(q, postit_id, "done_at = %s, done_by = %s", [now, by.id]), True
            return "undone", self._restore_position(q, self._update_row(q, postit_id, "done_at = NULL, done_by = NULL", [])), True
        return self._versioned(postit_id, by, idempotency_key, op="postit.done" if done else "postit.undone",
                               value=done, expect_version=expect_version, change=change)

    def set_label(self, postit_id: int, label: str | None, by, *, expect_version: int, idempotency_key: str):
        if label is not None and label not in LABELS:
            raise ValueError("unknown label")

        def change(q, cur, now):
            return "labelled", self._update_row(q, postit_id, "label = %s", [label]), False
        return self._versioned(postit_id, by, idempotency_key, op="postit.label", value=label,
                               expect_version=expect_version, change=change)

    def set_link(self, postit_id: int, item_ref: int | None, by, *, expect_version: int, idempotency_key: str):
        """Record (or clear) the queue item a note is about. The item itself
        is never read for writing or changed here."""
        def change(q, cur, now):
            return "linked", self._update_row(q, postit_id, "item_ref = %s", [item_ref]), False
        return self._versioned(postit_id, by, idempotency_key, op="postit.link", value=item_ref,
                               expect_version=expect_version, change=change)

    def set_text(self, postit_id: int, text: str, by, *, expect_version: int, idempotency_key: str):
        """Change what a note says (the Board's click-to-edit). Behind the same
        version check as Done, label and link: a stale version is a ``conflict``
        carrying the current note and nothing is changed. A note in the Trash is
        restored first ("in_trash"); a photo's caption is not edited here
        ("not_a_note"). The caller has already scrubbed and length-checked."""
        def change(q, cur, now):
            if cur["state"] == "binned":
                return "in_trash", cur, False
            if cur.get("kind") == "photo":
                return "not_a_note", cur, False
            if cur["text"] == text:
                return "unchanged", cur, False
            return "edited", self._update_row(q, postit_id, "text = %s", [text]), False
        return self._versioned(postit_id, by, idempotency_key, op="postit.text", value=text,
                               expect_version=expect_version, change=change)

    def fingerprint(self):
        """A cheap "did anything on the Board change" signal for a second window
        (the pop-out): the two revisions plus how many notes there are, the sum of
        their versions and the newest id. Every change to a note bumps its
        version, and every add, bin, restore, reorder and purge moves a revision,
        a count or the newest id, so it differs after any change. Read-only; no
        schema change."""
        def work(q):
            order_rev, trash_rev = self._revs(q)
            n, total, newest = q("SELECT COUNT(*), COALESCE(SUM(version), 0), COALESCE(MAX(id), 0) "
                                 "FROM guild.floor_postits WHERE floor = %s", [self.floor])[0]
            return {"rev": f"{order_rev}.{trash_rev}.{int(n)}.{int(total)}.{int(newest)}",
                    "order_rev": order_rev, "trash_rev": trash_rev}
        return self._read(work, fresh_for_s=5)

    # ── photos ───────────────────────────────────────────────────────────
    def add_photo(self, asset_id: str, owner: str, caption: str | None, by, *, idempotency_key: str):
        """Place a library photo on the Board. The asset row is locked first:
        not found or not the owner's -> not_found; purged -> gone; in the
        library Trash -> asset_trashed (new placements are refused there).
        The reference is created in the same transaction. Lock order is the
        Board meta row first, then the asset row, the same as restore, so a
        placement and a restore of the same photo cannot deadlock."""
        now = S.utc_now()
        text = (caption or "").strip() or "Photo"
        try:
            uuid.UUID(str(asset_id))
        except ValueError:
            return S.WriteResult("not_found", None, False)   # never reaches Postgres's uuid cast

        def work(q):
            prior = self._claim(q, idempotency_key, by.id, "postit.photo", f"{asset_id}:{text}", now)
            if prior is not None:
                if not prior["same"]:
                    return S.WriteResult("idempotency_mismatch", None, True)
                return S.WriteResult(prior["outcome"], self._postit_by_id(q, prior["ref"]) if prior["ref"] else None,
                                     True)
            self._meta(q)
            rows = q(f"SELECT owner, kind, trashed_at, purged_at FROM media.assets WHERE id = %s{self._for_update}",
                     [str(asset_id)])
            if not rows or rows[0][0] != owner or rows[0][1] == "emoji":
                outcome = "not_found"
            elif rows[0][3] is not None:
                outcome = "gone"
            elif rows[0][2] is not None:
                outcome = "asset_trashed"
            else:
                outcome = None
            if outcome:
                self._settle(q, idempotency_key, outcome, None)
                return S.WriteResult(outcome, None, False)
            rows = q(f"INSERT INTO guild.floor_postits (floor, text, author, author_kind, author_label, created_at, "
                     f"kind, asset_id) VALUES (%s, %s, %s, %s, %s, %s, 'photo', %s) RETURNING {S._POSTIT_COLS}",
                     [self.floor, text, by.id, by.kind, by.label, now, str(asset_id)])
            postit = S._postit(rows[0])
            q('INSERT INTO media."references" (asset_id, domain, ref_kind, ref_id, created_at) '
              "VALUES (%s, 'guild', 'postit', %s, %s) ON CONFLICT (asset_id, domain, ref_kind, ref_id) DO NOTHING",
              [str(asset_id), str(postit["id"]), now])
            self._bump(q, order=True)
            self._settle(q, idempotency_key, "added", postit["id"])
            return S.WriteResult("added", postit, False)
        return self._run(work, write=True)

    # ── order (R3) ───────────────────────────────────────────────────────
    def reorder(self, postit_id: int, *, before_id: int | None, after_id: int | None, expect_order_rev: int,
                by, idempotency_key: str):
        """Move a note to just before ``before_id`` (or just after ``after_id``)
        in the active order. Outcome "moved", "conflict" (the order changed:
        the value carries the current order and order_rev), "not_found" or
        "invalid". Renumbers the whole active order when no gap is left."""
        now = S.utc_now()
        anchor = before_id if before_id is not None else after_id
        if (before_id is None) == (after_id is None) or anchor == postit_id:
            raise ValueError("name exactly one other note: before_id or after_id")
        target = f"{int(postit_id)}:{before_id}:{after_id}:{int(expect_order_rev)}"

        def work(q):
            prior = self._claim(q, idempotency_key, by.id, "postit.reorder", target, now)
            if prior is not None:
                if not prior["same"]:
                    return S.WriteResult("idempotency_mismatch", None, True)
                return S.WriteResult(prior["outcome"], self._order_value(q), True)
            order_rev, _trash = self._meta(q)
            if order_rev != int(expect_order_rev):
                self._settle(q, idempotency_key, "conflict", None)
                return S.WriteResult("conflict", self._order_value(q), False)
            rows = q(f"SELECT id, sort_key FROM guild.floor_postits WHERE floor = %s AND binned_at IS NULL "
                     f"AND done_at IS NULL ORDER BY sort_key ASC NULLS FIRST, id DESC{self._for_update}", [self.floor])
            order = [(int(r[0]), None if r[1] is None else int(r[1])) for r in rows]
            ids = [i for i, _k in order]
            if postit_id not in ids or anchor not in ids:
                self._settle(q, idempotency_key, "not_found", None)
                return S.WriteResult("not_found", self._order_value(q), False)
            rest = [(i, k) for i, k in order if i != postit_id]
            at = [i for i, _k in rest].index(anchor) + (0 if before_id is not None else 1)
            new_order = rest[:at] + [(postit_id, None)] + rest[at:]
            prev_key = new_order[at - 1][1] if at > 0 else None
            next_key = new_order[at + 1][1] if at + 1 < len(new_order) else None
            keyed = all(k is not None for i, k in rest)
            if keyed and prev_key is None and next_key is not None:
                key, renumber = next_key - STEP, False
            elif keyed and next_key is None and prev_key is not None:
                key, renumber = prev_key + STEP, False
            elif keyed and prev_key is not None and next_key is not None and next_key - prev_key >= 2:
                key, renumber = (prev_key + next_key) // 2, False
            else:
                key, renumber = None, True
            if renumber:
                # No gap left (or notes without a key yet): renumber the whole
                # active order in this same transaction.
                for n, (i, _k) in enumerate(new_order, start=1):
                    q("UPDATE guild.floor_postits SET sort_key = %s, version = version + 1 WHERE id = %s AND floor = %s",
                      [n * STEP, i, self.floor])
            else:
                q("UPDATE guild.floor_postits SET sort_key = %s, version = version + 1 WHERE id = %s AND floor = %s",
                  [key, int(postit_id), self.floor])
            self._bump(q, order=True)
            self._settle(q, idempotency_key, "moved", postit_id)
            value = self._order_value(q)
            value["renumbered"] = renumber
            return S.WriteResult("moved", value, False)
        return self._run(work, write=True)

    # ── free placement (sql/004_board_place.sql) ─────────────────────────
    @staticmethod
    def _overlap(a, b) -> bool:
        aw, ah = a[2:] if len(a) == 4 else (NOTE_W, NOTE_H)
        bw, bh = b[2:] if len(b) == 4 else (NOTE_W, NOTE_H)
        return a[0] < b[0] + bw and b[0] < a[0] + aw and a[1] < b[1] + bh and b[1] < a[1] + ah

    def _restore_position(self, q, card):
        """Keep a restored card's dimensions and relocate only it if its old space was reused."""
        if card["state"] != "active" or card["pos_x"] is None:
            return card
        w, h = card["size_w"] or NOTE_W, card["size_h"] or NOTE_H
        others = [(p["pos_x"], p["pos_y"], p["size_w"] or NOTE_W, p["size_h"] or NOTE_H)
                  for p in self._active(q) if p["id"] != card["id"] and p["pos_x"] is not None]
        def free(x, y):
            return x + w <= BOARD_W and not any(self._overlap((x,y,w,h),o) for o in others)
        if free(card["pos_x"], card["pos_y"]):
            return card
        for y in range(10, BOARD_MAX_Y + 1, 20):
            for x in range(10, BOARD_W - w + 1, 20):
                if free(x,y):
                    q("UPDATE guild.floor_postits SET pos_x=%s,pos_y=%s WHERE id=%s AND floor=%s", [x,y,card["id"],self.floor])
                    return {**card,"pos_x":x,"pos_y":y}
        raise S.FloorStoreUnavailable("no free position for restored card")

    def place(self, moves: list[dict], *, expect_order_rev: int, by, idempotency_key: str):
        """Put notes at positions on the board, in one transaction: ``moves`` is [{id, x, y}]. Outcome "moved", "conflict" (the arrangement changed:
        the value carries the current positions and order_rev), "occupied" (a note would overlap another: nothing is changed, the value carries
        the positions), "not_found" or "invalid" (ValueError). Placing never changes a note's text version, so an open edit is not disturbed."""
        if not isinstance(moves, list) or not 1 <= len(moves) <= MAX_MOVES:
            raise ValueError("send between 1 and %d moves" % MAX_MOVES)
        clean, seen = [], set()
        for m in moves:
            if not isinstance(m, dict):
                raise ValueError("each move is {id, x, y}")
            i, x, y = m.get("id"), m.get("x"), m.get("y")
            if any(isinstance(v, bool) or not isinstance(v, int) for v in (i, x, y)) or i <= 0 or i in seen:
                raise ValueError("each move needs a distinct whole-number id, x and y")
            w, h = m.get("w"), m.get("h")
            if (w is None) != (h is None) or (w is not None and (
                    any(isinstance(v, bool) or not isinstance(v, int) for v in (w, h))
                    or not SIZE_MIN_W <= w <= SIZE_MAX_W or not SIZE_MIN_H <= h <= SIZE_MAX_H)):
                raise ValueError("send both whole-number width and height within the size limits")
            if not (0 <= x <= BOARD_W and 0 <= y <= BOARD_MAX_Y):
                raise ValueError("a position is outside the board")
            seen.add(i)
            clean.append((i, x, y, w, h))
        now = S.utc_now()
        target = json.dumps({"m": sorted([list(c[:3]) if c[3] is None else list(c) for c in clean]), "r": int(expect_order_rev)}, sort_keys=True)

        def work(q):
            prior = self._claim(q, idempotency_key, by.id, "postit.place", target, now)
            if prior is not None:
                if not prior["same"]:
                    return S.WriteResult("idempotency_mismatch", None, True)
                return S.WriteResult(prior["outcome"], self._place_value(q), True)
            order_rev, _trash = self._meta(q)
            if order_rev != int(expect_order_rev):
                self._settle(q, idempotency_key, "conflict", None)
                return S.WriteResult("conflict", self._place_value(q), False)
            rows = q(f"SELECT id, pos_x, pos_y, size_w, size_h FROM guild.floor_postits WHERE floor = %s AND binned_at IS NULL "
                     f"AND done_at IS NULL{self._for_update}", [self.floor])
            final = {int(r[0]): (None if r[1] is None else int(r[1]), None if r[2] is None else int(r[2]), int(r[3] or NOTE_W), int(r[4] or NOTE_H)) for r in rows}
            if any(i not in final for i, _x, _y, _w, _h in clean):
                self._settle(q, idempotency_key, "not_found", None)
                return S.WriteResult("not_found", self._place_value(q), False)
            for i, x, y, w, h in clean:
                fw, fh = (w, h) if w is not None else final[i][2:]
                if x + fw > BOARD_W:
                    self._settle(q, idempotency_key, "invalid", None)
                    return S.WriteResult("invalid", self._place_value(q), False)
                final[i] = (x, y, fw, fh)
            placed = [(i, p) for i, p in final.items() if p[0] is not None]
            moved_ids = {i for i, _x, _y, _w, _h in clean}
            for i, p in placed:
                if i in moved_ids and any(j != i and self._overlap(p, o) for j, o in placed):
                    self._settle(q, idempotency_key, "occupied", None)
                    return S.WriteResult("occupied", self._place_value(q), False)
            for i, x, y, w, h in clean:
                if w is not None:
                    q("UPDATE guild.floor_postits SET size_w = %s, size_h = %s WHERE id = %s AND floor = %s", [w, h, i, self.floor])
                q("UPDATE guild.floor_postits SET pos_x = %s, pos_y = %s WHERE id = %s AND floor = %s", [x, y, i, self.floor])
            self._bump(q, order=True)
            self._settle(q, idempotency_key, "moved", None)
            return S.WriteResult("moved", self._place_value(q), False)
        return self._run(work, write=True)

    def _place_value(self, q) -> dict:
        order_rev, _trash = self._revs(q)
        return {"order_rev": order_rev,
                "positions": {str(p["id"]): [p["pos_x"], p["pos_y"]] for p in self._active(q) if p["pos_x"] is not None},
                "sizes": {str(p["id"]): [p["size_w"] or NOTE_W, p["size_h"] or NOTE_H] for p in self._active(q)}}

    def _order_value(self, q) -> dict:
        order_rev, _trash = self._revs(q)
        return {"order": [p["id"] for p in self._active(q)], "order_rev": order_rev}

    # ── Empty trash (R2) ─────────────────────────────────────────────────
    def empty_trash(self, *, trash_rev: int, items: list[dict], by, idempotency_key: str):
        """Delete exactly the confirmed Trash. Outcome "emptied" (value: the
        purge receipt), or "conflict" (value: the current Trash and
        trash_rev), which deletes nothing. A retry returns the receipt."""
        now = S.utc_now()
        wanted = sorted((int(i["id"]), int(i["version"])) for i in items)
        target = hashlib.sha256(json.dumps([int(trash_rev), wanted]).encode()).hexdigest()[:24]

        def work(q):
            prior = self._claim(q, idempotency_key, by.id, "postit.trash.empty", target, now)
            if prior is not None:
                if not prior["same"]:
                    return S.WriteResult("idempotency_mismatch", None, True)
                if prior["outcome"] == "emptied":
                    return S.WriteResult("emptied", json.loads(prior["ref"]), True)
                return S.WriteResult(prior["outcome"], self._trash_value(q), True)
            _order, rev = self._meta(q)
            current = q(f"SELECT id, version, kind, asset_id FROM guild.floor_postits WHERE floor = %s "
                        f"AND binned_at IS NOT NULL ORDER BY id{self._for_update}", [self.floor])
            have = sorted((int(r[0]), int(r[1])) for r in current)
            if rev != int(trash_rev) or have != wanted:
                self._settle(q, idempotency_key, "conflict", None)
                return S.WriteResult("conflict", self._trash_value(q), False)
            ids = [i for i, _v in wanted]
            if ids:
                marks = ", ".join(["%s"] * len(ids))
                photos = [str(int(r[0])) for r in current if r[2] == "photo"]
                if photos:
                    pm = ", ".join(["%s"] * len(photos))
                    q(f'UPDATE media."references" SET released_at = %s WHERE domain = \'guild\' AND ref_kind = \'postit\' '
                      f"AND ref_id IN ({pm}) AND released_at IS NULL", [now] + photos)
                q(f"DELETE FROM guild.floor_postits WHERE floor = %s AND binned_at IS NOT NULL AND id IN ({marks})",
                  [self.floor] + ids)
            receipt = {"receipt_id": f"t-{now[:19].replace('-', '').replace(':', '')}-{target[:6]}",
                       "items": [{"id": i, "version": v} for i, v in wanted], "count": len(wanted),
                       "principal": by.id, "at": now, "trash_rev": rev + 1}
            self._bump(q, trash=True)
            self._settle(q, idempotency_key, "emptied", json.dumps(receipt, sort_keys=True))
            return S.WriteResult("emptied", receipt, False)
        return self._run(work, write=True)

    def _trash_value(self, q) -> dict:
        _order, trash_rev = self._revs(q)
        return {"trash": self._trash_rows(q), "trash_rev": trash_rev}
