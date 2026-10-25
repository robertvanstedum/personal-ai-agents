"""Fixed synthetic Career Kit writer / read-only verifier. Root and kit are operator argv, never model input.
No retry of create: each operation directory is claimed once with mkdir. Files are never overwritten.
"""
import base64
import hashlib
import json
import os
import re
import stat
import sys
import time
import tempfile
from pathlib import Path
import importlib.util
_spec=importlib.util.spec_from_file_location('mc_career_recipe',Path(__file__).with_name('career_trial_recipe.py'))
_recipe=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_recipe)
checked_kit,generate=_recipe.checked_kit,_recipe.generate

MAX = 1024 * 1024

class Refused(Exception):
    pass


def directory(path):
    if not isinstance(path,str) or not path.startswith('/') or any(p in ('','.','..') for p in path[1:].split('/')):
        raise Refused()
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path[1:].split('/'):
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=child
        return fd
    except BaseException:
        os.close(fd);raise


def alive(request):
    if type(request.get('expires_at')) not in (int,float) or time.time() >= request['expires_at']:
        raise Refused()


def run(request, root, kit):
    fds=[]
    try:
        if (type(request) is not dict or set(request) != {'mode','operation_id','expires_at','payload','sha256'} or
            request['mode'] not in ('create','verify') or not re.fullmatch('[0-9a-f-]{36}',str(request['operation_id']))):
            raise Refused()
        alive(request)
        payload=base64.b64decode(request['payload'],validate=True)
        if len(payload)>MAX or hashlib.sha256(payload).hexdigest()!=request['sha256']:
            raise Refused()
        bundle=json.loads(payload)
        manifest=json.loads((Path(kit)/'manifest.json').read_text())
        if bundle!={'career_trial':manifest} or manifest['fixture']!='synthetic-v1':raise Refused()
        checked_kit(kit,manifest) # Source/dependency checks apply even to read-only recovery.
        expected=manifest['expected']
        if len(expected)!=14 or len({f['name'] for f in expected})!=14:raise Refused()
        for f in expected:
            if set(f)!={'name','bytes','sha256'} or not re.fullmatch(r'[a-z-]+\.(docx|md|json)',f['name']) or not 0<f['bytes']<=MAX:raise Refused()
        files=[(f['name'],f) for f in expected]
        fd=directory(root);fds.append(fd)
        st=os.fstat(fd)
        if st.st_uid!=os.getuid() or st.st_mode & 0o022:raise Refused()
        if request['mode']=='create':
            try:
                os.mkdir('mc-artifacts',mode=0o700,dir_fd=fd)
                os.fsync(fd) # Persist the new top-level directory entry before acknowledging descendants.
            except FileExistsError:pass
        artifacts=os.open('mc-artifacts',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);fds.append(artifacts)
        st=os.fstat(artifacts)
        if st.st_uid!=os.getuid() or st.st_mode & 0o077:raise Refused()
        if request['mode']=='create':
            alive(request)
            os.mkdir(request['operation_id'],mode=0o700,dir_fd=artifacts) # Existing directory ALWAYS refuses create.
            os.fsync(artifacts)
        op=os.open(request['operation_id'],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=artifacts);fds.append(op)
        st=os.fstat(op)
        if st.st_uid!=os.getuid() or st.st_mode & 0o077:raise Refused()
        if request['mode']=='create':
            with tempfile.TemporaryDirectory(prefix='mc-career-synthetic-') as tmp:
                generated=generate(kit,manifest,Path(tmp))
            if [{'name':n,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for n,raw in generated.items()]!=expected:raise Refused()
            for name,raw in generated.items():
                alive(request)
                file=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=op)
                try:
                    view=memoryview(raw)
                    while view:
                        alive(request)
                        count=os.write(file,view)
                        if count<=0:raise Refused()
                        view=view[count:]
                    os.fsync(file)
                finally:os.close(file)
            os.fsync(op)
        if sorted(os.listdir(op))!=sorted(n for n,_ in files):raise Refused()
        verified=[]
        for name,want in files:
            file=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=op)
            try:
                st=os.fstat(file)
                if (not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_size!=want['bytes'] or
                        st.st_uid!=os.getuid() or st.st_mode & 0o077):raise Refused()
                chunks=[];size=0
                while True:
                    raw=os.read(file,65536)
                    if not raw:break
                    size+=len(raw)
                    if size>MAX:raise Refused()
                    chunks.append(raw)
                after=os.fstat(file)
                if (st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise Refused()
                named=os.stat(name,dir_fd=op,follow_symlinks=False)
                if (named.st_dev,named.st_ino)!=(st.st_dev,st.st_ino):raise Refused()
                actual=b''.join(chunks)
                if hashlib.sha256(actual).hexdigest()!=want['sha256']:raise Refused()
                verified.append({'name':name,'bytes':len(actual),'sha256':hashlib.sha256(actual).hexdigest()})
            finally:os.close(file)
        # The saved path must still name the verified open directories, not a renamed orphan.
        check=directory(root)
        try:
            for expected,name in [(fd,None),(artifacts,'mc-artifacts'),(op,request['operation_id'])]:
                if name is not None:
                    child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=check)
                    os.close(check);check=child
                a,b=os.fstat(expected),os.fstat(check)
                if ((a.st_dev,a.st_ino)!=(b.st_dev,b.st_ino) or b.st_uid!=os.getuid() or
                        b.st_mode & (0o022 if name is None else 0o077)):raise Refused()
        finally:os.close(check)
        alive(request)
        return {'verified':True,'files':verified}
    except Exception:
        # Missing/partial/expired is uncertain, never evidence that a dispatched write did not happen.
        return {'verified':False,'files':[]}
    finally:
        for fd in reversed(fds):os.close(fd)


if __name__=='__main__':
    try:
        raw=sys.stdin.buffer.read(MAX*2+1)
        result=run(json.loads(raw),sys.argv[1],sys.argv[2]) if len(raw)<=MAX*2 and len(sys.argv)==3 else {'verified':False,'files':[]}
    except Exception:result={'verified':False,'files':[]}
    sys.stdout.write(json.dumps(result))
