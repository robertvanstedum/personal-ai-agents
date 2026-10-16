from pathlib import Path
import hashlib,json,zipfile
from datetime import datetime,timezone
from unittest.mock import Mock
import pytest
from core.memory_shelf import fidelity,inbox,report


def test_source_too_large_refuses_before_hash_or_parse(tmp_path,monkeypatch):
    from core.memory_shelf.config import Config,SourceCfg
    p=tmp_path/'source.jsonl';p.write_bytes(b'x'*9)
    cfg=Config(sources={'codex':SourceCfg('codex','codex',tmp_path)})
    monkeypatch.setenv('MOI_CAPTURE_MAX_SOURCE_BYTES','8');parser=Mock();monkeypatch.setitem(fidelity.watchers.PARSERS,'codex',parser)
    got,reason=fidelity.load_source(cfg,{'retained':{'kind':'source-file','source':'codex','rel':p.name}})
    assert got is None and reason=='source_too_large';parser.assert_not_called()


def test_changed_source_hash_skips_before_parser(tmp_path,monkeypatch):
    from core.memory_shelf.config import Config,SourceCfg
    p=tmp_path/'source.jsonl';p.write_text('{}')
    cfg=Config(sources={'codex':SourceCfg('codex','codex',tmp_path)})
    parser=Mock();monkeypatch.setitem(fidelity.watchers.PARSERS,'codex',parser)
    got,reason=fidelity.load_source(cfg,{'retained':{'kind':'source-file','source':'codex','rel':p.name}},expected_hash='0'*64)
    assert got is None and reason=='source_changed';parser.assert_not_called()


def test_compressed_expansion_is_guarded_before_parser(tmp_path,monkeypatch):
    p=tmp_path/'export.zip'
    with zipfile.ZipFile(p,'w',compression=zipfile.ZIP_DEFLATED) as z:z.writestr('conversations.json',' '*4096)
    parse=Mock();monkeypatch.setattr(inbox,'parse_conversation',parse)
    with pytest.raises(fidelity.ReplayHeld,match='source_too_large'):
        fidelity._inbox_selected(p,datetime.now(timezone.utc),{'synthetic'},1024)
    parse.assert_not_called()


def test_global_historical_sample_is_not_repeated_as_source_failure(tmp_path,monkeypatch):
    from types import SimpleNamespace
    shelf=SimpleNamespace(status_dir=tmp_path);(tmp_path/'quality').mkdir()
    now=datetime(2026,10,15,tzinfo=timezone.utc)
    d={'at':now.isoformat(),'checked':10,'ok':9,'failed':1,'skipped':{},'sample_size':10}
    (tmp_path/'quality/fidelity.json').write_text(json.dumps(d))
    assert report._fidelity_block(shelf,now)['state']=='failed'
    for source in ['claude-ai','claude-code','codex','grok']:
        assert report._fidelity_block(shelf,now,source)=={'state':'unknown','attribution':'global_sample_not_attributed'}


def test_only_actual_sampled_source_gets_failure(tmp_path):
    from types import SimpleNamespace
    shelf=SimpleNamespace(status_dir=tmp_path);(tmp_path/'quality').mkdir();now=datetime(2026,10,15,tzinfo=timezone.utc)
    d={'at':now.isoformat(),'checked':2,'ok':1,'failed':1,'skipped':{},'sample_size':2,'by_source':{'claude-ai':{'sample_size':1,'checked':1,'ok':0,'failed':1,'skipped':{}},'codex':{'sample_size':1,'checked':1,'ok':1,'failed':0,'skipped':{}}}}
    (tmp_path/'quality/fidelity.json').write_text(json.dumps(d))
    assert report._fidelity_block(shelf,now,'claude-ai')['state']=='failed'
    assert report._fidelity_block(shelf,now,'codex')['state']=='ok'
    assert report._fidelity_block(shelf,now,'grok')['attribution']=='not_sampled'

from .test_backfill_and_fidelity import world,main_for,rewrite_turns
from core.memory_shelf import record,watchers


def test_oversize_replay_keeps_independent_storage_evidence(tmp_path,monkeypatch):
    shelf,cfg=world(tmp_path);main=main_for(shelf,'claude-code:s-1')
    monkeypatch.setenv('MOI_CAPTURE_MAX_SOURCE_BYTES','1')
    result=fidelity.check_record(cfg,main)
    assert result['status']=='skipped' and result['reason']=='source_too_large'
    assert result['storage']=={'status':'ok'}
    result=fidelity.sample(shelf,cfg,10,seed=1)
    assert result['ok']==0 and result['checked']==0
    assert result['by_source']['claude-code']['storage']=={'checked':1,'ok':1,'failed':0}


