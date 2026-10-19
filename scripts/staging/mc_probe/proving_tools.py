"""Operator-side bounded repository access for a dev MC tool adapter.

No caller-selected shell command, checkout, image, host path, or network access.
The operator pins reviewed revisions and an immutable source snapshot/image.
Transport/authentication and OpenClaw's tool allowlist are separate prerequisites.
"""
from pathlib import PurePosixPath, Path
import difflib
import hashlib
import json
import re
import subprocess
import threading
from uuid import uuid4

class ToolRefused(ValueError):pass

MAX_CHANGES=200        # changed paths listed per commit
MAX_SUBJECT=200        # characters of the commit subject

def check_source_path(path):
 """The read allowlist: shared by reads, diffs and the readable flag on listed changes."""
 if not isinstance(path,str) or not re.fullmatch(r'[A-Za-z0-9_./-]+',path):raise ToolRefused('invalid_path')
 parts=PurePosixPath(path).parts
 if not parts or path.startswith('/') or '..' in parts or parts[0] not in ('core','minimoi_portal','domains','tests','services','scripts','docker'):
  raise ToolRefused('path_not_allowlisted')
 if any(p.startswith('.') or p in ('data','auth','secrets','node_modules') for p in parts):raise ToolRefused('private_or_generated_path')
 if PurePosixPath(path).suffix not in ('.py','.js','.mjs','.ts','.json','.md','.sh'):
  raise ToolRefused('source_text_only')

SUITES={
 'board_commands': 'tests/guild/shop_floor/test_owner_board_commands.py',
 'board_multisentence': 'tests/guild/shop_floor/test_owner_board_multisentence.py',
}

