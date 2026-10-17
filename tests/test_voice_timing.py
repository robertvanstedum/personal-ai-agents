"""No provider, microphone, network or real registry access."""
import logging
from types import SimpleNamespace
from core.realtime_voice import timing as t
from core.realtime_voice import controlled_session as m
from core.realtime_voice.server_openai import OpenAIControlledProvider
from core.realtime_voice.budget import VoiceBudget


def test_fake_clock_commit_asr_and_privacy(caplog):
    clock=[10.0];x=t.VoiceTiming(clock=lambda:clock[0],correlation='a'*32)
    with caplog.at_level(logging.INFO,logger=t.LOG.name):
        x.emit('input_committed',item='PRIVATE_PROVIDER_ITEM')
        clock[0]=10.375;x.emit('asr_final',item='PRIVATE_PROVIDER_ITEM')
        x.emit('asr_final',item='PRIVATE_PROVIDER_ITEM')
        x.emit('secret prompt text',item='PRIVATE_PROVIDER_ITEM')
    assert len(caplog.records)==2
    assert 'asr_after_commit_ms=375' in caplog.text
    assert 'PRIVATE_PROVIDER_ITEM' not in caplog.text and 'secret prompt' not in caplog.text


def test_logging_and_clock_failure_are_nonfatal(monkeypatch):
    x=t.VoiceTiming(clock=lambda:0)
    monkeypatch.setattr(t.LOG,'info',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('secret')))
    x.emit('session_active')
    x.clock=lambda:(_ for _ in ()).throw(RuntimeError('secret'))
    x.emit('end_start')


def test_metadata_bound():
    x=t.VoiceTiming(clock=lambda:0)
    for i in range(3000):x.emit('asr_final',item=str(i))
    assert len(x.seen)==2048


def test_first_audio_once_and_failure_isolated():
    p=OpenAIControlledProvider(SimpleNamespace());stages=[]
    p.set_timing_observer(stages.append)
    p._observe_first_audio();p._observe_first_audio()
    assert stages==['provider_first_audio_observed']
    p=OpenAIControlledProvider(SimpleNamespace())
    p.set_timing_observer(lambda _:(_ for _ in ()).throw(RuntimeError('secret')))
    p._observe_first_audio()


def test_actual_session_stage_order_keeps_asr_authority(tmp_path,caplog):
    clock=[1.0];b=VoiceBudget('sqlite:///'+str(tmp_path/'synthetic.db'),cap_usd=lambda:20)
    b.ensure_schema();j=m.Journal(b._tx);j.init();sent=[];dispatch=[]
    class Provider:
        def create(self,*args):clock[0]=2;return 'SDP','rtc_synthetic'
        def set_timing_observer(self,cb):self.cb=cb
        def attach(self,call,observe):clock[0]=3
        def send(self,event):sent.append(event)
        def hangup(self,call):pass
    s=m.ControlledSession(journal=j,budget=b,provider=Provider(),principal='synthetic_owner',conversation='synthetic_conv',
        allowance_micro=5000000,dispatch=lambda *args:dispatch.append(args),now=lambda:clock[0],canonical_command_match=lambda *_:False)
    try:
        with caplog.at_level(logging.INFO,logger=t.LOG.name):
            s.start('PRIVATE_SDP',{'model':m.MODEL,'type':'realtime','audio':{'input':{'turn_detection':{'create_response':False}}}})
            s.observe({'type':'input_audio_buffer.committed','item_id':'synthetic_item'})
            assert sent==[]
            clock[0]=3.5
            s.observe({'type':'conversation.item.input_audio_transcription.completed','item_id':'synthetic_item','transcript':'PRIVATE_OWNER_TEXT',
                'usage':{'type':'tokens','input_tokens':1,'output_tokens':1,'input_token_details':{'text_tokens':0,'audio_tokens':1}}})
            assert sent==[{'type':'response.create'}] and not dispatch
            s.end('user_ended')
        stages=[r.args[1] for r in caplog.records]
        assert stages==['provider_create_start','provider_create_end','sideband_attach_start','sideband_attach_end','session_active','input_committed','asr_final','response_requested','end_start','end_confirmed']
        assert all(secret not in caplog.text for secret in ('PRIVATE_SDP','PRIVATE_OWNER_TEXT','synthetic_owner','synthetic_conv'))
    finally:s.end('user_ended');s.worker.shutdown(wait=True)


def test_native_context_backend_and_receipt_separate_preserve_text(monkeypatch,caplog):
    from domains.cos import confer_service as c
    clock=[0.0]
    monkeypatch.setattr(c,'VoiceTiming',lambda **kw:t.VoiceTiming(clock=lambda:clock[0],**kw))
    def context():clock[0]=8.0;return {'system_prompt':'existing'}
    def backend(text,context,policy):
        assert text=='PRIVATE_CANONICAL_OWNER_TEXT'
        clock[0]=11.0;return 'reply'
    def receipt(_):clock[0]=12.0;return {}
    svc=c.ConferTurnService(call_backend=backend,build_context=context,increment_chat=lambda:None,
        backend_metadata=lambda:('native','configured'),get_routing_receipt=receipt)
    with caplog.at_level(logging.INFO,logger=t.LOG.name):
        result=svc.handle(c.ConferTurnRequest(text='PRIVATE_CANONICAL_OWNER_TEXT',channel='api_text'))
    assert result.reply=='reply'
    assert [r.args[1] for r in caplog.records]==['context_start','context_end','native_start','native_end','routing_receipt_start','routing_receipt_end']
    assert [r.args[3] for r in caplog.records]==[0,8000,8000,11000,11000,12000]
    assert 'PRIVATE_CANONICAL_OWNER_TEXT' not in caplog.text


def test_gateway_failure_fixed_metadata_preserves_exception(caplog):
    from domains.cos.backends.openclaw_backend import OpenClawBackend
    obj=object.__new__(OpenClawBackend)
    def fail(**kw):raise RuntimeError('PRIVATE_PROVIDER_ERROR')
    obj._post_turn=fail
    import pytest
    with caplog.at_level(logging.INFO,logger=t.LOG.name):
        with pytest.raises(RuntimeError,match='PRIVATE_PROVIDER_ERROR'):
            obj._call_backend_payload('PRIVATE_PROMPT',{'confer':{'receipt_id':'b'*32}}, {})
    assert [r.args[1] for r in caplog.records]==['gateway_request_start','gateway_request_failed']
    assert 'PRIVATE_PROVIDER_ERROR' not in caplog.text and 'PRIVATE_PROMPT' not in caplog.text


def test_failed_initial_clock_never_reports_system_uptime_as_latency(caplog):
    def broken():raise RuntimeError('clock unavailable')
    timer=t.VoiceTiming(clock=broken)
    timer.clock=lambda:999999.0
    with caplog.at_level(logging.INFO,logger=t.LOG.name):
        timer.emit('session_active')
    assert not caplog.records and timer.seen=={}
