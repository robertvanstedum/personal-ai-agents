"""CoS Private mode (Spec 160 D6, the #281 review condition): a sticky,
server-owned switch. Owner-only, JSON-only, small, atomic; an unreadable mode
is Private; without a turn log the switch is unavailable."""
import json
import stat
from datetime import datetime, timezone

import pytest
from flask import Flask

from domains.cos import private_mode as pm

OWNER = {"X-Minimoi-Auth-Id": "1", "X-Minimoi-User-Tier": "owner"}


def _client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(pm.create_private_mode_blueprint())
    return app.test_client()


@pytest.fixture
def turns_dir(tmp_path, monkeypatch):
    root = tmp_path / "cos-turns"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("COS_TURNS_DIR", str(root))
    return root


def _set(private, headers=OWNER, **kw):
    return _client().post("/ui/private-mode", json={"private": private}, headers=headers, **kw)


def test_default_is_kept_and_the_switch_is_available(turns_dir):
    data = _client().get("/ui/private-mode", headers=OWNER).get_json()
    assert data["available"] is True and data["private"] is False
    assert data["disclosure"] == "Private: not kept in your CoS history. The agent itself may still remember it."


def test_on_is_sticky_and_written_per_spec_160(turns_dir):
    assert _set(True).get_json()["private"] is True
    modes = json.loads((turns_dir / "_mode.json").read_text())
    assert modes["owner"]["mode"] == "private"
    assert modes["owner"]["changed_at"].endswith("Z")
    assert stat.S_IMODE((turns_dir / "_mode.json").stat().st_mode) == 0o600
    assert _client().get("/ui/private-mode", headers=OWNER).get_json()["private"] is True   # sticky
    assert _set(False).get_json()["private"] is False                                     # off only by the switch
    assert json.loads((turns_dir / "_mode.json").read_text())["owner"]["mode"] == "public"


def test_other_conversations_are_kept(turns_dir):
    (turns_dir / "_mode.json").write_text('{"conv-2": {"mode": "private"}}')
    _set(True)
    modes = json.loads((turns_dir / "_mode.json").read_text())
    assert modes["conv-2"] == {"mode": "private"} and modes["owner"]["mode"] == "private"


@pytest.mark.parametrize("content", ["not json", "[1]", '{"owner": "someday"}'])
def test_an_unreadable_or_unknown_mode_reads_as_private(turns_dir, content):
    (turns_dir / "_mode.json").write_text(content)
    assert _client().get("/ui/private-mode", headers=OWNER).get_json()["private"] is True
    assert pm.is_private(turns_dir) is True


def test_a_mode_file_that_cannot_be_opened_is_private(turns_dir):
    (turns_dir / "_mode.json").mkdir()
    assert pm.read_mode(turns_dir) == (True, "unreadable")


def test_an_explicit_choice_replaces_an_unparseable_file(turns_dir):
    (turns_dir / "_mode.json").write_text("garbage")
    assert _set(False).get_json()["private"] is False


def test_two_switches_in_one_second_still_change_the_epoch(turns_dir):
    same = datetime(2026, 9, 29, 20, 0, tzinfo=timezone.utc)
    pm.set_private(turns_dir, False, now=same)
    started = pm.read_mode(turns_dir)[1]
    pm.set_private(turns_dir, True, now=same)
    pm.set_private(turns_dir, False, now=same)                   # back where it was, same second
    assert pm.read_mode(turns_dir)[1] != started
    assert json.loads((turns_dir / "_mode.json").read_text())["owner"]["revision"] == 3


def test_the_epoch_changes_on_every_switch(turns_dir):
    first = pm.read_mode(turns_dir)[1]
    assert first == "absent"
    pm.set_private(turns_dir, True, now=datetime(2026, 9, 29, 20, 0, tzinfo=timezone.utc))
    second = pm.read_mode(turns_dir)[1]
    pm.set_private(turns_dir, True, now=datetime(2026, 9, 29, 20, 1, tzinfo=timezone.utc))
    assert len({first, second, pm.read_mode(turns_dir)[1]}) == 3


@pytest.mark.parametrize("headers", [
    {},                                                         # nobody
    {"X-Minimoi-Auth-Id": "7", "X-Minimoi-User-Tier": "guest"},  # signed in, not the owner
    {"X-Minimoi-Auth-Id": "1"},                                  # no tier from the portal
])
def test_only_the_owner_can_switch(turns_dir, headers):
    assert _set(True, headers=headers).status_code == 403
    assert not (turns_dir / "_mode.json").exists()


def test_reading_needs_identity(turns_dir):
    assert _client().get("/ui/private-mode").status_code == 401


def test_json_only_and_small(turns_dir):
    client = _client()
    form = client.post("/ui/private-mode", data={"private": "true"}, headers=OWNER)
    assert form.status_code == 415                               # a cross-site form cannot switch it
    big = client.post("/ui/private-mode", data=b'{"private": true, "x": "' + b"y" * 2000 + b'"}',
                      headers={**OWNER, "Content-Type": "application/json"})
    assert big.status_code == 413
    assert client.post("/ui/private-mode", json={"private": "yes"}, headers=OWNER).status_code == 400
    assert not (turns_dir / "_mode.json").exists()


def test_without_a_turn_log_the_switch_is_unavailable(monkeypatch):
    monkeypatch.delenv("COS_TURNS_DIR", raising=False)
    assert _client().get("/ui/private-mode", headers=OWNER).get_json() == {
        "available": False, "private": False, "disclosure": pm.DISCLOSURE}
    response = _set(True)
    assert response.status_code == 409 and response.get_json()["available"] is False


def test_a_failed_write_says_so_and_logs_no_content(turns_dir, monkeypatch, capsys):
    def boom(*a, **k):
        raise PermissionError("denied")
    monkeypatch.setattr(pm, "set_private", boom)
    response = _set(True)
    assert response.status_code == 500 and response.get_json()["error"] == "mode not saved"
    assert "saved=false error=PermissionError" in capsys.readouterr().out


def test_chief_of_staff_wires_private_mode_to_text_voice_and_the_switch():
    source = (pm.Path(pm.__file__).parent / "chief_of_staff.py").read_text()
    assert "app.register_blueprint(private_mode.create_private_mode_blueprint())" in source
    assert "session_context=lambda user_id: private_mode.state()," in source
    assert 'payload["private"] = private_mode.state()["private"]' in source   # text replies are marked
