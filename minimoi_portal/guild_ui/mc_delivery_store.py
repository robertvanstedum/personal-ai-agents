"""Owner-bound generated deliveries, separate from uploaded source documents.

One immutable bundle per owner/conversation/source note. Thirty-day availability,
ten bundles per conversation and 50 MiB total; expiry does not delete files.
"""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

MAX_CONVERSATION = 10
MAX_TOTAL_BYTES = 50 * 1024 * 1024
RETENTION_S = 30 * 24 * 3600
_ID = re.compile(r'a-[0-9a-f]{40}')

class DeliveryUnavailable(Exception): pass
class DeliveryMissing(Exception): pass
class DeliveryExpired(DeliveryMissing): pass
class DeliveryFull(Exception): pass

class DeliveryStore:
    def __init__(self, root, *, clock=time.time):
        self.root=Path(root); self.clock=clock

    @contextmanager
    def _locked(self):
        try:
            self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
            info=self.root.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o077:
                raise DeliveryUnavailable('private_directory_required')
            fd=os.open(self.root/'.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            try:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o077:
                    raise DeliveryUnavailable('private_lock_required')
                fcntl.flock(fd,fcntl.LOCK_EX)
                yield
            finally:os.close(fd)
        except OSError:
            raise DeliveryUnavailable('store_unavailable') from None

    def _read(self,path,max_bytes):
        try:
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        except FileNotFoundError:
            raise DeliveryMissing() from None
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o077 or info.st_size>max_bytes:
                raise DeliveryUnavailable('invalid_stored_file')
            with os.fdopen(fd,'rb',closefd=False) as f:data=f.read(max_bytes+1)
            if len(data)>max_bytes:raise DeliveryUnavailable('invalid_stored_file')
            return data
        finally:os.close(fd)

    def _meta(self,ident):
        try:
            value=json.loads(self._read(self.root/(ident+'.json'),65536))
        except (ValueError,UnicodeError):
            raise DeliveryUnavailable('invalid_metadata') from None
        if not isinstance(value,dict) or value.get('id')!=ident:
            raise DeliveryUnavailable('invalid_metadata')
        return value

    @staticmethod
    def _binding(principal,cid,note_id):
        if any(not isinstance(v,str) or not 1<=len(v)<=200 for v in (principal,cid,note_id)):
            raise ValueError('invalid_binding')
        return json.dumps([principal,cid,note_id],ensure_ascii=True,separators=(',',':')).encode()

    def _verified(self,meta):
        data=self._read(self.root/(meta['id']+'.zip'),256*1024)
        if len(data)!=meta.get('bytes') or hashlib.sha256(data).hexdigest()!=meta.get('sha256'):
            raise DeliveryUnavailable('integrity_failed')
        return data

    def get(self,principal,cid,ident):
        if not isinstance(ident,str) or not _ID.fullmatch(ident):raise DeliveryMissing()
        with self._locked():
            meta=self._meta(ident)
            if meta.get('principal')!=principal or meta.get('conversation_id')!=cid:
                raise DeliveryMissing()
            if self.clock()>=meta['expires_at']:raise DeliveryExpired()
            return meta,self._verified(meta)

    def existing(self,principal,cid,note_id):
        ident='a-'+hashlib.sha256(self._binding(principal,cid,note_id)).hexdigest()[:40]
        try:return self.get(principal,cid,ident)
        except DeliveryExpired:raise
        except DeliveryMissing:
            # A partial or expired prior delivery must never regenerate under the same source turn.
            if (self.root/(ident+'.json')).exists() or (self.root/(ident+'.zip')).exists():
                raise DeliveryUnavailable('previous_delivery_unavailable') from None
            raise

    def put(self,principal,cid,note_id,bundle):
        ident='a-'+hashlib.sha256(self._binding(principal,cid,note_id)).hexdigest()[:40]
        with self._locked():
            try:meta=self._meta(ident)
            except DeliveryMissing:meta=None
            if meta is not None:
                if (meta.get('principal'),meta.get('conversation_id'),meta.get('source_note'))!=(principal,cid,note_id):
                    raise DeliveryUnavailable('binding_mismatch')
                if self.clock()>=meta['expires_at']:raise DeliveryExpired()
                return meta,self._verified(meta) # retry always returns the original, never regenerates
            # An incomplete earlier write is unknown; never overwrite or silently retry it.
            if (self.root/(ident+'.zip')).exists():raise DeliveryUnavailable('unconfirmed_previous_write')
            total=0; count=0
            for path in self.root.iterdir():
                if path.suffix=='.zip':total+=path.lstat().st_size
                if path.suffix=='.json' and _ID.fullmatch(path.stem):
                    row=self._meta(path.stem)
                    if row.get('principal')==principal and row.get('conversation_id')==cid:count+=1
            if count>=MAX_CONVERSATION or total+len(bundle.data)>MAX_TOTAL_BYTES:raise DeliveryFull()
            created=self.clock()
            meta={'id':ident,'principal':principal,'conversation_id':cid,'source_note':note_id,
                  'author':'master_craftsman','sha256':bundle.sha256,'bytes':len(bundle.data),
                  'files':list(bundle.files),'created_at':created,'expires_at':created+RETENTION_S}
            # File first, metadata last. Any partial failure remains unknown for reconciliation.
            for suffix,data in (('.zip',bundle.data),('.json',json.dumps(meta,ensure_ascii=True).encode())):
                fd=os.open(self.root/(ident+suffix),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
            directory=os.open(self.root,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
            stored=self._meta(ident)
            return stored,self._verified(stored)
