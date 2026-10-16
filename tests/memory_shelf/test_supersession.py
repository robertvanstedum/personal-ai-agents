"""Synthetic original editions only; no owner data, grant or runtime writes."""
import hashlib,copy
from datetime import datetime,timezone
import pytest
from core.memory_shelf import approvals as A,events as E,editions,record,weight
from tests.memory_shelf.helpers import make_shelf,cc_bundle,cc_lines
NOW=datetime(2026,10,14,18,tzinfo=timezone.utc)
OWNER=E.OwnerAuthority('moi-approve')
def ref(shelf,rid):
 path=shelf.find_record(rid);raw=path.read_bytes();meta,_=record.load(raw.decode());ed=next(e for e in editions.list_editions(path.parent) if e.number==meta['edition']);data=ed.path.read_bytes();piece=data.decode().splitlines(keepends=True)[0].encode()
 return {'record_id':rid,'edition':meta['edition'],'edition_sha256':hashlib.sha256(data).hexdigest(),'record_sha256':hashlib.sha256(raw).hexdigest(),'lines':[1,1],'range_sha256':hashlib.sha256(piece).hexdigest()}
def world(tmp_path):
 shelf=make_shelf(tmp_path);ids=[]
 for name in ('earlier','later','third'):
  r=shelf.ingest(cc_bundle(cc_lines(name,('human','Owner discussion '+name))));ids.append(r.record_id)
 return shelf,ids

def test_authenticated_supersession_exact_refs_duplicate_and_immutable_originals(tmp_path):
 shelf,ids=world(tmp_path);a,b=ids[:2];refs=[ref(shelf,a),ref(shelf,b)];originals={p:p.read_bytes() for p in shelf.root.rglob('editions/*') if p.is_file()}
 with pytest.raises(A.ApprovalRefused):A.supersede_record(shelf,*refs,{'approved':True,'owner':'robert'},request_id='1'*32,now=NOW)
 done=A.supersede_record(shelf,*refs,OWNER,request_id='1'*32,now=NOW)
 assert not done['repeated'] and done['index_refresh_required']
 meta,_=record.load(shelf.find_record(a).read_text());assert weight.derive(meta)['weight']=='superseded' and meta['events'][-1]['successor']==b
 assert all(p.read_bytes()==data for p,data in originals.items())
 assert weight.derive(record.load(shelf.find_record(b).read_text())[0])['weight']=='deliberation'
 assert A.supersede_record(shelf,*refs,OWNER,request_id='1'*32,now=NOW)['repeated']
 assert len(record.load(shelf.find_record(a).read_text())[0]['events'])==1
 assert A.annotation_reference_evidence(shelf,refs[1])['record_id']==b

@pytest.mark.parametrize('failure',['source_hash','edition_hash','range_hash','bad_lines','extra_approval','missing_record','self','cross_scope','cross_tier'])
def test_invalid_or_stale_reference_no_projection_write(tmp_path,failure):
 shelf,ids=world(tmp_path);source=ref(shelf,ids[0]);target=ref(shelf,ids[1])
 if failure=='source_hash':source['record_sha256']='0'*64
 if failure=='edition_hash':source['edition_sha256']='0'*64
 if failure=='range_hash':source['range_sha256']='0'*64
 if failure=='bad_lines':source['lines']=[True,1]
 if failure=='extra_approval':source['approved']=True
 if failure=='missing_record':target['record_id']='0'*26
 if failure=='self':target=copy.deepcopy(source)
 if failure in ('cross_scope','cross_tier'):
  p=shelf.find_record(ids[1]);m,body=record.load(p.read_text());m['scope' if failure=='cross_scope' else 'tier']='mandate:'+ids[2] if failure=='cross_scope' else 'curated';record.write(p,m,body);target=ref(shelf,ids[1])
 before=shelf.find_record(ids[0]).read_bytes()
 with pytest.raises(A.ApprovalRefused):A.supersede_record(shelf,source,target,OWNER,request_id='2'*32,now=NOW)
 assert shelf.find_record(ids[0]).read_bytes()==before


