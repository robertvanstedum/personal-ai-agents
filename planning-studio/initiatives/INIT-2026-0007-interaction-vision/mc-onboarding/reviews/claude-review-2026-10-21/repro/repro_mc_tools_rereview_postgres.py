"""Re-review reproductions against c8b950f7 (scratch copy only)."""
import hashlib, secrets, unittest
from test_tool_path_postgres import Base, DSN, OWNER, CONV  # noqa: F401
from core.mc_tools.proposals import ToolProposals, parse
from core.mc_tools.service import ToolService
from services.mc_tools.listener import create_app


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class ReReview(Base, unittest.TestCase):
    def props(self, **kw):
        return ToolProposals(self.connect, principal=OWNER, environment="dev", **kw)

    def default_mode(self):           # as deployed: MINIMOI_MC_TOOLS_RUNTIME_ISOLATED unset
        self.service.isolated_runtime = False

    def test_T1_forged_call_gets_no_pii_and_its_proposal_needs_the_code(self):
        self.default_mode()
        token = secrets.token_urlsafe(32)
        app = create_app(self.service, token_sha256=hashlib.sha256(token.encode()).hexdigest())
        forged = "agent:mc-chat:openai-user:guild-mc:" + hashlib.sha256(f"guild:{OWNER}:{CONV}".encode()).hexdigest()[:32]
        h = {"Authorization": "Bearer " + token, "X-MC-Session": forged, "X-MC-Agent": "mc-chat"}
        body = app.test_client().post("/internal/mc/v1/tools/minimoi_guest_requests", json={"arguments": {}}, headers=h).get_json()
        r2 = app.test_client().post("/internal/mc/v1/tools/minimoi_issue_comment", headers=h,
            json={"arguments": {"number": 69, "summary": "Forged text that Robert did not ask for at all.", "refs": ["guild:1"]}}).get_json()
        posted = []
        done = self.say(self.props(comment_issue=lambda n, b: posted.append(n) or ("created", 1)), "ok", "note-next")
        print("\nT1 list leaks pii:", any(x in body["message"] for x in ("ana@", "Ana", "Bruno")), "| forged proposal:",
              r2["status"], "| next plain ok ->", done["operation"]["state"], posted)
        self.assertFalse(any(x in body["message"] for x in ("ana@", "Ana", "Bruno")))
        self.assertEqual((done["operation"]["state"], posted), ("needs_code", []))

    def test_T2_plain_ok_on_a_later_turn_no_longer_approves(self):
        self.call("minimoi_issue_comment", {"number": 69, "summary": "Text shown verbatim by the platform here.", "refs": ["guild:1"]})
        self.next_turn("note-other")
        done = self.say(self.props(comment_issue=lambda n, b: ("created", 1)), "ok", "note-ok")
        print("\nT2 later ok ->", done["operation"]["state"])
        self.assertEqual(done["operation"]["state"], "needs_code")

    def test_T4_T5_ok_after_done_and_lesson_yes(self):
        p = self.props(comment_issue=lambda n, b: ("created", 1))
        self.call("minimoi_issue_comment", {"number": 69, "summary": "Workaround noted for the record here.", "refs": ["guild:1"]})
        self.say(p, "yes", "note-yes-1")
        print("\nT4 has_pending for a later ok:", p.has_pending(CONV, "note-later"))
        self.assertFalse(p.has_pending(CONV, "note-later"))

    def test_T6_filter(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO guild.guest_requests (name,email) VALUES ('Robert Ng','rng@example.test')")
        out = [self.call("minimoi_issue_comment", {"number": 69, "summary": s, "refs": ["guild:1"]})["status"]
               for s in ("Rejected Robert's request on the backend for now.", "Rejected Аna's request on the backend today.",
                         "Rejected B r u n o's request on the backend today.")]
        print("\nT6", out)
        self.assertNotIn("needs_confirmation", out[1:])

    def test_R1_github_text_from_the_previous_turn_still_allows_a_plain_yes_when_isolated(self):
        self.call("minimoi_github_issues", {"number": 69})                 # turn N: MC reads public text
        self.next_turn("note-n1")                                          # turn N+1: no read, but the session remembers it
        self.call("minimoi_issue_comment", {"number": 69, "summary": "Steered by last turn's public comment.", "refs": []})
        posted = []
        done = self.say(self.props(comment_issue=lambda n, b: posted.append(n) or ("created", 1)), "yes", "note-yes")
        print("\nR1 isolated mode, plain yes after a previous-turn GitHub read ->", done["operation"]["state"], posted)
        self.assertEqual(posted, [69])
