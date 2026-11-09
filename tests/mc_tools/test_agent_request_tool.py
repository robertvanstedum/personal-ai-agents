"""minimoi_agent_request: a typed review or analysis request (MC front door spec §4.2, §4.3, A3, A4, A6). The ledger, the front
door and the Build Log store are the real ones; only the turn binding and the receipts are stood in for."""
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import agent_requests as ar
from core.mc_tools import availability as A, catalogue, effects, keep_here, policy as P
from core.mc_tools.catalogue import ArgumentsRefused
from core.mc_tools.service import Caller, ToolService
from core.supervision import relay as R
from domains.guild import queue_store as qs
from minimoi_portal.guild_ui.agent_ask import AskService
from minimoi_portal.guild_ui.mc_tools_wiring import agent_asker
from scripts.mc_host import supervision_sync as sync

ROOT = Path(__file__).resolve().parents[2]
CONV, NOTE_ID = "c-0123456789abcdef", "n-owner-note-1"
NOTE = "Can we think about this example direction?"
ANALYSIS = {"to": "claude-code", "purpose": "analysis", "analysis": "Is the inbox the right shape?"}


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
        assert req.operation in ("agent.request.typed", "agent.request.withdraw", "guild.build_log.add") and operation_id not in self.rows
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
        self.ledger = tempfile.mkdtemp()
        self.asks = AskService(self.ledger)
        folder = Path(tempfile.mkdtemp()) / "state" / "guild"
        folder.mkdir(parents=True)
        self.path = folder / "build_queue.json"
        words = "An example owner message."
        self.path.write_bytes(qs.serialize([{
            "id": 8, "spec_title": "An example idea", "status": "idea", "blocked_reason": None, "owner_words": words,
            "first_pass": {"by": "master-craftsman", "revision": 1, "text": "Three parts."},
            "source": {"conversation": CONV, "note": "n-0", "sha256": hashlib.sha256(words.encode()).hexdigest(), "chars": len(words)}}]))
        self.receipts = _Receipts()
        for name, fn in (("receipt_exists", self.receipts.exists), ("begin_standing_effect", self.receipts.begin),
                         ("reconcile_effect", self.receipts.reconcile), ("record_evidence", lambda *a, **k: "ev-1"),
                         ("RECEIPTS", SimpleNamespace(finish=self.receipts.finish, read=self.receipts.read))):
            patcher = mock.patch.object(effects, name, fn)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.note = {"text": NOTE}
        self.note_id = NOTE_ID

    def service(self):
        store = qs.QueueStore(self.path, in_container=False, code_root=self.path.parent.parent.parent / "code")
        s = ToolService(lambda: _Conn(), principal="robert", environment="dev", read_owner_note=lambda c, n: self.note,
                        agent_ask=agent_asker(self.asks), build_log=store)
        b = SimpleNamespace(conversation_id=CONV, note_request_id=self.note_id, policy_version="mc-policy:x:y", context=lambda *a: None)
        s._bind = lambda conn, caller: b
        s._bind_again = lambda conn, caller, b: b
        return s

    def call(self, args, s=None, call_id="call-1"):
        caller = Caller("guild:robert:" + CONV, "mc-chat", "run-1", call_id, None, "minimoi_agent_request",
                        SimpleNamespace(tier=2, max_operations_per_turn=1))
        return (s or self.service())._agent_request(catalogue.validate("minimoi_agent_request", args), caller, None)

    def docs(self):
        folder = Path(self.ledger) / "requests"
        return [ar.load(self.ledger, p.name)["request"] for p in sorted(folder.iterdir())] if folder.is_dir() else []

    def review(self, **over):
        return {"to": "codex", "purpose": "review", "item_id": 8, "analysis": "Worth a second look at the inbox.", **over}


