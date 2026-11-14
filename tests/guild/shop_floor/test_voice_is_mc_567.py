"""#567 direction (Robert, 13 Nov 2026: "MC's voice is MC") and #570 (phone versus desktop was not recorded).

- MC's voice path: "chained" by default, "direct" or "realtime" only when asked for.
  A stray value stays "chained".
- Each call keeps one durable line: path, provider and device class, never the user agent or any words.
- The chained page plays one soft tone while MC works, made on the Voice tap (an iPhone plays sound only after a gesture).
"""
from __future__ import annotations

import json
import logging
import pathlib

import pytest

from floor_helpers import load_portal, write_headers  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401
from test_mc_turns import API, turned  # noqa: F401
from test_mc_voice import _on, provider  # noqa: F401

from core.realtime_voice.devices import device_class
from minimoi_portal.guild_ui import mc_voice_direct as direct
from minimoi_portal.guild_ui.mc.voice_calls import FILE_NAME, VoiceCallLog, call_record_folder

ROOT = pathlib.Path(__file__).resolve().parents[3]
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
          "Version/18.1 Mobile/15E148 Safari/604.1")
MAC = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 "
       "Safari/605.1.15")


@pytest.mark.parametrize("value,path", [(None, "chained"), ("", "chained"), ("chained", "chained"), ("CHAINED", "chained"),
                                        ("direct", "direct"), (" Direct ", "direct"), ("realtime", "realtime"),
                                        ("REALTIME", "realtime"), ("nope", "chained")])
def test_mcs_voice_path_is_chained_unless_direct_is_asked_for(value, path):
    env = {} if value is None else {direct.PATH_ENV: value}
    assert direct.voice_path(env) == path


@pytest.mark.parametrize("admitted", [None, "1"])
def test_the_chained_path_holds_direct_starts_off_wherever_starts_were_allowed(admitted):
    base = {} if admitted is None else {direct.ADMITTED: admitted}
    assert direct.may_start(base) is False                                        # the default: MC's voice is MC
    assert direct.may_start({**base, direct.PATH_ENV: "direct"}) is True          # the rollback restores today's starts
    assert direct.may_start({direct.ADMITTED: "0", direct.PATH_ENV: "direct"}) is False   # production's own hold stands


@pytest.mark.parametrize("ua,device", [
    (IPHONE, "phone"),
    ("Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Mobile Safari/537.36", "phone"),
    ("Mozilla/5.0 (iPad; CPU OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148", "tablet"),
    ("Mozilla/5.0 (Linux; Android 15; SM-X910) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36", "tablet"),
    (MAC, "desktop"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36", "desktop"),
    ("", "unknown"), (None, "unknown"), ("curl/8.7.1", "unknown"),
])
def test_device_class_reads_phone_tablet_or_desktop_from_the_user_agent(ua, device):
    assert device_class(ua) == device


def test_a_call_line_keeps_path_provider_device_and_a_short_session_only(tmp_path):
    log = VoiceCallLog(str(tmp_path))
    assert log.record(path="direct", provider="openai", device="phone", session="5ce74912-245c-4f23-afe5-d4c78343ce8a")
    assert log.record(path="chained", provider="openai", device="desktop")
    lines = [json.loads(line) for line in (tmp_path / FILE_NAME).read_text().splitlines()]
    assert [(l["path"], l["provider"], l["device"], l.get("session")) for l in lines] == [
        ("direct", "openai", "phone", "5ce74912"), ("chained", "openai", "desktop", None)]
    assert set(lines[0]) == {"at", "path", "provider", "device", "session"}
    assert (tmp_path / FILE_NAME).stat().st_mode & 0o777 == 0o600


def test_a_call_line_that_cannot_be_written_says_so_and_never_raises(tmp_path, caplog):
    (tmp_path / FILE_NAME).mkdir()                                   # a folder where the file should be
    assert VoiceCallLog(str(tmp_path)).record(path="chained", provider="openai", device="phone") is False
    with caplog.at_level(logging.WARNING, logger="guild_ui.mc"):
        assert VoiceCallLog(None).record(path="chained", provider="openai", device="phone") is False
    assert "no folder" in caplog.text


class _Store:
    def __init__(self, folder, path="x"):
        self.store = type("S", (), {"folder": folder, "path": path})()


def test_mc_turns_dir_wins_when_it_is_a_directory(tmp_path, monkeypatch):
    turns = tmp_path / "mc-turns"
    turns.mkdir()
    monkeypatch.setenv("MC_TURNS_DIR", str(turns))
    assert call_record_folder(_Store(str(tmp_path / "guild"))) == str(turns)


def test_a_missing_turns_dir_falls_back_to_the_store(tmp_path, monkeypatch):
    monkeypatch.setenv("MC_TURNS_DIR", str(tmp_path / "missing"))
    guild = tmp_path / "guild"
    guild.mkdir()
    assert call_record_folder(_Store(str(guild))) == str(guild)


def test_no_folder_writes_nothing(monkeypatch):
    monkeypatch.delenv("MC_TURNS_DIR", raising=False)
    assert call_record_folder(_Store(None, path=None)) is None


def test_a_chained_call_from_the_iphone_is_recorded_as_a_phone_call(turned, provider, monkeypatch):
    _on(monkeypatch)
    client = turned.owner()
    token = turned.csrf(client)
    r = client.post(f"{API}/mc/voice/bootstrap", json={"provider": "openai"},
                    headers={**write_headers(token), "User-Agent": IPHONE})
    assert r.status_code == 200, r.get_json()
    folder = turned.app.extensions["guild_ui_next"]["services"].store.folder
    [line] = [json.loads(l) for l in (pathlib.Path(folder) / FILE_NAME).read_text().splitlines()]
    assert (line["path"], line["provider"], line["device"]) == ("chained", "openai", "phone")
    assert "iPhone" not in json.dumps(line)                          # the class only, never the user agent


def test_the_chained_page_plays_one_soft_tone_while_mc_works_made_on_the_voice_tap():
    page = (ROOT / "minimoi_portal/guild_ui/static/js/mc_voice.js").read_text()
    start = page.index("async function start()")
    first_await = page.index("await ", start)
    assert start < page.index("primeCue();", start) < first_await        # on the tap, before anything is awaited
    submit = page.index("async _submitTurn(text, requestId)")
    assert page.index("this._onStateChange('thinking');", submit) < page.index("heardCue();", submit)
    assert page.count("heardCue();") == 1                                # one tone per turn, nowhere else
