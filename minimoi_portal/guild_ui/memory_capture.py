"""Read only the counts-only memory matrix, never the underlying shelf."""
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 'minimoi.memory-capture-matrix.v1'
RANK = {'off': 0, 'current': 1, 'unknown': 2, 'watch': 3}
TOKEN = re.compile(r'[A-Za-z0-9_:.+ -]{1,100}\Z')


def stamp(value):
    if not isinstance(value, str):
        raise ValueError('timestamp')
    moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if moment.tzinfo is None:
        raise ValueError('timezone')
    return moment


def age(moment, now):
    seconds = max(0, int((now - moment).total_seconds()))
    for divisor, label in [(86400, 'days'), (3600, 'h'), (60, 'min')]:
        if seconds >= divisor:
            return f'{seconds // divisor} {label} ago'
    return 'just now'


def overview(root=None, *, now=None):
    now = now or datetime.now(timezone.utc)
    unknown = dict(status='unknown', reason='Memory capture report unavailable', rows=[], generated_at=None)
    root = root if root is not None else os.environ.get('MINIMOI_JOBS_DIR')
    if not root:
        return unknown
    try:
        path = Path(root) / 'memory-capture-matrix.json'
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError('not regular')
            raw = handle.read(65537)
        if len(raw) > 65536:
            raise ValueError('oversized')
        doc = json.loads(raw)
        if not isinstance(doc, dict) or doc.get('schema') != SCHEMA or doc.get('overall') not in RANK:
            raise ValueError('schema')
        generated = stamp(doc['generated_at'])
        limit = doc['stale_after_s']
        if type(limit) is not int or not 0 < limit <= 172800 or (generated-now).total_seconds() > 120:
            raise ValueError('freshness')
        stale = (now-generated).total_seconds() > limit
        if not isinstance(doc['rows'], list) or len(doc['rows']) > 32:
            raise ValueError('rows')
        rows, seen = [], set()
        for entry in doc['rows']:
            for key in ('source', 'label', 'reason'):
                if not isinstance(entry[key], str) or not TOKEN.fullmatch(entry[key]):
                    raise ValueError('label')
            source = entry['source']
            if source in seen:
                raise ValueError('duplicate')
            seen.add(source)
            state, kind, count = entry['status'], entry['last_check_kind'], entry['captured_records']
            if state not in RANK or kind not in ('none', 'check', 'capture') or entry['mode'] not in ('manual', 'scheduled'):
                raise ValueError('state')
            if type(count) is not int or count < 0:
                raise ValueError('count')
            at = entry['last_success_at']
            moment = stamp(at) if at is not None else None
            if moment and (moment-generated).total_seconds() > 120:
                raise ValueError('future')
            if state == 'current' and moment is None:
                raise ValueError('no evidence')
            rows.append(dict(label=entry['label'], manual=entry['mode']=='manual', records=count,
                             status='unknown' if stale else state,
                             reason='Report stale' if stale else entry['reason'].replace('_', ' '), at=at,
                             age=age(moment, now) if moment else 'No successful check recorded',
                             kind='Capture' if kind=='capture' else 'Check'))
        return dict(status='unknown' if stale else max((r['status'] for r in rows), key=RANK.get, default='unknown'),
                    reason='Report stale; counts are historical' if stale else 'Background capture by source',
                    rows=rows, generated_at=doc['generated_at'])
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return unknown
