"""Synthetic disposable PostgreSQL, explicit privacy negatives; no owner data."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from test_tool_path_postgres import Base,DSN,MIGRATIONS,NOTE,OWNER,session_key
from test_board_postgres import BOARD_MIGRATIONS
from minimoi_portal.guild_ui.stores import FloorStores
from minimoi_portal.guild_ui.mc_recall import Source
from core.mc_tools import recall,binding
C='c-aaaaaaaaaaaa';D='c-bbbbbbbbbbbb';NOTE='recall-request'

@unittest.skipUnless(DSN,'disposable PostgreSQL only')
class RecallTests(Base,unittest.TestCase):
    migrations=BOARD_MIGRATIONS+MIGRATIONS
    def install_policy(self,revision='recall-test',change=None):
        def extend(doc):
            doc['rules']=[r for r in doc['rules'] if r['handler']!='recall_v1']
            doc['rules'].append(dict(capability='mc.records.read',version=1,actors=['mc'],origins=['owner_turn'],tier=1,approval='none',source_requirement='current_owner_turn',handler='recall_v1',verify=None,undo=None,egress='none',max_operations_per_turn=20))
            if change:change(doc)
        return super().install_policy(revision,extend)
    def setUp(self):
        super().setUp()
        self.folder=Path(self.tmp.name)/'conversations';self.folder.mkdir()
        self.services=SimpleNamespace(store=SimpleNamespace(folder=self.tmp.name,path='synthetic'),floor=FloorStores(lambda:DSN,floor='guild'))
        self.service.recall_source=lambda:Source(self.services,OWNER)
        self.ledger_id=self.claim(NOTE,C);self.bind(self.ledger_id,NOTE,C);self.run=self.runs[self.ledger_id]
        with self.connect() as conn,conn.cursor() as q:
            q.execute('TRUNCATE guild.floor_messages CASCADE')
            # Disposable negative fixture deliberately permits private rows and large notes. The schema's own
            # checks go back after each test: later tests on this database rely on them (owner note, artifacts).
            q.execute("""SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='guild.floor_messages'::regclass
                AND conname IN ('floor_messages_record_mode_check','floor_messages_text_check')""")
            self.addCleanup(self.restore_checks,q.fetchall())
            q.execute('ALTER TABLE guild.floor_messages DROP CONSTRAINT IF EXISTS floor_messages_record_mode_check')
            q.execute('ALTER TABLE guild.floor_messages DROP CONSTRAINT IF EXISTS floor_messages_text_check')
        self.meta(C);self.meta(D)
        self.add(C,'old','needle old');self.add(C,NOTE,'current owner request');self.add(C,'later','needle forbidden')
        self.add(D,'prior','needle historical');self.add(D,'private','needle private',mode='off_record')
        self.add('cos','cross-role','needle CoS')
    def restore_checks(self,dropped):
        with self.connect() as conn,conn.cursor() as q:
            q.execute('TRUNCATE guild.floor_messages CASCADE')
            for name,definition in dropped:
                q.execute(f'ALTER TABLE guild.floor_messages ADD CONSTRAINT {name} {definition}')
    def meta(self,c,**extra):
        data=dict(id=c,principal=OWNER,legacy=False,archived=False,scope='operate',notes_floor='guild/'+c);data.update(extra)
        (self.folder/(c+'.json')).write_text(json.dumps(data))
    def add(self,c,key,text,who=OWNER,mode='on_record'):
        return self.one("INSERT INTO guild.floor_messages(floor,request_id,author,author_kind,author_label,text,record_mode,created_at) VALUES(%s,%s,%s,'owner','Owner',%s,%s,now()) RETURNING id",('guild/'+c,key,who,text,mode))[0]
    def invoke(self,args,offered=True):
        return self.call('minimoi_recall',args,key=session_key(C),offered_tools=['minimoi_recall'] if offered else None)
    def test_find_open_private_and_current_boundary(self):
        out=self.invoke(dict(action='find',query='needle'));self.assertEqual(out['status'],'done',out)
        texts=' '.join(r['text'] for r in out['data']['items'])
        self.assertIn('historical',texts);self.assertIn('old',texts)
        for forbidden in ('forbidden','private','CoS'):self.assertNotIn(forbidden,texts)
        out=self.invoke(dict(action='open',conversation_ref=C));self.assertEqual(out['status'],'done',out)
        self.assertNotIn('forbidden',json.dumps(out))
        self.assertTrue(self.one('SELECT untrusted_read FROM guild.mc_tool_turns WHERE ledger_id=%s',(self.ledger_id,))[0])
        self.assertEqual(self.one('SELECT count(*) FROM guild.mc_tool_conversation_taint WHERE conversation_id=%s',(C,))[0],1)
    def test_foreign_contamination_fails_closed(self):
        self.add(D,'foreign','private other owner',who='other')
        self.assertEqual(self.invoke(dict(action='open',conversation_ref=D))['status'],'refused')
    def test_archived_foreign_and_bad_floor_not_disclosed(self):
        for extra in (dict(archived=True),dict(principal='other'),dict(notes_floor='cos')):
            self.meta(D,**extra)
            out=self.invoke(dict(action='open',conversation_ref=D));self.assertEqual(out['status'],'refused',out)
            self.assertNotIn('historical',json.dumps(out))
    def test_missing_offer_and_stopped_never_construct_source(self):
        with patch.object(self.service,'recall_source') as source:
            self.assertEqual(self.invoke(dict(action='find',query='needle'),offered=False)['status'],'refused');source.assert_not_called()
            self.finish_turn(self.ledger_id)
            self.assertEqual(self.invoke(dict(action='find',query='needle'))['status'],'refused');source.assert_not_called()
    def test_private_current_request_refused(self):
        with self.connect() as conn,conn.cursor() as q:q.execute("UPDATE guild.floor_messages SET record_mode='off_record' WHERE request_id=%s",(NOTE,))
        self.assertEqual(self.invoke(dict(action='find',query='needle'))['status'],'refused')
    def test_oversized_partial_and_open_refusal(self):
        self.add(D,'large','x'*1048577)
        out=self.invoke(dict(action='find',query='needle'));self.assertEqual(out['status'],'done',out)
        self.assertFalse(out['data']['complete']);self.assertEqual(out['data']['skipped_oversized'],1)
        self.assertEqual(self.invoke(dict(action='open',conversation_ref=D))['code'],'recall_snapshot_too_large')
    def test_metadata_corruption_and_symlink_refuse_not_empty_success(self):
        f=self.folder/(D+'.json');f.write_text('{bad')
        self.assertEqual(self.invoke(dict(action='find',query='needle'))['status'],'refused')
        f.unlink();f.symlink_to(self.folder/(C+'.json'))
        self.assertEqual(self.invoke(dict(action='find',query='needle'))['status'],'refused')
    def test_stop_during_source_read_discards_content(self):
        real=self.service.recall_source
        def factory():
            src=real();original=src.recheck
            def stopped():original();self.finish_turn(self.ledger_id)
            src.recheck=stopped;return src
        self.service.recall_source=factory
        out=self.invoke(dict(action='find',query='needle'));self.assertEqual(out['status'],'refused');self.assertNotIn('historical',json.dumps(out))
    def test_continuation_rechecks_current_cap_and_changed_snapshot(self):
        args=dict(action='open',conversation_ref=C,limit=1)
        out=self.invoke(args);self.assertEqual(out['status'],'done',out)
        cursor=out['data']['continuation'];self.assertTrue(cursor)
        _,value=recall.request({**args,'continuation':cursor});value['b']=999999
        out=self.invoke({**args,'continuation':recall.token(value)})
        self.assertEqual(out['status'],'done',out);self.assertNotIn('forbidden',json.dumps(out))
        with self.connect() as conn,conn.cursor() as q:q.execute("UPDATE guild.floor_messages SET text='changed' WHERE floor=%s AND request_id='old'",('guild/'+C,))
        self.assertEqual(self.invoke({**args,'continuation':cursor})['code'],'recall_source_changed')
    def test_wrong_identity_and_old_instance_never_construct_source(self):
        with patch.object(self.service,'recall_source') as source:
            for kwargs in ({'key':session_key('c-cccccccccccc')},{'instance':'b'*32},{'run':'chatcmpl_00000000-0000-0000-0000-000000000000'}):
                out=self.call('minimoi_recall',dict(action='find',query='needle'),offered_tools=['minimoi_recall'],**kwargs)
                self.assertEqual(out['status'],'refused',out)
            source.assert_not_called()

    def test_policy_swap_during_source_read_discards_content(self):
        real=self.service.recall_source
        def factory():
            src=real();original=src.recheck
            def changed():original();self.install_policy('recall-new-policy')
            src.recheck=changed;return src
        self.service.recall_source=factory
        out=self.invoke(dict(action='find',query='needle'));self.assertEqual(out['status'],'refused');self.assertNotIn('historical',json.dumps(out))

    def test_unknown_scope_is_excluded_without_poisoning_known_history(self):
        self.meta(D,scope='outside-mc')
        out=self.invoke(dict(action='find',query='needle'))
        self.assertEqual(out['status'],'done',out)
        self.assertIn('other_scopes',out['data']['excluded'])
        self.assertIn('needle old',json.dumps(out))
        self.assertNotIn('needle historical',json.dumps(out))
        self.assertNotIn(D,json.dumps(out))
        self.assertEqual(self.invoke(dict(action='open',conversation_ref=D))['status'],'refused')
        self.meta(C,scope='outside-mc')
        self.assertEqual(self.invoke(dict(action='find',query='needle'))['status'],'refused')
