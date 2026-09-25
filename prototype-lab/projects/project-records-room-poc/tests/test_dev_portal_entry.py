from types import SimpleNamespace
from flask import Flask
from dev_portal_entry import enable_records


def portal():
    app = Flask(__name__)
    app.add_url_rule('/health', view_func=lambda: 'healthy')
    return SimpleNamespace(app=app, _require_login=lambda fn: fn, _require_owner=lambda fn: fn)


def test_dev_only_and_exact_adjacent_module(monkeypatch):
    monkeypatch.setenv('BASE_URL', 'https://minimoi.ai')
    p = portal()
    assert not enable_records(p)
    assert not any(r.rule.startswith('/app/records') for r in p.app.url_map.iter_rules())
    monkeypatch.setenv('BASE_URL', 'https://dev.minimoi.ai')
    assert enable_records(p)
    assert p.app.test_client().get('/app/records', base_url='https://minimoi.ai').status_code == 404


def test_missing_module_keeps_portal_alive(monkeypatch, tmp_path):
    monkeypatch.setenv('BASE_URL', 'https://dev.minimoi.ai')
    p = portal()
    assert not enable_records(p, tmp_path/'missing.py')
    assert p.app.test_client().get('/health').data == b'healthy'
    assert p.app.test_client().get('/app/records').status_code == 404


def test_partial_registration_is_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv('BASE_URL', 'https://dev.minimoi.ai')
    bridge = tmp_path/'bad.py'
    bridge.write_text("def install(app, *args):\n app.add_url_rule('/app/records', endpoint='records_dev_root', view_func=lambda: 'must never forward')\n raise RuntimeError('simulated setup failure')\n")
    p = portal()
    assert not enable_records(p, bridge)
    assert p.app.test_client().get('/health').data == b'healthy'
    assert p.app.test_client().get('/app/records').status_code == 503
