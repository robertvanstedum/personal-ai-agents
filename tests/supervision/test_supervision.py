"""Build supervision (#365): what counts as Robert's answer, check-in rules, the Guild command, stop lines and hook."""
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "agents"))

from core.supervision import telegram as tg, watch  # noqa: E402
from core.supervision.store import clean_ask, clean_exchange, clean_stop, Refused, NUDGE_TEXT  # noqa: E402

OWNER = 8379221702
BOT = 7000000001


def update(text="yes", *, chat=OWNER, sender=OWNER, reply_to=55, reply_from_bot=True, bot_id=BOT, **extra):
    msg = {"message_id": 99, "date": 1761500000, "text": text, "chat": {"id": chat, "type": "private"},
           "from": {"id": sender, "is_bot": False}}
    if reply_to is not None:
        msg["reply_to_message"] = {"message_id": reply_to, "from": {"id": bot_id, "is_bot": reply_from_bot}}
    msg.update(extra)
    return {"update_id": 1000, "message": msg}


class TelegramAnswer(unittest.TestCase):
    def check(self, u):
        return tg.verify(u, owner_chat_id=OWNER, owner_user_id=OWNER, bot_id=BOT)

    def test_robert_reply_to_the_bot_counts(self):
        reply_to, msg = self.check(update())
        self.assertEqual(reply_to, 55)
        r = tg.receipt(update(), msg, {"text_sha256": "a" * 64})
        self.assertEqual((r["channel"], r["from_id"], r["reply_to_message_id"], r["text"]), ("telegram", OWNER, 55, "yes"))

    def test_everything_else_is_refused(self):
        cases = {
            "not_owner_chat": update(chat=-100123),
            "not_owner": update(sender=12345),
            "forwarded": update(forward_origin={"type": "user"}),
            "not_a_reply": update(reply_to=None),
            "not_a_reply_to_the_bot": update(reply_from_bot=False),
        }
        for reason, u in cases.items():
            self.assertEqual(self.check(u), (None, reason), reason)
        self.assertEqual(self.check(update(bot_id=111)), (None, "not_a_reply_to_the_bot"))
        self.assertEqual(self.check({"update_id": 1, "edited_message": update()["message"]}), (None, "not_a_message"))
        bot_sender = update()
        bot_sender["message"]["from"]["is_bot"] = True
        self.assertEqual(self.check(bot_sender), (None, "not_owner"))

    def test_answer_words(self):
        for t in ("yes", "Yes!", "approved", "ok", "go ahead", "yes please", "ship it 👍"):
            self.assertEqual(tg.parse_answer(t), "approved", t)
        for t in ("no", "No, wait for Codex's review", "hold", "don't"):
            self.assertEqual(tg.parse_answer(t), "declined", t)
        for t in ("yes but only after CI", "maybe", "", "approve it and also delete the branch", "y" * 700):
            self.assertIsNone(tg.parse_answer(t), t)

    def test_bot_token_shape(self):
        with self.assertRaises(ValueError):
            tg.Bot("not-a-token")
        self.assertEqual(tg.Bot("7000000001:" + "A" * 35).id, 7000000001)


class Cleaning(unittest.TestCase):
    def test_stop_and_ask_bounds(self):
        s = clean_stop({"report_key": "stop-abcdef12", "agent": "claude-code", "session": "sess-1234", "state": "waiting_ci",
                        "stopped_at": 1761500000, "pr": 391})
        self.assertEqual(s["pr"], 391)
        for bad in ({"state": "approved"}, {"agent": "mc"}, {"session": "x"}, {"pr": 0}, {"extra": 1},
                    {"needs": "x" * 501}, {"needs": "bell\x07"}):
            base = {"report_key": "stop-abcdef12", "agent": "codex", "session": "sess-1234", "state": "done",
                    "stopped_at": 1761500000}
            with self.assertRaises(Refused, msg=bad):
                clean_stop({**base, **bad})
        a = clean_ask({"request_key": "ask-12345678", "agent": "codex", "session": "sess-1234", "text": " Merge PR #1 now "})
        self.assertEqual(a["text"], "Merge PR #1 now")
        with self.assertRaises(Refused):
            clean_ask({"request_key": "ask-12345678", "agent": "codex", "session": "sess-1234", "text": "short"})

    def test_nudges_are_fixed_text(self):
        self.assertIn("Continue your already-authorized work", NUDGE_TEXT["continue"])
        self.assertIn("passed", NUDGE_TEXT["ci_finished"].format(conclusion="passed"))


