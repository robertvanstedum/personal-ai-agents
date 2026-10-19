"""Runs one proving journey through the authenticated dev client, inside D23's limits.

The operator builds and logs in the GuildClient (proving_driver: dev origin, test identity,
server-reported dev marker) and opens the Budget ledger (proving_budget). This runner then:

- holds one lock for the whole run, so two runners never interleave a journal;
- refuses a journey longer than the turn limit before anything is sent, and never starts a
  step that could run past the time limit (each request may take the driver's full timeout);
- reserves a conservative estimate for every MC turn before it is sent, and releases it to
  zero only when the journaled answer is the portal's own (status 'answered', backend_kind
  'platform'); every other outcome stays charged on the day it was admitted;
- stops at the first refusal, error or uncertain outcome, and never retries. An HTTP 200
  that is not an answer (status not_kept, error, unavailable, job, ...) or an answer from a
  stub or unknown backend also stops the run, with its reservation kept;
- records a platform operation reported as not saved (operation.saved false) as a failed
  capability: an honest deterministic refusal, never a success;
- turns the journal into a contract-v2 run record with the receipt collector.

No model, Docker or store access of its own: the stores are read by the operator into
``snapshots`` after the run.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from proving_budget import BudgetRefused, micro
from proving_driver import Refused
import proving_receipts
from proving_receipts import MC_BACKEND_KINDS

REQUEST_TIMEOUT_S = 180            # proving_driver's per-request timeout
MAX_TURNS = 50
MAX_SECONDS = 4 * 3600


class RunRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class Limits:
    max_turns: int
    max_seconds: float
    estimate_usd: str              # conservative per-turn reservation, as a decimal string

    def __post_init__(self):
        if type(self.max_turns) is not int or not 1 <= self.max_turns <= MAX_TURNS:
            raise RunRefused('max_turns_out_of_range')
        if isinstance(self.max_seconds, bool) or not isinstance(self.max_seconds, (int, float)) \
                or not 2 * REQUEST_TIMEOUT_S <= self.max_seconds <= MAX_SECONDS:
            raise RunRefused('max_seconds_out_of_range')
        if not isinstance(self.estimate_usd, str) or micro(self.estimate_usd) <= 0:
            raise RunRefused('positive_estimate_required')


@contextmanager
def run_lock(directory):
    """One run per journal across processes, held from the first request to the last."""
    fd = os.open(Path(directory) / '.run.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunRefused('another_proving_run_is_active') from None
        yield
    finally:
        os.close(fd)


def _now():
    return datetime.now(timezone.utc)


class Runner:
    def __init__(self, client, budget, limits, *, clock=time.monotonic, wall=_now):
        if not isinstance(limits, Limits):
            raise RunRefused('limits_required')
        if not client.csrf or not client.environment:
            raise RunRefused('log_in_and_verify_the_dev_marker_first')
        self.client, self.budget, self.limits = client, budget, limits
        self.clock, self.wall = clock, wall
        self._pending = None
        client.admit = self._admit     # every MC turn this client sends is admitted here

    def _admit(self):
        if self._pending is None:
            raise RunRefused('mc_turn_outside_the_runner')
        return self.budget.reserve(self._pending, self.limits.estimate_usd)

    def run(self, texts, *, fresh_conversation_per_turn=False):
        """Send each owner text as one MC turn. Returns the run summary; never raises for a
        refusal or failure once the run has started (it is the summary's stop_reason)."""
        if not isinstance(texts, list) or not texts or not all(isinstance(t, str) for t in texts):
            raise RunRefused('owner_texts_required')
        if len(texts) > self.limits.max_turns:
            raise RunRefused('journey_exceeds_turn_limit')
        journal = self.client.journal.path
        with run_lock(journal):
            if any(journal.glob('*.json')):
                raise RunRefused('fresh_journal_required')
            summary = {'started_utc': self.wall().isoformat(), 'stop_reason': 'completed', 'turns': [],
                       'limits': {'max_turns': self.limits.max_turns, 'max_seconds': self.limits.max_seconds,
                                  'estimate_usd': self.limits.estimate_usd}}
            start, conversation = self.clock(), None
            for text in texts:
                fresh = conversation is None or fresh_conversation_per_turn
                worst = (3 if fresh else 2) * REQUEST_TIMEOUT_S
                if self.clock() - start + worst > self.limits.max_seconds:
                    summary['stop_reason'] = 'time_limit'
                    break
                try:
                    if fresh:
                        conversation = self.client.conversation()
                    turn = self._turn(conversation, text)
                    summary['turns'].append(turn)
                    if turn['stop']:
                        summary['stop_reason'] = turn['stop']
                        break
                except (Refused, BudgetRefused, RunRefused) as exc:
                    summary['stop_reason'] = f'refused:{exc}'
                    break
                except Exception as exc:          # a timeout or transport error: the outcome is unknown
                    summary['stop_reason'] = f'uncertain:{type(exc).__name__}'
                    break
            summary['finished_utc'] = self.wall().isoformat()
        return summary

    def _turn(self, conversation, text):
        key = 'proving-' + uuid4().hex
        self.client.write('/notes', {'idempotency_key': key, 'conversation_id': conversation, 'text': text}, key)
        self._pending = key
        try:
            answer = self.client.write('/mc/turns', {'note_request_id': key, 'request_id': key,
                                                     'conversation_id': conversation}, key + '-turn')
        finally:
            self._pending = None
        status, kind = answer.get('status'), answer.get('backend_kind')
        turn = {'request_id': key, 'conversation_id': conversation, 'status': status, 'backend_kind': kind,
                'backend_turn_id': answer.get('backend_turn_id'), 'stop': None, 'capability_failed': False,
                'settlement': {'usd': None, 'source': 'estimate_held', 'receipt_ids': []}}
        if status != 'answered':
            turn['stop'] = f'not_answered:{status}'
        elif kind == 'platform':
            turn['settlement'] = self.budget.settle_platform(key, answer)
            operation = answer.get('operation')
            turn['capability_failed'] = isinstance(operation, dict) and operation.get('saved') is False
        elif kind not in MC_BACKEND_KINDS:
            turn['stop'] = f'unrecognized_backend:{kind}'
        return turn


def spend_admission(client):
    """The run's admission as the run record states it: every MC turn admitted, with each reservation."""
    journal = client.journal.path
    reservations = []
    for intent in sorted(journal.glob('*-turn-intent.json')):
        reservations.append(json.loads(intent.read_text()).get('spend_admission'))
    admitted = bool(reservations) and all(isinstance(a, dict) and a.get('admitted') is True for a in reservations)
    first = reservations[0] if admitted else {}
    return {'admitted': admitted, 'cap_usd': 25, 'day_spent_before_usd': first.get('day_spent_before_usd'),
            'decided_utc': first.get('decided_utc'), 'reservations': reservations}


def run_record(client, summary, meta, snapshots):
    """A contract-v2 run record from the journal. ``meta`` carries what only the operator knows
    (suite_version, journey, commit, mc_policy_sha256, model_route); identity, the server-reported
    dev marker, run times and admission come from the client and the run."""
    meta = {**meta, 'env': 'dev', 'identity': {'principal': client.principal, 'test': True},
            'env_marker': client.environment, 'expected_instance_id': client.instance_id,
            'started_utc': summary['started_utc'], 'finished_utc': summary['finished_utc'],
            'spend_admission': spend_admission(client)}
    exchanges = proving_receipts.exchanges_from_journal(client.journal.path)
    record = proving_receipts.build_run_record(meta, exchanges, snapshots)
    record['run'] = {'stop_reason': summary['stop_reason'], 'limits': summary['limits'], 'turns': summary['turns'],
                     'capability_failures': [t['request_id'] for t in summary['turns'] if t.get('capability_failed')]}
    if summary['stop_reason'] != 'completed':
        record['errors'].append(f"run stopped: {summary['stop_reason']}")
    return record


def write_record(path, record):
    """Write once, private; an existing record is never replaced."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump(record, out, indent=1, sort_keys=True)
        out.flush()
        os.fsync(out.fileno())
