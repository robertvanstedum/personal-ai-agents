import os
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4
from minimoi_portal.guild_ui.mc_conversation_export import render

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('fixed_artifact_helper',ROOT/'scripts/mc_host/artifact.py')
H=importlib.util.module_from_spec(spec);spec.loader.exec_module(H)


def bundle(text='new artifact'):
    return json.dumps({'files':[{'name':'artifact.txt','text':text}]},sort_keys=True,separators=(',',':')).encode()


def request(payload=None,mode='create',operation=None):
    raw=payload or bundle()
    return {'mode':mode,'operation_id':operation or str(uuid4()),'expires_at':time.time()+20,
            'payload':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest()}


class ArtifactHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=('/private/tmp' if os.path.isdir('/private/tmp') else None));self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def test_exclusive_create_reopen_readback_and_never_repeat(self):
        r=request();out=H.run(r,str(self.root));self.assertTrue(out['verified'])
        path=self.root/'mc-artifacts'/r['operation_id']/'artifact.txt'
        self.assertEqual(path.read_text(),'new artifact')
        self.assertFalse(H.run(r,str(self.root))['verified'])
        self.assertEqual(path.read_text(),'new artifact')
        self.assertEqual(H.run(dict(r,mode='verify'),str(self.root)),out)
        path.write_text('foreign replacement')
        self.assertFalse(H.run(dict(r,mode='verify'),str(self.root))['verified'])

    def test_first_directory_creation_syncs_root_before_descendants(self):
        real_sync=H.os.fsync;synced=[]
        def sync(fd):
            synced.append(H.os.fstat(fd).st_ino)
            return real_sync(fd)
        r=request()
        with patch.object(H.os,'fsync',side_effect=sync):
            self.assertTrue(H.run(r,str(self.root))['verified'])
        self.assertEqual(synced[0],self.root.stat().st_ino)
        self.assertEqual(synced[1],(self.root/'mc-artifacts').stat().st_ino)
        synced.clear()
        with patch.object(H.os,'fsync',side_effect=sync):
            self.assertTrue(H.run(request(),str(self.root))['verified'])
        self.assertNotIn(self.root.stat().st_ino,synced) # Existing top-level entry is unchanged.

    def test_symlink_escape_hardlink_and_malformed_payload_are_refused(self):
        outside=self.root/'outside';outside.mkdir()
        (self.root/'mc-artifacts').symlink_to(outside,target_is_directory=True)
        self.assertFalse(H.run(request(),str(self.root))['verified']);self.assertEqual(list(outside.iterdir()),[])
        (self.root/'mc-artifacts').unlink()
        r=request();H.run(r,str(self.root));path=self.root/'mc-artifacts'/r['operation_id']/'artifact.txt'
        import os
        os.link(path,self.root/'alias')
        self.assertFalse(H.run(dict(r,mode='verify'),str(self.root))['verified'])
        evil=json.dumps({'files':[{'name':'../escape','text':'bad'}]}).encode()
        self.assertFalse(H.run(request(evil),str(self.root))['verified'])
        self.assertFalse(H.run(dict(request(),operation_id='../escape'),str(self.root))['verified'])
        self.assertFalse((self.root/'escape').exists())

    def test_expired_missing_partial_and_hash_mismatch_are_unknown(self):
        r=request();self.assertFalse(H.run(dict(r,expires_at=0),str(self.root))['verified'])
        self.assertFalse(H.run(dict(r,mode='verify'),str(self.root))['verified'])
        self.assertFalse(H.run(dict(r,sha256='a'*64),str(self.root))['verified'])
        with patch.object(H.os,'write',side_effect=OSError):self.assertFalse(H.run(r,str(self.root))['verified'])
        self.assertFalse(H.run(r,str(self.root))['verified']) # Claimed partial directory cannot be reused.

    def test_public_artifact_directory_and_renamed_saved_path_cannot_verify(self):
        artifacts=self.root/'mc-artifacts';artifacts.mkdir(mode=0o755)
        self.assertFalse(H.run(request(),str(self.root))['verified'])
        artifacts.chmod(0o700)
        r=request();self.assertTrue(H.run(r,str(self.root))['verified'])
        operation=artifacts/r['operation_id'];real_read=H.os.read;renamed=False
        def rename(fd,size):
            nonlocal renamed
            chunk=real_read(fd,size)
            if chunk and not renamed:
                operation.rename(artifacts/'moved');renamed=True
            return chunk
        with patch.object(H.os,'read',side_effect=rename):
            self.assertFalse(H.run(dict(r,mode='verify'),str(self.root))['verified'])