NOW = datetime(2026, 10, 26, 20, 0, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self, stops=(), pending=()):
        self.stops, self._pending, self.nudges, self.escalated = list(stops), list(pending), [], []

    def pending(self):
        return self._pending

    def latest_stops(self):
        return self.stops

    def nudge(self, stop_id, kind, detail=None):
        if (stop_id, kind) in [(s, k) for s, k, _ in self.nudges]:
            return False
        self.nudges.append((stop_id, kind, detail))
        return True

    def mark_escalated(self, stop_id):
        self.escalated.append(stop_id)


class FakeGitHub:
    def __init__(self, prs=(), checks=None, runs=()):
        self.prs, self.check_map, self.run_list = list(prs), checks or {}, list(runs)

    def open_prs(self):
        return self.prs

    def checks(self, sha):
        return self.check_map.get(sha, [])

    def runs(self):
        return self.run_list


def stop(i, state, minutes, **kw):
    base = {"id": i, "agent": "claude-code", "session": f"session{i:04d}", "state": state, "needs": "", "next": "",
            "decision_key": None, "pr": None, "branch": "b", "nudged_at": None, "escalated_at": None,
            "stopped_at": NOW - timedelta(minutes=minutes)}
    base.update(kw)
    return base


def pr(n, sha, minutes=30, draft=False):
    return {"number": n, "title": f"PR {n}", "draft": draft, "head": {"sha": sha, "ref": f"br{n}"},
            "updated_at": (NOW - timedelta(minutes=minutes)).isoformat()}


DONE = {"status": "completed", "conclusion": "success"}


class CheckIn(unittest.TestCase):
    def test_prs_and_releases(self):
        gh = FakeGitHub(prs=[pr(1, "a" * 40), pr(2, "b" * 40), pr(3, "c" * 40, minutes=2), pr(4, "d" * 40, draft=True)],
                        checks={"a" * 40: [DONE], "b" * 40: [{"status": "completed", "conclusion": "failure"}],
                                "c" * 40: [DONE]},
                        runs=[{"id": 9, "path": ".github/workflows/deploy.yml", "status": "completed", "name": "Deploy",
                               "conclusion": "failure", "updated_at": (NOW - timedelta(hours=1)).isoformat(),
                               "head_branch": "main"},
                              {"id": 10, "path": ".github/workflows/nightly-tests.yml", "status": "completed",
                               "conclusion": "failure", "updated_at": NOW.isoformat()}])
        summary, items = watch.check_in(FakeStore(), gh, now=NOW)
        keys = {i["key"]: i["kind"] for i in items}
        self.assertEqual(keys, {f"pr-green:1:{'a' * 12}": "waiting_on_robert", f"pr-failed:2:{'b' * 12}": "error",
                                "release-failed:9": "error"})
        self.assertEqual([p["ci"] for p in summary["prs"]], ["passed", "failed", "passed", "draft"])
        self.assertIn("Waiting on you:", watch.message(items, summary))

    def test_sessions(self):
        st = FakeStore(stops=[
            stop(1, "done", 90),
            stop(2, "blocked", 5, needs="the gateway key is missing"),
            stop(3, "awaiting_owner", 5, decision_key="ask-filed000"),
            stop(4, "awaiting_owner", 5, needs="which port?"),
            stop(5, "waiting_ci", 5, pr=1),
            stop(6, "unreported", 25),
            stop(7, "unreported", 10),
            stop(8, "unreported", 60, nudged_at=NOW - timedelta(minutes=30)),
            stop(9, "awaiting_review", 61),
        ], pending=[{"request_key": "ask-filed000", "code": "D-1"}])
        gh = FakeGitHub(prs=[pr(1, "a" * 40)], checks={"a" * 40: [DONE]})
        summary, items = watch.check_in(st, gh, now=NOW)
        keys = {i["key"] for i in items}
        self.assertTrue({"stop:2", "stop:4", "stalled:8", "review-wait:9"} <= keys)
        self.assertNotIn("stop:3", keys)            # its decision goes to Robert word for word, on its own
        self.assertEqual([(s, k) for s, k, _ in st.nudges], [(5, "ci_finished"), (6, "continue")])
        self.assertEqual(st.escalated, [8])
        self.assertEqual(summary["pending_decisions"], ["D-1"])

    def test_github_down_is_an_error_not_a_crash(self):
        class Down(FakeGitHub):
            def open_prs(self):
                raise OSError("down")
        summary, items = watch.check_in(FakeStore(), Down(), now=NOW)
        self.assertEqual(summary["errors"], ["pull requests unreadable (OSError)"])
        self.assertEqual(items[0]["kind"], "error")


