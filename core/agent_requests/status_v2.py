"""Versioned fixed status protocol. Reports observed commentary, never completion."""
import json
import math
import time
import re
from core.agent_requests.codex_desktop_queue import message_for as legacy_message
from core.agent_requests.codex_read_observer import correlate as legacy_correlate, need
from core.agent_requests.direct_contact import _hash

PRESERVE = ('This is an interleaved status query about the existing owner-authorized objective. '
            'It does not pause, cancel, replace, narrow, or complete that objective. '
            'Give a brief status update in commentary, then continue the already-authorized work. '
            'Do not end the build turn merely to answer this query. '
            'No additional work beyond existing owner authorization is granted. '
            'Respect any real owner stop, safety boundary, approval requirement, or blocker.')
MODES = {'status_request':'Report current progress and the next already-authorized step.',
         'clarification_request':'Report any blocker or question in the already-authorized work.'}


def message_for(target, request, correlation, *, version=1, mode=None):
    need(type(version) is int, 'status_protocol_invalid')
    if version == 1:
        # No edits/normalization: original bytes and stored digest stay compatible.
        return legacy_message(target, request, correlation)
    need(version == 2 and mode in MODES, 'status_protocol_invalid')
    return json.dumps({'format':'minimoi.direct-session-notice.v2',
        'request_id':request['request_id'],'correlation':correlation,'from':'mc',
        'target_session':target.session_id,'authority':'request_not_new_owner_approval',
        'action':MODES[mode]+' '+PRESERVE,
        'reply_protocol':{'phase':'commentary','prefix':'MC-STATUS:'+correlation+'\n',
            'json_fields':['format','request_id','correlation','report'],
            'format':'minimoi.status.v2','suffix':'\nMC-STATUS-END:'+correlation,
            'instruction':'Emit exactly one complete framed JSON status message. No final response is required for this query. '
                          'Status receipt is not overall build completion.'}},
        ensure_ascii=False,separators=(',',':'))


def unique(pairs):
    out={}
    for k,v in pairs:
        need(k not in out,'status_duplicate_key')
        out[k]=v
    return out


def correlate_status(snapshot, baseline, exact_message, correlation, request_id, previous=None):
    """Newly persisted commentary, not a completed-turn result.

    Two distinct observations of exact item bytes avoid accepting a partial
    streaming frame. This is observed stability, never proof the turn is complete.
    Production must persist the pending candidate separately from final receipts.
    """
    need(snapshot['thread_id']==baseline['thread_id'] and snapshot['target_revision']==baseline['target_revision'],
         'baseline_binding_changed')
    now=snapshot['observed_at']
    need(type(now) in (int,float) and math.isfinite(now) and 0<now<=time.time()+5,'status_chronology')
    if previous is not None:
        prior=previous.get('observed_at')
        need(type(prior) in (int,float) and math.isfinite(prior) and 0<prior<now,'status_chronology')
    ids=[i['id'] for t in snapshot['turns'] for i in t['items']]
    need(len(ids)==len(set(ids)) and set(baseline['items'])<=set(ids),'native_source_missing')
    inputs=[]
    for turn in snapshot['turns']:
        for n,item in enumerate(turn['items']):
            if item['id'] in baseline['items']:
                old=baseline['items'][item['id']]
                need(old is None or old==item['sha256'],'native_source_changed')
            elif item['type']=='userMessage' and item.get('text')==exact_message:
                inputs.append((turn,n,item))
    need(len(inputs)<=1,'native_duplicate_input')
    if not inputs:return {'state':'unverified','reason':'native_input_not_found'}
    turn,at,source=inputs[0]
    found=[]
    prefix='MC-STATUS:'+correlation+'\n';suffix='\nMC-STATUS-END:'+correlation
    for item in turn['items'][at+1:]:
        if (item['id'] in baseline['items'] or item['type']!='agentMessage'
                or item.get('phase')!='commentary'):continue
        text=item.get('text','')
        if not text.startswith(prefix):continue
        if not text.endswith(suffix):continue  # incomplete frame: no receipt
        need(len(text.encode())<=10000,'status_report_bound')
        try:value=json.loads(text[len(prefix):-len(suffix)],object_pairs_hook=unique)
        except (ValueError,TypeError):return {'state':'unverified','reason':'status_frame_invalid'}
        need(type(value) is dict and set(value)=={'format','request_id','correlation','report'}
             and value['format']=='minimoi.status.v2' and value['request_id']==request_id
             and value['correlation']==correlation and type(value['report']) is str
             and 0<len(value['report'])<=4000 and value['report'].strip(),'status_report_invalid')
        found.append((item,value))
    need(len(found)<=1,'native_duplicate_status')
    if not found:return {'state':'unverified','reason':'status_frame_not_found'}
    start=turn.get('startedAt');now=snapshot['observed_at']
    need(type(start) in (int,float) and math.isfinite(start) and 0<start<=now+5,'status_chronology')
    item,value=found[0]
    proof={'protocol_version':2,'request_id':request_id,'thread_id':snapshot['thread_id'],
        'target_revision':snapshot['target_revision'],'correlation':correlation,'message_sha256':_hash(exact_message),'turn_id':turn['id'],
        'input_id':source['id'],'input_sha256':source['sha256'],
        'status_id':item['id'],'status_sha256':item['sha256'],'report':value['report']}
    if previous is None or previous.get('proof')!=proof:
        return {'state':'status_candidate','proof':proof,'observed_at':now}
    first=previous.get('first_observed_at',previous['observed_at'])
    need(type(first) in (int,float) and math.isfinite(first) and 0<first<=previous['observed_at'],'status_chronology')
    if now<first+1:return {'state':'status_candidate','proof':proof,'observed_at':now,'first_observed_at':first}
    return {'state':'status_report','proof':proof,'observed_at':now,
            'first_observed_at':first,'native_turn_status':turn['status'],'build_complete':False,'native_dispatch_required':False}