class TypedRequestTests(Base):
    def test_a_review_carries_the_exact_revision_and_the_whole_item(self):
        r = self.call(self.review())
        self.assertEqual((r["status"], r["code"]), ("done", "requested"))
        [req] = self.docs()
        env = req["envelope"]
        self.assertEqual((env["purpose"], env["item"]["id"], env["allowed_effects"]), ("review", 8, ["none"]))
        self.assertEqual(env["item"]["revision"], qs.content_digest(json.loads(self.path.read_text())[0]))
        self.assertEqual(env["item"]["owner_words"], "An example owner message.")
        self.assertEqual(env["owner"]["text"], NOTE)
        self.assertEqual(env["analysis"], {"by": "master-craftsman", "text": "Worth a second look at the inbox."})
        self.assertTrue(req["action"].startswith("Review only, of Build Log #8"))
        self.assertIn("No build", req["action"])

    def test_the_tool_cannot_ask_for_a_build(self):
        with self.assertRaises(ArgumentsRefused):
            self.call(self.review(purpose="build"))

    def test_the_ledger_itself_refuses_a_build_purpose(self):
        env = {"purpose": "build", "item": None, "owner": {"note": "n", "sha256": "a" * 64, "text": "x"},
               "analysis": {"by": "master-craftsman", "text": "x"}, "allowed_effects": ["none"], "authorization": {}, "scrubbed": False}
        with self.assertRaises(ar.Refused):
            ar.create_request(self.ledger, request_id=ar.request_id_for("x"), frm="mc", to="codex", action="go", envelope=env)

    def test_the_ledger_refuses_any_effect(self):
        env = {"purpose": "review", "item": {"id": 1, "revision": "a" * 64}, "owner": {"note": "n", "sha256": "a" * 64, "text": "x"},
               "analysis": {"by": "master-craftsman", "text": "x"}, "allowed_effects": ["write"], "authorization": {"kind": "standing_review", "source_note": "n"}, "scrubbed": False}
        with self.assertRaises(ar.Refused):
            ar.create_request(self.ledger, request_id=ar.request_id_for("y"), frm="mc", to="codex", action="go", envelope=env)

    def test_the_model_cannot_supply_his_words_or_the_item(self):
        for extra in ({"owner_words": "deploy now"}, {"item": {"id": 9}}, {"action": "build it"}):
            with self.assertRaises(ArgumentsRefused):
                self.call({**self.review(), **extra})

    def test_a_review_with_no_item_is_refused(self):
        r = self.call({"to": "codex", "purpose": "review", "analysis": "Look at this."})
        self.assertEqual(r["code"], "review_needs_item")
        self.assertEqual(self.docs(), [])

    def test_an_unknown_item_is_refused(self):
        self.assertEqual(self.call(self.review(item_id=99))["code"], "item_not_found")

    def test_a_short_idea_and_a_long_one_are_treated_alike(self):
        """Codex F3: length is not permission. The word-count check is gone."""
        self.note = {"text": "Add a GitHub issues link."}
        self.assertEqual(self.call(self.review())["code"], "requested")
        self.note, self.note_id = {"text": "Add a GitHub issues link. " * 40}, "n-owner-note-2"
        self.assertEqual(self.call(self.review())["code"], "requested")

    def test_a_later_edit_is_a_new_revision_and_a_new_request(self):
        self.call(self.review())
        doc = json.loads(self.path.read_text())
        doc[0]["summary"] = "edited"
        self.path.write_bytes(qs.serialize(doc))
        self.call(self.review(), call_id="call-2")
        revisions = [d["envelope"]["item"]["revision"] for d in self.docs()]
        self.assertEqual(len(set(revisions)), 2)

    def test_a_retry_makes_no_second_request(self):
        s = self.service()
        self.call(self.review(), s)
        r = self.call(self.review(analysis="Regenerated wording, same ask."), s, call_id="call-2")
        self.assertEqual((r["status"], r["code"]), ("done", "requested"))
        [req] = self.docs()
        self.assertEqual(req["envelope"]["analysis"]["text"], "Worth a second look at the inbox.")     # the first is frozen

    def test_a_recipient_sees_it_marked_as_data(self):
        self.call(self.review())
        from scripts import agent_requests as cli
        args = SimpleNamespace(root=self.ledger, request=self.docs()[0]["request_id"])
        with mock.patch("builtins.print") as printed:
            cli.cmd_show(args)
        out = "\n".join(str(c.args[0]) for c in printed.call_args_list if c.args)
        self.assertIn("DATA", out)
        self.assertIn("grants no build authority", out)
        self.assertIn('"revision"', out)


