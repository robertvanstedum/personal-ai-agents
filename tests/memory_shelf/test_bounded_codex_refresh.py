"""Synthetic admission budgets and targeted capture; no real sources or large files."""
import dataclasses
import io
import json
from pathlib import Path
import tracemalloc
import pytest
from core.memory_shelf import approvals, capture_limits as caps, codes, events, fsio, ledger, render, sessions, watchers, coverage
from core.memory_shelf.config import SourceCfg
from tests.memory_shelf.helpers import make_shelf, codex_lines

SID='11111111-1111-1111-1111-111111111111'
OTHER='22222222-2222-2222-2222-222222222222'
LIMITS=caps.CodexLimits()


def jl(rows): return [(json.dumps(r)+'\n').encode() for r in rows]

def fixture(tmp_path, body=None, never=()):
    shelf=make_shelf(tmp_path); root=tmp_path/'sources'; root.mkdir()
    source=root/('rollout-'+SID+'.jsonl')
    source.write_text(body or codex_lines(SID,('human','owner'),('assistant','answer')))
    other=root/('rollout-'+OTHER+'.jsonl');other.write_text(codex_lines(OTHER,('human','unselected')))
    cfg=SourceCfg('codex','codex',root,never)
    watchers.dry_run(shelf,cfg);approvals.approve_source(shelf,'codex',cfg.fingerprint(),events.OwnerAuthority('moi-approve'))
    return shelf,cfg,source,other


def selected(shelf,cfg,**kw):
    return watchers.run_source(shelf,cfg,source_ids=[SID],codex_limits=kw.pop('codex_limits',LIMITS),**kw)


