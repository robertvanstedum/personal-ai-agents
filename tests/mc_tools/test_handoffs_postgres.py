"""CoS -> MC handoff, MC's receiving side (spec v1.2 §16.1; A11a) on real PostgreSQL. Run with run_postgres.sh."""
import hashlib
import unittest
import uuid

from test_tool_path_postgres import Base, DSN, OWNER  # noqa: F401
from core.mc_tools import handoffs

WORDS = "Can you reject the guest request from Bruno? I can't do it in the screens."


def body(tid=None, words=WORDS, **change):
    doc = {"transfer_id": tid or str(uuid.uuid4()), "principal": OWNER, "environment": "dev",
           "source": {"agent": "cos", "conversation_id": "cos-conv-1", "request_note_id": "cos-note-7",
                      "request_text": words, "request_text_sha256": hashlib.sha256(words.encode()).hexdigest()},
           "approval": {"note_id": "cos-note-8", "text": "Yes, hand it to MC.", "at": "2026-10-22T19:05:00-05:00"},
           "summary": "Guest request rejection belongs to Master Craftsman."}
    doc.update(change)
    return doc


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class HandoffTests(Base, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.delivered, self.notes, self.fail_delivery = [], {}, False

    def deliver(self, tid, title, text):
        if self.fail_delivery:
            raise handoffs.HandoffRefused("handoff_not_delivered")
        self.delivered.append((tid, title))
        key = f"mc-handoff-{tid}"
        self.notes.setdefault(("conv-" + tid[:8], key), text)
        return "conv-" + tid[:8], key

    def read_back(self, conv, key):
        return self.notes.get((conv, key))

    def receive(self, doc):
        return handoffs.receive(self.connect, doc, principal=OWNER, environment="dev", deliver=self.deliver,
                                read_back=self.read_back, link_for=lambda cid: f"/guild-next/guild/build?c={cid}")

    def test_a11a_persisted_delivered_then_acknowledged_once(self):
        doc = body()
        ack = self.receive(doc)
        self.assertEqual((ack["state"], ack["transfer_id"]), ("delivered", doc["transfer_id"]))
        self.assertIsNotNone(ack["acknowledged_at"])
        [(tid, title)] = self.delivered
        self.assertTrue(title.startswith("From Chief of Staff: Can you reject the guest request"))
        text = self.notes[(ack["mc_conversation_id"], ack["note_ref"])]
        self.assertIn(f"Robert asked: “{WORDS}”", text)
        self.assertIn("approves nothing by itself", text)
        again = self.receive(doc)                                          # CoS retries with the same id
        self.assertEqual(again, ack)
        self.assertEqual(len(self.delivered), 1)
        self.assertEqual(self.receipts(), [])                               # nothing executed from a transfer

    def test_a11a_no_acknowledgement_without_a_delivered_note_and_the_retry_completes(self):
        doc = body()
        self.fail_delivery = True
        with self.assertRaises(handoffs.HandoffUnknown):               # never "refused" after an attempt (H1)
            self.receive(doc)
        self.assertEqual(self.one("SELECT state FROM guild.mc_handoffs")[0], "received")   # CoS stays "pending"
        self.fail_delivery = False
        self.assertEqual(self.receive(doc)["state"], "delivered")

    def test_h1_a_note_written_but_not_read_back_is_unknown_and_the_retry_completes(self):
        doc, real = body(), self.read_back
        self.read_back = lambda conv, key: None                          # the note exists; read-back fails
        with self.assertRaises(handoffs.HandoffUnknown):
            self.receive(doc)
        self.assertEqual(len(self.delivered), 1)
        self.read_back = real
        ack = self.receive(doc)
        self.assertEqual((ack["state"], len(self.notes)), ("delivered", 1))

    def test_h2_the_acknowledgement_links_to_the_conversation_identically_on_replay(self):
        doc = body()
        ack = self.receive(doc)
        self.assertEqual(ack["link"], f"/guild-next/guild/build?c={ack['mc_conversation_id']}")
        self.assertEqual(self.receive(doc)["link"], ack["link"])

    def test_h3_the_approval_time_must_be_a_timezone_aware_iso_time(self):
        for at in ("not-a-time", "2026-10-22T19:05:00", "yesterday"):
            doc = body()
            doc["approval"]["at"] = at
            with self.assertRaisesRegex(handoffs.HandoffRefused, "handoff_approval_invalid"):
                self.receive(doc)
        doc = body()
        doc["approval"]["at"] = "2026-10-23T00:05:00Z"
        self.assertEqual(self.receive(doc)["state"], "delivered")

    def test_a11a_a_reused_id_with_other_words_is_refused(self):
        doc = body()
        self.receive(doc)
        with self.assertRaisesRegex(handoffs.HandoffRefused, "handoff_id_reused"):
            self.receive(body(doc["transfer_id"], words="Delete every guest request."))

    def test_a11a_only_robert_exact_words_for_this_owner_and_environment(self):
        bad_hash = body()
        bad_hash["source"]["request_text_sha256"] = "0" * 64
        for doc, code in ((bad_hash, "handoff_source_invalid"), (body(principal="someone"), "handoff_not_for_this_owner"),
                          (body(environment="prod"), "handoff_not_for_this_owner"),
                          ({**body(), "execute": True}, "handoff_invalid")):
            with self.assertRaisesRegex(handoffs.HandoffRefused, code):
                self.receive(doc)
        self.assertEqual(self.one("SELECT count(*) FROM guild.mc_handoffs")[0], 0)


if __name__ == "__main__":
    unittest.main()