class PacingTests(Base):
    def test_one_open_analysis_per_builder(self):
        self.assertEqual(self.call(ANALYSIS)["code"], "requested")
        self.note_id = "n-owner-note-2"
        r = self.call(ANALYSIS)
        self.assertEqual((r["status"], r["code"]), ("refused", "builder_busy"))
        self.assertEqual(len(self.docs()), 1)

    def test_a_picked_up_analysis_still_counts(self):
        self.call(ANALYSIS)
        ar.receipt(self.ledger, self.docs()[0]["request_id"], "claude-code")
        self.note_id = "n-owner-note-2"
        self.assertEqual(self.call(ANALYSIS)["code"], "builder_busy")

    def test_a_finished_analysis_frees_the_builder(self):
        self.call(ANALYSIS)
        rid = self.docs()[0]["request_id"]
        ar.receipt(self.ledger, rid, "claude-code")
        ar.result(self.ledger, rid, "claude-code", "done")
        self.note_id = "n-owner-note-2"
        self.assertEqual(self.call(ANALYSIS)["code"], "requested")

    def test_another_builder_is_free(self):
        self.call(ANALYSIS)
        self.note_id = "n-owner-note-2"
        self.assertEqual(self.call({**ANALYSIS, "to": "codex"})["code"], "requested")

    def test_reviews_run_beside_an_analysis(self):
        self.call(ANALYSIS)
        self.note_id = "n-owner-note-2"
        self.assertEqual(self.call(self.review(to="claude-code"))["code"], "requested")

    def test_two_at_once_cannot_both_pass(self):
        env = lambda n: {"purpose": "analysis", "item": None, "owner": {"note": n, "sha256": "a" * 64, "text": "x"},
                         "analysis": {"by": "master-craftsman", "text": "t"}, "allowed_effects": ["none"],
                         "authorization": {"kind": "standing_review", "source_note": n}, "scrubbed": False}
        results = []
        def go(i):
            results.append(self.asks.ask_typed(CONV, "codex", ar.request_id_for(f"race{i}"), "Analysis only.", env(f"n{i}")))
        threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sum(1 for r in results if r["ok"]), 1)
        self.assertEqual(sum(1 for r in results if r.get("busy")), 7)


class KeepHereTests(Base):
    def test_the_phrasings_he_uses(self):
        for text in ("Keep this here for now.", "keep it between us", "Don't send this anywhere.", "Don’t send this anywhere.",
                     "do not share that with anyone", "this is just between us"):
            self.assertTrue(keep_here.says_keep_here(text), text)

    def test_ordinary_words_are_not_caught(self):
        for text in ("Keep the link on the build log page", "don't worry about it", "Send this to Codex", "I want to keep going here"):
            self.assertFalse(keep_here.says_keep_here(text), text)

    def test_nothing_is_sent(self):
        self.note = {"text": NOTE + " Keep this here for now."}
        r = self.call(self.review())
        self.assertEqual((r["status"], r["code"]), ("refused", "kept_here"))
        self.assertIn("stays in this conversation", r["message"])
        self.assertEqual(self.docs(), [])

    def test_nothing_is_filed(self):
        self.note = {"text": NOTE + " Keep this here for now."}
        before = self.path.read_bytes()
        caller = Caller("guild:robert:" + CONV, "mc-chat", "run-1", "c", None, "minimoi_build_log_add",
                        SimpleNamespace(tier=2, max_operations_per_turn=1))
        args = {"title": "An idea", "status": "idea", "take": "My take."}
        r = self.service()._build_log_add(catalogue.validate("minimoi_build_log_add", args), caller, None)
        self.assertEqual((r["status"], r["code"]), ("refused", "kept_here"))
        self.assertEqual(self.path.read_bytes(), before)


