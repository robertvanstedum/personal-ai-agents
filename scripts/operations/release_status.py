"""Nonsecret local deployment observations. Existing deploy scripts own calls.

Only fixed container image/running/health fields; no env, logs or owner content.
"""
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import hashlib
import sys

ROOT=Path('/opt/minimoi/release-status')
SERVICES=('portal','curator','german','portuguese','system-bot','cos-bot','cos-scheduler','cos-agent-a','model-gateway')
REGISTRY='332704997792.dkr.ecr.us-east-1.amazonaws.com/minimoi'
FORMAT='{"name":{{json .Name}},"image":{{json .Config.Image}},"image_id":{{json .Image}},"running":{{json .State.Running}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}}}'
EMPTY={'image':None,'image_id':None,'running':None,'health':None}

def need(v):
    if not v:raise ValueError('release_observation_refused')

def inventory(run=subprocess.run):
    result=run(['docker','inspect','--format',FORMAT,*['minimoi-'+s for s in SERVICES]],capture_output=True,text=True,timeout=5)
    need(len(result.stdout)<=12288)
    current={s:dict(EMPTY)for s in SERVICES}
    for line in result.stdout.splitlines():
        row=json.loads(line);name=row.pop('name','').removeprefix('/minimoi-')
        need(name in current and set(row)==set(EMPTY));current[name]=row
    return current

def expected(service,tag):
    repository='mein-deutsch'if service=='german'else'cos-scheduler'if service in('cos-agent-a','model-gateway')else service
    prefix='agent-a-'if service=='cos-agent-a'else'model-gateway-'if service=='model-gateway'else''
    return f'{REGISTRY}/{repository}:{prefix}{tag}'

def observation(phase,tag,selected,current,previous=None,*,now=None,manifest=None,environment='production'):
    need(phase in('begin','finish')and re.fullmatch(r'[a-f0-9]{7,40}|latest',tag)and selected and len(selected)==len(set(selected))and set(selected)<=set(SERVICES))
    need(environment in('production','dev'))
    status='deployment_in_progress'
    if phase=='finish':status='selected_images_observed'if all(current[s]['image']==expected(s,tag)and current[s]['running']is True for s in selected)else'selected_image_check_failed'
    previous=previous or {};changes=[]
    # A finishing observation is comparable only to THIS begin request.
    if phase=='finish'and previous.get('status')=='deployment_in_progress'and previous.get('requested_tag')==tag and previous.get('selected')==selected:
        for service in SERVICES:
            before=(previous.get('current',{}).get(service)or{}).get('image');after=current[service]['image']
            before_id=(previous.get('current',{}).get(service)or{}).get('image_id');after_id=current[service]['image_id']
            if (before,before_id)!=(after,after_id):changes.append({'service':service,'before':before,'after':after,'before_image_id':before_id,'after_image_id':after_id})
    if not(type(manifest)is dict and re.fullmatch(r'[a-f0-9]{40}',manifest.get('source_commit',''))and tag!='latest'and manifest['source_commit'].startswith(tag)):manifest=None
    return {'environment':environment,'manifest':manifest,'format':'minimoi.release-observation/1','observed_at':(now or datetime.now(timezone.utc)).isoformat(),'status':status,'requested_tag':tag,'selected':selected,'current':current,'changes':changes,'source':'local Docker selected-image deployment checks'}

