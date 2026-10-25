"""Protected, original-owner handoff records. No freeform model task input."""
import hashlib
import json
import re
import time
from dataclasses import asdict
from pathlib import Path
from core.agent_requests.direct_contact import _read


from core.agent_requests.handoff_payload import canonical, digest, validate, action


def load_registry(path):
    path=Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('handoff_registry_unsafe')
    def load():
        doc=_read(path)
        if set(doc)!={'handoffs'} or type(doc['handoffs']) is not dict or len(doc['handoffs'])>8:
            raise ValueError('handoff_registry_invalid')
        return doc['handoffs']
    return load


class ApprovedHandoffs:
    def __init__(self,load):self.load=load

    def resolve(self,service,ref,target,conversation):
        d=self.load().get(ref)
        validate(d,live=True)
        if (d['principal']!=service.principal or d['environment']!=service.environment or
                d['conversation_id']!=conversation or d['target']!=asdict(target) or target.recipient!='claude-code'):
            raise ValueError('approved_handoff_unavailable')
        note=service.read_owner_note(conversation,d['note_request_id']) if service.read_owner_note else None
        if not note or note.get('text')!=d['owner_text']:raise ValueError('approved_owner_source_changed')
        return {'ref':ref,'sha256':digest(d),'record':d,'action':action(d)}