class WholeItemAndScrubTests(Base):
    CARD = "4111 1111 1111 1111"

    def test_a_review_after_an_addition_carries_the_addition(self):
        """Codex: the new revision hash went out with only the original text."""
        store = qs.QueueStore(self.path, in_container=False, code_root=self.path.parent.parent.parent / "code")
        w = "No, the opposite of that."
        rev = qs.content_digest(json.loads(self.path.read_text())[0])
        saved = store.add_to_item(8, rev, source={"conversation": CONV, "note": "n-9", "chars": len(w), "sha256": hashlib.sha256(w.encode()).hexdigest()},
                                  owner_words=w, kind="conflict", note={"by": "master-craftsman", "revision": 1, "text": "He reversed it."},
                                  question="Which holds?", principal="robert")
        self.assertTrue(saved.ok, saved)
        self.call(self.review())
        env = self.docs()[0]["envelope"]
        self.assertEqual(env["item"]["additions"][0]["owner_words"], w)
        self.assertEqual(env["item"]["additions"][0]["open_question"], "Which holds?")
        self.assertEqual(env["item"]["revision"], qs.content_digest(json.loads(self.path.read_text())[0]))

    def service_with_scrub(self):
        s = self.service()
        real = s.agent_ask
        s.agent_ask = SimpleNamespace(typed=real.typed, withdraw=real.withdraw, scrub=lambda t: t.replace(self.CARD, "[card removed]"))
        return s

    def seed_card(self):
        doc = json.loads(self.path.read_text())
        doc[0]["owner_words"] = f"My card is {self.CARD} for the record."
        self.path.write_bytes(qs.serialize(doc))

    def test_stored_words_are_scrubbed_on_the_way_out(self):
        self.seed_card()
        self.call(self.review(), self.service_with_scrub())
        [req] = self.docs()
        self.assertNotIn(self.CARD, json.dumps(req))
        self.assertTrue(req["envelope"]["scrubbed"])

    def test_the_analysis_is_scrubbed(self):
        self.call(self.review(analysis=f"He gave {self.CARD} here."), self.service_with_scrub())
        self.assertNotIn(self.CARD, json.dumps(self.docs()[0]))

    def test_the_revision_still_names_the_original(self):
        self.seed_card()
        original = qs.content_digest(json.loads(self.path.read_text())[0])
        self.call(self.review(), self.service_with_scrub())
        self.assertEqual(self.docs()[0]["envelope"]["item"]["revision"], original)

    def test_clean_text_is_not_marked_scrubbed(self):
        self.call(self.review(), self.service_with_scrub())
        self.assertFalse(self.docs()[0]["envelope"]["scrubbed"])


class KeepHereEverywhereTests(Base):
    def test_delegation_proposals_respect_it(self):
        self.note = {"text": "Keep this here for now."}
        s = self.service()
        s.delegation = True
        for name, method in (("minimoi_delegate_build", s._delegate_build), ("minimoi_delegate_research", s._delegate_research)):
            caller = Caller("k", "mc-chat", "r", "c", None, name, SimpleNamespace(tier=3, max_operations_per_turn=1))
            self.assertEqual(method({"title": "x", "agent": "claude-code"}, caller, None)["code"], "kept_here", name)

    def test_a_named_ask_respects_it(self):
        self.note = {"text": "Ask Codex about this but keep this here for now."}
        caller = Caller("k", "mc-chat", "r", "c", None, "minimoi_agent_ask", SimpleNamespace(tier=2, max_operations_per_turn=1))
        r = self.service()._agent_ask(catalogue.validate("minimoi_agent_ask", {"to": ["codex"], "title": "A title"}), caller, None)
        self.assertEqual(r["code"], "kept_here")
        self.assertEqual(self.docs(), [])