class RepositoryTools:
 def __init__(self, repo, revisions, *, snapshot=None, image=None, snapshot_manifest=None):
  self.repo=Path(repo).resolve();self.revisions={}
  for label, revision in revisions.items():
   if not re.fullmatch('[0-9a-f]{7,40}',revision):raise ToolRefused('revision_must_be_hex')
   sha=self._git('rev-parse','--verify',revision+'^{commit}').decode().strip()
   self.revisions[label]=sha
  self.snapshot=Path(snapshot).resolve() if snapshot else None
  self.image=image;self.lock=threading.Lock();self.snapshot_manifest=snapshot_manifest

 def _git(self,*args):
  result=subprocess.run(['git','--no-pager','-C',str(self.repo),*args],capture_output=True,timeout=10,
                        env={'PATH':'/usr/bin:/bin','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_NO_REPLACE_OBJECTS':'1'})
  if result.returncode:raise ToolRefused('repository_object_unavailable')
  return result.stdout

 def read_file(self, revision, path, start=1, count=120):
  if revision not in self.revisions:raise ToolRefused('revision_not_allowlisted')
  check_source_path(path)
  if type(start)!=int or type(count)!=int or start<1 or not 1<=count<=200:raise ToolRefused('invalid_line_window')
  sha=self.revisions[revision]; obj=sha+':'+path
  entry=self._git('ls-tree',sha,'--',path).decode().split()
  if len(entry)<3 or entry[0] not in ('100644','100755') or entry[1]!='blob':raise ToolRefused('regular_source_blob_required')
  if int(self._git('cat-file','-s',obj))>256_000:raise ToolRefused('source_file_too_large')
  raw=self._git('cat-file','blob',obj)
  try:lines=raw.decode('utf-8').splitlines()
  except UnicodeError:raise ToolRefused('source_not_utf8') from None
  return {'revision':sha,'path':path,'blob':entry[2],'sha256':hashlib.sha256(raw).hexdigest(),
          'start':start,'total_lines':len(lines),'lines':lines[start-1:start-1+count]}

 def diff(self, before, after, path):
  """Compare one allowed source path at two frozen revisions, with bounded output."""
  left=self.read_file(before,path,1,1);right=self.read_file(after,path,1,1)
  a=self._git('cat-file','blob',left['revision']+':'+path)
  b=self._git('cat-file','blob',right['revision']+':'+path)
  # Reuse read_file's regular-blob, path, UTF-8 and size checks above.
  result=''.join(difflib.unified_diff(a.decode().splitlines(True),b.decode().splitlines(True),
                fromfile=left['revision']+'/'+path,tofile=right['revision']+'/'+path))
  raw=result.encode()
  return {'before':left['revision'],'after':right['revision'],'path':path,
          'before_sha256':left['sha256'],'after_sha256':right['sha256'],
          'diff':raw[:64000].decode('utf-8','replace'),'truncated':len(raw)>64000,
          'diff_sha256':hashlib.sha256(raw).hexdigest()}

 def changes(self, revision):
  """What one pinned commit changed against its first parent: subject, parents, and each changed
  path with its status. Paths and status only; never file contents, other refs or a command."""
  if not isinstance(revision,str) or revision not in self.revisions:raise ToolRefused('revision_not_allowlisted')
  sha=self.revisions[revision]
  head=self._git('show','-s','--no-show-signature','--format=%H%x00%P%x00%s',sha).decode('utf-8','replace').rstrip('\n').split('\x00')
  if len(head)!=3 or head[0]!=sha:raise ToolRefused('repository_object_unavailable')
  parents=head[1].split()
  # First parent, or the empty tree for a root commit; rename detection, NUL-separated paths.
  base=parents[0] if parents else '4b825dc642cb6eb9a060e54bf8d69288fbee4904'
  raw=self._git('diff-tree','-r','-z','-M','--name-status','--no-ext-diff','--no-textconv',base,sha).split(b'\x00')
  labels={}
  for label,full in self.revisions.items():labels.setdefault(full,[]).append(label)
  files=[];i=0
  while i<len(raw) and raw[i]:
   status=raw[i].decode('ascii','replace');kind=status[:1]
   if kind in ('R','C'):
    old,new=raw[i+1].decode('utf-8','replace'),raw[i+2].decode('utf-8','replace');i+=3
   else:
    old,new=None,raw[i+1].decode('utf-8','replace');i+=2
   entry={'status':{'A':'added','M':'modified','D':'deleted','R':'renamed','C':'copied','T':'type_changed'}.get(kind,'other'),'path':new}
   if old is not None:entry['from']=old
   try:check_source_path(new);entry['readable']=True
   except ToolRefused:entry['readable']=False
   files.append(entry)
  return {'revision':sha,'subject':head[2][:MAX_SUBJECT],
          'parents':[{'commit':p,'labels':sorted(labels.get(p,[]))} for p in parents],
          'compared_with':parents[0] if parents else 'empty_tree',
          'total_files':len(files),'files':files[:MAX_CHANGES],'truncated':len(files)>MAX_CHANGES}

 def verify_snapshot(self):
  manifest=self.snapshot_manifest
  if not self.snapshot or not isinstance(manifest,dict) or set(manifest)!={'commit','files'}:
   raise ToolRefused('snapshot_manifest_required')
  if manifest['commit'] not in self.revisions.values() or not isinstance(manifest['files'],dict):
   raise ToolRefused('snapshot_revision_not_allowlisted')
  seen={}
  for path in self.snapshot.rglob('*'):
   if path.is_symlink():raise ToolRefused('snapshot_symlink_refused')
   if path.is_file():seen[path.relative_to(self.snapshot).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
  if seen!=manifest['files']:raise ToolRefused('snapshot_changed')
  return manifest['commit']

 def run_suite(self, suite):
  if suite not in SUITES:raise ToolRefused('suite_not_allowlisted')
  if not self.snapshot or not self.image or not re.fullmatch(r'sha256:[0-9a-f]{64}',self.image):
   raise ToolRefused('reviewed_snapshot_and_image_required')
  snapshot_commit=self.verify_snapshot()
  if not self.lock.acquire(blocking=False):raise ToolRefused('one_test_job_at_a_time')
  name='mc-proving-test-'+uuid4().hex
  # No Docker socket, provider credential, shared writable store, or network
  # enters the child. Only fixed reviewed tests may run against the snapshot.
  cmd=['docker','run','--rm','--pull','never','--name',name,'--network','none','--read-only',
       '--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','64',
       '--memory','256m','--memory-swap','256m','--cpus','0.5','--user','65534:65534',
       '--tmpfs','/tmp:rw,noexec,nosuid,size=32m',
       '--mount',f'type=bind,src={self.snapshot},dst=/source,readonly',
       '--workdir','/source','--entrypoint','/usr/bin/env',self.image,
       '-i','PATH=/usr/local/bin:/usr/bin:/bin','HOME=/tmp','PYTHONDONTWRITEBYTECODE=1',
       'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1','PYTHONPATH=/source','python','-m','pytest',
       '-q','-p','no:cacheprovider',SUITES[suite]]
  try:
   result=subprocess.run(cmd,capture_output=True,timeout=60)
   output=result.stdout+result.stderr
   return {'suite':suite,'snapshot_commit':snapshot_commit,'argv':['python','-m','pytest','-q','-p','no:cacheprovider',SUITES[suite]],
           'exit_code':result.returncode,'output':output[:64000].decode('utf-8','replace'),
           'truncated':len(output)>64000,'output_sha256':hashlib.sha256(output).hexdigest(),'image':self.image}
  except subprocess.TimeoutExpired:
   raise ToolRefused('test_timeout_outcome_requires_receipt') from None
  finally:
   subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=15)
   self.lock.release()
