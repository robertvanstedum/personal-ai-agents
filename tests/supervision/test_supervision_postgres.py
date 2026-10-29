"""Build supervision end to end on a disposable PostgreSQL (MC_BINDING_TEST_DSN, an mc12_test_* database): an agent
asks, the bot sends it, only Robert's reply to that message answers it, the Mac sync gets the answer once, and a
Guild answer lands in the same record."""
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "minimoi_actions"))
from postgres_fixture_support import DSN, PostgresFixture  # noqa: E402

from core.supervision.store import Store, Refused  # noqa: E402
from tests.supervision.test_supervision import OWNER, BOT, update, FakeGitHub, pr, DONE  # noqa: E402
from services.supervisor.main import Supervisor  # noqa: E402


class FakeBot:
    id = BOT

    def __init__(self):
        self.sent, self.inbox, self.next_id = [], [], 500

    def send(self, chat_id, text, *, reply_to=None):
        self.next_id += 1
        self.sent.append((self.next_id, text, reply_to))
        return self.next_id

    def updates(self, offset, *, wait=25):
        out, self.inbox = [u for u in self.inbox if u["update_id"] >= offset], []
        return out


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class SupervisionFlow(PostgresFixture, unittest.TestCase):
    migrations = ("core/supervision/sql/001_supervision.sql", "core/supervision/sql/002_grok_and_exchanges.sql")

    def setUp(self):
        with self.guard.cursor() as cur:
            cur.execute("TRUNCATE guild.supervision_nudges, guild.supervision_stops, guild.supervision_decisions, "
                        "guild.supervision_checkins, guild.supervision_alerts RESTART IDENTITY CASCADE")
            cur.execute("UPDATE guild.supervision_telegram SET offset_=0")
        self.guard.commit()
        self.store = Store(self.connect)
        self.bot = FakeBot()
        self.sup = Supervisor(self.store, self.bot, FakeGitHub(), chat_id=OWNER, user_id=OWNER)

    def ask(self, key="ask-0000000001", text="Merge PR #391 at abc1234 to main and deploy the portal to prod"):
        return self.store.worker({"phase": "report", "asks": [
            {"request_key": key, "agent": "claude-code", "session": "sess-abcd", "branch": "b", "text": text}],
            "stops": [{"report_key": "stop-00000001", "agent": "claude-code", "session": "sess-abcd",
                       "state": "awaiting_owner", "decision_key": key, "stopped_at": 1761500000.0}]})

    def reply(self, text, update_id, **kw):
        u = update(text, reply_to=self.bot.sent[0][0], **kw)
        u["update_id"] = update_id
        self.bot.inbox.append(u)
        self.sup.read_answers(wait=0)

    def test_telegram_reply_answers_and_reaches_the_agent_once(self):
        out = self.ask()
        self.assertEqual(out["asks"], [{"request_key": "ask-0000000001", "code": "D-1", "accepted": True}])
        self.assertEqual(self.store.worker({"phase": "pull"}), {"decisions": [], "nudges": []})
        self.sup.send_new()
        self.assertIn("Merge PR #391 at abc1234", self.bot.sent[0][1])
        self.reply("yes", 10, sender=12345)                 # not Robert
        self.reply("yes", 11, forward_origin={"type": "user"})
        self.reply("maybe later", 12)                        # not an answer
        self.assertEqual(self.store.pending()[0]["state"], "sent")
        self.reply("Yes", 13)
        got = self.store.worker({"phase": "pull"})["decisions"]
        self.assertEqual(len(got), 1)
        d = got[0]
        self.assertEqual((d["code"], d["state"], d["session"], d["receipt"]["channel"], d["receipt"]["text"]),
                         ("D-1", "approved", "sess-abcd", "telegram", "Yes"))
        self.reply("no", 14)                                 # already answered: nothing changes
        self.store.worker({"phase": "ack", "decisions": ["ask-0000000001"]})
        self.assertEqual(self.store.worker({"phase": "pull"})["decisions"], [])
        self.assertEqual(self.store.offset(), 15)
        self.sup.announce()
        self.assertIn("Recorded: D-1 approved here", self.bot.sent[-1][1])
        self.assertEqual(self.store.by_code("D-1")["state"], "approved")

    def test_same_key_other_words_does_not_change_the_decision(self):
        self.ask()
        out = self.ask(text="Merge PR #391 and ALSO drop the production database")
        self.assertFalse(out["asks"][0]["accepted"])
        self.assertEqual(self.store.by_code("D-1")["text"], "Merge PR #391 at abc1234 to main and deploy the portal to prod")

    def test_guild_answer_lands_in_the_same_record(self):
        self.ask()
        self.sup.send_new()
        from minimoi_portal.guild_ui import supervision
        import os
        os.environ.setdefault("DATABASE_URL", "postgresql://unused")
        found = (None, "robert", {"id": "conv-1"}, None, {"text": "no D-1", "author_kind": "owner", "who": "robert"},
                 "note-9", "req", [])
        r = supervision.owner_decision_command(found, environ={"MINIMOI_SUPERVISION": "1",
                                                               "MINIMOI_SUPERVISION_OWNER": "robert"},
                                               store_factory=lambda: self.store)
        self.assertEqual(r["operation"], {"type": "owner_decision", "state": "verified", "decision": "D-1",
                                          "answer": "declined"})
        d = self.store.worker({"phase": "pull"})["decisions"][0]
        self.assertEqual((d["state"], d["receipt"]["channel"], d["receipt"]["note_id"]), ("declined", "guild", "note-9"))
        self.reply("yes", 20)                                # Telegram after Guild: no second answer
        self.assertEqual(self.store.by_code("D-1")["state"], "declined")
        block = supervision.context_block("robert", environ={"MINIMOI_SUPERVISION": "1",
                                                             "MINIMOI_SUPERVISION_OWNER": "robert"},
                                          store_factory=lambda: self.store)
        self.assertNotIn("D-1:", block)                      # no longer waiting

    def test_checkin_tells_a_finished_ci_once_and_nudges_nobody(self):
        self.store.worker({"phase": "report", "stops": [
            {"report_key": "stop-ci000001", "agent": "claude-code", "session": "sess-ci01", "state": "waiting_ci",
             "pr": 7, "stopped_at": time.time() - 60}]})
        self.sup.github = FakeGitHub(prs=[pr(7, "e" * 40)], checks={"e" * 40: [DONE]})
        summary, told = self.sup.checkin()
        self.assertEqual([t["key"] for t in told], [f"pr-green:7:{'e' * 12}", "ci-finished:1:passed"])
        self.assertEqual(self.bot.sent[-1][1].splitlines()[0], "MC check-in")
        self.assertIn("sess-ci0 waits on PR #7, whose CI has finished (passed)", self.bot.sent[-1][1])
        self.assertEqual(self.store.worker({"phase": "pull"}), {"decisions": [], "nudges": []})
        with self.guard.cursor() as cur:
            cur.execute("SELECT count(*) FROM guild.supervision_nudges")
            self.assertEqual(cur.fetchone()[0], 0)           # nothing queued for the session
            # A ci_finished nudge queued before the retirement stays in the table and is never delivered.
            cur.execute("""INSERT INTO guild.supervision_nudges (agent, session, kind, detail, stop_id)
                VALUES ('claude-code', 'sess-ci01', 'ci_finished', '{"conclusion": "passed"}', 1)""")
        self.guard.commit()
        self.assertEqual(self.store.worker({"phase": "pull"})["nudges"], [])
        sent = len(self.bot.sent)
        self.sup.checkin()                                   # nothing new: no message, still recorded
        self.assertEqual(len(self.bot.sent), sent)
        self.assertEqual(self.store.latest_checkin()["telegram"], "none")

    def test_portal_path_authenticates_the_sync(self):
        import hashlib
        import os
        from flask import Flask
        from minimoi_portal.guild_ui import supervision
        os.environ.setdefault("DATABASE_URL", "postgresql://unused")
        token = "t" * 48
        env = {"MINIMOI_SUPERVISION": "1", "MINIMOI_SUPERVISION_WORKER_TOKEN_SHA256": hashlib.sha256(token.encode()).hexdigest()}
        app = Flask("t")
        self.assertTrue(supervision.attach(app, env, store_factory=lambda: self.store))
        self.assertFalse(supervision.attach(app, env, store_factory=lambda: self.store))      # a second mount: no-op
        self.assertFalse(supervision.attach(Flask("off"), {"MINIMOI_SUPERVISION": "1"}))       # no hash: off
        c = app.test_client()
        self.assertEqual(c.post(supervision.PATH, json={"phase": "pull"}).status_code, 401)
        self.assertEqual(c.post(supervision.PATH, json={"phase": "pull"},
                                headers={"Authorization": "Bearer " + "x" * 48}).status_code, 401)
        ok = c.post(supervision.PATH, json={"phase": "pull"}, headers={"Authorization": "Bearer " + token})
        self.assertEqual((ok.status_code, ok.get_json()), (200, {"decisions": [], "nudges": []}))
        bad = c.post(supervision.PATH, json={"phase": "decide"}, headers={"Authorization": "Bearer " + token})
        self.assertEqual(bad.status_code, 400)

    def test_exchanges_are_kept_once_and_read_newest_first(self):
        rid = "r-" + "b" * 24
        body = {"phase": "report", "exchanges": [
            {"request_id": rid, "event_id": "ask", "kind": "ask", "actor": "grok", "to": "claude-code",
             "at": time.time() - 120, "text": "P0 ready for review."},
            {"request_id": rid, "event_id": "0123abcd", "kind": "needs_you", "actor": "claude-code", "to": "grok",
             "at": time.time() - 60, "text": "Robert: which phone path?"}]}
        self.assertEqual(len(self.store.worker(body)["exchanges"]), 2)
        self.store.worker(body)                                      # the same entries again: kept once
        got = self.store.recent_exchanges()
        self.assertEqual([(e["kind"], e["actor"]) for e in got], [("needs_you", "claude-code"), ("ask", "grok")])
        self.assertEqual([e["kind"] for e in self.store.recent_exchanges(kinds=["needs_you"])], ["needs_you"])
        self.store.worker({"phase": "report", "stops": [
            {"report_key": "stop-grok0001", "agent": "grok", "session": "grok-sess1", "state": "done",
             "stopped_at": time.time()}]})                            # Grok's stop line fits the table now

    def test_worker_cannot_answer(self):
        for body in ({"phase": "decide"}, {"phase": "report", "decisions": []},
                     {"phase": "ack", "decisions": ["x"]}, {"phase": "pull", "extra": 1}):
            with self.assertRaises(Refused):
                self.store.worker(body)


if __name__ == "__main__":
    unittest.main()
