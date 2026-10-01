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

    def note_text(self, note_id: int) -> dict | None:
        """A stored, on-the-record note on this floor or one of its
        conversations (``<floor>/<id>``), by id: {id, text, floor}, or None."""
        def work(q):
            rows = q("SELECT id, text, floor, record_mode FROM guild.floor_messages WHERE id = %s "
                     "AND (floor = %s OR floor LIKE %s)", [int(note_id), self.floor, f"{self.floor}/%"])
            if not rows or rows[0][3] != "on_record":
                return None
            return {"id": int(rows[0][0]), "text": rows[0][1], "floor": rows[0][2]}
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
        rows = q(f"UPDATE guild.floor_postits SET {sets}, version = version + 1 WHERE id = %s AND floor = %s "
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
            return "undone", self._update_row(q, postit_id, "done_at = NULL, done_by = NULL", []), True
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
