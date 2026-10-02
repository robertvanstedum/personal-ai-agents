"""An explicit synthetic learning-cycle sample, never a real transcript."""
from pathlib import Path
import tempfile
from .contracts import VERSION, canonical, seal, stable_id, now


def seed(store, context):
    p=store.authority.principal(context)
    if not p.owner:
        raise ValueError('Owner required')
    stamp=now()
    texts=[('owner','owner','On the record for demo: compare an always-on graph server with file-first retrieval.'),
           ('builder','agent','Proposal: use a graph server first. It may improve relationship queries, but adds operational overhead.'),
           ('owner','owner','Correction: exact citations and recovery matter first. Keep file-first retrieval; defer the graph comparison.'),
           ('builder','agent','Outcome assertion: the synthetic rebuild preserved exact citations. This is fixture evidence, not a production restore.')]
    events=[dict(schema_version='minimoi.fixture-session/1.0',synthetic=True,provider='synthetic-demo',thread_id='learning-cycle-v1',
                 event_id=f'demo-{i}',index=i,author=author if origin!='owner' else p.name,origin=origin,private=False,
                 event_time='2026-10-02T12:00:00Z',text=text,attachments=[]) for i,(author,origin,text) in enumerate(texts)]
    d=seal(dict(schema_version=VERSION,id=stable_id('demo-designation-v1'),provider='synthetic-demo',thread_id='learning-cycle-v1',
                topic='demo',disclosure_class='team',start=0,stop=4,owner_event_id='demo-0',designated_by=p.name,
                created_at='2026-10-02T12:00:00Z',attachments=[]))
    with tempfile.TemporaryDirectory(prefix='intelligence-demo-') as directory:
        path=Path(directory).resolve()/'export.jsonl'
        path.write_bytes(b''.join(canonical(e)+b'\n' for e in events))
        result=store.capture(path,d,context,store.authority.issue(context,'designate',d['hash']),apply=True,captured_at=stamp)
    # An explicit synthetic fixture owner keeps exact revisions. This does not
    # represent Robert adopting a real practice or a successful real restore.
    with store._lock():
        state,_,_=store._load()
    sources={r['provenance']['event_id']:r for r in state['records'].values() if r['type']=='source'}
    def make(kind,label,text,parent,authority,data):
        rid=stable_id(['demo-record-v1',label])
        if rid in state['records']:
            return state['records'][rid]
        record=seal(dict(schema_version=VERSION,id=rid,type=kind,created_at=stamp,event_time='2026-10-02T12:00:00Z',
            author=p.name,disclosure_class='team',topics=['demo'],text=text,authority=authority,data=data,
            sources=[dict(record_id=parent['id'],record_hash=parent['hash'],start=0,end=len(parent['text']))],
            provenance=dict(provider='synthetic-demo',thread_id='learning-cycle-v1',event_id=label,
                capture_method='synthetic-fixture-assertion',extractor_version='1',model=None,fidelity='reviewed' if authority=='owner' else 'observed',
                gaps=['synthetic example only; no real restore or adopted practice'])) )
        receipt=store.authority.issue(context,'promote',record['hash']) if authority=='owner' else None
        store.add_record(record,context,receipt=receipt)
        state['records'][rid]=record
        return record
    decision_data=dict(scope='synthetic demo',supersedes=None,superseded_by=None)
    proposal=make('decision','proposal','Proposal: start with a graph server.',sources['demo-1'],'agent_proposal',decision_data)
    decision=make('decision','decision','Fixture decision: preserve exact citations first; defer the graph comparison.',sources['demo-2'],'owner',decision_data)
    lesson=make('lesson','lesson','Fixture lesson: operational complexity must earn its place through evidence.',sources['demo-2'],'owner',{})
    practice=make('practice','practice','Fixture practice: rebuild and verify exact citations before adding a retrieval backend.',lesson,'owner',
                  dict(version=1,in_force_from=stamp,in_force_to=None,agents_md_ref='synthetic-demo://practice'))
    outcome=make('outcome','outcome','Outcome assertion: the synthetic rebuild preserved exact citations; real restore remains unverified.',sources['demo-3'],'assertion',{})
    new_relations=0
    for kind,a,b in [('supersedes',decision,proposal),('motivates',lesson,practice),('outcome_of',outcome,practice)]:
        rid=stable_id(['demo-edge-v1',kind])
        if rid in state['relations']:
            continue
        edge=seal(dict(schema_version=VERSION,id=rid,kind=kind,from_id=a['id'],to_id=b['id'],author=p.name,authority='owner',
                       created_at=stamp,event_time='2026-10-02T12:00:00Z',disclosure_class='team'))
        store.add_relation(edge,context,receipt=store.authority.issue(context,'relation',edge['hash']))
        new_relations+=1
    return {**result,'learning_records':5,'new_relations':new_relations}
