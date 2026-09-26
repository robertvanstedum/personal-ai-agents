"""Connector contract tests. Real local Flask API, no model or dev deployment."""
import json
from pathlib import Path
import sys
from uuid import uuid4

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"integration"))
from cos_records_bridge import RoomBridgeError,RoomClient,dispatch,parse_command
from app import create_app


class LocalSession:
    def __init__(self,app):self.app=app.test_client();self.calls=[]
    def request(self,method,url,headers,json,**kwargs):
        from urllib.parse import urlsplit
        self.calls.append((method,url))
        response=self.app.open(urlsplit(url).path+("?"+urlsplit(url).query if urlsplit(url).query else ""),method=method,headers=headers,json=json)
        class Response:
            status_code=response.status_code
            def json(self):return response.get_json()
        return Response()


@pytest.fixture
def bridge(tmp_path):
    app=create_app(tmp_path/"records",testing=True)
    store=app.extensions["records_store"]
    room=store.create_room("robert","create",dict(title="Prototype plan",purpose="Synthetic connector test",mode="meeting",recording_acknowledged=True))["result"]["id"]
    token=store.add_principal("robert","client",dict(id="cos-dev",label="Development CoS"))["access_token"]
    store.membership("robert","invite",room,dict(actor="cos-dev",role="contributor"))
    key=tmp_path/"client-key";key.write_text(token);key.chmod(0o600)
    config=tmp_path/"config.json";config.write_text(json.dumps(dict(url="http://host.lima.internal:18880",actor_id="cos-dev",token_file=str(key))));config.chmod(0o600)
    client=RoomClient(config,session=LocalSession(app))
    return store,room,client,config,key


def test_ordinary_conversation_never_loads_config_or_records():
    assert dispatch("Guten Tag; how do I say this?",config_path="/does/not/exist") is None
    assert dispatch("Maybe we should have a meeting next week",config_path="/does/not/exist") is None


def test_list_read_and_real_platform_acknowledgement(bridge):
    store,room,client,_,_=bridge
    assert "Prototype plan" in dispatch("/rooms",client=client)["reply"]
    operation=str(uuid4())
    result=dispatch("join the meeting I just opened",operation,client=client)
    event=store.room("robert",room)["events"][-1]
    assert event["actor"]=="cos-dev" and "not a background listener" in event["body"]
    assert result["operation"]["status"]=="committed"
    assert dispatch("join the meeting I just opened",operation,client=client)==result
    read=dispatch("/read-room Prototype plan",client=client)
    assert event["id"] in read["reply"]
    assert read["operation"]["agent_execution"] is False


def test_post_is_explicit_relay_not_impersonated_owner(bridge):
    store,room,client,_,_=bridge
    dispatch(f"/room-post {room} A synthetic message",str(uuid4()),client=client)
    event=store.room("robert",room)["events"][-1]
    assert event["actor"]=="cos-dev" and event["context_class"]=="external_source"
    assert "not a model-generated response" in event["body"]


def test_reuse_with_changed_content_is_not_a_new_write(bridge):
    store,room,client,_,_=bridge
    operation=str(uuid4())
    dispatch(f"/room-post {room} First",operation,client=client)
    before=len(store.room("robert",room)["events"])
    with pytest.raises(RoomBridgeError,match="different room content"):
        dispatch(f"/room-post {room} Second",operation,client=client)
    assert len(store.room("robert",room)["events"])==before


def test_paused_or_revoked_cannot_join_or_replay(bridge):
    store,room,client,_,_=bridge
    operation=str(uuid4())
    dispatch(f"/join-room {room}",operation,client=client)
    store.state("robert","pause",room,dict(state="paused",version=1,checkpoint="Stop"))
    with pytest.raises(RoomBridgeError,match="not recording"):
        dispatch(f"/join-room {room}",str(uuid4()),client=client)
    store.membership("robert","remove",room,dict(actor="cos-dev",role="remove"))
    with pytest.raises(RoomBridgeError,match="No unique"):
        dispatch(f"/join-room {room}",operation,client=client)


