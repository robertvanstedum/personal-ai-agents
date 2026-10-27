"""The relay over the supervision sync (OPS-2026-10-27-mc-front-door-prod): Robert's request, put to an agent through MC
on production, reaches the Mac's shared ledger with the same id and content, and the agent's replies come back into
production's ledger through the ledger's own rules. A temp production ledger, a temp Mac ledger, the real sync code;
the HTTPS call goes straight to the worker (no database: the supervision tables are stood in for)."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "mc_host"))

from core import agent_requests as ar  # noqa: E402
from core.supervision import relay as R  # noqa: E402
from core.supervision.store import Refused, Store  # noqa: E402
from minimoi_portal.guild_ui.agent_ask import AskService  # noqa: E402
import supervision_sync as sync  # noqa: E402

CONV = "c-0123456789abcdef"
NOTE = "Ask Grok to check the phone path.\n\tKeep it short."


class _Cur:
    def execute(self, *a):
        pass

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.prod, self.mac, self.state = (tempfile.mkdtemp() for _ in range(3))
        self.relay = R.Relay(self.prod)
        self.store = Store(lambda: None)
        self.store._run = lambda fn: fn(_Cur())
        for name, value in (("LEDGER", Path(self.mac)), ("ROOT", Path(self.state)),
                            ("WORKER_LEDGER", Path(self.state) / "no-worker")):
            patcher = mock.patch.object(sync, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(sync, "call", lambda body, secret: json.loads(json.dumps(
            self.store.worker(json.loads(sync._body(body)), relay=self.relay))))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.made = AskService(self.prod).ask({"id": CONV}, {"text": NOTE}, "n-1", ["grok", "codex"])
        self.grok, self.codex = (m["request_id"] for m in self.made)

    def mac_events(self, rid):
        return ar.load(self.mac, rid)["events"]

    def prod_doc(self, rid):
        return ar.load(self.prod, rid)

    def test_round_trip_request_down_receipt_and_result_up(self):
        got = sync.down("t")
        self.assertEqual(sorted(got["requests"]), sorted([self.grok, self.codex]))
        mine, theirs = ar.load(self.mac, self.grok)["request"], self.prod_doc(self.grok)["request"]
        self.assertEqual({k: mine[k] for k in mine if k != "created_at"}, {k: theirs[k] for k in theirs if k != "created_at"})
        self.assertEqual(mine["action"], NOTE)
        # The ledger itself reads the Mac's copy as its own: same content, a delivery event, the wake hint.
        self.assertEqual(ar.reconcile_request(self.mac, self.grok, frm="mc", to="grok", action=NOTE,
                                              conversation_id=CONV, origin="owner_message_via_mc"), "recorded")
        self.assertEqual(ar.create_request(self.mac, request_id=self.grok, frm="mc", to="grok", action=NOTE,
                                           conversation_id=CONV, origin="owner_message_via_mc")[1], False)
        self.assertEqual([e["kind"] for e in self.mac_events(self.grok)], ["delivery"])
        self.assertEqual(Path(self.mac, "pending", "grok.log").read_text().strip(), ar.notice(mine))
        self.assertEqual([d["request"]["request_id"] for d in ar.pending(self.mac, "grok")], [self.grok])
        self.assertTrue(self.relay.relayed(self.grok))
        self.assertEqual(self.relay.pull(), [])                              # taken: not offered again
        self.assertEqual(sync.down("t")["requests"], [])
        # Grok answers on the Mac; the next sync carries it up, and production keeps it with Grok's name.
        ar.receipt(self.mac, self.grok, "grok")
        ar.progress(self.mac, self.grok, "grok", "Looking at it.")
        ar.result(self.mac, self.grok, "grok", "The phone path breaks at login.\n\t- step 2")
        up = sync.up("t")
        self.assertEqual([o["state"] for o in up["relay_events"]], ["applied"] * 3)
        doc = self.prod_doc(self.grok)
        self.assertEqual([(e["kind"], e["actor"]) for e in doc["events"]],
                         [("delivery", "ledger"), ("receipt", "grok"), ("progress", "grok"), ("result", "grok")])
        self.assertEqual((doc["closed"], doc["events"][-1]["text"]), ("result", "The phone path breaks at login.\n\t- step 2"))
        self.assertNotIn("relay_events", sync.up("t"))                        # each reply once
        self.assertEqual(len(self.prod_doc(self.grok)["events"]), 4)

    def test_the_owner_s_conversation_gets_the_agent_s_report_attributed(self):
        sync.down("t")
        ar.result(self.mac, self.codex, "codex", "Done: two defects.")
        sync.up("t")
        notes = AskService(self.prod)._notes_for(self.prod_doc(self.codex))
        self.assertEqual(notes[-1][2].id, "codex")
        self.assertIn("Done: two defects.", notes[-1][1])

    def test_an_event_for_a_request_not_relayed_is_refused(self):
        event = {"request_id": self.grok, "event_id": "0123abcd", "kind": "result", "actor": "grok", "text": "Fake."}
        out = self.store.worker({"phase": "report", "relay_events": [event]}, relay=self.relay)
        self.assertEqual(out["relay_events"], [{"key": f"{self.grok}:0123abcd", "state": "refused", "code": "not_relayed"}])
        missing = dict(event, request_id="r-" + "e" * 24)
        self.assertEqual(self.relay.apply([missing])[0]["code"], "not_relayed")
        self.assertEqual([e["kind"] for e in self.prod_doc(self.grok)["events"]], ["delivery"])
        out = self.store.worker({"phase": "report", "relay_events": [event]})          # front door off: nothing
        self.assertEqual(out["relay_events"][0]["code"], "relay_off")

    def test_the_ledger_s_actor_rules_hold_and_robert_cannot_be_relayed(self):
        self.relay.ack([self.grok])
        wrong = [{"request_id": self.grok, "event_id": "0000aaaa", "kind": "result", "actor": "codex", "text": "Me."},
                 {"request_id": self.grok, "event_id": "0000bbbb", "kind": "cancel", "actor": "grok"},
                 {"request_id": self.grok, "event_id": "0000cccc", "kind": "receipt", "actor": "mc"}]
        self.assertEqual([o["code"] for o in self.relay.apply(wrong)], ["not_recipient", "not_sender", "not_recipient"])
        self.assertEqual([e["kind"] for e in self.prod_doc(self.grok)["events"]], ["delivery"])
        for bad in ({"actor": "robert"}, {"actor": "ledger"}, {"kind": "delivery"}, {"kind": "approve"}):
            with self.assertRaises(Refused, msg=bad):
                self.store.worker({"phase": "report", "relay_events": [
                    {"request_id": self.grok, "event_id": "0000dddd", "kind": "cancel", "actor": "mc", **bad}]},
                    relay=self.relay)
        out = self.relay.apply([{"request_id": self.grok, "event_id": "0000eeee", "kind": "cancel", "actor": "mc",
                                 "text": ""}])
        self.assertEqual(out[0]["state"], "applied")                                   # the sender may cancel

    def test_duplicates_have_one_effect(self):
        self.relay.ack([self.grok])
        progress = {"request_id": self.grok, "event_id": "1111aaaa", "kind": "progress", "actor": "grok", "text": "Half."}
        result = {"request_id": self.grok, "event_id": "2222bbbb", "kind": "result", "actor": "grok", "text": "Done."}
        self.assertEqual([o["state"] for o in self.relay.apply([progress, result])], ["applied", "applied"])
        self.assertEqual([o["state"] for o in self.relay.apply([progress, result])], ["duplicate", "duplicate"])
        again = dict(result, event_id="3333cccc")                                      # the same report, new id
        self.assertEqual(self.relay.apply([again])[0]["state"], "duplicate")
        other = dict(result, event_id="4444dddd", text="A different answer.")
        self.assertEqual(self.relay.apply([other])[0], {"key": f"{self.grok}:4444dddd", "state": "refused",
                                                         "code": "exists"})
        self.assertEqual([e["kind"] for e in self.prod_doc(self.grok)["events"]],
                         ["delivery", "receipt", "progress", "result"])
        self.assertEqual(self.relay.ack([self.grok]), [self.grok])                    # a second ack changes nothing
        self.assertEqual(Path(self.prod, "requests", self.grok, R.MARKER).read_text().count("relayed_at"), 1)

    def test_the_worker_never_creates_a_request_on_production(self):
        self.assertEqual(self.relay.ack(["r-" + "f" * 24]), [])
        self.assertFalse(Path(self.prod, "requests", "r-" + "f" * 24).exists())
        mine = ar.create_request(self.prod, request_id=ar.request_id_for("agent"), frm="codex", to="grok",
                                 action="Agent to agent.")[0]
        self.assertEqual(self.relay.ack([mine["request_id"]]), [])                    # not Robert's via MC: not relayed
        self.assertNotIn(mine["request_id"], [r["request_id"] for r in self.relay.pull()])
        for body in ({"phase": "report", "requests": []}, {"phase": "pull", "requests": []},
                     {"phase": "ack", "requests": ["../x"]}, {"phase": "ack", "requests": ["r-" + "a" * 24] * 21}):
            with self.assertRaises(Refused, msg=body):
                self.store.worker(body, relay=self.relay)

    def test_a_pull_that_does_not_match_its_hash_is_not_created_or_acked(self):
        [pulled] = [r for r in self.relay.pull() if r["to"] == "grok"]
        self.assertFalse(sync.create_relayed(Path(self.mac), dict(pulled, action="Something Robert never said.")))
        self.assertFalse(sync.create_relayed(Path(self.mac), dict(pulled, to="robert")))
        self.assertFalse(Path(self.mac, "requests", self.grok).exists())
        ar.create_request(self.mac, request_id=self.grok, frm="grok", to="codex", action="Another request.")
        self.assertFalse(sync.create_relayed(Path(self.mac), pulled))                 # the id holds another request

    def test_listeners_seen_on_the_mac_are_said_on_production_with_their_age(self):
        ar.arm(self.mac, "grok")
        os.utime(Path(self.mac, "pending", "grok.armed"), (time.time() - 30, time.time() - 30))
        ar.arm(self.mac, "codex")
        os.utime(Path(self.mac, "pending", "codex.armed"), (time.time() - 600, time.time() - 600))
        self.assertEqual(sorted(sync.listeners(time.time())), ["grok"])
        self.assertEqual(sync.up("t")["listeners"], ["grok"])
        self.assertAlmostEqual(ar.armed_age(self.prod, "grok"), 30, delta=5)
        self.assertIsNone(ar.armed_age(self.prod, "codex"))
        self.assertIn("a listener is running", AskService(self.prod).words(
            AskService(self.prod).ask({"id": CONV}, {"text": "Again."}, "n-2", ["grok"])))
        for bad in ({"robert": 1}, {"grok": -1}, {"grok": "1"}, ["grok"]):
            with self.assertRaises(Refused, msg=bad):
                self.store.worker({"phase": "report", "listeners": bad}, relay=self.relay)

    def test_bounds(self):
        self.relay.ack([self.grok])
        base = {"request_id": self.grok, "event_id": "5555aaaa", "kind": "result", "actor": "grok", "text": "x"}
        for bad in ({"text": "x" * (ar.TEXT_MAX + 1)}, {"text": "bell\x07"}, {"text": ""}, {"event_id": "ask"},
                    {"request_id": "../../etc"}, {"extra": 1}):
            with self.assertRaises(Refused, msg=bad):
                self.store.worker({"phase": "report", "relay_events": [{**base, **bad}]}, relay=self.relay)
        with self.assertRaises(Refused):
            self.store.worker({"phase": "report", "relay_events": [dict(base, event_id=f"{i:08x}") for i in range(21)]},
                              relay=self.relay)
        with self.assertRaises(Refused):                                                 # a receipt carries no words
            self.store.worker({"phase": "report", "relay_events": [dict(base, kind="receipt")]}, relay=self.relay)
        made = [AskService(self.prod).ask({"id": CONV}, {"text": f"Request {i}."}, f"n-{i}", ["codex"])[0]
                for i in range(25)]
        self.assertEqual(len(made), 25)
        self.assertEqual(len(self.relay.pull()), R.MAX_PULL)

    def test_the_mac_packs_long_replies_under_the_body_bound_and_never_stalls(self):
        sync.down("t")
        big = "✓" * ar.TEXT_MAX                                                          # 48 KiB each in UTF-8
        rids = [self.grok, self.codex]
        for rid, who in zip(rids, ("grok", "codex")):
            for _ in range(3):
                ar.progress(self.mac, rid, who, big)
        events, _ = sync.relay_events(set(rids), set())
        self.assertEqual(len(events), 8)                                                # 2 receipts (implied), 6 big
        sent = []
        real = sync.call

        def measured(body, secret):
            sent.append(len(sync._body(body)))
            return real(body, secret)
        with mock.patch.object(sync, "call", measured):
            for _ in range(6):
                sync.up("t")
        self.assertTrue(all(n <= sync.RELAY_BYTES for n in sent))
        self.assertEqual(sum(1 for e in self.prod_doc(self.grok)["events"] if e["kind"] == "progress"), 3)
        self.assertEqual(sum(1 for e in self.prod_doc(self.codex)["events"] if e["kind"] == "progress"), 3)

    def test_both_mac_ledgers_are_mirrored_once_each(self):
        worker = Path(self.state, "worker")
        with mock.patch.object(sync, "WORKER_LEDGER", worker):
            rid = ar.request_id_for("direct")
            ar.create_request(str(worker), request_id=rid, frm="mc", to="codex", action="Status, please.")
            ar.create_request(self.mac, request_id=rid, frm="mc", to="codex", action="Status, please.")
            other = ar.create_request(self.mac, request_id=ar.request_id_for("shared"), frm="grok", to="codex",
                                      action="Review P0.")[0]["request_id"]
            asks = [e for e in sync.exchanges(set(), time.time()) if e["kind"] == "ask"]
        self.assertEqual(sorted(e["request_id"] for e in asks), sorted([rid, other]))


class ContentHashTests(unittest.TestCase):
    def test_the_sync_s_hash_is_the_ledger_s(self):
        body = {"v": 1, "request_id": "r-" + "a" * 24, "from": "mc", "to": "grok", "action": "Ünïcode ✓",
                "conversation_id": None, "topic_id": None, "pointer": None, "origin": "owner_message_via_mc"}
        from core.agent_requests.ledger import _digest
        self.assertEqual(_digest(body), hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False)
                                                       .encode("utf-8")).hexdigest())


if __name__ == "__main__":
    unittest.main()
