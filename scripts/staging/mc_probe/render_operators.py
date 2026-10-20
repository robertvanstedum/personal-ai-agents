"""Render reviewed dev exam operators into a new private, persistent directory.

Rendering compiles but never executes the generated scripts. Configuration contains
paths and admission parameters, never credentials or answer keys. Review the rendered
manifest before invoking window.py; the result is an operator tool, not unattended CI.
"""
import argparse
from datetime import date
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re

PATH_KEYS = ('state_root', 'repo_root', 'python', 'brief', 'voice_probe', 'model_receipt',
             'usage_file', 'source_snapshot', 'source_manifest')
NUMBER_KEYS = ('base_calls', 'base_micro', 'window_micro', 'max_new_calls',
               'input_rate', 'output_rate')
OTHER_KEYS = ('day', 'run_id', 'expected_model', 'key_ref', 'test_image', 'revisions', 'fixtures', 'original_guard_sha256')


def validate(config):
    if not isinstance(config, dict) or set(config) != set(PATH_KEYS + NUMBER_KEYS + OTHER_KEYS):
        raise ValueError('exact_config_fields_required_no_credentials')
    for key in PATH_KEYS:
        value = config[key]
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError('absolute_path_required:' + key)
        if any(word in value.lower() for word in ('sealed', 'answer-key', 'answer_key')):
            raise ValueError('answer_keys_must_remain_outside_operator_scope')
    for key in NUMBER_KEYS:
        if type(config[key]) is not int or config[key] < 0:
            raise ValueError('nonnegative_integer_required:' + key)
    if not 0 < config['window_micro'] <= 25_000_000 - config['base_micro']:
        raise ValueError('daily_cap_exceeded')
    if not 1 <= config['max_new_calls'] <= 100:
        raise ValueError('bounded_call_count_required')
    if not 1 <= config['input_rate'] <= 1000 or not 1 <= config['output_rate'] <= 1000:
        raise ValueError('reviewed_positive_microdollar_rates_required')
    if not isinstance(config['day'], str) or date.fromisoformat(config['day']).isoformat() != config['day']:
        raise ValueError('iso_day_required')
    if not isinstance(config['run_id'], str) or not re.fullmatch(r'[a-z][a-z0-9-]{1,40}', config['run_id']):
        raise ValueError('safe_distinct_run_id_required')
    if not isinstance(config['expected_model'], str) or not re.fullmatch(r'anthropic/[a-zA-Z0-9._-]+', config['expected_model']):
        raise ValueError('reviewed_anthropic_model_required')
    if not isinstance(config['key_ref'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', config['key_ref']):
        raise ValueError('usage_key_reference_required_not_key_value')
    if not isinstance(config['test_image'], str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', config['test_image']):
        raise ValueError('immutable_local_test_image_required')
    if not isinstance(config['original_guard_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', config['original_guard_sha256']):
        raise ValueError('reviewed_original_guard_hash_required')
    revisions = config['revisions']
    if not isinstance(revisions, dict) or not revisions or any(
        not isinstance(k, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', k)
        or not isinstance(v, str) or not re.fullmatch(r'[a-f0-9]{40}', v)
        for k, v in revisions.items()
    ):
        raise ValueError('pinned_full_revision_map_required')
    fixtures = config['fixtures']
    if not isinstance(fixtures, dict):
        raise ValueError('fixture_map_required')
    for revision, paths in fixtures.items():
        if not isinstance(revision, str) or not isinstance(paths, dict):
            raise ValueError('invalid_fixture_map')
        for name, spec in paths.items():
            if not isinstance(name, str) or not isinstance(spec, dict) or set(spec) != {'file', 'sha256'}:
                raise ValueError('invalid_fixture_spec')
            if not isinstance(spec['file'], str) or not Path(spec['file']).is_absolute():
                raise ValueError('absolute_fixture_required')
            if any(word in (revision + name + spec['file']).lower() for word in ('sealed', 'answer-key', 'answer_key')):
                raise ValueError('sealed_fixture_forbidden')
            if not isinstance(spec['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', spec['sha256']):
                raise ValueError('frozen_fixture_hash_required')
    return config


def render(config, destination):
    config = validate(config)
    dest = Path(destination).absolute()
    if dest.exists() or dest.is_symlink():
        raise ValueError('new_output_directory_required')
    if not dest.parent.is_dir():
        raise ValueError('existing_private_parent_required')
    repo = Path(config['repo_root']).resolve()
    if dest.resolve().is_relative_to(repo):
        raise ValueError('render_private_config_outside_repository')
    values = {key.upper(): repr(config[key]) for key in PATH_KEYS + NUMBER_KEYS + ('day', 'key_ref', 'test_image', 'revisions', 'fixtures', 'expected_model')}
    values.update({
        'RUN_ID': config['run_id'],
        'RUN_TAG': config['run_id'].replace('-', '_').upper(),
        'RUN_SQL': config['run_id'].replace('-', '_'),
        'MODEL_NAME': config['expected_model'],
        'DAY_TEXT': config['day'],
        'MAX_CALLS': str(config['base_calls'] + config['max_new_calls']),
        'WINDOW_USD': repr(str(Decimal(config['window_micro']) / 1_000_000)),
    })
    templates = Path(__file__).with_name('operator_templates')
    rendered = {}
    source_hashes = {}
    for template in sorted(templates.glob('*.py.tmpl')):
        source = template.read_text()
        def replace(match):
            key = match.group(1)
            if key not in values:
                raise ValueError('unknown_template_field:' + key)
            return values[key]
        output = re.sub(r'@@([A-Z_]+)@@', replace, source)
        compile(output, str(template), 'exec')
        rendered[template.name.removesuffix('.tmpl')] = output.encode()
        source_hashes[template.name] = hashlib.sha256(source.encode()).hexdigest()
    if len(rendered) != 5:
        raise ValueError('five_operator_templates_required')
    dest.mkdir(mode=0o700)
    manifest = {'schema': 1, 'run_id': config['run_id'], 'scripts': {}, 'templates': source_hashes,
                'config_sha256': hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
                'executed': False, 'original_guard_sha256': config['original_guard_sha256']}
    for name, raw in rendered.items():
        fd = os.open(dest / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
        manifest['scripts'][name] = hashlib.sha256(raw).hexdigest()
    fd = os.open(dest / 'MANIFEST.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(manifest, stream, indent=2)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    render(json.loads(args.config.read_text()), args.out)
    print('Rendered five private operators and MANIFEST.json; nothing executed or armed.')
