"""Memory across time (MC front door spec §5, A7, A8, case C8). The Build Log store is the real one on a real file."""
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.mc_tools import catalogue, effects
from core.mc_tools.service import Caller, ToolService
from domains.guild import queue_store as qs

CONV, NOTE_ID = "c-0123456789abcdef", "n-owner-note-1"


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


class _Receipts:
    def __init__(self):
        self.rows = {}

    def exists(self, conn, principal, environment, op):
        return op in self.rows

    def begin(self, conn, b, req, *, operation_id, worker):
        row = self.rows[operation_id] = {"receipt_id": f"rc-{len(self.rows) + 1}", "state": "dispatched", "outcome": None}

        def finish(conn, kind, outcome, *, verified):
            row.update(state="verified" if verified else "failed", outcome=outcome)
        return SimpleNamespace(finish=finish, unknown=lambda conn: row.update(state="unknown"))

    def reconcile(self, conn, *, principal, environment, operation_id, worker):
        row = self.rows[operation_id]
        return SimpleNamespace(op=operation_id) if row["state"] in ("dispatched", "unknown") else None

    def finish(self, conn, lease, state, *, evidence_refs=()):
        self.rows[lease.op]["state"] = state

    def read(self, conn, principal, environment, op):
        return self.rows[op]


class Base(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp()) / "state" / "guild"
        folder.mkdir(parents=True)
        self.path = folder / "build_queue.json"
        self.path.write_bytes(qs.serialize([
            {"id": 7, "spec_title": "German dictionary export", "status": "idea", "blocked_reason": None, "last_transition_at": "2026-11-02T10:00:00Z",
             "owner_words": "Export my German dictionary words every week."},
            {"id": 8, "spec_title": "Curator ranking fix", "status": "done", "blocked_reason": None, "owner_words": "Ranking looks wrong on Tuesdays."},
            {"id": 9, "spec_title": "Old German dictionary sync", "status": "superseded", "blocked_reason": None, "owner_words": "Sync the German dictionary."}]))
        self.receipts = _Receipts()
        for name, fn in (("receipt_exists", self.receipts.exists), ("begin_standing_effect", self.receipts.begin),
                         ("reconcile_effect", self.receipts.reconcile), ("record_evidence", lambda *a, **k: "ev-1"),
                         ("RECEIPTS", SimpleNamespace(finish=self.receipts.finish, read=self.receipts.read))):
            patcher = mock.patch.object(effects, name, fn)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.note = {"text": "Actually export the German dictionary every day."}
        self.note_id = NOTE_ID

    def store(self):
        return qs.QueueStore(self.path, in_container=False, code_root=self.path.parent.parent.parent / "code")

    def service(self, store=None):
        s = ToolService(lambda: _Conn(), principal="robert", environment="dev", read_owner_note=lambda c, n: self.note, build_log=store or self.store())
        b = SimpleNamespace(conversation_id=CONV, note_request_id=self.note_id, policy_version="p", context=lambda *a: None)
        s._bind = lambda conn, caller: b
        s._bind_again = lambda conn, caller, b: b
        return s

    def items(self):
        return {i["id"]: i for i in json.loads(self.path.read_text())}

    def revision(self, item_id=7):
        return qs.content_digest(self.items()[item_id])

    def search(self, query, store=None):
        caller = Caller("k", "mc-chat", "r", "c", None, "minimoi_build_log_search", SimpleNamespace(tier=1, max_operations_per_turn=1))
        return self.service(store)._build_log_search(catalogue.validate("minimoi_build_log_search", {"query": query}), caller, None)

    def add(self, *, kind="addition", item_id=7, revision=None, store=None, call_id="c1", **over):
        args = {"item_id": item_id, "revision": revision or self.revision(item_id), "kind": kind, "note": "He changed the frequency.", **over}
        caller = Caller("k", "mc-chat", "r", call_id, None, "minimoi_build_log_add_to", SimpleNamespace(tier=2, max_operations_per_turn=1))
        return self.service(store)._build_log_add_to(catalogue.validate("minimoi_build_log_add_to", args), caller, None)


