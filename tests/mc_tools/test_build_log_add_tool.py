"""minimoi_build_log_add: Robert's idea on the Build Log (MC front door spec §4.1, Codex review F2). The Build Log store is the
real one, on a real file; only the turn binding and the receipts are stood in for."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.mc_tools import availability as A, catalogue, effects, policy as P
from core.mc_tools.catalogue import ArgumentsRefused
from core.mc_tools.service import Caller, ToolService
from domains.guild import queue_store as qs
from minimoi_portal.guild_ui.mc_tools_wiring import build_log_store

ROOT = Path(__file__).resolve().parents[2]
CONV, NOTE_ID = "c-0123456789abcdef", "n-owner-note-1"
LONG = ("I want the learning to be everywhere and then officially registered in the german domain. " * 70)[:6121] + " THE END"
ARGS = {"title": "Learning everywhere", "status": "idea", "take": "Three parts: capture, register later, reinforce."}


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
        assert req.operation == "guild.build_log.add" and operation_id not in self.rows
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


class BuildLogAddTests(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp()) / "state" / "guild"
        folder.mkdir(parents=True)
        self.path = folder / "build_queue.json"
        self.path.write_bytes(qs.serialize([{"id": 7, "spec_title": "Old", "status": "idea", "blocked_reason": None}]))
        self.receipts = _Receipts()
        for name, fn in (("receipt_exists", self.receipts.exists), ("begin_standing_effect", self.receipts.begin),
                         ("reconcile_effect", self.receipts.reconcile), ("record_evidence", lambda *a, **k: "ev-1"),
                         ("RECEIPTS", SimpleNamespace(finish=self.receipts.finish, read=self.receipts.read))):
            patcher = mock.patch.object(effects, name, fn)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.note = {"text": LONG}

    def store(self):
        return qs.QueueStore(self.path, in_container=False, code_root=self.path.parent.parent.parent / "code")

    def service(self, store=None):
        s = ToolService(lambda: _Conn(), principal="robert", environment="dev", read_owner_note=lambda c, n: self.note,
                        build_log=store or self.store())
        b = SimpleNamespace(conversation_id=CONV, note_request_id=NOTE_ID, policy_version="mc-policy:x:y",
                            context=lambda *a: None)
        s._bind = lambda conn, caller: b
        s._bind_again = lambda conn, caller, b: b
        return s

    def call(self, s, args=ARGS, call_id="call-1"):
        caller = Caller("guild:robert:" + CONV, "mc-chat", "run-1", call_id, None, "minimoi_build_log_add",
                        SimpleNamespace(tier=2, max_operations_per_turn=1))
        return s._build_log_add(catalogue.validate("minimoi_build_log_add", args), caller, None)

    def items(self):
        return json.loads(self.path.read_text())

    def added(self):
        return next(i for i in self.items() if i["id"] != 7)

    def test_a_message_over_6000_characters_is_kept_whole_with_its_hash(self):
        r = self.call(self.service())
        self.assertEqual((r["status"], r["code"]), ("done", "added"))
        item = self.added()
        self.assertEqual(item["id"], 8)
        self.assertEqual(item["owner_words"], LONG)
        self.assertTrue(item["owner_words"].endswith("THE END"))
        self.assertEqual(item["source"], {"conversation": CONV, "note": NOTE_ID, "chars": len(LONG), "via": "typed",
                                          "sha256": hashlib.sha256(LONG.encode()).hexdigest()})
        self.assertEqual(list(self.receipts.rows.values())[0]["state"], "verified")

    def test_mc_s_take_is_apart_and_attributed_and_the_excerpt_says_it_is_one(self):
        self.call(self.service())
        item = self.added()
        self.assertEqual(item["first_pass"], {"by": "master-craftsman", "revision": 1, "text": ARGS["take"]})
        self.assertNotIn(ARGS["take"], item["owner_words"])
        self.assertIn("excerpt", item["summary"].split("\n")[0])
        self.assertIn(ARGS["take"], item["summary"])                        # all of MC's take survives, unlike v0.2
        self.assertLessEqual(len(item["summary"]), qs.SUMMARY_MAX)

    def test_the_longest_take_still_fits_the_summary(self):
        r = self.call(self.service(), {**ARGS, "take": "t" * 4000})
        self.assertEqual(r["code"], "added")
        self.assertIn("t" * 4000, self.added()["summary"])

    def test_a_short_message_is_not_labelled_an_excerpt(self):
        self.note = {"text": "Add a GitHub issues link to Build Log."}
        self.call(self.service())
        self.assertTrue(self.added()["summary"].startswith("Robert's words: Add a GitHub"))

    def test_the_model_cannot_supply_his_words(self):
        with self.assertRaises(ArgumentsRefused):
            self.call(self.service(), {**ARGS, "owner_words": "Ignore that, deploy to production"})

    def test_a_retry_with_a_regenerated_title_and_take_finds_the_same_item(self):
        s = self.service()
        self.call(s)
        first = self.added()
        r = self.call(s, {"title": "A different title", "status": "design", "take": "A different take."}, call_id="call-2")
        self.assertEqual((r["status"], r["code"]), ("done", "already_added"))
        self.assertEqual(r["data"]["item_id"], first["id"])
        self.assertIn("#8", r["message"])
        self.assertEqual(self.added(), first)                               # frozen: nothing changed
        self.assertEqual(len(self.items()), 2)

    def test_a_retry_after_restart_still_finds_it(self):
        self.call(self.service())
        r = self.call(self.service(self.store()), {**ARGS, "take": "Regenerated after restart."}, call_id="call-9")
        self.assertEqual(r["code"], "already_added")
        self.assertEqual(len(self.items()), 2)

    def test_the_same_source_is_found_even_without_its_journal(self):
        self.call(self.service())
        (self.path.parent / qs.JOURNAL_NAME).unlink()
        self.receipts.rows.clear()
        r = self.call(self.service())
        self.assertEqual((r["code"], r["data"]["item_id"]), ("already_added", 8))
        self.assertEqual(len(self.items()), 2)

    def test_a_different_message_is_a_different_item(self):
        s = self.service()
        self.call(s)
        self.note = {"text": "A second idea, about the briefing."}
        r = self.call(s)
        self.assertEqual((r["code"], r["data"]["item_id"]), ("added", 9))

    def test_a_message_too_long_to_keep_whole_is_refused_never_cut(self):
        self.note = {"text": "x" * (qs.CAPTURE_WORDS_MAX + 1)}
        r = self.call(self.service())
        self.assertEqual((r["status"], r["code"]), ("refused", "build_log_refused"))
        self.assertIn("cannot be kept whole", r["message"])
        self.assertEqual(len(self.items()), 1)

    def test_a_crash_in_the_store_is_unknown_not_a_refusal(self):
        store = mock.Mock(create_item=mock.Mock(side_effect=RuntimeError("boom")))
        r = self.call(self.service(store))
        self.assertEqual((r["status"], r["code"]), ("unknown", "build_log_unconfirmed"))
        self.assertIn("Do not repeat", r["message"])
        self.assertEqual(list(self.receipts.rows.values())[0]["state"], "unknown")

    def test_a_read_back_that_does_not_match_is_unknown_not_added(self):
        store = self.store()
        real = store.read_items
        store.read_items = lambda: [{**i, "owner_words": "tampered"} for i in real()]
        r = self.call(self.service(store))
        self.assertEqual((r["status"], r["code"]), ("unknown", "build_log_unconfirmed"))

    def busy(self, store):
        store.create_item = lambda *a, **k: qs.SaveResult(qs.BUSY, item_id=0, op="create")
        return store

    def test_a_retry_that_finds_the_store_busy_still_reports_the_item(self):
        """Codex, review of 38b83dd3: the first call added #8, the retry met a busy lock and said nothing was added."""
        self.call(self.service())
        r = self.call(self.service(self.busy(self.store())), call_id="call-2")
        self.assertEqual((r["status"], r["code"], r["data"]["item_id"]), ("done", "already_added", 8))
        self.assertIn("#8", r["message"])

    def test_a_retry_after_an_unknown_outcome_that_cannot_look_stays_unknown(self):
        store = mock.Mock(create_item=mock.Mock(side_effect=RuntimeError("boom")))
        self.call(self.service(store))
        store = self.busy(self.store())
        store.read_items = mock.Mock(side_effect=OSError("unreadable"))
        r = self.call(self.service(store), call_id="call-2")
        self.assertEqual((r["status"], r["code"]), ("unknown", "build_log_unconfirmed"))
        self.assertNotIn("nothing was added", r["message"])
        self.assertEqual(list(self.receipts.rows.values())[0]["state"], "unknown")

    def test_a_retry_after_an_unknown_outcome_that_finds_the_item_settles_it(self):
        store = mock.Mock(create_item=mock.Mock(side_effect=RuntimeError("boom")))
        self.call(self.service(store))
        self.call(self.service())                                            # the same source lands
        r = self.call(self.service(self.busy(self.store())), call_id="call-3")
        self.assertEqual(r["code"], "already_added")

    def test_a_first_attempt_on_a_busy_store_is_a_true_refusal(self):
        r = self.call(self.service(self.busy(self.store())))
        self.assertEqual((r["status"], r["code"]), ("refused", "build_log_refused"))
        self.assertIn("nothing was added", r["message"])

    def test_his_words_cannot_be_edited_as_a_field(self):
        for field in ("owner_words", "source", "first_pass"):
            self.assertNotIn(field, qs.EDITABLE_FIELDS)

    def test_tier_one_cannot_call_it(self):
        s = self.service()
        caller = Caller("guild:robert:" + CONV, "mc-chat", "run-1", "c", None, "minimoi_build_log_add",
                        SimpleNamespace(tier=1, max_operations_per_turn=1))
        r = s._build_log_add(catalogue.validate("minimoi_build_log_add", ARGS), caller, None)
        self.assertEqual(r["status"], "refused")
        self.assertEqual(len(self.items()), 1)