def release_manifest(notes,commit,subject,paths,*,now=None,changed_paths_status='first_parent_diff'):
    need(type(notes)is dict and set(notes)=={'format','version','capabilities'}and notes['format']=='minimoi.authored-release-notes/1')
    need(type(notes['version'])is str and re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?',notes['version']))
    need(re.fullmatch(r'[a-f0-9]{40}',commit)and type(subject)is str and len(subject)<=160)
    need(type(notes['capabilities'])is list and 1<=len(notes['capabilities'])<=8)
    for c in notes['capabilities']:
        need(type(c)is dict and set(c)=={'id','summary','services'}and re.fullmatch(r'[a-z0-9-]{1,64}',c['id']))
        need(type(c['summary'])is str and 1<=len(c['summary'])<=240 and not any(ord(v)<32 for v in c['summary']))
        need(type(c['services'])is list and c['services']and set(c['services'])<=set(SERVICES))
    need(changed_paths_status in('first_parent_diff','root_commit','unavailable_parent'))
    need(changed_paths_status!='unavailable_parent'or not paths)
    paths=sorted(set(paths));need(all(type(p)is str and len(p)<=160 and not any(ord(v)<32 for v in p)for p in paths))
    result={'format':'minimoi.released-source-manifest/1','source':'CI checkout public commit and authored config/release_notes.json','generated_at':(now or datetime.now(timezone.utc)).isoformat(),'source_commit':commit,'source_notes_sha256':hashlib.sha256(json.dumps(notes,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'subject':subject,'changed_paths_status':changed_paths_status,'changed_paths':paths[:12],'paths_truncated':len(paths)>12,'version':notes['version'],'capabilities':notes['capabilities']}
    need(len(json.dumps(result).encode())<=4096);return result

def changed_paths(root,run=subprocess.run):
    """First parent only. A shallow boundary is NOT an actual root commit."""
    def git(*args):return run(['git',*args],cwd=root,capture_output=True,text=True,timeout=5)
    shallow=git('rev-parse','--is-shallow-repository')
    if shallow.returncode!=0 or shallow.stdout.strip()not in('true','false'):return [],'unavailable_parent'
    parent=git('rev-parse','--verify','HEAD^1')
    if parent.returncode==0 and re.fullmatch(r'[a-f0-9]{40}',parent.stdout.strip()):
        diff=git('diff','--name-only','HEAD^1','HEAD','--')
        if diff.returncode==0:return diff.stdout.splitlines(),'first_parent_diff'
        return [],'unavailable_parent'
    # Only full ancestry plus an explicit empty parent list proves a root.
    parents=git('rev-list','--parents','-n','1','HEAD')
    if shallow.stdout.strip()=='false'and parents.returncode==0 and len(parents.stdout.split())==1 and re.fullmatch(r'[a-f0-9]{40}',parents.stdout.strip()):
        tree=git('ls-tree','-r','--name-only','HEAD')
        if tree.returncode==0:return tree.stdout.splitlines(),'root_commit'
    return [],'unavailable_parent'

def produce_manifest(root=None,run=subprocess.run):
    root=root or Path(__file__).resolve().parents[2]
    def git(*args,trim=True):
        out=run(['git',*args],cwd=root,capture_output=True,text=True,check=True,timeout=5).stdout
        return out.strip()if trim else out
    # Never combine future/dirty local source with an older committed identity.
    need(git('status','--porcelain=v1','--untracked-files=all')=='')
    path=root/'config/release_notes.json';need(not path.is_symlink())
    with path.open('rb')as f:raw=f.read(4097)
    need(len(raw)<=4096)
    need(raw==git('show','HEAD:config/release_notes.json',trim=False).encode('utf-8'))
    notes=json.loads(raw);paths,status=changed_paths(root,run)
    result=release_manifest(notes,git('rev-parse','HEAD'),git('show','-s','--format=%s','HEAD')[:160],paths,changed_paths_status=status)
    result['source_notes_sha256']=hashlib.sha256(raw).hexdigest();return result

def directory(root):
    for p in (root.parent,*root.parent.parents):
        s=p.lstat();need(stat.S_ISDIR(s.st_mode)and not s.st_mode&0o002)
    try:root.mkdir(mode=0o755)
    except FileExistsError:pass
    s=root.lstat();need(stat.S_ISDIR(s.st_mode)and s.st_uid==0 and not s.st_mode&0o022)

def publish(root,doc,name='status.json'):
    directory(root);raw=json.dumps(doc,sort_keys=True,separators=(',',':')).encode();need(len(raw)<=16384)
    filename=name;fd,name=tempfile.mkstemp(prefix='.status-',dir=root)
    try:
        os.fchmod(fd,0o644)
        with os.fdopen(fd,'wb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
        os.replace(name,root/filename)
        d=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(d)
        finally:os.close(d)
    finally:
        if os.path.lexists(name):os.unlink(name)

def prior(root,name='status.json'):
    path=root/name
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:return None
    try:
        s=os.fstat(fd);need(stat.S_ISREG(s.st_mode)and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022 and s.st_size<=16384)
        raw=os.read(fd,16385);need(len(raw)==s.st_size);return json.loads(raw)
    finally:os.close(fd)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('phase',choices=['begin','finish','manifest','install-manifest']);p.add_argument('tag',nargs='?');p.add_argument('services',nargs='*');a=p.parse_args()
    if a.phase=='manifest':print(json.dumps(produce_manifest(),sort_keys=True));return
    need(os.geteuid()==0);directory(ROOT)
    if a.phase=='install-manifest':
        raw=sys.stdin.buffer.read(4097);need(len(raw)<=4096);doc=json.loads(raw)
        need(doc.get('format')=='minimoi.released-source-manifest/1'and re.fullmatch(r'[a-f0-9]{40}',doc.get('source_commit','')))
        publish(ROOT,doc,'manifest.json');return
    previous=prior(ROOT);current=inventory();manifest=prior(ROOT,'manifest.json')
    publish(ROOT,observation(a.phase,a.tag,a.services,current,previous,manifest=manifest))

if __name__=='__main__':main()
