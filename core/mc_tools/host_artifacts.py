"""Prepared bytes -> authenticated chunks -> live grant/dispatch -> create once -> readback.

Prepared payload and expected hashes are durable and authoritative. Effect/lease
handles exist only in this process. A lost handle/expired lease never permits a
second create; a fresh bound recovery challenge can claim read-only reconciliation.
Missing/partial files remain unknown, since an interrupted writer may have run.
"""
import base64
import hashlib
import json
import secrets
import time
from uuid import uuid4
from core.minimoi_actions.receipts import _cursor
from core.minimoi_actions.context import ActionContractError
from . import effects
from .host_files import digest

TOOLS=frozenset({'minimoi_working_write','minimoi_conversation_export','minimoi_artifact_recover','minimoi_career_trial'})
ROOT='/Users/vanstedum/Projects/personal-ai-agents/_working'
MAX=1024*1024
CHUNK=4096


def _hash(raw):return hashlib.sha256(raw).hexdigest()


def _receipt(row, *, fresh_verification=False):
    from .service import result
    if row['state']=='prepared':
        return result('done','artifact_not_started',
            'No write was dispatched. This prepared operation cannot be resumed or rewritten by recovery. '
            'A new owner request may create a new artifact.',
            {'operation_id':str(row['operation_id']),'state':'not_started','payload_sha256':row['payload_sha256']})
    return result('done' if row['state']=='verified' else 'unknown','artifact_'+row['state'],
        (('Artifact bytes verified by this host readback.' if fresh_verification else
          'Retained verified artifact receipt. No fresh filesystem check was performed for this response; current file presence or hashes are not established.') if row['state']=='verified' else
         'Artifact outcome is uncertain. Recover this operation by read-only inspection; never repeat its write.'),
        {'operation_id':str(row['operation_id']),'path':row['root']+'/mc-artifacts/'+str(row['operation_id']),
         'payload_sha256':row['payload_sha256'],'files':row['expected'],'state':row['state'],**row['summary'],
         'verification_provenance':'fresh_host_readback' if fresh_verification else 'retained_receipt',
         'fresh_filesystem_check':fresh_verification})


def _descriptor(row, challenge, nonce, expires, mode):
    from .service import result
    return result('done','artifact_prepared','Private host preparation; no write has started.',
        {'artifact_descriptor':{'challenge_id':challenge,'operation_id':str(row['operation_id']),'nonce':nonce,
         'expires_at':expires,'mode':mode,'root':row['root'],'sha256':row['payload_sha256'],
         'bytes':len(row['payload']),'chunk_bytes':CHUNK,
         **({'kind':'career_trial'} if row['capability']=='host.career.synthetic' else {})}})


def call(service,args,caller,bridge):
    from .service import refused
    if caller.offered_tools is None or caller.tool not in caller.offered_tools:
        return refused('tool_arguments_invalid')
    # Expired handles have no authority and must not accumulate. Never serialize one to durable storage.
    with service._host_effect_lock:
        service._host_effects={k:v for k,v in service._host_effects.items() if v[0]>time.monotonic()}
    return prepare(service,args,caller) if bridge is None else advance(service,args,caller,bridge)


