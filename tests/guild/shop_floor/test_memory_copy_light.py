"""The Agents light carries the memory copy (Spec 160 §7, T8): the reason always says
"memory copy"; never green without a live, enabled, green answer; names and text never pass."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from minimoi_portal.guild_ui import lights
from minimoi_portal.guild_ui.adapters import MemoryCopyProbe
from minimoi_portal.guild_ui.adapters.contract import live_ok, live_unknown, not_configured

NOW = datetime(2026, 10, 3, 15, 0, tzinfo=timezone.utc)


def payload(state="green", reason="claude-code memory copy 3 h old", enabled=True, minutes_ahead=0, **extra):
    return {"enabled": enabled, "state": state, "reason": reason,
            "as_of": (NOW + timedelta(minutes=minutes_ahead)).isoformat(),
            "sources": {"claude-code": {"state": state, "reason": reason}},
            "turn_log": {"cos-scheduler": {"last_success_at": "2026-10-03T14:00:00Z",
                                           "last_failure_at": None, "last_failure_code": None}}, **extra}


@pytest.mark.parametrize("body,state", [
    (payload("green"), "green"),
    (payload("yellow", "claude-code memory copy 40 h old"), "yellow"),
    (payload("red", "claude-code memory copy never succeeded"), "red"),
    (payload("unknown", "memory copy no answer"), "unknown"),
    (payload("green", enabled=False), "unknown"),              # the copier is off: never green
    (payload("green", minutes_ahead=30), "unknown"),           # an answer from the future is clock skew
])
def test_the_light_follows_the_scheduler_and_never_invents_green(body, state):
    light = lights.memory_copy_light(live_ok(body, "probe"), NOW)
    assert light["state"] == state
    assert len(light["reason"]) <= lights.REASON_MAX
    if state == "unknown":
        assert light["word"] == "Unknown"


def test_no_answer_or_not_configured_is_unknown_and_says_memory_copy():
    for res in (live_unknown("read_failed", "unreachable", "probe"), not_configured("GUILD_MEMORY_STATUS_URL", "x")):
        light = lights.memory_copy_light(res, NOW)
        assert light["state"] == "unknown" and "memory copy" in light["reason"]


def test_detail_lines_show_each_source_and_the_turn_log():
    light = lights.memory_copy_light(live_ok(payload("yellow", "claude-code memory copy 40 h old"), "probe"), NOW)
    assert any(l.startswith("claude-code: yellow") for l in light["detail"])
    assert any(l.startswith("turn log (cos-scheduler)") for l in light["detail"])


class Answer:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def probe(answer):
    return MemoryCopyProbe("http://x/agent-memory/status", http_get=lambda url, timeout: answer)


def test_probe_unset_url_is_not_configured():
    res = MemoryCopyProbe(None).status()
    assert res.source == "not_instrumented" and not res.ok


@pytest.mark.parametrize("answer", [Answer({}, 500), Answer(ValueError("x")), Answer([1, 2]),
                                    Answer({"enabled": True, "state": "purple"}),
                                    Answer({"enabled": "yes", "state": "green"})])
def test_probe_failures_are_unknown_never_zero(answer):
    res = probe(answer).status()
    assert res.status == "unknown" and res.data is None


def test_probe_raising_is_unknown():
    def boom(url, timeout):
        raise ConnectionError("refused")
    res = MemoryCopyProbe("http://x", http_get=boom).status()
    assert res.status == "unknown" and "ConnectionError" in res.error


def test_probe_passes_only_whitelisted_keys_and_nothing_secret():
    body = payload(secret="TOPSECRET-9Z", file_names=["private.md"])
    body["sources"]["claude-code"]["path"] = "/Users/robert/private.md"
    body["turn_log"]["cos-scheduler"]["user_text"] = "TOPSECRET-9Z"
    data = probe(Answer(body)).status().data
    blob = str(data)
    assert "TOPSECRET" not in blob and "private.md" not in blob and "/Users/" not in blob
    assert data["sources"]["claude-code"].keys() == {"state", "reason"}


def test_probe_caches_answers_and_failures_for_a_minute():
    clock = [0.0]
    calls = []

    def get(url, timeout):
        calls.append(1)
        return Answer(payload())
    p = MemoryCopyProbe("http://x", http_get=get, monotonic=lambda: clock[0])
    p.status(); p.status()
    assert len(calls) == 1
    clock[0] = 61
    p.status()
    assert len(calls) == 2
