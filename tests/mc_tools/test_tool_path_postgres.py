"""MC's tool path on a real disposable PostgreSQL: binding to the owner's live ledger turn, every adapter,
receipts and grants, and the owner's Confirm (docs/design/mc_tool_path_design_2026-10-21.md).

Run: tests/mc_tools/run_postgres.sh <postgres bin>   (under heavy_job.py)
"""
import hashlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "minimoi_actions"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mc_domain_actions"))
from postgres_fixture_support import DSN, PostgresFixture  # noqa: E402

from core.mc_lessons import commands as lessons  # noqa: E402
from core.mc_lessons.ledger import LessonLedger  # noqa: E402
from core.mc_tools import binding  # noqa: E402
from core.mc_tools.proposals import ToolProposals, parse  # noqa: E402
from core.mc_tools.service import ToolService  # noqa: E402
from test_guest_actions_postgres import GUESTS_DDL  # noqa: E402

OWNER, CONV, NOTE = "robert", "c-tools-1", "note-ask-1"
MIGRATIONS = ("minimoi_portal/guild_ui/sql/007_mc_turn_ledger.sql",
              "core/minimoi_actions/sql/004_action_receipts.sql",
              "core/minimoi_actions/sql/005_action_grants.sql",
              "core/mc_domain_actions/sql/001_guest_action_evidence.sql",
              "core/mc_domain_actions/sql/002_guest_proposals.sql",
              "core/mc_tools/sql/001_tool_turns.sql")
HOST = {"host": "mac", "disks": [{"mount": "/System/Volumes/Data", "total_gb": 228.27, "free_gb": 7.5}],
        "memory": {"total_gb": 16, "free_gb": 1.25},
        "containers": [{"name": "minimoi-portal", "status": "Up 6 minutes"},
                       {"name": "minimoi-mc-relay", "status": "Exited (0) 2 hours ago"}]}


class FakeGitHub:
    def __init__(self):
        self.asked = []

    def issue(self, number):
        self.asked.append(("issue", number))
        return "ok", f"#{number} · issue · open · Guest approval path lost", {"number": number}

    def search(self, query):
        self.asked.append(("search", query))
        return "not_found", "GitHub has no the search.", {}


def session_user(conv=CONV, principal=OWNER):
    return "guild-mc:" + hashlib.sha256(f"guild:{principal}:{conv}".encode()).hexdigest()[:32]


def session_key(conv=CONV, agent="mc-chat"):
    return f"agent:{agent}:openai-user:{session_user(conv)}"