def prepare(service,args,caller):
    from .service import result,refused
    with service._tx() as conn:
        b=service._bind(conn,caller)
        with _cursor(conn) as cur:
            cur.execute('SELECT 1 FROM guild.mc_host_artifact_challenges WHERE ledger_id=%s AND claim_generation=%s AND tool_call_id=%s',
                        (b.ledger_id,b.generation,caller.tool_call_id))
            if cur.fetchone():return refused('artifact_call_already_prepared')
            if caller.tool=='minimoi_artifact_recover':
                cur.execute('''SELECT * FROM guild.mc_host_artifacts WHERE operation_id=%s AND principal_id=%s
                    AND environment=%s AND conversation_id=%s FOR UPDATE''',
                    (args['operation_ref'],service.principal,service.environment,b.conversation_id))
                row=cur.fetchone()
                if not row:return refused('artifact_not_found')
                if row['state'] in ('prepared','verified'):return _receipt(row)
                mode='verify'
            else:
                capability=caller.rule.capability
                operation=service._operation_id(b,capability,args)
                cur.execute('SELECT * FROM guild.mc_host_artifacts WHERE operation_id=%s FOR UPDATE',(operation,))
                row=cur.fetchone()
                if row:return _receipt(row) # Same owner request is immutable; never recapture or issue new create.
                if caller.tool=='minimoi_career_trial':
                    from .career_trials import prepared
                    payload,summary=prepared()
                elif caller.tool=='minimoi_conversation_export':
                    if service.export_conversation is None:return refused('tool_unavailable')
                    try:payload,summary=service.export_conversation(b.conversation_id,b.note_request_id)
                    except Exception:return refused('export_snapshot_unavailable')
                else:
                    payload=json.dumps({'files':[{'name':'artifact.txt','text':args['text']}]},
                        sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
                    summary={'source':'model_supplied_artifact','note_count':0}
                if type(payload) is not bytes or len(payload)>MAX:return refused('artifact_payload_invalid')
                try:
                    bundle=json.loads(payload)
                    expected=(bundle['career_trial']['expected'] if caller.tool=='minimoi_career_trial' else
                        [{'name':f['name'],'bytes':len(f['text'].encode()),'sha256':_hash(f['text'].encode())} for f in bundle['files']])
                except Exception:return refused('artifact_payload_invalid')
                cur.execute('''INSERT INTO guild.mc_host_artifacts
                    (operation_id,principal_id,environment,conversation_id,capability,payload,payload_sha256,expected,summary,root,state)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,'prepared') RETURNING *''',
                    (operation,service.principal,service.environment,b.conversation_id,capability,payload,_hash(payload),
                     json.dumps(expected),json.dumps(summary),service.artifact_root))
                row=cur.fetchone();mode='create'
            challenge,nonce=str(uuid4()),secrets.token_hex(32)
            cur.execute('''INSERT INTO guild.mc_host_artifact_challenges
                (challenge_id,operation_id,ledger_id,claim_generation,tool_call_id,tool,policy_version,instance_id,
                 request_sha256,nonce_sha256,mode,expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,least(%s,clock_timestamp()+interval '120 seconds'))
                RETURNING extract(epoch FROM expires_at) AS expires''',
                (challenge,row['operation_id'],b.ledger_id,b.generation,caller.tool_call_id,caller.tool,caller.policy_version,
                 caller.instance_id,digest(args),_hash(nonce.encode()),mode,b.lease_until))
            expires=float(cur.fetchone()['expires'])
    return _descriptor(row,challenge,nonce,expires,mode)


def advance(service,args,caller,bridge):
    from .service import result,refused
    if type(bridge) is not dict or bridge.get('phase') not in ('chunk','start','complete'):
        return refused('tool_arguments_invalid')
    phase=bridge['phase'];keys={'phase','challenge_id','nonce'}|({'offset'} if phase=='chunk' else {'sha256'} if phase=='start' else {'result'})
    if set(bridge)!=keys or not all(isinstance(bridge.get(k),str) for k in ('challenge_id','nonce')):
        return refused('tool_arguments_invalid')
    import re
    if not re.fullmatch('[0-9a-f-]{36}',bridge['challenge_id']) or not re.fullmatch('[0-9a-f]{64}',bridge['nonce']):
        return refused('tool_arguments_invalid')
    handle_to_store=None
    with service._tx() as conn:
        b=service._bind(conn,caller)
        with _cursor(conn) as cur:
            cur.execute('''SELECT * FROM guild.mc_host_artifact_challenges WHERE challenge_id=%s AND ledger_id=%s
                AND claim_generation=%s AND tool_call_id=%s AND tool=%s AND policy_version=%s AND instance_id=%s
                AND expires_at>clock_timestamp() FOR UPDATE''',
                (bridge['challenge_id'],b.ledger_id,b.generation,caller.tool_call_id,caller.tool,caller.policy_version,caller.instance_id))
            challenge=cur.fetchone()
            if (not challenge or challenge['request_sha256']!=digest(args) or
                    not secrets.compare_digest(challenge['nonce_sha256'],_hash(bridge['nonce'].encode()))):
                return refused('artifact_callback_refused')
            cur.execute('''SELECT * FROM guild.mc_host_artifacts WHERE operation_id=%s AND principal_id=%s
                AND environment=%s AND conversation_id=%s FOR UPDATE''',
                (challenge['operation_id'],service.principal,service.environment,b.conversation_id))
            row=cur.fetchone()
            if not row:return refused('artifact_callback_refused')
            if phase=='chunk':
                offset=bridge['offset']
                if (challenge['started_at'] is not None or type(offset) is not int or offset<0 or
                        offset%CHUNK or offset>=len(row['payload'])):return refused('artifact_chunk_refused')
                raw=bytes(row['payload'])[offset:offset+CHUNK]
                return result('done','artifact_chunk','Private artifact chunk.',{'offset':offset,
                    'chunk':base64.b64encode(raw).decode(),'sha256':row['payload_sha256'],'bytes':len(row['payload'])})
            if phase=='start':
                if challenge['started_at'] is not None or bridge['sha256']!=row['payload_sha256']:
                    return refused('artifact_start_refused')
                if challenge['mode']=='create':
                    if row['state']!='prepared':return _receipt(row)
                    if row['capability']=='host.conversation.export':
                        try:service.export_conversation.authorize(b.conversation_id)
                        except Exception:return refused('export_snapshot_unavailable')
                    req=effects.request(row['capability'],{'operation_id':str(row['operation_id']),
                        'sha256':row['payload_sha256'],'root':row['root'],'files':row['expected']})
                    effect=effects.begin_standing_effect(conn,b,req,operation_id=str(row['operation_id']),worker=service.worker)
                    handle=('create',effect,b.context())
                else:
                    if row['state']=='verified':return _receipt(row)
                    lease=effects.reconcile_effect(conn,principal=service.principal,environment=service.environment,
                        operation_id=str(row['operation_id']),worker=service.worker)
                    if lease is None:return _receipt(row)
                    handle=('verify',lease,b.context())
                cur.execute("UPDATE guild.mc_host_artifacts SET state=%s WHERE operation_id=%s",
                            ('dispatched' if challenge['mode']=='create' else 'unknown',row['operation_id']))
                cur.execute("""UPDATE guild.mc_host_artifact_challenges SET started_at=clock_timestamp(),
                    expires_at=least(expires_at,clock_timestamp()+interval '20 seconds') WHERE challenge_id=%s
                    RETURNING extract(epoch FROM expires_at) AS expires""",(challenge['challenge_id'],))
                deadline=float(cur.fetchone()['expires'])
                handle_to_store=(bridge['challenge_id'],(time.monotonic()+30,handle))
                answer=result('done','artifact_execute','Private single-use execution admission.',
                    {'expires_at':deadline,'mode':challenge['mode'],'sha256':row['payload_sha256']})
            else:
                if challenge['completed_at'] is not None:return _receipt(row)
                output=bridge['result']
                if (challenge['started_at'] is None or type(output) is not dict or set(output)!={'verified','files'} or
                        type(output['verified']) is not bool or type(output['files']) is not list):
                    return refused('artifact_callback_refused')
                with service._host_effect_lock:held=service._host_effects.get(bridge['challenge_id'])
                if not held:return _receipt(row)
                mode,handle,context=held[1]
                verified=output['verified'] and output['files']==row['expected']
                if output['verified'] and not verified:return refused('artifact_readback_mismatch')
                try:
                    if verified:
                        evidence={'sha256':row['payload_sha256'],'files':row['expected']}
                        if mode=='create':handle.finish(conn,'host_artifact_readback',evidence,verified=True)
                        else:
                            evidence_id=effects.record_evidence(conn,context,str(row['operation_id']),'host_artifact_readback',evidence)
                            effects.RECEIPTS.finish(conn,handle,'verified',evidence_refs=(evidence_id,))
                    elif mode=='create':handle.unknown(conn)
                    else:effects.RECEIPTS.finish(conn,handle,'unknown')
                except ActionContractError:
                    return _receipt(row)
                state='verified' if verified else 'unknown'
                cur.execute('UPDATE guild.mc_host_artifacts SET state=%s WHERE operation_id=%s',(state,row['operation_id']))
                cur.execute('UPDATE guild.mc_host_artifact_challenges SET completed_at=clock_timestamp() WHERE challenge_id=%s',(challenge['challenge_id'],))
                row=dict(row,state=state);answer=_receipt(row,fresh_verification=state=='verified')
    if handle_to_store:
        with service._host_effect_lock:service._host_effects[handle_to_store[0]]=handle_to_store[1]
    return answer
