import ast
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
from pathlib import Path
import threading
import pytest
from domains.curator import preferences_store as prefs
from domains.curator.library_intake import LibraryIntake,IntakeRefused

@pytest.fixture
def path(tmp_path,monkeypatch):
    monkeypatch.setattr(prefs,'writer_domain',lambda:'Linux:11111111-1111-1111-1111-111111111111')
    return tmp_path/'curator_preferences.json'

def capture(path,**kw):
    return LibraryIntake(path,history_path=path.parent/'curator_history.json').capture(principal='robert',on_record=True,operation_id=kw.pop('operation_id','one'),url=kw.pop('url','https://EXAMPLE.com:443/article'),**kw)

def test_save_receipt_and_unknown_metadata(path):
    result=capture(path,comment='Keep original reasoning',tags=['read later'])
    assert result['path']==str(path) and result['source_content']=='not_fetched'
    assert result['library_link']=='/app/curator/curator_library.html'
    data=prefs.load(path);row=next(iter(data['feedback_history'].values()))['saved'][0]
    assert row['url']=='https://example.com/article'and row['owner_intake']['annotations'][0]['publisher']is None
    assert row['owner_intake']['comments'][0]['text']=='Keep original reasoning'
    assert row['owner_intake']['annotations'][0]['metadata_origin']=='owner_supplied_not_fetched'

def test_global_operation_replay_and_conflict(path):
    first=capture(path);before=path.read_bytes();assert capture(path)['repeated']
    with pytest.raises(IntakeRefused,match='idempotency'):capture(path,url='https://example.com/different')
    assert path.read_bytes()==before

def test_duplicate_existing_url_tags_correctable_and_provenance_retained(path):
    capture(path,tags=['initial'],comment='First thought')
    receipt=capture(path,operation_id='two',url='https://example.com/article',tags=['corrected'],comment='Correction',source_claims='Source says X',interpretation='Possible Y')
    rows=next(iter(prefs.load(path)['feedback_history'].values()))['saved'];assert len(rows)==1 and receipt['result']=='already_saved_attached'
    intake=rows[0]['owner_intake'];assert intake['revision']==2 and len(intake['annotations'])==2
    assert intake['annotations'][-1]['tags']==['corrected']and intake['annotations'][-1]['source_claims']=='Source says X'
    assert intake['annotations'][-1]['agent_interpretation']=='Possible Y'and len(intake['comments'])==2

def test_liked_record_reused_preserving_patterns(path):
    with prefs.locked(path):
        v=prefs.load(path);v.update(learned_patterns={'keep':1},feedback_history={'yesterday':{'liked':[{'article_id':'known','url':'https://example.com/article','your_words':'Existing'}]}});prefs.write(path,v)
    assert capture(path)['article_id']=='known'
    data=prefs.load(path);assert data['learned_patterns']=={'keep':1}and len(data['feedback_history'])==1

@pytest.mark.parametrize('bad',['file:///etc/passwd','http://user:pass@example.com/','https://example.com/\nsecret'])
def test_no_fetch_bad_urls_refused(path,bad):
    with pytest.raises(IntakeRefused):capture(path,url=bad)
    assert not path.exists()

def test_owner_private_gate_before_any_io(path):
    for principal,on_record in [('other',True),('robert',False)]:
        with pytest.raises(IntakeRefused):LibraryIntake(path,history_path=path.parent/'curator_history.json').capture(principal=principal,on_record=on_record,operation_id='x',url='https://example.com/')
    assert not list(path.parent.iterdir())

def test_stale_snapshot_cannot_overwrite_newer(path):
    stale=prefs.load(path);capture(path)
    stale['learned_patterns']={'stale':True}
    with pytest.raises(prefs.PreferencesRefused,match='stale_snapshot'):prefs.write(path,stale)
    assert 'owner_library_operations'in prefs.load(path)

