"""Guildhall worker adapter to the normal CoS coordination service."""
import requests
from services.rooms_worker.clients import read_secret


class CoS:
    # Stopping delivery is not proof that a remote tool operation stopped.
    stop_guaranteed = False
    def __init__(self, token_file, base='http://cos-scheduler:8769', session=None):
        if base!='http://cos-scheduler:8769':raise ValueError('Fixed dev CoS endpoint required')
        self.base=base;self.token_file=token_file
        self.session=session or requests.Session();self.session.trust_env=False
    def headers(self):return {'Authorization':'Bearer '+read_secret(self.token_file)}
    def ready(self):
        try:return self.session.get(self.base+'/internal/guildhall/ready',headers=self.headers(),timeout=5).status_code==200
        except requests.RequestException:return False
    def stop(self, correlation):return 202  # request only; worker retains uncertainty
    def stream(self,messages,user,correlation,*,turn,admit,**kwargs):
        trigger=turn.get('trigger') or {}
        empty={'text':'','usage':None}
        if (turn.get('addressee')!='cos' or trigger.get('actor')!='robert'
                or trigger.get('kind')!='message' or 'origin' not in trigger or trigger['origin'] is not None):
            return {**empty,'outcome':'refused','detail':'owner_trigger_required'}
        approved,state=admit()
        if not approved:return {**empty,'outcome':'not_admitted','detail':'admission_refused','state':state}
        try:
            # Only stable content enters the idempotent coordinator ledger.
            payload={k:turn[k] for k in ('id','room','addressee','trigger','brief','transcript','coverage')}
            response=self.session.post(self.base+'/internal/guildhall/turn',headers=self.headers(),json=payload,timeout=(5,150),allow_redirects=False)
            data=response.json()
            if response.status_code==200 and data.get('turn_id')==turn['id'] and data.get('room')==turn['room'] and isinstance(data.get('text'),str):
                return {'outcome':'done','text':data['text'],'usage':None,'detail':'done'}
            return {**empty,'outcome':'error','detail':'cos_outcome_uncertain'}
        except (requests.RequestException,ValueError):
            return {**empty,'outcome':'error','detail':'cos_outcome_uncertain'}


def main():
    import os,fcntl,time
    from pathlib import Path
    from services.rooms_worker.clients import Records
    from services.rooms_worker.worker import Worker,log
    from services.rooms_worker.journal import TurnJournal
    from services.rooms_worker.transport_profile import admit_runtime
    url,profile=admit_runtime(os.environ,kind='worker')
    if profile!='dev':raise ValueError('Guildhall CoS is dev-only')
    secrets=Path(os.environ['ROOMS_SECRETS_DIR'])
    root=Path(os.environ['ROOMS_JOURNAL_DIR']);root.mkdir(mode=0o700,parents=True,exist_ok=True)
    lock=(root/'worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    worker=Worker(Records(url,lambda:read_secret(secrets/'rooms-worker-cos.token')),
                  Records(url,lambda:read_secret(secrets/'cos.token')),
                  CoS(secrets/'cos-service.token'),TurnJournal(root/'turns'),
                  teammate='cos',agent_id='cos-agent-a',alive_path='/tmp/rooms-worker/alive')
    worker.start_housekeeping()
    while True:
        try:busy=worker.run_once()
        except Exception as error:
            log(event='cos_worker_error',kind=type(error).__name__);busy=False
        if not busy:time.sleep(1)

if __name__=='__main__':main()
