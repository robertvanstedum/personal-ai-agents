"""B2: MC's build update for CoS (CoS 1.2 spec v0.3 section 4.4, publish_update_v1) on real PostgreSQL and the real
policy. Run with tests/mc_tools/run_postgres.sh <pgbin>."""
import json
import unittest
from pathlib import Path

from test_tool_path_postgres import Base, DSN  # noqa: F401

from core.activity.writer import ActivityWriter, today
from domains.cos.store import contracts

UPDATE = {"decisions_needed": [{"one_line": "Merge #462 for B0 and B1a?", "ref": {"type": "github.pr", "id": "462"}}],
          "next": ["B2 activity writers"],
          "blocked": [{"one_line": "Dev switch-on waits on Robert's word", "who_can_unblock": "robert"}]}


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class PublishUpdateTests(Base, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.folder = Path(self.tmp.name).resolve() / "activity" / "guild-build"
        self.service.activity_update = ActivityWriter(self.folder, domain="guild-build", part="update",
                                                      metric="build_update", environment="dev",
                                                      service="minimoi-portal")

    def record(self):
        return json.loads((self.folder / f"{today()}.update.json").read_text())

    def test_the_update_is_written_as_mc_s_seat_validated_read_back_and_receipted(self):
        r = self.call("minimoi_publish_update", UPDATE)
        self.assertEqual((r["status"], r["code"]), ("done", "published"))
        record = self.record()
        self.assertEqual(contracts.errors("activity-record", None, record), [])
        self.assertEqual(record["detail"], {"seat": "mc", **UPDATE})
        self.assertEqual((record["part"], record["metric"]), ("update", "build_update"))
        self.assertIn(("guild.build.publish_update", "verified"), [x[:2] for x in self.receipts()])

    def test_a_retry_never_writes_twice_and_a_new_update_replaces_it(self):
        self.call("minimoi_publish_update", UPDATE)
        self.call("minimoi_publish_update", UPDATE)
        self.assertEqual(self.record()["seq"], 1)
        self.next_turn("note-2")
        changed = dict(UPDATE, next=["B4 issues"])
        self.assertEqual(self.call("minimoi_publish_update", changed)["code"], "published")
        self.assertEqual((self.record()["seq"], self.record()["detail"]["next"]), (2, ["B4 issues"]))

    def test_the_model_cannot_name_a_seat_or_an_unknown_unblocker(self):
        self.assertEqual(self.call("minimoi_publish_update", dict(UPDATE, seat="cos"))["status"], "refused")
        bad = dict(UPDATE, blocked=[{"one_line": "x", "who_can_unblock": "anyone"}])
        self.assertEqual(self.call("minimoi_publish_update", bad)["status"], "refused")
        self.assertFalse(self.folder.exists())

    def test_off_until_the_portal_names_the_folder(self):
        self.service.activity_update = None
        self.assertEqual(self.call("minimoi_publish_update", UPDATE)["status"], "refused")
