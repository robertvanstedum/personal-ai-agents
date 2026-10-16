"""Synthetic result custody/presentation; no model, database, or live ledger."""
import hashlib,types
from unittest.mock import patch
import pytest
from minimoi_portal.guild_ui import agent_ask as m
RID='r-synthetic-result';NAME='Claude Code'
def head():return f"**{NAME}'s report** (agent-authored; not a verification and not an approval)\n\n"
@pytest.mark.parametrize('extra',[-1,0,1,9000])
def test_exact_4000_boundary_and_original_text_unchanged(extra):
 text='x'*(4000-len(head())+extra);before=text;shown=m.report_note(NAME,RID,text)
 assert len(shown)<=4000 and text==before
 if extra<=0:assert shown==head()+text
 else:
  assert 'Excerpt only' in shown and 'not available in full here' in shown and RID in shown
  assert hashlib.sha256(text.encode()).hexdigest()in shown
  assert f'{len(text):,} characters'in shown

def test_unicode_count_and_utf8_hash_are_original_not_excerpt():
 text='🙂漢é'*3000;shown=m.report_note(NAME,RID,text)
 assert len(shown)==4000 and hashlib.sha256(text.encode()).hexdigest()in shown and '9,000 characters'in shown
 assert m.report_note(NAME,RID,text)==shown

class Floor:
 def __init__(self):self.saved={};self.fail=False
 def add_note(self,rid,text,author):
  if self.fail:raise RuntimeError('SYNTHETIC_PRIVATE_DB_ERROR')
  assert len(text)<=4000
  if rid in self.saved:assert self.saved[rid]['text']==text
  self.saved[rid]={'id':1,'text':text,'created_at':'2026-10-16T00:00:00Z'}
  return types.SimpleNamespace(outcome='kept')
 def get_note(self,rid):return self.saved.get(rid)
def document(text):return {'request':{'request_id':RID,'conversation_id':'c-synthetic','to':'claude-code','action':'synthetic'},'events':[{'kind':'result','text':text}],'stage':'returned','closed':'returned','needs_you':[]}
def test_long_ledger_result_repeated_sync_keeps_one_bounded_note_no_original_change(tmp_path):
 text='synthetic report '*700;doc=document(text);floor=Floor();svc=m.AskService(str(tmp_path))
 with patch.object(m.ar,'for_conversation',return_value=[doc]):
  first=svc.sync({'id':'c-synthetic'},floor);second=svc.sync({'id':'c-synthetic'},floor)
 assert first['unkept']==second['unkept']==0 and first['outstanding']==0
 assert len(floor.saved)==1 and doc['events'][0]['text']==text
 assert first['notes']==second['notes']
def test_persistence_failure_signalled_then_recovery_same_id(tmp_path,caplog):
 doc=document('synthetic');floor=Floor();floor.fail=True;svc=m.AskService(str(tmp_path))
 with patch.object(m.ar,'for_conversation',return_value=[doc]):
  failed=svc.sync({'id':'c-synthetic'},floor);floor.fail=False;recovered=svc.sync({'id':'c-synthetic'},floor)
 assert failed['unkept']==1 and failed['notes']==[] and failed['outstanding']==0
 assert recovered['unkept']==0 and len(recovered['notes'])==1
 assert 'could not be kept'in caplog.text and 'PRIVATE_DB_ERROR'not in caplog.text