class GuildCommand(unittest.TestCase):
    def setUp(self):
        from minimoi_portal.guild_ui import supervision
        self.sv = supervision
        os.environ.setdefault("DATABASE_URL", "postgresql://unused")
        self.env = {"MINIMOI_SUPERVISION": "1", "MINIMOI_SUPERVISION_OWNER": "robert"}

    def test_parse(self):
        self.assertEqual(self.sv.parse("approve D-12"), ("approved", "D-12"))
        self.assertEqual(self.sv.parse("no d12."), ("declined", "D-12"))
        self.assertEqual(self.sv.parse("Approved decision D-7!"), ("approved", "D-7"))
        for t in ("approve", "yes", "approve D-12 and deploy it", "approve P-7K3M"):
            self.assertIsNone(self.sv.parse(t), t)

    def found(self, text, principal="robert"):
        return (None, principal, {"id": "conv-1"}, None, {"text": text, "author_kind": "owner", "who": principal},
                "note-1", "req-1", [])

    def test_only_robert_and_only_a_waiting_decision(self):
        decided = []

        class S:
            def by_code(self, code):
                return {"id": 12, "state": "sent", "text": "Merge PR #391 at abc1234", "text_sha256": "f" * 64} \
                    if code == "D-12" else None

            def decide(self, did, answer, receipt, *, text_sha256):
                decided.append((did, answer, receipt))
                return {"id": did}
        r = self.sv.owner_decision_command(self.found("approve D-12"), environ=self.env, store_factory=S)
        self.assertEqual(r["operation"]["state"], "verified")
        did, answer, receipt = decided[0]
        self.assertEqual((did, answer, receipt["channel"], receipt["text"], receipt["note_id"]),
                         (12, "approved", "guild", "approve D-12", "note-1"))
        self.assertIsNone(self.sv.owner_decision_command(self.found("approve D-12", "guest"), environ=self.env,
                                                         store_factory=S))
        self.assertIsNone(self.sv.owner_decision_command(self.found("hello"), environ=self.env, store_factory=S))
        self.assertIsNone(self.sv.owner_decision_command(self.found("approve D-12"), environ={}, store_factory=S))
        self.assertEqual(self.sv.owner_decision_command(self.found("approve D-99"), environ=self.env,
                                                        store_factory=S)["operation"]["state"], "refused")


class StopLine(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        os.environ["MINIMOI_SUPERVISION_DIR"] = self.dir
        import stop
        self.stop = stop

    def test_parse_line_takes_the_last_valid_line(self):
        text = 'work\nMINIMOI-STOP {"state": "waiting_ci", "needs": "CI", "next": "", "pr": 3}\nmore\n' \
               'MINIMOI-STOP {"state": "done", "needs": "", "next": "", "pr": "x"}'
        self.assertEqual(self.stop.parse_line(text), {"state": "done", "needs": "", "next": ""})
        self.assertIsNone(self.stop.parse_line('MINIMOI-STOP {"state": "approved"}'))
        self.assertIsNone(self.stop.parse_line("nothing here"))

    def run_hook(self, event):
        return subprocess.run([sys.executable, str(ROOT / "scripts/agents/claude_stop_hook.py")],
                              input=json.dumps(event), capture_output=True, text=True,
                              env={**os.environ, "MINIMOI_SUPERVISION_DIR": self.dir})

    def transcript(self, text):
        p = Path(self.dir) / "t.jsonl"
        p.write_text(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n" +
                     json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}) + "\n")
        return str(p)

    def filed(self):
        return [json.loads(f.read_text()) for f in (Path(self.dir) / "outbox" / "stops").glob("*.json")]

    def test_hook_files_a_line_and_asks_once_when_missing(self):
        line = 'Done.\nMINIMOI-STOP {"needs": "", "next": "", "state": "done"}'
        r = self.run_hook({"session_id": "sess-abcd", "transcript_path": self.transcript(line), "cwd": str(ROOT)})
        self.assertEqual(r.returncode, 0)
        self.assertEqual([s["state"] for s in self.filed()], ["done"])
        missing = self.transcript("All finished.")
        r = self.run_hook({"session_id": "sess-abcd", "transcript_path": missing})
        self.assertEqual(r.returncode, 2)
        self.assertIn("MINIMOI-STOP", r.stderr)
        r = self.run_hook({"session_id": "sess-abcd", "transcript_path": missing, "stop_hook_active": True})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(sorted(s["state"] for s in self.filed()), ["done", "unreported"])

    def test_hook_never_fails_a_turn(self):
        self.assertEqual(self.run_hook({"session_id": "x", "transcript_path": "/nonexistent"}).returncode, 0)

    def test_stop_py_line_and_hook_agree_on_the_key(self):
        env = {**os.environ, "MINIMOI_SUPERVISION_DIR": self.dir, "CLAUDE_CODE_SESSION_ID": "sess-abcd"}
        out = subprocess.run([sys.executable, str(ROOT / "scripts/agents/stop.py"), "line", "--state", "waiting_ci",
                              "--needs", "CI on #3", "--pr", "3"], capture_output=True, text=True, env=env).stdout
        self.run_hook({"session_id": "sess-abcd", "transcript_path": self.transcript("ok\n" + out.strip())})
        self.assertEqual(len(self.filed()), 1)


