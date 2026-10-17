"""Typed host Records binding, fixed project/Compose files. Never deploys anything.

Absent binding is allowed ONLY if no Records container or wired portal exists.
--portal-env emits validated nonsecret references for read/export, never eval.
--backend-plan prints a fixed admitted command; execution is a separate operator.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

PATH = Path('/etc/minimoi/records-production.json')
PROJECT = 'minimoi-records-production'
ROOT = Path('/opt/minimoi')
PORTAL_FILE = ROOT/'docker-compose.records-production-portal.yml'
BACKEND_FILE = ROOT/'docker-compose.records-production.yml'
ENV_KEYS = ('MINIMOI_RECORDS_IMAGE', 'MINIMOI_RECORDS_STATE', 'MINIMOI_RECORDS_PORTAL_KEY', 'MINIMOI_RECORDS_NETWORK')

class Refused(ValueError):
    pass

def need(ok):
    if not ok:
        raise Refused('records_deployment_refused')

def unique(pairs):
    doc = {}
    for key, value in pairs:
        need(key not in doc)
        doc[key] = value
    return doc

def read(path=PATH, *, trusted_uid=0):
    path=Path(path)
    need(path.is_absolute() and '..'not in path.parts)
    parent=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in path.parent.parts[1:]:
            try:other=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
            except FileNotFoundError:return None
            os.close(parent);parent=other
            s=os.fstat(parent)
            # Root-owned production ancestors; synthetic fixtures may use their
            # own UID and sticky /private/tmp as an ancestor, never config parent.
            need(s.st_uid in {0,trusted_uid})
            need(not s.st_mode&0o022 or (s.st_uid==0 and s.st_mode&stat.S_ISVTX))
        try:fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        except FileNotFoundError:return None
        try:
            p=os.fstat(parent);s=os.fstat(fd)
            need(p.st_uid==trusted_uid and stat.S_IMODE(p.st_mode)==0o700)
            need(stat.S_ISREG(s.st_mode)and s.st_uid==trusted_uid and stat.S_IMODE(s.st_mode)==0o600 and s.st_nlink==1 and 0<s.st_size<=8192)
            raw=os.read(fd,8193);z=os.fstat(fd);named=os.stat(path.name,dir_fd=parent,follow_symlinks=False)
            identity=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns,x.st_ctime_ns,x.st_mode,x.st_uid,x.st_nlink)
            need(identity(s)==identity(z)==identity(named)and len(raw)==s.st_size)
            doc=json.loads(raw.decode(),object_pairs_hook=unique)
            need(isinstance(doc,dict)and set(doc)=={'schema','inputs'}and doc['schema']=='minimoi.records-production-binding.v1')
            return doc['inputs']
        finally:os.close(fd)
    finally:os.close(parent)

def plan_module():
    spec=importlib.util.spec_from_file_location('records_fixed_production_plan',Path(__file__).with_name('production_plan.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def local_docker(args):
    environment=dict(os.environ)
    for key in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):
        environment.pop(key,None)
    result=subprocess.run(['docker','--host','unix:///var/run/docker.sock',*args],env=environment,capture_output=True,timeout=10)
    need(result.returncode==0 and len(result.stdout)+len(result.stderr)<=4*1024*1024)
    return result.stdout

def inventory(run=local_docker):
    names=run(['ps','-a','--format','{{.Names}}']).decode().splitlines()
    need(len(names)<=128 and len(names)==len(set(names)))
    selected=[n for n in ('minimoi-records','minimoi-portal')if n in names]
    if not selected:return {}
    rows=json.loads(run(['inspect',*selected]))
    need(isinstance(rows,list)and len(rows)==len(selected))
    result={r['Name'].lstrip('/'):r for r in rows}
    need(set(result)==set(selected))
    return result

def portal_wired(row):
    env=dict(s.split('=',1)for s in row.get('Config',{}).get('Env',[])if'='in s)
    return (env.get('MINIMOI_RECORDS_PRODUCTION')=='1'
            or any(m.get('Destination')=='/run/secrets/rooms-bridge/portal-bridge.key'for m in row.get('Mounts',[]))
            or any(n.startswith('minimoi-records-production-')for n in row.get('NetworkSettings',{}).get('Networks',{})))


PRODUCTION_ENV = {
    'BASE_URL':'https://minimoi.ai', 'MINIMOI_ROLE':'production',
    'MINIMOI_RECORDS_PROFILE':'production', 'MINIMOI_RECORDS_PRODUCTION':'1',
    'RECORDS_PORTAL_ENV':'production', 'RECORDS_ALLOWED_HOSTS':'minimoi-records:18880',
    'RECORDS_PORTAL_KEY_FILE':'/run/secrets/rooms-bridge/portal-bridge.key',
    'PYTHONDONTWRITEBYTECODE':'1', 'AWS_EC2_METADATA_DISABLED':'true',
}
RUNTIME_COMMAND = ['python','-m','services.records_runtime','--data-dir','/var/lib/minimoi-records','--execute']
HEALTH_TEST = ['CMD','python','-c',"import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:18880/health', headers={'Host':'minimoi-records:18880'}), timeout=3)"]

def portal_ready(record, inputs, network):
    """Point-in-time backend readiness; preparation admission is NOT activation."""
    need(isinstance(record,dict))
    state=record.get('State')or{}
    need(state.get('Running')is True and (state.get('Health')or{}).get('Status')=='healthy')
    config=record.get('Config')or{}
    need(config.get('Image')==inputs['image'] and config.get('Cmd')==RUNTIME_COMMAND)
    need(config.get('User','')in ('','0','0:0') and config.get('Entrypoint')in (None,[]) and config.get('WorkingDir')=='/app')
    # Reject duplicate keys rather than silently accepting their last value.
    items=config.get('Env',[])
    need(isinstance(items,list)and all(isinstance(v,str)and'='in v for v in items))
    pairs=[v.split('=',1)for v in items]
    env=dict(pairs);need(len(env)==len(pairs))
    need(all(env.get(k)==v for k,v in PRODUCTION_ENV.items()))
    health=config.get('Healthcheck')or{}
    need(health.get('Test')==HEALTH_TEST)
    need(all(health.get(k)==v for k,v in {'Interval':30000000000,'Timeout':5000000000,'StartPeriod':15000000000,'Retries':3}.items()))
    host=record.get('HostConfig')or{}
    need(host.get('ReadonlyRootfs')is True and host.get('Privileged')is False)
    need(host.get('CapAdd')in (None,[])and host.get('CapDrop')==['ALL'])
    need(host.get('SecurityOpt')in (['no-new-privileges:true'],['no-new-privileges']))
    need(host.get('Memory')==384*1024*1024 and host.get('MemorySwap')==384*1024*1024)
    need(host.get('NanoCpus')==500000000 and host.get('PidsLimit')==64)
    need(host.get('NetworkMode')==inputs['network'])
    need(host.get('PublishAllPorts')is False and host.get('PortBindings')in (None,{}))
    need(all(not value for value in (record.get('NetworkSettings',{}).get('Ports')or{}).values()))
    need(host.get('RestartPolicy',{}).get('Name')=='no')
    tmpfs=host.get('Tmpfs')
    need(isinstance(tmpfs,dict)and set(tmpfs)=={'/tmp'})
    need(set(tmpfs['/tmp'].split(','))in ({'size=16m','mode=1777'},{'size=16777216','mode=1777'}))
    mounts=record['Mounts']
    binds=[m for m in mounts if m.get('Type')=='bind']
    expected={
        '/var/lib/minimoi-records':(inputs['state'],True),
        '/run/secrets/rooms-bridge/portal-bridge.key':(inputs['portal_key'],False),
    }
    need(len(binds)==2 and {m['Destination']for m in binds}==set(expected))
    need(all((m.get('Source'),m.get('RW'))==expected[m['Destination']]for m in binds))
    extras=[m for m in mounts if m.get('Type')!='bind']
    need(len(extras)<=1 and all(m.get('Type')=='tmpfs'and m.get('Destination')=='/tmp'and m.get('RW')is True for m in extras))
    # Network inspect must agree this running container is actually attached.
    member=network['Containers'].get(record.get('Id'))or{}
    need(member.get('Name')=='minimoi-records')


def admit(inputs, *, rows, network=None, validate=None, portal_file=PORTAL_FILE, backend_file=BACKEND_FILE, portal_required=False):
    if inputs is None:
        need('minimoi-records'not in rows and not portal_wired(rows.get('minimoi-portal',{})))
        return None
    production=plan_module()if validate is None else validate
    result=production.plan(inputs)  # Exact existing five-field/path/image grammar.
    need(not os.path.lexists(Path(inputs['state'])/'INCOMPLETE'))
    # Fixed reviewed code files, never operator-selected paths or overlay text.
    for path in (portal_file,backend_file):
        for part in (path,*path.parents):need(not part.is_symlink())
        s=path.stat();need(stat.S_ISREG(s.st_mode)and s.st_uid in {0,os.geteuid()}and not s.st_mode&0o022 and s.st_nlink==1)
    need(isinstance(network,dict)and network.get('Name')==inputs['network']and network.get('Internal')is True)
    members=network.get('Containers',{})
    need(isinstance(members,dict)and {r.get('Name')for r in members.values()}<={'minimoi-records','minimoi-portal'})
    record=rows.get('minimoi-records')
    if record is not None:
        labels=record['Config'].get('Labels')or{}
        need(labels.get('com.docker.compose.project')==PROJECT and labels.get('com.docker.compose.service')=='minimoi-records')
        need(set(record['NetworkSettings']['Networks'])=={inputs['network']})
        mounts={m['Destination']:m for m in record['Mounts']}
        need(len(mounts)==len(record['Mounts']))
        need(mounts.get('/var/lib/minimoi-records',{}).get('Source')==inputs['state'])
        key=mounts.get('/run/secrets/rooms-bridge/portal-bridge.key',{})
        need(key.get('Source')==inputs['portal_key']and key.get('RW')is False)
    if portal_required:
        portal_ready(record,inputs,network)
    return result

def backend_plan(result, action):
    need(result is not None and action in {'preflight','start','status'})
    if action=='preflight':
        argv=result['runtime_preflight_argv']
    else:
        argv=['docker-compose','--project-name',PROJECT,'-f',str(BACKEND_FILE),'--profile','records-production']
        argv+=['up','-d','--no-deps','--pull','never','minimoi-records']if action=='start'else['ps','minimoi-records']
    return {'plan_only':True,'backend_project':PROJECT,'argv':argv,'environment_references':result['environment_references'],'activated':False}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    options=parser.add_mutually_exclusive_group(required=True)
    options.add_argument('--portal-env',action='store_true')
    options.add_argument('--backend-plan',choices=('preflight','start','status'))
    args=parser.parse_args()
    need(os.geteuid()==0)
    inputs=read();rows=inventory()
    network=None
    if inputs is not None:
        # Validate fields BEFORE constructing even a read-only Docker argument.
        plan_module().plan(inputs)
        inspected=json.loads(local_docker(['network','inspect',inputs['network']]))
        need(isinstance(inspected,list)and len(inspected)==1);network=inspected[0]
    result=admit(inputs,rows=rows,network=network,portal_required=args.portal_env)
    if args.portal_env:
        if result is not None:
            for key in ENV_KEYS:print(key+'='+result['environment_references'][key])
    else:print(json.dumps(backend_plan(result,args.backend_plan),sort_keys=True))

if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError,TypeError,AttributeError,subprocess.SubprocessError):
        print('records_deployment_refused',file=sys.stderr)
        sys.exit(1)