def test_cycle_and_idempotency_mismatch_refused(tmp_path):
 shelf,ids=world(tmp_path);a,b,c=ids
 old_a,old_b=ref(shelf,a),ref(shelf,b)
 A.supersede_record(shelf,old_a,old_b,OWNER,request_id='3'*32,now=NOW)
 with pytest.raises(A.ApprovalRefused,match='annotation_cycle'):A.supersede_record(shelf,ref(shelf,b),ref(shelf,a),OWNER,request_id='4'*32,now=NOW)
 with pytest.raises(A.ApprovalRefused,match='annotation_idempotency_mismatch'):A.supersede_record(shelf,old_a,ref(shelf,c),OWNER,request_id='3'*32,now=NOW)


def test_publication_uncertain_same_request_reconciles_without_second_event(tmp_path,monkeypatch):
 from core.memory_shelf import fsio
 shelf,ids=world(tmp_path);refs=[ref(shelf,ids[0]),ref(shelf,ids[1])]
 original=fsio._sync_publication
 monkeypatch.setattr(fsio,'_sync_publication',lambda p:(_ for _ in ()).throw(fsio.PublicationUncertain(5,'publication_uncertain')))
 with pytest.raises(fsio.PublicationUncertain):A.supersede_record(shelf,*refs,OWNER,request_id='5'*32,now=NOW)
 monkeypatch.setattr(fsio,'_sync_publication',original)
 assert A.supersede_record(shelf,*refs,OWNER,request_id='5'*32,now=NOW)['repeated']
 assert len(record.load(shelf.find_record(ids[0]).read_text())[0]['events'])==1


def test_owner_cli_no_tty_or_declined_never_creates_annotation(tmp_path,monkeypatch):
 import io,json
 from core.memory_shelf import cli
 shelf,ids=world(tmp_path);refs=[ref(shelf,ids[0]),ref(shelf,ids[1])];files=[]
 for i,value in enumerate(refs):
  p=tmp_path/f'ref-{i}.json';p.write_text(json.dumps(value));files.append(str(p))
 monkeypatch.setattr(cli.config,'load',lambda _:object());monkeypatch.setattr(cli,'_shelf',lambda *a:shelf)
 args=['supersede',*ids[:2],'--source-ref',files[0],'--successor-ref',files[1],'--request-id','6'*32]
 before=shelf.find_record(ids[0]).read_bytes();prompts=[]
 assert cli.main(args,is_tty=lambda:False,confirm=lambda p:prompts.append(p) or True,out=io.StringIO())==cli.REFUSED
 assert prompts==[]
 assert cli.main(args,is_tty=lambda:True,confirm=lambda p:prompts.append(p) or False,out=io.StringIO())==cli.REFUSED
 assert refs[0]['edition_sha256'] in prompts[0] and refs[1]['range_sha256'] in prompts[0]
 assert shelf.find_record(ids[0]).read_bytes()==before
 assert cli.main(args,is_tty=lambda:True,confirm=lambda p:True,out=io.StringIO(),now=NOW)==cli.OK


def test_missing_original_edition_and_symlink_refused(tmp_path):
 shelf,ids=world(tmp_path);refs=[ref(shelf,ids[0]),ref(shelf,ids[1])]
 original=editions.list_editions(shelf.find_record(ids[1]).parent)[0].path
 raw=original.read_bytes();saved=tmp_path/'original.txt';saved.write_bytes(raw);original.unlink();original.symlink_to(saved)
 before=shelf.find_record(ids[0]).read_bytes()
 with pytest.raises(A.ApprovalRefused):A.supersede_record(shelf,*refs,OWNER,request_id='7'*32,now=NOW)
 assert shelf.find_record(ids[0]).read_bytes()==before


