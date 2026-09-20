"""Single dev worker. Actual OpenClaw agent; synthetic allowlist, no retry."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from store import Store
from cos_requests import CoSRequests
from cos_records_bridge import RoomClient, private_file
from cos_agent_responder import OpenClawMeetingModel, SyntheticSessionPolicy, respond
from cos_room_responder import TurnJournal


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',required=True)
    parser.add_argument('--config',required=True)
    args=parser.parse_args()
    config=json.loads(private_file(args.config).read_text())
    # Pin backend bytes. Import only the reviewed adapter, not CoS application.
    import hashlib
    source=private_file(config['backend_file']).read_bytes()
    if hashlib.sha256(source).hexdigest()!=config['backend_sha256']:raise ValueError('Reviewed backend changed')
    spec=importlib.util.spec_from_file_location('records_reviewed_cos_backend',config['backend_file'])
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    token=private_file(config['runtime_token_file']).read_text().strip()
    backend=module.OpenClawBackend(None,None,gateway_url='http://127.0.0.1:18790/v1',gateway_token=token,agent_id='cos-agent-a')
    model=OpenClawMeetingModel(backend)
    client=RoomClient(args.config)
    policy=SyntheticSessionPolicy(config['allowed_sessions'])
    store=Store(args.data_dir)
    queue=CoSRequests(store,config['allowed_sessions'])
    journal=TurnJournal(store.root/'cos-turns')
    # Prevent two workers from racing startup recovery / expensive inference.
    import fcntl
    lock=open(store.root/'cos-worker.lock','a');os.chmod(lock.name,0o600)
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    queue.recover_interrupted()
    def run(room,request_id,action,guard):
        return respond(room,request_id,client=client,model=model,journal=journal,
                       policy=policy,owner_authorized=True,action=action,expected_guard=guard)
    def forbid_inference(*args):
        raise RuntimeError('Reconciliation never starts inference')
    def reconcile(room,request_id,action):
        return respond(room,request_id,client=client,model=forbid_inference,journal=journal,
                       policy=policy,owner_authorized=True,action=action)
    while True:
        if not queue.run_once(run,reconcile):time.sleep(1)


if __name__=='__main__':main()
