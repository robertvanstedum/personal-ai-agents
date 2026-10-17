"""Shared atomic access to existing Curator preferences; not a new inventory.
All read/modify/write callers must hold locked(); write() nests safely. No models.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import hashlib
from core.work_registers import writer_domain

MAX_BYTES=8<<20
_MUTEX=threading.Lock()
_LOCKS={}
_LOCAL=threading.local()

class Snapshot(dict):pass
class PreferencesRefused(ValueError):pass
class PreferencesUncertain(PreferencesRefused):pass
class PreferencesBusy(PreferencesRefused):pass

def need(ok,code):
    if not ok:raise PreferencesRefused(code)

def _path(path):
    path=Path(path)
    need(path.is_absolute()and path.name=='curator_preferences.json'and '..'not in path.parts,'canonical_preferences_path_required')
    for p in (path,*path.parents):need(not p.is_symlink(),'preferences_symlink_refused')
    need(path.parent.is_dir(),'preferences_parent_missing')
    return path

@contextmanager
def locked(path):
    path=_path(path);key=str(path)
    with _MUTEX:mutex=_LOCKS.setdefault(key,threading.RLock())
    if not mutex.acquire(timeout=2):raise PreferencesBusy('preferences_busy')
    held=getattr(_LOCAL,'held',set());_LOCAL.held=held
    fd=None
    try:
        if key not in held:
            fd=os.open(path.with_name('.curator-preferences.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
            s=os.fstat(fd);need(stat.S_ISREG(s.st_mode)and s.st_nlink==1 and s.st_uid==os.geteuid()and stat.S_IMODE(s.st_mode)==0o600,'preferences_lock_refused')
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise PreferencesBusy('preferences_busy')from None
            # Atomic create-only domain election prevents independent Mac/Linux
            # flock domains from both writing a shared bind mount.
            domain=writer_domain();marker=path.with_name('.curator-preferences-writer.json')
            expected=(json.dumps({'writer_domain':domain},sort_keys=True)+'\n').encode()
            try:
                marker_fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            except FileExistsError:pass
            else:
                try:os.write(marker_fd,expected);os.fsync(marker_fd)
                finally:os.close(marker_fd)
                directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                try:os.fsync(directory)
                finally:os.close(directory)
            actual=marker.lstat();need(stat.S_ISREG(actual.st_mode)and actual.st_uid==os.geteuid()and actual.st_nlink==1 and stat.S_IMODE(actual.st_mode)==0o600,'preferences_domain_marker_refused')
            marker_fd=os.open(marker,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            try:
                need(os.read(marker_fd,256)==expected,'preferences_writer_domain_changed')
                # Matching bytes are not proof that a previous publication was
                # durable. Reconcile the exact retained marker before any write.
                try:
                    os.fsync(marker_fd)
                    directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                    try:os.fsync(directory)
                    finally:os.close(directory)
                except BaseException:raise PreferencesUncertain('preferences_domain_durability_uncertain')from None
            finally:os.close(marker_fd)
            held.add(key)
        yield
    finally:
        if fd is not None:
            held.discard(key);os.close(fd)
        mutex.release()

def load(path):
    path=_path(path)
    try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:
        result=Snapshot(version='1.0',feedback_history={});result.original_sha256=None;return result
    try:
        a=os.fstat(fd);need(stat.S_ISREG(a.st_mode)and a.st_nlink==1 and a.st_size<=MAX_BYTES,'preferences_file_refused')
        raw=os.read(fd,MAX_BYTES+1);b=os.fstat(fd);c=path.lstat()
        identity=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
        need(len(raw)==a.st_size and identity(a)==identity(b)==identity(c),'preferences_file_changed')
        value=json.loads(raw);need(type(value)is dict and type(value.get('feedback_history',{}))is dict,'preferences_schema_invalid')
        value.setdefault('feedback_history',{});result=Snapshot(value);result.original_sha256=hashlib.sha256(raw).hexdigest();return result
    finally:os.close(fd)

def write(path,value):
    path=_path(path);need(isinstance(value,Snapshot),'preferences_loaded_snapshot_required')
    raw=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode();need(len(raw)<=MAX_BYTES,'preferences_size_bound')
    with locked(path):
        current=load(path);need(current.original_sha256==value.original_sha256,'preferences_stale_snapshot')
        original=path.stat()if path.exists()else None
        mode=stat.S_IMODE(original.st_mode)if original else 0o600
        fd,tmp=tempfile.mkstemp(prefix='.curator-preferences-',dir=path.parent)
        try:
            if original and (os.fstat(fd).st_uid,os.fstat(fd).st_gid)!=(original.st_uid,original.st_gid):
                os.fchown(fd,original.st_uid,original.st_gid)
            os.fchmod(fd,mode)
            with os.fdopen(fd,'wb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
            try:
                os.replace(tmp,path)
                value.original_sha256=hashlib.sha256(raw).hexdigest()
                d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                try:os.fsync(d)
                finally:os.close(d)
            except BaseException:raise PreferencesUncertain('preferences_publication_uncertain_inspect_before_retry')from None
        finally:
            if os.path.lexists(tmp):os.unlink(tmp)


def library_history(path):
    """Read the existing canonical bookmark/history store; never write it."""
    path=Path(path)
    need(path.is_absolute()and path.name=='curator_history.json'and '..'not in path.parts,'canonical_library_history_required')
    for p in (path,*path.parents):need(not p.is_symlink(),'preferences_symlink_refused')
    try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:return {}
    try:
        a=os.fstat(fd);need(stat.S_ISREG(a.st_mode)and a.st_nlink==1 and a.st_size<=MAX_BYTES,'library_history_file_refused')
        raw=os.read(fd,MAX_BYTES+1);b=os.fstat(fd);c=path.lstat()
        identity=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
        need(len(raw)==a.st_size and identity(a)==identity(b)==identity(c),'library_history_changed')
        value=json.loads(raw);need(type(value)is dict,'library_history_schema_invalid');return value
    finally:os.close(fd)


def confirm(path,expected_sha256):
    """Replay only after confirming durability of the same current snapshot."""
    path=_path(path)
    need(str(path)in getattr(_LOCAL,'held',set()),'preferences_transaction_required')
    need(load(path).original_sha256==expected_sha256,'preferences_replay_snapshot_changed')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        a=os.fstat(fd);b=path.lstat()
        need(stat.S_ISREG(a.st_mode)and a.st_nlink==1 and (a.st_dev,a.st_ino)==(b.st_dev,b.st_ino),'preferences_replay_file_changed')
        try:
            os.fsync(fd)
            directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:os.fsync(directory)
            finally:os.close(directory)
        except BaseException:raise PreferencesUncertain('preferences_replay_durability_uncertain')from None
    finally:os.close(fd)
    need(load(path).original_sha256==expected_sha256,'preferences_replay_snapshot_changed')
