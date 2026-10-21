"""Review reproductions for the MC tool path (temporary; scratch copy only)."""
import hashlib, json, secrets, unittest
from test_tool_path_postgres import Base, DSN, OWNER, CONV, NOTE, session_key, session_user  # noqa: F401
from core.mc_tools.proposals import ToolProposals, parse
from services.mc_tools.listener import create_app


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class Repro(Base, unittest.TestCase):
    def props(self, **kw):
        return ToolProposals(self.connect, principal=OWNER, environment="dev", **kw)

    def test_T1_any_token_holder_forges_the_plugin_headers(self):
        token = secrets.token_urlsafe(32)
        app = create_app(self.service, token_sha256=hashlib.sha256(token.encode()).hexdigest())
        # Not the plugin: a process that read the token file and knows the conversation id (session key is derivable).
        forged = "agent:mc-chat:openai-user:guild-mc:" + hashlib.sha256(f"guild:{OWNER}:{CONV}".encode()).hexdigest()[:32]
        r = app.test_client().post("/internal/mc/v1/tools/minimoi_guest_requests", json={"arguments": {}},
                                   headers={"Authorization": "Bearer " + token, "X-MC-Session": forged,
                                            "X-MC-Agent": "mc-chat"})
        body = r.get_json()
        print("\nT1 forged list ->", body["status"], body["code"], "| leaks:", "ana@example.test" in body["message"])
        r2 = app.test_client().post("/internal/mc/v1/tools/minimoi_issue_comment",
            json={"arguments": {"number": 69, "summary": "Spam text approved by nobody, posted as MiniMoi.", "refs": ["guild:1"]}},
            headers={"Authorization": "Bearer " + token, "X-MC-Session": forged, "X-MC-Agent": "mc-chat"})
        print("T1 forged comment proposal ->", r2.get_json()["status"])
        self.assertEqual(body["status"], "done")
        self.assertEqual(r2.get_json()["status"], "needs_confirmation")

    def test_T2_plain_ok_approves_a_proposal_whose_text_only_the_model_relayed(self):
        r = self.call("minimoi_issue_comment", {"number": 69, "summary": "Text Robert never saw verbatim on screen.",
                                                "refs": ["guild:1"]})
        posted = []
        done = self.props(comment_issue=lambda n, b: posted.append(b) or ("created", 1)).handle(
            parse("ok"), conversation=CONV, turn_key="note-ok")
        print("\nT2 'ok' ->", done["operation"]["state"], "| footer:", posted[0].splitlines()[-1][:70])
        self.assertEqual(done["operation"]["state"], "verified")

    def test_T3_retried_yes_confirms_a_newer_proposal(self):
        posted = []
        p = self.props(comment_issue=lambda n, b: posted.append(n) or ("created", len(posted)))
        self.call("minimoi_issue_comment", {"number": 69, "summary": "First comment, approved by Robert.", "refs": ["guild:1"]})
        p.handle("confirm", conversation=CONV, turn_key="note-yes-1")
        self.call("minimoi_issue_comment", {"number": 70, "summary": "Second comment, not approved yet.", "refs": ["guild:2"]})
        retry = p.handle("confirm", conversation=CONV, turn_key="note-yes-1")     # the first yes, retried
        print("\nT3 retried yes ->", retry["operation"]["state"], "| issues commented:", posted)
        self.assertEqual(posted, [69, 70])

    def test_T4_after_any_confirm_plain_ok_and_no_are_claimed_for_30_minutes(self):
        p = self.props(comment_issue=lambda n, b: ("created", 1))
        self.call("minimoi_issue_comment", {"number": 69, "summary": "Workaround noted for the record here.", "refs": ["guild:1"]})
        p.handle("confirm", conversation=CONV, turn_key="note-yes-1")
        print("\nT4 has_pending after done:", p.has_pending(CONV),
              "| later 'ok' ->", p.handle(parse("ok"), conversation=CONV, turn_key="n2")["reply"],
              "| later 'no' ->", p.handle(parse("no"), conversation=CONV, turn_key="n3")["reply"])
        self.assertTrue(p.has_pending(CONV))

    def test_T5_yes_meant_for_a_lesson_approves_an_earlier_tool_proposal(self):
        posted = []
        self.call("minimoi_issue_comment", {"number": 69, "summary": "An earlier offer Robert moved past.", "refs": ["guild:1"]})
        lesson = self.call("minimoi_lesson_propose", {"text": "Lead with the answer, then the detail."})
        done = self.props(comment_issue=lambda n, b: posted.append(n) or ("created", 1)).handle(
            parse("yes"), conversation=CONV, turn_key="note-yes")
        print("\nT5 lesson offer:", lesson["status"], "| 'yes' ->", done["reply"], "| lessons:", self.ledger.read().active())
        self.assertEqual(posted, [69])

    def test_T6_public_text_filter_gaps(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO guild.guest_requests (name,email) VALUES ('Robert Ng','rng@example.test')")
        for summary in ("Rejected Robert's request on the backend for now.",          # 'robert' is a generic word
                        "Rejected Аna's request on the backend today.",                 # Cyrillic А in Ana
                        "Rejected B r u n o's request on the backend today."):
            r = self.call("minimoi_issue_comment", {"number": 69, "summary": summary, "refs": ["guild:1"]})
            print("T6", repr(summary[:34]), "->", r["status"])
            self.assertEqual(r["status"], "needs_confirmation")
