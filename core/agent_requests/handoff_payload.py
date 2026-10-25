"""Pure, versioned fixed handoff construction shared by portal and host sender."""
import hashlib
import json
import math
import time

TOKEN='MC-CLAUDE-BETA-PROBE-20261024'
PROBE_PURPOSE='synthetic-connectivity-probe.v1'
PROBE_TEXT=f'Synthetic MC connectivity test {TOKEN}. Reply exactly {TOKEN} in your own chat. Do not inspect files, call tools, modify anything, or start work.'
PROBE_CONSTRAINTS=['Synthetic connectivity only; no files, tools, modifications or work.']
CAREER_PURPOSE='Existing owner-authorized Career spec/build handoff'
AUTHORITY='Original owner request only; no new work, merge, production or private-data authority.'
FIELDS={'version','principal','environment','conversation_id','note_request_id','owner_text','owner_text_sha256','target','constraints','expires_at'}

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)
def digest(value):return hashlib.sha256(canonical(value).encode('ascii')).hexdigest()

def validate(record,*,live=False):
    if type(record) is not dict:raise ValueError('approved_handoff_unavailable')
    version=record.get('version')
    if type(version) is not int or version not in (1,2) or set(record)!=(FIELDS|({'purpose'} if version==2 else set())):
        raise ValueError('approved_handoff_unavailable')
    if (any(type(record[k]) is not str or not record[k] for k in ('principal','environment','conversation_id','note_request_id','owner_text')) or
        hashlib.sha256(record['owner_text'].encode()).hexdigest()!=record['owner_text_sha256'] or
        type(record['constraints']) is not list or not 1<=len(record['constraints'])<=8 or
        any(type(s) is not str or not 0<len(s)<=500 for s in record['constraints']) or
        type(record['expires_at']) not in (int,float) or not math.isfinite(record['expires_at']) or
        not 0<record['expires_at']<1e12 or (live and record['expires_at']<=time.time())):
        raise ValueError('approved_handoff_unavailable')
    if version==2 and (record['purpose']!=PROBE_PURPOSE or record['constraints']!=PROBE_CONSTRAINTS):
        raise ValueError('approved_handoff_unavailable')
    return version

def action(record):
    version=validate(record)
    if version==1:
        value={'format':'minimoi.approved-owner-handoff.v1','purpose':CAREER_PURPOSE,
            'owner_text':record['owner_text'],'owner_note_request_id':record['note_request_id'],
            'constraints':record['constraints'],'authority':AUTHORITY}
    else:
        value={'format':'minimoi.approved-owner-probe.v1','purpose':PROBE_PURPOSE,
            'payload':PROBE_TEXT,'owner_note_request_id':record['note_request_id'],
            'constraints':PROBE_CONSTRAINTS,'authority':'Fixed synthetic connectivity probe only; no new work authority.'}
    result=canonical(value)
    if len(result)>4000:raise ValueError('approved_handoff_bound')
    return result

def send_input(record,*,target,request_id,correlation,approved_action):
    expected=action(record)
    if approved_action!=expected:raise ValueError('handoff_payload_changed')
    probe=record['version']==2
    message=canonical({'format':'minimoi.fixed-synthetic-probe.v1' if probe else 'minimoi.fixed-owner-handoff.v1',
        'purpose':PROBE_PURPOSE if probe else CAREER_PURPOSE,'request_id':request_id,'correlation':correlation,
        'handoff':json.loads(expected),'reply':f'Reply exactly {TOKEN} in your own chat; no return SendMessage or other tools.' if probe else
        'Report in your own session when appropriate; no return SendMessage required. Preserve existing release hold.'})
    return {'to':target,'message':message,'summary':'Fixed synthetic MC connectivity probe; no work' if probe else 'Original owner Career spec handoff; preserve release hold'}

def valid_reply(record,proof):
    return record['version']==1 or proof.get('state')!='reply_observed' or proof.get('report')==TOKEN
