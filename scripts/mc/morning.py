#!/usr/bin/env python3
"""Preparation by default. Scheduler/registry installation belongs to root.

--adoption-confirmed is an operator attestation, never an agent ACK generator.
Run only after preserving actual owner approval and real MC acknowledgement.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.jobs import registry, schedule, status
from minimoi_portal.guild_ui.mc.cos import ChiefOfStaffBackend
from minimoi_portal.guild_ui.mc.morning import MorningError, ZONE, run_once
from minimoi_portal.guild_ui.stores import FloorStores


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--adoption-confirmed', action='store_true')
    parser.add_argument('--registry', type=Path)
    parser.add_argument('--reminder-id', type=int)
    parser.add_argument('--supervised-create-first', action='store_true')
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({'plan_only': True, 'job_id': 'mc-morning',
                          'adoption_attested': args.adoption_confirmed,
                          'calls_performed': 0, 'writes_performed': 0}))
        return 0
    if not args.adoption_confirmed:
        raise MorningError('adoption_required')
    if (args.reminder_id is None) == (not args.supervised_create_first):
        raise MorningError('choose_verified_reminder_or_supervised_create')
    job = registry.load(args.registry).get('mc-morning')
    if job is None or job.host != os.environ.get('MINIMOI_JOBS_HOST'):
        raise MorningError('registered_host_required')
    if job.schedule.get('tz') != 'America/Chicago' or job.schedule.get('kind') != 'daily':
        raise MorningError('schedule_invalid')
    root_raw = os.environ.get('MINIMOI_JOBS_DIR')
    dsn = os.environ.get('DATABASE_URL')
    if not root_raw or not dsn:
        raise MorningError('configuration_required')
    root = Path(root_raw)
    # Existing scheduler-private root only: do not reclaim a shared folder.
    s = root.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) != 0o700:
        raise MorningError('status_root_refused')
    lockfd = os.open(root / '.mc-morning.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        ls = os.fstat(lockfd)
        if not stat.S_ISREG(ls.st_mode) or ls.st_uid != os.geteuid() or ls.st_nlink != 1 or stat.S_IMODE(ls.st_mode) != 0o600:
            raise MorningError('lock_refused')
        try:
            fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'result': 'already_running'}))
            return 0
        now = datetime.now(timezone.utc)
        local = now.astimezone(ZONE)
        if local.hour >= 6:
            raise MorningError('outside_batch_window')
        prior = status.read_status(root, job.status_file, job.id)
        finished = status.parse_time((prior or {}).get('finished_at'))
        if prior and prior.get('host') == job.host and prior.get('state') == 'ok' and finished and finished.astimezone(ZONE).date() == local.date():
            print(json.dumps({'result': 'already_completed'}))
            return 0
        outcome_path = root / 'mc-morning-outcome.json'
        if outcome_path.exists() or outcome_path.is_symlink():
            if outcome_path.is_symlink() or outcome_path.stat().st_size > 16000:
                raise MorningError('outcome_refused')
            previous = json.loads(outcome_path.read_text())
            if not isinstance(previous, dict) or not isinstance(previous.get('request_id'), str):
                raise MorningError('outcome_refused')
            if (previous.get('code') not in {'noop','added','edited','unchanged','repeated'}
                    or previous['request_id'].endswith(local.date().isoformat())):
                # Even failed parsing after dispatch may have spent money or
                # executed native tools. Never infer that retry is harmless.
                raise MorningError('same_day_attempt_requires_supervised_disposition')
        running = status.start_run(root, job, secrets.token_hex(16), now)
        outcome = 'failed'
        code = 'execution_failed'
        try:
            backend = ChiefOfStaffBackend.from_env(os.environ)
            floor = FloorStores(lambda: dsn)  # canonical Guild floor, no schema creation
            result = run_once(floor, backend, now=now, reminder_id=args.reminder_id,
                record=lambda receipt: status.write_json_atomic(root, 'mc-morning-outcome.json', receipt))
            code = result['code']
            outcome = 'ok'
        except MorningError as error:
            code = str(error)
        except Exception:
            code = 'execution_failed'  # no secret exception or model/source text
        end = datetime.now(timezone.utc)
        status.finish_run(root, job, running, state=outcome, now=end,
                          exit_code=0 if outcome == 'ok' else 1,
                          summary='morning glance completed' if outcome == 'ok' else 'morning glance failed',
                          results={'board': code}, next_due_by=schedule.next_fire(job.schedule, end))
        print(json.dumps({'state': outcome, 'result': code}))
        return 0 if outcome == 'ok' else 1
    finally:
        os.close(lockfd)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print(json.dumps({'state': 'failed', 'result': 'preflight_failed'}))
        raise SystemExit(1)
