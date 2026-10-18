"""Register context across typed/managed voice; synthetic store, no provider calls."""
from types import SimpleNamespace
import pytest
from flask import Flask, g
from minimoi_portal.guild_ui import api, mc_registers, mc_context, mc_regression, release_context, mc_voice_transcripts
import minimoi_portal.guild_ui as guild

CONV = {'id': 'fixture', 'principal': 'robert', 'notes_floor': 'floor'}
MARKER = 'Synthetic register responsibility'

@pytest.fixture
def context(monkeypatch):
    fresh = dict(CONV)
    monkeypatch.setattr(api, '_conversations', lambda: SimpleNamespace(get=lambda *a: dict(fresh)))
    monkeypatch.setattr(api, 'cfg', lambda: {'base_url': 'http://localhost'})
    monkeypatch.setattr(guild, 'feature_environ', lambda: {'MINIMOI_GUILD_REGISTERS': '1'})
    monkeypatch.setattr(mc_registers, 'configured_store', lambda *a: SimpleNamespace(projection=lambda *a, **kw: {'entries': [{'body': MARKER}]}))
    monkeypatch.setattr(release_context, 'block', lambda *a: '')
    monkeypatch.setattr(mc_regression, 'block', lambda *a, **kw: '')
    monkeypatch.setattr(mc_context, 'board_block', lambda *a, **kw: '')
    monkeypatch.setattr(mc_context, 'github_block', lambda *a, **kw: '')
    monkeypatch.setattr(mc_voice_transcripts, 'block', lambda *a, **kw: '')
    monkeypatch.setattr(api, '_conv_floor', lambda *a: None)
    return Flask(__name__), fresh

def run(voice):
    return api._with_context('What is your register?', 'What is your register?', CONV, 'robert', current_voice=voice)

def test_managed_voice_reads_register_without_request(context):
    app, fresh = context
    with app.app_context():
        assert MARKER in run(True)

@pytest.mark.parametrize('change', [{'principal': 'other'}, {'archived': True}, {'record_mode': 'off_record'}, {'notes_floor': 'other'}])
def test_managed_voice_refuses_changed_scope(context, change):
    app, fresh = context
    fresh.update(change)
    with app.app_context():
        assert MARKER not in run(True)

@pytest.mark.parametrize('attested,header,expected', [(True, 'on_record', True), (False, 'on_record', False), (True, 'off_record', False)])
def test_typed_turn_still_requires_request_attestation(context, attested, header, expected):
    app, fresh = context
    with app.test_request_context(headers={'X-Record-Mode': header}):
        if attested: g.mc_kept_owner_scope = ('robert', 'fixture', 'note')
        assert (MARKER in run(False)) is expected

def test_register_context_stays_off_when_disabled(context, monkeypatch):
    app, fresh = context
    monkeypatch.setattr(guild, 'feature_environ', lambda: {})
    with app.app_context():
        assert MARKER not in run(True)
