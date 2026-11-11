"""Canonical MC note and live claim fence in the same disposable PostgreSQL transaction."""
import unittest
from pathlib import Path
from test_tool_path_postgres import Base,DSN,MIGRATIONS,OWNER,NOTE
from test_board_postgres import BOARD_MIGRATIONS
from core.lead_wake.hosts import MCHost
from minimoi_portal.guild_ui.conversations import ConversationStore
from minimoi_portal.guild_ui.stores import FloorStores,Author
from minimoi_portal.guild_ui.mc.turn_ledger import TurnLedger


@unittest.skipUnless(DSN,'requires disposable MC_BINDING_TEST_DSN')
class ReturnPublication(Base,unittest.TestCase):
    migrations=BOARD_MIGRATIONS+MIGRATIONS+('minimoi_portal/guild_ui/sql/009_mc_lead_review_lane.sql',)

    def setUp(self):
        super().setUp()
        self.conversations=ConversationStore(self.tmp.name,base_floor="return-test")
        self.conv,_=self.conversations.create(OWNER,key='return-fixture-123')
        self.floor=FloorStores(lambda:DSN)
        self.notes=self.floor.for_floor(self.conv['notes_floor'])
        self.notes.add_note(NOTE,'Find the build log.',Author(OWNER,'owner','Robert'))
        self.origin=self.claim(NOTE,conv=self.conv['id'])
        self.finish_turn(self.origin)
        self.turn_ledger=TurnLedger(lambda:DSN,worker='return-test')
        self.host=MCHost(conversations=self.conversations,floor=self.floor,ledger=self.turn_ledger,
            principal=OWNER,environment='dev',access=lambda p:True)
        self.packet={'lead':'mc','principal':OWNER,'environment':'dev','data_scope':'personal',
            'source_ref':{'store':'mc-floor','id':self.conv['id']+':'+NOTE,'position':1},
            'id':'consult:OB-publish-'+'a'*40,'text':'Apprentice found build_log.html; MC reviewed the evidence.'}

    def test_canonical_write_readback_and_no_duplicate(self):
        with self.host.lane('op',self.packet) as lane:
            self.assertIsNotNone(lane)
            self.host.write(self.packet)
            self.host.write(self.packet)
            self.assertTrue(self.host.read(self.packet))
        self.assertEqual(self.one('SELECT count(*) FROM guild.floor_messages WHERE request_id=%s',(self.packet['id'],))[0],1)

    def test_claim_takeover_after_admission_prevents_note_write(self):
        with self.host.lane('op',self.packet) as lane:
            lane.check()
            claim=self.host._claim.get()
            with self.connect() as conn,conn.cursor() as cur:
                cur.execute("UPDATE guild.mc_turn_ledger SET lease_until=now()-interval '1 second' WHERE id=%s",(claim.ledger_id,))
            with self.assertRaises(Exception):self.host.write(self.packet)
        self.assertIsNone(self.notes.get_note(self.packet['id']))

    def test_explicit_lost_claim_cannot_publish(self):
        with self.host.lane('op',self.packet) as lane:
            claim=self.host._claim.get()
            self.assertTrue(self.turn_ledger.finish(claim,'not_sent',{}))
            with self.assertRaises(Exception):self.host.write(self.packet)
        self.assertIsNone(self.notes.get_note(self.packet['id']))

    def test_inline_receipt_reads_exact_canonical_mc_reply(self):
        import hashlib
        from minimoi_portal.guild_ui.stores import MASTER_CRAFTSMAN
        key='mc-'+hashlib.sha256(NOTE.encode('utf-8')).hexdigest()[:40]
        self.assertIsNone(self.host.inline_answer(self.packet))
        self.notes.add_note(key,'Exact reviewed helper block.',MASTER_CRAFTSMAN,page='mc')
        self.assertEqual(self.host.inline_answer(self.packet),(key,'Exact reviewed helper block.'))
        self.host.access=lambda _:False
        with self.assertRaises(PermissionError):self.host.inline_answer(self.packet)
