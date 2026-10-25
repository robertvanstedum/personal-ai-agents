#!/usr/bin/env python3
"""Installed isolated all-tool hook. No caller-provided authority or arbitrary path."""
import json
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from core.agent_requests import claude_send_guard as G
from core.agent_requests.direct_contact import _read
try:
    # The operator-reviewed settings supplies one fixed private binding pathname.
    if len(sys.argv)!=2:raise G.Refused('binding_required')
    p=Path(sys.argv[1])
    if not p.is_absolute() or any(q.is_symlink() for q in (p,*p.parents)):raise G.Refused('binding_unsafe')
    binding=_read(p)
    st=Path(G.BINARY).stat()
    identity=[st.st_dev,st.st_ino,st.st_uid,st.st_mode,st.st_size,st.st_mtime_ns,st.st_ctime_ns]
    if identity!=binding['binary_identity']:raise G.Refused('binary_changed')
    result=G.admit(sys.stdin.buffer.read(32769),binding,p.parent,binary_hash=binding['binary_sha256'])
    sys.stdout.write(json.dumps(result));sys.stdout.flush()
except BaseException:
    sys.stderr.write('Fixed owner handoff refused. No retry authorized.\n')
    sys.exit(2)