class Base(PostgresFixture):
    migrations = MIGRATIONS

    def setUp(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""TRUNCATE guild.mc_tool_turns, guild.mc_tool_proposals, guild.mc_tool_evidence,
                guild.mc_guest_proposals, guild.mc_guest_action_evidence, guild.mc_action_grant_consumptions,
                guild.mc_action_grants, guild.mc_action_events, guild.mc_action_receipts,
                guild.mc_turn_barrier, guild.mc_turn_ledger CASCADE""")
            cur.execute(GUESTS_DDL)
            cur.execute("INSERT INTO guild.guest_requests (name,email) VALUES "
                        "('Ana Souza','ana@example.test'),('Bruno Costa','bruno@example.test')")
        self.tmp = TemporaryDirectory()
        self.ledger = LessonLedger(Path(self.tmp.name), principal=OWNER, environment="dev")
        self.service = ToolService(self.connect, principal=OWNER, environment="dev", lessons=self.ledger,
                                   delegation=True, invites=True, tickets=True, comments=True, guest_rejects=True,
                                   github=FakeGitHub(), show=self.show, guests_lookup=lambda email: None)
        self.shown, self.turns = [], {NOTE}
        self.ledger_id = self.claim(NOTE)
        self.bind(self.ledger_id, NOTE)

    def show(self, conversation_id, note_key, text):
        self.shown.append((conversation_id, note_key, text))
        return True

    def next_turn(self, note):
        """Robert's next message in this conversation: a new claimed ledger turn, bound as the portal would."""
        self.ledger_id = self.claim(note)
        self.bind(self.ledger_id, note)
        self.turns.add(note)

    def say(self, proposals, text, turn):
        """Robert types ``text`` as the note ``turn`` (his next turn, unless that note was already sent)."""
        if turn not in self.turns:
            self.next_turn(turn)
        kind, code = parse(text)
        return proposals.handle(kind, conversation=CONV, turn_key=turn, code=code)

    def tearDown(self):
        self.tmp.cleanup()

    # ── the owner's turn, as the portal records it ──
    def claim(self, note, conv=CONV, principal=OWNER):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""UPDATE guild.mc_turn_ledger SET state='answered', claim_token=NULL, lease_until=NULL
                WHERE principal=%s AND state='claimed'""", (principal,))
            cur.execute("""INSERT INTO guild.mc_turn_ledger (principal, conversation_id, note_request_id, turn_request_id,
                kind, state, claim_token, claim_generation, lease_until)
                VALUES (%s,%s,%s,%s,'typed','claimed','tok',1, now() + interval '120 seconds') RETURNING id""",
                (principal, conv, note, "note-" + note))
            return cur.fetchone()[0]

    def bind(self, ledger_id, note, conv=CONV, principal=OWNER, generation=1):
        with self.connect() as conn:
            return binding.record(conn, ledger_id=ledger_id, claim_generation=generation, principal=principal,
                                  conversation_id=conv, note_request_id=note, session_user=session_user(conv, principal))

    def finish_turn(self, ledger_id):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""UPDATE guild.mc_turn_ledger SET state='answered', claim_token=NULL, lease_until=NULL
                WHERE id=%s""", (ledger_id,))

    def call(self, name, args=None, *, key=None, agent="mc-chat", observation=None):
        return self.service.call(name, {} if args is None else args, session_key=session_key() if key is None else key,
                                 agent_id=agent, observation=observation)

    def one(self, sql, params=()):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def receipts(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT capability, state, turn_id FROM guild.mc_action_receipts ORDER BY created_at")
            return cur.fetchall()


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class BindingTests(Base, unittest.TestCase):
    def test_live_claim_binds_and_names_the_owner_turn(self):
        with self.connect() as conn:
            b = binding.resolve(conn, session_key=session_key(), agent_id="mc-chat", allowed_agents={"mc-chat"},
                                principal=OWNER, environment="dev")
        self.assertEqual((b.principal, b.conversation_id, b.note_request_id, b.turn_id),
                         (OWNER, CONV, NOTE, f"ledger:{self.ledger_id}:1"))

    def test_finished_turn_refuses_every_later_call(self):
        self.assertEqual(self.call("minimoi_guest_requests")["status"], "done")
        self.finish_turn(self.ledger_id)
        r = self.call("minimoi_guest_requests")
        self.assertEqual((r["status"], r["code"]), ("refused", "tool_turn_not_live"))

    def test_lapsed_lease_refuses(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE guild.mc_turn_ledger SET lease_until = now() - interval '1 second' WHERE id=%s",
                        (self.ledger_id,))
        self.assertEqual(self.call("minimoi_guest_requests")["code"], "tool_turn_not_live")

    def test_reclaimed_generation_does_not_inherit_the_old_binding(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE guild.mc_turn_ledger SET claim_generation=2 WHERE id=%s", (self.ledger_id,))
        self.assertEqual(self.call("minimoi_guest_requests")["code"], "tool_turn_not_live")

    def test_session_and_agent_come_from_the_host_and_must_match(self):
        for key, agent in ((session_key("c-other"), "mc-chat"),             # another conversation's session
                           (session_key(agent="main"), "main"),             # an agent that is not MC
                           (session_key(), "mc-agent"),                     # key and agent disagree
                           ("agent:mc-chat:main", "mc-chat"),
                           (session_key().upper(), "mc-chat"), ("", "mc-chat")):
            r = self.call("minimoi_guest_requests", key=key, agent=agent)
            self.assertEqual(r["status"], "refused", (key, agent))

    def test_record_refuses_a_claim_that_is_not_live(self):
        self.finish_turn(self.ledger_id)
        self.assertFalse(self.bind(self.ledger_id, NOTE))
        other = self.claim("note-2")
        self.assertFalse(self.bind(other, "note-WRONG"))                     # must match the ledger's own note
        self.assertTrue(self.bind(other, "note-2"))
        self.assertTrue(self.bind(other, "note-2"))                           # an exact repeat is harmless

    def test_another_owners_live_turn_is_not_ours(self):
        self.finish_turn(self.ledger_id)
        ledger_id = self.claim("note-x", principal="someone")
        self.bind(ledger_id, "note-x", principal="someone")
        self.assertEqual(self.call("minimoi_guest_requests")["code"], "tool_turn_not_live")


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class ToolTests(Base, unittest.TestCase):
    def test_arguments_carry_no_authority(self):
        for args in ({"refs": ["guild:1"], "change": "reject", "principal": "robert"},
                     {"refs": ["guild:1"], "change": "reject", "approved": True}, {"refs": ["1"], "change": "reject"},
                     {"refs": ["guild:1 "], "change": "reject"}, {}, {"refs": "guild:1", "change": "reject"},
                     {"refs": ["Ana Souza"], "change": "reject"}, {"refs": ["guild:1"], "change": "drop table"}):
            r = self.call("minimoi_data_repair_ticket", args)
            self.assertEqual(r["status"], "refused", args)
        self.assertEqual(self.call("minimoi_nope")["code"], "tool_unknown")
        self.assertEqual(self.receipts(), [])
        self.assertIsNone(self.one("SELECT 1 FROM guild.mc_tool_proposals"))

    def test_host_health_reports_the_measured_host_only(self):
        r = self.call("minimoi_host_health", observation=HOST)
        self.assertEqual(r["status"], "done")
        self.assertIn("7.5 GB free of 228.3 GB", r["message"])
        self.assertIn("1 running, 1 stopped", r["message"])
        self.assertEqual(self.call("minimoi_host_health")["status"], "refused")       # no observation
        bad = dict(HOST, disks=[{"mount": "/", "total_gb": 1, "free_gb": 5}])
        self.assertEqual(self.call("minimoi_host_health", observation=bad)["code"], "host_observation_invalid")
        self.assertEqual(self.call("minimoi_guest_requests", observation=HOST)["status"], "refused")
        self.finish_turn(self.ledger_id)
        self.assertEqual(self.call("minimoi_host_health", observation=HOST)["code"], "tool_turn_not_live")

    def test_guest_list_then_a_ticket_only_after_the_owners_yes(self):
        listed = self.call("minimoi_guest_requests")
        self.assertEqual(listed["code"], "listed")
        refs = [i["ref"] for i in listed["data"]["requests"]]
        r = self.call("minimoi_data_repair_ticket", {"refs": list(reversed(refs)), "change": "reject"})
        self.assertEqual((r["status"], r["code"]), ("needs_confirmation", "proposed"))
        self.assertIn("guild:1 and guild:2", self.shown[-1][2])
        filed = []
        proposals = ToolProposals(self.connect, principal=OWNER, environment="dev",
                                  file_issue=lambda title, body: filed.append((title, body)) or ("created", 123))
        self.assertEqual(filed, [])                                                   # nothing before the yes
        self.assertEqual(parse("yes"), ("confirm", None))
        done = self.say(proposals, "Yes.", "note-yes")
        self.assertEqual((done["operation"]["state"], done["operation"]["issue"]), ("verified", 123))
        self.assertTrue(done["reply"].startswith("Ticket 123 created"))
        (title, body), = filed
        self.assertIn("guild:1, guild:2", title)
        for private in ("Ana", "Bruno", "example.test", "@"):                     # the repository is public
            self.assertNotIn(private, title + body)
        self.assertEqual([x[:2] for x in self.receipts()],
                         [("issue.propose_data_repair", "verified"), ("issue.create_data_repair", "verified")])
        self.assertEqual(self.one("SELECT status FROM guild.guest_requests WHERE id=1")[0], "requested")
        again = self.say(proposals, "yes", "note-yes")    # the same yes retried
        self.assertTrue(again["operation"].get("repeated"))
        self.assertEqual(len(filed), 1)

    def test_guest_reject_after_the_owners_yes_is_verified_by_read_back(self):
        r = self.call("minimoi_guest_request_reject", {"ref": "guild:2"})
        self.assertEqual((r["status"], r["code"]), ("needs_confirmation", "proposed"))
        self.assertIn("Bruno Costa", self.shown[-1][2])                             # private conversation, not public
        self.assertEqual(self.one("SELECT status FROM guild.guest_requests WHERE id=2")[0], "requested")
        proposals = ToolProposals(self.connect, principal=OWNER, environment="dev", guest_rejects=True)
        done = self.say(proposals, "yes", "note-yes")
        self.assertEqual((done["operation"]["state"], done["operation"]["outcome"]), ("verified", "rejected"))
        self.assertIn("verified by read-back", done["reply"])
        self.assertEqual(self.one("SELECT status FROM guild.guest_requests WHERE id=2")[0], "rejected")
        self.assertEqual([x[:2] for x in self.receipts()],
                         [("guest.propose_reject", "verified"), ("guest.reject_request", "verified")])
        again = self.say(proposals, "yes", "note-yes")
        self.assertTrue(again["operation"].get("repeated"))

    def test_guest_reject_does_nothing_when_the_request_changed(self):
        self.call("minimoi_guest_request_reject", {"ref": "guild:1"})
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE guild.guest_requests SET status='granted' WHERE id=1")
        done = self.say(ToolProposals(self.connect, principal=OWNER, environment="dev", guest_rejects=True), "yes", "n2")
        self.assertEqual(done["operation"]["state"], "stale")
        self.assertEqual(self.one("SELECT status FROM guild.guest_requests WHERE id=1")[0], "granted")
        self.assertEqual(self.one("SELECT count(*) FROM guild.mc_action_receipts WHERE capability='guest.reject_request'")[0], 0)
        self.assertEqual(self.call("minimoi_guest_request_reject", {"ref": "guild:999"})["code"], "no_open_request")

    def test_github_read_is_bound_and_read_only(self):
        r = self.call("minimoi_github_issues", {"number": 69})
        self.assertEqual((r["status"], r["data"]["number"]), ("done", 69))
        self.assertEqual(self.call("minimoi_github_issues", {"number": 69, "query": "guest"})["status"], "refused")
        self.assertEqual(self.call("minimoi_github_issues", {})["status"], "refused")
        self.finish_turn(self.ledger_id)
        self.assertEqual(self.call("minimoi_github_issues", {"query": "guest approval"})["code"], "tool_turn_not_live")
        self.assertEqual(self.service.github.asked, [("issue", 69)])
        self.assertEqual(self.receipts(), [])

    def test_issue_comment_shows_the_exact_public_text_and_posts_only_after_yes(self):
        for summary in ("Workaround: Bruno can be rejected on the backend.", "Mail ana@example.test about it."):
            r = self.call("minimoi_issue_comment", {"number": 69, "summary": summary, "refs": ["guild:2"]})
            self.assertEqual(r["status"], "refused", summary)
        summary = "Workaround until the approval path is rebuilt: MC rejects stuck guest requests on the backend."
        r = self.call("minimoi_issue_comment", {"number": 69, "summary": summary, "refs": ["guild:2", "guild:1"]})
        self.assertEqual(r["status"], "needs_confirmation")
        self.assertIn(summary, self.shown[-1][2])
        self.assertIn("guild:1, guild:2", self.shown[-1][2])
        posted = []
        proposals = ToolProposals(self.connect, principal=OWNER, environment="dev",
                                  comment_issue=lambda n, body: posted.append((n, body)) or ("created", 9001))
        done = self.say(proposals, "Yes, please", "note-yes")
        self.assertEqual((done["operation"]["state"], done["reply"]), ("verified", "Comment added to issue #69."))
        (number, body), = posted
        self.assertEqual(number, 69)
        self.assertTrue(body.startswith(summary))
        for private in ("Ana", "Bruno", "example.test", "@"):
            self.assertNotIn(private, body)
        self.assertEqual([x[:2] for x in self.receipts()],
                         [("issue.propose_comment", "verified"), ("issue.comment", "verified")])

    def test_ticket_for_an_unknown_ref_or_a_refused_filing(self):
        r = self.call("minimoi_data_repair_ticket", {"refs": ["guild:999"], "change": "reject"})
        self.assertEqual((r["status"], r["code"]), ("refused", "unknown_ref"))
        self.assertEqual(self.receipts(), [])
        self.call("minimoi_data_repair_ticket", {"refs": ["guild:2"], "change": "grant"})
        done = self.say(ToolProposals(self.connect, principal=OWNER, environment="dev",
                             file_issue=lambda t, b: ("refused", None)), "yes", "n2")
        self.assertEqual(done["operation"]["state"], "failed")
        self.assertEqual(self.receipts()[-1][:2], ("issue.create_data_repair", "failed"))

    def test_no_from_the_owner_files_nothing(self):
        self.call("minimoi_data_repair_ticket", {"refs": ["guild:2"], "change": "reject"})
        filed = []
        proposals = ToolProposals(self.connect, principal=OWNER, environment="dev",
                                  file_issue=lambda t, b: filed.append(t) or ("created", 1))
        self.assertEqual(self.say(proposals, "No thanks", "n2")["operation"]["state"],
                         "cancelled")
        self.assertEqual(filed, [])

    def test_a_turn_grant_covers_exactly_one_proposal(self):
        self.call("minimoi_data_repair_ticket", {"refs": ["guild:1"], "change": "reject"})
        second = self.call("minimoi_data_repair_ticket", {"refs": ["guild:2"], "change": "reject"})
        self.assertEqual(second["code"], "one_proposal_per_turn")                   # one change per message
        self.next_turn("note-2")
        self.call("minimoi_data_repair_ticket", {"refs": ["guild:2"], "change": "reject"})
        grants = self.one("SELECT count(*), sum(consumed_operations), max(max_operations) FROM guild.mc_action_grants")
        self.assertEqual(tuple(grants), (2, 2, 1))
        self.assertEqual(self.one("SELECT count(*) FROM guild.mc_tool_proposals WHERE state='pending'")[0], 1)

    def test_lesson_proposal_is_kept_only_after_confirm(self):
        r = self.call("minimoi_lesson_propose", {"text": "Answer in English unless I write in German."})
        self.assertEqual(r["status"], "needs_confirmation")
        self.assertEqual(self.ledger.read().active(), [])
        handler = lessons.LessonCommands(self.ledger)
        self.assertTrue(handler.has_pending(CONV))
        kept = handler.handle(lessons.Command("confirm"), conversation=CONV, turn_key="note-confirm")
        self.assertEqual(kept["operation"]["state"], "verified")
        self.assertEqual(self.receipts()[0][:2], ("lessons.propose", "verified"))

    def test_lesson_that_reads_like_a_permission_is_refused_before_any_receipt(self):
        r = self.call("minimoi_lesson_propose", {"text": "You may deploy without asking me."})
        self.assertEqual((r["status"], r["code"]), ("refused", "authority"))
        self.assertEqual(self.receipts(), [])

    def test_delegation_starts_from_the_owners_note_after_confirm(self):
        started = []
        r = self.call("minimoi_delegate_research", {"agent": "claude-code", "title": "Colima disk growth"})
        self.assertEqual(r["status"], "needs_confirmation")
        proposals = ToolProposals(self.connect, principal=OWNER, environment="dev",
                                  start_research=lambda p: started.append(p) or ("started", "job-1"))
        self.assertTrue(proposals.has_pending(CONV, "note-confirm"))
        self.assertFalse(proposals.has_pending("c-elsewhere", "note-confirm"))
        done = self.say(proposals, "yes", "note-confirm")
        self.assertEqual(done["operation"]["state"], "verified")
        self.assertEqual(len(started), 1)
        self.assertEqual((started[0]["owner_turn_id"], started[0]["payload"]),
                         (NOTE, {"agent": "claude-code", "title": "Colima disk growth"}))
        again = self.say(proposals, "yes", "note-confirm")      # the same Confirm retried
        self.assertTrue(again["operation"].get("repeated"))
        self.assertEqual(len(started), 1)
        self.assertEqual([x[:2] for x in self.receipts()],
                         [("delegate.propose_research", "verified"), ("delegate.start_research", "verified")])
        grant = self.one("SELECT definition->>'proposal_id', consumed_operations FROM guild.mc_action_grants "
                         "WHERE definition->>'capability'='delegate.start_research'")
        self.assertIsNotNone(grant[0])
        self.assertEqual(grant[1], 1)

    def test_cancel_and_expiry_do_nothing(self):
        self.call("minimoi_delegate_research", {"agent": "codex", "title": "Look at the relay"})
        started = []
        proposals = ToolProposals(self.connect, principal=OWNER, environment="dev",
                                  start_research=lambda p: started.append(p) or ("started", "job-1"))
        self.assertEqual(self.say(proposals, "no", "n2")["operation"]["state"], "cancelled")
        self.assertEqual(self.say(proposals, "yes", "n3")["operation"]["state"],
                         "nothing_pending")
        self.call("minimoi_delegate_research", {"agent": "codex", "title": "Look at the relay"})
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE guild.mc_tool_proposals SET expires_at = now() - interval '1 second' WHERE state='pending'")
        self.assertEqual(self.say(proposals, "yes", "n4")["operation"]["state"], "expired")
        self.assertEqual(started, [])

    def test_unknown_start_is_recorded_unknown_and_not_retried(self):
        self.call("minimoi_delegate_research", {"agent": "codex", "title": "Look at the relay"})

        def boom(p):
            raise RuntimeError("relay down")
        proposals = ToolProposals(self.connect, principal=OWNER, environment="dev", start_research=boom)
        self.assertEqual(self.say(proposals, "yes", "n2")["operation"]["state"], "unknown")
        self.assertEqual(self.receipts()[-1][:2], ("delegate.start_research", "unknown"))

    def test_invite_creates_the_login_only_after_confirm_and_keeps_the_password_out_of_replies(self):
        r = self.call("minimoi_access_invite", {"name": "Sofia", "email": "Sofia@Example.test", "days": 30})
        self.assertEqual(r["status"], "needs_confirmation")
        self.assertIn("sofia@example.test", self.shown[-1][2])
        self.assertIsNone(self.one("SELECT 1 FROM guild.guest_requests WHERE email='sofia@example.test'"))
        delivered, calls = [], []

        def invite(p, ref):
            calls.append(ref)
            return "done", {"username": "guest_abcd1234"}, lambda: delivered.append(ref) or True
        proposals = ToolProposals(self.connect, principal=OWNER, environment="dev", invite=invite)
        done = self.say(proposals, "yes", "note-confirm")
        self.assertEqual(done["operation"]["state"], "verified")
        row = self.one("SELECT id, status, domain FROM guild.guest_requests WHERE email='sofia@example.test'")
        self.assertEqual((calls, row[2]), ([f"guild:{row[0]}"], "portuguese"))
        self.assertEqual(delivered, calls)
        self.assertIn("guest_abcd1234", done["reply"])

    def test_a_confirm_for_a_switched_off_kind_changes_nothing(self):
        self.call("minimoi_access_invite", {"name": "Sofia", "email": "sofia@example.test", "days": 30})
        done = self.say(ToolProposals(self.connect, principal=OWNER, environment="dev"), "yes", "note-confirm")
        self.assertEqual((done["operation"]["state"], done["operation"]["code"]), ("refused", "kind_off"))
        self.assertIsNone(self.one("SELECT 1 FROM guild.guest_requests WHERE email='sofia@example.test'"))
        self.assertIsNone(self.one("SELECT 1 FROM guild.mc_action_grants WHERE definition->>'capability'='access.invite_user'"))

    def test_invite_for_an_existing_login_asks_instead(self):
        self.service.guests_lookup = lambda email: "guest_0001"
        r = self.call("minimoi_access_invite", {"name": "Ana", "email": "ana@example.test", "days": 7})
        self.assertEqual((r["status"], r["code"]), ("needs_clarification", "login_exists"))
        self.assertEqual(self.receipts(), [])

    def test_invite_through_the_real_grant_state_machine(self):
        import os
        os.environ.setdefault("DATABASE_URL", DSN)
        from minimoi_portal import auth, guest_requests as gr
        from minimoi_portal.guild_ui.mc_tools_wiring import invite_runner
        saved = auth.AUTH_DIR
        auth.AUTH_DIR = Path(self.tmp.name)
        try:
            self.call("minimoi_access_invite", {"name": "Sofia", "email": "sofia@example.test", "days": 30})
            mails, notices = [], []
            runner = invite_runner(lambda guest, temp_password: mails.append((guest, temp_password)) or True,
                                   lambda guest, email_sent, temp_password: notices.append(email_sent),
                                   db=gr.PostgresStore(DSN))
            done = self.say(ToolProposals(self.connect, principal=OWNER, environment="dev", invite=runner), "yes", "note-confirm")
            self.assertEqual(done["operation"]["state"], "verified", done)
            (guest, secret), = mails
            self.assertTrue(secret and secret not in done["reply"])
            self.assertEqual(notices, [True])
            self.assertEqual(self.one("SELECT status FROM guild.guest_requests WHERE email='sofia@example.test'")[0],
                             "granted")
            account = auth.guest_entry(guest["username"])
            self.assertTrue(account["must_change_password"])
        finally:
            auth.AUTH_DIR = saved


if __name__ == "__main__":
    unittest.main()
