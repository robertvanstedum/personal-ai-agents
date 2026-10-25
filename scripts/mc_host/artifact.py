"""Isolated create-only artifact writer / read-only verifier. Root is operator argv, never model input.
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


def run(request, root):
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
        if type(bundle) is not dict or set(bundle)!={'files'} or type(bundle['files']) is not list or not 1<=len(bundle['files'])<=2:
            raise Refused()
        files=[]
        for entry in bundle['files']:
            if type(entry) is not dict or set(entry)!={'name','text'} or entry['name'] not in ('artifact.txt','conversation.txt','conversation.json') or type(entry['text']) is not str:
                raise Refused()
            raw=entry['text'].encode('utf-8');files.append((entry['name'],raw))
        if len({n for n,_ in files})!=len(files):raise Refused()
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
            for name,raw in files:
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
                if (not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_size!=len(want) or
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
                if actual!=want:raise Refused()
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
        result=run(json.loads(raw),sys.argv[1]) if len(raw)<=MAX*2 and len(sys.argv)==2 else {'verified':False,'files':[]}
    except Exception:result={'verified':False,'files':[]}
    sys.stdout.write(json.dumps(result))
