"""The cost probe's streaming mode (streaming S1): turn A (streaming and usage)
and turn B (Stop) through api.open_mc_stream, the same server path as
/mc/turns/stream. The relay is mocked (a scripted NDJSON stream) and a
stand-in gateway writes its usage records; no network, no model call."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored  # noqa: F401  (pytest fixtures)
from test_mc_streaming import HAPPY, StreamRuntime, nd, _fresh_dispatch_state  # noqa: F401  (autouse fixture)
from test_mc_turns import _openclaw, _turn_on

from minimoi_portal.guild_ui.conversations import conversations_of
from minimoi_portal.guild_ui.mc import cost_probe, stream_probe


class GatewayRuntime(StreamRuntime):
    """The relay, plus the gateway's usage record for each streamed call."""

    def __init__(self, usage_dir, *, record_aborted=True, cost=0.004):
        super().__init__()
        self.usage_dir, self.record_aborted, self.cost = usage_dir, record_aborted, cost

    def post(self, url, data=None, headers=None, stream=False, **kw):
        resp = super().post(url, data=data, headers=headers, stream=stream, **kw)
        if stream and url.endswith("/chat/completions"):
            aborted = "WAIT" in self.script
            if not aborted or self.record_aborted:
                rec = {"v": 1, "record_id": os.urandom(16).hex(), "occurred_at": datetime.now(timezone.utc).isoformat(),
                       "env": "staging", "emitter": "gateway", "actor": "mc", "kind": "model",
                       "route": "minimoi-mc-agent", "status": "ok", "input_tokens": 50,
                       "output_tokens": 3 if aborted else 7, "cost_usd": self.cost, "cost_source": "price_table"}
                with open(os.path.join(self.usage_dir, f"usage-{rec['occurred_at'][:7]}.jsonl"), "a") as f:
                    f.write(json.dumps(rec) + "\n")
        return resp


@pytest.fixture
def probe(floored, tmp_path, monkeypatch):
    usage = tmp_path / "usage"
    usage.mkdir()
    monkeypatch.setenv("MINIMOI_USAGE_DIR", str(usage))
    runtime = GatewayRuntime(str(usage))
    services = _turn_on(floored, _openclaw(runtime))
    services.mc_stream = True
    return {"services": services, "runtime": runtime, "usage": str(usage)}


def _run(probe, *, stop_only=False, cap=1.0):
    lines = []
    result = stream_probe.run(probe["services"], conversations_of(probe["services"]), principal="robert",
                              label="Robert", stop_only=stop_only, cap=cap, usage_dir=probe["usage"], wait_s=2,
                              out=lines.append, sleep=lambda s: None)
    return result, lines


def test_turn_a_then_turn_b_report_streaming_usage_and_stop(probe):
    runtime = probe["runtime"]
    scripts = iter([list(HAPPY), [nd({"t": "delta", "text": "The first words "}), "WAIT"] + HAPPY])
    original = runtime.post

    def post(url, data=None, headers=None, stream=False, **kw):
        if stream and url.endswith("/chat/completions"):
            runtime.script = next(scripts)
        return original(url, data=data, headers=headers, stream=stream, **kw)
    runtime.post = post
    probe["services"].mc = _openclaw(runtime)               # the adapter sees the wrapped post
    probe["services"].mc_health = __import__("minimoi_portal.guild_ui.mc", fromlist=["CachedHealth"]).CachedHealth(
        probe["services"].mc)
    result, lines = _run(probe)
    text = "\n".join(lines)
    a, b = result["turns"]
    assert a["status"] == "answered" and a["timing"]["first_delta_ms"] is not None and a["timing"]["finish_ms"] is not None
    assert a["browser_events"]["ack"] == 1 and a["browser_events"]["delta"] == 2 and a["browser_events"]["render"] >= 1
    assert a["backend_events"]["finish"] == 1 and a["backend_events"]["usage"] == 1
    assert a["turn_line"]["mode"] == "stream" and a["turn_line"]["status"] == "answered"
    assert a["runtime_stream"]["output_tokens"] == 7 and a["gateway_output_tokens"] == 7 and a["cost_usd"] == 0.004
    assert "output tokens: stream 7 vs gateway 7 (0% apart)" in text
    assert b["status"] == "stopped" and b["failure_class"] == "stopped" and b["stopped"] is True
    assert b["runtime_stream"]["status"] == "error" and b["runtime_stream"]["output_tokens"] is None
    assert b["turn_line"]["status"] == "stopped" and b["gateway"] and b["gateway"][0]["output_tokens"] == 3
    assert any(p["url"].endswith("/turns/stop") for p in runtime.posts)
    assert "total spent: $0.008000" in text


def test_turn_b_alone_says_when_the_gateway_did_not_record_the_aborted_call(probe):
    runtime = probe["runtime"]
    runtime.record_aborted = False
    runtime.script = [nd({"t": "delta", "text": "Some text "}), "WAIT"] + HAPPY
    result, lines = _run(probe, stop_only=True)
    [b] = result["turns"]
    assert b["status"] == "stopped" and b["gateway"] == [] and b["cost_usd"] is None
    assert any("gateway: no record in the turn's window within 2 s (the aborted call was not recorded, or not yet)" in l
               for l in lines)
    assert result["stopped"] == "usage_unknown"


def test_an_unknown_cost_on_turn_a_stops_before_turn_b(probe):
    runtime = probe["runtime"]
    runtime.cost = None                                       # the gateway's record has no cost
    result, lines = _run(probe)
    assert len(result["turns"]) == 1 and result["stopped"] == "usage_unknown"
    assert any(l.startswith("STOP: turn A's cost could not be read") for l in lines)
    assert len([p for p in runtime.posts if p["url"].endswith("/chat/completions")]) == 1


def test_the_cap_stops_before_turn_b_and_is_never_above_a_dollar(probe):
    probe["runtime"].cost = 1.2                                # turn A alone passes $1 (the cap is never above it)
    result, lines = _run(probe, cap=5.0)
    assert len(result["turns"]) == 1 and result["stopped"] == "cap"
    assert any("STOP: spent $1.2000, over the $1.00 cap; turn B not sent." in l for l in lines)
    assert len([p for p in probe["runtime"].posts if p["url"].endswith("/chat/completions")]) == 1


def test_the_stream_mode_refuses_without_yes_spend_a_store_or_streaming(probe, capsys, tmp_path):
    services = probe["services"]
    assert cost_probe.main(["--stream"], services=services) == 2
    assert "Refused: this sends 2 paid" in capsys.readouterr().out
    assert cost_probe.main(["--stop-after-first-text", "--yes-spend"], services=services) == 2
    services.mc_stream = False
    assert cost_probe.main(["--stream", "--yes-spend"], services=services) == 3
    services.mc_stream = True
    result = stream_probe.run(services, conversations_of(services), principal="robert", label="Robert", stop_only=False,
                              cap=1.0, usage_dir=str(tmp_path / "absent"), out=lambda s: None)
    assert result["stopped"] == "no_usage_store"
    assert probe["runtime"].posts == []