def test_admitted_lines_are_byte_identical_with_exact_normalizer_and_coverage():
    rows=[{'type':'session_meta','payload':{'id':SID,'thread_source':'agent_created_thread'}},
          {'type':'event_msg','payload':{'type':'task_started'}},
          {'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'agent brief'}]}},
          {'type':'event_msg','payload':{'type':'task_started'}},
          {'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'owner correction'}]}},
          {'type':'event_msg','payload':{'type':'item_completed','item':{'type':'UserMessage','client_id':'client-1','content':[{'type':'text','text':'owner correction'}]}}},
          {'type':'event_msg','payload':{'type':'item_completed','item':{'type':'AgentMessage','content':[{'type':'text','text':'reply'}]}}},
          {'type':'response_item','payload':{'type':'agent_message','author':'a','recipient':'b','content':[{'type':'input_text','text':'peer evidence'},{'type':'encrypted_content','data':'opaque'}]}}]
    rows += [{'type':'compacted','payload':{'text':'tool-like ignored bytes'*100}} for _ in range(30)]
    raw=jl(rows); checked=list(caps.checked_codex_lines(iter(raw),LIMITS,SID));assert checked==raw
    a=coverage.parse_with_coverage('codex',iter(raw),watchers.PARSERS)
    b=coverage.parse_with_coverage('codex',iter(checked),watchers.PARSERS)
    assert a==b and a.normalizer==5
    assert [t.text for t in render.dialogue_turns(b.turns)]==['owner correction','reply']
    assert render.edition_bytes(a,'a'*64,123,0)==render.edition_bytes(b,'a'*64,123,0)


@pytest.mark.parametrize('changes,reason',[
    ({'line_bytes':12},'source_line_limit'),
    ({'rows':1},'source_row_limit'),
    ({'retained_bytes':5},'source_text_limit'),
    ({'entries':1},'source_entry_limit'),
    ({'parts_per_message':1},'source_parts_limit'),
])
def test_every_budget_refuses_without_stage_or_success_state(tmp_path,monkeypatch,changes,reason):
    body=codex_lines(SID,('human','synthetic long enough'),('assistant','reply'))
    if reason=='source_parts_limit':
        rows=[json.loads(x) for x in body.splitlines()];rows[1]['payload']['item']['content'].append({'type':'text','text':'second part'});body=b''.join(jl(rows)).decode()
    shelf,cfg,source,other=fixture(tmp_path,body)
    original=source.read_bytes();monkeypatch.setattr(shelf,'stage',lambda *_:pytest.fail('partial stage'))
    result=selected(shelf,cfg,codex_limits=dataclasses.replace(LIMITS,**changes))
    assert result['counts']=={'failed':1,reason:1} and result['status']=='selected_only'
    assert shelf.list_records()==[] and source.read_bytes()==original
    state=fsio.read_json(watchers._state_path(shelf,'codex'))['files'];entry=next(iter(state.values()))
    assert entry['outcome']=='failed' and entry['reason']==reason and entry.get('normalizer') is None
    assert ledger.report(shelf)['missing']==1
    assert not (Path(shelf.status_dir)/'watch-codex.json').exists()


def test_full_handoff_payload_not_just_visible_text_is_charged():
    rows=jl([{'type':'response_item','payload':{'type':'agent_message','content':[{'type':'input_text','text':'x'},{'type':'encrypted_content','data':'z'*1000}]}}])
    with pytest.raises(caps.ResourceLimit,match='source_text_limit'):
        list(caps.checked_codex_lines(rows,dataclasses.replace(LIMITS,retained_bytes=200)))


def test_handoff_nested_entries_are_bounded():
    rows=jl([{'type':'response_item','payload':{'type':'agent_message','content':[{'type':'input_text','text':'x'}],'unused':list(range(50))}}])
    with pytest.raises(caps.ResourceLimit,match='source_entry_limit'):
        list(caps.checked_codex_lines(rows,dataclasses.replace(LIMITS,entries=20)))


def test_targeted_pass_captures_only_exact_source_and_does_not_advance_global_success(tmp_path):
    shelf,cfg,source,other=fixture(tmp_path);status=Path(shelf.status_dir)/'watch-codex.json';fsio.write_json(status,{'last_success_at':'old','status':'warn'})
    original=status.read_bytes();fingerprint=cfg.fingerprint()
    result=selected(shelf,cfg,max_source_bytes=watchers.DEFAULT_MAX_SOURCE_BYTES+1)
    assert result['counts']['captured']==1 and len(shelf.list_records())==1
    assert status.read_bytes()==original and cfg.fingerprint()==fingerprint
    assert set(shelf.index())=={'codex:'+SID}
    assert (Path(shelf.status_dir)/'watch-codex-selected.json').is_file()


@pytest.mark.parametrize('ids',[[OTHER,'33333333-3333-3333-3333-333333333333'],[SID,SID],['../path'],[{}],[]])
def test_missing_or_invalid_selection_does_not_capture_anything(tmp_path,ids):
    shelf,cfg,source,other=fixture(tmp_path)
    result=watchers.run_source(shelf,cfg,source_ids=ids)
    assert result['counts']=={'failed':1} and shelf.list_records()==[]


def test_duplicate_filename_identity_is_refused(tmp_path):
    shelf,cfg,source,other=fixture(tmp_path);sub=cfg.root/'nested';sub.mkdir();(sub/source.name).write_bytes(source.read_bytes())
    assert selected(shelf,cfg)['status']=='source_selection_missing_or_ambiguous'
    assert shelf.list_records()==[]


def test_structural_identity_mismatch_or_missing_header_refused(tmp_path):
    for i,body in enumerate([codex_lines(OTHER,('human','wrong file identity')),json.dumps({'type':'compacted'})+'\n']):
        location=tmp_path/str(i);location.mkdir();shelf,cfg,source,other=fixture(location,body)
        assert selected(shelf,cfg)['counts']=={'failed':1,'source_identity_mismatch':1}
        assert shelf.list_records()==[]


def test_second_session_meta_cannot_change_identity(tmp_path):
    body=codex_lines(SID,('human','x'))+json.dumps({'type':'session_meta','payload':{'id':OTHER}})+'\n'
    shelf,cfg,source,other=fixture(tmp_path,body)
    assert selected(shelf,cfg)['counts']['source_identity_mismatch']==1 and shelf.list_records()==[]


def test_approval_and_exclusions_still_apply(tmp_path):
    shelf,cfg,source,other=fixture(tmp_path,never=('rollout-'+SID+'.jsonl',))
    assert selected(shelf,cfg)['counts']=={'excluded':1} and shelf.list_records()==[]
    approvals.approval_path(shelf,'codex').unlink()
    assert selected(shelf,cfg)['status']=='not_approved' and shelf.list_records()==[]


def test_raised_ceiling_no_longer_needs_bounded_codex_opt_in_but_limits_still_need_selection(tmp_path):
    # Size stopped being a memory guard on 17 Oct (streaming capture); CodexLimits remain a selected-refresh tool only.
    shelf,cfg,source,other=fixture(tmp_path)
    assert watchers.run_source(shelf,cfg,max_source_bytes=watchers.DEFAULT_MAX_SOURCE_BYTES+1,codex_limits=LIMITS)['status']=='bounded_codex_selection_required'
    assert shelf.list_records()==[]
    assert watchers.run_source(shelf,cfg,max_source_bytes=watchers.DEFAULT_MAX_SOURCE_BYTES+1)['status']=='ok'
    assert len(shelf.list_records())==2


def test_budget_hold_preserves_existing_record_state_and_retries(tmp_path):
    shelf,cfg,source,other=fixture(tmp_path);assert selected(shelf,cfg)['counts']['captured']==1
    path=watchers._state_path(shelf,'codex');before=fsio.read_json(path)['files'];old=next(iter(before.values()))
    assert selected(shelf,cfg,codex_limits=dataclasses.replace(LIMITS,retained_bytes=1))['counts']['source_text_limit']==1
    held=next(iter(fsio.read_json(path)['files'].values()))
    for field in ('record','normalizer','sha256','size','mtime_ns'):assert held[field]==old[field]
    assert len(shelf.list_records())==1
    retry=selected(shelf,cfg)
    assert sum(retry['counts'].get(k,0) for k in codes.OK_OUTCOMES)==1
    assert ledger.report(shelf)['missing']==0


def test_growth_after_discovery_fails_stability_without_stage(tmp_path,monkeypatch):
    shelf,cfg,source,other=fixture(tmp_path);real=render.hash_file
    def grow(path):
        result=real(path)
        with source.open('a') as f:f.write(json.dumps({'type':'compacted','payload':{'text':'new'}})+'\n')
        return result
    monkeypatch.setattr(watchers.render,'hash_file',grow);monkeypatch.setattr(shelf,'stage',lambda *_:pytest.fail('unstable staged'))
    assert selected(shelf,cfg)['counts']['unstable']==1 and shelf.list_records()==[]


def test_tool_heavy_generator_has_measured_bounded_peak_for_synthetic_fixture():
    raw=json.dumps({'type':'compacted','payload':{'text':'x'*2048}}).encode()+b'\n'
    tracemalloc.start()
    p=coverage.parse_with_coverage('codex',caps.checked_codex_lines((raw for _ in range(1000)),LIMITS),watchers.PARSERS)
    current,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    assert p.omitted['line:compacted']==1000 and peak<8<<20


def test_handoff_parts_and_unicode_line_byte_units():
    row={'type':'response_item','payload':{'type':'agent_message','content':[{'type':'text','text':'x'},{'type':'text','text':'y'}]}}
    with pytest.raises(caps.ResourceLimit,match='source_parts_limit'):
        list(caps.checked_codex_lines(jl([row]),dataclasses.replace(LIMITS,parts_per_message=1)))
    raw=json.dumps({'type':'compacted','text':'雪'*30},ensure_ascii=False)+'\n'
    assert len(raw)<len(raw.encode())
    with pytest.raises(caps.ResourceLimit,match='source_line_limit'):
        list(caps.checked_codex_lines([raw],dataclasses.replace(LIMITS,line_bytes=len(raw))))


def test_cli_routes_exact_selector_limits_and_refuses_failure(tmp_path):
    from core.memory_shelf import cli
    shelf,cfg,source,other=fixture(tmp_path)
    config=tmp_path/'config.json';config.write_text(json.dumps({'schema_version':1,'shelf_root':str(shelf.root),
        'headroom':{'min_free_bytes':0},'sources':{'codex':{'kind':'codex','root':str(cfg.root),'never_copy':[]}}}))
    out=io.StringIO()
    args=['--config',str(config),'watch','codex','--id',SID,'--bounded-codex','--max-source-bytes',str(watchers.DEFAULT_MAX_SOURCE_BYTES+1)]
    assert cli.main(args,out=out)==0 and 'selected_only' in out.getvalue()
    assert len(shelf.list_records())==1
    args[args.index(SID)]='../path';out=io.StringIO()
    assert cli.main(args,out=out)==cli.REFUSED and 'source_selection_invalid' in out.getvalue()


@pytest.mark.parametrize('row',[
    {'type':'z'*1000,'payload':None},
    {'type':'event_msg','payload':{'type':{'nested':'z'*1000}}},
    {'type':'compacted','timestamp':'z'*1000},
    {'type':'compacted','timestamp':{'nested':list(range(50))}},
])
def test_retained_skip_labels_and_timestamps_cannot_escape_budget(row):
    with pytest.raises(caps.ResourceLimit,match='source_text_limit'):
        list(caps.checked_codex_lines(jl([row]),dataclasses.replace(LIMITS,retained_bytes=100)))
