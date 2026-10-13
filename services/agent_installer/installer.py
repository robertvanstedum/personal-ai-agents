"""Pull pending Records approvals through a dedicated loopback-only channel.

Run under the local operator, with a private root used only for these agents.
Records and the portal never receive this path. No subprocess or arbitrary paths.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from urllib.parse import urlsplit
from uuid import uuid4
import requests

FILES={'mc':('mc.token','rooms-worker.token'),
       'claude-code':('claude-code.token','rooms-connector-mac.token'),
       'codex':('codex.token','rooms-connector-codex.token')}


def private_dir(path):
    p=Path(path)
    p.mkdir(mode=0o700,parents=True,exist_ok=True)
    if p.is_symlink() or p.resolve()!=p.absolute(): raise ValueError('Private path must not contain symlinks')
    fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    s=os.fstat(fd)
    if s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)&0o077:
        os.close(fd);raise ValueError('Installer directory must be owner-private')
    return fd


def put(fd,name,data):
    temp='.'+name+'.'+uuid4().hex
    handle=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
    try:
        with os.fdopen(handle,'wb') as f: f.write(data);f.flush();os.fsync(f.fileno())
        # Never follows an existing destination link; replacement is atomic.
        os.replace(temp,name,src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
    finally:
        try: os.unlink(temp,dir_fd=fd)
        except FileNotFoundError: pass


class Installer:
    def __init__(self,base,token_file,root):
        u=urlsplit(base)
        if u.scheme!='http' or u.hostname not in ('127.0.0.1','localhost','::1') or u.path not in ('','/') or u.query or u.fragment or u.username:
            raise ValueError('Installer requires the configured loopback Records endpoint')
        self.base=base.rstrip('/')+'/api/v1/agent-installer'
        self.root=Path(root).absolute();fd=private_dir(self.root);os.close(fd)
        p=Path(token_file)
        if p.is_symlink() or p.stat().st_uid!=os.getuid() or stat.S_IMODE(p.stat().st_mode)&0o077:
            raise ValueError('Installer key must be owner-private')
        self.token=p.read_text().strip()
        if len(self.token)<32: raise ValueError('Invalid installer key')
        self.session=requests.Session();self.session.trust_env=False
        self.last_status=None

    def call(self,method,path,body=None):
        r=self.session.request(method,self.base+path,json=body,
            headers={'Authorization':'Bearer '+self.token},timeout=15,allow_redirects=False)
        self.last_status=r.status_code
        if r.status_code!=200: return r.status_code,{}
        if len(r.content)>100_000: raise ValueError('Installer response too large')
        return r.status_code,r.json()

    def install(self,item):
        agent=item['agent'];generation=item['generation']
        if agent not in FILES or not isinstance(generation,str): raise ValueError('Unknown agent')
        fd=private_dir(self.root/agent)
        try:
            try:
                h=os.open('installation.json',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
                with os.fdopen(h) as f: saved=json.load(f)
            except FileNotFoundError: saved=None
            intact = bool(saved and saved.get('files'))
            for name in (*FILES[agent], 'standing-brief.txt'):
                try:
                    h=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
                    with os.fdopen(h,'rb') as f: digest=hashlib.sha256(f.read(100_000)).hexdigest()
                    intact = intact and digest == saved['files'].get(name)
                except (OSError, KeyError, TypeError): intact=False
            if intact and saved.get('generation')==generation:
                status,_=self.call('POST','/'+agent+'/ack',saved)
                if status==200: return True
            status,bundle=self.call('POST','/'+agent+'/issue',{'generation':generation})
            if status!=200: return False
            if bundle.get('agent')!=agent or bundle.get('generation')!=generation: raise ValueError('Mismatched installation')
            brief=bundle['brief'].encode()
            if hashlib.sha256(brief).hexdigest()!=bundle['brief_hash']: raise ValueError('Brief hash mismatch')
            pair=bundle['credentials']
            contents={'standing-brief.txt':brief}
            for scope,name in zip(('membership','work'),FILES[agent]):
                token=pair[scope]['token']
                if not isinstance(token,str) or not 32<=len(token)<=200: raise ValueError('Invalid credential')
                contents[name]=(token+'\n').encode()
            for name,data in contents.items(): put(fd,name,data)
            ack={'generation':generation,'member_id':pair['membership']['credential_id'],
                 'worker_id':pair['work']['credential_id'],'brief_hash':bundle['brief_hash'],
                 'files':{name:hashlib.sha256(data).hexdigest() for name,data in contents.items()}}
            put(fd,'installation.json',json.dumps(ack).encode())
            status,_=self.call('POST','/'+agent+'/ack',ack)
            return status==200
        finally: os.close(fd)

    def once(self):
        status,body=self.call('GET','/pending')
        if status!=200: return False
        okay=True
        for item in body.get('pending',[]): okay=self.install(item) and okay
        return okay


def main():
    p=argparse.ArgumentParser();p.add_argument('--records',default='http://127.0.0.1:18881')
    p.add_argument('--token-file',required=True);p.add_argument('--root',required=True);p.add_argument('--once',action='store_true')
    a=p.parse_args();installer=Installer(a.records,a.token_file,a.root)
    import fcntl
    fd=private_dir(a.root)
    lock=os.open('installer.lock',os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600,dir_fd=fd)
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    while True:
        try: ok=installer.once()
        except Exception: ok=False  # never print response, request headers or secrets
        print(json.dumps({'event':'installer_check','ok':ok,'http_status':installer.last_status}),flush=True)
        if a.once: return 0 if ok else 1
        time.sleep(5)

if __name__=='__main__': raise SystemExit(main())
