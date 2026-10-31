"""Signed readiness of the dev Board API sidecar, run inside it: `docker exec -i <sidecar> python - < this`.

Reads the sidecar's existing key profile (never rotates or prints it), calls capabilities.read over the
signed client on the portal namespace's loopback, and prints one JSON line. Same probe as the reviewed
headless lifecycle (board-partnership-build b5). No owner notes are read and nothing is mutated.
"""
import json
import time
from pathlib import Path

from core.domain_api.client import Endpoint
from core.domain_api.contract import KeyProfile
from domains.guild.board_client import BoardClient

profile = json.loads(Path('/run/board-api/profiles.json').read_text())['profiles'][0]
profile['secret'] = bytes.fromhex(profile.pop('secret_hex'))
profile['capabilities'] = frozenset(profile['capabilities'])
client = BoardClient(Endpoint('guild', '127.0.0.1', 5013), KeyProfile(**profile), board='guild')
for attempt in range(90):
    try:
        reply = client.call('capabilities.read')
        if reply.status == 200:
            same = set(reply.body['capabilities']) == set(profile['capabilities'])
            print(json.dumps({'signed_readiness': 'pass' if same else 'capabilities_differ',
                              'attempt': attempt + 1, 'owner_notes_read': False, 'mutations': 0}))
            break
    except Exception:
        pass
    time.sleep(0.5)
else:
    print(json.dumps({'signed_readiness': 'timeout', 'owner_notes_read': False, 'mutations': 0}))
