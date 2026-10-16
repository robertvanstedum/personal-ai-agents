"""Bounded private native-file custody. No extraction, editing, deletion or publication.

Working documents stay editable in their private source home; this captures exact
immutable bytes plus their provenance. A separate asset export format deliberately
fails closed on older tools rather than letting a conversation-only restore drop
native files. Current per-file/export bound is 50 MiB, not all-artifact coverage.
"""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
import re
import stat
import shutil
import itertools
from datetime import datetime, timezone
from pathlib import Path
from core.vault_t1 import reader
from core.vault_t1.reader import VaultError, Damaged, sha256

FORMAT='vault-assets-export/1'
CAPTURE_FORMAT='vault-asset-capture/1'
MAX_BYTES=50 << 20
MAX_CAPTURES=4096
MAX_METADATA_BYTES=1 << 20
MAX_EXPORT_FILES=10000
RESERVE_BYTES=5 << 30
BLOCK=4096  # conservative Mac/Linux allocation model, as in the backup tool
NATIVE_EXTENSIONS={'.pdf','.docx','.pptx','.xlsx','.md','.txt','.html','.csv','.png','.jpg','.jpeg','.webp','.heic','.svg','.odt','.odp'}
ID=re.compile(r'^[0-9a-f]{32}$')
HEX=re.compile(r'^[0-9a-f]{64}$')
CAP=re.compile(r'^(\d{6})-([0-9a-f]{64})\.json$')
SCOPE=re.compile(r'^(robert|mandate:[A-Za-z0-9_.-]{1,64})$')
KEYS={'format','asset_id','edition','source_instance','source_id','source_path','original_name','scope','captured_at','bytes','sha256','previous_capture_sha256'}
SCHEMA=b'''# Native asset export, vault-assets-export/1\nOriginal native bytes, immutable capture editions and private provenance.\nobjects/<sha256>.bin stores exact bytes; metadata retains the original filename.\nEvery file is listed with its full SHA-256 and size in manifest.json.\nmanifest.sha256 detects accidental corruption, not authenticity.\nNo PDF extraction, semantic search, malware inspection or deletion is claimed.\n'''

def canonical(doc):return (json.dumps(doc,sort_keys=True,ensure_ascii=False)+'\n').encode()

def object_path(digest):return 'assets/objects/'+digest+'.bin'

def capture_path(doc,data):return f"assets/captures/{doc['asset_id']}/{doc['edition']:06d}-{sha256(data)}.json"

def read_stable(path):
    path=Path(path).absolute()
    fd=reader._open_chain('/',list(path.parts[1:]),final_file=True)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size>MAX_BYTES:raise VaultError('asset_not_bounded_regular_file')
        chunks=[];size=0
        while True:
            block=os.read(fd,1<<20)
            if not block:break
            size+=len(block)
            if size>MAX_BYTES:raise VaultError('asset_too_large')
            chunks.append(block)
        after=os.fstat(fd)
        fields=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
        if any(getattr(before,k)!=getattr(after,k) for k in fields) or size!=before.st_size:raise VaultError('asset_changed_while_read')
        return b''.join(chunks)
    finally:os.close(fd)

def check_capture(doc,rel,data):
    if not isinstance(doc,dict) or set(doc)!=KEYS or doc['format']!=CAPTURE_FORMAT:raise Damaged('asset_capture_schema')
    if not isinstance(doc['asset_id'],str) or not ID.fullmatch(doc['asset_id']) or not isinstance(doc['sha256'],str) or not HEX.fullmatch(doc['sha256']):raise Damaged('asset_capture_schema')
    if isinstance(doc['edition'],bool) or not isinstance(doc['edition'],int) or not 1<=doc['edition']<=999999:raise Damaged('asset_capture_schema')
    if isinstance(doc['bytes'],bool) or not isinstance(doc['bytes'],int) or not 0<=doc['bytes']<=MAX_BYTES:raise Damaged('asset_capture_schema')
    if not isinstance(doc['scope'],str) or not SCOPE.fullmatch(doc['scope']):raise Damaged('asset_capture_schema')
    for key,limit in [('source_instance',128),('source_id',4096),('source_path',8192),('original_name',255),('captured_at',64)]:
        if not isinstance(doc[key],str) or not doc[key] or len(doc[key])>limit or '\x00' in doc[key]:raise Damaged('asset_capture_schema')
    if '/' in doc['original_name'] or '\\' in doc['original_name']:raise Damaged('asset_capture_schema')
    expected=sha256(canonical([doc['source_instance'],doc['source_id']]))[:32]
    if expected!=doc['asset_id'] or rel!=capture_path(doc,data):raise Damaged('asset_capture_identity')
    previous=doc['previous_capture_sha256']
    if previous is not None and (not isinstance(previous,str) or not HEX.fullmatch(previous)):raise Damaged('asset_capture_schema')
    try:dt=datetime.fromisoformat(doc['captured_at'].replace('Z','+00:00'))
    except ValueError:raise Damaged('asset_capture_schema') from None
    if dt.utcoffset()!=timezone.utc.utcoffset(dt):raise Damaged('asset_capture_schema')
    return doc