def test_actual_resolver_status_and_successor_ref_after_index_refresh_then_revocation(tmp_path):
 import sqlite3
 from core.memory_index import indexer,policy,resolver,store
 from tests.memory_index.helpers import add_record,FakeEmbedder
 shelf=make_shelf(tmp_path)
 a=add_record(shelf,'grok','earlier-decision',[('human','Earlier voice decision uses manual send.')]);b=add_record(shelf,'grok','later-decision',[('human','New voice decision uses realtime turn taking.')])
 ember=FakeEmbedder();assert indexer.update(shelf.root,ember,min_free_bytes=0)['code']=='ok'
 def source_row():
  con=store.connect(indexer.default_index_path(shelf.root),readonly=True);con.row_factory=sqlite3.Row
  try:return dict(con.execute('SELECT * FROM chunks WHERE record_id=?',(a,)).fetchone())
  finally:con.close()
 old=source_row();A.supersede_record(shelf,ref(shelf,a),ref(shelf,b),OWNER,request_id='8'*32,now=NOW)
 hit,gap=resolver.resolve(shelf.root,old,policy.Caller(tier='raw',scopes=('robert',)),'keyword');assert hit is None and gap
 assert indexer.update(shelf.root,ember,min_free_bytes=0)['code']=='ok'
 row=source_row();hit,gap=resolver.resolve(shelf.root,row,policy.Caller(tier='raw',scopes=('robert',)),'keyword')
 assert gap is None and hit['decision_status']=='superseded' and hit['decision_authority']=='owner_event'
 assert hit['successor_evidence']['record_id']==b and hit['successor_evidence']['edition_sha256']==ref(shelf,b)['edition_sha256']
 p=shelf.find_record(b);m,body=record.load(p.read_text());m['scope']='mandate:'+a;record.write(p,m,body)
 hit,gap=resolver.resolve(shelf.root,row,policy.Caller(tier='raw',scopes=('robert',)),'keyword')
 assert gap is None and hit['successor_evidence'] is None and hit['decision_gap']=='successor_evidence_unavailable'
 assert b not in str(hit) and b[-8:].lower() not in hit['provenance']

@pytest.mark.parametrize('malformation',['mismatch','shape','cross_scope','canary'])
def test_imported_annotation_cannot_emit_denied_reference_or_owner_authority(tmp_path,malformation):
 import sqlite3
 from core.memory_index import indexer,policy,resolver,store
 from tests.memory_index.helpers import add_record,FakeEmbedder
 shelf=make_shelf(tmp_path)
 a=add_record(shelf,'grok','source',[('human','Earlier decision source.')]);b=add_record(shelf,'grok','allowed',[('human','Allowed successor.')]);c=add_record(shelf,'grok','denied',[('human','Private successor.')])
 p=shelf.find_record(c);m,body=record.load(p.read_text());m['scope']='mandate:'+a;record.write(p,m,body)
 sr,tr=ref(shelf,a),ref(shelf,b)
 if malformation=='mismatch':tr=ref(shelf,c)
 if malformation=='shape':tr['edition']=True
 if malformation in ('cross_scope','canary'):
  p=shelf.find_record(b);m,body=record.load(p.read_text())
  if malformation=='cross_scope':m['scope']='mandate:'+a
  else:m['kind']='canary'
  record.write(p,m,body);tr=ref(shelf,b)
 event={'at':E.stamp(NOW),'kind':'superseded','by':'robert','successor':b,'source_ref':sr,'successor_ref':tr,'request_id':'a'*32}  # imported persisted event, not a newly authorized write
 record.append_event(shelf.find_record(a),event)
 indexer.update(shelf.root,FakeEmbedder(),min_free_bytes=0)
 con=store.connect(indexer.default_index_path(shelf.root),readonly=True);con.row_factory=sqlite3.Row
 try:row=dict(con.execute('SELECT * FROM chunks WHERE record_id=?',(a,)).fetchone())
 finally:con.close()
 hit,gap=resolver.resolve(shelf.root,row,policy.Caller(tier='raw',scopes=('robert',)),'keyword')
 assert gap is None and hit['successor_evidence'] is None and hit['decision_authority']=='record_event'
 assert c not in str(hit) and b not in str(hit)


