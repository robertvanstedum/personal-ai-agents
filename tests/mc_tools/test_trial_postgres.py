"""M1.4 A24 one-offer measurement from the records (spec v1.2 §16.4). Run with tests/mc_tools/run_postgres.sh."""
import unittest
from datetime import datetime, timedelta, timezone

from test_tool_path_postgres import Base, DSN, OWNER  # noqa: F401
from core.mc_tools import trial


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class TrialTests(Base, unittest.TestCase):
    def test_a24_counts_distinct_blocked_owner_requests_and_never_a_retry_twice(self):
        since = datetime.now(timezone.utc) - timedelta(minutes=1)
        first = self.call("minimoi_issue_comment", {"number": 69, "summary": "The first change Robert asked for.", "refs": []})
        self.assertEqual(first["status"], "needs_confirmation")
        self.next_turn("note-2")
        args = {"number": 70, "summary": "An unrelated change asked next.", "refs": []}
        self.assertEqual(self.call("minimoi_issue_comment", args, call_id="call_blocked")["code"], "offer_pending")
        self.assertEqual(self.call("minimoi_issue_comment", args, call_id="call_blocked")["code"], "offer_pending")
        self.next_turn("note-3")
        self.call("minimoi_guest_requests")                                         # a read: not an action request
        with self.connect() as conn:
            report = trial.one_offer_report(conn, principal=OWNER, since=since)
        self.assertEqual((report["action_requests"], report["blocked_by_pending_offer"]), (2, 1))
        self.assertEqual([(b["note"], b["tool"], b["call"]) for b in report["blocked"]],
                         [("note-2", "minimoi_issue_comment", "call_blocked")])
        codes = self.one("SELECT array_agg(result_code ORDER BY admitted_at) FROM guild.mc_tool_admissions")[0]
        self.assertEqual(codes, ["proposed", "offer_pending", "listed"])

    def test_a24_zero_is_reported_explicitly(self):
        with self.connect() as conn:
            report = trial.one_offer_report(conn, principal=OWNER, since=datetime.now(timezone.utc))
        self.assertEqual((report["action_requests"], report["blocked_by_pending_offer"], report["blocked"]), (0, 0, []))


if __name__ == "__main__":
    unittest.main()
