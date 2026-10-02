"""Offline exact retrieval. No models, network, vector or portal dependency."""
import os
from pathlib import Path
import sqlite3
import tempfile

from .access import can_read
from .contracts import ContractError, Unavailable, canonical, timestamp


def capabilities():
    with sqlite3.connect(':memory:') as db:
        try:
            db.execute('CREATE VIRTUAL TABLE f USING fts5(text)')
            fts = True
        except sqlite3.OperationalError:
            fts = False
    return dict(sqlite=sqlite3.sqlite_version,fts5=fts,
                vectors='unavailable: no approved local embedding integration',
                models='unavailable: no approved model integration',
                encryption='unavailable: key custody and tool drill pending',
                backup='unavailable: real disposable restore/erasure drill pending')


def _visible_time(record, as_of, event_from, event_to, receipts):
    event = timestamp(record['event_time']) if record['event_time'] is not None else None
    knowledge = timestamp(record['created_at'])
    if record['authority'] == 'owner':
        approvals = [timestamp(r['created_at']) for r in receipts if r['target_hash'] == record['hash'] and r['operation'] in ('promote','relation')]
        if not approvals:
            return False
        knowledge = max(knowledge,min(approvals))
    if as_of is not None and (knowledge > as_of or event is None or event > as_of):
        return False
    return not ((event_from is not None and (event is None or event < event_from)) or
                (event_to is not None and (event is None or event >= event_to)))


