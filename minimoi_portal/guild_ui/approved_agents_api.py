"""Owner controls; Records owns approvals, installer owns private delivery."""
from flask import current_app, jsonify
from .security import json_error


def listing():
    call=current_app.extensions.get('rooms_call')
    if not call: return json_error('unavailable','Agent approvals are unavailable.',503)
    status,body=call('GET','/api/v1/approved-agents')
    if status!=200: return json_error('unavailable','Agent approvals could not be read.',503)
    return jsonify(body)


def change(agent, brief_only=False):
    from . import api
    refusal,body=api._write_body()
    if refusal is not None: return refusal
    if agent not in ('mc','claude-code','codex') or (not brief_only and type(body.get('enabled')) is not bool):
        return json_error('invalid','Choose an agent and On or Off.',422)
    payload={} if brief_only else {'enabled':body['enabled']}
    if 'brief' in body: payload['brief']=body['brief']
    call=current_app.extensions.get('rooms_call')
    if not call: return json_error('unavailable','Agent approvals are unavailable.',503)
    status,result=call('POST','/api/v1/approved-agents/'+agent+('/brief' if brief_only else ''),payload)
    if status!=200:
        safe={'Separate the shared worker installation before changing this approval',
              'Brief must contain at most 8000 characters','Adopt this agent before editing its brief',
              'Provision the existing agent identity first','Wait for installation before editing the brief'}
        reason=result.get('error') if isinstance(result,dict) else None
        return json_error('not_finished',reason if reason in safe else 'Approval change was not confirmed. Refresh before retrying.',status if status in (403,409,422) else 502)
    return jsonify(result)

def brief(agent):
    return change(agent, brief_only=True)

RULES=[('/rooms/agents/<agent>/brief','api_approved_agent_brief',brief,['POST']),('/rooms/agents','api_approved_agents',listing,['GET']),
       ('/rooms/agents/<agent>','api_approved_agent_change',change,['POST'])]
