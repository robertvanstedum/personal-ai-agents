"""Confer voice transcripts (Phase A): POST /ui/voice/transcript writes one
html_voice record to the CoS turn log, and nothing at all while Private."""
import json
import stat
from datetime import datetime, timezone

import pytest
from flask import Flask

from domains.cos import private_mode
from domains.cos import voice_transcripts as vt

OWNER = {"X-Minimoi-Auth-Id": "1"}
NOW = datetime(2026, 9, 30, 3, 15, tzinfo=timezone.utc)      # Sep 29 in Chicago
TURNS = [
    {"speaker": "assistant", "text": "Hi Robert, what shall we work on?", "completed": True},
    {"speaker": "user", "text": "What is on my list today?", "completed": True},
    {"speaker": "assistant", "text": "Two items: the voice fix and the review.", "completed": True},
]


def _client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(vt.create_voice_transcript_blueprint(clock=lambda: NOW))
    return app.test_client()


_CURRENT = object()


def _post(body, headers=OWNER, epoch=_CURRENT):
    """Posts as the page does: with the mode epoch its bootstrap handed it
    (by default the mode as it is now, so the mode did not change)."""
    body = dict(body)
    if epoch is _CURRENT:
        root = vt.turns_dir()
        epoch = private_mode.read_mode(root)[1] if root else None
    if epoch is not None and "mode_epoch" not in body:
        body["mode_epoch"] = epoch
    return _client().post("/ui/voice/transcript", json=body, headers=headers)


def _day_file(root):
    return root / "2026" / "2026-09-29.jsonl"


@pytest.fixture
def turns_dir(tmp_path, monkeypatch):
    root = tmp_path / "cos-turns"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("COS_TURNS_DIR", str(root))
    monkeypatch.setenv("COS_AGENT_TIMEZONE", "America/Chicago")
    return root


def test_identity_is_required(turns_dir):
    response = _post({"turns": TURNS}, headers={})
    assert response.status_code == 401
    assert not _day_file(turns_dir).exists()


def test_without_a_turn_log_nothing_is_written(monkeypatch, tmp_path):
    monkeypatch.delenv("COS_TURNS_DIR", raising=False)
    response = _post({"turns": TURNS})
    assert response.status_code == 200
    assert response.get_json() == {"saved": False, "reason": "no_turn_log"}


def test_a_session_is_one_html_voice_line_named_by_the_local_day(turns_dir):
    response = _post({
        "turns": TURNS, "provider": "openai", "partial": False, "partial_reason": None,
        "duration_seconds": 42.26, "session_id": "sess-1", "reply_mode": "write",
    })
    assert response.get_json() == {"saved": True, "turns": 3}
    lines = _day_file(turns_dir).read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["schema_version"] == 1
    assert record["record_type"] == "voice_session"
    assert record["channel"] == "html_voice"
    assert record["conversation_id"] == "owner"
    assert record["time"] == "2026-09-30T03:15:00Z"               # UTC inside
    assert record["voice_provider"] == "openai"
    assert record["reply_mode"] == "write"
    assert record["duration_seconds"] == 42.3
    assert record["session_id"] == "sess-1"
    assert [t["speaker"] for t in record["turns"]] == ["assistant", "user", "assistant"]
    assert record["sanitized"] is False


def test_files_are_0600_and_folders_0700(turns_dir):
    _post({"turns": TURNS})
    assert stat.S_IMODE(_day_file(turns_dir).stat().st_mode) == 0o600
    assert stat.S_IMODE((turns_dir / "2026").stat().st_mode) == 0o700


def test_a_second_session_appends(turns_dir):
    _post({"turns": TURNS})
    _post({"turns": TURNS[:2]})
    assert len(_day_file(turns_dir).read_text().splitlines()) == 2


@pytest.mark.parametrize("mode_file", [
    '{"owner": "private"}',
    '{"owner": {"private": true}}',
    '{"owner": {"mode": "private"}}',
    '{"owner": "something new"}',          # a mode this reader does not know: Private
    "not json",                             # unreadable: Private
    '["owner"]',
])
def test_private_or_unreadable_mode_writes_nothing(turns_dir, mode_file):
    (turns_dir / "_mode.json").write_text(mode_file)
    response = _post({"turns": TURNS})
    assert response.get_json() == {"saved": False, "reason": "private"}
    assert not (turns_dir / "2026").exists()


def test_a_mode_file_that_cannot_be_read_is_private(turns_dir):
    mode = turns_dir / "_mode.json"
    mode.mkdir()                                     # reading it raises OSError
    assert _post({"turns": TURNS}).get_json()["reason"] == "private"


@pytest.mark.parametrize("mode_file", ['{"owner": "public"}', '{"someone-else": "private"}', "{}"])
def test_public_or_absent_mode_is_kept(turns_dir, mode_file):
    (turns_dir / "_mode.json").write_text(mode_file)
    assert _post({"turns": TURNS}).get_json()["saved"] is True


def test_the_request_can_ask_for_private(turns_dir):
    assert _post({"turns": TURNS, "private": True}).get_json()["reason"] == "private"
    assert not (turns_dir / "2026").exists()