def test_storage_damage_fails_even_when_source_replay_is_held(tmp_path,monkeypatch):
    shelf,cfg=world(tmp_path);main=main_for(shelf,'claude-code:s-1')
    rewrite_turns(main,lambda turns:turns.pop())
    monkeypatch.setenv('MOI_CAPTURE_MAX_SOURCE_BYTES','1')
    result=fidelity.check_record(cfg,main)
    assert result['status']=='failed' and result['storage']['status']=='failed'
    assert result['source_replay']=={'status':'skipped','reason':'source_too_large'}
    assert all(f['kind'].startswith('edition_mismatch:') for f in result['findings'])


def test_old_normalizer_does_not_load_raw_source(tmp_path,monkeypatch):
    shelf,cfg=world(tmp_path);main=main_for(shelf,'codex:0199aaaa-bbbb-cccc-dddd-eeeeffff0001')
    meta,body=record.load(record.read(main));meta['normalized']['normalizer']=0
    record.write(main,meta,body)
    load=Mock(side_effect=AssertionError('must not read raw source'));monkeypatch.setattr(fidelity,'load_source',load)
    result=fidelity.check_record(cfg,main)
    assert result['reason']=='normalizer_changed' and result['storage']['status']=='ok';load.assert_not_called()


def test_two_sampled_grok_members_share_one_existing_stream_pass(tmp_path,monkeypatch):
    from .test_grok_export import grok_zip,simple
    from .helpers import make_shelf,process
    from core.memory_shelf.config import Config
    shelf=make_shelf(tmp_path);box=tmp_path/'box';grok_zip(box,[simple('g-1'),simple('g-2')])
    process(shelf,box,now=datetime(2026,10,3,tzinfo=timezone.utc))
    stream=Mock(wraps=inbox._grok_stream);monkeypatch.setattr(inbox,'_grok_stream',stream)
    out=fidelity.sample(shelf,Config(shelf_root=shelf.root,inbox_root=box),n=2,seed=1)
    assert out['ok']==2 and out['failed']==0 and stream.call_count==1
    assert out['by_source']['grok']['checked']==2


def test_bounded_hash_stops_growth(tmp_path,monkeypatch):
    import io
    p=tmp_path/'growing';p.write_bytes(b'a')
    original=Path.open
    def growing_open(self,*args,**kw):
        return io.BytesIO(b'abcdef') if self==p else original(self,*args,**kw)
    monkeypatch.setattr(Path,'open',growing_open)
    with pytest.raises(fidelity.ReplayHeld,match='source_too_large'):fidelity._bounded_hash(p,4)


@pytest.mark.parametrize('bad',[-1,True,'1',None])
def test_malformed_attribution_counts_are_unknown(tmp_path,bad):
    from types import SimpleNamespace
    shelf=SimpleNamespace(status_dir=tmp_path);(tmp_path/'quality').mkdir();now=datetime(2026,10,15,tzinfo=timezone.utc)
    d={'at':now.isoformat(),'checked':1,'ok':1,'failed':0,'skipped':{},'sample_size':1,'by_source':{'codex':{'sample_size':1,'checked':1,'ok':bad,'failed':0,'skipped':{}}}}
    (tmp_path/'quality/fidelity.json').write_text(json.dumps(d))
    assert report._fidelity_block(shelf,now,'codex')=={'state':'unknown','attribution':'source_counts_invalid'}


def test_skipped_source_is_not_passed_and_global_counts_reconcile(tmp_path,monkeypatch):
    shelf,cfg=world(tmp_path);monkeypatch.setenv('MOI_CAPTURE_MAX_SOURCE_BYTES','1')
    out=fidelity.sample(shelf,cfg,10,seed=1);now=datetime(2026,10,15,tzinfo=timezone.utc)
    report.record_quality(shelf,'fidelity',{'at':now.isoformat(),'sample_size':10,**{k:out[k] for k in ('checked','ok','failed','skipped','by_source')}})
    assert report._fidelity_block(shelf,now,'codex')['state']=='unknown'
    assert sum(row['sample_size'] for row in out['by_source'].values())==4
    assert sum(out['skipped'].values())==4


def test_expanded_stream_growth_keeps_resource_hold_reason():
    import io
    stream=fidelity._ReplayCapped(io.BytesIO(b'['+b' '*30+b']'),8)
    with pytest.raises(fidelity.ReplayHeld,match='source_too_large'):list(inbox._stream(stream))


def test_duplicate_member_keeps_existing_first_match_semantics(tmp_path):
    from .test_inbox import conv,make_zip
    first=conv('u','first',[('human','first text')]);second=conv('u','second',[('human','second text')])
    make_zip(tmp_path,'dup.zip',[first,second]);path=tmp_path/'dup.zip'
    selected=fidelity._inbox_selected(path,datetime.now(timezone.utc),{'u'},1<<20)
    old=next(iter(inbox.load_items(path,datetime.now(timezone.utc),only='u')))
    assert selected['u'].source_sha256==old.source_sha256


