"""Faithful full-or-refuse export of one authorized retained conversation snapshot.
This is retained portal evidence, not complete native history or verified playback.
"""
import json
import hashlib
import re
from .mc_voice_export import _entry as voice_entry

MAX_BYTES=1024*1024


def render(rows, *, boundary_request, principal):
    if (not isinstance(rows,list) or not rows or len(rows)>4000 or
            rows[-1].get('request_id')!=boundary_request or
            [r.get('id') for r in rows]!=sorted({r.get('id') for r in rows})):
        raise ValueError('export_snapshot_unavailable')
    if any(r.get('author_kind') == 'owner' and r.get('who') != principal for r in rows):
        raise ValueError('export_snapshot_unavailable')
    if not rows or rows[-1].get('request_id') != boundary_request or rows[-1].get('who') != principal:
        raise ValueError('export_snapshot_unavailable')
    notes=[];text=['Retained on-record portal conversation through this owner request; later replies and off-record notes are excluded.',
        'Exact retained note text follows. This is not complete native history. Voice owner speech is ASR; assistant playback is unverified.','']
    for row in rows:
        if (row.get('record_mode')!='on_record' or type(row.get('text')) is not str or
                type(row.get('created_at')) is not str or row.get('author_kind') not in ('owner','agent','platform')):
            raise ValueError('export_snapshot_unavailable')
        note={k:row.get(k) for k in ('id','request_id','who','author_kind','author_label','created_at','text')}
        # Same attribution contract as the retained Vault exporter: owner role, bounded machine IDs otherwise.
        who=str(row.get('who') or '')
        note['who']='owner' if row['author_kind']=='owner' else who if re.fullmatch(r'[A-Za-z0-9_:.+-]{1,64}',who) else 'id-'+hashlib.sha256(who.encode()).hexdigest()[:16]
        parsed=voice_entry(row)
        if parsed:
            _,speaker,stamp,part,total,partial,_=parsed
            note['voice_attribution']={'speaker':speaker,'timestamp':stamp,'part':part,'parts':total,'partial':partial,
                                       'evidence':'retained provider transcript; ASR/playback unverified'}
        notes.append(note)
        text.extend([f"Note {note['id']} · {note['author_kind']} · {note['author_label'] or note['who']} · {note['created_at']}",note['text'],''])
    canonical=json.dumps({'schema':'minimoi-retained-conversation/1','boundary_note_id':rows[-1]['id'],
        'complete_native_history':False,'notes':notes},sort_keys=True,separators=(',',':'),ensure_ascii=False)
    bundle={'files':[{'name':'conversation.txt','text':'\n'.join(text)}, {'name':'conversation.json','text':canonical}]}
    payload=json.dumps(bundle,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    if len(payload)>MAX_BYTES:raise ValueError('export_snapshot_too_large')
    return payload,{'note_count':len(rows),'boundary_note_id':rows[-1]['id'],'complete_native_history':False}
