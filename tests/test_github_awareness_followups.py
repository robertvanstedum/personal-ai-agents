"""GitHub awareness follow-ups on the reviewed draft: busy-repository streams, deployment
statuses, health/rebind operator steps, the context bound, and typed + voice context.

Synthetic clients and fixture files only; no network, no provider, no live cache.
"""
import ast
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core import github_awareness as C
from minimoi_portal.guild_ui import github_awareness as H

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 17, 15, tzinfo=timezone.utc)


def state():
    return {'format': 'minimoi.github-observations/1', 'repo': C.REPO, 'writer_domain': 'fixture', 'generation': 0,
            'last_attempt': None,
            'streams': {n: {'cursor': C.iso(NOW - timedelta(hours=24)), 'last_success': None, 'last_attempt': None,
                            'health': 'never_synced', 'retry_at': None, 'items': [], 'evicted': 0,
                            'gap': 'initial_window',
                            'coverage': 'rolling_window' if n in {'events', 'runs', 'deployments'}
                            else 'updated_since_cursor'} for n in C.STREAMS}}


def row(n, minutes_ago=1):
    return {'id': n, 'title': f'Item {n}', 'updated_at': C.iso(NOW - timedelta(minutes=minutes_ago)),
            'html_url': f'https://github.com/{C.REPO}/actions/runs/{n}', 'body': 'untrusted'}


class Client:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def get(self, path, params):
        self.calls.append((path, params))
        return self.handler(path, params)


def busy(path, params):
    """This repository today: >300 events and >600 workflow runs, every page full."""
    full = [row(n) for n in range(1, params['per_page'] + 1)]
    if path.endswith('/actions/runs'):
        return 200, {'workflow_runs': full}, {}
    if path.endswith('/events'):
        return 200, [{**r, 'type': 'PushEvent', 'created_at': r['updated_at']} for r in full], {}
    return 200, [], {}


def only(stream):
    """A poll state where only `stream` is due, so one call shows its behaviour."""
    doc = state()
    for name, r in doc['streams'].items():
        if name != stream:
            r['retry_at'] = C.iso(NOW + timedelta(hours=1))
    return doc


# ── busy rolling-window streams ───────────────────────────────────────────────

@pytest.mark.parametrize('stream', ['runs', 'events'])
def test_busy_rolling_stream_reads_one_page_and_succeeds(stream):
    client = Client(busy)
    out = C.poll(only(stream), client, now=NOW)
    r = out['streams'][stream]
    assert len(client.calls) == 1 and client.calls[0][1]['page'] == 1
    assert r['health'] == 'ok' and r['last_success'] == C.iso(NOW) and len(r['items']) == (25 if stream == 'runs' else 100)


def test_all_streams_reach_a_success_within_the_anonymous_budget_on_a_busy_repo():
    doc, total = state(), 0
    for poll in range(3):                              # 3 polls x 3 requests covers 7 streams
        client = Client(busy)
        doc = C.poll(doc, client, now=NOW + timedelta(minutes=5 * poll))
        total += len(client.calls)
        assert len(client.calls) <= 3
    assert all(r['last_success'] is not None for r in doc['streams'].values()), \
        {n: r['health'] for n, r in doc['streams'].items()}


def test_full_newest_page_after_the_last_success_flags_a_possible_gap():
    doc = only('runs')
    doc['streams']['runs']['last_success'] = C.iso(NOW - timedelta(minutes=10))
    out = C.poll(doc, Client(busy), now=NOW)          # all 100 runs updated 1 minute ago
    assert out['streams']['runs']['health'] == 'ok' and out['streams']['runs']['gap'] == 'possible_poll_gap'


def test_a_partial_newest_page_keeps_the_initial_window_label():
    doc = only('runs')
    doc['streams']['runs']['last_success'] = C.iso(NOW - timedelta(minutes=10))
    out = C.poll(doc, Client(lambda p, q: (200, {'workflow_runs': [row(1)]}, {})), now=NOW)
    assert out['streams']['runs']['gap'] == 'initial_window'