def test_legacy_global_failure_remains_global_watch(tmp_path):
    shelf,cfg=world(tmp_path);now=datetime(2026,10,15,tzinfo=timezone.utc)
    report.record_quality(shelf,'fidelity',{'at':now.isoformat(),'sample_size':10,'checked':10,'ok':9,'failed':1,'skipped':{}})
    rep=report.build(shelf,cfg,now)
    assert rep['quality']['fidelity']['failed']==1 and report.matrix(rep)['overall']=='watch'
    assert all(s['quality']['fidelity']['state']=='unknown' for s in rep['sources'].values())


def test_malformed_unselected_member_identity_cannot_abort_replay(tmp_path):
    from .test_inbox import conv,make_zip
    good=conv('u','one',[('human','text')]);bad={'uuid':{'unhashable':'not an identity'}}
    make_zip(tmp_path,'mixed.zip',[bad,good])
    selected=fidelity._inbox_selected(tmp_path/'mixed.zip',datetime.now(timezone.utc),{'u'},1<<20)
    assert set(selected)=={'u'}


@pytest.mark.parametrize('field,value',[
    ('sample_size',{'n':1}),('checked',{'n':1}),('ok',{'n':1}),('failed',{'n':0}),
    ('skipped',{'source_changed':{'n':1}}),('skipped',{'source_changed':True}),
    ('skipped',{'source_changed':-1}),('skipped',{'source_changed':'1'})])
def test_nested_attribution_counts_are_unknown_without_arithmetic(tmp_path,field,value):
    from types import SimpleNamespace
    shelf=SimpleNamespace(status_dir=tmp_path);(tmp_path/'quality').mkdir();now=datetime(2026,10,15,tzinfo=timezone.utc)
    row={'sample_size':1,'checked':1,'ok':1,'failed':0,'skipped':{}};row[field]=value
    d={'at':now.isoformat(),'checked':1,'ok':1,'failed':0,'skipped':{},'sample_size':1,'by_source':{'codex':row}}
    (tmp_path/'quality/fidelity.json').write_text(json.dumps(d))
    assert report._fidelity_block(shelf,now,'codex')=={'state':'unknown','attribution':'source_counts_invalid'}
    assert report._fidelity_block(shelf,now)['state']=='ok'  # legacy global counters unchanged


def test_rooms_aggregate_budget_caps_growth_between_file_reads(tmp_path,monkeypatch):
    from core.memory_shelf import rooms,codes
    from . import rooms_fixtures as fx
    path=fx.write_bundle(tmp_path);limit=sum((path/n).stat().st_size for n in rooms.FILES)
    original=rooms._read_bytes;seen=[]
    def grow_after_manifest(p,cap):
        seen.append((p.name,cap));raw=original(p,cap)
        if p.name=='manifest.json':
            victim=path/'transcript.json';victim.write_bytes(victim.read_bytes()+b'x')
        return raw
    monkeypatch.setattr(rooms,'_read_bytes',grow_after_manifest)
    with pytest.raises(rooms.Refusal) as caught:rooms.verify_bundle(path,max_total_bytes=limit)
    assert caught.value.code==codes.TOO_LARGE
    assert seen[1][1]==limit-(path/'manifest.json').stat().st_size
    assert len(seen)<=3


def test_rooms_fidelity_budget_failure_still_checks_storage(tmp_path,monkeypatch):
    from core.memory_shelf import rooms,approvals,events,codes
    from core.memory_shelf.config import Config
    from . import rooms_fixtures as fx
    from .helpers import make_shelf
    shelf=make_shelf(tmp_path);root=tmp_path/'rooms';root.mkdir();fx.write_bundle(root);source=fx.cfg(root)
    now=datetime(2026,10,4,tzinfo=timezone.utc)
    watchers.dry_run(shelf,source,now);approvals.approve_source(shelf,source.name,source.fingerprint(),events.OwnerAuthority('moi-approve'));watchers.run_source(shelf,source,now=now)
    main=shelf.main_path(shelf.list_records()[0]);cfg=Config(shelf_root=shelf.root,sources={'rooms':source})
    original=rooms.read_bundle
    def grows(path,source,*,max_total_bytes=None):
        assert max_total_bytes==watchers.DEFAULT_MAX_SOURCE_BYTES
        raise rooms.Refusal(codes.TOO_LARGE)
    monkeypatch.setattr(rooms,'read_bundle',grows)
    result=fidelity.check_record(cfg,main)
    assert result['status']=='skipped' and result['reason']=='source_too_large' and result['storage']=={'status':'ok'}
