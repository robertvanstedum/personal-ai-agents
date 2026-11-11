import json
from types import SimpleNamespace
import pytest
from core.lead_wake.review import GatewayReview, Route, ReviewUnavailable

PACKET={'lead':'mc','task_id':'TK-demo','result_digest':'sha256:'+'a'*64}
RESULT={'verdict':'approve','reasons':'The cited file identifies the function.','answer':'The helper found the function in file.py.'}


def gateway(reply=None):
    calls=[]
    def post(url,**kwargs):
        calls.append((url,kwargs))
        return SimpleNamespace(status_code=200,json=lambda:reply or {
            'choices':[{'message':{'role':'assistant','content':json.dumps(RESULT)},'finish_reason':'stop'}]})
    return GatewayReview(url='http://gateway.test/v1',key='synthetic',
                         route=Route('api-openai-low','minimoi-openai-low','low'),http_post=post),calls


def test_review_has_no_tools_session_or_owner_binding():
    g,calls=gateway()
    assert g(PACKET,{'kept_request':'Locate function','helper_result':'file.py'})==RESULT
    body=calls[0][1]['json']
    assert set(body)=={'model','reasoning_effort','stream','max_tokens','messages'}
    assert body['model']=='minimoi-openai-low' and body['max_tokens']==1200
    assert calls[0][1]['allow_redirects'] is False
    assert len(calls)==1


@pytest.mark.parametrize('reply',[
    {'choices':[{'message':{'role':'assistant','content':json.dumps(RESULT),'tool_calls':[{'id':'bad'}]},'finish_reason':'stop'}]},
    {'choices':[{'message':{'role':'assistant','content':json.dumps(RESULT)},'finish_reason':'length'}]},
    {'choices':[{'message':{'role':'assistant','content':'{"verdict":"approve","verdict":"changes_requested"}'},'finish_reason':'stop'}]},
])
def test_invalid_or_action_shaped_response_is_never_retried(reply):
    g,calls=gateway(reply)
    with pytest.raises(ReviewUnavailable):g(PACKET,{})
    assert len(calls)==1


def test_oversize_context_never_dispatches():
    g,calls=gateway()
    with pytest.raises(ReviewUnavailable):g(PACKET,{'text':'😀'*24000})
    assert calls==[]


def test_credentials_are_scrubbed_without_damaging_evidence_json():
    g,calls=gateway()
    g(PACKET,{'request':'Inspect Authorization: Bearer secret_123456789',
              'helper_result':{'answer':'The next field remains intact.'}})
    raw=calls[0][1]['json']['messages'][1]['content']
    kept=json.loads(raw)
    assert 'secret_123456789' not in raw
    assert kept['binding']==PACKET
    assert kept['evidence']['helper_result']['answer']=='The next field remains intact.'


def test_redaction_expansion_and_system_overhead_are_in_final_budget(monkeypatch):
    import core.lead_wake.review as module
    g,calls=gateway()
    monkeypatch.setattr(module,'_scrub_evidence',lambda value:{'expanded':'x'*module.MAX_INPUT_BYTES})
    with pytest.raises(ReviewUnavailable):g(PACKET,{'small':'value'})
    assert calls==[]
