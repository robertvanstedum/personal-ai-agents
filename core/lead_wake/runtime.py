"""Exact-result review consumer. Explicit construction only; no import-time worker.

Read and commit credentials are distinct. The task service atomically commits
review+publication. Once committed, delivery belongs to the publication outbox,
not to current wake eligibility. Unknown inference never gets another attempt.
"""
import json
import math
import uuid
from core.canonical_json import digest
from core.lead_wake.inbox import StaleWake, Conflict
from services.partnership_tasks.client import command
from services.helper_seats.spend import CapReached


class Runtime:
    def __init__(self, *, inbox, reader, committer, principal, lead, environment,
                 host, reviewer, charters, ledger_for, review_bound, review_reserve):
        if lead not in ('mc', 'cos') or not principal or not environment:
            raise ValueError('host identity required')
        if not all(math.isfinite(v) for v in (review_bound,review_reserve)) or review_bound <= 0 or review_reserve < review_bound:
            raise ValueError('review budget bound required')
        self.inbox, self.reader, self.committer = inbox, reader, committer
        self.principal, self.lead, self.environment = principal, lead, environment
        self.host, self.reviewer, self.charters = host, reviewer, charters
        self.ledger_for, self.review_bound, self.review_reserve = ledger_for, review_bound, review_reserve
        with inbox._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS return_commands(operation_id TEXT PRIMARY KEY, command TEXT NOT NULL)')

    def publication(self, op):
        row = self.reader.get('outbox', 'OB-publish-'+op[8:])
        if row is None:
            return None
        p = row['packet']
        if (row['operation'] != 'publish_return' or row['destination'] != 'lead_publication'
                or row['bundle_digest'] != digest(p) or p['wake_operation_id'] != op
                or p['principal'] != self.principal or p['lead'] != self.lead
                or p['environment'] != self.environment or p['review'] != self.inbox.review(op)):
            raise Conflict('publication differs')
        return row

    def context(self, packet):
        task = self.reader.get('task', packet['task_id'])
        source = self.reader.get('source_event', packet['source_event_id'])
        if not task or not source or not task['artifact_sets']:
            raise StaleWake('missing evidence')
        current = task['artifact_sets'][-1]
        if current['digest'] != packet['result_digest'] or source['source'] != packet['source_ref']:
            raise StaleWake('changed evidence')
        found = self.charters.chartered(packet['charter_ref'])
        if found is None:
            raise StaleWake('charter unavailable')
        charter, kind = found
        if self.lead not in charter.leads or packet['data_scope'] not in charter.scopes:
            raise StaleWake('charter scope changed')
        artifacts = []
        for ref in current['artifacts']:
            if ref['store'] != 'task_service':
                raise ValueError('unsupported helper evidence')
            row = self.reader.get('source_event', ref['id'])
            if (row is None or row['data_scope'] != packet['data_scope']
                    or row['source']['store'] != 'helper-result'
                    or row['provenance']['actor_seat'] != charter.seat
                    or digest(row['content']) != ref['digest']):
                raise ValueError('unverified helper evidence')
            artifacts.append(row['content'])
        return task, charter, kind, {'request': source['content'], 'results': artifacts,
                                    'drafted_by': charter.seat}

    def reserve(self, op, task, charter, kind):
        ledger = self.ledger_for(charter)
        cap = kind['per_task_budget_usd']
        ledger.allocate_task(task['task_id'], task_cap=cap, review_reserve=self.review_reserve)
        ledger.reserve('review-'+op, bound_usd=self.review_bound, task_spent=0,
                       task_cap=cap, purpose='lead-review', task_id=task['task_id'], part='review')
        # Conservative bound remains charged until independently verified usage;
        # provider prose and token guesses never release spend.

    def commit(self, op, packet, result):
        with self.inbox._db() as db:
            row = db.execute('SELECT command FROM return_commands WHERE operation_id=?',(op,)).fetchone()
        if row:
            cmd = json.loads(row[0])
        else:
            task = self.reader.get('task',packet['task_id'])
            cmd = command('task.commit_helper_return','task',packet['task_id'],
                dict(wake_operation_id=op,result_digest=packet['result_digest'],**result),
                command_id='cmd-return-'+op[8:],environment=self.environment,
                scope=packet['data_scope'],expected_revision=task['revision'])
            raw = json.dumps(cmd,sort_keys=True)
            with self.inbox._db() as db:
                db.execute('INSERT OR IGNORE INTO return_commands VALUES(?,?)',(op,raw))
                kept = db.execute('SELECT command FROM return_commands WHERE operation_id=?',(op,)).fetchone()[0]
            if kept != raw:
                raise Conflict('commit command changed')
        result = self.committer.submit(cmd)  # client reconciles lost ACK by exact command
        if result['outcome'] != 'accepted':
            with self.inbox._db() as db:
                db.execute("UPDATE entries SET state='commit_blocked' WHERE operation_id=? AND state='reviewed'",(op,))
                db.execute("UPDATE reviews SET failure='commit_refused' WHERE operation_id=?",(op,))
            return False
        return self.publication(op) is not None

    def pass_once(self):
        report=[]
        for op, packet in self.inbox.pending():
            try:
                # A committed result survives Stop. Never ask current wake
                # authority before reconciling this immutable intent.
                if self.publication(op):
                    report.append((op,'committed'))
                    continue
                if self.inbox.resolve(op) != packet:
                    raise StaleWake('wake changed')
                with self.host.lane(op,packet) as admitted:
                    if not admitted:
                        report.append((op,'busy'))
                        continue
                    result = self.inbox.review(op)
                    if result is None:
                        task,charter,kind,context = self.context(packet)
                        if task['state']=='completed' or any(r['reviewer']==self.lead and r['reviewed_set_digest']==packet['result_digest'] for r in task['reviews']):
                            # Inline review is not a canonical presentation receipt.
                            # Avoid extra spend, but keep delivery unproven explicitly.
                            with self.inbox._db() as db:
                                db.execute("UPDATE entries SET state='reviewed_elsewhere' WHERE operation_id=? AND state='queued'",(op,))
                            report.append((op,'reviewed_elsewhere'))
                            continue
                        claim=uuid.uuid4().hex
                        if not self.inbox.claim(op,claim):
                            continue
                        try:
                            try:
                                self.reserve(op,task,charter,kind)
                            except CapReached:
                                self.inbox.budget_blocked(op,claim)
                                report.append((op,'budget_blocked'))
                                continue
                            if self.inbox.resolve(op) != packet:
                                raise StaleWake('wake changed')
                            result=self.reviewer(packet,context)
                            self.inbox.save_review(op,claim,result)
                        except StaleWake:
                            raise
                        except Exception:
                            self.inbox.uncertain(op,claim)
                            report.append((op,'uncertain'))
                            continue
                    admitted.check()
                    if self.inbox.resolve(op) != packet:
                        raise StaleWake('wake changed')
                    report.append((op,'committed' if self.commit(op,packet,result) else 'commit_refused'))
            except StaleWake:
                self.inbox.supersede(op)
                report.append((op,'superseded'))
            except Exception:
                report.append((op,'unavailable'))
        return report
