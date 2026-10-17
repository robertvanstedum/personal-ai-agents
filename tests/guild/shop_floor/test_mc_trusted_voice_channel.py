"""Synthetic trusted-host propagation; no provider/native network calls."""
from dataclasses import replace
import pytest

from minimoi_portal.guild_ui import api
from minimoi_portal.guild_ui.mc import TurnRequest
from minimoi_portal.guild_ui.mc.cos import MANAGED_VOICE_CHANNEL
from test_mc_cos_backend import _backend, _cos_turned, _keep, API, REQ
from floor_db_helpers import floor_db, floored, keyed  # noqa:F401
from floor_helpers import load_portal, write_headers  # noqa:F401
from test_mc_managed_realtime import ready, start  # noqa:F401


@pytest.mark.parametrize('context', [{'channel':'html_voice'}, {'host_channel':'html_voice'}, {'page':'voice'}, {'host_channel':True}])
def test_untrusted_context_cannot_select_native_voice(context):
    backend,calls=_backend()
    assert backend.turn(replace(REQ,context=context)).status=='answered'
    assert calls['post'][0][1]['channel']=='api_text'


@pytest.mark.parametrize('text', ['/new','Save a note: exact words','Current status?'])
def test_trusted_voice_changes_only_channel_and_preserves_exact_text_and_ids(text):
    backend,calls=_backend()
    req=replace(REQ,text=text,context={'host_channel':MANAGED_VOICE_CHANNEL})
    assert backend.turn(req).status=='answered'
    body=calls['post'][0][1]
    assert body=={'text':text,'channel':'html_voice','conversation_id':backend._session(REQ.conversation_id),'request_id':REQ.note_request_id}


def test_trusted_voice_does_not_bypass_private_refusal():
    backend,calls=_backend()
    result=backend.turn(replace(REQ,context={'mode':'private','host_channel':MANAGED_VOICE_CHANNEL}))
    assert result.status=='refused' and calls['post']==[]


def test_actual_typed_route_ignores_channel_in_browser_body_and_kept_context(floored):
    calls=_cos_turned(floored);client=floored.owner();token=floored.csrf(client)
    response=client.post(f'{API}/notes',json=keyed(text='Typed exact words',context={'page':'voice','channel':'html_voice','host_channel':'html_voice'}),headers=write_headers(token))
    assert response.status_code==200
    note=response.json['note']
    response=client.post(f'{API}/mc/turns',json={'note_request_id':note['request_id'],'record_mode':'on_record','channel':'html_voice','host_channel':'html_voice','context':{'host_channel':'html_voice'}},headers=write_headers(token))
    assert response.status_code==200
    assert len(calls['post'])==1
    assert calls['post'][0][1]['channel']=='api_text'
    assert calls['post'][0][1]['text']=='Typed exact words'


def test_actual_turn_boundary_propagates_only_explicit_host_marker(floored,monkeypatch):
    calls=_cos_turned(floored);client=floored.owner();token=floored.csrf(client)
    actual=api.run_mc_turn
    def host_call(*args,**kwargs):
        return actual(*args,**kwargs,host_channel=MANAGED_VOICE_CHANNEL)
    monkeypatch.setattr(api,'run_mc_turn',host_call)
    note=_keep(client,token,'Actual spoken words')
    response=client.post(f'{API}/mc/turns',json={'note_request_id':note['request_id'],'record_mode':'on_record'},headers=write_headers(token))
    assert response.status_code==200 and response.json['status']=='answered'
    assert calls['post'][0][1]['channel']=='html_voice'
    assert calls['post'][0][1]['text']=='Actual spoken words'
    assert calls['post'][0][1]['request_id']==note['request_id']


def test_actual_managed_host_supplies_marker_without_changing_canonical_note(ready):
    r=ready;s,_=start(r)
    out=s.dispatch('consult_cos_agent','Exact canonical spoken words','c-123456abcdef','operation-channel')
    assert out['reply']=='Synthetic native reply'
    assert r.calls[0]['host_channel'] is MANAGED_VOICE_CHANNEL
    assert r.calls[0]['note']['text']=='Exact canonical spoken words'
    assert r.calls[0]['note_id']=='operation-channel'


def test_selected_channel_reaches_existing_confer_concision_and_exact_commands():
    from domains.cos.confer_service import ConferTurnRequest, ConferTurnService
    from minimoi_portal.guild_ui.mc.cos import ChiefOfStaffBackend
    from test_mc_cos_backend import Resp
    native=[];saved=[];reset=[]
    def call_backend(text,context,policy):
        native.append((text,context,policy));return 'Actual synthetic answer.'
    service=ConferTurnService(call_backend=call_backend,build_context=lambda:{'system_prompt':'Existing authority'},increment_chat=lambda:None,
        backend_metadata=lambda:('OpenClaw','synthetic'),save_note=lambda text,rid:(saved.append((text,rid)) or {'saved':True}),
        reset_conversation=lambda cid:(reset.append(cid) or True))
    def post(url,*,json,timeout):
        answer=service.handle(ConferTurnRequest(**json));return Resp(200,{'reply':answer.reply})
    backend=ChiefOfStaffBackend('http://synthetic.invalid',http_post=post)
    req=replace(REQ,text='What next?',note_request_id='11111111-1111-1111-1111-111111111111',context={'host_channel':MANAGED_VOICE_CHANNEL})
    assert backend.turn(req).status=='answered'
    assert native[-1][0]=='What next?' and native[-1][1]['confer']['input_channel']=='html_voice'
    assert 'one to three short' in native[-1][1]['system_prompt']
    assert native[-1][1]['system_prompt'].startswith('Existing authority')
    assert backend.turn(replace(req,context={})).status=='answered'
    assert native[-1][1]['confer']['input_channel']=='api_text' and 'one to three short' not in native[-1][1]['system_prompt']
    native.clear()
    assert backend.turn(replace(req,text='/new')).status=='answered'
    assert backend.turn(replace(req,text='Save a note: exact retained words')).status=='answered'
    assert not native and len(reset)==1
    assert saved==[('exact retained words',req.note_request_id)]
