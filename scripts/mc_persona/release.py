#!/usr/bin/env python3
"""Prepare a private, drift-guarded native MC projection; never restart the gateway."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

FILES = ('SOUL.md', 'AGENTS.md', 'RUNTIME.openclaw.md', 'CHANGES.md')
TARGETS = ('SOUL.md', 'AGENTS.md')

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def atomic(path, raw):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('symlink destination refused')
    fd, name = tempfile.mkstemp(prefix='.mc-release-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw); f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)

def encode(obj):
    return (json.dumps(obj, indent=2, ensure_ascii=False)+'\n').encode()

def candidate_tools(before):
    if before.get('profile') != 'minimal' or before.get('fs') != {'workspaceOnly': True}:
        raise ValueError('unexpected tool profile or filesystem boundary')
    must = {'group:runtime', 'write', 'edit', 'apply_patch', 'group:sessions', 'group:memory', 'gateway', 'cron'}
    if not must.issubset(before.get('deny', [])):
        raise ValueError('required deny boundary absent')
    after = json.loads(json.dumps(before))
    after['alsoAllow'] = [v for v in before['alsoAllow'] if v not in {'dir_list','grep','find'}]
    for name in ('web_search','web_fetch'):
        if name not in after['alsoAllow']: after['alsoAllow'].append(name)
    after['deny'] = [v for v in before['deny'] if v not in {'group:web','web_search','web_fetch'}]
    return after

def prepare(release, workspace, config, out, context=None, model=None):
    release,workspace,config,out = map(Path,(release,workspace,config,out))
    manifest = json.loads((release/'manifest.json').read_text())
    for name in FILES:
        if digest((release/name).read_bytes()) != manifest['files'][name]:
            raise ValueError('release hash mismatch: '+name)
    cfg_raw = config.read_bytes(); cfg = json.loads(cfg_raw)
    agent = cfg['agents']['entries']['mc-chat']
    if Path(agent['workspace']).resolve() != workspace.resolve():
        raise ValueError('workspace differs from configured mc-chat workspace')
    private = ''
    context_expiry = None
    if context:
        packet=json.loads(Path(context).read_text())
        if set(packet) != {'as_of','expires_at','sources','text'} or not packet['sources']:
            raise ValueError('context requires dated provenance')
        from datetime import datetime, timezone
        now=datetime.now(timezone.utc)
        start=datetime.fromisoformat(packet['as_of']); end=datetime.fromisoformat(packet['expires_at'])
        if not start <= now < end or (end-start).total_seconds() > 172800:
            raise ValueError('context stale, future dated, or exceeds 48 hours')
        context_expiry=packet['expires_at']
        private='\n\n# Private current context (operator-supplied data)\n'+json.dumps(packet,ensure_ascii=False,indent=2)
        if len(private)>6500: raise ValueError('context exceeds 6500 characters')
    projected={'SOUL.md':(release/'SOUL.md').read_bytes(), 'AGENTS.md':((release/'AGENTS.md').read_text()+'\n'+(release/'RUNTIME.openclaw.md').read_text()+private+'\n').encode()}
    if len(projected['AGENTS.md'].decode())>14000: raise ValueError('projection exceeds bootstrap budget')
    before={n:(workspace/n).read_bytes() for n in TARGETS}
    preserved={n:digest((workspace/n).read_bytes()) for n in ('USER.md','IDENTITY.md','MEMORY.md') if (workspace/n).exists()}
    tools=agent['tools']; after=candidate_tools(tools)
    model_before=agent.get('model')
    model_after=model_before
    if model:
        if model != 'anthropic/claude-opus-5': raise ValueError('model is outside this reviewed dev trial')
        if not isinstance(model_before,dict): raise ValueError('unexpected model binding')
        model_after={**model_before,'primary':model}
    out.mkdir(mode=0o700,parents=True,exist_ok=False)
    for sub,values in [('before',before),('after',projected)]:
        (out/sub).mkdir(mode=0o700)
        for n,raw in values.items(): atomic(out/sub/n,raw)
    plan={'schema':1,'context_expires_at':context_expiry,'version':manifest['version'],'workspace':str(workspace.resolve()),'config':str(config.resolve()),'config_sha256':digest(cfg_raw),'before':{n:digest(v) for n,v in before.items()},'after':{n:digest(v) for n,v in projected.items()},'preserved':preserved,'tools_before':tools,'tools_after':after,'model_before':model_before,'model_after':model_after,'release_manifest_sha256':digest((release/'manifest.json').read_bytes()),'activation':'pending sequencing disposition and review'}
    atomic(out/'plan.json',encode(plan))
    return plan

def activate(out, *, rollback=False, disposition=None, reviewed=False):
    if not rollback and (disposition not in ('model-day-complete','owner-waived-for-dev') or not reviewed):
        raise ValueError('explicit sequencing disposition and reviewed candidate required')
    out=Path(out); plan=json.loads((out/'plan.json').read_text()); workspace=Path(plan['workspace']); config=Path(plan['config'])
    if not rollback and plan.get('context_expires_at'):
        from datetime import datetime, timezone
        if datetime.now(timezone.utc) >= datetime.fromisoformat(plan['context_expires_at']):
            raise ValueError('staged context expired; refresh and reprepare')
    lock=workspace/'.mc-persona-activation.lock'
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    try:
        os.close(fd)
        old,new=('after','before') if rollback else ('before','after')
        raw=config.read_bytes(); cfg=json.loads(raw); tools=cfg['agents']['entries']['mc-chat']['tools']
        if tools != plan['tools_'+old]: raise ValueError('configuration tool drift')
        if cfg['agents']['entries']['mc-chat'].get('model') != plan['model_'+old]: raise ValueError('configuration model drift')
        if not rollback and digest(raw)!=plan['config_sha256']: raise ValueError('configuration changed since preparation; reprepare')
        for n,expected in plan['preserved'].items():
            if not rollback and digest((workspace/n).read_bytes())!=expected: raise ValueError('preserved context drift: '+n)
        for n in TARGETS:
            if digest((workspace/n).read_bytes())!=plan[old][n]: raise ValueError('persona drift: '+n)
            if digest((out/new/n).read_bytes())!=plan[new][n]: raise ValueError('staged artifact drift: '+n)
        cfg['agents']['entries']['mc-chat']['tools']=plan['tools_'+new]
        if plan['model_'+new] is not None: cfg['agents']['entries']['mc-chat']['model']=plan['model_'+new]
        # Recheck config immediately before replacement; no full-config backup with credentials.
        if config.read_bytes()!=raw: raise ValueError('concurrent configuration change')
        changed=[]
        try:
            for n in TARGETS:
                atomic(workspace/n,(out/new/n).read_bytes()); changed.append(n)
            if config.read_bytes()!=raw: raise ValueError('concurrent configuration change')
            atomic(config,encode(cfg))
        except Exception:
            for n in changed: atomic(workspace/n,(out/old/n).read_bytes())
            raise
        receipt={'version':plan['version'],'operation':'rollback' if rollback else 'activate','disposition':disposition,'persona_hashes':plan[new],'config_sha256':digest(config.read_bytes()),'model':plan['model_'+new],'gateway_restart':False}
        atomic(out/('rollback.json' if rollback else 'activation.json'),encode(receipt))
        return receipt
    finally:
        lock.unlink()

def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('prepare')
    for key in ('release','workspace','config','out'): a.add_argument('--'+key,required=True)
    a.add_argument('--context'); a.add_argument('--model')
    for name in ('activate','rollback'):
        a=sub.add_parser(name); a.add_argument('--out',required=True)
        if name=='activate':
            a.add_argument('--sequencing-disposition',choices=['model-day-complete','owner-waived-for-dev'],required=True)
            a.add_argument('--reviewed',action='store_true',required=True)
    a=p.parse_args()
    if a.command=='prepare': result=prepare(a.release,a.workspace,a.config,a.out,a.context,a.model)
    else: result=activate(a.out,rollback=a.command=='rollback',disposition=getattr(a,'sequencing_disposition',None),reviewed=getattr(a,'reviewed',False))
    print(json.dumps(result,indent=2))
if __name__=='__main__': main()
