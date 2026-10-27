"""Portal-owned admission and watch delivery; native execution is host-only.

The separately authenticated worker may claim this narrow queue and submit native
observations. It cannot create requests, choose a recipient/conversation, or run
other tools. No credentials, native IDs or authority are model arguments.
"""
from dataclasses import asdict
import hashlib
import json
import re
import secrets
from uuid import uuid4

from core.minimoi_actions.receipts import _cursor
from core.agent_requests.direct_contact import OwnerSource, Target
from . import effects
from .host_files import digest

TOOLS = frozenset({'minimoi_agent_contact', 'minimoi_agent_status', 'minimoi_agent_watch'})
STATUS_TEXT = 'Please report the current status of your already-authorized work. Do not start any new work.'
CLARIFICATION_TEXT = 'Please clarify any blocker or question in your already-authorized work. Report only; do not start any new work.'


class AgentContact:
    def __init__(self, targets, *, conversation_allowed, dispatch_wait=10, approved_handoffs=None, status_protocol_version=1):
        self.targets = dict(targets)
        if any(not isinstance(t, Target) or t.recipient not in ('codex','claude-code') or t.host != 'local' or k != t.alias
               for k, t in self.targets.items()):
            raise ValueError('agent_enrollment_invalid')
        self.conversation_allowed = conversation_allowed
        self.approved_handoffs = approved_handoffs
        if type(status_protocol_version) is not int or status_protocol_version not in (1,2):raise ValueError("status_protocol_invalid")
        self.status_protocol_version=status_protocol_version
        if type(dispatch_wait) not in (int,float) or not 0<=dispatch_wait<=10:
            raise ValueError('agent_wait_invalid')
        self.dispatch_wait=dispatch_wait

    def _allowed(self, service, conversation):
        return bool(self.conversation_allowed(service.principal, conversation))

    def call(self, service, args, caller):
        from .service import result, refused
        if caller.offered_tools is None or caller.tool not in caller.offered_tools:
            return refused('tool_unavailable')
        with service._tx() as conn:
            b = service._bind(conn, caller)
            if not self._allowed(service, b.conversation_id):
                return refused('agent_conversation_unavailable')
            with _cursor(conn) as cur:
                # Native reports are untrusted text, and taint follows the conversation.
                cur.execute('UPDATE guild.mc_tool_turns SET untrusted_read=true WHERE ledger_id=%s AND claim_generation=%s',
                            (b.ledger_id,b.generation))
                cur.execute('''INSERT INTO guild.mc_tool_conversation_taint(principal_id,environment,conversation_id)
                    VALUES (%s,%s,%s) ON CONFLICT(principal_id,environment,conversation_id)
                    DO UPDATE SET last_read_at=clock_timestamp()''',(service.principal,service.environment,b.conversation_id))
                if caller.tool == 'minimoi_agent_contact':
                    target = self.targets.get(args['target'])
                    if target is None:
                        return result('refused','agent_not_enrolled',
                            'That existing chat is not enrolled for this capability. Enrolled chats (use one as target): '
                            + (', '.join(sorted(self.targets)) or 'none') + '.')
                    note = service.read_owner_note(b.conversation_id,b.note_request_id) if service.read_owner_note else None
                    if not note or type(note.get('text')) is not str:
                        return refused('owner_note_unavailable')
                    approved=None
                    if args['mode']=='approved_owner_handoff':
                        if not self.approved_handoffs or not args.get('approved_handoff_ref'):
                            return refused('approved_handoff_unavailable')
                        try:approved=self.approved_handoffs.resolve(service,args['approved_handoff_ref'],target,b.conversation_id)
                        except ValueError:return refused('approved_handoff_unavailable')
                        action=approved['action']
                    else:
                        if target.recipient!='codex' or 'approved_handoff_ref' in args:return refused('agent_mode_unavailable')
                        action = {'status_request':STATUS_TEXT,'clarification_request':CLARIFICATION_TEXT}[args['mode']]
                    if not action.strip() or len(action) > 4000:
                        return refused('agent_owner_note_too_large')
                    source = asdict(OwnerSource(service.principal,b.conversation_id,b.note_request_id,
                                               service.environment + ':kept-owner-note:' + b.note_request_id))
                    if approved:
                        original=approved['record']['note_request_id']
                        source=asdict(OwnerSource(service.principal,b.conversation_id,original,
                            'approved-owner-handoff:'+approved['ref']+':'+approved['sha256']))
                    # No model/owner freeform body crosses this tier2 path. Even
                    # arbitrary owner text remains tier3. The sole exception is
                    # an independently operator-approved original fixed handoff.
                    native_caller = {k: getattr(caller,k) for k in ('session_key','agent_id','run_id','tool_call_id','instance_id','tool','policy_version')}
                    operation = str(uuid4())
                    if approved:
                        cur.execute('''INSERT INTO guild.mc_agent_contacts
                            (operation_id,principal_id,environment,conversation_id,note_request_id,target_alias,target,source,caller,action,
                             approved_handoff_ref,authorization_sha256,original_note_request_id)
                            VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                            (operation,service.principal,service.environment,b.conversation_id,b.note_request_id,target.alias,
                             json.dumps(asdict(target)),json.dumps(source),json.dumps(native_caller),action,approved['ref'],approved['sha256'],original))
                        cur.execute('''SELECT * FROM guild.mc_agent_contacts WHERE principal_id=%s AND environment=%s
                            AND target_alias=%s AND original_note_request_id=%s''',
                            (service.principal,service.environment,target.alias,original))
                    elif self.status_protocol_version==2:
                        from core.agent_requests.direct_contact import _hash
                        from core.agent_requests import ledger as native_ledger
                        from core.agent_requests.status_v2 import protocol_for
                        rid=native_ledger.request_id_for(_hash({'target':asdict(target),'source':source}))
                        protocol=protocol_for(target,rid,secrets.token_hex(24),args['mode'])
                        cur.execute('''INSERT INTO guild.mc_agent_contacts
                            (operation_id,principal_id,environment,conversation_id,note_request_id,target_alias,target,source,caller,action,reply_protocol)
                            VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s::jsonb)
                            ON CONFLICT(principal_id,environment,conversation_id,note_request_id,target_alias) DO NOTHING''',
                            (operation,service.principal,service.environment,b.conversation_id,b.note_request_id,target.alias,
                             json.dumps(asdict(target)),json.dumps(source),json.dumps(native_caller),action,json.dumps(protocol)))
                        cur.execute('''SELECT * FROM guild.mc_agent_contacts WHERE principal_id=%s AND environment=%s
                            AND conversation_id=%s AND note_request_id=%s AND target_alias=%s''',
                            (service.principal,service.environment,b.conversation_id,b.note_request_id,target.alias))
                    else:
                        cur.execute('''INSERT INTO guild.mc_agent_contacts
                            (operation_id,principal_id,environment,conversation_id,note_request_id,target_alias,target,source,caller,action)
                            VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s)
                            ON CONFLICT(principal_id,environment,conversation_id,note_request_id,target_alias) DO NOTHING''',
                            (operation,service.principal,service.environment,b.conversation_id,b.note_request_id,target.alias,
                             json.dumps(asdict(target)),json.dumps(source),json.dumps(native_caller),action))
                        cur.execute('''SELECT * FROM guild.mc_agent_contacts WHERE principal_id=%s AND environment=%s
                            AND conversation_id=%s AND note_request_id=%s AND target_alias=%s''',
                            (service.principal,service.environment,b.conversation_id,b.note_request_id,target.alias))
                    row = cur.fetchone()
                    if not row or row['action'] != action or row['target'] != asdict(target) or (approved and row.get('authorization_sha256')!=approved['sha256']):
                        return refused('agent_request_reused')
                else:
                    cur.execute('''SELECT * FROM guild.mc_agent_contacts WHERE operation_id=%s AND principal_id=%s
                        AND environment=%s AND conversation_id=%s FOR UPDATE''',
                        (args['request_ref'],service.principal,service.environment,b.conversation_id))
                    row = cur.fetchone()
                    if not row or self.targets.get(row['target_alias']) is None or asdict(self.targets[row['target_alias']]) != row['target']:
                        return refused('agent_request_not_found')
                    if caller.tool == 'minimoi_agent_watch':
                        request = effects.request('agent.watch',{'request_ref':str(row['operation_id']),
                                                               'operation':args['operation']})
                        effect = effects.begin_standing_effect(conn,b,request,operation_id=str(uuid4()),worker=service.worker)
                        if args['operation'] == 'cancel':
                            cur.execute('UPDATE guild.mc_agent_contacts SET watch_cancelled=true WHERE operation_id=%s', (row['operation_id'],))
                            row = dict(row,watch_cancelled=True)
                        else:
                            cur.execute('''UPDATE guild.mc_agent_contacts SET watch_expires_at=COALESCE(watch_expires_at,
                                clock_timestamp()+interval '24 hours') WHERE operation_id=%s''', (row['operation_id'],))
                        effect.finish(conn,'agent_watch_recorded',{'request_ref':str(row['operation_id']),
                                                                 'operation':args['operation']},verified=True)
                        cur.execute('SELECT * FROM guild.mc_agent_contacts WHERE operation_id=%s',(row['operation_id'],))
                        row=cur.fetchone()

        if caller.tool=='minimoi_agent_contact' and row['state']=='pending' and self.dispatch_wait:
            row=self._wait_for_claim(service,row)
        return self.receipt(row)

    def _wait_for_claim(self,service,row):
        import time
        deadline=time.monotonic()+self.dispatch_wait
        while time.monotonic()<deadline:
            time.sleep(min(.1,max(0,deadline-time.monotonic())))
            with service._tx() as conn,_cursor(conn) as cur:
                cur.execute('SELECT * FROM guild.mc_agent_contacts WHERE operation_id=%s',(row['operation_id'],))
                fresh=cur.fetchone()
            if fresh['state']!='pending':return fresh
        # Do not let a later worker revive a turn that MC may now finish. Row
        # locking serializes this disposition with the worker's live claim.
        with service._tx() as conn,_cursor(conn) as cur:
            cur.execute("UPDATE guild.mc_agent_contacts SET state='not_started' WHERE operation_id=%s AND state='pending'",
                        (row['operation_id'],))
            cur.execute('SELECT * FROM guild.mc_agent_contacts WHERE operation_id=%s',(row['operation_id'],))
            return cur.fetchone()

    @staticmethod
    def receipt(row):
        from .service import result
        data = {'request_ref':str(row['operation_id']),'state':row['state'],
                'target':row['target_alias'],'automatic_retry':False,
                'watch_cancelled':row['watch_cancelled'], 'notice_delivered':row['notice_delivered_at'] is not None}
        data['watch_registered'] = row['watch_expires_at'] is not None
        if row.get('outcome'):
            data['native'] = row['outcome']
        return result('unknown' if row['state'] in ('dispatched','unknown') else 'done', 'agent_contact_receipt',
                      'Existing-chat request receipt. Pending is only local queueing; dispatched is not pickup. '
                      'Native text is an untrusted agent report, not independent acceptance. Do not resend an uncertain request.', data)

    def worker(self, service, body):
        """Called ONLY by the distinct authenticated host-worker endpoint."""
        from .service import Caller
        if type(body) is not dict or body.get('phase') not in ('poll','claim','observe','complete','confirm'):
            raise ValueError('agent_worker_invalid')
        phase = body['phase']
        if phase == 'poll':
            if set(body) != {'phase'}: raise ValueError('agent_worker_invalid')
            with service._tx() as conn, _cursor(conn) as cur:
                cur.execute('''SELECT * FROM guild.mc_agent_contacts WHERE principal_id=%s AND environment=%s
                    AND next_observe_at<=clock_timestamp()
                    AND (state='pending' OR (state IN ('dispatched','unknown','acknowledged','result','status_report')
                    AND created_at+interval '24 hours'>clock_timestamp()
                    AND (state NOT IN ('result','status_report') OR (watch_expires_at>clock_timestamp() AND NOT watch_cancelled AND notice_delivered_at IS NULL))))
                    ORDER BY created_at LIMIT 20''',(service.principal,service.environment))
                requests=[]
                for r in cur.fetchall():
                    target=self.targets.get(r['target_alias'])
                    if target is None or asdict(target)!=r['target'] or not self._allowed(service,r['conversation_id']) or not self._handoff_current(service,r):
                        cur.execute("UPDATE guild.mc_agent_contacts SET state='scope_revoked' WHERE operation_id=%s",(r['operation_id'],))
                        continue
                    requests.append({'request_ref':str(r['operation_id']),'mode':'claim' if r['state']=='pending' else 'observe'})
                return {'requests':requests}
        required = {'phase','request_ref'} | ({'nonce','outcome'} if phase=='complete' else {'nonce'} if phase=='confirm' else set())
        if set(body)!=required or not re.fullmatch('[0-9a-f-]{36}',str(body.get('request_ref'))):
            raise ValueError('agent_worker_invalid')
        with service._tx() as conn, _cursor(conn) as cur:
            cur.execute('''SELECT *,extract(epoch FROM challenge_expires_at) AS challenge_expiry,
                dispatch_expires_at>clock_timestamp() AS dispatch_live,
                watch_expires_at>clock_timestamp() AS watch_live FROM guild.mc_agent_contacts
                WHERE operation_id=%s AND principal_id=%s AND environment=%s FOR UPDATE''',
                (body['request_ref'],service.principal,service.environment))
            row = cur.fetchone()
            if not row or self.targets.get(row['target_alias']) is None or asdict(self.targets[row['target_alias']]) != row['target'] or not self._allowed(service,row['conversation_id']):
                raise ValueError('agent_worker_scope_refused')
            approved=None
            if row['target']['recipient']=='claude-code':
                if not self.approved_handoffs:raise ValueError('approved_handoff_unavailable')
                approved=self.approved_handoffs.resolve(service,row['approved_handoff_ref'],self.targets[row['target_alias']],row['conversation_id'])
                if approved['sha256']!=row['authorization_sha256'] or approved['action']!=row['action']:
                    raise ValueError('approved_handoff_changed')
            if phase=='confirm':
                import time
                if (not approved or row['state']!='dispatched' or type(body['nonce']) is not str or
                        not row['nonce_sha256'] or not row['challenge_expiry'] or float(row['challenge_expiry'])<=time.time() or
                        not secrets.compare_digest(hashlib.sha256(body['nonce'].encode()).hexdigest(),row['nonce_sha256'])):
                    raise ValueError('agent_confirmation_refused')
                compiled,rule,refusal=service._admitting_policy('minimoi_agent_contact')
                if refusal is not None or compiled.version!=row['caller']['policy_version']:
                    raise ValueError('agent_confirmation_refused')
                current=Caller(**row['caller'],rule=rule,policy_tools=frozenset(compiled.tools),offered_tools=TOOLS)
                service._bind(conn,current)
                return {'admitted':True,'expires_at':float(row['challenge_expiry'])}
            if phase == 'claim':
                if row['state'] != 'pending': return {'state':'unknown','execute':False}
                if not row['dispatch_live']:
                    cur.execute("UPDATE guild.mc_agent_contacts SET state='not_started' WHERE operation_id=%s",(row['operation_id'],))
                    return {'state':'not_started','execute':False}
                compiled,rule,refusal = service._admitting_policy('minimoi_agent_contact')
                if refusal is not None or compiled.version != row['caller']['policy_version']:
                    return {'state':'not_started','execute':False}
                caller = Caller(**row['caller'],rule=rule,policy_tools=frozenset(compiled.tools),offered_tools=TOOLS)
                b = service._bind(conn,caller)  # Stop, policy/instance/epoch replacement: no dispatch.
                effect_args={'operation_id':str(row['operation_id']),'target':row['target'],'action_sha256':digest(row['action'])}
                if row.get('reply_protocol'):effect_args['reply_protocol_sha256']=digest(row['reply_protocol'])
                request = effects.request('agent.contact.existing',effect_args)
                effect = effects.begin_standing_effect(conn,b,request,operation_id=str(row['operation_id']),worker=service.worker)
                # An async queue handoff cannot yet verify native receipt. Persist
                # uncertainty now; native proof remains a separate explicit receipt.
                effect.unknown(conn)
                cur.execute("UPDATE guild.mc_agent_contacts SET state='dispatched' WHERE operation_id=%s",(row['operation_id'],))
            elif phase == 'observe':
                if row['state'] not in ('dispatched','unknown','acknowledged','result','status_report'):
                    return {'execute':False,'state':row['state']}
            else:
                return self._complete(service,cur,row,body)
            nonce = secrets.token_hex(32)
            cur.execute('''UPDATE guild.mc_agent_contacts SET nonce_sha256=%s,next_observe_at=clock_timestamp()+interval '30 seconds',
                challenge_expires_at=clock_timestamp()+interval '20 seconds' WHERE operation_id=%s
                RETURNING extract(epoch FROM challenge_expires_at) AS expires''',
                (hashlib.sha256(nonce.encode()).hexdigest(),row['operation_id']))
            expires = float(cur.fetchone()['expires'])
            descriptor={'execute':phase=='claim','request_ref':str(row['operation_id']),'nonce':nonce,
                    'expires_at':expires,'target':row['target'],'source':row['source'],'action':row['action']}
            if approved:descriptor['approved_handoff']=approved
            if row.get('reply_protocol'):descriptor['reply_protocol']=row['reply_protocol']
            return descriptor

    def _complete(self,service,cur,row,body):
        import time
        output = body['outcome']
        if (type(body['nonce']) is not str or not row['nonce_sha256'] or
                not secrets.compare_digest(hashlib.sha256(body['nonce'].encode()).hexdigest(),row['nonce_sha256']) or
                not row['challenge_expiry'] or float(row['challenge_expiry']) <= time.time() or
                type(output) is not dict or set(output) != {'state','request_id','proof'} or
                output['state'] not in ('dispatched','unknown','acknowledged','result','status_report','not_started') or
                not re.fullmatch(r'r-[0-9a-f]{20,40}',str(output['request_id'])) or
                type(output['proof']) is not dict or len(json.dumps(output).encode())>12000):
            raise ValueError('agent_worker_callback_refused')
        if row['native_request_id'] and row['native_request_id']!=output['request_id']:
            raise ValueError('agent_worker_callback_refused')
        proof = output['proof']
        protocol=row.get('reply_protocol')
        if protocol:
            from core.agent_requests.status_v2 import validate_protocol,validate_receipt
            validate_protocol(protocol,self.targets[row['target_alias']],output['request_id'],protocol.get('correlation'))
            if output['state'] in ('result','acknowledged'):raise ValueError('agent_worker_protocol_mismatch')
        if output['state']=='status_report':
            if not protocol or row['target']['recipient']!='codex':raise ValueError('agent_worker_protocol_mismatch')
            validate_receipt(proof,protocol,self.targets[row['target_alias']])
        if output['state']=='result' and row['target']['recipient']=='codex':
            if (proof.get('state')!='result' or type(proof.get('report')) is not str or
                    not proof['report'].strip() or not re.fullmatch('[0-9a-f]{64}',str(proof.get('result_sha256'))) or
                    not all(type(proof.get(k)) is str and proof[k] for k in ('turn_id','input_id','result_id'))):
                raise ValueError('agent_worker_callback_refused')
        if row['target']['recipient']=='claude-code' and output['state'] in ('acknowledged','result'):
            expected='reply_observed' if output['state']=='result' else 'pickup_observed'
            if (proof.get('state')!=expected or proof.get('task_complete') is not False or
                    not all(type(proof.get(k)) is str and proof[k] for k in ('receipt_uuid','msg_id','receipt_sha256')) or
                    not re.fullmatch('[0-9a-f]{64}',proof['receipt_sha256'])):
                raise ValueError('agent_worker_callback_refused')
            if output['state']=='result' and (proof.get('clean_native_turn_complete') is not False or
                    type(proof.get('report')) is not str or not proof['report'].strip() or
                    not re.fullmatch('[0-9a-f]{64}',str(proof.get('reply_sha256')))):
                raise ValueError('agent_worker_callback_refused')
        if row['outcome'] and row['state'] in ('result','status_report') and row['outcome']!=output:
            raise ValueError('agent_worker_result_changed')
        cur.execute('''UPDATE guild.mc_agent_contacts SET state=%s,native_request_id=%s,outcome=%s::jsonb
            WHERE operation_id=%s''',(output['state'],output['request_id'],json.dumps(output),row['operation_id']))
        # Notice is persisted before a separate insert/readback phase; this DB
        # transaction never claims delivery merely because show() was invoked.
        if output['state'] in ('result','status_report') and row['watch_live'] and not row['watch_cancelled'] and row['notice_text'] is None:
            lead='The enrolled Claude session replied (this does not establish task completion):\n\n' if row['target']['recipient']=='claude-code' else 'The enrolled Codex session completed its response and reported:\n\n'
            report=proof.get('report','')
            if output['state']=='status_report':
                lead='The enrolled Codex session provided an observed commentary status report (not build completion):\n\n'
                report=proof['proof']['report']
            text = (lead + report +
                    '\n\nThis is the agent\'s report, not independent build acceptance.')
            cur.execute('UPDATE guild.mc_agent_contacts SET notice_text=%s,notice_sha256=%s WHERE operation_id=%s',
                        (text,digest(text),row['operation_id']))
        return {'state':'recorded'}

    def _handoff_current(self,service,row):
        if row['target']['recipient']!='claude-code':return True
        try:
            if not self.approved_handoffs:return False
            value=self.approved_handoffs.resolve(service,row['approved_handoff_ref'],self.targets[row['target_alias']],row['conversation_id'])
            return value['sha256']==row['authorization_sha256'] and value['action']==row['action']
        except (ValueError,KeyError):return False

    def deliver_notices(self,service):
        """One bounded operator tick; no live turn and no native mutation."""
        count = 0
        with service._tx() as conn, _cursor(conn) as cur:
            cur.execute('''SELECT * FROM guild.mc_agent_contacts WHERE principal_id=%s AND environment=%s
                AND notice_text IS NOT NULL AND notice_delivered_at IS NULL AND NOT watch_cancelled
                AND watch_expires_at>clock_timestamp() ORDER BY created_at LIMIT 20 FOR UPDATE SKIP LOCKED''',
                (service.principal,service.environment))
            rows = cur.fetchall()
            for row in rows:
                if (self.targets.get(row['target_alias']) is None or asdict(self.targets[row['target_alias']])!=row['target'] or
                        not self._allowed(service,row['conversation_id']) or not self._handoff_current(service,row) or digest(row['notice_text'])!=row['notice_sha256']):
                    continue
                key = 'mc-native-watch-' + str(row['operation_id'])
                note = service.read_shown(row['conversation_id'],key)
                if note is None:
                    service.show(row['conversation_id'],key,row['notice_text'])
                    note = service.read_shown(row['conversation_id'],key)
                if note and note.get('who')=='guild_platform' and note.get('text')==row['notice_text']:
                    cur.execute('UPDATE guild.mc_agent_contacts SET notice_delivered_at=clock_timestamp() WHERE operation_id=%s',
                                (row['operation_id'],))
                    count += 1
        return count
