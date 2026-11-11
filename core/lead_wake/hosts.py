"""Native conversation lanes and canonical stores for service-origin returns.

No fake user message, runtime session or action/tool binding is created.
Current account access is checked by a host-supplied validator on every operation.
"""
from contextlib import contextmanager
import hashlib
import uuid
from contextvars import ContextVar
from core.lead_wake.inbox import StaleWake, Conflict


def source_ids(packet, lead):
    ref=packet['source_ref']
    if ref['store'] != {'mc':'mc-floor','cos':'cos-history'}[lead]:
        raise StaleWake('wrong canonical source')
    parts=ref['id'].split(':')
    if len(parts)!=2 or not all(parts):
        raise StaleWake('invalid canonical source')
    return parts


class Admission:
    def __init__(self, check): self.check=check


class MCHost:
    def __init__(self, *, conversations, floor, ledger, principal, environment, access):
        self.conversations,self.floor,self.ledger=conversations,floor,ledger
        self.principal,self.environment,self.access=principal,environment,access
        self._claim=ContextVar("mc_return_claim",default=None)

    def _bound(self,p):
        if (p['lead']!='mc' or p['environment']!=self.environment or p['data_scope']!='personal'
                or p.get('principal',self.principal)!=self.principal or not self.access(self.principal)):
            raise PermissionError('current disclosure unavailable')
        cid,tid=source_ids(p,'mc')
        from minimoi_portal.guild_ui.conversations import ID_RE
        if not ID_RE.fullmatch(cid):raise StaleWake('invalid conversation')
        conv=self.conversations._read(cid,self.principal)
        if not conv or conv['principal']!=self.principal or conv.get('archived'):
            raise PermissionError('conversation unavailable')
        floor=self.floor.for_floor(conv['notes_floor'])
        if floor.get_owner_note(tid,self.principal) is None:
            raise PermissionError('canonical owner source unavailable')
        return floor

    @contextmanager
    def lane(self, op, p):
        cid,tid=source_ids(p,'mc')
        with self.conversations._lock(): self._bound(p)
        # Fresh service admission identity permits reconciliation attempts; the
        # durable inbox claim, not this lane identity, controls one inference.
        key='OB-wake-'+hashlib.sha256((op+uuid.uuid4().hex).encode()).hexdigest()[:40]
        admission=self.ledger.admit_service_review(principal=self.principal,conversation_id=cid,
                          source_note_request_id=tid,operation_id=key)
        if admission.result!='claimed':
            yield None
            return
        def check():
            if not self.ledger.heartbeat(admission.claim):
                raise RuntimeError('conversation lane lost')
        try:
            with self.ledger.held(admission.claim):
                token=self._claim.set(admission.claim)
                try:yield Admission(check)
                finally:self._claim.reset(token)
        finally:
            # No agent-runtime call was issued. An uncertain gateway review is
            # fenced by its inbox claim and need not pause future owner chat.
            self.ledger.finish(admission.claim,'not_sent',{'reason':'service_review_released'})

    def read(self,p):
        from minimoi_portal.guild_ui.stores import MASTER_CRAFTSMAN
        with self.conversations._lock():
            floor=self._bound(p)
            note=floor.get_note(p['id'])
            if note is None:return False
            if note['text']!=p['text'] or note['who']!=MASTER_CRAFTSMAN.id:
                raise Conflict('canonical note differs')
            return True

    def write(self,p):
        from minimoi_portal.guild_ui.stores import MASTER_CRAFTSMAN
        with self.conversations._lock():
            floor=self._bound(p)
            claim=self._claim.get()
            if claim is None:raise RuntimeError('canonical lane required')
            with self.ledger._tx(self.principal) as cur:
                cur.execute("SELECT 1 FROM guild.mc_turn_ledger WHERE id=%s AND principal=%s AND state='claimed' "
                            "AND claim_token=%s AND claim_generation=%s AND lease_until>clock_timestamp() FOR UPDATE",
                            (claim.ledger_id,self.principal,claim.token,claim.generation))
                if cur.fetchone() is None:raise RuntimeError('canonical lane lost')
                result=floor.add_note(p['id'],p['text'],MASTER_CRAFTSMAN,page='mc',transaction=cur.connection)
                if result.outcome!='kept':raise Conflict('canonical write differs')


class CoSHost:
    def __init__(self, *, history, lane, principal, environment, access, settled):
        if lane is None:raise ValueError('shared CoS lane required')
        self.history,self.execution_lane,self.settled=history,lane,settled
        self.principal,self.environment,self.access=principal,environment,access

    def _bound(self,p):
        from core import durable_files as fsio
        cid,tid=source_ids(p,'cos')
        if (p['lead']!='cos' or p['environment']!=self.environment or p['data_scope']!='personal'
                or self.history.environment!=self.environment or p.get('principal',self.principal)!=self.principal or not self.access(self.principal)):
            raise PermissionError('current disclosure unavailable')
        conv=self.history.conversation(cid)
        if (not conv or conv['private'] or conv['read_only'] or conv['environment']!=self.environment
                or conv['data_scope']!='personal'):
            raise PermissionError('conversation unavailable')
        source=self.history._turn_locked(cid,tid)
        if source['status']=='pending':raise RuntimeError('source turn unsettled')
        return cid

    @contextmanager
    def lane(self,op,p):
        from domains.cos.history import _fold
        from core import durable_files as fsio
        with self.execution_lane.hold(blocking=False) as acquired:
            if not acquired:
                yield None
                return
            with self.history._locked():
                self._bound(p)
                pending=not self.settled(p['source_ref']) or any(t['status']=='pending' for path in (self.history.root/'turns').glob('*.jsonl')
                            for t in _fold(fsio.read_stream(path)).values())
            if pending:
                yield None
                return
            yield Admission(lambda:None)  # flock remains held until exit

    @staticmethod
    def note(p):
        return {'schema':'minimoi.cos.stream_note/1','note_id':p['id'],
                'conversation_id':p['conversation_id'],'kind':'consult',
                'created_at':p['created_at'],'text':p['text']}

    def read(self,p):
        from core import durable_files as fsio
        with self.history._locked():
            cid=self._bound(p)
            for row in fsio.read_stream(self.history._notes_path(cid)):
                if row['note_id']==p['id']:
                    if row!=self.note(p):raise Conflict('canonical note differs')
                    return True
            return False

    def write(self,p):
        from core import durable_files as fsio
        from domains.cos.store import contracts
        note=self.note(p)
        if contracts.errors('conversation','stream_note',note):raise ValueError('invalid canonical note')
        with self.history._locked():
            cid=self._bound(p)
            path=self.history._notes_path(cid)
            for row in fsio.read_stream(path):
                if row['note_id']==p['id']:
                    if row!=note:raise Conflict('canonical note differs')
                    return
            self.history._append_verified(path,note)
