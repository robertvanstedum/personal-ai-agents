"""The action-waiting notices against a real PostgreSQL (opt-in: MC_BINDING_TEST_DSN, a disposable local mc12_test_*
database, as tests/minimoi_actions/postgres_fixture_support.py requires; skipped without it).

What the fakes cannot show: the new guild select (open_with_coverage) and its 5 s timeouts, the portal's
RETURNING insert on the verify path (review B1), the read-only decisions reader, the D-code source end to end, and a
whole sweep over real rows. Telegram is still a fake opener; nothing leaves the machine."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "minimoi_actions"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from postgres_fixture_support import DSN, PostgresFixture  # noqa: E402

from aw_fakes import BASE, CHAT, FAKE_TOKEN, FakeTelegram  # noqa: E402

# The table as production has it: the guest-access spec's DDL plus migration 002's domain column and status CHECK
# (the same shape tests/test_guest_requests_postgres.py builds).
GUEST_DDL = """
DROP TABLE IF EXISTS guild.guest_requests;
CREATE TABLE guild.guest_requests (
  id SERIAL PRIMARY KEY, name TEXT, email TEXT NOT NULL UNIQUE, reason TEXT,
  domain VARCHAR(50) DEFAULT 'portuguese',
  requested_at TIMESTAMPTZ DEFAULT NOW(), status TEXT DEFAULT 'requested'
    CHECK (status IN ('requested','pending','granted','rejected','approved_pending_email')),
  actioned_at TIMESTAMPTZ, last_nudged_at TIMESTAMPTZ);