def test_transaction_threads_preserve_all_updates(path):
    def append(n):
        with prefs.locked(path):
            v=prefs.load(path);v.setdefault('fixture',[]).append(n);prefs.write(path,v)
    with ThreadPoolExecutor(max_workers=4)as pool:list(pool.map(append,range(20)))
    assert sorted(prefs.load(path)['fixture'])==list(range(20))

def test_cross_kernel_refuses_writes_preserves_bytes(path,monkeypatch):
    capture(path);before=path.read_bytes();monkeypatch.setattr(prefs,'writer_domain',lambda:'Darwin:22222222-2222-2222-2222-222222222222')
    assert prefs.load(path)['feedback_history']
    with pytest.raises(prefs.PreferencesRefused,match='domain_changed'):capture(path,operation_id='two')
    assert path.read_bytes()==before

def test_fifo_and_symlink_refuse_without_block(path):
    import os
    os.mkfifo(path)
    with pytest.raises(prefs.PreferencesRefused):prefs.load(path)
    path.unlink();target=path.parent/'target';target.write_text('{}');path.symlink_to(target)
    with pytest.raises(prefs.PreferencesRefused):prefs.load(path)

def test_real_priority_save_and_feedback_transaction(path):
    # Compile actual handlers without importing app/provider/bootstrap module.
    from flask import Flask,request,jsonify
    source=Path(__file__).resolve().parents[1]/'domains/curator/curator_server.py'
    tree=ast.parse(source.read_text());functions=[]
    for node in tree.body:
        if isinstance(node,ast.FunctionDef)and node.name in {'api_priority_feed_save','api_priority_feed_feedback'}:
            node.decorator_list=[];functions.append(node)
    namespace={'request':request,'jsonify':jsonify,'preference_store':prefs,'_DATA_DIR':path.parent,'datetime':datetime,'json':json}
    exec(compile(ast.Module(body=functions,type_ignores=[]),str(source),'exec'),namespace)
    app=Flask(__name__)
    with app.test_request_context(json={'url':'https://example.com/feed','title':'Feed','source':'Synthetic'}):assert namespace['api_priority_feed_save']().get_json()['success']
    capture(path)
    with app.test_request_context(json={'url':'https://example.com/other','title':'Other','source':'Synthetic','action':'like'}):assert namespace['api_priority_feed_feedback']().get_json()['success']
    rows=prefs.load(path)['feedback_history'];assert sum(len(v.get('saved',[]))for v in rows.values())==2
    assert sum(len(v.get('liked',[]))for v in rows.values())==1

def test_all_known_writer_rmw_sections_use_shared_transaction():
    root=Path(__file__).resolve().parents[1]
    for rel in ['scripts/x_bootstrap.py','scripts/x_adapter.py','scripts/x_import_archive.py','scripts/enrich_signals.py','domains/curator/curator_server.py','domains/curator/curator_feedback.py']:
        tree=ast.parse((root/rel).read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.Call)and isinstance(node.func,ast.Attribute)and node.func.attr=='write_text':
                assert not(isinstance(node.func.value,ast.Name)and node.func.value.id in {'PREFS_PATH','PREFS_FILE','prefs_path'})
        assert 'preference_store.locked('in(root/rel).read_text()


def test_existing_bookmark_reused_without_history_write(path):
    history=path.parent/'curator_history.json'
    history.write_text(json.dumps({'abc12':{'bookmarked':True,'url':'https://EXAMPLE.com:443/article','title':'Known title'}}))
    before=history.read_bytes();receipt=capture(path)
    assert receipt['article_id']=='abc12'and receipt['result']=='already_saved_attached'
    row=next(iter(prefs.load(path)['feedback_history'].values()))['saved'][0]
    assert row['url']=='https://EXAMPLE.com:443/article'and row['title']=='Known title'
    assert history.read_bytes()==before


