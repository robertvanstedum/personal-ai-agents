"""Synthetic adapter/store tests: no runtime, database or paid provider calls."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from minimoi_portal.guild_ui.mc.backend import TurnResult

REPO = Path(__file__).resolve().parents[2]
from minimoi_portal.guild_ui.mc import morning as module
NOW = datetime(2026, 10, 14, 9, 40, tzinfo=timezone.utc)


def row(i=1, **changes):
    value = dict(id=i, version=3, text='Review pending decision', author='owner', kind='note', label='decide')
    value.update(changes)
    return value


class Floor:
    floor = 'guild'
    def __init__(self, active=None, ok=True, outcome='added'):
        self.active = [row()] if active is None else active
        self.ok, self.outcome, self.writes = ok, outcome, []
    def board(self):
        return SimpleNamespace(ok=self.ok, data={'active': self.active})
    def add_postit(self, text, author, **kwargs):
        self.writes.append(('add', text, author, kwargs))
        return SimpleNamespace(outcome=self.outcome, repeated=False, value={'id': 7})
    def set_text(self, identity, text, author, **kwargs):
        self.writes.append(('update', identity, text, author, kwargs))
        return SimpleNamespace(outcome=self.outcome, repeated=False, value={'id': identity})


class Backend:
    kind = 'cos'
    def __init__(self, text='{"action":"noop"}', status='answered'):
        self.text, self.status, self.calls = text, status, []
    def turn(self, request):
        self.calls.append(request)
        return TurnResult(self.status, 'cos', text=self.text if self.status == 'answered' else None)


def remind(text='Please review the decision', ids=None):
    return json.dumps({'action': 'remind', 'text': text, 'source_ids': [1] if ids is None else ids})


def test_noop_keeps_board_unchanged():
    f, b = Floor(), Backend()
    assert module.run_once(f, b, now=NOW)['code'] == 'noop'
    assert f.writes == [] and len(b.calls) == 1


def test_add_scrubs_inbound_and_outbound_and_has_daily_key():
    f = Floor(active=[row(text='Contact owner@example.com')])
    b = Backend(remind('Email owner@example.com about decision'))
    assert module.run_once(f, b, now=NOW)['code'] == 'added'
    assert 'owner@example.com' not in b.calls[0].text
    assert 'owner@example.com' not in f.writes[0][1]
    assert f.writes[0][-1]['idempotency_key'].endswith('2026-10-14')
    assert f.writes[0][-1]['label'] == 'remember'


def test_update_only_explicit_mc_note_and_uses_expected_version():
    target = row(2); target.update(author='master_craftsman', text='Earlier reminder')
    f, b = Floor(active=[row(), target], outcome='edited'), Backend(remind())
    assert module.run_once(f, b, now=NOW, reminder_id=2)['code'] == 'edited'
    assert f.writes[0][0:2] == ('update', 2)
    assert f.writes[0][-1]['expect_version'] == 3
    assert 'Earlier reminder' not in b.calls[0].text
    assert len(f.writes) == 1


@pytest.mark.parametrize('outcome', ['conflict', 'idempotency_mismatch', 'not_found'])
def test_store_refusal_is_failure_not_success(outcome):
    f, b = Floor(outcome=outcome), Backend(remind())
    with pytest.raises(module.MorningError, match='board_write_refused'):
        module.run_once(f, b, now=NOW)


@pytest.mark.parametrize('text', ['garbage', '{"action":"noop","extra":1}', remind(ids=[99]), remind(ids=[True]), remind(ids=[]), remind('x'*281), 'prose\n'+remind()])
def test_malformed_or_unfounded_reply_never_writes(text):
    f = Floor()
    with pytest.raises(module.MorningError):
        module.run_once(f, Backend(text), now=NOW)
    assert not f.writes


def test_one_exact_json_fence_supported():
    assert module.run_once(Floor(), Backend('```json\n'+remind()+'\n```'), now=NOW)['code'] == 'added'


@pytest.mark.parametrize('target', [None, row(2)])
def test_missing_or_foreign_reminder_refused_before_model(target):
    f = Floor(active=[row()] + ([] if target is None else [target])); b = Backend()
    with pytest.raises(module.MorningError):
        module.run_once(f, b, now=NOW, reminder_id=2)
    assert not b.calls and not f.writes


def test_unavailable_board_not_empty_and_no_model_call():
    f, b = Floor(ok=False), Backend()
    with pytest.raises(module.MorningError, match='board_unavailable'):
        module.run_once(f, b, now=NOW)
    assert not b.calls


def test_empty_board_noop_without_provider_call():
    b = Backend()
    assert module.run_once(Floor(active=[]), b, now=NOW)['code'] == 'noop'
    assert not b.calls


def test_timeout_uncertain_never_writes():
    f = Floor()
    with pytest.raises(module.MorningError, match='timeout_uncertain'):
        module.run_once(f, Backend(status='timeout_uncertain'), now=NOW)
    assert not f.writes


def test_day_key_uses_chicago_not_utc():
    f = Floor()
    module.run_once(f, Backend(remind()), now=datetime(2026,10,14,1,tzinfo=timezone.utc))
    assert f.writes[0][-1]['idempotency_key'].endswith('2026-10-13')


def test_prepare_cli_never_needs_runtime_configuration(capsys):
    spec = importlib.util.spec_from_file_location('morning_cli', REPO / 'scripts/mc/morning.py')
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    assert cli.main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['plan_only'] and result['calls_performed'] == result['writes_performed'] == 0
    with pytest.raises(module.MorningError, match='adoption_required'):
        cli.main(['--execute'])


def test_cli_retains_real_status_and_skips_completed_day(tmp_path, monkeypatch, capsys):
    from core.jobs import registry, status
    spec = importlib.util.spec_from_file_location('morning_cli_test', REPO / 'scripts/mc/morning.py')
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    root = tmp_path / 'jobs'; root.mkdir(mode=0o700)
    job = registry.Job('mc-morning', 'MC morning', 'ec2',
        {'kind':'daily','at':'04:40','tz':'America/Chicago','text':'daily morning'},
        86400,180000,3600,'mc-morning.json','robert','existing approved work record')
    monkeypatch.setenv('MINIMOI_JOBS_DIR', str(root))
    monkeypatch.setenv('DATABASE_URL', 'synthetic-only')
    monkeypatch.setenv('MINIMOI_JOBS_HOST','ec2')
    monkeypatch.setattr(cli.registry,'load',lambda path: registry.Registry((job,)))
    class Clock:
        @staticmethod
        def now(tz): return NOW
    monkeypatch.setattr(cli,'datetime',Clock)
    f, b = Floor(), Backend(remind())
    monkeypatch.setattr(cli,'FloorStores',lambda callback:f)
    monkeypatch.setattr(cli.ChiefOfStaffBackend,'from_env',lambda env:b)
    assert cli.main(['--execute','--adoption-confirmed','--supervised-create-first']) == 0
    receipt = status.read_status(root,job.status_file,job.id)
    assert receipt['state']=='ok' and receipt['host']=='ec2' and receipt['results']=={'board':'added'}
    assert cli.main(['--execute','--adoption-confirmed','--supervised-create-first']) == 0
    assert len(b.calls)==len(f.writes)==1
    assert 'already_completed' in capsys.readouterr().out


def test_cli_failed_backend_has_truthful_content_free_status(tmp_path, monkeypatch):
    from core.jobs import registry, status
    spec = importlib.util.spec_from_file_location('morning_cli_failure', REPO / 'scripts/mc/morning.py')
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    root = tmp_path / 'jobs'; root.mkdir(mode=0o700)
    job = registry.Job('mc-morning','MC morning','ec2',
        {'kind':'daily','at':'04:40','tz':'America/Chicago','text':'morning'},
        86400,180000,3600,'mc-morning.json','robert','existing work record')
    monkeypatch.setenv('MINIMOI_JOBS_DIR',str(root));monkeypatch.setenv('DATABASE_URL','synthetic')
    monkeypatch.setenv('MINIMOI_JOBS_HOST','ec2')
    monkeypatch.setattr(cli.registry,'load',lambda path:registry.Registry((job,)))
    class Clock:
        @staticmethod
        def now(tz): return NOW
    monkeypatch.setattr(cli,'datetime',Clock)
    f = Floor()
    monkeypatch.setattr(cli,'FloorStores',lambda callback:f)
    monkeypatch.setattr(cli.ChiefOfStaffBackend,'from_env',lambda env:Backend(status='timeout_uncertain'))
    assert cli.main(['--execute','--adoption-confirmed','--supervised-create-first'])==1
    receipt=status.read_status(root,job.status_file,job.id)
    assert receipt['state']=='failed' and receipt['last_success_at'] is None
    assert receipt['results']=={'board':'timeout_uncertain'} and not f.writes
    saved = json.loads((root / 'mc-morning-outcome.json').read_text())
    assert saved['code'] == 'timeout_uncertain'
    with pytest.raises(module.MorningError, match='same_day_attempt_requires_supervised_disposition'):
        cli.main(['--execute','--adoption-confirmed','--supervised-create-first'])


@pytest.mark.parametrize('change', ['edited', 'binned', 'done'])
def test_cited_source_changed_during_backend_call_never_writes(change):
    f = Floor()
    class RaceBackend(Backend):
        def turn(self, request):
            result = super().turn(request)
            if change == 'edited':
                f.active[0]['version'] += 1
            else:
                f.active = []
            return result
    with pytest.raises(module.MorningError, match='source_changed'):
        module.run_once(f, RaceBackend(remind()), now=NOW)
    assert not f.writes


def test_actual_receipt_keeps_cited_versions_request_and_reminder_id():
    f, records = Floor(), []
    result = module.run_once(f, Backend(remind()), now=NOW, record=records.append)
    assert result['sources'] == [{'id': 1, 'version': 3}]
    assert result['reminder_id'] == 7
    assert result['request_id'] == f.writes[0][-1]['idempotency_key']
    assert [r['code'] for r in records] == ['attempt_started', 'write_pending', 'added']
    assert all('text' not in r for r in records)


def test_literal_280_char_limit_is_enforced():
    f = Floor()
    assert module.run_once(f, Backend(remind('z'*280)), now=NOW)['code']=='added'
    f = Floor()
    with pytest.raises(module.MorningError, match='text_invalid'):
        module.run_once(f, Backend(remind('z'*281)), now=NOW)
    assert not f.writes


def test_store_missing_receipt_id_cannot_be_claimed_success():
    class NoReceipt(Floor):
        def add_postit(self, text, author, **kwargs):
            return SimpleNamespace(outcome='added', repeated=False, value=None)
    with pytest.raises(module.MorningError, match='receipt_invalid'):
        module.run_once(NoReceipt(), Backend(remind()), now=NOW)


def test_failed_before_attempt_receipt_cannot_dispatch_native_call():
    b = Backend(remind())
    def disk_failure(receipt):
        raise OSError('synthetic storage failure')
    with pytest.raises(OSError):
        module.run_once(Floor(), b, now=NOW, record=disk_failure)
    assert not b.calls


def test_timeout_retains_captured_versions_and_request_identity():
    records = []
    with pytest.raises(module.MorningError, match='timeout_uncertain'):
        module.run_once(Floor(), Backend(status='timeout_uncertain'), now=NOW, record=records.append)
    assert [r['code'] for r in records] == ['attempt_started', 'timeout_uncertain']
    assert all(r['sources']==[{'id':1,'version':3}] for r in records)
    assert records[0]['request_id']==records[1]['request_id']


def test_execute_requires_verified_id_or_explicit_supervised_first_create():
    spec = importlib.util.spec_from_file_location('morning_cli_gate', REPO / 'scripts/mc/morning.py')
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    with pytest.raises(module.MorningError, match='choose_verified_reminder_or_supervised_create'):
        cli.main(['--execute','--adoption-confirmed'])
    with pytest.raises(module.MorningError, match='choose_verified_reminder_or_supervised_create'):
        cli.main(['--execute','--adoption-confirmed','--supervised-create-first','--reminder-id','7'])