class SearchTests(Base):
    def test_a_close_item_comes_back_with_where_and_when(self):
        r = self.search("export the german dictionary")
        self.assertEqual(r["data"]["state"], "candidates")
        first = r["data"]["candidates"][0]
        self.assertEqual((first["item_id"], first["last_changed"], first["revision"]), (7, "2026-11-02T10:00:00Z", self.revision()))
        self.assertTrue(first["current"])
        self.assertIn("not permission to merge", r["message"])

    def test_a_superseded_item_is_marked_so_and_ranked_after_a_current_one(self):
        ids = {c["item_id"]: c for c in self.search("german dictionary")["data"]["candidates"]}
        self.assertFalse(ids[9]["current"])
        self.assertLess([*ids].index(7), [*ids].index(9))

    def test_nothing_found_says_what_was_searched(self):
        r = self.search("kubernetes helm charts")
        self.assertEqual((r["data"]["state"], r["data"]["candidates"]), ("none_found", []))
        self.assertEqual(r["data"]["coverage"]["build_log_items_searched"], 3)
        self.assertIn("not searched", r["data"]["coverage"]["conversations"])

    def test_could_not_look_is_not_found_nothing(self):
        store = mock.Mock(read_items=mock.Mock(side_effect=OSError("no")))
        r = self.search("german dictionary", store)
        self.assertEqual((r["status"], r["code"]), ("refused", "search_unavailable"))
        self.assertIn("do not say it is new", r["message"])

    def test_it_only_reads(self):
        before = self.path.read_bytes()
        self.search("german dictionary")
        self.assertEqual(self.path.read_bytes(), before)
        from core.mc_tools import read_only
        self.assertIn("minimoi_build_log_search", read_only.READ_TOOLS)


class AddToTests(Base):
    def test_an_addition_keeps_his_words_and_the_original(self):
        original = self.items()[7]["owner_words"]
        r = self.add()
        self.assertEqual((r["status"], r["code"]), ("done", "added_to"))
        item = self.items()[7]
        self.assertEqual(item["owner_words"], original)
        [entry] = item["additions"]
        self.assertEqual((entry["owner_words"], entry["kind"]), (self.note["text"], "addition"))
        self.assertEqual(entry["note"]["by"], "master-craftsman")
        self.assertTrue(entry["at"])

    def test_a_conflict_keeps_both_and_one_open_question(self):
        r = self.add(kind="conflict", question="Weekly or daily?")
        self.assertEqual(r["code"], "added_to")
        self.assertIn("which holds", r["message"])
        item = self.items()[7]
        self.assertIn("every week", item["owner_words"])
        self.assertEqual(item["additions"][0]["open_question"], "Weekly or daily?")

    def test_a_conflict_needs_its_question(self):
        self.assertEqual(self.add(kind="conflict")["code"], "build_log_refused")
        self.assertNotIn("additions", self.items()[7])

    def test_a_stale_revision_is_refused_by_name(self):
        r = self.add(revision="a" * 64)
        self.assertEqual((r["status"], r["code"]), ("refused", "item_changed_since_read"))
        self.assertNotIn("additions", self.items()[7])

    def test_a_retry_adds_once(self):
        revision = self.revision()
        self.add(revision=revision)
        r = self.add(revision=revision, call_id="c2", note="Regenerated note.")
        self.assertEqual(r["code"], "already_added")
        self.assertEqual(len(self.items()[7]["additions"]), 1)

    def test_a_retry_on_a_busy_store_still_finds_it(self):
        revision = self.revision()
        self.add(revision=revision)
        store = self.store()
        store.add_to_item = lambda *a, **k: qs.SaveResult(qs.BUSY, item_id=7, op="add_to")
        self.assertEqual(self.add(revision=revision, store=store, call_id="c2")["code"], "already_added")

    def test_an_unknown_item_is_refused(self):
        self.assertEqual(self.add(item_id=99, revision="a" * 64)["code"], "build_log_refused")

    def test_keep_here_adds_nothing(self):
        self.note = {"text": "Daily, not weekly. Keep this here."}
        self.assertEqual(self.add()["code"], "kept_here")
        self.assertNotIn("additions", self.items()[7])

    def test_two_at_once_do_not_overwrite_each_other(self):
        """CoS and MC adding to one item: each is checked against what it read, and neither is lost silently."""
        store = self.store()
        words = ["first words", "second words"]
        out, revision = [], self.revision()
        def go(i):
            w = words[i]
            out.append(store.add_to_item(7, revision, source={"conversation": CONV, "note": f"n{i}", "chars": len(w), "sha256": hashlib.sha256(w.encode()).hexdigest()},
                                         owner_words=w, kind="addition", note={"by": "master-craftsman", "revision": 1, "text": "t"}, question=None, principal="robert"))
        threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(self.items()[7]["additions"]), sum(1 for r in out if r.ok))
        self.assertEqual(sum(1 for r in out if r.ok), 1)

    def test_an_addition_is_a_new_revision(self):
        before = self.revision()
        self.add()
        self.assertNotEqual(self.revision(), before)


if __name__ == "__main__":
    unittest.main()
