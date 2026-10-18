"""Actual legacy route/owner-guard bodies in an isolated Flask app; no portal startup or live data."""
import ast,copy,tempfile,unittest
from functools import wraps
from pathlib import Path
from unittest.mock import patch
from flask import Flask,jsonify,redirect,render_template,request,session,url_for
from urllib.parse import quote
from jinja2 import DictLoader
from minimoi_portal.guild_ui import references
APP_SOURCE=Path(__file__).resolve().parents[1]/'minimoi_portal/app.py'
READY='# Canonical fixture\n\n## Definition of Done\n- Pass\n\n## Commit\nCommit fixture\n'
class LegacyBuildReferences(unittest.TestCase):
 def setUp(self):
  self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup);self.root=Path(self.folder.name).resolve();self.items=[];self.unreadable=None
  self.app=Flask(__name__);self.app.config.update(TESTING=True,SECRET_KEY='synthetic-reader-test')
  self.app.jinja_loader=DictLoader({'guild/spec_detail.html':'{{ filename }}|{{ content|safe }}'})
  self.app.add_url_rule('/login','login',lambda:'login')
  names={'_login_next','_current_user','_require_owner','guild_build_spec','guild_build_spec_raw','guild_build_check'}
  tree=ast.parse(APP_SOURCE.read_text());nodes=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in names]
  assert len(nodes)==len(names)
  namespace={'quote':quote,'app':self.app,'__file__':str(self.root/'minimoi_portal/app.py'),'session':session,'request':request,'redirect':redirect,'url_for':url_for,'wraps':wraps,'render_template':render_template,'jsonify':jsonify,'_read_build_queue':lambda:(self.items,self.unreadable)}
  exec(compile(ast.Module(body=nodes,type_ignores=[]),str(APP_SOURCE),'exec'),namespace)
  self.client=self.app.test_client()
  with self.client.session_transaction() as value:value['user']={'username':'owner-fixture','tier':'owner'}
 def write(self,path,text=READY):
  target=self.root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text);return target
 def urls(self,filename,item=1):return ['/guild/build/spec/'+filename,'/guild/build/spec/'+filename+'/raw','/guild/build/items/'+str(item)+'/check']
 def test_all_five_aliases_use_canonical_for_html_raw_and_readiness(self):
  for number,(legacy,canonical) in enumerate(references.LEGACY_ALIASES.items(),1):
   with self.subTest(legacy=legacy):
    self.write(canonical);self.write('_working/'+legacy,'private stale fixture')
    self.items=[{'id':number,'spec_file':legacy,'status':'design'}];before=copy.deepcopy(self.items);html,raw,check=self.urls(legacy,number)
    response=self.client.get(html);self.assertEqual(response.status_code,200);self.assertIn(b'<h1>Canonical fixture</h1>',response.data);self.assertNotIn(b'private stale fixture',response.data)
    response=self.client.get(raw);self.assertEqual(response.status_code,200);self.assertEqual(response.get_data(as_text=True),READY);self.assertEqual(response.mimetype,'text/plain');self.assertIn('filename="'+legacy+'"',response.headers['Content-Disposition'])
    data=self.client.get(check).get_json();self.assertEqual(data['spec_file'],legacy);self.assertEqual(data['current_status'],'spec_ready');self.assertEqual(data['failures'],[]);self.assertTrue(data['stale']);self.assertEqual(self.items,before)
 def test_all_existing_flat_lookup_locations_including_private_remain(self):
  for number,folder in enumerate(('docs/specs','docs/design','docs','_working'),1):
   with self.subTest(folder=folder):
    name='flat_'+str(number)+'.md';self.write(folder+'/'+name);self.items=[{'id':1,'spec_file':name,'status':'spec_ready'}]
    html,raw,check=self.urls(name);self.assertEqual(self.client.get(html).status_code,200);self.assertEqual(self.client.get(raw).get_data(as_text=True),READY);self.assertFalse(self.client.get(check).get_json()['stale'])
 def test_nonalias_flat_precedence_matches_shared_reader(self):
  self.write('docs/specs/precedence.md',READY);self.write('docs/design/precedence.md','different design');self.write('_working/precedence.md','different private');self.assertEqual(self.client.get(self.urls('precedence.md')[1]).get_data(as_text=True),READY)
 def test_missing_and_queue_failure_contracts_unchanged(self):
  self.items=[{'id':1,'spec_file':'missing.md','status':'design'}];html,raw,check=self.urls('missing.md')
  response=self.client.get(html);self.assertEqual(response.status_code,200);self.assertIn(b'Spec file not found.',response.data);self.assertEqual(self.client.get(raw).status_code,404)
  response=self.client.get(check);self.assertEqual(response.status_code,200);self.assertEqual(response.get_json(),{'error':'missing.md not found','failures':['file missing: missing.md']})
  self.assertEqual(self.client.get('/guild/build/items/99/check').status_code,404);self.items=[{'id':1}];self.assertEqual(self.client.get(check).status_code,400)
  self.unreadable='synthetic unreadable';response=self.client.get(check);self.assertEqual(response.status_code,503);self.assertEqual(response.get_json()['reason'],self.unreadable)
 def test_actual_owner_guard_runs_before_resolver(self):
  for user in (None,{'username':'guest-fixture','tier':'guest'}):
   client=self.app.test_client()
   if user:
    with client.session_transaction() as value:value['user']=user
   with patch.object(references,'resolve',side_effect=AssertionError('unauthorized read')):
    for url in self.urls('private.md'):
     response=client.get(url);self.assertEqual(response.status_code,302);self.assertIn('/login?',response.headers['Location'])
 def test_alias_target_missing_never_reads_private_fallback(self):
  legacy=next(iter(references.LEGACY_ALIASES));self.write('_working/'+legacy,'private must not be read');self.items=[{'id':1,'spec_file':legacy}]
  html,raw,check=self.urls(legacy);self.assertIn(b'Spec file not found',self.client.get(html).data);self.assertEqual(self.client.get(raw).status_code,404);self.assertIn('file missing',self.client.get(check).get_json()['failures'][0])
 def test_flat_reader_validation_and_check_source_bounds(self):
  for name in ('docs/nested.md','../outside.md','UPPER.MD','not-markdown.txt'):
   self.assertEqual(self.client.get('/guild/build/spec/'+name).status_code,404);self.assertEqual(self.client.get('/guild/build/spec/'+name+'/raw').status_code,404)
  self.write('outside.md','private outside');(self.root/'docs').mkdir(exist_ok=True);(self.root/'docs/escape.md').symlink_to(self.root/'outside.md');self.items=[{'id':1,'spec_file':'escape.md'}]
  self.assertIn('file missing',self.client.get(self.urls('escape.md')[2]).get_json()['failures'][0]);self.items=[{'id':1,'spec_file':'../outside.md'}];self.assertIn('file missing',self.client.get(self.urls('escape.md')[2]).get_json()['failures'][0])
 def test_readiness_failures_and_shared_size_bound(self):
  self.write('docs/specs/incomplete.md','# Incomplete');self.items=[{'id':1,'spec_file':'incomplete.md','status':'spec_ready'}]
  data=self.client.get(self.urls('incomplete.md')[2]).get_json();self.assertEqual(data['current_status'],'design');self.assertEqual(len(data['failures']),2);self.assertTrue(data['stale'])
  self.write('docs/specs/large.md','x'*1_000_001);self.assertIn(b'Spec file not found',self.client.get(self.urls('large.md')[0]).data);self.assertEqual(self.client.get(self.urls('large.md')[1]).status_code,404)