# ── deployment statuses ───────────────────────────────────────────────────────

def test_deployment_statuses_are_fetched_only_for_deployments_changed_since_the_cursor():
    def response(path, params):
        if path.endswith('/deployments'):
            return 200, [{'id': 10, 'updated_at': C.iso(NOW), 'created_at': C.iso(NOW)},
                         {'id': 11, 'updated_at': C.iso(NOW - timedelta(days=5)),
                          'created_at': C.iso(NOW - timedelta(days=5))}], {}
        if path.endswith('/statuses'):
            return 200, [{'id': 7, 'state': 'success', 'created_at': C.iso(NOW), 'description': 'd'}], {}
        return 200, [], {}
    client = Client(response)
    out = C.poll(only('deployments'), client, now=NOW, request_limit=40)
    assert [p for p, _ in client.calls if p.endswith('/statuses')] == [C.PREFIX + '/deployments/10/statuses']
    assert out['streams']['deployments']['health'] == 'ok'


# ── health and rebind (real files; only fixture admission and kernel identity substituted) ──

@pytest.fixture
def cache(tmp_path, monkeypatch):
    root = tmp_path / 'cache'
    root.mkdir(mode=0o700)
    monkeypatch.setattr(C, 'root_check', lambda p: Path(p))
    monkeypatch.setattr(C.files, 'writer_domain', lambda: 'boot-1')
    monkeypatch.setattr(C.shutil, 'disk_usage', lambda p: type('Space', (), {'free': 6 << 30})())
    doc = state()
    doc['writer_domain'] = 'boot-1'
    C.files.atomic(root / 'state.json', C.encoded(doc), exclusive=True)
    return root


def test_health_reads_without_network_or_write(cache):
    before = (cache / 'state.json').read_bytes()
    report = C.health(cache, now=NOW)
    assert report['writer_bound_to_this_boot'] is True
    assert set(report['streams']) == set(C.STREAMS)
    assert all(r['health'] == 'never_synced' and r['age_seconds'] is None for r in report['streams'].values())
    assert (cache / 'state.json').read_bytes() == before


def test_rebind_after_a_restart_resumes_writes_and_marks_every_stream_gapped(cache, monkeypatch):
    with pytest.raises(C.Refused, match='writer_domain_unchanged'):
        C.rebind(cache, now=NOW)
    monkeypatch.setattr(C.files, 'writer_domain', lambda: 'boot-2')          # the kernel restarted
    assert C.health(cache, now=NOW)['writer_bound_to_this_boot'] is False
    with pytest.raises(C.Refused, match='writer_domain_changed'):
        C.sync(cache, Client(busy), now=NOW)
    doc = C.rebind(cache, now=NOW)
    assert doc['writer_domain'] == 'boot-2' and doc['generation'] == 1
    assert all(r['gap'] == 'possible_poll_gap' for r in doc['streams'].values())
    assert C.load(cache) == doc
    assert C.sync(cache, Client(busy), now=NOW)['generation'] == 2


