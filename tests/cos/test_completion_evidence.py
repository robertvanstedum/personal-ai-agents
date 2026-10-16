"""Completion metadata is connector evidence, never inferred from answer text."""
import pytest
from domains.cos.backends.openclaw_backend import OpenClawBackend
from domains.cos.confer_service import ConferTurnRequest, ConferTurnService

@pytest.mark.parametrize("finish",["stop",None,"length","tool_calls"])
def test_connector_only_marks_an_explicit_final_response(finish):
    backend=object.__new__(OpenClawBackend)
    backend._agent_id="cos-agent-a"
    payload={"object":"chat.completion","model":"openclaw/cos-agent-a","choices":[
        {"finish_reason":finish,"message":{"role":"assistant","content":"Actual reply"}}]}
    backend._call_backend_payload=lambda *args:payload
    context={"completion_evidence":{"state":"complete","source":"openclaw_connector"}}
    assert backend.call_backend("Check",context,{})=="Actual reply"
    if finish=="stop":assert context["completion_evidence"]["state"]=="complete"
    else:assert "completion_evidence" not in context

@pytest.mark.parametrize("verified",[True,False])
def test_confer_exposes_only_explicit_completion_marker(verified):
    def call(prompt,context,policy):
        if verified:context["completion_evidence"]={"state":"complete","source":"openclaw_connector"}
        return "Actual reply"
    service=ConferTurnService(call_backend=call,build_context=lambda:{},increment_chat=lambda:None,backend_metadata=lambda:("OpenClaw","test"))
    payload=service.handle(ConferTurnRequest("Check","api_text")).public_dict()
    assert ("completion" in payload)==verified


@pytest.mark.parametrize("field,value",[("object","other"),("model","other"),("role","tool"),("tool_calls",[{}]),("refusal","refused")])
def test_nonfinal_or_wrong_connector_payload_never_marks_complete(field,value):
    backend=object.__new__(OpenClawBackend);backend._agent_id="cos-agent-a"
    payload={"object":"chat.completion","model":"openclaw/cos-agent-a","choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"Reply"}}]}
    if field in ("object","model"):payload[field]=value
    else:payload["choices"][0]["message"][field]=value
    backend._call_backend_payload=lambda *args:payload
    context={}
    backend.call_backend("Check",context,{})
    assert "completion_evidence" not in context