class WithdrawTests(Base):
    def withdraw(self, rid, s=None):
        caller = Caller("guild:robert:" + CONV, "mc-chat", "run-1", "w-1", None, "minimoi_agent_request_withdraw",
                        SimpleNamespace(tier=2, max_operations_per_turn=1))
        return (s or self.service())._agent_request_withdraw(catalogue.validate("minimoi_agent_request_withdraw", {"request_id": rid}), caller, None)

    def test_a_queued_request_is_withdrawn_and_frees_the_builder(self):
        self.call(ANALYSIS)
        r = self.withdraw(self.docs()[0]["request_id"])
        self.assertEqual((r["status"], r["code"]), ("done", "withdrawn"))
        self.note_id = "n-owner-note-2"
        self.assertEqual(self.call(ANALYSIS)["code"], "requested")

    def test_a_picked_up_request_cannot_be_withdrawn(self):
        self.call(ANALYSIS)
        rid = self.docs()[0]["request_id"]
        ar.receipt(self.ledger, rid, "claude-code")
        self.assertEqual(self.withdraw(rid)["code"], "already_picked_up")
        self.assertIsNone(ar.load(self.ledger, rid)["closed"])

    def test_another_conversation_s_request_cannot_be_withdrawn(self):
        rid = ar.request_id_for("other")
        ar.create_request(self.ledger, request_id=rid, frm="mc", to="codex", action="Hello.", conversation_id="c-ffffffffffffffff")
        self.assertEqual(self.withdraw(rid)["code"], "not_withdrawable")


class VoiceTests(Base):
    def test_a_confirmed_spoken_turn_can_write_and_a_spoken_lookup_cannot(self):
        from core.mc_tools import read_only
        for tool in ("minimoi_build_log_add", "minimoi_agent_request", "minimoi_agent_request_withdraw"):
            self.assertTrue(read_only.allowed("vc-confirmed-turn", tool), tool)
        read_only.mark("vc-lookup-turn")
        for tool in ("minimoi_build_log_add", "minimoi_agent_request", "minimoi_agent_request_withdraw"):
            self.assertFalse(read_only.allowed("vc-lookup-turn", tool), tool)

    def test_a_spoken_idea_is_recorded_as_a_transcript(self):
        self.note_id = "vc-session-item"
        caller = Caller("guild:robert:" + CONV, "mc-chat", "run-1", "c", None, "minimoi_build_log_add",
                        SimpleNamespace(tier=2, max_operations_per_turn=1))
        args = {"title": "An idea", "status": "idea", "take": "My take."}
        r = self.service()._build_log_add(catalogue.validate("minimoi_build_log_add", args), caller, None)
        self.assertEqual(r["code"], "added")
        self.assertEqual(json.loads(self.path.read_text())[1]["source"]["via"], "voice")


class RelayTests(Base):
    def test_a_typed_request_reaches_the_mac_with_its_hash_intact(self):
        self.call(self.review())
        mine = R.Relay(self.ledger)
        pulled = [{**{k: d[k] for k in ("request_id", "to", "action", "conversation_id", "created_at", "content_sha256", "envelope")}} for d in self.docs()]
        self.assertEqual(mine.pull()[0].get("envelope"), self.docs()[0]["envelope"])
        mac = Path(tempfile.mkdtemp())
        self.assertTrue(sync.create_relayed(mac, pulled[0]))
        self.assertEqual(ar.load(str(mac), pulled[0]["request_id"])["request"]["envelope"], self.docs()[0]["envelope"])

    def test_a_relayed_envelope_with_a_build_purpose_is_refused_on_the_mac(self):
        self.call(self.review())
        d = self.docs()[0]
        bad = {**{k: d[k] for k in ("request_id", "to", "action", "conversation_id", "created_at", "content_sha256")},
               "envelope": {**d["envelope"], "purpose": "build"}}
        self.assertFalse(sync.create_relayed(Path(tempfile.mkdtemp()), bad))

    def test_a_relayed_envelope_allowing_effects_is_refused_even_with_a_matching_hash(self):
        self.call(self.review())
        d = self.docs()[0]
        env = {**d["envelope"], "allowed_effects": ["write", "deploy"]}
        body = {k: d[k] for k in ("v", "request_id", "from", "to", "action", "conversation_id", "topic_id", "pointer", "origin")}
        body["envelope"] = env
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        bad = {**{k: d[k] for k in ("request_id", "to", "action", "conversation_id", "created_at")}, "content_sha256": digest, "envelope": env}
        self.assertFalse(sync.create_relayed(Path(tempfile.mkdtemp()), bad))

    def test_an_altered_envelope_fails_the_hash(self):
        self.call(self.review())
        d = self.docs()[0]
        bad = {**{k: d[k] for k in ("request_id", "to", "action", "conversation_id", "created_at", "content_sha256")},
               "envelope": {**d["envelope"], "analysis": {"by": "master-craftsman", "text": "changed in flight"}}}
        self.assertFalse(sync.create_relayed(Path(tempfile.mkdtemp()), bad))


