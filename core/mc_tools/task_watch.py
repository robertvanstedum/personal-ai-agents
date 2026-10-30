"""Operator-enrolled task subscriptions, not model tools or contact requests.

The distinct authenticated host worker may only observe registered subscriptions.
No phase creates native work. Immutable registration is supplied by a private
operator registry; omission/revision revokes it. Checkpoint+proof commit together.
"""
import hashlib
import json
import re
import secrets
import time
from dataclasses import asdict
from pathlib import Path

from core.agent_requests.direct_contact import _read
from core.agent_requests.task_watch import sealed_terminal, anchor_lifecycle, CHUNK, MAX_STATE, DONE, OUTCOMES, encoded, sha, validate_spec, unique
from core.minimoi_actions.receipts import _cursor


def require(ok):
    if not ok: raise ValueError('task_watch_refused')


def registry(path):
    path = Path(path)
    require(path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)))
    def load():
        value = _read(path)
        require(set(value) == {'subscriptions'} and type(value['subscriptions']) is list and len(value['subscriptions']) <= 16)
        return value['subscriptions']
    return load


class TaskWatch:
    def __init__(self, registrations, contact):
        self.registrations, self.contact = registrations, contact

    def declarations(self, service):
        entries = self.registrations()
        require(type(entries) is list and len(entries) <= 16)
        out = {}
        for d in entries:
            require(type(d) is dict and set(d) == {'subscription_id', 'principal', 'environment', 'conversation_id',
                    'note_request_id', 'owner_note_sha256', 'target', 'spec', 'scope', 'expires_at'})
            validate_spec(d['spec'])
            require(re.fullmatch('[0-9a-f-]{36}', str(d['subscription_id'])) and d['subscription_id'] not in out)
            require(d['principal'] == service.principal and d['environment'] == service.environment)
            require(type(d['scope']) is dict and sha(d['scope']) == d['spec']['scope_sha256'])
            require(type(d['expires_at']) in (int, float) and 0 < d['expires_at'] < 1e12)
            require(type(d['target']) is dict and type(d['target'].get('alias')) is str)
            out[d['subscription_id']] = d
        return out

    def allowed(self, service, d):
        target = self.contact.targets.get(d['target']['alias'])
        if (target is None or asdict(target) != d['target'] or d['expires_at'] <= time.time()
                or not self.contact._allowed(service, d['conversation_id'])):
            return False
        note = service.read_owner_note(d['conversation_id'], d['note_request_id']) if service.read_owner_note else None
        return bool(note and type(note.get('text')) is str and
                    hashlib.sha256(note['text'].encode()).hexdigest() == d['owner_note_sha256'])

    def poll(self, service):
        declarations = self.declarations(service)
        with service._tx() as conn, _cursor(conn) as cur:
            for sid, d in declarations.items():
                if not self.allowed(service, d): continue
                cur.execute('''INSERT INTO guild.mc_task_subscriptions
                    (subscription_id,principal_id,environment,task_id,generation,declaration,declaration_sha256)
                    VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING''',
                    (sid,service.principal,service.environment,d['spec']['task_id'],d['spec']['generation'],json.dumps(d),sha(d)))
            cur.execute('''SELECT * FROM guild.mc_task_subscriptions WHERE principal_id=%s AND environment=%s
                AND state='armed' ORDER BY created_at FOR UPDATE''',(service.principal,service.environment))
            rows=cur.fetchall()
            # next_observe_at is stamped by the database's clock, so due-ness reads that clock
            # (after the row locks), never this host's: the two can differ (a Docker VM's clock).
            cur.execute('SELECT clock_timestamp() AS now')
            now=cur.fetchone()['now']
            jobs=[]
            for row in rows:
                d=declarations.get(str(row['subscription_id']))
                if d != row['declaration'] or not d or not self.allowed(service,d):
                    state='expired' if row['declaration']['expires_at'] <= time.time() else 'revoked'
                    cur.execute('UPDATE guild.mc_task_subscriptions SET state=%s WHERE subscription_id=%s',(state,row['subscription_id']))
                    continue
                if row['next_observe_at'] <= now:
                    jobs.append({'mode':'task_observe','subscription_ref':str(row['subscription_id'])})
            return jobs[:16]

    def worker(self,service,body):
        require(type(body) is dict)
        phase=body.get('phase')
        fields={'task_begin':set(), 'task_chunk':{'nonce','offset'}, 'task_stage':{'nonce','offset','data'},
                'task_commit':{'nonce','sha256'}}
        require(phase in fields and set(body)=={'phase','subscription_ref'}|fields[phase])
        sid=body['subscription_ref']
        require(type(sid) is str and re.fullmatch('[0-9a-f-]{36}',sid))
        d=self.declarations(service).get(sid)
        require(d is not None and self.allowed(service,d))
        with service._tx() as conn,_cursor(conn) as cur:
            cur.execute('''SELECT * FROM guild.mc_task_subscriptions WHERE subscription_id=%s AND principal_id=%s
                AND environment=%s FOR UPDATE''',(sid,service.principal,service.environment))
            row=cur.fetchone()
            require(row and row['declaration']==d and row['state']=='armed')
            # The database stamps the challenge expiry, so it is read against the database's clock (as in poll).
            cur.execute('SELECT clock_timestamp() AS now')
            now=cur.fetchone()['now']
            challenge_live=row['challenge_expires_at'] is not None and row['challenge_expires_at']>now
            if phase=='task_begin':
                # Overlapping/restarted ticks cannot replace an active challenge.
                if challenge_live:return {'state':'busy'}
                nonce=secrets.token_hex(32)
                cur.execute('''UPDATE guild.mc_task_subscriptions SET nonce_sha256=%s,staged='',
                    challenge_expires_at=clock_timestamp()+interval '120 seconds',
                    next_observe_at=clock_timestamp()+interval '30 seconds' WHERE subscription_id=%s''',
                    (hashlib.sha256(nonce.encode()).hexdigest(),sid))
                raw=encoded(row['checkpoint'])
                return {'subscription_ref':sid,'nonce':nonce,'expires_at':time.time()+115,'target':d['target'],
                        'principal':d['principal'],'environment':d['environment'],'spec':d['spec'],
                        'checkpoint_bytes':len(raw),'checkpoint_sha256':hashlib.sha256(raw).hexdigest()}
            require(type(body['nonce']) is str and row['nonce_sha256'] and challenge_live and
                    secrets.compare_digest(hashlib.sha256(body['nonce'].encode()).hexdigest(),row['nonce_sha256']))
            if phase=='task_chunk':
                raw=encoded(row['checkpoint']).decode('ascii')
                offset=body['offset']
                require(type(offset) is int and 0<=offset<len(raw) and offset%CHUNK==0)
                return {'offset':offset,'data':raw[offset:offset+CHUNK]}
            if phase=='task_stage':
                offset,data=body['offset'],body['data']
                require(type(offset) is int and offset>=0 and type(data) is str and data.isascii()
                        and 0<len(data)<=CHUNK and offset+len(data)<=MAX_STATE)
                staged=row['staged']
                if offset<len(staged):
                    require(staged[offset:offset+len(data)]==data)
                else:
                    require(offset==len(staged))
                    cur.execute('UPDATE guild.mc_task_subscriptions SET staged=staged||%s WHERE subscription_id=%s',(data,sid))
                return {'state':'staged'}
            raw=row['staged'].encode('ascii')
            require(hashlib.sha256(raw).hexdigest()==body['sha256'])
            output=json.loads(raw,object_pairs_hook=unique)
            require(type(output) is dict and set(output)=={'checkpoint','proof','state'} and output['state']=='armed')
            checkpoint=output['checkpoint']
            require(type(checkpoint) is dict and set(checkpoint)=={'thread_id','revision','spec_sha256','anchor','items','excluded','active','batch_head','pending_done'}
                    and checkpoint['thread_id']==d['target']['session_id'] and checkpoint['revision']==d['target']['revision']
                    and checkpoint['spec_sha256']==sha(d['spec']))
            require(type(checkpoint['items']) is dict and len(checkpoint['items'])<=1600)
            baseline=row['initial_checkpoint'] or checkpoint
            require(type(checkpoint['excluded']) is list and checkpoint['excluded']==sorted(baseline['items']))
            proof=output['proof']
            prior=row['checkpoint']
            if prior and prior['anchor'] and checkpoint['anchor'] and prior['anchor']['id']==checkpoint['anchor']['id']:
                anchor_lifecycle(prior['anchor'],checkpoint['anchor'])
                require(set(prior['items'])<=set(checkpoint['items']) and all(v is None or checkpoint['items'][k]==v for k,v in prior['items'].items()))
            # Registration must establish and persist its old-item baseline
            # before any later callback can report completion.
            require(row['checkpoint'] is not None or (proof is None and checkpoint['pending_done'] is None))
            old_pending=(row['checkpoint'] or {}).get('pending_done')
            if old_pending:
                require((checkpoint['pending_done']==old_pending and proof is None) or
                        (proof==old_pending and checkpoint['pending_done'] is None))
            final_proof=proof
            for proof in (checkpoint['pending_done'],final_proof):
                if proof is None:continue
                require(type(proof) is dict and set(proof)=={'report','turn_id','item_id','item_sha256','completed_at'})
                report=proof['report']
                require(type(report) is dict and set(report)=={'format',*d['spec'].keys(),'outcome','evidence_refs','remaining'}
                        and report['format']=='minimoi.task-terminal.v1' and all(report.get(k)==v for k,v in d['spec'].items())
                        and report.get('outcome') in OUTCOMES and type(report.get('remaining')) is str
                        and len(report['remaining'])<=2000 and len(encoded(proof))<=6000
                        and re.fullmatch('[0-9a-f]{64}',str(proof['item_sha256'])))
                require(type(report['evidence_refs']) is list and len(report['evidence_refs'])<=8
                        and all(type(v) is str and re.fullmatch('[A-Za-z0-9_.:-]{1,160}',v) for v in report['evidence_refs']))
                old=row['checkpoint']
                excluded=set(row['initial_checkpoint']['items'])
                if old['anchor'] and sealed_terminal(old['anchor']):
                    excluded.update(old['items'])
                require((proof==old.get('pending_done') or proof['item_id'] not in excluded) and type(proof['completed_at']) in (int,float)
                        and 0<proof['completed_at']<=time.time()+5)
                require(proof==old.get('pending_done') or (checkpoint['anchor'] and checkpoint['anchor']['id']==proof['turn_id']
                        and checkpoint['anchor']['status']=='completed' and proof['item_id'] in checkpoint['items']
                        and checkpoint['anchor']['completedAt']==proof['completed_at']))
                if proof is checkpoint['pending_done']:
                    require(report['outcome'] in DONE and checkpoint['batch_head'] is not None)
                elif report['outcome'] in DONE:
                    require(checkpoint['batch_head'] is None and checkpoint['pending_done'] is None)
            proof=final_proof
            done=proof is not None and proof['report']['outcome'] in DONE
            notice=None
            if done:
                label='reports that the scoped build is ready for owner UAT; acceptance is pending' if proof['report']['outcome']=='ready_for_owner_uat' else 'reports that the registered build scope is complete'
                notice=('The enrolled Codex chat '+label+'.\n\nTask: '+d['spec']['task_id']+' / '+d['spec']['generation']+
                        '\nScope SHA-256: '+d['spec']['scope_sha256']+'\n\nReported remaining work: '+proof['report']['remaining']+
                        '\n\nThis is a native agent report, not independent verification or owner acceptance.')
            cur.execute('''UPDATE guild.mc_task_subscriptions SET initial_checkpoint=COALESCE(initial_checkpoint,%s::jsonb),checkpoint=%s::jsonb,checkpoint_version=checkpoint_version+1,
                last_observed_at=clock_timestamp(),last_proof=COALESCE(%s::jsonb,last_proof),terminal_proof=%s::jsonb,
                state=%s,notice_text=%s,notice_sha256=%s,staged='',nonce_sha256=NULL,challenge_expires_at=NULL
                WHERE subscription_id=%s''',
                (json.dumps(checkpoint),json.dumps(checkpoint),json.dumps(proof) if proof else None,json.dumps(proof) if done else None,
                 'reported' if done else 'armed',notice,hashlib.sha256(notice.encode()).hexdigest() if notice else None,sid))
            return {'state':'reported' if done else 'armed'}

    def deliver_notices(self,service):
        declarations=self.declarations(service)
        count=0
        with service._tx() as conn,_cursor(conn) as cur:
            cur.execute('''SELECT * FROM guild.mc_task_subscriptions WHERE principal_id=%s AND environment=%s
                AND state='reported' AND notice_delivered_at IS NULL ORDER BY created_at LIMIT 16 FOR UPDATE SKIP LOCKED''',
                (service.principal,service.environment))
            for row in cur.fetchall():
                d=declarations.get(str(row['subscription_id']))
                if not d or d!=row['declaration'] or not self.allowed(service,d):continue
                text=row['notice_text']
                require(hashlib.sha256(text.encode()).hexdigest()==row['notice_sha256'])
                # Reported text is untrusted even though provenance is authenticated.
                cur.execute('''INSERT INTO guild.mc_tool_conversation_taint(principal_id,environment,conversation_id)
                    VALUES (%s,%s,%s) ON CONFLICT(principal_id,environment,conversation_id)
                    DO UPDATE SET last_read_at=clock_timestamp()''',(service.principal,service.environment,d['conversation_id']))
                key='mc-task-terminal-'+str(row['subscription_id'])
                note=service.read_shown(d['conversation_id'],key)
                if note is None:
                    service.show(d['conversation_id'],key,text)
                    note=service.read_shown(d['conversation_id'],key)
                if note and note.get('who')=='guild_platform' and note.get('text')==text:
                    cur.execute('UPDATE guild.mc_task_subscriptions SET notice_delivered_at=clock_timestamp() WHERE subscription_id=%s',(row['subscription_id'],))
                    count+=1
        return count