def graph_rows(captures):
    rows=[];groups={}
    for rel,raw in captures.items():
        try:doc=json.loads(raw)
        except (ValueError,UnicodeDecodeError):raise Damaged('asset_capture_schema') from None
        check_capture(doc,rel,raw)
        groups.setdefault(doc['asset_id'],[]).append((doc,rel,raw))
    for group in groups.values():
        group.sort(key=lambda x:x[0]['edition'])
        if [x[0]['edition'] for x in group]!=list(range(1,len(group)+1)):raise Damaged('asset_edition_gap')
        prev=None;scope=group[0][0]['scope']
        for doc,rel,raw in group:
            if doc['previous_capture_sha256']!=prev or doc['scope']!=scope:raise Damaged('asset_lineage_mismatch')
            prev=sha256(raw);rows.append((doc,rel,raw))
    return rows


def bounded_list(root,parts,limit=None):
    limit=MAX_CAPTURES if limit is None else limit
    try:fd=reader._open_chain(root,parts,final_file=False)
    except reader.NotFound:return []
    try:
        with os.scandir(fd) as scan:
            rows=[]
            for entry in itertools.islice(scan,limit+1):
                if len(rows)>=limit:raise Damaged('asset_inventory_limit')
                rows.append((entry.name,entry.is_file(follow_symlinks=False) or entry.is_dir(follow_symlinks=False)))
            return rows
    finally:os.close(fd)

def ensure_headroom(root,need=0):
    if shutil.disk_usage(root).free < RESERVE_BYTES+need:raise VaultError('asset_disk_low')