@pytest.mark.parametrize('script',['x_adapter.py','x_import_archive.py','enrich_signals.py'])
def test_actual_local_script_merge_preserves_intake(path,script):
    from collections import Counter
    import logging
    root=Path(__file__).resolve().parents[1];source=root/'scripts'/script
    tree=ast.parse(source.read_text());name='update_learned_patterns'if script=='enrich_signals.py'else'update_preferences'
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name==name)
    namespace={'Counter':Counter,'PREFS_PATH':path,'PREFS_FILE':path,'ACTIVE_DOMAIN':'Synthetic','preference_store':prefs,'log':logging.getLogger('synthetic')}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),namespace)
    capture(path);prior=prefs.load(path)['owner_library_operations']
    if script=='enrich_signals.py':namespace[name]([{'metadata':{'linked_content':{'domain':'example.com','source_type':'Synthetic'}}}])
    elif script=='x_import_archive.py':namespace[name]('Synthetic',Counter({'example.com':1}))
    else:namespace[name](Counter({'example.com':1}),'Synthetic')
    result=prefs.load(path);assert result['owner_library_operations']==prior and result['learned_patterns']

def test_parent_fsync_failure_is_uncertain_not_false_saved(path,monkeypatch):
    original=prefs.os.fsync
    def fail_directory(fd):
        import stat
        if stat.S_ISDIR(prefs.os.fstat(fd).st_mode)and path.exists():raise OSError('fixture directory durability failure')
        return original(fd)
    with prefs.locked(path):pass
    monkeypatch.setattr(prefs.os,'fsync',fail_directory)
    with pytest.raises(prefs.PreferencesUncertain):capture(path)
    assert path.exists()and prefs.load(path)['owner_library_operations']
    before=path.read_bytes()
    with pytest.raises(prefs.PreferencesUncertain):capture(path)
    assert path.read_bytes()==before
    monkeypatch.setattr(prefs.os,'fsync',original)
    assert capture(path)['repeated']and path.read_bytes()==before


def test_marker_publication_requires_successful_parent_barrier_on_retry(path,monkeypatch):
    import stat
    original=prefs.os.fsync
    def fail_directory(fd):
        if stat.S_ISDIR(prefs.os.fstat(fd).st_mode):raise OSError('fixture marker directory failure')
        return original(fd)
    monkeypatch.setattr(prefs.os,'fsync',fail_directory)
    with pytest.raises(OSError):capture(path)
    marker=path.with_name('.curator-preferences-writer.json');assert marker.exists()and not path.exists()
    before=marker.read_bytes()
    with pytest.raises(prefs.PreferencesUncertain,match='domain_durability'):capture(path)
    assert marker.read_bytes()==before and not path.exists()
    monkeypatch.setattr(prefs.os,'fsync',original)
    assert capture(path)['result']=='saved'and marker.read_bytes()==before


def test_saved_replay_calls_file_and_parent_barrier(path,monkeypatch):
    capture(path);original=prefs.os.fsync;seen=[]
    def spy(fd):
        import stat
        seen.append('directory'if stat.S_ISDIR(prefs.os.fstat(fd).st_mode)else'file')
        return original(fd)
    monkeypatch.setattr(prefs.os,'fsync',spy)
    assert capture(path)['repeated']
    assert seen.count('file')>=2 and seen.count('directory')>=2


@pytest.mark.parametrize('per_item',[False,True])
def test_replay_canonical_barrier_itself_must_succeed(path,monkeypatch,per_item):
    capture(path)
    if per_item:
        with prefs.locked(path):
            data=prefs.load(path);data['owner_library_operations']={};prefs.write(path,data)
    before=path.read_bytes();original=prefs.os.fsync;directories=0
    def fail_second_parent(fd):
        import stat
        nonlocal directories
        if stat.S_ISDIR(prefs.os.fstat(fd).st_mode):
            directories+=1
            if directories==2:raise OSError('fixture canonical replay parent failure')
        return original(fd)
    monkeypatch.setattr(prefs.os,'fsync',fail_second_parent)
    with pytest.raises(prefs.PreferencesUncertain,match='replay_durability'):capture(path)
    assert directories==2 and path.read_bytes()==before
