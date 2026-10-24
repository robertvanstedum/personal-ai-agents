#!/usr/bin/env python3
"""Read only outbound web-call arguments, never messages or results."""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys

def calls(db, session_id, since, until=None):
    uri=Path(db).resolve().as_uri()+'?mode=ro'
    with closing(sqlite3.connect(uri,uri=True)) as c:
        c.execute('PRAGMA query_only=ON')
        query='SELECT seq,event_json,created_at FROM transcript_events WHERE session_id=? AND created_at>=?'
        params=(session_id,since)
        if until is not None:
            query+=' AND created_at<=?'
            params+=(until,)
        for seq,raw,created in c.execute(query+' ORDER BY seq',params):
            e=json.loads(raw); message=e.get('message',{})
            if message.get('role')!='assistant': continue
            content=message.get('content',[])
            if not isinstance(content,list): continue
            for b in content:
                if not isinstance(b,dict) or b.get('type')!='toolCall': continue
                key={'web_search':'query','web_fetch':'url'}.get(b.get('name'))
                if not key: continue
                args=b.get('arguments',{})
                yield {'session_id':session_id,'seq':seq,'created_at':created,'call_id':b.get('id'),'tool':b['name'],key:args.get(key) if isinstance(args,dict) else None,'arguments_available':isinstance(args,dict) and key in args}

# Allowlist of the only tool and parameter names that may ever appear in summary output.
# Independent of calls(): neither row fields nor window metadata enter summaries.
TOOL_KEYS={'web_search':'query','web_fetch':'url'}

def summarize(rows):
    """Consume calls() rows into counters only.

    Construction is by allowlist: the returned dict is built field by field from
    integers plus the literal names in TOOL_KEYS. No row object, row value, or
    value-derived datum (length, scheme, host, prefix) is reachable from the
    result. The only value inspection performed is `is None`, a nullity check
    rather than a read of content; empty strings therefore count as present.
    """
    by_tool={t:0 for t in TOOL_KEYS}
    missing={t+'.'+k:0 for t,k in TOOL_KEYS.items()}
    nulls={t+'.'+k:0 for t,k in TOOL_KEYS.items()}
    total=0;failed=False
    try:
        for row in rows:
            tool=row['tool']
            if tool not in TOOL_KEYS: continue
            key=TOOL_KEYS[tool];label=tool+'.'+key
            total+=1;by_tool[tool]+=1
            if not row.get('arguments_available'): missing[label]+=1
            elif row.get(key) is None: nulls[label]+=1
    except Exception:
        failed=True
    # Raised outside the handler so the original exception is attached to
    # neither __cause__ nor __context__: its message could carry a row value.
    if failed: raise RuntimeError('outbound summary failed; detail suppressed to avoid leaking row data')
    return {'total_web_calls':total,'by_tool':by_tool,'missing_arguments':missing,'null_arguments':nulls}

# Fixed text: --since is operator-supplied and may itself be private context, so
# summary mode must never render the value or the underlying parse exception.
SINCE_ERROR='error: --since must be a timezone-aware UTC ISO date/time (value withheld)'
UNTIL_ERROR='error: --until must be a timezone-aware UTC ISO date/time (value withheld)'

def since_ms(raw):
    dt=datetime.fromisoformat(raw.replace('Z','+00:00'))
    if dt.tzinfo is None: raise ValueError('naive timestamp')
    return int(dt.timestamp()*1000)

def exact_ms(dt):
    """Floor an aware instant to the inclusive upper bound, without float loss."""
    return (dt-datetime(1970,1,1,tzinfo=timezone.utc)) // timedelta(milliseconds=1)

def until_ms(raw):
    if raw is None: return None
    result=None;failed=False
    try:
        dt=datetime.fromisoformat(raw.replace('Z','+00:00'))
        if dt.tzinfo is None: raise ValueError('naive timestamp')
        result=exact_ms(dt)
    except Exception: failed=True
    # Unlike legacy JSONL --since, this new flag withholds bad values in both
    # modes. SinceParsingPrivacyTests.test_default_jsonl_mode_behaviour_is_unchanged
    # pins that older surface; do not broaden this change to alter it.
    # Exit outside the handler to discard both exception-chain attributes.
    if failed:
        sys.stderr.write(UNTIL_ERROR+'\n');raise SystemExit(2)
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--session-id',required=True);p.add_argument('--since',required=True,help='UTC ISO date/time');p.add_argument('--summary',action='store_true',help='emit one aggregate JSON object instead of the JSONL stream')
    p.add_argument('--until',help='inclusive upper UTC ISO date/time (optional)')
    a=p.parse_args()
    if a.summary:
        since=None;failed=False
        try: since=since_ms(a.since)
        except Exception: failed=True
        # Raised outside the handler so neither the supplied value nor the
        # original ValueError is reachable via __cause__/__context__.
        if failed:
            sys.stderr.write(SINCE_ERROR+'\n');raise SystemExit(2)
        print(json.dumps(summarize(calls(a.db,a.session_id,since,until_ms(a.until))),ensure_ascii=False));return
    dt=datetime.fromisoformat(a.since.replace('Z','+00:00'))
    if dt.tzinfo is None: p.error('--since needs timezone')
    since=int(dt.timestamp()*1000)
    for row in calls(a.db,a.session_id,since,until_ms(a.until)):print(json.dumps(row,ensure_ascii=False))
if __name__=='__main__':main()
