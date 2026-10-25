import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from core.agent_requests import claude_jsonl_observer as E

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=('/private/tmp' if os.path.isdir('/private/tmp') else None));self.path=Path(self.tmp.name)/'target.jsonl'
  self.path.write_bytes(b'{"type":"custom-title"}\n');self.path.chmod(0o600)
  self.base=E.baseline(self.path,'receiver')
  self.binding={'native_shape_validated':True,'session_id':'receiver','sender_pid':123,'sender_process_start':'start-123','msg_id':'msg-1','payload_sha256':E.hashed(b'fixed envelope'),'dispatch_at':'2026-10-24T20:00:00Z','observation_at':'2026-10-24T21:00:00Z'}
  self.receipt={'type':'user','uuid':'receipt-1','parentUuid':'old','sessionId':'receiver','isSidechain':False,'timestamp':'2026-10-24T20:01:00Z','message':{'role':'user','content':'<cross-session-message>fixed envelope</cross-session-message>'},'origin':{'kind':'peer','from':'claimed-sender','verifiedPeerPid':123,'verifiedPeerProcStart':'start-123','msg_id':'msg-1','body':'fixed envelope'}}
  self.reply={'type':'assistant','uuid':'reply-1','parentUuid':'receipt-1','sessionId':'receiver','isSidechain':False,'timestamp':'2026-10-24T20:02:00Z','message':{'id':'native-answer-1','role':'assistant','stop_reason':'end_turn','content':[{'type':'text','text':'Correlated reply; release remains on hold.'}]}}
 def tearDown(self):self.tmp.cleanup()
 def append(self,*values):
  with self.path.open('ab') as f:
   for v in values:f.write(json.dumps(v).encode()+b'\n')
 def observe(self):return E.observe(self.base,self.binding)
 def test_exact_pickup(self):
  self.append(self.receipt);self.assertEqual(self.observe()['state'],'pickup_observed')
 def test_exact_reply_not_completion(self):
  self.append(self.receipt,self.reply);r=self.observe()
  self.assertEqual(r['state'],'reply_observed');self.assertFalse(r['task_complete']);self.assertFalse(r['clean_native_turn_complete'])
 def test_native_validation_gate(self):
  self.append(self.receipt);self.binding['native_shape_validated']=False
  self.assertEqual(self.observe()['state'],'unknown')
 def test_quoted_user_text_has_no_authority(self):
  r=copy.deepcopy(self.receipt);del r['origin'];self.append(r,self.reply)
  self.assertEqual(self.observe()['state'],'unknown')
 def test_exact_origin_checks(self):
  for k,v in [('msg_id','wrong'),('verifiedPeerPid',456),('verifiedPeerProcStart','other'),('body','changed'),('kind','human'),('hostInjected',True),('members',[])]:
   with self.subTest(key=k):
    self.path.write_bytes(b'{"type":"custom-title"}\n');r=copy.deepcopy(self.receipt);r['origin'][k]=v;self.append(r)
    self.assertEqual(self.observe()['state'],'unknown')
 def test_missing_native_markers(self):
  for k in ('verifiedPeerPid','verifiedPeerProcStart','msg_id','body'):
   self.path.write_bytes(b'{"type":"custom-title"}\n');r=copy.deepcopy(self.receipt);del r['origin'][k];self.append(r)
   self.assertEqual(self.observe()['state'],'unknown')
 def test_foreign_session_and_before_dispatch(self):
  for k,v in [('sessionId','other'),('timestamp','2026-10-24T19:59:59Z'),('isSidechain',True)]:
   self.path.write_bytes(b'{"type":"custom-title"}\n');r=dict(self.receipt,**{k:v});self.append(r)
   self.assertEqual(self.observe()['state'],'unknown')
 def test_ambiguous_duplicate_receipt(self):
  self.append(self.receipt,dict(self.receipt,uuid='receipt-2'))
  self.assertEqual(self.observe()['state'],'unknown')
 def test_parent_chain_and_intervening_user(self):
  self.append(self.receipt,dict(self.reply,parentUuid='unrelated'))
  self.assertEqual(self.observe()['state'],'unknown')
  self.path.write_bytes(b'{"type":"custom-title"}\n');u=dict(self.receipt,uuid='other-user',parentUuid='receipt-1',origin={'kind':'human'})
  self.append(self.receipt,u,dict(self.reply,parentUuid='other-user'))
  self.assertEqual(self.observe()['state'],'unknown')
 def test_nonterminal_response_does_not_finish(self):
  r=copy.deepcopy(self.reply);r['message']['stop_reason']=None
  self.append(self.receipt,r);self.assertEqual(self.observe()['state'],'pickup_observed')
 def test_continuation_after_final_unknown(self):
  self.append(self.receipt,self.reply,dict(self.reply,uuid='reply-2',parentUuid='reply-1'))
  self.assertEqual(self.observe()['state'],'unknown')
 def test_real_tool_chain(self):
  t=copy.deepcopy(self.reply);t['uuid']='tool-call';t['message']={'id':'tool-msg','stop_reason':'tool_use','content':[{'type':'tool_use','id':'tool-1','name':'Read','input':{}}]}
  u=dict(self.receipt,uuid='tool-result',parentUuid='tool-call',origin={'kind':'human'},message={'role':'user','content':[{'type':'tool_result','tool_use_id':'tool-1','content':'not emitted'}]})
  self.append(self.receipt,t,u,dict(self.reply,parentUuid='tool-result'))
  self.assertEqual(self.observe()['state'],'reply_observed')
 def test_partial_last_line_not_interpreted(self):
  self.append(self.receipt)
  with self.path.open('ab') as f:f.write(json.dumps(self.reply).encode()[:100])
  self.assertEqual(self.observe()['state'],'pickup_observed')
 def test_replacement_and_truncation(self):
  replacement=self.path.with_suffix('.new');replacement.write_bytes(self.path.read_bytes());replacement.chmod(0o600);replacement.replace(self.path)
  self.assertEqual(self.observe()['state'],'unknown')
  self.base=E.baseline(self.path,'receiver');self.path.write_bytes(b'')
  self.assertEqual(self.observe()['state'],'unknown')
 def test_baseline_rewrite(self):
  self.path.write_bytes(b'{"type":"custom-wrong"}\n');self.append(self.receipt)
  self.assertEqual(self.observe()['state'],'unknown')
 def test_duplicate_json_key_and_large_delta(self):
  with self.path.open('ab') as f:f.write(b'{"type":"user","type":"assistant"}\n')
  self.assertEqual(self.observe()['state'],'unknown')
  self.path.write_bytes(b'{"type":"custom-title"}\n'+b'x'*(E.MAX_DELTA+1))
  self.assertEqual(self.observe()['state'],'unknown')
 def test_report_limit_and_compaction(self):
  r=copy.deepcopy(self.reply);r['message']['content'][0]['text']='x'*(E.MAX_REPORT+1)
  self.append(self.receipt,r);self.assertEqual(self.observe()['state'],'unknown')
  self.path.write_bytes(b'{"type":"custom-title"}\n');self.append(self.receipt,dict(self.reply,type='system',subtype='compact_boundary'))
  self.assertEqual(self.observe()['state'],'unknown')
 def test_invalid_timestamp(self):
  self.receipt['timestamp']='2026-10-24not-a-time';self.append(self.receipt)
  self.assertEqual(self.observe()['state'],'unknown')
 def test_reasoning_not_emitted(self):
  r=copy.deepcopy(self.reply);r['message']['content'].insert(0,{'type':'thinking','thinking':'SECRET_TEST_FIXTURE'})
  self.append(self.receipt,r);self.assertNotIn('SECRET_TEST_FIXTURE',json.dumps(self.observe()))