def test_private_logs_nothing_to_stdout(turns_dir, capsys):
    (turns_dir / "_mode.json").write_text('{"owner": "private"}')
    _post({"turns": TURNS})
    assert "list today" not in capsys.readouterr().out


def test_card_numbers_are_removed(turns_dir):
    turns = [{"speaker": "user", "text": "my card is 4242 4242 4242 4242 and id 1234567890123", "completed": True}]
    _post({"turns": turns})
    record = json.loads(_day_file(turns_dir).read_text())
    text = record["turns"][0]["text"]
    assert "4242" not in text and vt.REMOVED in text
    assert "1234567890123" in text                   # fails Luhn: an id, kept
    assert record["sanitized"] is True


def test_a_truncated_tail_is_closed_with_a_gap_line(turns_dir):
    day = _day_file(turns_dir)
    day.parent.mkdir(mode=0o700)
    day.write_text('{"record_type":"voice_session","tur')
    _post({"turns": TURNS})
    lines = day.read_text().splitlines()
    assert json.loads(lines[1]) == {"record_type": "log_gap", "reason": "truncated_tail"}
    assert json.loads(lines[2])["record_type"] == "voice_session"


@pytest.mark.parametrize("body", [
    {"turns": "no"},
    {"turns": [{"speaker": "system", "text": "x"}]},
    {"turns": [{"speaker": "user", "text": 5}]},
    {"turns": [{"speaker": "user", "text": "x" * (vt.MAX_TEXT + 1)}]},
    {"turns": [{"speaker": "user", "text": "x"}] * (vt.MAX_TURNS + 1)},
])
def test_invalid_bodies_are_refused(turns_dir, body):
    assert _post(body).status_code == 400
    assert not (turns_dir / "2026").exists()


def test_empty_turns_write_nothing(turns_dir):
    assert _post({"turns": [{"speaker": "user", "text": "  "}]}).get_json()["reason"] == "empty"


def test_a_write_failure_is_reported_without_content(turns_dir, capsys, monkeypatch):
    def boom(*a, **k):
        raise PermissionError("denied")
    monkeypatch.setattr(vt, "append_record", boom)
    response = _post({"turns": TURNS})
    assert response.status_code == 500 and response.get_json()["reason"] == "write_failed"
    out = capsys.readouterr().out
    assert "saved=false reason=write_failed" in out and "list today" not in out


def test_chief_of_staff_registers_the_route():
    source = (vt.Path(vt.__file__).parent / "chief_of_staff.py").read_text()
    assert "app.register_blueprint(create_voice_transcript_blueprint())" in source


# ── F2: a mode change during the session makes the whole session Private ─────

def test_private_at_the_start_then_switched_off_keeps_nothing(turns_dir):
    private_mode.set_private(turns_dir, True)
    started_under = private_mode.read_mode(turns_dir)[1]         # the bootstrap's epoch
    private_mode.set_private(turns_dir, False)                   # switched off before Stop
    response = _post({"turns": TURNS}, epoch=started_under)
    assert response.get_json() == {"saved": False, "reason": "mode_changed"}
    assert not (turns_dir / "2026").exists()


def test_public_then_private_during_the_session_keeps_nothing(turns_dir):
    started_under = private_mode.read_mode(turns_dir)[1]         # "absent": kept by default
    private_mode.set_private(turns_dir, True)
    assert _post({"turns": TURNS}, epoch=started_under).get_json()["reason"] == "private"
    assert not (turns_dir / "2026").exists()


def test_no_record_of_the_start_keeps_nothing(turns_dir):
    assert _post({"turns": TURNS}, epoch=None).get_json()["reason"] == "mode_changed"
    assert not (turns_dir / "2026").exists()


def test_an_unchanged_mode_is_kept(turns_dir):
    private_mode.set_private(turns_dir, False)
    started_under = private_mode.read_mode(turns_dir)[1]
    assert _post({"turns": TURNS}, epoch=started_under).get_json()["saved"] is True


# ── F3: credentials are scrubbed too ─────────────────────────────────────────

def test_credentials_are_removed_before_a_line_is_written(turns_dir):
    said = ("my api key is sk-ant-abcdefgh12345678 and the password: hunter2, "
            "db postgres://bob:pw1@db:5432/x, token=ghp_abcdefghijklmnop")
    _post({"turns": [{"speaker": "user", "text": said, "completed": True}]})
    record = json.loads(_day_file(turns_dir).read_text())
    text = record["turns"][0]["text"]
    for secret in ("sk-ant-abcdefgh12345678", "hunter2", "bob:pw1", "ghp_abcdefghijklmnop"):
        assert secret not in text
    assert "[credential removed]" in text and record["sanitized"] is True


# ── F5: the body is capped before it is parsed ───────────────────────────────

def test_a_body_over_the_cap_is_refused_before_parsing(turns_dir, monkeypatch):
    parsed = []
    monkeypatch.setattr("flask.Request.get_json", lambda *a, **k: parsed.append(1) or {})
    big = b'{"turns": [], "pad": "' + b"x" * vt.MAX_BODY + b'"}'
    response = _client().post("/ui/voice/transcript", data=big, headers={**OWNER, "Content-Type": "application/json"})
    assert response.status_code == 413 and response.get_json()["reason"] == "too_large"
    assert parsed == []                                          # never parsed
    assert not (turns_dir / "2026").exists()