def test_actual_cli_repeat_after_uncertain_uses_original_refs(tmp_path,monkeypatch):
 import io,json
 from core.memory_shelf import cli,fsio
 shelf,ids=world(tmp_path);refs=[ref(shelf,ids[0]),ref(shelf,ids[1])];files=[]
 for i,value in enumerate(refs):
  p=tmp_path/f'cli-ref-{i}.json';p.write_text(json.dumps(value));files.append(str(p))
 monkeypatch.setattr(cli.config,'load',lambda _:object());monkeypatch.setattr(cli,'_shelf',lambda *a:shelf)
 args=['supersede',*ids[:2],'--source-ref',files[0],'--successor-ref',files[1],'--request-id','b'*32]
 original=fsio._sync_publication
 monkeypatch.setattr(fsio,'_sync_publication',lambda p:(_ for _ in ()).throw(fsio.PublicationUncertain(5,'publication_uncertain')))
 assert cli.main(args,is_tty=lambda:True,confirm=lambda p:True,out=io.StringIO(),now=NOW)==cli.REFUSED
 monkeypatch.setattr(fsio,'_sync_publication',original)
 out=io.StringIO();assert cli.main(args,is_tty=lambda:True,confirm=lambda p:True,out=out,now=NOW)==cli.OK
 assert json.loads(out.getvalue())['repeated'] is True
 assert cli.main(args,is_tty=lambda:True,confirm=lambda p:True,out=io.StringIO(),now=NOW)==cli.OK
 assert len(record.load(shelf.find_record(ids[0]).read_text())[0]['events'])==1

@pytest.mark.parametrize('successor',[{},[],None,'not-a-record'])
def test_corrupt_loaded_successor_chain_refuses_without_write(tmp_path,successor):
 shelf,ids=world(tmp_path);a,b=ids[:2]
 event={'at':E.stamp(NOW),'kind':'superseded','by':'robert','successor':ids[2]}  # legacy/imported event
 event['successor']=successor  # Imported persisted event bypasses constructor validation.
 record.append_event(shelf.find_record(b),event)
 refs=[ref(shelf,a),ref(shelf,b)];before=shelf.find_record(a).read_bytes()
 with pytest.raises(A.ApprovalRefused,match='annotation_chain_unreadable'):
  A.supersede_record(shelf,*refs,OWNER,request_id='c'*32,now=NOW)
 assert shelf.find_record(a).read_bytes()==before


@pytest.mark.parametrize('by,authority', [('robert',None),('Codex',None),('robert',{'owner':'robert'}),('Codex',OWNER)])
def test_superseded_factory_refuses_unauthed_or_mismatched_actor(by,authority):
 with pytest.raises(E.EventRefused):
  E.make_event('superseded',by,authority=authority,now=NOW,successor='0'*26)


def test_superseded_factory_accepts_authenticated_actor_without_approval_side_effects():
 event=E.make_event('superseded','robert',authority=OWNER,now=NOW,successor='0'*26)
 assert event=={'at':E.stamp(NOW),'kind':'superseded','by':'robert','successor':'0'*26}
 assert 'via' not in event and 'superseded' not in E.APPROVALS and 'superseded' in E.OWNER_ACTIONS


def test_legacy_supersession_read_shape_does_not_need_a_new_authority_token():
 legacy={'at':E.stamp(NOW),'kind':'superseded','by':'old-import','successor':'0'*26}
 assert E.check_events([legacy])==[legacy]
 assert legacy['by']=='old-import' and not isinstance(legacy,E.OwnerAuthority)
