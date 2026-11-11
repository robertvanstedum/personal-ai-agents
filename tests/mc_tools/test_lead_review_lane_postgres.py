"""A service review competes with normal chat on the real canonical MC lane."""
import unittest
from test_tool_path_postgres import Base, DSN, OWNER, CONV, NOTE
from minimoi_portal.guild_ui.mc.turn_ledger import TurnLedger


@unittest.skipUnless(DSN,'requires disposable MC_BINDING_TEST_DSN')
class LeadReviewLane(Base,unittest.TestCase):
    migrations=Base.migrations+('minimoi_portal/guild_ui/sql/009_mc_lead_review_lane.sql',)

    def review(self,ledger,op='a'*40):
        return ledger.admit_service_review(principal=OWNER,conversation_id=CONV,
                                           source_note_request_id=NOTE,operation_id='OB-wake-'+op)

    def ledger_store(self):
        return TurnLedger(lambda:DSN,worker='synthetic-review')

    def test_unsettled_origin_refuses_then_service_claim_blocks_owner(self):
        ledger=self.ledger_store()
        self.assertEqual(self.review(ledger).result,'busy')
        self.finish_turn(self.ledger_id)
        admitted=self.review(ledger)
        self.assertEqual(admitted.result,'claimed')
        self.assertEqual(admitted.entry['kind'],'lead_review')
        next_owner=ledger.admit(principal=OWNER,conversation_id=CONV,
                                note_request_id='owner-next',turn_request_id='owner-turn-next')
        self.assertEqual(next_owner.result,'busy')
        self.assertEqual(self.review(ledger).result,'running')
        self.assertTrue(ledger.finish(admitted.claim,'answered',{'status':'presented','reply_note_id':123}))
        self.assertEqual(self.review(ledger).result,'done')

    def test_queued_owner_goes_before_service_review(self):
        ledger=self.ledger_store()
        self.finish_turn(self.ledger_id)
        queued=ledger.enqueue(principal=OWNER,conversation_id=CONV,
                              note_request_id='owner-next',turn_request_id='owner-turn-next')
        self.assertEqual(queued.result,'queued')
        self.assertEqual(self.review(ledger).result,'queue_pending')

    def test_normal_entrypoint_cannot_claim_service_origin(self):
        ledger=self.ledger_store()
        with self.assertRaises(ValueError):
            ledger.admit(principal=OWNER,conversation_id=CONV,note_request_id='fake',
                         turn_request_id='fake-turn',kind='lead_review')

    def test_continue_does_not_settle_unknown_origin(self):
        ledger = self.ledger_store()
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE guild.mc_turn_ledger SET state='unknown', claim_token=NULL, lease_until=NULL WHERE id=%s",
                        (self.ledger_id,))
            cur.execute("INSERT INTO guild.mc_turn_barrier (principal,ledger_id,reason) VALUES (%s,%s,'unknown')",
                        (OWNER,self.ledger_id))
        self.assertEqual(ledger.continue_after(OWNER,self.ledger_id)['result'],'continued')
        self.assertEqual(self.review(ledger).result,'busy')
        self.finish_turn(self.ledger_id)
        self.assertEqual(self.review(ledger).result,'claimed')
