"""Operator-owned file queue for four dev-only tools; never exposes Docker socket.

Run only against the dedicated agent queue, not a shared or user-controlled folder.
A claimed request is never replayed. Restart requires operator reconciliation.
"""
import json
import fcntl
import os
from pathlib import Path
import re
import time
import subprocess
from proving_tools import ToolRefused

METHODS={
 'mc_repo_read':('read_file',{'revision','path','start','count'},{'revision','path'}),
 'mc_repo_diff':('diff',{'before','after','path'},{'before','after','path'}),
 'mc_run_tests':('run_suite',{'suite'},{'suite'}),
 'mc_repo_changes':('changes',{'revision'},{'revision'}),
}

def dispatch(tools, request):
 if not isinstance(request,dict) or set(request)!={'id','method','args'}:raise ToolRefused('invalid_request')
 if not isinstance(request['id'],str) or not re.fullmatch('[0-9a-f-]{36}',request['id']):raise ToolRefused('invalid_id')
 if request['method'] not in METHODS:raise ToolRefused('method_not_allowlisted')
 method,allowed,required=METHODS[request['method']];args=request['args']
 if not isinstance(args,dict) or set(args)-allowed or not required.issubset(args):raise ToolRefused('invalid_arguments')
 return getattr(tools,method)(**args)

def _serve_locked(tools, directory, *, max_requests=30, max_seconds=900):
 root=Path(directory)
 if root.is_symlink() or not root.is_dir():raise ToolRefused('dedicated_queue_required')
 if list(root.glob('*.claimed')):raise ToolRefused('unresolved_tool_request')
 deadline=time.monotonic()+max_seconds;count=0
 while time.monotonic()<deadline and count<max_requests:
  for path in sorted(root.glob('*.request')):
   if not re.fullmatch('[0-9a-f-]{36}\\.request',path.name):raise ToolRefused('invalid_queue_filename')
   claimed=path.with_suffix('.claimed')
   fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
   with os.fdopen(fd,'rb') as stream:raw=stream.read(8193)
   # Claim before parsing; malformed requests still receive a definite refusal.
   os.rename(path,claimed);count+=1
   try:
    if len(raw)>8192:raise ToolRefused('request_too_large')
    request=json.loads(raw)
    if not isinstance(request,dict):raise ToolRefused('invalid_request')
    if request.get('id')!=path.stem:raise ToolRefused('request_id_mismatch')
    result={'ok':True,'result':dispatch(tools,request)}
   except (ToolRefused,TypeError,ValueError) as exc:result={'ok':False,'error':str(exc)}
   except (subprocess.TimeoutExpired,OSError):result={'ok':False,'error':'tool_unavailable'}
   out=root/(path.stem+'.response')
   temporary=out.with_suffix('.writing')
   fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   with os.fdopen(fd,'w') as stream:
    json.dump(result,stream);stream.flush();os.fsync(stream.fileno())
   os.link(temporary,out);temporary.unlink()
   claimed.rename(root/(path.stem+'.completed'))
   if count>=max_requests:break
  time.sleep(.1)


def serve(tools, directory, *, max_requests=30, max_seconds=900):
 root=Path(directory)
 if root.is_symlink() or not root.is_dir():raise ToolRefused('dedicated_queue_required')
 fd=os.open(root/'.worker.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
 try:
  try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise ToolRefused('another_tool_worker_active') from None
  return _serve_locked(tools,root,max_requests=max_requests,max_seconds=max_seconds)
 finally:os.close(fd)