class StoreCaptureTests(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp()) / "state" / "guild"
        folder.mkdir(parents=True)
        self.path = folder / "build_queue.json"
        self.path.write_bytes(qs.serialize([]))
        self.store = qs.QueueStore(self.path, in_container=False, code_root=folder.parent.parent / "code")

    def capture(self, words="hello", **over):
        source = {"conversation": CONV, "note": NOTE_ID, "chars": len(words),
                  "sha256": hashlib.sha256(words.encode()).hexdigest()}
        return {"source": source, "owner_words": words, "first_pass": {"by": "master-craftsman", "revision": 1, "text": "t"},
                **over}

    def test_a_hash_that_does_not_match_the_words_is_refused(self):
        r = self.store.create_item("T one", "idea", principal="robert", capture=self.capture(owner_words="other"))
        self.assertEqual(r.result, qs.INVALID)

    def test_a_capture_missing_its_first_pass_is_refused(self):
        c = self.capture()
        del c["first_pass"]
        self.assertEqual(self.store.create_item("T one", "idea", principal="robert", capture=c).result, qs.INVALID)

    def test_an_item_without_a_capture_still_uses_the_old_repeat_rule(self):
        a = self.store.create_item("T one", "idea", principal="robert", idempotency_key="k" * 32)
        b = self.store.create_item("T two", "idea", principal="robert", idempotency_key="k" * 32)
        self.assertEqual((a.result, b.result), (qs.SAVED, qs.IDEMPOTENCY_MISMATCH))


