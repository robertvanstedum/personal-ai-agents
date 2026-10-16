#!/usr/bin/env python3
"""Default offline plan. No registry/scheduler installation or authority creation."""
import argparse
from datetime import datetime,timezone
import fcntl,json,os,secrets,stat,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from core.jobs import registry,status,schedule
from minimoi_portal.rooms_archive_caller import from_env
from minimoi_portal.guild_ui.rooms_archive_api import _private_dir,_read
from minimoi_portal.guild_ui.rooms_archive_runner import run_once,batch_deadline


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');parser.add_argument('--registry',type=Path);args=parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({'dry_run':True,'job_id':'rooms-archive','threshold_days':30,'calls_performed':0,'writes_performed':0,'activation':'not_installed'}));return 0
    now=datetime.now(timezone.utc);batch_deadline(now)
    job=registry.load(args.registry or os.environ.get('MINIMOI_JOBS_REGISTRY')).get('rooms-archive')
    if job is None or job.host!=os.environ.get('MINIMOI_JOBS_HOST') or job.schedule.get('kind')!='daily' or job.schedule.get('tz')!='America/Chicago':raise ValueError('registered_host_required')
    root=Path(os.environ['MINIMOI_JOBS_DIR'])
    queue=Path(os.environ['GUILD_QUEUE_PATH'])
    if not queue.is_absolute():raise ValueError('canonical_queue_required')
    ledger_root=queue.parent/'agent_requests'  # exact existing agent_ask.attach root
    _private_dir(root);_private_dir(ledger_root,trusted_root=True)
    fd=os.open(root/'.rooms-archive.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:raise ValueError('lock_refused')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'state':'already_running'}));return 0
        run_id=secrets.token_hex(16);caller=from_env(os.environ,run_id=run_id);caller.authorize()
        previous=None
        try:previous=json.loads(_read(str(root/'rooms-archive-outcome.json'),2*1024*1024))
        except FileNotFoundError:pass
        running=status.start_run(root,job,run_id,now)
        state='failed'
        try:
            receipt=run_once(caller=caller,ledger_root=ledger_root,previous=previous,
                record=lambda r:status.write_json_atomic(root,'rooms-archive-outcome.json',r))
            state=receipt['state']
        except Exception:state='failed'
        finished=datetime.now(timezone.utc)
        status.finish_run(root,job,running,state=state,now=finished,exit_code=0 if state=='ok' else 1,
            summary='rooms archive completed' if state=='ok' else 'rooms archive failed',results={'archive':state},next_due_by=schedule.next_fire(job.schedule,finished))
        print(json.dumps({'state':state}));return 0 if state=='ok' else 1
    finally:os.close(fd)

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        print(json.dumps({'state':'failed','error':'archive_preflight_refused'}));raise SystemExit(1)
