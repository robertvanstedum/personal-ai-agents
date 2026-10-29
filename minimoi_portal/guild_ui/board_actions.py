"""Board application operations in one borrowed domain transaction.

No transport or model authority lives here. An authenticated adapter constructs
BoardContext and binds a service to an owner/environment/floor. Receipts and
effects commit together; a connection/commit error is unknown to the caller.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from uuid import UUID, uuid5, NAMESPACE_URL

from .stores import Author, WriteResult, FloorStoreUnavailable, utc_now, _POSTIT_COLS, _postit
from domains.guild.board_wire import payload as wire_payload, digest as wire_digest
from domains.guild.board_contract import BoardContext, BoardRefused, LABELS
from domains.guild.board_text import clean, TextRefused



def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _reference(value):
    if type(value) is not str or not value.isascii() or not value.isdecimal() or value.startswith("0"):
        raise BoardRefused("note_reference_invalid")
    if len(value) > 18:
        raise BoardRefused("note_reference_invalid")
    return int(value)


def _snapshot(note):
    return {"text": note["text"], "label": note["label"], "item_ref": note["item_ref"],
            "done": note["state"] == "done", "state": note["state"],
            "semantic_revision": note["semantic_revision"]}


class BoardActions:
    def __init__(self, store, *, owner, environment, cursor_key, item_allowed=None, transaction_guard=None):
        if not owner or environment not in {"dev", "prod"} or not isinstance(cursor_key, bytes) or len(cursor_key) < 32:
            raise BoardRefused("board_configuration_invalid")
        self.store, self.owner, self.environment = store, owner, environment
        self._cursor_key = cursor_key
        # A queue reference cannot become valid simply because lookup is absent.
        self.item_allowed = item_allowed or (lambda principal, item: False)
        self.transaction_guard = transaction_guard

    def _admit(self, ctx, capability):
        if (type(ctx) is not BoardContext or ctx.principal != self.owner or ctx.environment != self.environment
                or ctx.board != self.store.floor or capability not in ctx.capabilities
                or not all(type(v) is str and 0 < len(v) <= 256 for v in (ctx.executor, ctx.source_ref))
                or (ctx.owner_session and ctx.executor != ctx.principal)):
            raise BoardRefused("board_scope_denied")

    def _scope(self, ctx):
        return [ctx.principal, ctx.environment, ctx.board]

    def _get(self, q, target, *, lock=False):
        if target is None:
            return None
        rows = q(f"SELECT {_POSTIT_COLS}, semantic_revision FROM guild.floor_postits WHERE floor=%s AND id=%s"
                 + (self.store._for_update if lock else ""), [self.store.floor, target])
        if not rows:
            return None
        note = _postit(rows[0][:-1])
        if note["kind"] != "note":
            return None
        note["semantic_revision"] = int(rows[0][-1])
        return note

    @staticmethod
    def _public(note, target=None):
        if note is None:
            return {"id": str(target), "state": "unavailable"} if target is not None else None
        return {"id": str(note["id"]), "text": note["text"], "label": note["label"],
                "item_ref": note["item_ref"], "state": note["state"],
                "semantic_revision": note["semantic_revision"]}

    def _original(self, q, ctx, operation_id):
        rows = q("SELECT executor, payload_sha256, receipt_json, before_json, after_json, payload_json "
                 "FROM guild.board_operations WHERE principal=%s AND environment=%s AND floor=%s AND operation_id=%s",
                 self._scope(ctx) + [operation_id])
        if not rows:
            return None
        actor, digest, receipt, before, after, payload = rows[0]
        if actor != ctx.executor and not ctx.owner_session:
            return {"denied": True}
        return {"executor": actor, "digest": digest, "receipt": json.loads(receipt),
                "before": json.loads(before) if before else None,
                "after": json.loads(after) if after else None, "payload": json.loads(payload)}

    def read(self, ctx, note_ref):
        self._admit(ctx, "guild.board.notes.read")
        target = _reference(note_ref)
        note = self.store._run(lambda q: self._get(q, target), write=False)
        if note is None:
            raise BoardRefused("note_not_found")
        return self._public(note)

    def result(self, ctx, operation_id):
        self._admit(ctx, "guild.board.operations.read")
        if not _uuid(operation_id):
            raise BoardRefused("operation_reference_invalid")

        def work(q):
            prior = self._original(q, ctx, operation_id)
            if prior is None or prior.get("denied"):
                # Absent is not evidence that another in-flight call cannot commit.
                return {"state": "unknown", "code": "operation_not_observed", "receipt": None,
                        "current": None, "replayed": False}
            return self._result(q, prior["receipt"], replayed=True)
        return self.store._run(work, write=False)

    def _result(self, q, receipt, *, replayed=False):
        target = int(receipt["target_ref"]) if receipt["target_ref"] else None
        state = "verified" if receipt["outcome"] in {"created", "updated", "unchanged", "undone", "already_undone"} else "not_applied"
        return {"state": state, "code": receipt["outcome"], "receipt": receipt,
                "current": self._public(self._get(q, target), target), "replayed": replayed}

    def _changes(self, ctx, value, *, creating=False):
        if type(value) is not dict or not value or set(value) - {"text", "label", "item_ref", "done"}:
            raise BoardRefused("changes_invalid")
        if creating and ("text" not in value or "done" in value):
            raise BoardRefused("create_invalid")
        out = dict(value)
        if "text" in out:
            out["text"] = clean(out["text"]).text
        if "label" in out and out["label"] is not None and out["label"] not in LABELS:
            raise BoardRefused("label_invalid")
        if "done" in out and type(out["done"]) is not bool:
            raise BoardRefused("done_invalid")
        if "item_ref" in out and out["item_ref"] is not None:
            item = out["item_ref"]
            if type(item) is not int or item < 1 or not self.item_allowed(ctx.principal, item):
                raise BoardRefused("item_reference_invalid")
        return out

    def create(self, ctx, operation_id, value):
        self._admit(ctx, "guild.board.note.create")
        self._changes(ctx, value, creating=True)
        return self._mutate(ctx, operation_id, "guild.board.note.create",
                            wire_payload("guild.board.note.create", value))

    def update(self, ctx, operation_id, note_ref, expected_revision, changes):
        self._admit(ctx, "guild.board.note.update")
        target = _reference(note_ref)
        if type(expected_revision) is not int or expected_revision < 1:
            raise BoardRefused("revision_invalid")
        self._changes(ctx, changes)
        payload = wire_payload("guild.board.note.update", {"expected_revision": expected_revision, "changes": changes}, target)
        return self._mutate(ctx, operation_id, "guild.board.note.update", payload)

    def undo(self, ctx, operation_id, original_operation_id, expected_revision):
        self._admit(ctx, "guild.board.operation.undo")
        if not _uuid(original_operation_id) or type(expected_revision) is not int or expected_revision < 1:
            raise BoardRefused("undo_invalid")
        return self._mutate(ctx, operation_id, "guild.board.operation.undo",
                            {"undo_of": original_operation_id, "expected_revision": expected_revision})

    def _apply_patch(self, q, ctx, target, changes, now):
        # Field names come only from this fixed mapping, never request SQL.
        sets, params = [], []
        for name in ("text", "label", "item_ref"):
            if name in changes:
                sets.append(name + "=%s")
                params.append(changes[name])
        if "done" in changes:
            sets += ["done_at=%s", "done_by=%s"]
            params += [now if changes["done"] else None, ctx.executor if changes["done"] else None]
        self.store._update_row(q, target, ", ".join(sets), params)
        note = self._get(q, target)
        if changes.get("done") is False:
            self.store._restore_position(q, note)
        self.store._bump(q, order=True)
        return self._get(q, target)

    def _effect(self, q, ctx, capability, payload, now):
        """Return outcome, before note, after note, target. Caller holds board lock."""
        if capability == "guild.board.note.create":
            change = payload["changes"]
            actor = Author(ctx.executor, "owner" if ctx.owner_session else "agent", ctx.executor)
            note = self.store._insert_postit(q, change["text"], actor, now,
                                             label=change.get("label"), item_ref=change.get("item_ref"))
            self.store._bump(q, order=True)
            return "created", None, self._get(q, note["id"]), note["id"]

        original = None
        target = payload.get("target")
        if capability == "guild.board.operation.undo":
            original = self._original(q, ctx, payload["undo_of"])
            if original is None or original.get("denied"):
                return "operation_not_found", None, None, None
            receipt = original["receipt"]
            target = int(receipt["target_ref"]) if receipt["target_ref"] else None
            if receipt["outcome"] not in {"created", "updated"}:
                return "undo_unavailable", None, None, target
            prior_undo = q("SELECT operation_id FROM guild.board_operations WHERE principal=%s AND environment=%s "
                           "AND floor=%s AND undo_of=%s AND after_json IS NOT NULL", self._scope(ctx) + [payload["undo_of"]])
            if prior_undo:
                return "already_undone", None, None, target
            if payload["expected_revision"] != receipt["after_revision"]:
                return "revision_conflict", None, None, target

        before = self._get(q, target, lock=True)
        if before is None:
            return "note_not_found", None, None, target
        if (("expected_ui_version" in payload and before["version"] != payload["expected_ui_version"])
                or ("expected_ui_version" not in payload and before["semantic_revision"] != payload["expected_revision"])):
            return "revision_conflict", before, None, target
        if before["state"] == "binned":
            return "in_bin", before, None, target
        if original and original["receipt"]["outcome"] == "created":
            self.store._update_row(q, target, "binned_at=%s, binned_by=%s, binned_by_label=%s",
                                   [now, ctx.executor, ctx.executor])
            self.store._bump(q, order=True, trash=True)
            return "undone", before, self._get(q, target), target
        if original:
            changed_fields = original["payload"]["changes"]
            changes = {field: original["before"][field] for field in changed_fields}
            # Revalidate restored links and text; no resurrected dangling queue reference.
            try:
                changes = self._changes(ctx, changes)
            except (BoardRefused, TextRefused):
                return "undo_value_invalid", before, None, target
        else:
            changes = payload["changes"]
        if all(_snapshot(before).get(k) == v for k, v in changes.items()):
            return "unchanged", before, None, target
        after = self._apply_patch(q, ctx, target, changes, now)
        return "undone" if original else "updated", before, after, target

    def _mutate(self, ctx, operation_id, capability, payload):
        if not _uuid(operation_id):
            raise BoardRefused("operation_identity_invalid")
        try:
            return self.store._run(lambda q: self._mutate_in(q, ctx, operation_id, capability, payload), write=True)
        except FloorStoreUnavailable:
            # Includes a lost connection on commit. Never claim not_applied here.
            return {"state": "unknown", "code": "board_unavailable", "receipt": None,
                    "current": None, "replayed": False}

    def _mutate_in(self, q, ctx, operation_id, capability, payload):
        """Borrow the caller's transaction; receipt and effect always settle together."""
        digest = wire_digest(capability, payload)
        self.store._meta(q)  # Existing shared board row lock; serial with UI writers.
        if self.transaction_guard is not None:
            self.transaction_guard(q, capability)
        prior = self._original(q, ctx, operation_id)
        if prior:
            if prior.get("denied") or prior["executor"] != ctx.executor or prior["digest"] != digest:
                return {"state": "not_applied", "code": "operation_conflict", "receipt": None,
                        "current": None, "replayed": True}
            return self._result(q, prior["receipt"], replayed=True)
        now = utc_now()
        outcome, before, after, target = self._effect(q, ctx, capability, payload, now)
        receipt = {"operation_id": operation_id, "capability": capability, "version": 1,
                   "principal": ctx.principal, "executor": ctx.executor, "environment": ctx.environment,
                   "board": ctx.board, "payload_sha256": digest, "target_ref": str(target) if target else None,
                   "outcome": outcome, "committed_at": now,
                   "before_revision": before["semantic_revision"] if before else None,
                   "after_revision": after["semantic_revision"] if after else None,
                   "source_ref": ctx.source_ref, "undo_of": payload.get("undo_of"),
                   "text_scrubbed": payload.get("text_scrubbed", False)}
        q("INSERT INTO guild.board_operations (principal,environment,floor,operation_id,executor,capability,"
          "payload_sha256,payload_json,receipt_json,before_json,after_json,target_ref,undo_of,committed_at) "
          "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
          self._scope(ctx) + [operation_id, ctx.executor, capability, digest, _json(payload), _json(receipt),
                             _json(_snapshot(before)) if before else None,
                             _json(_snapshot(after)) if after else None, target, payload.get("undo_of"), now])
        return self._result(q, receipt)

    def ui_write(self, ctx, key, changes, *, target=None, version=None, author=None):
        """Bridge old UI request keys/version checks into the same domain transaction.

        Old keys are claimed with the original byte convention, before the board
        lock, so retries from before promotion cannot duplicate their effects.
        Only authenticated owner adapters may use the UI version precondition.
        """
        capability = "guild.board.note.create" if target is None else "guild.board.note.update"
        self._admit(ctx, capability)
        if not ctx.owner_session or type(key) is not str or not key or len(key) > 256:
            raise BoardRefused("ui_request_invalid")
        scrubbed = clean(changes["text"]).scrubbed if "text" in changes else False
        changes = self._changes(ctx, changes, creating=target is None)
        if target is None:
            text, label, item = changes["text"], changes.get("label"), changes.get("item_ref")
            legacy_op = "postit.add"
            legacy_target = hashlib.sha256(f"{text}\x00{label}\x00{item}".encode()).hexdigest()[:16]
        else:
            if type(target) is not int or target < 1 or type(version) is not int or version < 1 or len(changes) != 1:
                raise BoardRefused("ui_request_invalid")
            field, value = next(iter(changes.items()))
            suffix = ("done" if value else "undone") if field == "done" else {"text": "text", "label": "label", "item_ref": "link"}[field]
            legacy_op = "postit." + suffix
            legacy_target = f"{target}:{version}:{json.dumps(value, sort_keys=True)}"
        operation_id = str(uuid5(NAMESPACE_URL, _json(["minimoi-board-ui-v1", *self._scope(ctx), key])))
        payload = {"changes": changes, "text_scrubbed": scrubbed}
        if target is not None:
            payload.update(target=target, expected_ui_version=version)

        def work(q):
            prior = self.store._claim(q, key, ctx.principal, legacy_op, legacy_target, utc_now())
            if prior is not None:
                if not prior["same"]:
                    return WriteResult("idempotency_mismatch", None, True)
                row = self.store._postit_by_id(q, prior["ref"]) if prior["ref"] else None
                done = WriteResult(prior["outcome"], row, True)
                receipt = self._original(q, ctx, operation_id)
                if receipt and not receipt.get("denied"):
                    done.operation = self._result(q, receipt["receipt"], replayed=True)
                return done
            result = self._mutate_in(q, ctx, operation_id, capability, payload)
            code = result["code"]
            outcome = {"created": "added", "revision_conflict": "conflict", "in_bin": "in_trash",
                       "note_not_found": "not_found", "operation_conflict": "idempotency_mismatch"}.get(code, code)
            if code == "updated":
                outcome = {"text": "edited", "label": "labelled", "item_ref": "linked", "done": "done" if changes.get("done") else "undone"}[next(iter(changes))]
            if code == "unchanged" and "done" in changes:
                outcome = "already_done" if changes["done"] else "already_active"
            receipt = result["receipt"]
            ref = receipt["target_ref"] if receipt else target
            self.store._settle(q, key, outcome, ref)
            row = self.store._postit_by_id(q, ref) if ref else None
            if target is None and row and author is not None:
                # Preserve the owner's display label; attribution is supplied by the authenticated adapter.
                q("UPDATE guild.floor_postits SET author_label=%s WHERE floor=%s AND id=%s", [author.label, self.store.floor, row["id"]])
                row["author_label"] = author.label
            done = WriteResult(outcome, row, False)
            done.operation = result
            return done
        try:
            return self.store._run(work, write=True)
        except FloorStoreUnavailable:
            return WriteResult("unknown", None, False)

    def query(self, ctx, *, query="", state="active", limit=10, cursor=None):
        self._admit(ctx, "guild.board.notes.read")
        if (type(query) is not str or len(query) > 200 or state not in {"active", "done", "all"}
                or type(limit) is not int or not 1 <= limit <= 20):
            raise BoardRefused("query_invalid")
        scope = [*self._scope(ctx), ctx.executor, query, state]
        last = None
        if cursor is not None:
            if type(cursor) is not str or not cursor:
                raise BoardRefused("cursor_invalid")
            try:
                body, signature = cursor.split(".")
                if len(cursor) > 2048 or not hmac.compare_digest(signature, hmac.new(self._cursor_key, body.encode(), hashlib.sha256).hexdigest()):
                    raise ValueError()
                saved = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
                if saved["scope"] != scope:
                    raise ValueError()
                last = saved["last"]
            except (ValueError, TypeError, KeyError):
                raise BoardRefused("cursor_invalid") from None

        def work(q):
            meta = q("SELECT order_rev FROM guild.floor_board_meta WHERE floor=%s", [ctx.board])
            where = "floor=%s AND kind='note' AND binned_at IS NULL"
            args = [ctx.board]
            if state != "all":
                where += " AND done_at IS " + ("NULL" if state == "active" else "NOT NULL")
            if query:
                where += " AND LOWER(text) LIKE %s ESCAPE '!'"
                args.append("%" + query.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%")
            if last:
                where += " AND (created_at<%s OR (created_at=%s AND id<%s))"
                args += [last[0], last[0], last[1]]
            rows = q(f"SELECT {_POSTIT_COLS}, semantic_revision FROM guild.floor_postits WHERE " + where +
                     " ORDER BY created_at DESC,id DESC LIMIT %s", args + [limit + 1])
            notes = [self._public({**_postit(row[:-1]), "semantic_revision": int(row[-1])}) for row in rows[:limit]]
            next_cursor = None
            if len(rows) > limit:
                row = rows[limit-1]
                body = base64.urlsafe_b64encode(_json({"scope": scope, "last": [str(row[5]), row[0]]}).encode()).decode().rstrip("=")
                next_cursor = body + "." + hmac.new(self._cursor_key, body.encode(), hashlib.sha256).hexdigest()
            return {"notes": notes, "next_cursor": next_cursor, "truncated": len(rows) > limit,
                    "observed_revision": int(meta[0][0]) if meta else 0, "observed_at": utc_now()}
        return self.store._run(work, write=False)
