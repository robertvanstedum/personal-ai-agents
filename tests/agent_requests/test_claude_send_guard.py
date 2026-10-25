import concurrent.futures
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from core.agent_requests import claude_send_guard as A

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/private/tmp')
        self.root = Path(self.tmp.name)
        self.b = {'enabled':True, 'binary_sha256':A.BINARY_SHA256, 'issued_at':10, 'expires_at':20,
                  'input':{'to':'Fixture session [abcdef]', 'message':'Offline fixture only', 'summary':'Offline schema fixture'},
                  'sender_session_id':'fixture-sender', 'operation_sha256':'a'*64,
                  'owner_source':{'note_id':'fixture-note','request_id':'fixture-request','conversation_id':'fixture-chat'}}
        self.b['configuration']={str(self.root/'absent-settings.json'):None}
        self.b['configuration_sha256']=A.configuration_hash(self.b['configuration'])
        self.e = {'hook_event_name':'PreToolUse', 'tool_name':'SendMessage', 'session_id':'fixture-sender',
                  'tool_use_id':'toolu_fixture', 'tool_input':dict(self.b['input'])}
    def tearDown(self): self.tmp.cleanup()
    def call(self, e=None, b=None, **kw):
        return A.admit(A.encoded(e or self.e), b or self.b, self.root, now=kw.pop('now',15), binary_hash=kw.pop('binary_hash',A.BINARY_SHA256), **kw)
    def test_normal_permission_preserved(self):
        result = self.call()
        self.assertNotIn('permissionDecision', result['hookSpecificOutput'])
        self.assertEqual(result['hookSpecificOutput']['updatedInput'],self.b['input'])
        self.assertEqual(len(list(self.root.glob('*.intent.json'))),1)
        self.assertEqual(os.stat(next(self.root.glob('*.intent.json'))).st_mode & 0o777,0o600)
    def test_native_backfill(self):
        self.e['tool_input'].update(type='message', recipient=self.b['input']['to'], recipient_kind='name', content=self.b['input']['message'])
        self.call()
    def test_backfill_long_ascii(self):
        self.b['input']['message']='x'*100; self.e['tool_input']=dict(self.b['input'])
        self.e['tool_input'].update(type='message', recipient=self.b['input']['to'], recipient_kind='name', content='x'*49+'…')
        self.call()
    def test_invalid_fields(self):
        for key,value in [('notify_when_idle',True),('recipient','other'),('content','other'),('type','message'),('extra',1)]:
            with self.subTest(key=key):
                e=copy.deepcopy(self.e);e['tool_input'][key]=value
                with self.assertRaises(A.Refused):self.call(e)
    def test_changed_canonical(self):
        for key in A.CANONICAL:
            e=copy.deepcopy(self.e);e['tool_input'][key]='other'
            with self.assertRaises(A.Refused):self.call(e)
    def test_alias_only_structured_unicode(self):
        for data in [{'recipient':'x','content':'y'},dict(self.b['input'],message={'type':'shutdown_request'})]:
            e=copy.deepcopy(self.e);e['tool_input']=data
            with self.assertRaises(A.Refused):self.call(e)
        self.b['input']['message']='Grüße'
        with self.assertRaises(A.Refused):self.call()
    def test_wrong_session_event_tool(self):
        for k in ('session_id','hook_event_name','tool_name'):
            e=copy.deepcopy(self.e);e[k]='wrong'
            with self.assertRaises(A.Refused):self.call(e)
    def test_disabled_binary_expiry(self):
        for args in ({'binary_hash':'x'},{'now':20},{'now':9},{'b':dict(self.b,enabled=False)}):
            with self.assertRaises(A.Refused):self.call(**args)
    def test_cloud_uuid_addresses(self):
        for to in ('200e6e1f-00b2-4aef-adb3-fa477d7e83cd','session://anything','web:target','main'):
            b=copy.deepcopy(self.b); b['input']['to']=to
            with self.assertRaises(A.Refused):self.call(b=b)
    def test_duplicate_and_size(self):
        for raw in (b'{"a":1,"a":2}',b'x'*32769):
            with self.assertRaises(A.Refused):A.decode(raw)
    def test_single_use_concurrent(self):
        def run(_):
            try:self.call();return True
            except FileExistsError:return False
        with concurrent.futures.ThreadPoolExecutor(8) as pool: values=list(pool.map(run,range(8)))
        self.assertEqual(sum(values),1)
    def test_partial_intent_never_retried(self):
        (self.root/('a'*64+'.intent.json')).write_bytes(b'{')
        with self.assertRaises(FileExistsError):self.call()
    def test_unsafe_root(self):
        os.chmod(self.root,0o755)
        with self.assertRaises(A.Refused):self.call()
    def test_native_receipts(self):
        def result(data,**kw):
            return A.tool_result(A.encoded(dict(type='tool_result',tool_use_id='toolu_fixture',content=json.dumps(data),**kw)),expected_tool_id='toolu_fixture')
        self.assertEqual(result({'success':True,'msg_id':'msg-1'})['state'],'enqueued')
        for data in ({'success':False,'message':'timeout'},{'success':True},{'success':'true','msg_id':'msg-1'}):
            self.assertEqual(result(data)['state'],'unknown')
        self.assertEqual(result({'success':True,'msg_id':'msg-1'},is_error=True)['state'],'unknown')
        self.assertEqual(A.tool_result(A.encoded({'type':'assistant','content':'Delivered!'}),expected_tool_id='x')['state'],'unknown')
    def test_notices_not_completion(self):
        args=dict(native_status='delivered',native_msg_ids=['msg-1'],expected_msg_id='msg-1',sender_session_id='sender',expected_sender_session_id='sender',native_sequence=3,baseline_sequence=2)
        self.assertEqual(A.delivery_notice(**args)['state'],'released')
        self.assertFalse(A.delivery_notice(**args)['completed_reply'])
        for changes in ({'native_msg_ids':['other']},{'native_msg_ids':['msg-1','msg-2']},{'native_sequence':2},{'sender_session_id':'other'},{'native_status':'completed'}):
            self.assertEqual(A.delivery_notice(**dict(args,**changes))['state'],'unknown')
        for state in ('held','denied','expired','refused','dropped'):
            self.assertNotEqual(A.delivery_notice(**dict(args,native_status=state))['state'],'released')

if __name__=='__main__':unittest.main(verbosity=2)

class ConfigurationTests(unittest.TestCase):
    def test_inherited_deny_is_preserved_and_additional_hooks_refuse(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as tmp:
            path=Path(tmp)/'settings.json'
            raw=json.dumps({'permissions':{'deny':['SendMessage']}}).encode();path.write_bytes(raw);path.chmod(0o600)
            import hashlib
            pins={str(path):hashlib.sha256(raw).hexdigest()}
            self.assertEqual(A.configuration_hash(pins),A.digest(pins))
            self.assertEqual(path.read_bytes(),raw)
            path.write_text(json.dumps({'hooks':{'PreToolUse':[{'anything':'unreviewed'}]}}))
            with self.assertRaises(A.Refused):A.configuration_hash(pins)
            pins[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaises(A.Refused):A.configuration_hash(pins)

    def test_fsync_failure_leaves_exclusive_intent_no_retry(self):
        from unittest.mock import patch
        case=Tests();case.setUp()
        try:
            with patch.object(A.os,'fsync',side_effect=OSError('synthetic disk failure')):
                with self.assertRaises(OSError):case.call()
            with self.assertRaises(FileExistsError):case.call()
        finally:case.tearDown()