class BuildJobSwitchTests(unittest.TestCase):
    def cfg(self, **env):
        from minimoi_portal.guild_ui import mc_tools_wiring as W
        base = {W.FLAG: "1", "MINIMOI_MC_TOOLS_PRINCIPAL": "robert", "MINIMOI_MC_TURN_LEDGER": "1", "MINIMOI_MC_DELEGATION": "1"}
        with mock.patch.dict("os.environ", {"DATABASE_URL": "postgresql://x"}):
            return W.settings({**base, **env})

    def test_build_jobs_are_off_on_dev_and_on_in_production_by_default(self):
        self.assertFalse(self.cfg(MINIMOI_MC_TOOLS_ENV="dev")["build_jobs"])
        self.assertTrue(self.cfg(MINIMOI_MC_TOOLS_ENV="prod")["build_jobs"])
        self.assertTrue(self.cfg(MINIMOI_MC_TOOLS_ENV="dev", MINIMOI_MC_BUILD_JOBS="1")["build_jobs"])

    def test_a_disabled_build_is_refused_when_proposed(self):
        s = ToolService(lambda: _Conn(), principal="robert", environment="dev", delegation=True, build_jobs=False)
        caller = Caller("k", "mc-chat", "r", "c", None, "minimoi_delegate_build", SimpleNamespace(tier=3, max_operations_per_turn=1))
        self.assertEqual(s._delegate_build({"title": "x", "agent": "claude-code"}, caller, None)["status"], "refused")

    def test_a_card_left_over_from_before_is_refused_at_confirm(self):
        """Grok's case: with no build executor, a stored card can't start on a plain yes."""
        source = (ROOT / "core/mc_tools/proposals.py").read_text()
        self.assertIn('"delegate_build": self.start_build}', source)
        self.assertIn('if handlers[p["kind"]] is None:', source)
        wiring = (ROOT / "minimoi_portal/guild_ui/mc_tools_wiring.py").read_text()
        self.assertIn('start_build=start_build if cfg["delegation"] and cfg["build_jobs"] else None', wiring)


class RegistrationTests(unittest.TestCase):
    def test_tier_two_at_the_floor(self):
        h = P.HANDLERS["agent_request_v1"]
        self.assertEqual((h.tool, h.capability, h.tiers), ("minimoi_agent_request", "agent.request.typed", frozenset({2})))

    def test_it_is_not_a_read_only_tool(self):
        from core.mc_tools import read_only
        self.assertNotIn("minimoi_agent_request", read_only.READ_TOOLS)

    def test_the_production_policy_has_the_front_door_rows_as_reviewed_and_no_helpers(self):
        prod = json.loads((ROOT / "services/mc_operations/policy/mc-policy.prod.json").read_text())
        example = json.loads((ROOT / "services/mc_operations/policy/mc-policy.example.json").read_text())
        reviewed = {r["handler"]: r for r in example["rules"]}
        rows = {r["handler"]: r for r in prod["rules"]}
        for handler in ("agent_request_v1", "agent_request_withdraw_v1", "build_log_search_v1", "build_log_add_v1", "build_log_add_to_v1", "task_build_item_v1"):
            self.assertEqual(rows[handler], reviewed[handler])
        self.assertFalse({"helper_ask_v1", "helper_result_v1", "helper_review_v1"} & set(rows))


if __name__ == "__main__":
    unittest.main()