class Retrieval:
    def __init__(self, store):
        self.store = store

    def _build(self,state,manifest):
        if not capabilities()['fts5']:
            raise Unavailable('This Python SQLite build lacks FTS5; select an approved runtime')
        path = self.store.root / 'catalog.sqlite'
        fd, scratch = tempfile.mkstemp(prefix='catalog-pending-',dir=self.store.root)
        os.close(fd)
        try:
            with sqlite3.connect(scratch) as db:
                db.execute('CREATE VIRTUAL TABLE passages USING fts5(id UNINDEXED,text)')
                db.execute('CREATE TABLE stubs(id TEXT PRIMARY KEY,type TEXT,class TEXT,created_at TEXT,event_time TEXT,hash TEXT)')
                db.execute('CREATE TABLE relations(id TEXT PRIMARY KEY,from_id TEXT,to_id TEXT,kind TEXT,authority TEXT)')
                db.execute('CREATE TABLE build(generation TEXT,class TEXT)')
                db.execute('INSERT INTO build VALUES (?,?)',(manifest['generation'],'owner-local'))
                for r in state['records'].values():
                    if r['disclosure_class'] == 'owner-only':
                        db.execute('INSERT INTO stubs VALUES (?,?,?,?,?,?)',tuple(r[k] for k in ('id','type','disclosure_class','created_at','event_time','hash')))
                    else:
                        db.execute('INSERT INTO passages VALUES (?,?)',(r['id'],r['text']))
                for r in state['relations'].values():
                    ends=[state['records'].get(r[k]) for k in ('from_id','to_id')]
                    if all(e and e['disclosure_class'] != 'owner-only' for e in ends):
                        db.execute('INSERT INTO relations VALUES (?,?,?,?,?)',tuple(r[k] for k in ('id','from_id','to_id','kind','authority')))
            with open(scratch,'rb') as f:
                os.fsync(f.fileno())
            os.replace(scratch,path)
        finally:
            if os.path.exists(scratch):
                os.unlink(scratch)
        return path

    def rebuild(self):
        with self.store._lock():
            state,_,manifest=self.store._load()
            self._build(state,manifest)
            return {'generation':manifest['generation'],'classification':'owner-local','source':'authoritative-files'}

    @staticmethod
    def _clocks(as_of,event_from,event_to):
        return tuple(timestamp(t) if t is not None else None for t in (as_of,event_from,event_to))

    def search(self,query,context,*,route='local',topic,as_of=None,event_from=None,event_to=None):
        p=self.store.authority.principal(context)
        if topic not in p.topics:
            raise ContractError('Topic outside authenticated scope')
        clocks=self._clocks(as_of,event_from,event_to)
        if route not in ('local','team-cloud'):
            raise ContractError('Unconfigured route')
        with self.store._lock():
            state,_,manifest=self.store._load()
            # Rebuild ensures corruption/staleness cannot change the authority.
            path=self._build(state,manifest)
            visible={i:r for i,r in state['records'].items() if topic in r['topics'] and
                     can_read(p,r,route) and _visible_time(r,*clocks,state['receipts'])}
            with sqlite3.connect(path) as db:
                try:
                    ids=[row[0] for row in db.execute('SELECT id FROM passages WHERE passages MATCH ? ORDER BY rank',(query,))]
                except sqlite3.OperationalError:
                    raise ContractError('Invalid FTS query') from None
            results=[]
            for rid in ids:
                if rid not in visible:
                    continue
                r=visible[rid]
                citations=self._citations(state,r,p,route,clocks)
                # Suppress dependent outputs immediately if ANY source is now
                # inaccessible, deleted or outside the requested knowledge time.
                if citations is None:
                    continue
                results.append(dict(id=rid,type=r['type'],authority=r['authority'],text=r['text'],
                                    event_time=r['event_time'],created_at=r['created_at'],
                                    citations=citations,gaps=r['provenance']['gaps']))
            # Counts scoped to this explicitly authorized topic; no global count.
            withheld=sum(1 for r in state['records'].values() if topic in r['topics'] and
                         not can_read(p,r,route) and _visible_time(r,*clocks,state['receipts']))
            return dict(results=results,withheld_in_topic=withheld,
                        gaps=['unknown_source_times_excluded'] if any(c is not None for c in clocks) else [],
                        models='unavailable',generation=manifest['generation'])

    def _citations(self,state,record,p,route,clocks,seen=None):
        seen=set() if seen is None else set(seen)
        if record['id'] in seen:
            raise ContractError('Cyclic source citations')
        seen.add(record['id'])
        if not can_read(p,record,route) or not _visible_time(record,*clocks,state['receipts']):
            return None
        if record['type']=='source':
            return [self._citation(record,0,len(record['text']))]
        output=[]
        for a in record['sources']:
            parent=self.store._anchor(state,a)
            if not can_read(p,parent,route) or not _visible_time(parent,*clocks,state['receipts']):
                return None
            if parent['type']=='source':
                output.append(self._citation(parent,a['start'],a['end']))
            else:
                nested=self._citations(state,parent,p,route,clocks,seen)
                if nested is None:
                    return None
                output.extend(nested)
        return output

    @staticmethod
    def _citation(r,start,end):
        return dict(record_id=r['id'],record_hash=r['hash'],start=start,end=end,
                    provider=r['provenance']['provider'],thread_id=r['provenance']['thread_id'],
                    event_id=r['provenance']['event_id'],raw_hash=r['data']['raw_hash'],
                    text=r['text'][start:end])

    def resolve(self,citation,context,*,route='local'):
        p=self.store.authority.principal(context)
        with self.store._lock():
            state,blobs,_=self.store._load()
            r=self.store._anchor(state,citation)
            if r['type']!='source' or not can_read(p,r,route):
                raise ContractError('Citation unavailable for this principal and route')
            expected=self._citation(r,citation['start'],citation['end'])
            if citation != expected:
                raise ContractError('Citation identity or text mismatch')
            return {'text':expected['text'],'raw_hash':r['data']['raw_hash'],'source_bytes_verified':r['data']['raw_hash'] in blobs}

    def relations(self,start_id,context,*,topic,route='local',as_of=None,include_pending=False):
        p=self.store.authority.principal(context)
        if topic not in p.topics:
            raise ContractError('Topic outside authenticated scope')
        clocks=self._clocks(as_of,None,None)
        with self.store._lock():
            state,_,_=self.store._load()
            visible={i:r for i,r in state['records'].items() if topic in r['topics'] and can_read(p,r,route)
                     and _visible_time(r,*clocks,state['receipts']) and self._citations(state,r,p,route,clocks) is not None}
            if start_id not in visible:
                return []
            edges=[r for r in state['relations'].values() if r['from_id'] in visible and r['to_id'] in visible
                   and (include_pending or r['authority']=='owner') and _visible_time(r,*clocks,state['receipts'])]
            # Bounded by finite edges; UNION stops cycles. No authority inferred
            # from reachability and no unrelated-topic endpoint disclosed.
            with sqlite3.connect(':memory:') as db:
                db.execute('CREATE TABLE edge(id TEXT,a TEXT,b TEXT)')
                db.executemany('INSERT INTO edge VALUES (?,?,?)',[(r['id'],r['from_id'],r['to_id']) for r in edges])
                ids={x[0] for x in db.execute('WITH RECURSIVE reach(n) AS (VALUES(?) UNION SELECT b FROM edge JOIN reach ON a=n) SELECT id FROM edge WHERE a IN reach',(start_id,))}
            return [{**r,'from_citations':self._citations(state,visible[r['from_id']],p,route,clocks),
                     'to_citations':self._citations(state,visible[r['to_id']],p,route,clocks)} for r in edges if r['id'] in ids]


class ModelInterface:
    def generate(self,*args,**kwargs):
        raise Unavailable('Model integration disabled; explicit owner setup and route policy required')


class VectorInterface:
    def embed(self,*args,**kwargs):
        raise Unavailable('Local embedding integration unavailable; no cloud fallback')