def test_a_second_writer_is_refused_while_the_lock_is_held(cache):
    import fcntl
    fd = os.open(cache / 'sync.lock', os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(C.Refused, match='sync_busy'):
            C.sync(cache, Client(busy), now=NOW)
    finally:
        os.close(fd)


# ── the context bound ─────────────────────────────────────────────────────────

def test_large_observations_shrink_but_stream_health_stays_visible():
    doc = state()
    big = '\\' * 1000                                # each backslash doubles in JSON
    doc['streams']['issues']['items'] = [
        C.project('issues', {'id': n, 'title': big, 'updated_at': C.iso(NOW - timedelta(minutes=n)),
                             'html_url': f'https://github.com/{C.REPO}/issues/{n}' + big, 'body': big}, NOW)
        for n in range(1, 30)]
    env = {'MINIMOI_MC_GITHUB_AWARENESS': '1', 'MINIMOI_MC_GITHUB_AWARENESS_OWNER': 'robert'}
    block = H.block('robert', {'principal': 'robert', 'record_mode': 'on_record'}, environ=env, now=NOW,
                    loader=lambda root: doc)
    assert 'cache unavailable' not in block
    payload = json.loads(block.split('\n')[2])
    assert len(block) < 13000 and set(payload['stream_status']) == set(C.STREAMS)
    assert 0 < len(payload['recent_observations']) < 8          # the bound really applied
    assert payload['omitted_cached_items'] == 29 - len(payload['recent_observations'])


# ── typed and voice turns both reach the default context ──────────────────────

class Store:
    def __init__(self, conv):
        self.conv = conv

    def get(self, cid, principal):
        return dict(self.conv)


CONV = {'id': 'c1', 'principal': 'robert', 'notes_floor': 'floor-1'}


@pytest.fixture
def api(monkeypatch):
    from minimoi_portal.guild_ui import api as module
    return module


def test_voice_turn_rereads_the_kept_owner_conversation(api, monkeypatch):
    monkeypatch.setattr(api, '_conversations', lambda: Store(CONV))
    fresh = api._fresh_context_conversation(CONV, 'robert', True)
    assert fresh['record_mode'] == 'on_record' and fresh['principal'] == 'robert'


@pytest.mark.parametrize('stored', [
    {**CONV, 'principal': 'someone-else'}, {**CONV, 'archived': True}, {**CONV, 'record_mode': 'off_record'},
    {**CONV, 'notes_floor': 'another-floor'},
])
def test_voice_turn_refuses_a_conversation_out_of_scope(api, monkeypatch, stored):
    monkeypatch.setattr(api, '_conversations', lambda: Store(stored))
    assert api._fresh_context_conversation(CONV, 'robert', True) is None


def test_voice_turn_refuses_when_the_store_fails(api, monkeypatch):
    def broken():
        raise RuntimeError('store down')
    monkeypatch.setattr(api, '_conversations', broken)
    assert api._fresh_context_conversation(CONV, 'robert', True) is None


def test_typed_turn_keeps_the_request_attestation(api, monkeypatch):
    seen = []
    monkeypatch.setattr(api, '_fresh_kept_owner_conversation', lambda conv, principal: seen.append(principal) or 'x')
    monkeypatch.setattr(api, '_conversations', lambda: pytest.fail('typed turns use the request attestation'))
    assert api._fresh_context_conversation(CONV, 'robert', False) == 'x' and seen == ['robert']


def test_the_context_hook_passes_the_voice_flag_and_reads_no_network():
    tree = ast.parse((ROOT / 'minimoi_portal/guild_ui/api.py').read_text())
    hook = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_with_context')
    calls = [n for n in ast.walk(hook) if isinstance(n, ast.Call)
             and getattr(n.func, 'id', '') == '_fresh_context_conversation']
    assert len(calls) == 2 and all([getattr(a, 'id', None) for a in call.args][-1] == 'current_voice' for call in calls)
    host = (ROOT / 'minimoi_portal/guild_ui/github_awareness.py').read_text()
    assert 'PublicClient' not in host and 'urllib' not in host and 'requests' not in host


# ── the operator CLI: invocation and one writer admission per run ─────────────

import importlib.util
import subprocess
import sys

CLI_PATH = ROOT / 'scripts/mc/github_awareness.py'
PROPOSED_JOB = {'id': 'mc-github-awareness', 'name': 'MC GitHub awareness poll', 'host': 'ec2',
                'schedule': {'text': 'every 5 minutes', 'kind': 'interval', 'every_s': 300},
                'expected_every_s': 300, 'grace_s': 900, 'missed_after_s': 3600, 'stuck_after_s': 300,
                'status_file': 'mc-github-awareness.json', 'owner': 'robert',
                'runbook': 'OPERATIONS.md#scheduled-jobs-and-launchd-on-the-mac'}


@pytest.mark.parametrize('args,code', [([], 0), (['--health'], 1)])
def test_cli_runs_by_path_without_pythonpath_from_any_directory(tmp_path, args, code):
    env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
    result = subprocess.run([sys.executable, str(CLI_PATH), *args], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=60)
    assert 'ModuleNotFoundError' not in result.stderr, result.stderr
    assert result.returncode == code, result.stdout + result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert (out['plan_only'] is True and out['writes'] == 0) if not args else out == {
        'state': 'held', 'error_class': 'FileNotFoundError'}            # no cache here: refused, nothing written


@pytest.fixture
def cli(cache, tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('github_awareness_cli', CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from core.jobs import registry
    job = registry.parse({'schema_version': 1, 'jobs': [PROPOSED_JOB]}).get('mc-github-awareness')
    jobs_root = tmp_path / 'jobs'
    jobs_root.mkdir(mode=0o700)
    monkeypatch.setattr(module, 'ROOT', str(cache))
    monkeypatch.setattr(module, '_registered_job', lambda: job)
    monkeypatch.setattr(module, 'PublicClient', lambda token=None: Client(busy))
    monkeypatch.setenv('MINIMOI_JOBS_ROOT', str(jobs_root))
    module.job, module.status_path = job, jobs_root / PROPOSED_JOB['status_file']
    return module


def test_a_competitor_refused_at_admission_leaves_the_active_receipt_unchanged(cli, cache):
    from core.jobs import status
    with C.Admission(cache):                                     # the active run holds the writer admission...
        status.start_run(os.environ['MINIMOI_JOBS_ROOT'], cli.job, 'active-run', NOW)
        active = cli.status_path.read_bytes()                    # ...and has written its running receipt
        with pytest.raises(C.Refused, match='sync_busy'):
            cli._poll(None, NOW)                                 # the competitor (real status writer, real lock)
        assert cli.status_path.read_bytes() == active
    assert json.loads(active)['run_id'] == 'active-run' and json.loads(active)['state'] == 'running'


def test_one_admitted_run_writes_its_start_and_finish(cli):
    out = cli._poll(None, NOW)
    assert out['job_status_written'] is True
    receipt = json.loads(cli.status_path.read_text())
    assert receipt['state'] in ('ok', 'warn') and receipt['finished_at'] is not None
    assert set(receipt['results']) == set(C.STREAMS)


def test_a_run_that_is_not_due_writes_no_status(cli):
    cli._poll(None, NOW)
    before = cli.status_path.read_bytes()
    assert cli._poll(None, NOW + timedelta(seconds=60)) == {'state': 'not_due', 'job_status_written': False,
                                                            'provider_calls': 0}
    assert cli.status_path.read_bytes() == before


def test_every_documented_command_runs_the_cli_by_absolute_path():
    text = CLI_PATH.read_text()
    assert 'python3 /app/scripts/mc/github_awareness.py' in text
    assert 'sys.path.insert(0, str(Path(__file__).resolve().parents[2]))' in text


def test_cli_and_context_share_dedicated_persistent_volume():
    import yaml
    from scripts.mc import github_awareness as cli
    config = yaml.safe_load((ROOT / 'docker-compose.prod.yml').read_text())
    assert cli.ROOT == H.ROOT == '/app/runtime/mc-observations/github-awareness'
    mount = 'mc-observations:/app/runtime/mc-observations'
    assert mount in config['services']['portal']['volumes']
    assert 'mc-observations' in config['volumes']
    assert all(mount not in s.get('volumes', []) for n, s in config['services'].items() if n != 'portal')


def test_runs_full_smaller_page_preserves_gap_warning():
    doc = only('runs')
    doc['streams']['runs']['last_success'] = C.iso(NOW - timedelta(minutes=10))
    client = Client(busy)
    out = C.poll(doc, client, now=NOW)
    assert client.calls[0][1]['per_page'] == 25
    assert out['streams']['runs']['gap'] == 'possible_poll_gap'
    assert out['streams']['runs']['health'] == 'ok'