if __name__=='__main__':unittest.main(verbosity=2)

class LivePidTests(unittest.TestCase):
 setUp=Tests.setUp
 tearDown=Tests.tearDown
 append=Tests.append
 observe=Tests.observe
 def test_live_pid_without_nonexistent_process_start_can_capture(self):
  self.binding['provenance_mode']='owned-live-pid-v1';self.binding['owned_child_alive']=True
  self.binding.pop('sender_process_start',None);self.receipt['origin'].pop('verifiedPeerProcStart',None)
  self.append(self.receipt)
  proof=self.observe();self.assertEqual(proof['state'],'pickup_observed')
  self.binding['owned_child_alive']=False
  self.assertEqual(self.observe()['state'],'unknown')
  self.binding['authenticated_receipt']=proof
  self.append(self.reply)
  self.assertEqual(self.observe()['state'],'reply_observed')

 def test_prior_receipt_hash_cannot_be_substituted(self):
  self.binding['provenance_mode']='owned-live-pid-v1';self.binding['owned_child_alive']=True
  self.binding.pop('sender_process_start',None);self.receipt['origin'].pop('verifiedPeerProcStart',None)
  self.append(self.receipt);proof=self.observe()
  self.binding['owned_child_alive']=False;self.binding['authenticated_receipt']=dict(proof,receipt_sha256='0'*64)
  self.assertEqual(self.observe()['state'],'unknown')
