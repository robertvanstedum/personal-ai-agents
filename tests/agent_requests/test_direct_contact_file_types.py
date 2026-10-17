"""Real filesystem boundary refusals, isolated child so a FIFO hang is bounded."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
import core.agent_requests

DRAFT = Path(__file__).resolve().parents[2]
REPO = Path(core.agent_requests.__file__).resolve().parents[2]

CHILD = r'''
import os,sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import core.agent_requests
core.agent_requests.__path__.insert(0, sys.argv[2])
from core.agent_requests.direct_contact import Outbox, OwnerSource, Target, Refused
root=Path(sys.argv[3]); root.chmod(0o700)
t=Target('fixture','codex','11111111-1111-1111-1111-111111111111','local','/synthetic/repo','fixture','v1')
b=Outbox(root,{'fixture':t})
rid=b.enqueue('fixture',OwnerSource('owner','c-123456789abc','note-1','kept:note-1'),'Synthetic request')
which,kind=sys.argv[4:]
p=b.root/'.direct-lock' if which=='lock' else b.root/'requests'/rid/('events.jsonl' if which=='events' else 'direct.json')
if kind=='fifo':
    p.unlink(); os.mkfifo(p,mode=0o600)
else:
    p.chmod(0o644)
try:
    if which=='lock':
        with b._lock(): raise AssertionError('nonregular lock admitted')
    else: b.status(rid)
except Refused as e:
    expected={'lock':'unsafe_lock','events':'unsafe_events','entry':'unsafe_entry'}[which]
    assert str(e)==expected,(str(e),expected)
    print(expected)
else: raise AssertionError('unsafe filesystem entry admitted')
'''


@pytest.mark.parametrize('which', ['entry', 'events', 'lock'])
@pytest.mark.parametrize('kind', ['fifo', 'public_mode'])
def test_malformed_entry_cannot_block_or_enter_lock(tmp_path, which, kind):
    result = subprocess.run([sys.executable, '-I', '-c', CHILD, str(REPO),
                             str(DRAFT / 'core/agent_requests'), str(tmp_path), which, kind],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=3)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    assert result.stdout.strip() == {'entry': b'unsafe_entry', 'events': b'unsafe_events',
                                    'lock': b'unsafe_lock'}[which]
