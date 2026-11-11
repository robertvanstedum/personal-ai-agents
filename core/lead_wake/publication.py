"""Canonical publication destination. Receipts prove persisted exact bytes.

Absent lookup fences a send attempt under the same SQLite lock as delivery.
The authenticated task reader supplies immutable intents, never HTTP body text.
Task cancellation after commit cannot erase the intent; disclosure can still fail.
"""
import json
import re
from core.canonical_json import digest
from services.partnership_tasks.helper_return import displayed_text
from core.lead_wake.inbox import Conflict, StaleWake


class Publication:
    def __init__(self, *, journal, reader, host, principal, lead, environment):
        self.journal, self.reader, self.host = journal, reader, host
        self.principal, self.lead, self.environment = principal, lead, environment
        with journal._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS publication_absent(operation_id TEXT, attempt INTEGER, PRIMARY KEY(operation_id,attempt))')

    def resolve(self, operation_id):
        if not isinstance(operation_id,str) or not re.fullmatch(r'OB-publish-[0-9a-f]{40}',operation_id):
            raise ValueError('invalid publication id')
        row=self.reader.get('outbox',operation_id)
        if row is None:
            raise StaleWake('publication unavailable')
        p=row['packet']
        if (row['operation_id'] != operation_id or row['operation'] != 'publish_return'
                or row['destination'] != 'lead_publication' or row['bundle_digest'] != digest(p)
                or p['kind'] != 'helper_return_committed' or p['principal'] != self.principal
                or p['lead'] != self.lead or p['environment'] != self.environment
                or p['data_scope'] != 'personal' or p['review_digest'] != digest(p['review'])
                or p['text'] != displayed_text(p['lead'],p['drafted_by'],p['review']['answer']) or p['id'] != 'consult:'+operation_id
                or p['source_ref']['store'] != {'mc':'mc-floor','cos':'cos-history'}[self.lead]
                or p['source_ref']['id'] != p['conversation_id']+':'+p['source_turn_id']):
            raise Conflict('publication binding differs')
        # A fresh authenticated read rechecks revoked binding/scope on every call.
        source=self.reader.get('source_event',p['source_event_id'])
        if (source is None or source['source'] != p['source_ref'] or source['data_scope'] != p['data_scope']
                or source['provenance']['origin'] != 'platform'):
            raise Conflict('publication source differs')
        return row

    @staticmethod
    def attempted(row,attempt):
        sends=sum(a['observation']=='attempted' for a in row['attempts'])
        if row['state'] not in ('attempted','unknown','acknowledged') or sends!=attempt:
            raise StaleWake('publication attempt not dispatched')

    @staticmethod
    def receipt(row):
        return {'store':'lead_publication','id':row['operation_id'], 'edition':None, 'digest':row['bundle_digest']}

    def deliver(self, operation_id, payload_digest, *, task_id, kind, attempt):
        if type(attempt) is not int or attempt < 1:
            raise ValueError('invalid attempt')
        row=self.resolve(operation_id)
        self.attempted(row,attempt)
        if (row['bundle_digest'] != payload_digest or row['task_id'] != task_id
                or row['packet']['kind'] != kind):
            raise Conflict('publication request differs')
        with self.host.lane(operation_id,row['packet']) as lane:
            if not lane:
                raise RuntimeError('conversation busy')
            with self.journal._db() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT 1 FROM publication_absent WHERE operation_id=? AND attempt=?',(operation_id,attempt)).fetchone():
                    raise StaleWake('attempt fenced')
                # Both canonical writers validate ownership/privacy at write and
                # compare actual persisted text/id. No receipt from a queue.
                lane.check()
                self.host.write(row['packet'])
                if not self.host.read(row['packet']):
                    raise RuntimeError('publication readback unconfirmed')
        self.journal.mark_presented(row['packet']['wake_operation_id'],
            {k:row['packet'][k] for k in ('store','id','result_digest')})
        return {'receipt':self.receipt(row)}

    def lookup(self, operation_id, *, attempt):
        if type(attempt) is not int or attempt < 1:
            raise ValueError('invalid attempt')
        row=self.resolve(operation_id)
        self.attempted(row,attempt)
        with self.journal._db() as db:
            db.execute('BEGIN IMMEDIATE')
            applied=self.host.read(row['packet'])
            if not applied:
                db.execute('INSERT OR IGNORE INTO publication_absent VALUES(?,?)',(operation_id,attempt))
        if applied:
            self.journal.mark_presented(row['packet']['wake_operation_id'],
                {k:row['packet'][k] for k in ('store','id','result_digest')})
            return {'state':'applied','receipt':self.receipt(row)}
        return {'state':'absent'}