def test_ambiguous_meetings_require_choice(bridge):
    store,room,client,_,_=bridge
    second=store.create_room("robert","second",dict(title="Other meeting",purpose="Synthetic",mode="meeting",recording_acknowledged=True))["result"]["id"]
    store.membership("robert","second-invite",second,dict(actor="cos-dev",role="contributor"))
    with pytest.raises(RoomBridgeError,match="No unique"):
        dispatch("join the meeting I just opened",str(uuid4()),client=client)


def test_owner_credential_and_remote_target_refused(bridge):
    store,room,client,config,key=bridge
    key.write_text(store.owner_key)
    with pytest.raises(RoomBridgeError,match="does not belong"):
        dispatch("/rooms",client=RoomClient(config,session=client.session))
    config.write_text(json.dumps(dict(url="https://example.com",actor_id="cos-dev",token_file=str(key))))
    with pytest.raises(RoomBridgeError,match="restricted"):
        RoomClient(config)


def test_write_requires_stable_request_id(bridge):
    _,room,client,_,_=bridge
    with pytest.raises(RoomBridgeError,match="stable request_id"):
        dispatch(f"/join-room {room}",client=client)


def test_no_connected_claim_when_configuration_missing():
    with pytest.raises(RoomBridgeError,match="configuration is unavailable"):
        dispatch("/rooms",config_path="/does/not/exist")


def test_committed_post_lost_response_recovers_once(bridge):
    store,room,client,_,_=bridge
    original=client.session.request
    lost=[False]
    def request(method,url,**kwargs):
        response=original(method,url,**kwargs)
        if method=="POST" and not lost[0]:
            lost[0]=True
            import requests
            raise requests.ConnectionError("Synthetic response loss")
        return response
    client.session.request=request
    operation=str(uuid4())
    with pytest.raises(RoomBridgeError,match="may have committed"):
        dispatch(f"/join-room {room}",operation,client=client)
    result=dispatch(f"/join-room {room}",operation,client=client)
    assert result["operation"]["status"]=="committed"
    assert sum(e["actor"]=="cos-dev" for e in store.room("robert",room)["events"])==1


def test_staged_hook_owner_gate_and_ordinary_passthrough(monkeypatch):
    """Execute the actual added hook lines, without touching the live service."""
    import ast
    from datetime import datetime,timezone
    from types import ModuleType,SimpleNamespace
    from flask import Flask,request
    import cos_records_bridge
    patch=(Path(__file__).resolve().parents[1]/"integration"/"dev_cos_hook.patch").read_text()
    added="\n".join(line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++"))
    source="def hook(text, request_id=None, conversation_id='owner', channel='html_text'):\n"+added+"\n    return 'ordinary'\n"
    ast.parse(source)
    calls=[]
    fake=ModuleType("domains.cos.cos_records_bridge")
    fake.parse_command=cos_records_bridge.parse_command
    fake.RoomBridgeError=RoomBridgeError
    fake.dispatch=lambda *a: calls.append(a) or ({"reply":"Acknowledged","operation":{}} if parse_command(a[0]) else None)
    monkeypatch.setitem(sys.modules,"domains.cos.cos_records_bridge",fake)
    confer=ModuleType("domains.cos.confer_service");confer.ConferTurnResult=lambda **kw:SimpleNamespace(**kw)
    monkeypatch.setitem(sys.modules,"domains.cos.confer_service",confer)
    namespace=dict(ConferOperationFailed=RuntimeError,resolve_user_id=lambda req:req.headers.get("X-Minimoi-Auth-Id"),
                   request=request,_inc_chat=lambda:None,datetime=datetime,timezone=timezone)
    exec(compile(source,"<staged dev hook>","exec"),namespace)
    hook=namespace["hook"];app=Flask("hook-test")
    assert hook("Ordinary chat")=="ordinary"
    calls.clear()
    with app.test_request_context(headers={"X-Minimoi-User-Tier":"guest","X-Minimoi-Auth-Id":"2"}):
        with pytest.raises(RuntimeError,match="authenticated owner"):hook("/rooms")
    with app.test_request_context():
        with pytest.raises(RuntimeError,match="authenticated owner"):hook("/rooms")
    assert not calls
    with app.test_request_context(headers={"X-Minimoi-User-Tier":"owner","X-Minimoi-Auth-Id":"1"}):
        assert hook("/rooms").reply=="Acknowledged"