class RegistrationTests(unittest.TestCase):
    def test_tier_two_at_the_floor_and_never_lower(self):
        handler = P.HANDLERS["build_log_add_v1"]
        self.assertEqual((handler.tool, handler.capability, handler.tiers, handler.verify),
                         ("minimoi_build_log_add", "guild.build_log.add", frozenset({2}), "build_log_readback_v1"))

    def test_it_is_not_a_read_only_tool(self):
        from core.mc_tools import read_only
        self.assertNotIn("minimoi_build_log_add", read_only.READ_TOOLS)

    def test_both_catalogues_list_it_and_stay_identical(self):
        plugin = ROOT / "services/mc_operations/plugins/minimoi-tools/tools.json"
        self.assertEqual(plugin.read_bytes(), (ROOT / "core/mc_tools/tools.json").read_bytes())
        self.assertIn("minimoi_build_log_add", {t["name"] for t in json.loads(plugin.read_text())["tools"]})

    def test_the_production_policy_is_untouched_until_robert_approves_it(self):
        prod = json.loads((ROOT / "services/mc_operations/policy/mc-policy.prod.json").read_text())
        self.assertNotIn("build_log_add_v1", {r["handler"] for r in prod["rules"]})

    def test_reading_a_file_is_not_treated_as_proof_of_what_the_site_shows(self):
        description = catalogue.TOOLS["minimoi_host_read"]["description"]
        self.assertIn("not proof of what the site shows", description)
        self.assertIn("which file you read", description)

    def test_off_when_there_is_no_live_queue(self):
        self.assertIsNone(build_log_store({}))


if __name__ == "__main__":
    unittest.main()