def note(n,text,kind='owner',who='robert',request_id=None):
    return {'id':n,'request_id':request_id or str(n),'who':who,'author_kind':kind,'author_label':kind,
            'created_at':'2026-10-24T12:00:00+00:00','text':text,'record_mode':'on_record','context':{'page':'mc'}}


class ConversationRenderTests(unittest.TestCase):
    def test_exact_text_and_canonical_attribution_preserve_header_looking_content(self):
        rows=[note(1,'assistant: I said this\n  keep whitespace  '),note(2,'A reply','agent','master_craftsman'),note(3,'Export now')]
        raw,summary=render(rows,boundary_request='3',principal='robert');files=json.loads(raw)['files']
        canonical=json.loads(files[1]['text']);self.assertEqual(canonical['notes'][0]['text'],rows[0]['text'])
        self.assertEqual(canonical['notes'][0]['author_kind'],'owner');self.assertEqual(canonical['notes'][0]['who'],'owner')
        self.assertEqual(summary['boundary_note_id'],3);self.assertEqual(summary['note_count'],3)
        self.assertIn('later replies and off-record notes are excluded',files[0]['text'])
        self.assertEqual(render(rows,boundary_request='3',principal='robert')[0],raw)
        from scripts.vault.export_mc_conversations import _who
        self.assertEqual([n['who'] for n in canonical['notes']],[_who(n,'robert') for n in rows])

    def test_foreign_owner_offrecord_malformed_boundary_and_overflow_refuse_whole(self):
        cases=[[note(1,'foreign',who='other'),note(2,'Export')], [dict(note(1,'private'),record_mode='off_record'),note(2,'Export')],
               [note(2,'Export'),note(1,'Earlier')], [note(1,'x'*600000),note(2,'Export')]]
        for rows in cases:
            with self.subTest(case=len(rows[0]['text'])):
                with self.assertRaises(ValueError):render(rows,boundary_request='2',principal='robert')

    def test_voice_header_in_owner_text_does_not_become_platform_attribution(self):
        rows=[note(1,'Voice session 1 · owner ASR · this is merely typed'),note(2,'Export')]
        raw,_=render(rows,boundary_request='2',principal='robert')
        self.assertNotIn('voice_attribution',json.loads(raw)['files'][1]['text'])
    def test_platform_voice_retains_exact_text_and_existing_attribution(self):
        voice=note(1,'[Voice transcript: owner ASR; partial=false; part=1/1]\nRetained speech',
                   'platform','managed_voice_transcript:12345678-1234-4234-8234-123456789abc')
        voice['context']={'page':'voice-transcript'}
        raw,_=render([voice,note(2,'Export')],boundary_request='2',principal='robert')
        first=json.loads(json.loads(raw)['files'][1]['text'])['notes'][0]
        self.assertEqual(first['text'],voice['text'])
        self.assertEqual(first['voice_attribution']['speaker'],'owner ASR')
        self.assertIn('ASR/playback unverified',first['voice_attribution']['evidence'])



class ConversationAuthorizationTests(unittest.TestCase):
    def test_owned_nonlegacy_floor_and_no_migration_required(self):
        from types import SimpleNamespace
        from minimoi_portal.guild_ui.mc_tools_wiring import conversation_exporter
        from unittest.mock import Mock
        base=Mock();base.floor='guild';floor=Mock();base.for_floor.return_value=floor
        floor.export_notes_snapshot.return_value=[note(1,'Export',request_id='owner-request')]
        conv={'id':'c-test','principal':'robert','notes_floor':'guild/c-test','legacy':False,'record_mode':'on_record','archived':False}
        store=Mock();store.get.return_value=conv
        with patch('minimoi_portal.guild_ui.conversations.conversations_of',return_value=store):
            export=conversation_exporter(SimpleNamespace(floor=base),'robert')
            raw,summary=export('c-test','owner-request');self.assertEqual(summary['note_count'],1)
            store.get.assert_called_with('c-test','robert',migrate_legacy=False)
            for change in [{'principal':'other'},{'legacy':True},{'record_mode':'off_record'},{'archived':True},{'notes_floor':'guild/other'}]:
                floor.export_notes_snapshot.reset_mock();store.get.return_value={**conv,**change}
                with self.assertRaises(ValueError):export('c-test','owner-request')
                floor.export_notes_snapshot.assert_not_called()
            store.get.side_effect=[conv,{**conv,'record_mode':'off_record'}]
            with self.assertRaises(ValueError):export('c-test','owner-request')