def correlate_versioned(snapshot,baseline,message,correlation,request_id,*,version=1,previous=None):
    need(type(version) is int,'status_protocol_invalid')
    if version==1:return legacy_correlate(snapshot,baseline,message,correlation)
    need(version==2,'status_protocol_invalid')
    return correlate_status(snapshot,baseline,message,correlation,request_id,previous)


def protocol_for(target,request_id,correlation,mode):
    need(type(correlation) is str and re.fullmatch('[0-9a-f]{48}',correlation),'status_protocol_invalid')
    message=message_for(target,{'request_id':request_id},correlation,version=2,mode=mode)
    return {'version':2,'mode':mode,'request_id':request_id,'correlation':correlation,
            'message':message,'message_sha256':_hash(message)}


def validate_protocol(value,target,request_id,correlation):
    need(type(value) is dict and type(value.get('version')) is int and value['version']==2,'status_protocol_invalid')
    expected=protocol_for(target,request_id,correlation,value.get('mode'))
    need(value==expected,'status_protocol_changed')
    return value['message']


def request_message(target,request,correlation):
    if 'reply_protocol' not in request:return legacy_message(target,request,correlation)
    return validate_protocol(request['reply_protocol'],target,request['request_id'],correlation)


def validate_receipt(value,protocol,target):
    need(type(value) is dict and value.get('state')=='status_report' and value.get('build_complete') is False
         and value.get('native_dispatch_required') is False,'status_receipt_invalid')
    observed=value.get('observed_at');first=value.get('first_observed_at')
    need(type(observed) in (int,float) and math.isfinite(observed) and 0<observed<=time.time()+5
         and type(first) in (int,float) and math.isfinite(first) and 0<first<=observed-1,'status_chronology')
    need(value.get('native_turn_status') in ('inProgress','completed','interrupted','failed'),'status_native_state_invalid')
    proof=value.get('proof')
    need(type(proof) is dict and type(proof.get('protocol_version')) is int and proof['protocol_version']==2
         and proof.get('request_id')==protocol['request_id'] and proof.get('correlation')==protocol['correlation']
         and proof.get('message_sha256')==protocol['message_sha256'] and proof.get('thread_id')==target.session_id
         and proof.get('target_revision')==target.revision,'status_receipt_binding')
    need(all(type(proof.get(k)) is str and 0<len(proof[k])<=200 for k in ('turn_id','input_id','status_id'))
         and all(type(proof.get(k)) is str and re.fullmatch('[0-9a-f]{64}',proof[k]) for k in ('input_sha256','status_sha256'))
         and type(proof.get('report')) is str and proof['report'].strip() and len(proof['report'])<=4000,'status_receipt_invalid')
    return value
