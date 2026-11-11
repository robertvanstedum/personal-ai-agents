"""Host-bound presentation candidates, not receipts. Only canonical readback proves display."""
import json
import os
import logging
import sqlite3
from pathlib import Path
from core.canonical_json import digest
from core.lead_wake.inbox import Inbox, Conflict
from services.helper_seats.presentation import accepted_result
from services.partnership_tasks.helper_return import displayed_text


class Inline:
    def __init__(self, inbox):
        self.inbox = inbox
        with inbox._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS inline_candidates(candidate_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, candidate TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS inline_receipts(operation_id TEXT PRIMARY KEY, proof TEXT NOT NULL)')

    def keep(self, candidate):
        key = digest(candidate)
        with self.inbox._db() as db:
            db.execute('INSERT OR IGNORE INTO inline_candidates VALUES(?,?,?)',
                       (key, candidate['task_id'], json.dumps(candidate, sort_keys=True)))

    def candidates(self, packet, principal, review_id, text):
        with self.inbox._db() as db:
            rows = db.execute('SELECT candidate FROM inline_candidates WHERE task_id=?', (packet['task_id'],)).fetchall()
        return [c for (raw,) in rows if (c := json.loads(raw))['result_digest'] == packet['result_digest']
                and c['review_id'] == review_id and c['text'] == text and c['principal'] == principal
                and all(c[k] == packet[k] for k in ('lead', 'environment', 'data_scope'))]

    def proved(self, op, candidate, receipt, answer_digest):
        proof = dict(candidate=candidate, receipt=receipt, answer_digest=answer_digest)
        raw = json.dumps(proof, sort_keys=True)
        with self.inbox._db() as db:
            old = db.execute('SELECT proof FROM inline_receipts WHERE operation_id=?', (op,)).fetchone()
            if old and old[0] != raw:
                raise Conflict('inline proof differs')
            db.execute('INSERT OR IGNORE INTO inline_receipts VALUES(?,?)', (op, raw))
        self.inbox.mark_presented(op, receipt)

    def reconcile(self, op, host):
        with self.inbox._db() as db:
            row = db.execute('SELECT proof FROM inline_receipts WHERE operation_id=?', (op,)).fetchone()
        if not row:
            return False
        proof = json.loads(row[0])
        saved = host.inline_answer(proof['candidate'])
        if not saved or saved[0] != proof['receipt']['id'] or digest(saved[1]) != proof['answer_digest']:
            raise Conflict('canonical inline proof differs')
        self.inbox.mark_presented(op, proof['receipt'])
        return True


def offer(**kwargs):
    # Acceptance already committed. A display preparation failure cannot undo it
    # or invite a repeat action. Keep delivery explicitly unconfirmed.
    from services.partnership_tasks.client import TaskServiceUnavailable
    try:
        return _offer(**kwargs)
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, TaskServiceUnavailable):
        logging.getLogger(__name__).warning('accepted helper presentation unconfirmed')
        return None


def _offer(*, lead, principal, environment, source_ref, task, reader, review_id):
    """Called only after authenticated owner-tool acceptance. Default off with W3."""
    if os.environ.get(lead.upper() + '_HELPER_RETURN') != '1':
        return None
    if os.environ['LEAD_RETURN_PRINCIPAL'] != principal or os.environ['PARTNERSHIP_ENVIRONMENT'] != environment:
        raise ValueError('inline host identity differs')
    root = Path(os.environ['LEAD_RETURN_STATE_DIR'])
    if not root.is_absolute():
        raise ValueError('absolute inline state required')
    result, rid = accepted_result(task, lead, reader.get, review_id)
    text = displayed_text(lead, task['holder'], result['answer'])
    candidate = dict(task_id=task['task_id'], result_digest=task['artifact_sets'][-1]['digest'],
                     review_id=rid, principal=principal, lead=lead, environment=environment,
                     data_scope=task['data_scope'], source_ref=source_ref, text=text)
    Inline(Inbox(root/'inbox.sqlite', None)).keep(candidate)
    return text
