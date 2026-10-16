import json
import os
from pathlib import Path
import pytest
from core.memory_ask import regression, runner
from tests.memory_ask.test_runner import suite, result, execute, setup


def detailed(root, rid):
    return runner.load_run(root, rid, include_details=True)


def test_history_dates_integrity_and_default_shape(setup):
    first=execute(suite(),setup);second=execute(suite(),setup)
    root,_=setup
    rows=regression.history(root,suite_id='synthetic')
    assert len(rows)==2 and all(r['started_at'] and r['finished_at'] for r in rows)
    assert all(r['counts']['needs_review']==1 for r in rows)
    assert set(runner.load_run(root,first['run_id']))=={'manifest','report'}
    (root/second['run_id']/'report.txt').write_text('changed')
    rows=regression.history(root)
    assert sum(r['state']=='incomplete_or_invalid' for r in rows)==1


def test_report_discloses_question_evidence_model_and_no_acceptance(setup):
    out=execute(suite(),setup);root,_=setup
    before={p.name:p.read_bytes() for p in (root/out['run_id']).iterdir()}
    text=regression.render_report(detailed(root,out['run_id']))
    assert 'What was decided?' in text and 'A synthetic retained decision.' in text
    assert 'fixture@v1' in text and 'No generated answer' in text
    assert 'needs_review' in text and 'Definition SHA256' in text
    assert before=={p.name:p.read_bytes() for p in (root/out['run_id']).iterdir()}


def test_revision_added_removed_not_false_outcome_regression(setup):
    a=execute(suite(),setup);root,_=setup
    doc=json.loads(suite(question='Revised wording?'))
    extra=dict(doc['cases'][0],case_id='new-case')
    doc['cases'].append(extra);doc['version']='v2'
    b=execute(json.dumps(doc).encode(),setup)
    diff=regression.compare_cases(detailed(root,a['run_id']),detailed(root,b['run_id']))
    assert [c['definition_change'] for c in diff['cases']]==['revised','added']
    assert all(c['outcome_changed'] is None for c in diff['cases'])
    assert not diff['comparison']['comparable']
    reverse=regression.compare_cases(detailed(root,b['run_id']),detailed(root,a['run_id']))
    assert reverse['cases'][1]['definition_change']=='removed_from_suite'


def test_unchanged_case_and_cross_suite_refusal(setup):
    a=execute(suite(),setup);b=execute(suite(),setup);root,_=setup
    first=detailed(root,a['run_id']);second=detailed(root,b['run_id'])
    row=regression.compare_cases(first,second)['cases'][0]
    assert row['definition_change']=='unchanged' and row['outcome_changed'] is False
    assert row['evidence_changed'] is False
    changed=second['cases'][0]['evidence_block'].replace('A synthetic retained decision.', 'Different evidence.')
    second['cases'][0]['evidence_block']=changed
    assert regression.compare_cases(first,second)['cases'][0]['evidence_changed'] is True
    second['suite']['suite_id']='different'
    with pytest.raises(runner.Refused):regression.compare_cases(first,second)


def test_symlink_root_refused_and_incomplete_visible(setup,tmp_path):
    root,_=setup
    link=tmp_path/'link';link.symlink_to(root,target_is_directory=True)
    with pytest.raises(OSError):regression.history(link)
    (root/('r-'+'a'*32)).mkdir(mode=0o700)
    assert regression.history(root)[0]['state']=='incomplete_or_invalid'


def test_catalog_has_no_implied_completed_manual_checks():
    path=Path(__file__).parents[2]/'scripts/mc/regression-catalog.json'
    catalog=json.loads(path.read_text())
    assert len(catalog['suites'])==7 and catalog['registered_on']=='2026-10-15'
    assert all(s['implementation'] in ('existing_runner','manual_checks_defined') for s in catalog['suites'])
    assert catalog['cadence'].endswith('not enabled')


def test_malformed_evidence_is_unknown_and_history_is_bounded(setup,monkeypatch):
    a=execute(suite(),setup);root,_=setup
    run=detailed(root,a['run_id']);run['cases'][0]['evidence_block']={'bad':'shape'}
    assert regression.evidence_signature(run['cases'][0]) is None
    assert 'No retrieved evidence retained' in regression.render_report(run)
    monkeypatch.setattr(regression,'MAX_HISTORY',0)
    with pytest.raises(runner.Refused,match='history_limit'):regression.history(root)