"""


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class NoticesOnPostgres(PostgresFixture, unittest.TestCase):
    migrations = ("core/supervision/sql/001_supervision.sql", "core/supervision/sql/002_grok_and_exchanges.sql")

    def setUp(self):
        from minimoi_portal import auth
        with self.guard.cursor() as cur:
            cur.execute(GUEST_DDL)
            cur.execute("TRUNCATE guild.supervision_decisions RESTART IDENTITY CASCADE")
        self.guard.commit()
        self.tmp = Path(tempfile.mkdtemp(prefix="aw-pg-"))
        (self.tmp / "auth").mkdir()
        (self.tmp / "auth" / "pending.json").write_text(json.dumps({"pending": []}))
        token = self.tmp / "bot-token"
        token.write_text(FAKE_TOKEN)
        os.chmod(token, 0o600)
        self.token_file = token
        for patcher in (mock.patch.object(auth, "AUTH_DIR", self.tmp / "auth"),
                        mock.patch.dict(os.environ, {"DATABASE_URL": DSN})):   # the throwaway database only
            patcher.start()
            self.addCleanup(patcher.stop)
        self.offset = 0.0

    # ---- helpers ------------------------------------------------------------------------------------------------
    def sql(self, statement, params=()):
        with self.guard.cursor() as cur:
            cur.execute(statement, params)
            rows = cur.fetchall() if cur.description else None
        self.guard.commit()
        return rows

    def guest(self, email, *, status="requested", reason="", domain="portuguese", name="Ann Guest"):
        return self.sql("INSERT INTO guild.guest_requests (name, email, reason, domain, status) VALUES (%s,%s,%s,%s,%s)"
                        " RETURNING id", (name, email, reason, domain, status))[0][0]

    def service(self, *, supervision=False):
        from core.supervision.telegram import Bot
        from minimoi_portal import guest_requests as gr
        from minimoi_portal.action_waiting import sources as src, store as st
        from minimoi_portal.action_waiting.service import Service
        from minimoi_portal.action_waiting.settings import Settings
        settings = Settings(mode="on", env="dev", root=self.tmp / "notices", token_file=str(self.token_file),
                            chat_id=CHAT, owner="robert", base_url=BASE, supervision=supervision)
        clock = lambda: time.time() + self.offset                                                   # noqa: E731
        store = st.Store(settings.root, clock=clock)
        sources = src.Sources(settings, store.digest, guild_db=gr.PostgresStore(DSN, timeouts=True), clock=clock)
        self.telegram = FakeTelegram()
        svc = Service(settings, store=store, sources=sources, opener=self.telegram, clock=clock,
                      legacy_bot=lambda: Bot(FAKE_TOKEN, opener=FakeTelegram()), source_timeout=10)
        self.addCleanup(svc.sender.release)
        return svc

    def open_items(self, svc):
        from minimoi_portal.action_waiting import store as st
        return st.open_items(svc.store.read())

    # ---- the guest store ---------------------------------------------------------------------------------------
    def test_open_with_coverage_reads_the_real_table_with_timeouts_and_no_names(self):
        from minimoi_portal import guest_requests as gr
        from minimoi_portal.action_waiting.sources import INVITE_REASON
        a = self.guest("ann@x.test")
        self.guest("gina@x.test", status="granted")
        self.guest("rex@x.test", status="rejected")
        inv = self.guest("inv@x.test", reason=INVITE_REASON, domain="german")
        db = gr.PostgresStore(DSN, timeouts=True)
        out = gr.open_with_coverage(db)
        self.assertEqual(out["pending"], [])
        rows = {r["id"]: r for r in out["guild"]}
        self.assertEqual(set(rows), {a, inv})
        self.assertEqual(set(rows[a]), {"id", "email", "domain", "status", "requested_at", "reason"})
        self.assertEqual(rows[inv]["reason"], INVITE_REASON)
        self.assertIsInstance(rows[a]["requested_at"], datetime)
        self.assertEqual(db.query("SHOW statement_timeout"), [{"statement_timeout": "5s"}])
        self.assertEqual(gr.PostgresStore(DSN).query("SHOW statement_timeout")[0]["statement_timeout"], "0")

    def test_an_unreachable_database_is_unavailable_never_empty(self):
        from minimoi_portal import guest_requests as gr
        dead = gr.PostgresStore("host=127.0.0.1 port=1 dbname=mc12_test_none user=postgres", timeouts=True)
        self.assertIsNone(gr.open_with_coverage(dead)["guild"])

    def test_the_verify_paths_insert_returns_a_row_only_for_a_new_or_reopened_request(self):
        """Review B1, on the real table: RETURNING gives a row for a new email or a re-opened rejected one, and
        nothing for an email whose row is already requested or granted (then the direct notice must go)."""
        import minimoi_portal.app as app_mod
        first = app_mod._log_guest_request("Ann Guest", "ann@x.test", "learning", domain="portuguese")
        self.assertEqual(set(first), {"id", "requested_at"})
        self.assertIsNone(app_mod._log_guest_request("Ann Guest", "ann@x.test", "again"))           # still requested
        self.sql("UPDATE guild.guest_requests SET status='granted' WHERE id=%s", (first["id"],))
        self.assertIsNone(app_mod._log_guest_request("Ann Guest", "ann@x.test", "again"))           # the case Robert hit
        self.sql("UPDATE guild.guest_requests SET status='rejected', requested_at=NOW() - interval '1 day'"
                 " WHERE id=%s", (first["id"],))
        reopened = app_mod._log_guest_request("Ann Guest", "ann@x.test", "please", domain="portuguese")
        self.assertEqual(reopened["id"], first["id"])
        self.assertGreaterEqual(reopened["requested_at"], first["requested_at"])                    # a new version
        self.assertEqual(self.sql("SELECT status FROM guild.guest_requests WHERE id=%s", (first["id"],)),
                         [("requested",)])

    def test_a_sweep_over_real_rows_tells_once_resolves_on_answer_and_tells_a_rerequest(self):
        from minimoi_portal import guest_requests as gr
        from minimoi_portal.action_waiting.sources import INVITE_REASON
        rid = self.guest("ann@x.test")
        self.guest("inv@x.test", reason=INVITE_REASON)                       # Robert's own invite: not waiting
        svc = self.service()
        svc.sweep()
        svc.sweep()
        [item] = self.open_items(svc)
        self.assertEqual((item["code"], item["app"], item["delivery"]["state"]), ("W-1", "Meu Português", "told"))
        self.assertEqual(len(self.telegram.sent), 1)
        blob = (svc.store.root / "store.json").read_text()
        self.assertNotIn("ann@x.test", blob)
        self.assertNotIn("Ann Guest", blob)
        self.assertEqual(gr.reject(f"guild:{rid}", gr.PostgresStore(DSN)).status, "done")          # answered on Operate
        svc.sweep()
        self.assertEqual(self.open_items(svc), [])
        self.assertEqual(len(self.telegram.sent), 1)
        self.sql("UPDATE guild.guest_requests SET status='requested', requested_at=NOW() + interval '1 second'"
                 " WHERE id=%s", (rid,))                                     # a re-request after the rejection
        svc.sweep()
        [again] = self.open_items(svc)
        self.assertEqual((again["code"], again["generation"]), ("W-2", 2))
        self.assertEqual(len(self.telegram.sent), 2)

    # ---- the decisions table (D-codes) ---------------------------------------------------------------------------
    def ask(self, key="ask-0000000001", text="Merge PR #391 at abc1234 to main; token ghp_ABCDEFGHIJKLMNOPQRSTUV"):
        from core.supervision.store import Store
        out = Store(self.connect).worker({"phase": "report", "asks": [
            {"request_key": key, "agent": "codex", "session": "sess-abcd", "branch": "codex/x", "text": text}]})
        return out["asks"][0]["code"]

    def test_the_decisions_source_reads_real_rows(self):
        from core.supervision.store import Store
        code = self.ask()
        self.assertEqual(code, "D-1")
        svc = self.service(supervision=True)
        snap = svc.sources.collect()
        self.assertEqual(snap.coverage["decisions"], "ok")
        [obs] = [o for o in snap.obs if o.kind == "decision"]
        self.assertEqual((obs.key, obs.version, obs.state), ("decision:D-1", "decision:1", "new"))
        self.assertNotIn("ghp_", obs.fields["words"])
        self.assertEqual(obs.fields["facts"]["agent"], "codex")
        self.assertEqual(obs.fields["facts"]["branch"], "codex/x")
        self.assertEqual(svc.sources.decision_state(1), "new")
        Store(self.connect).mark_sent(1, 77)                                 # the supervisor sent it itself
        self.assertEqual(svc.sources.decision_state(1), "sent")
        svc.sweep()
        [item] = self.open_items(svc)
        self.assertEqual(item["delivery"]["state"], "by_supervisor")
        self.assertEqual(self.telegram.sent, [])
        self.sql("UPDATE guild.supervision_decisions SET state='expired' WHERE id=1")
        svc.sweep()
        self.assertEqual(self.open_items(svc), [])

    def test_a_new_d_code_is_told_after_the_grace_and_expired_rows_are_not_read(self):
        self.ask()
        self.ask(key="ask-0000000002", text="This one already expired long ago")
        self.sql("UPDATE guild.supervision_decisions SET expires_at = clock_timestamp() - interval '1 minute'"
                 " WHERE id=2")
        svc = self.service(supervision=True)
        svc.sweep()
        self.assertEqual(self.telegram.sent, [])                            # inside the 180 s grace
        self.offset = 181
        svc.sweep()
        svc.sweep()
        self.assertEqual(len(self.telegram.sent), 1)
        text = self.telegram.texts[0]
        self.assertIn("Decision D-1 from Codex · answer in MC: approve D-1 / no D-1", text)
        self.assertNotIn("D-2", text)
        self.assertEqual([i["facts"]["decision"] for i in self.open_items(svc)], ["D-1"])

    def test_the_decisions_reader_cannot_write(self):
        import psycopg2
        from minimoi_portal.action_waiting.sources import Sources
        self.ask()
        conn = Sources._connect()
        try:
            with conn.cursor() as cur:
                with self.assertRaises(psycopg2.errors.ReadOnlySqlTransaction):
                    cur.execute("UPDATE guild.supervision_decisions SET state='expired'")
        finally:
            conn.close()
        self.assertEqual(self.sql("SELECT state FROM guild.supervision_decisions"), [("new",)])

    def test_an_absent_decisions_table_is_skipped(self):
        svc = self.service(supervision=True)
        self.sql("ALTER TABLE guild.supervision_decisions RENAME TO supervision_decisions_away")
        try:
            self.assertEqual(svc.sources.collect().coverage["decisions"], "skipped")
        finally:
            self.sql("ALTER TABLE guild.supervision_decisions_away RENAME TO supervision_decisions")
        self.assertEqual(svc.sources.collect().coverage["decisions"], "ok")


if __name__ == "__main__":
    unittest.main()
