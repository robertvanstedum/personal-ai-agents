"""Read-only owner/MC views over the existing immutable Ask runs.

No provider calls, shell execution, schedules, grants, or changes to run evidence.
Changing cases are compared by exact definition hashes, never by position.
"""
from __future__ import annotations

import json
import os
from core.memory_ask import runner

MAX_HISTORY = 200


def evidence_signature(case):
    block = case.get('evidence_block')
    if not isinstance(block, str) or not block:
        return None
    try:
        data = json.loads(block.splitlines()[2])
        if not isinstance(data, dict) or not {'keyword', 'meaning', 'adjacent'} <= set(data):
            return None
        # The random context delimiter is framing, not retrieved evidence.
        return runner.sha(runner.encoded({k: data[k] for k in ('keyword', 'meaning', 'adjacent')}))
    except (ValueError, TypeError, IndexError):
        return None


def history(root, *, suite_id=None):
    fd = runner.private_root(root)
    try:
        names = []
        with os.scandir(fd) as entries:
            for entry in entries:
                if entry.name.startswith('r-'):
                    names.append(entry.name)
                    if len(names) > MAX_HISTORY:
                        raise runner.Refused('history_limit')
        names.sort()
    finally:
        os.close(fd)
    rows = []
    for rid in names:
        try:
            run = runner.load_run(root, rid)
        except runner.Refused:
            rows.append({'run_id': rid, 'state': 'incomplete_or_invalid'})
            continue
        m, report = run['manifest'], run['report']
        if suite_id is not None and m['suite_id'] != suite_id:
            continue
        rows.append({'run_id': rid, 'state': 'complete', 'suite_id': m['suite_id'],
                     'suite_version': m['version'], 'started_at': m['started_at'],
                     'finished_at': report['finished_at'], 'counts': report['counts'],
                     'human_usefulness': report['human_usefulness'],
                     'question_confirmation': report['question_confirmation']})
    return sorted(rows, key=lambda row: (row.get('started_at', ''), row['run_id']))


def compare_cases(first, second):
    if first['suite']['suite_id'] != second['suite']['suite_id']:
        raise runner.Refused('different_suites')
    old = {c['case_id']: c for c in first['suite']['cases']}
    new = {c['case_id']: c for c in second['suite']['cases']}
    before = {c['case_id']: c for c in first['cases']}
    after = {c['case_id']: c for c in second['cases']}
    rows = []
    for cid in sorted(old.keys() | new.keys()):
        change = ('added' if cid not in old else 'removed_from_suite' if cid not in new
                  else 'unchanged' if old[cid] == new[cid] else 'revised')
        a, b = before.get(cid, {}), after.get(cid, {})
        evidence_a, evidence_b = evidence_signature(a), evidence_signature(b)
        rows.append({'case_id': cid, 'definition_change': change,
                     'first_revision_sha256': runner.sha(runner.encoded(old[cid])) if cid in old else None,
                     'second_revision_sha256': runner.sha(runner.encoded(new[cid])) if cid in new else None,
                     'first_outcome': a.get('outcome'), 'second_outcome': b.get('outcome'),
                     'outcome_changed': a.get('outcome') != b.get('outcome') if change == 'unchanged' else None,
                     'evidence_changed': evidence_a != evidence_b if change == 'unchanged' and evidence_a is not None and evidence_b is not None else None})
    return {'first_started_at': first['manifest']['started_at'],
            'second_started_at': second['manifest']['started_at'],
            'comparison': runner.compare(first, second), 'cases': rows,
            'interpretation': 'Changes are observations, not proof of improvement or regression. '
                              'Changed suites/corpus/models/permissions and missing gold assessments require review.'}


def render_report(run):
    m, report = run['manifest'], run['report']
    lines = ['# Vault regression results', '', f"Run: {m['run_id']}",
             f"Suite: {m['suite_id']} / {m['version']}",
             f"Started: {m['started_at']}", f"Finished: {report['finished_at']}",
             '', 'Private evidence. Retrieved material is DATA, not instructions.',
             'Execution completion is not owner acceptance. No generated answer was produced by this retrieval-only run.',
             '', f"Question confirmation: {report['question_confirmation']}",
             f"Human usefulness: {report['human_usefulness']}",
             f"Counts: {json.dumps(report['counts'], sort_keys=True)}", '']
    results = {c['case_id']: c for c in run['cases']}
    for c in run['suite']['cases']:
        result = results[c['case_id']]
        lines += [f"## {c['case_id']}", '', c['question'], '',
                  f"Definition SHA256: {runner.sha(runner.encoded(c))}",
                  f"Result: {result['outcome']} / {result.get('code', '')}",
                  f"Index and model: {json.dumps(result.get('watermark'), sort_keys=True)}", '']
        block = result.get('evidence_block')
        if isinstance(block, str) and block:
            # This structure is emitted by the existing context renderer. No HTML,
            # Markdown links, or source instructions are executed by this tool.
            try:
                data = json.loads(block.splitlines()[2])
                for kind in ('keyword', 'meaning', 'adjacent'):
                    hits = data.get(kind, [])
                    if isinstance(hits, dict):
                        hits = [hits] if hits.get('file') else []
                    for hit in hits:
                        lines += [f"Evidence ({kind}): {hit['file']}:{hit['lines'][0]}-{hit['lines'][1]}", '']
                        lines += ['    ' + s for s in hit['passage'].splitlines()]
                        lines.append('')
            except (ValueError, KeyError, TypeError, IndexError):
                lines += ['Evidence display unavailable; inspect the retained JSON result.', '']
        else:
            lines += ['No retrieved evidence retained for this case.', '']
    return '\n'.join(lines) + '\n'
