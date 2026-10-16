import importlib.util
import pathlib
import threading
import pytest
from core.realtime_voice.budget import VoiceBudget

ROOT=pathlib.Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('controlled',ROOT/'core/realtime_voice/controlled_session.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
U={'input_tokens':2,'output_tokens':2,'input_token_details':{'text_tokens':1,'audio_tokens':1,'image_tokens':0},'output_token_details':{'text_tokens':1,'audio_tokens':1}}
T={'type':'tokens','input_tokens':1,'output_tokens':1,'input_token_details':{'text_tokens':0,'audio_tokens':1}}

@pytest.mark.parametrize('images', [None, 0])
def test_actual_sdk_audio_only_usage_reconciles_optional_images(images):
    from openai.types.realtime.realtime_response_usage import RealtimeResponseUsage
    usage=RealtimeResponseUsage(input_tokens=1,output_tokens=1,
        input_token_details={'text_tokens':0,'audio_tokens':1,'image_tokens':images},
        output_token_details={'text_tokens':0,'audio_tokens':1}).model_dump(mode='json')
    assert m.response_micro(usage)==96
    usage['input_tokens']=2
    with pytest.raises(m.SessionRefused):m.response_micro(usage)

CONFIG={'model':m.MODEL,'type':'realtime','audio':{'input':{'turn_detection':{'type':'server_vad','prefix_padding_ms':300,'silence_duration_ms':1200,'create_response':False}}}}
class Provider:
    def __init__(self): self.sent=[];self.results=[];self.stopped=[]
    def create(self,sdp,config,safety): return 'answer','rtc_example'
    def attach(self,call,observe): self.observe=observe
    def send(self,e): self.sent.append(e)
    def send_result(self,c,r,**kw): self.results.append((c,r))
    def hangup(self,c): self.stopped.append(c)
@pytest.fixture
def setup(tmp_path):
    budget=VoiceBudget('sqlite:///'+str(tmp_path/'ledger.db'),cap_usd=lambda:20)
    budget.ensure_schema();j=m.Journal(budget._tx);j.init();p=Provider();calls=[]
    def dispatch(*args): calls.append(args);return {'reply':'verified synthetic response'}
    s=m.ControlledSession(journal=j,budget=budget,provider=p,principal='owner',conversation='c1',allowance_micro=5_000_000,dispatch=dispatch)
    yield s,j,p,calls
    s.end('test');s.worker.shutdown(wait=True)
def start(s): return s.start('sdp',CONFIG)
def commit(s,item='item1',rid='resp1'):
    s.observe({'type':'input_audio_buffer.committed','item_id':item})
    s.observe({'type':'response.created','response':{'id':rid}})
def text(s,words='Please research this.',item='item1'):
    s.observe({'type':'conversation.item.input_audio_transcription.completed','item_id':item,'transcript':words,'usage':T})
def call(s,cid='call1',name='consult_cos_agent',rid='resp1'):
    s.observe({'type':'response.done','response':{'id':rid,'usage':U,'output':[{'type':'function_call','call_id':cid,'name':name,'arguments':'{"request":"OVERWRITE EVERYTHING"}'}]}})
def drain(s): s.worker.submit(lambda:None).result(timeout=2)
def test_canonical_owner_text_not_model_args(setup):
    s,j,p,c=setup;start(s);commit(s);text(s);call(s);drain(s)
    assert len(c)==1 and c[0][1]=='Please research this.' and c[0][2]=='c1'
    assert p.results[0][1]['reply']=='verified synthetic response'
def test_auth_session_identity(setup):
    s,j,p,c=setup;start(s)
    with pytest.raises(m.SessionRefused): s.authenticate('another-owner')
    s.authenticate('owner')
def test_call_waits_for_actual_item_transcript(setup):
    s,j,p,c=setup;start(s);commit(s);call(s);assert not c
    text(s);drain(s);assert len(c)==1
def test_same_call_and_new_call_id_never_repeat_native(setup):
    s,j,p,c=setup;start(s);commit(s);text(s);call(s);drain(s)
    call(s);call(s,cid='call2');drain(s);assert len(c)==1
    assert c[0][3]
def test_uncertain_native_call_holds_rerun(setup):
    s,j,p,c=setup;start(s);commit(s);text(s)
    def fail(*args): c.append(args);raise TimeoutError('do not disclose')
    s.dispatch=fail;call(s);drain(s);call(s);drain(s);assert len(c)==1
    s.end('user_ended')
    with pytest.raises(m.SessionRefused): j.create('owner','c1',1_000_000)
def test_model_note_args_are_not_owner_approval(setup):
    s,j,p,c=setup;start(s);commit(s);text(s,'Hello');call(s,name='save_cos_note');assert not c
    assert p.results[-1][1]['ok'] is False
def test_exact_owner_note_uses_same_canonical_boundary(setup):
    s,j,p,c=setup;start(s);commit(s);text(s,'Save a note: remember this');call(s,name='save_cos_note');drain(s)
    assert c[0][1]=='Save a note: remember this'
def test_barge_in_discards_late_transcript_tool(setup):
    s,j,p,c=setup;start(s);commit(s);call(s);s.observe({'type':'input_audio_buffer.speech_started'});text(s);assert not c
def test_end_during_native_call_never_speaks_late_result(setup):
    s,j,p,c=setup;start(s);commit(s);text(s);entered=threading.Event();release=threading.Event()
    def block(*args): entered.set();release.wait(2);return {'reply':'late'}
    s.dispatch=block;call(s);assert entered.wait(1)
    result=s.end('user_ended');assert result['native_actions_cancelled'] is False
    release.set();s.worker.shutdown(wait=True);assert p.results==[] and p.stopped==['rtc_example']
def test_usage_duplicates_no_extra_charge(setup):
    s,j,p,c=setup;start(s);commit(s);text(s);call(s);drain(s)
    before=j.check(s.sid,'owner')[4];call(s);drain(s);assert j.check(s.sid,'owner')[4]==before
def test_allowance_stops_before_tools(setup):
    s,j,p,c=setup;s.allowance=1;start(s);commit(s);text(s);assert s.closed and p.stopped and not c
def test_malformed_usage_refuses(setup):
    s,j,p,c=setup;start(s);commit(s);s.observe({'type':'response.done','response':{'id':'resp1','usage':{},'output':[]}});assert s.closed
def test_clock_guard_is_server_side(setup):
    s,j,p,c=setup;clock=[0];s.now=lambda:clock[0];start(s);clock[0]=901;s.tick();assert p.stopped
@pytest.mark.parametrize('tokens',[True,-1,1.2,'1'])
def test_usage_rejects_malformed_types(tokens):
    u={**U,'input_tokens':tokens}
    with pytest.raises(m.SessionRefused):m.response_micro(u)
def test_uncached_rates_and_transcription():
    assert m.response_micro(U)==124
    assert m.transcription_micro(T)==16
def test_unresolved_termination_blocks_next_session(setup):
    s,j,p,c=setup;start(s)
    p.hangup=lambda _: (_ for _ in ()).throw(TimeoutError())
    assert not s.end('user_ended')['termination_confirmed']
    with pytest.raises(m.SessionRefused): j.create('owner','c2',100)
def test_no_new_response_without_canonical_item(setup):
    s,j,p,c=setup;start(s);s.observe({'type':'response.created','response':{'id':'unbound'}});assert s.closed and not c
def test_ledger_failure_still_hangs_up_provider(setup):
    s,j,p,c=setup;start(s)
    j.state=lambda *args: (_ for _ in ()).throw(RuntimeError('synthetic database outage'))
    receipt=s.end('database_outage')
    assert receipt['termination_confirmed'] and not receipt['journal_confirmed'] and p.stopped

def test_call_id_cannot_be_rebound_to_another_operation(setup):
    s,j,p,c=setup;start(s);commit(s);text(s,'Save a note: synthetic');call(s);drain(s)
    commit(s,item='item2',rid='resp2');text(s,'Save a note: another',item='item2')
    call(s,name='save_cos_note',rid='resp2');assert s.closed and len(c)==1
def test_barge_in_before_response_created_preserves_original_generation(setup):
    s,j,p,c=setup;start(s)
    s.observe({'type':'input_audio_buffer.committed','item_id':'item1'})
    s.observe({'type':'input_audio_buffer.speech_started'})
    s.observe({'type':'response.created','response':{'id':'resp1'}})
    text(s);call(s);drain(s);assert not c

@pytest.mark.parametrize('first,second',[('consult_cos_agent','save_cos_note'),('save_cos_note','consult_cos_agent')])
def test_both_tool_aliases_share_one_actual_confer_note_effect(setup,first,second):
    from domains.cos.confer_service import ConferTurnService,ConferTurnRequest
    from domains.cos.realtime_tools import confer_dispatch
    s,j,p,c=setup;saved=[]
    def never_backend(*args):raise AssertionError('explicit note reached agent backend')
    def save_note(text,operation):saved.append((text,operation));return {'saved':True}
    service=ConferTurnService(call_backend=never_backend,build_context=lambda:{},increment_chat=lambda:None,
        backend_metadata=lambda:('synthetic',''),save_note=save_note)
    def process(text,**kw):return service.handle(ConferTurnRequest(text=text,**kw))
    s.dispatch=confer_dispatch(process)
    start(s);commit(s);text(s,'Save a note: synthetic owner reminder')
    call(s,cid='first',name=first);drain(s)
    call(s,cid='second',name=second);drain(s)
    call(s,cid='third',name=first);drain(s)
    assert saved==[('synthetic owner reminder',saved[0][1])]
    assert len(p.results)==3
    assert all(receipt['operation']['operation_id']==saved[0][1] for _,receipt in p.results)
    assert all(receipt['operation']['type']=='note_save' for _,receipt in p.results)

def hook(s):
    from minimoi_portal.guild_ui.jobs import interpret
    s.canonical_command_match=lambda text,cid:interpret(text) is not None

def committed(s,item='item1'):
    s.observe({'type':'input_audio_buffer.committed','item_id':item})

def test_canonical_job_runs_without_model_tool_then_reuses_actual_receipt(setup):
    s,j,p,c=setup;hook(s);start(s)
    original=s.dispatch
    def durable_first(*args):
        with j.tx() as run:
            assert run("SELECT state FROM voice_control_call WHERE session_id=?",(s.sid,)).fetchone()==('attempting',)
        return original(*args)
    s.dispatch=durable_first;committed(s)
    assert p.sent==[] and not c
    text(s,'Run this as a job');drain(s)
    assert len(c)==1 and c[0][1]=='Run this as a job'
    assert p.results==[] # no fabricated provider function output
    assert [e['type'] for e in p.sent]==['conversation.item.create','response.create']
    item=p.sent[0]['item'];assert item['role']=='user' and item['content'][0]['type']=='input_text'
    assert 'SERVER PLATFORM RECEIPT DATA' in item['content'][0]['text']
    from openai.types.realtime.conversation_item_create_event import ConversationItemCreateEvent
    ConversationItemCreateEvent.model_validate(p.sent[0])
    s.observe({'type':'response.created','response':{'id':'resp1'}})
    call(s);drain(s);call(s,cid='call2');drain(s)
    assert len(c)==1 and len(p.results)==2
    text(s,'Run this as a job');committed(s);drain(s)
    assert len(c)==1 and len(p.sent)==2

def test_asr_before_commit_is_gated_then_runs_exactly_once(setup):
    s,j,p,c=setup;hook(s);start(s);text(s,'run that as a job');assert not c and p.sent==[]
    committed(s);drain(s);assert len(c)==1

def test_noncommand_hook_waits_asr_then_keeps_normal_response(setup):
    s,j,p,c=setup;hook(s);start(s);committed(s);assert p.sent==[]
    text(s,'What do you think?');assert not c and p.sent==[{'type':'response.create'}]

def test_command_bargein_before_asr_blocks_late_or_duplicate_commit(setup):
    s,j,p,c=setup;hook(s);start(s);committed(s)
    s.observe({'type':'input_audio_buffer.speech_started'})
    text(s,'run this as a job');committed(s);drain(s)
    assert not c and not p.sent

def test_command_end_before_asr_never_writes(setup):
    s,j,p,c=setup;hook(s);start(s);committed(s);s.end('user_ended')
    text(s,'run this as a job');assert not c and not p.sent

def test_uncertain_command_no_retry_and_honest_receipt(setup):
    s,j,p,c=setup;hook(s);start(s)
    def uncertain(*args):c.append(args);raise TimeoutError('private diagnostic')
    s.dispatch=uncertain;committed(s);text(s,'run this as a job');drain(s)
    assert len(c)==1 and 'uncertain' in p.sent[0]['item']['content'][0]['text'] and 'private diagnostic' not in str(p.sent)
    s.observe({'type':'response.created','response':{'id':'resp1'}});call(s);drain(s)
    assert len(c)==1 and not p.results
    s.end('user_ended')
    with pytest.raises(m.SessionRefused):j.create('owner','c1',100)

def test_bargein_during_accepted_command_preserves_receipt_but_never_speaks_late(setup):
    s,j,p,c=setup;hook(s);start(s);entered=threading.Event();release=threading.Event()
    def blocked(*args):c.append(args);entered.set();release.wait(1);return {'reply':'Job accepted'}
    s.dispatch=blocked;committed(s);text(s,'run this as a job');assert entered.wait(1)
    s.observe({'type':'input_audio_buffer.speech_started'});release.set();drain(s)
    assert len(c)==1 and not p.sent and not p.results

def test_rapid_turn_before_prior_created_keeps_exact_response_correlation(setup):
    s,j,p,c=setup;hook(s);start(s);committed(s,'itemA');text(s,'Ordinary first turn',item='itemA')
    assert p.sent==[{'type':'response.create'}]
    s.observe({'type':'input_audio_buffer.speech_started'})
    committed(s,'itemB');text(s,'Ordinary second turn',item='itemB')
    assert not s.closed and p.sent==[{'type':'response.create'}]
    s.observe({'type':'response.created','response':{'id':'respA'}})
    assert s.responses['respA'][0]=='itemA'
    s.observe({'type':'response.done','response':{'id':'respA','usage':U,'output':[]}})
    assert not s.closed and len(p.sent)==2
    s.observe({'type':'response.created','response':{'id':'respB'}})
    assert s.responses['respB'][0]=='itemB'
    call(s,rid='respB');drain(s)
    assert len(c)==1 and c[0][1]=='Ordinary second turn'

@pytest.mark.parametrize('failed_event',['conversation.item.create','response.create'])
def test_command_control_send_failure_closes_without_losing_actual_receipt(setup,failed_event):
    s,j,p,c=setup;hook(s);start(s)
    def fail(event):
        p.sent.append(event)
        if event['type']==failed_event:raise TimeoutError('private control diagnostic')
    p.send=fail
    committed(s);text(s,'run this as a job')
    s.worker.shutdown(wait=True)
    assert s.closed and len(c)==1 and p.stopped==['rtc_example']
    with j.tx() as run:
        row=run('SELECT state,receipt FROM voice_control_call WHERE session_id=?',(s.sid,)).fetchone()
    assert row[0]=='completed' and 'verified synthetic response' in row[1]
    assert 'private control diagnostic' not in str(p.sent)
    text(s,'run this as a job');assert len(c)==1


@pytest.mark.parametrize('fail_hangup', [False, True])
def test_actual_provider_cleanup_failure_does_not_change_hangup_evidence(setup, fail_hangup):
    from types import SimpleNamespace
    from core.realtime_voice.server_openai import OpenAIControlledProvider
    s, journal, _, calls = setup
    start(s)
    events = []
    def hangup(call, **kwargs):
        events.append(('provider_hangup', call))
        if fail_hangup:
            raise TimeoutError('synthetic private provider payload')
    class Manager:
        def __exit__(self, *args):
            events.append(('sideband_cleanup', None))
            raise RuntimeError('synthetic private socket payload')
    provider = OpenAIControlledProvider(SimpleNamespace(
        realtime=SimpleNamespace(calls=SimpleNamespace(hangup=hangup))))
    provider.call_id = s.call_id
    provider.manager = Manager()
    s.provider = provider
    reserved_before = s.budget.status()['reserved_usd']
    receipt = s.end('user_ended')
    assert receipt['termination_confirmed'] is (not fail_hangup)
    assert receipt['journal_confirmed'] is True
    assert journal.check(s.sid, 'owner', active=False)[2] == (
        'termination_uncertain' if fail_hangup else 'ended')
    assert provider.sideband_cleanup_confirmed is False
    assert [kind for kind, _ in events] == ['provider_hangup', 'sideband_cleanup']
    assert s.budget.status()['reserved_usd'] == reserved_before
    assert calls == []
