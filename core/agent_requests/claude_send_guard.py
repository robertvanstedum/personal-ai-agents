"""Exact, single-use native SendMessage admission. All authority is operator-owned.
No permission override, subprocess, network, provider or receiver mutation.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

BINARY = '/Users/vanstedum/Library/Application Support/Claude/claude-code/2.1.304/6bbba6adc37f/claude.app/Contents/MacOS/claude'
BINARY_SHA256 = '21d484aa3737c3f2e665dbd849797caafb36675f8e26e11cb47e19220bdf6c15'
CANONICAL = {'to', 'message', 'summary'}
BACKFILL = {'type', 'recipient', 'recipient_kind', 'content'}

class Refused(Exception): pass

def decode(raw):
    if len(raw) > 32768: raise Refused('size')
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result: raise Refused('duplicate_key')
            result[k] = v
        return result
    value = json.loads(raw, object_pairs_hook=pairs)
    if type(value) is not dict: raise Refused('object_required')
    return value

def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()

def digest(value): return hashlib.sha256(encoded(value)).hexdigest()

def private_root(root):
    root = Path(root)
    for p in (root, *root.parents):
        if p.is_symlink(): raise Refused('symlink_root')
    s = root.stat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) != 0o700:
        raise Refused('unsafe_root')
    return root

def actual_binary_hash():
    h = hashlib.sha256()
    with open(BINARY, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()

def configuration_hash(paths):
    """Check every operator-reviewed effective settings/hook file or absence.
    The review record must enumerate the effective sources; no settings are disabled.
    """
    if type(paths) is not dict or not paths:raise Refused('configuration_missing')
    observed={}
    for name,expected in sorted(paths.items()):
        p=Path(name)
        if not p.is_absolute() or any(q.is_symlink() for q in (p,*p.parents)):raise Refused('configuration_symlink')
        if expected is None:
            if p.exists():raise Refused('configuration_changed')
            observed[name]=None;continue
        fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_mode & 0o022 or st.st_size>1024*1024:
                raise Refused('configuration_unsafe')
            raw=os.read(fd,1024*1024+1)
            if hashlib.sha256(raw).hexdigest()!=expected:raise Refused('configuration_changed')
            doc=json.loads(raw)
            # Additional effective hooks could mutate input after our exact check.
            if type(doc) is not dict or doc.get('hooks'):raise Refused('other_hooks_unsupported')
            observed[name]=hashlib.sha256(raw).hexdigest()
        finally:os.close(fd)
    return digest(observed)


def admit(raw, binding, root, *, now=None, binary_hash=None):
    """Exact input gate; exclusive durable admission, never an allow-permission override.
    Tests inject binary_hash; production wrapper must call actual_binary_hash itself.
    A surviving intent, even partial, prevents retry. Failure after fsync is uncertain.
    """
    now = time.time() if now is None else now
    if binding.get('enabled') is not True: raise Refused('disabled')
    if binary_hash != BINARY_SHA256 or binding.get('binary_sha256') != BINARY_SHA256:
        raise Refused('binary_changed')
    if not binding['issued_at'] <= now < binding['expires_at']: raise Refused('expired')
    if binding.get('configuration_sha256') != configuration_hash(binding['configuration']): raise Refused('configuration_changed')
    target = binding['input']
    if set(target) != CANONICAL or any(type(v) is not str or not v for v in target.values()):
        raise Refused('canonical_binding')
    # Deliberately only a locally listed label plus disambiguating ref; not UUID/address/cloud/team.
    if not re.fullmatch(r"[A-Za-z0-9 _.,'-]{1,100} \[[a-f0-9]{6}\]", target['to']):
        raise Refused('local_listing_required')
    if len(encoded(target)) > 16384: raise Refused('payload_bound')
    # Non-ASCII preview formatting requires a separate reviewed vendor-compatible formatter.
    # Fail closed in this prototype instead of Python slicing Unicode incorrectly.
    message = target['message']
    if not all(32 <= ord(c) <= 126 for c in message): raise Refused('nonprintable_ascii_preview_not_implemented')
    preview = message if len(message) <= 50 else message[:49] + '…'
    event = decode(raw)
    if event.get('hook_event_name') != 'PreToolUse' or event.get('tool_name') != 'SendMessage':
        raise Refused('wrong_event')
    if event.get('session_id') != binding['sender_session_id']: raise Refused('wrong_sender')
    tool_id = event.get('tool_use_id')
    if type(tool_id) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', tool_id): raise Refused('tool_id')
    data = event.get('tool_input')
    if type(data) is not dict or set(data) not in (CANONICAL, CANONICAL | BACKFILL):
        raise Refused('input_fields')
    if any(data[k] != target[k] or type(data[k]) is not str for k in CANONICAL):
        raise Refused('input_changed')
    if BACKFILL <= set(data) and {k: data[k] for k in BACKFILL} != {
        'type': 'message', 'recipient': target['to'], 'recipient_kind': 'name', 'content': preview}:
        raise Refused('observability_changed')
    root = private_root(root)
    # One intent per immutable owner operation, not per model-generated tool ID.
    operation = binding['operation_sha256']
    if not re.fullmatch('[a-f0-9]{64}', operation): raise Refused('operation_id')
    record = {'v': 1, 'operation_sha256': operation, 'binding_sha256': digest(binding),
              'tool_use_id': tool_id, 'sender_session_id': event['session_id'],
              'input_sha256': digest(target), 'at': now, 'state': 'admitted_not_sent'}
    dfd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in root.parts[1:]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=dfd)
            os.close(dfd);dfd=child
        directory=os.fstat(dfd)
        if directory.st_uid!=os.geteuid() or stat.S_IMODE(directory.st_mode)!=0o700:raise Refused('unsafe_root')
        fd=os.open(operation+'.intent.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600,dir_fd=dfd)
        with os.fdopen(fd,'wb') as stream:
            stream.write(encoded(record));stream.flush();os.fsync(stream.fileno())
        os.fsync(dfd)
        current=root.stat(follow_symlinks=False)
        if (current.st_dev,current.st_ino)!=(directory.st_dev,directory.st_ino):raise Refused('root_changed')
    finally:os.close(dfd)
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'updatedInput': dict(target)}}

def tool_result(raw, *, expected_tool_id):
    """Caller must obtain this record from its own captured native stream, never MC/model text."""
    event = decode(raw)
    unknown = {'state': 'unknown', 'automatic_retry': False}
    if event.get('type') != 'tool_result' or event.get('tool_use_id') != expected_tool_id: return unknown
    if event.get('is_error') is True: return unknown
    content = event.get('content')
    if type(content) is not str: return unknown
    try: data = decode(content.encode())
    except Exception: return unknown
    if data.get('success') is not True: return unknown
    mid = data.get('msg_id')
    if type(mid) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', mid): return unknown
    return {'state': 'enqueued', 'msg_id': mid, 'automatic_retry': False,
            'recipient_read': False, 'completed_reply': False}

def delivery_notice(*, native_status, native_msg_ids, expected_msg_id, sender_session_id,
                    expected_sender_session_id, native_sequence, baseline_sequence):
    """Only for a future trusted native-status extractor, NOT a text parser.
    Extractor provenance/format not validated yet, so no executable reader is supplied.
    Reject batched/partial identifiers. 'delivered' means released, not read or completed.
    """
    unknown = {'state': 'unknown', 'automatic_retry': False}
    if sender_session_id != expected_sender_session_id or native_msg_ids != [expected_msg_id]: return unknown
    if type(native_sequence) is not int or type(baseline_sequence) is not int or native_sequence <= baseline_sequence: return unknown
    states = {'held':'held', 'denied':'refused', 'expired':'expired', 'refused':'refused', 'dropped':'dropped', 'delivered':'released'}
    if native_status not in states: return unknown
    return {'state': states[native_status], 'automatic_retry': False, 'completed_reply': False}
