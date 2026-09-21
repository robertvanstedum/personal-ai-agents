"""Receipt-backed collaboration. Records requests; never launches a runtime."""
import json
from platform_access import AccessError
from store import Problem, canonical, now, string, uid


class Coordination:
    def __init__(self, store):
        self.store=store
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS coordination_items(
                    id TEXT PRIMARY KEY,room TEXT NOT NULL,kind TEXT NOT NULL,
                    title TEXT NOT NULL,body TEXT NOT NULL,requester TEXT NOT NULL,
                    assignee TEXT NOT NULL,state TEXT NOT NULL,version INTEGER NOT NULL,
                    created TEXT NOT NULL,updated TEXT NOT NULL,source_snapshot TEXT);
                CREATE TABLE IF NOT EXISTS coordination_steps(
                    id TEXT PRIMARY KEY,item TEXT NOT NULL,actor TEXT NOT NULL,
                    action TEXT NOT NULL,body TEXT NOT NULL,created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS executive_snapshots(
                    id TEXT PRIMARY KEY,room TEXT NOT NULL,source TEXT NOT NULL,
                    actor TEXT NOT NULL,created TEXT NOT NULL,through_seq INTEGER NOT NULL,
                    records TEXT NOT NULL);
            ''')
            if 'source_snapshot' not in {r['name'] for r in db.execute('PRAGMA table_info(coordination_items)')}:
                db.execute('ALTER TABLE coordination_items ADD COLUMN source_snapshot TEXT')

    def _active(self, db, actor, room):
        current=self.store.access(db,actor,room,True)
        if current['state']!='active':raise Problem('Session is not recording',409)

    def create(self, actor, key, room, payload):
        if set(payload)-{'kind','title','body','assignee','source_snapshot'} or not {'kind','title','body','assignee'}<=set(payload):raise Problem('Provide kind, title, body and assignee')
        kind=payload['kind']
        if kind not in {'review','handoff','owner_input'}:raise Problem('Unknown coordination kind')
        title=string(payload['title'],'Title',160);body=string(payload['body'],'Request',16000)
        assignee=string(payload['assignee'],'Assignee',60)
        if assignee==actor:raise Problem('Choose a different participant; requests require an independent recipient')
        if assignee=='cos-dev':raise Problem('Chief of Staff replies to messages; it cannot pick up coordination requests yet')
        if kind=='owner_input' and assignee!='robert':raise Problem('Owner input is addressed to Robert')
        def action(db):
            self._active(db,actor,room)
            if not db.execute("SELECT 1 FROM members WHERE room=? AND actor=? AND role='contributor'",(room,assignee)).fetchone():
                raise Problem('Assignee needs contributor membership in this session',403)
            snapshot=payload.get('source_snapshot')
            if snapshot is not None:
                snapshot=string(snapshot,'Source snapshot',100)
                self.store.owner(actor)
                link=db.execute('SELECT * FROM executive_snapshots WHERE id=? AND source=?',(snapshot,room)).fetchone()
                if not link or kind!='handoff':raise Problem('Handoff must reference an executive snapshot of this working session',403)
                self.store.access(db,actor,link['room'])
            item=uid();at=now()
            db.execute('INSERT INTO coordination_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (item,room,kind,title,body,actor,assignee,'requested',1,at,at,snapshot))
            db.execute('INSERT INTO coordination_steps VALUES(?,?,?,?,?,?)',(uid(),item,actor,'requested',body,at))
            self.store._event(db,room,actor,'coordination',f'{kind}: {title} · requested from {assignee}',
                              origin={'source_application':'minimoi_coordination','mode':'recorded_request'})
            return self._item(db,item)
        return self.store.mutate(actor,key,dict(op='coordination_create',room=room,payload=payload),action,room)

    def _item(self, db, item):
        row=db.execute('SELECT * FROM coordination_items WHERE id=?',(item,)).fetchone()
        if not row:raise Problem('Request not found',404)
        data=dict(row)
        data['steps']=[dict(r) for r in db.execute('SELECT * FROM coordination_steps WHERE item=? ORDER BY created,rowid',(item,))]
        data['execution']='Recorded workflow only; no runtime launched'
        return data

    def transition(self, actor, key, room, item, payload):
        if set(payload)!={'action','body','version'}:raise Problem('Provide action, body and version')
        verb=payload['action'];body=string(payload['body'],'Evidence / response',16000)
        if type(payload['version']) is not int:raise Problem('Version must be an integer')
        def action(db):
            self._active(db,actor,room);row=self._item(db,item)
            if row['room']!=room:raise Problem('Request not found',404)
            if row['version']!=payload['version']:raise Problem('Request changed; refresh before acting',409)
            # Assigned identity must speak for itself; owner cannot fake a pickup.
            if verb=='pickup' and row['state']=='requested' and actor==row['assignee']:state='picked_up'
            elif verb=='submit' and row['state']=='picked_up' and actor==row['assignee']:state='result_submitted'
            elif verb=='answer' and row['kind']=='owner_input' and row['state'] in {'requested','picked_up'} and actor=='robert':state='result_submitted'
            elif verb=='acknowledge' and row['state']=='result_submitted' and actor==row['requester'] and actor!=row['assignee']:state='acknowledged'
            elif verb=='cancel' and row['state'] not in {'acknowledged','cancelled'} and actor in {row['requester'],'robert'}:state='cancelled'
            else:raise Problem('This identity cannot perform that transition from the current state',403)
            at=now();db.execute('UPDATE coordination_items SET state=?,version=version+1,updated=? WHERE id=?',(state,at,item))
            db.execute('INSERT INTO coordination_steps VALUES(?,?,?,?,?,?)',(uid(),item,actor,verb,body,at))
            self.store._event(db,room,actor,'coordination',f"{row['title']} · {state}",
                              origin={'source_application':'minimoi_coordination','mode':'recorded_transition'})
            return self._item(db,item)
        return self.store.mutate(actor,key,dict(op='coordination_transition',room=room,item=item,payload=payload),action,room)

    def list(self, actor, room):
        with self.store.connect() as db:
            db.execute('BEGIN');self.store.access(db,actor,room)
            return dict(items=[self._item(db,r['id']) for r in db.execute('SELECT id FROM coordination_items WHERE room=? ORDER BY updated DESC',(room,))],
                        snapshots=[dict(r,records=json.loads(r['records'])) for r in db.execute('SELECT * FROM executive_snapshots WHERE room=? ORDER BY created DESC',(room,))])

    def inbox(self, actor):
        items=[]
        with self.store.connect() as db:
            db.execute('BEGIN')
            rows=db.execute("SELECT * FROM coordination_items WHERE (assignee=? AND state IN ('requested','picked_up')) OR (requester=? AND state='result_submitted') ORDER BY updated DESC",(actor,actor)).fetchall()
            for row in rows:
                try:self.store.access(db,actor,row['room'])
                except (Problem,AccessError) as error:
                    if error.status in {403,404}:continue
                    raise
                # Installation grants are checked by access as well as membership.
                items.append(self._item(db,row['id']))
                if len(items)>=100:break
        return dict(items=items,limit=100)

    def snapshot(self, actor, key, room, payload):
        self.store.owner(actor)
        if set(payload)!={'source','event_ids','disclosure_acknowledged'} or payload['disclosure_acknowledged'] is not True:
            raise Problem('Explicitly acknowledge disclosure of selected source records to this session')
        source=string(payload['source'],'Source session',100);ids=payload['event_ids']
        if source==room:raise Problem('Choose a separate executive session')
        if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):
            raise Problem('Select 1–100 distinct source records')
        def action(db):
            self._active(db,actor,room);self.store.access(db,actor,source)
            records=[]
            for eid in ids:
                row=db.execute('SELECT e.*,p.kind AS actor_kind FROM events e JOIN principals p ON p.id=e.actor WHERE e.room=? AND e.id=?',(source,eid)).fetchone()
                if not row:raise Problem('Selected source record unavailable',404)
                from contribution_view import connector_view
                event=dict(row);event['origin']=json.loads(event['origin']) if event['origin'] else None
                view=connector_view(db,event)
                records.append(dict(id=row['id'],seq=row['seq'],actor=row['actor'],kind=row['kind'],created=row['created'],
                    text=view['text'] if view else row['body'],warning=view['warning'] if view else None))
            records.sort(key=lambda r:r['seq']);sid=uid();at=now()
            db.execute('INSERT INTO executive_snapshots VALUES(?,?,?,?,?,?,?)',(sid,room,source,actor,at,records[-1]['seq'],canonical(records)))
            self.store._event(db,room,actor,'executive_snapshot',f'Executive briefing snapshot · {len(records)} selected records · captured {at}',
                              origin={'source_application':'minimoi_executive_snapshot','mode':'explicit_disclosure'})
            return dict(snapshot_id=sid,source=source,destination=room,created=at,through_seq=records[-1]['seq'],records=records,
                        coverage='Owner-selected records only; not a full-session summary')
        return self.store.mutate(actor,key,dict(op='executive_snapshot',room=room,payload=payload),action,room)


def export_briefing(db, room, event):
    """Expand the immutable selected records in exports without rewriting history."""
    body=event['body']
    if event['kind']!='executive_snapshot':return body
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='executive_snapshots'").fetchone():return body
    for snapshot in db.execute('SELECT * FROM executive_snapshots WHERE room=?',(room,)):
        # The original marker contains the exact snapshot timestamp, including microseconds.
        marker=f"Executive briefing snapshot · {len(json.loads(snapshot['records']))} selected records · captured {snapshot['created']}"
        if body!=marker:continue
        lines=[body,'',f"Source session: {snapshot['source']}",f"Snapshot: {snapshot['id']}",
               'Coverage: owner-selected records only; quoted speakers are not the submitting identity.']
        for record in json.loads(snapshot['records']):
            lines.extend(['',f"Quoted speaker: {record['actor']} · source record {record['id']} · {record['created']}",record['text']])
            if record.get('warning'):lines.append(record['warning'])
        return '\n'.join(lines)
    return body