def allocation_estimate(files,*,manifest_bytes=None):
    """Round each file and directory separately, including zero/tiny files.

    Counting already existing directories again is intentionally conservative.
    Hard-link publication does not duplicate file blocks; 64 KiB covers small
    bookkeeping/staging overhead beyond the bounded graph below.
    """
    rows=list(files)
    if manifest_bytes is not None:
        rows += [{'path':'manifest.json','bytes':manifest_bytes}, {'path':'manifest.sha256','bytes':80}]
    dirs={()}
    need=64 << 10
    for entry in rows:
        size=entry['bytes'];need+=max(BLOCK,((size+BLOCK-1)//BLOCK)*BLOCK)
        parts=tuple(entry['path'].split('/'))
        for i in range(1,len(parts)):dirs.add(parts[:i])
    return need+len(dirs)*BLOCK



def inventory(root):
    """Verified capture graph and blobs, at most one native object in memory."""
    captures={};metadata_bytes=0
    top=bounded_list(root,['assets'],3)
    if any(name not in ('captures','objects') or not plain for name,plain in top):raise Damaged('asset_store_layout')
    for aid,plain in bounded_list(root,['assets','captures']):
        if not plain or not ID.fullmatch(aid):raise Damaged('asset_capture_layout')
        for name,regular in bounded_list(root,['assets','captures',aid]):
            if not regular or not CAP.fullmatch(name):raise Damaged('asset_capture_layout')
            rel='assets/captures/'+aid+'/'+name
            if len(captures)>=MAX_CAPTURES:raise Damaged('asset_inventory_limit')
            raw=reader.read_path(root,rel.split('/'),65536);metadata_bytes+=len(raw)
            if metadata_bytes>MAX_METADATA_BYTES:raise Damaged('asset_metadata_limit')
            captures[rel]=raw
    rows=graph_rows(captures);objects={d['sha256'] for d,_,_ in rows}
    listed=set()
    for name,regular in bounded_list(root,['assets','objects']):
        digest=name[:-4] if name.endswith('.bin') else ''
        if not regular or not HEX.fullmatch(digest):raise Damaged('asset_object_layout')
        listed.add(digest)
    if not objects.issubset(listed):raise Damaged('asset_object_graph_mismatch')
    # A crash after publishing a verified immutable blob but before its capture
    # commit leaves an unreferenced object. Keep it, report it separately, and
    # allow a retry to reuse it; it is not an accepted asset or edition.
    for digest in sorted(listed):
        data=reader.read_path(root,object_path(digest).split('/'),MAX_BYTES)
        if sha256(data)!=digest:raise Damaged('asset_object_hash_mismatch')
        if any(d['bytes']!=len(data) for d,_,_ in rows if d['sha256']==digest):raise Damaged('asset_object_size_mismatch')
    return rows

def verify(root):
    try:
        rows=inventory(root)
        referenced={d['sha256'] for d,_,_ in rows}
        object_count=len(bounded_list(root,['assets','objects']))
        return {'ok':True,'checked':{'assets':len({d['asset_id'] for d,_,_ in rows}),'asset_editions':len(rows),'asset_objects':len(referenced)},'warnings':{'unreferenced_objects':object_count-len(referenced)},'problems':[]}
    except (VaultError,OSError) as exc:
        return {'ok':False,'checked':{},'problems':[{'where':'assets','code':exc.code if isinstance(exc,VaultError) else 'asset_unreadable'}]}

def capture(root,source,*,source_instance,source_id,scope='robert',now=None,expected_sha256=None,expected_bytes=None):
    """Exact owner-selected file. Existing originals are never changed or removed."""
    from core.vault_t1.portable import Publisher
    if Path(source).suffix.lower() not in NATIVE_EXTENSIONS:raise VaultError('asset_kind_not_supported')
    ensure_headroom(root,min(Path(source).lstat().st_size,MAX_BYTES)+MAX_METADATA_BYTES)
    data=read_stable(source);digest=sha256(data)
    if expected_sha256 is not None and expected_sha256!=digest:raise VaultError('asset_source_hash_changed')
    if expected_bytes is not None and (isinstance(expected_bytes,bool) or expected_bytes!=len(data)):raise VaultError('asset_source_size_changed')
    if not isinstance(scope,str) or not SCOPE.fullmatch(scope):raise VaultError('asset_scope_invalid')
    # Descriptor anchored append publication, never overwriting a blob or capture.
    class Append(Publisher):
        def __init__(self,path):
            self.path=Path(path);self.root_created=False;self.created_files=[];self.created_dirs=[]
            self.root_fd=reader._open_chain('/',list(self.path.absolute().parts[1:]),final_file=False);self._fds={():self.root_fd}
        def _dir(self,parts):
            fd=super()._dir(parts);st=os.fstat(fd)
            if st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700:raise VaultError('asset_destination_insecure')
            return fd
        def put(self,rel,data):
            super().put(rel,data)
            try:os.fsync(self._dir(tuple(rel.split('/')[:-1])))
            except OSError:raise VaultError('asset_publication_uncertain') from None
        def flush(self):
            try:
                for fd in self._fds.values():os.fsync(fd)
            except OSError:raise VaultError('asset_publication_uncertain') from None
    pub=Append(root)
    lock=None
    try:
        pub._dir(())
        root_identity=os.fstat(pub.root_fd)
        def check_root():
            try:current=os.lstat(root)
            except OSError:raise VaultError('asset_root_changed') from None
            if not stat.S_ISDIR(current.st_mode) or (current.st_dev,current.st_ino)!=(root_identity.st_dev,root_identity.st_ino):raise VaultError('asset_root_changed')
        lock=os.open('.assets.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=pub.root_fd)
        li=os.fstat(lock)
        if not stat.S_ISREG(li.st_mode) or li.st_uid!=os.getuid() or stat.S_IMODE(li.st_mode)!=0o600 or li.st_nlink!=1:raise VaultError('asset_lock_insecure')
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise VaultError('asset_busy') from None
        check_root()
        rows=inventory(root);aid=sha256(canonical([source_instance,source_id]))[:32]
        old=[r for r in rows if r[0]['asset_id']==aid];last=old[-1] if old else None
        if last and last[0]['scope']!=scope:raise VaultError('asset_scope_change_not_supported')
        if last and last[0]['sha256']==digest and last[0]['source_path']==str(Path(source).absolute()) and last[0]['scope']==scope:
            # Reconfirm directory publication on a retry after a prior fsync failure.
            check_root();pub._dir(('assets','objects'));pub._dir(('assets','captures',aid));pub.flush();check_root()
            return {'asset_id':aid,'edition':last[0]['edition'],'sha256':digest,'bytes':len(data),'added':False}
        doc={'format':CAPTURE_FORMAT,'asset_id':aid,'edition':len(old)+1,'source_instance':source_instance,'source_id':source_id,
             'source_path':str(Path(source).absolute()),'original_name':Path(source).name,'scope':scope,'captured_at':(now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
             'bytes':len(data),'sha256':digest,'previous_capture_sha256':sha256(last[2]) if last else None}
        raw=canonical(doc);rel=capture_path(doc,raw);check_capture(doc,rel,raw)
        if len(raw)>65536 or len(rows)>=MAX_CAPTURES or sum(len(r[2]) for r in rows)+len(raw)>MAX_METADATA_BYTES:raise VaultError('asset_metadata_limit')
        check_root()
        try:
            existing=reader.read_path(root,object_path(digest).split('/'),MAX_BYTES)
        except reader.NotFound:
            existing=None
        if existing is not None and (len(existing)!=len(data) or sha256(existing)!=digest):raise Damaged('asset_object_hash_mismatch')
        object_count=len(bounded_list(root,['assets','objects']))
        if existing is None and object_count>=MAX_CAPTURES:raise VaultError('asset_object_limit')
        additions=[{'path':rel,'bytes':len(raw)}, {'path':'.assets.lock','bytes':0}]
        if existing is None:additions.append({'path':object_path(digest),'bytes':len(data)})
        ensure_headroom(root,allocation_estimate(additions))
        check_root()
        if existing is None:pub.put(object_path(digest),data)
        pub.put(rel,raw);pub.flush();check_root()
        return {'asset_id':aid,'edition':doc['edition'],'sha256':digest,'bytes':len(data),'added':True}
    finally:
        if lock is not None:os.close(lock)
        pub.close()

def export(root,target,*,ids=None,mandates=()):
    from core.vault_t1.portable import Publisher,_stopped
    rows=inventory(root);allowed={'robert',*('mandate:'+m for m in mandates)}
    if ids is not None and (not isinstance(ids,(list,tuple)) or any(not isinstance(x,str) or not ID.fullmatch(x) for x in ids)):raise VaultError('asset_selection_invalid')
    rows=[r for r in rows if r[0]['scope'] in allowed and (ids is None or r[0]['asset_id'] in ids)]
    if ids is not None and set(ids)!={r[0]['asset_id'] for r in rows}:raise VaultError('asset_selection_unavailable')
    digests={r[0]['sha256'] for r in rows}
    size=sum(next(r[0]['bytes'] for r in rows if r[0]['sha256']==d) for d in digests)
    if size>MAX_BYTES:raise VaultError('asset_export_batch_too_large')
    files=[{'path':rel,'bytes':len(raw),'sha256':sha256(raw)} for _,rel,raw in rows]
    for digest in sorted(digests):
        n=next(d['bytes'] for d,_,_ in rows if d['sha256']==digest)
        files.append({'path':object_path(digest),'bytes':n,'sha256':digest})
    files.append({'path':'SCHEMA.md','bytes':len(SCHEMA),'sha256':sha256(SCHEMA)})
    total=sum(f['bytes'] for f in files)
    if len(files)>MAX_EXPORT_FILES or total>MAX_BYTES+MAX_METADATA_BYTES:raise VaultError('asset_export_batch_too_large')
    manifest={'format':FORMAT,'complete':True,'files':files,'counts':{'assets':len({d['asset_id'] for d,_,_ in rows}),'asset_editions':len(rows),'asset_bytes':size},'limitations':['native custody only; no text extraction or semantic retrieval'],'selection':{'asset_ids':ids,'scopes':sorted(allowed)}}
    raw_manifest=canonical(manifest)
    if len(raw_manifest)>MAX_METADATA_BYTES:raise VaultError('asset_metadata_limit')
    probe=Path(target).parent
    while not probe.exists():probe=probe.parent
    ensure_headroom(probe,allocation_estimate(files,manifest_bytes=len(raw_manifest)))
    pub=Publisher(target)
    try:
        for doc,rel,raw in rows:pub.put(rel,raw)
        for digest in sorted(digests):
            rel=object_path(digest);data=reader.read_path(root,rel.split('/'),MAX_BYTES)
            if sha256(data)!=digest:raise Damaged('asset_object_hash_mismatch')
            pub.put(rel,data)
        pub.put('SCHEMA.md',SCHEMA)
        pub.put('manifest.json',raw_manifest);pub.put('manifest.sha256',(sha256(raw_manifest)+'  manifest.json\n').encode())
    except BaseException as exc:_stopped(pub,exc);raise
    finally:pub.close()
    return manifest


def load_export(folder):
    from core.vault_t1.portable import _walk
    raw=reader.read_path(folder,['manifest.json'],MAX_METADATA_BYTES)
    try:side=reader.read_path(folder,['manifest.sha256'],4096).decode().split()
    except UnicodeDecodeError:raise Damaged('manifest_unreadable') from None
    if not side or side[0]!=sha256(raw):raise Damaged('manifest_hash_mismatch')
    try:doc=json.loads(raw)
    except ValueError:raise Damaged('manifest_not_json') from None
    if not isinstance(doc,dict) or doc.get('format')!=FORMAT or doc.get('complete') is not True:raise Damaged('unknown_asset_export_format')
    blobs={};total=0
    if not isinstance(doc.get('files'),list) or not doc['files'] or len(doc['files'])>MAX_EXPORT_FILES:raise Damaged('asset_export_layout')
    for item in doc['files']:
        rel=item.get('path') if isinstance(item,dict) else None
        if not isinstance(rel,str) or rel in blobs or not (rel=='SCHEMA.md' or re.fullmatch(r'assets/(objects/[0-9a-f]{64}\.bin|captures/[0-9a-f]{32}/[0-9]{6}-[0-9a-f]{64}\.json)',rel)):raise Damaged('asset_export_layout')
        data=reader.read_path(folder,rel.split('/'),MAX_BYTES)
        if len(data)!=item.get('bytes') or sha256(data)!=item.get('sha256'):raise Damaged('asset_export_hash_mismatch')
        total+=len(data)
        if total>MAX_BYTES+MAX_METADATA_BYTES:raise Damaged('asset_export_batch_too_large')
        blobs[rel]=data
    if _walk(folder)!=set(blobs)|{'manifest.json','manifest.sha256'}:raise Damaged('file_not_in_manifest')
    if 'SCHEMA.md' not in blobs:raise Damaged('asset_export_layout')
    captures={k:v for k,v in blobs.items() if k.startswith('assets/captures/')}
    if len(captures)>MAX_CAPTURES or sum(len(v) for v in captures.values())>MAX_METADATA_BYTES:raise Damaged('asset_metadata_limit')
    rows=graph_rows(captures)
    digests={d['sha256'] for d,_,_ in rows}
    if {k for k in blobs if k.startswith('assets/objects/')}!={object_path(d) for d in digests}:raise Damaged('asset_object_graph_mismatch')
    for capture,_,_ in rows:
        blob=blobs[object_path(capture['sha256'])]
        if len(blob)!=capture['bytes'] or sha256(blob)!=capture['sha256']:raise Damaged('asset_object_hash_mismatch')
    counts={'assets':len({d['asset_id'] for d,_,_ in rows}),'asset_editions':len(rows),'asset_bytes':sum(len(v) for k,v in blobs.items() if k.startswith('assets/objects/'))}
    if doc.get('counts')!=counts:raise Damaged('asset_export_count_mismatch')
    return doc,blobs

def verify_export(folder):
    try:
        doc,blobs=load_export(folder);return {'kind':'asset-export','ok':True,'checked':{'files':len(blobs)},'counts':doc['counts'],'problems':[]}
    except (VaultError,OSError) as exc:return {'kind':'asset-export','ok':False,'checked':{},'problems':[{'where':'asset-export','code':exc.code if isinstance(exc,VaultError) else 'asset_unreadable'}]}

def restore(folder,target):
    from core.vault_t1.portable import Publisher,_stopped
    doc,blobs=load_export(folder)
    probe=Path(target).parent
    while not probe.exists():probe=probe.parent
    ensure_headroom(probe,allocation_estimate([{'path':k,'bytes':len(v)} for k,v in blobs.items() if k.startswith('assets/')]))
    pub=Publisher(target)
    try:
        for rel,data in blobs.items():
            if rel.startswith('assets/'):pub.put(rel,data)
        checked=verify(target)
        if not checked['ok']:raise Damaged('restored_assets_do_not_verify')
    except BaseException as exc:_stopped(pub,exc);raise
    finally:pub.close()
    return {'target_verified':True,'restored_assets':doc['counts']['assets'],'checked':checked['checked']}
