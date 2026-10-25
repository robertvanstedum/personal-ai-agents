"""Scripted model -> real isolated native gateway -> relay/listener -> Board DB.
No paid provider or Robert data; these are dispatch proofs, not language evals.
"""
import base64
import json
import time
import unittest

import test_native_admission_probe as native
from test_tool_path_postgres import CONV, MIGRATIONS
from test_board_postgres import BOARD_MIGRATIONS
from core.mc_tools.board_adapter import BoardAdapter
from minimoi_portal.guild_ui.stores import FloorStores


@unittest.skipUnless(native.DSN and native.ENABLED, 'isolated native gateway opt-in required')
class BoardNative(native.NativeAdmissionProbe):
    migrations=BOARD_MIGRATIONS+MIGRATIONS+('core/mc_tools/sql/012_board_conversation.sql',)

    def setUp(self):
        super().setUp()
        with self.connect() as conn,conn.cursor() as cur:
            cur.execute('TRUNCATE guild.mc_board_slots,guild.mc_board_observations,guild.board_operations,'
                        'guild.floor_postits,guild.floor_board_meta,guild.floor_requests CASCADE')
        self.service.board=BoardAdapter(cursor_key=b'synthetic-native-board-fixture'*2)
        self.store=FloorStores(lambda:native.DSN)

    def scripted(self,note,words,steps):
        self.owner_notes[(CONV,note)]=words
        text=words+' BOARD_SCRIPT:'+base64.b64encode(json.dumps(steps).encode()).decode()
        started=time.perf_counter()
        thread,state=self.turn(note,text)
        thread.join(90)
        self.assertFalse(thread.is_alive(), 'native turn timed out')
        results=self.answers(state['run'],len(steps))
        self.assertEqual(len(results),len(steps),state)
        self.assertTrue(all(r.get('status')=='done' for r in results),results)
        self.finish_turn(state['ledger_id'])
        return results,{'seconds':time.perf_counter()-started,'run_bound':bool(state.get('attached')),
                        'result_codes':[r['code'] for r in results]}

    def test_board_native_create_update_recover_and_undo(self):
        steps=lambda name,args:[{'name':name,'arguments':args}]
        out,create=self.scripted('board-create','Bitte eine Notiz: Morgen anrufen.',
                                steps('minimoi_board_note_create',{'text':'Morgen anrufen. 👋\nRobert'}))
        note=out[0]['data']['current']['id'];op=out[0]['data']['operation_ref']
        out,recovery=self.scripted('board-recover','Did that save?',steps('minimoi_board_operation_read',{'operation_ref':'latest'}))
        self.assertEqual(out[0]['data']['operation_ref'],op)
        out,update=self.scripted('board-update','Mark that note done.',
            steps('minimoi_board_note_read',{'note_ref':note})+steps('minimoi_board_note_update',{'note_ref':note,'changes':{'done':True}}))
        self.assertEqual(out[-1]['data']['current']['state'],'done')
        undo_op=out[-1]['data']['operation_ref']
        out,undo=self.scripted('board-undo','Undo that.',steps('minimoi_board_operation_undo',{'operation_ref':undo_op}))
        self.assertEqual(out[-1]['data']['current']['state'],'active')
        self.assertEqual(self.one('SELECT count(*) FROM guild.floor_postits')[0],1)
        self.record('board_native_create_update_recover_undo',create=create,recovery=recovery,update=update,undo=undo,
                    note_count=1,host_effects=len(self.receipts()),provider='scripted-local')
