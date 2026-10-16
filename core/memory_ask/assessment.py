"""Private answer-assessment draft. No generation, providers, grants or schedules.

Reuses the existing integrity-checked saved-run reader. Imported answers are
operator-supplied evidence, NOT proof of actual delivery or owner acceptance.
The source run is never edited. CLI defaults to a zero-write plan.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import json
import os
import re
import stat
from core.memory_ask import citation, regression, runner

MAX_INPUT = 128 * 1024
SHA = re.compile(r'[0-9a-f]{64}\Z')
SCHEMA = 'minimoi.answer-import.v1'


def unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise runner.Refused('duplicate_field')
        out[key] = value
    return out


def parse(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_INPUT:
        raise runner.Refused('input_size')
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError):
        raise runner.Refused('invalid_json') from None


def private_input(path):
    """No symlink traversal, special files, oversized input or replacement races."""
    parent, leaf = os.path.split(os.path.abspath(os.fspath(path)))
    parent_fd = runner.private_root(parent)
    fd = None
    try:
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o600 or before.st_size > MAX_INPUT):
            raise runner.Refused('input_not_private')
        chunks, size = [], 0
        while True:
            chunk = os.read(fd, min(8192, MAX_INPUT + 1 - size))
            if not chunk:
                break
            chunks.append(chunk); size += len(chunk)
            if size > MAX_INPUT:
                raise runner.Refused('input_size')
        after = os.fstat(fd)
        named = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        key = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if key(before) != key(after) or key(after) != key(named):
            raise runner.Refused('input_changed')
        # The retained parent descriptor may now name a displaced directory.
        # Re-resolve the originally selected absolute chain without symlinks;
        # the pathname must still select both this parent and this exact file.
        fresh_parent_fd = runner.private_root(parent)
        try:
            original_parent, fresh_parent = os.fstat(parent_fd), os.fstat(fresh_parent_fd)
            fresh_named = os.stat(leaf, dir_fd=fresh_parent_fd, follow_symlinks=False)
            if ((original_parent.st_dev, original_parent.st_ino) !=
                    (fresh_parent.st_dev, fresh_parent.st_ino)
                    or key(before) != key(fresh_named)):
                raise runner.Refused('input_changed')
        finally:
            os.close(fresh_parent_fd)
        return b''.join(chunks)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


def binding(run, case_id):
    definitions = [c for c in run['suite']['cases'] if c['case_id'] == case_id]
    results = [c for c in run['cases'] if c['case_id'] == case_id]
    if len(definitions) != 1 or len(results) != 1:
        raise runner.Refused('case_not_found')
    evidence = regression.evidence_signature(results[0])
    if evidence is None:
        raise runner.Refused('evidence_unavailable')
    return {'run_id': run['manifest']['run_id'], 'case_id': case_id,
            # Hash of canonical loaded content, not a claim about raw manifest bytes.
            'run_snapshot_sha256': runner.sha(runner.encoded(run)),
            'case_sha256': runner.sha(runner.encoded(definitions[0])),
            'evidence_sha256': evidence}


def timestamp(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 80:
        raise runner.Refused('timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
    except ValueError:
        raise runner.Refused('timestamp') from None
    return value


def assess(raw, run):
    obj = parse(raw)
    keys = {'schema', 'run_id', 'case_id', 'run_snapshot_sha256', 'case_sha256',
            'evidence_sha256', 'answer', 'produced_at', 'producer', 'transport_receipt_sha256'}
    runner.fields(obj, keys)
    if obj['schema'] != SCHEMA:
        raise runner.Refused('schema')
    expected = binding(run, obj['case_id'])
    if any(obj[k] != value for k, value in expected.items()):
        raise runner.Refused('binding_mismatch')
    answer = obj['answer']
    if (not isinstance(answer, str) or not answer.strip()
            or len(answer.encode('utf-8')) > 32 * 1024
            or any(ord(c) < 32 and c not in '\n\r\t' for c in answer)):
        raise runner.Refused('answer_invalid')
    timestamp(obj['produced_at'])
    producer = obj['producer']
    runner.fields(producer, {'backend', 'model', 'source_version'})
    for value in producer.values():
        if value is not None:
            runner.string(value, 200)
    receipt = obj['transport_receipt_sha256']
    if receipt is not None and (not isinstance(receipt, str) or not SHA.fullmatch(receipt)):
        raise runner.Refused('receipt_hash')
    saved = next(c for c in run['cases'] if c['case_id'] == obj['case_id'])
    checks = citation.check(answer, saved['retained_citations'])
    counts = {name: sum(c['state'] == name for c in checks)
              for name in ('supported', 'invalid_citation', 'unsupported', 'exempt')}
    definitions = next(c for c in run['suite']['cases'] if c['case_id'] == obj['case_id'])
    evidence = json.loads(saved['evidence_block'].splitlines()[2])
    hits = evidence['keyword'] + evidence['meaning']
    if evidence['adjacent'].get('file'):
        hits.append(evidence['adjacent'])
    provenance = [{key: hit.get(key) for key in ('file', 'lines', 'record_id', 'edition',
                    'speaker', 'attribution', 'turn_kind', 'date', 'provenance')} for hit in hits]
    return {'schema': 'minimoi.answer-assessment.v1', **expected,
            'input_sha256': runner.sha(raw), 'answer_sha256': runner.sha(answer.encode('utf-8')),
            'answer': answer, 'produced_at': obj['produced_at'], 'producer': producer,
            'transport_receipt_sha256': receipt,
            'delivery_verification': 'not_verified_operator_import',
            'retrieval_outcome': saved['outcome'], 'citation_range_counts': counts,
            'citation_range_verdict': 'fail' if counts['invalid_citation'] or counts['unsupported'] else
                'needs_review' if not counts['supported'] else 'ranges_retained',
            'claim_support': 'needs_review', 'decision_chronology': 'needs_review',
            'owner_usefulness': 'needs_review', 'owner_acceptance': 'not_recorded',
            'question_confirmation': run['report']['question_confirmation'],
            'dates': {'run_started_at': run['manifest']['started_at'],
                      'answer_produced_at': obj['produced_at'], 'source_decision_time': None,
                      'source_decision_time_basis': 'not_selected_or_verified',
                      'requested_window': definitions['window']},
            'source_provenance': provenance,
            'source_provenance_state': 'retained_run_metadata_not_live_revalidated',
            'source_context_sha256': runner.sha(saved['evidence_block'].encode('utf-8')),
            'writes_to_source_run': 0, 'provider_calls': 0}


def render(value):
    return ('Answer review — private operator import\n'
            f"Run/case: {value['run_id']} / {value['case_id']}\n"
            f"Retrieval: {value['retrieval_outcome']}\n"
            f"Citation ranges: {value['citation_range_verdict']} {value['citation_range_counts']}\n"
            'Range containment does not establish semantic support.\n'
            'Delivery unverified; claim support, chronology and owner usefulness need review.\n'
            'Source decision date unknown; run/import dates are not decision dates.\n\n'
            + value['answer'] + '\n')


def publish(value, output_root, *, source_runs_root):
    """Explicit exclusive sidecar only, never placed inside a completed run."""
    if os.path.commonpath((os.path.realpath(output_root), os.path.realpath(source_runs_root))) == os.path.realpath(source_runs_root):
        raise runner.Refused('separate_output_required')
    fd = runner.private_root(output_root)
    try:
        name = 'a-' + runner.sha(runner.encoded(value)) + '.json'
        runner.publish(fd, name, value)
        return name
    finally:
        os.close(fd)


def template(run, case_id):
    """Bound schema scaffold only; never manufacture answer, date or producer."""
    return {'schema': SCHEMA, **binding(run, case_id), 'answer': '',
            'produced_at': None,
            'producer': {'backend': None, 'model': None, 'source_version': None},
            'transport_receipt_sha256': None}


def load_assessment(path, run):
    """Verify a hash-named sidecar against current saved-run evidence, read only.

    The original import-byte hash is retained provenance, not independently
    reconstructed or a transport-delivery attestation. All derived fields are
    recomputed from the retained answer and exact run before rendering.
    """
    raw = private_input(path)
    value = parse(raw)
    if not isinstance(value, dict):
        raise runner.Refused('assessment_schema')
    name = os.path.basename(os.fspath(path))
    expected_name = 'a-' + runner.sha(runner.encoded(value)) + '.json'
    if raw != runner.encoded(value) or name != expected_name:
        raise runner.Refused('assessment_hash_mismatch')
    fields = ('run_id', 'case_id', 'run_snapshot_sha256', 'case_sha256',
              'evidence_sha256', 'answer', 'produced_at', 'producer',
              'transport_receipt_sha256')
    try:
        imported = {'schema': SCHEMA, **{key: value[key] for key in fields}}
        recomputed = assess(runner.encoded(imported), run)
    except KeyError:
        raise runner.Refused('assessment_schema') from None
    original_input_hash = value.get('input_sha256')
    if not isinstance(original_input_hash, str) or not SHA.fullmatch(original_input_hash):
        raise runner.Refused('assessment_input_hash')
    recomputed['input_sha256'] = original_input_hash
    if value != recomputed:
        raise runner.Refused('assessment_derived_fields_mismatch')
    return value


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs', required=True); p.add_argument('--run-id', required=True)
    p.add_argument('--case-id', required=True)
    inputs = p.add_mutually_exclusive_group()
    inputs.add_argument('--answer-import')
    inputs.add_argument('--assessment', help='Verify and render an existing hash-named sidecar; read only')
    inputs.add_argument('--template', action='store_true', help='Emit exact bound import schema; answer must be supplied separately')
    p.add_argument('--output'); p.add_argument('--execute', action='store_true')
    a = p.parse_args(argv)
    try:
        run = runner.load_run(a.runs, a.run_id, include_details=True)
        if a.assessment or a.template:
            if a.execute or a.output:
                raise runner.Refused('read_only_operation')
            if a.template:
                print(json.dumps(template(run, a.case_id), indent=2))
            else:
                value = load_assessment(a.assessment, run)
                if value['case_id'] != a.case_id:
                    raise runner.Refused('case_argument_mismatch')
                print(render(value))
            return 0
        if not a.answer_import:
            if a.execute:
                raise runner.Refused('answer_import_required')
            print(json.dumps({'operation': 'plan', 'binding': binding(run, a.case_id),
                              'provider_calls': 0, 'writes': 0}, indent=2))
            return 0
        value = assess(private_input(a.answer_import), run)
        if value['case_id'] != a.case_id:
            raise runner.Refused('case_argument_mismatch')
        if a.execute:
            if not a.output or os.path.commonpath((os.path.realpath(a.output), os.path.realpath(a.runs))) == os.path.realpath(a.runs):
                raise runner.Refused('separate_output_required')
            print(json.dumps({'sidecar': publish(value, a.output, source_runs_root=a.runs), 'provider_calls': 0}))
        else:
            print(render(value))
        return 0
    except (runner.Refused, OSError, ValueError, KeyError, TypeError):
        print(json.dumps({'error': 'assessment_refused', 'provider_calls': 0}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