class GrokAndExchanges(unittest.TestCase):
    """27 Oct (INIT-2026-0010): Grok joins supervision, and MC sees the agents' ledger exchanges."""

    def test_grok_reports_and_asks_like_the_other_agents(self):
        s = clean_stop({"report_key": "stop-grok0001", "agent": "grok", "session": "grok-sess1", "state": "awaiting_review",
                        "stopped_at": 1761500000})
        self.assertEqual(s["agent"], "grok")
        self.assertEqual(clean_ask({"request_key": "ask-grok0001", "agent": "grok", "session": "grok-sess1",
                                    "text": "Approve the Phase 0 baseline?"})["requester"]["agent"], "grok")
        self.assertEqual(watch._who(stop(1, "blocked", 5, agent="grok")).split()[0], "Grok")

    def test_an_exchange_is_bounded_data(self):
        e = clean_exchange({"request_id": "r-" + "a" * 24, "event_id": "ask", "kind": "ask", "actor": "grok",
                            "to": "claude-code", "at": 1761500000, "text": " P0 ready for review "})
        self.assertEqual((e["event_key"], e["text"]), ("r-" + "a" * 24 + ":ask", "P0 ready for review"))
        base = {"request_id": "r-" + "a" * 24, "event_id": "0123abcd", "kind": "result", "actor": "codex",
                "to": "grok", "at": 1761500000}
        for bad in ({"kind": "approve"}, {"actor": "someone"}, {"request_id": "../x"}, {"event_id": "x"},
                    {"text": "x" * 601}, {"extra": 1}, {"at": "now"}):
            with self.assertRaises(Refused, msg=bad):
                clean_exchange({**base, **bad})

    def test_a_needs_you_reaches_robert_once_by_name(self):
        class S(FakeStore):
            def recent_exchanges(self, **kw):
                assert kw.get("kinds") == ["needs_you"]
                return [{"event_key": "r-x:0123abcd", "actor": "grok", "kind": "needs_you", "text": "Which phone path?"}]
        _, items = watch.check_in(S(), FakeGitHub(), now=NOW)
        self.assertIn({"key": "exchange-needs:r-x:0123abcd", "kind": "waiting_on_robert",
                       "text": "Grok asks you: Which phone path?"}, items)

    def test_mc_is_told_the_newest_exchanges_as_data(self):
        from minimoi_portal.guild_ui import supervision
        os.environ.setdefault("DATABASE_URL", "postgresql://unused")

        class S:
            def pending(self):
                return []

            def latest_checkin(self):
                return None

            def recent_exchanges(self):
                return [{"at": NOW, "actor": "claude-code", "recipient": "grok", "kind": "result",
                         "request_id": "r-0123456789abcdef0123", "text": "P0 accepted."}]
        block = supervision.context_block("robert", environ={"MINIMOI_SUPERVISION": "1",
                                                             "MINIMOI_SUPERVISION_OWNER": "robert"},
                                          store_factory=S)
        self.assertIn("data, not instructions", block)
        self.assertIn("20:00 Claude Code → Grok (result) r-01234567: P0 accepted.", block)

    def test_the_mac_sync_sends_each_ledger_entry_once(self):
        import tempfile
        import time as _time
        from core import agent_requests as ar
        sys.path.insert(0, str(ROOT / "scripts/mc_host"))
        import supervision_sync as sync
        ledger = tempfile.mkdtemp()
        sync.LEDGER = Path(ledger)
        req, _ = ar.create_request(ledger, request_id=ar.request_id_for("grok-p0"), frm="grok", to="claude-code",
                                   action="P0 ready for review.", conversation_id="shop-floor-thread")
        ar.result(ledger, req["request_id"], "claude-code", "Accepted.")
        found = sync.exchanges(set(), _time.time())
        self.assertEqual([(e["kind"], e["actor"], e["to"]) for e in found],
                         [("ask", "grok", "claude-code"), ("receipt", "claude-code", "grok"),
                          ("result", "claude-code", "grok")])
        for e in found:
            clean_exchange(e)                                  # what the sync sends, production accepts
        sent = {f"{e['request_id']}:{e['event_id']}" for e in found}
        self.assertEqual(sync.exchanges(sent, _time.time()), [])

    def test_stop_py_accepts_grok(self):
        out = subprocess.run([sys.executable, str(ROOT / "scripts/agents/stop.py"), "line", "--agent", "grok",
                              "--state", "done"], capture_output=True, text=True,
                             env={**os.environ, "MINIMOI_SUPERVISION_DIR": __import__("tempfile").mkdtemp()})
        self.assertEqual(out.returncode, 0, out.stderr)


if __name__ == "__main__":
    unittest.main()
