#!/usr/bin/env python3
"""One read-only GitHub poll, plus its operator steps. Registers no scheduler and activates nothing.

  (no flag)      print the plan; no I/O
  --initialize   create the private cache (explicit host preparation, once)
  --sync         one poll; refuses until due (300 s) and while another writer holds the lock
  --health       read-only stream health from the cache; no network, no write
  --rebind       supervised step after a kernel restart: adopt this boot as the writer

Run it by path from anywhere (no PYTHONPATH needed), e.g. in the portal container:
  docker exec minimoi-portal python3 /app/scripts/mc/github_awareness.py --sync

--sync holds the cache's one writer admission for the whole run: the job-status start, the
poll and the job-status finish. A competitor refused at admission (sync_busy) writes no
status, so it never replaces the active run's receipt. A run that is not yet due (300 s)
exits 0 with "not_due" and writes no status either.

--sync writes a job status file (core/jobs/status.py) only when the jobs registry has an
entry with id JOB_ID; until that reviewed entry exists the job is unregistered and reports
nothing. The status file holds stream names and health codes only, never GitHub text.
"""
import argparse, json, os, sys, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.github_awareness import Admission, due, initialize, sync_admitted, health, rebind, PublicClient, REPO

ROOT = '/app/runtime/mc-observations/github-awareness'
JOB_ID = 'mc-github-awareness'
INTERVAL_S = 300


def _registered_job():
    from core.jobs import registry
    try:
        return registry.load().get(JOB_ID)
    except Exception:
        return None


def _poll(token, now):
    with Admission(ROOT) as admission:          # sync_busy is raised here, before any status write
        if not due(admission, now=now):
            return {'state': 'not_due', 'job_status_written': False, 'provider_calls': 0}
        return _poll_admitted(admission, token, now)


def _poll_admitted(admission, token, now):
    from core.jobs import status
    job = _registered_job()
    jobs_root = os.environ.get('MINIMOI_JOBS_ROOT')
    running = None
    if job is not None and jobs_root:
        running = status.start_run(jobs_root, job, uuid.uuid4().hex[:32], now)
    try:
        result = sync_admitted(admission, PublicClient(token=token), now=now)
    except Exception as error:
        if running is not None:
            status.finish_run(jobs_root, job, running, state='failed', now=datetime.now(timezone.utc), exit_code=1,
                              summary='poll held: ' + type(error).__name__[:60],
                              results={}, next_due_by=now + timedelta(seconds=INTERVAL_S))
        raise
    healths = {name: row['health'] for name, row in result['streams'].items()}
    if running is not None:
        attempted = [h for h in healths.values() if h != 'never_synced']
        state = 'ok' if attempted and all(h == 'ok' for h in attempted) else 'warn'
        status.finish_run(jobs_root, job, running, state=state, now=datetime.now(timezone.utc), exit_code=0,
                          summary='rotating poll; see per-stream health', results=healths,
                          next_due_by=now + timedelta(seconds=INTERVAL_S))
    return {'generation': result['generation'], 'stream_health': healths, 'job_status_written': running is not None,
            'provider_calls': 0}


def main():
    p = argparse.ArgumentParser()
    mode = p.add_mutually_exclusive_group()
    for flag in ('--initialize', '--sync', '--health', '--rebind'):
        mode.add_argument(flag, action='store_true')
    p.add_argument('--use-configured-read-token', action='store_true')
    a = p.parse_args()
    now = datetime.now(timezone.utc)
    if a.initialize:
        initialize(ROOT, now=now)
        print('{"initialized":true,"github_requests":0}')
    elif a.health:
        print(json.dumps(health(ROOT, now=now), sort_keys=True))
    elif a.rebind:
        doc = rebind(ROOT, now=now)
        print(json.dumps({'rebound': True, 'generation': doc['generation'], 'github_requests': 0}))
    elif a.sync:
        token = os.environ.get('MINIMOI_GITHUB_READ_TOKEN') if a.use_configured_read_token else None
        if a.use_configured_read_token and not token:
            raise ValueError('configured_read_token_unavailable')
        print(json.dumps(_poll(token, now), sort_keys=True))
    else:
        print(json.dumps({'plan_only': True, 'repo': REPO, 'interval_seconds': INTERVAL_S,
                          'anonymous_max_requests_per_poll': 3, 'anonymous_nominal_max_requests_per_hour': 36,
                          'coverage': 'rotating streams, not every stream every five minutes', 'writes': 0,
                          'job_id': JOB_ID, 'job_registered': _registered_job() is not None}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Safe class only: network exceptions may contain URLs/headers/credentials.
        print(json.dumps({'state': 'held', 'error_class': type(error).__name__}))
        raise SystemExit(1)
