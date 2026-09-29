"""The operator-only cost probe (slice 2): four turns through the route's own
server path, per-turn tokens and cost from the usage store, --yes-spend, and a
hard stop past the cap. The relay is mocked; no model call."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored  # noqa: F401  (pytest fixtures)
from test_mc_turns import FakeRuntime, _openclaw, _turn_on

from minimoi_portal.guild_ui.conversations import LEGACY_ID, conversations_of
from minimoi_portal.guild_ui.mc import cost_probe

REPO = Path(__file__).resolve().parents[3]


def _gateway_writing(usage_dir, cost_per_call):
    """A stand-in relay that also leaves the gateway's usage record per call."""
    runtime = FakeRuntime()
    answer = runtime.answer

    def post(url, data=None, headers=None, **kw):
        body = json.loads(data)
        runtime.sent.append({"url": url, "body": body, "headers": dict(headers or {})})
        tokens_in = 1000 + 400 * sum(1 for s in runtime.sent if s["body"]["user"] == body["user"])
        rec = {"v": 1, "record_id": os.urandom(16).hex(), "occurred_at": datetime.now(timezone.utc).isoformat(),
               "env": "staging", "emitter": "gateway", "actor": "mc", "kind": "model", "route": "minimoi-mc-agent",
               "status": "ok", "input_tokens": tokens_in, "output_tokens": 50, "cost_usd": cost_per_call,
               "cost_source": "price_table"}
        with open(os.path.join(usage_dir, f"usage-{rec['occurred_at'][:7]}.jsonl"), "a") as f:
            f.write(json.dumps(rec) + "\n")
        return answer(body, headers or {})
    runtime.post = post
    return runtime


def _setup(floored, tmp_path, cost):
    runtime = _gateway_writing(str(tmp_path), cost)
    services = _turn_on(floored, _openclaw(runtime))
    return services, runtime


def _owner(floored):
    return floored.owner().get("/guild-next/api/v1/session").get_json().get("user", {}).get("username") or "robert"


def test_four_turns_measure_long_against_fresh_context(floored, tmp_path):
    services, runtime = _setup(floored, tmp_path, 0.01)
    lines = []
    result = cost_probe.run(services, conversations_of(services), principal="robert", label="Robert", turns=4,
                            usage_dir=str(tmp_path), out=lines.append, sleep=lambda s: None)
    users = [s["body"]["user"] for s in runtime.sent]
    assert len(users) == 4 and users[0] == users[1] == users[3] and users[2] != users[0]   # thread, thread, fresh, thread
    rows = result["rows"]
    assert [r["where"] for r in rows] == ["thread", "thread", "new", "thread"]
    assert rows[0]["conversation"] == LEGACY_ID and rows[2]["conversation"] == result["fresh"]
    assert rows[1]["input_tokens"] > rows[2]["input_tokens"]                  # the long context costs more
    assert all(r["calls"] == 1 and r["cost_usd"] == 0.01 for r in rows)
    assert any("long (turn 2) minus fresh (turn 3) input tokens: 400" in l for l in lines)
    assert round(result["spent"], 4) == 0.04
    for s in runtime.sent:                                                    # the notes are marked
        assert s["body"]["messages"][0]["content"].count("[cost probe]") == 1


def test_it_stops_before_the_next_turn_once_past_the_cap(floored, tmp_path):
    services, runtime = _setup(floored, tmp_path, 0.6)
    lines = []
    result = cost_probe.run(services, conversations_of(services), principal="robert", label="Robert", turns=4,
                            cap=5.0, usage_dir=str(tmp_path), out=lines.append, sleep=lambda s: None)
    assert len(runtime.sent) == 2 and len(result["rows"]) == 2               # 0.6 + 0.6 > $1: turn 3 never sent
    assert any(l.startswith("STOP: spent $1.2000, over the $1.00 cap") for l in lines)    # the cap is never above $1


def test_it_refuses_without_yes_spend_or_with_turns_off(floored, tmp_path, capsys):
    services, runtime = _setup(floored, tmp_path, 0.01)
    assert cost_probe.main(["--turns", "3"], services=services) == 2
    assert "Refused" in capsys.readouterr().out and runtime.sent == []
    services.mc_turns = False
    assert cost_probe.main(["--yes-spend"], services=services) == 3
    assert runtime.sent == []


def test_the_wrapper_runs_only_inside_the_portal_container():
    text = (REPO / "scripts/staging/mc_cost_probe.sh").read_text()
    assert "docker exec -i minimoi-portal python -m minimoi_portal.mc_cost_probe" in text
    body = "\n".join(l for l in text.splitlines() if not l.startswith("#"))
    assert "dev.minimoi" not in body and "curl" not in body and "http" not in body


def test_the_entry_point_uses_the_running_portals_own_services(capsys):
    text = (REPO / "minimoi_portal/mc_cost_probe.py").read_text()
    assert 'portal_app.app.extensions["guild_ui_next"]["services"]' in text and "cost_probe.main(argv, services=services)" in text
    assert cost_probe.main(["--yes-spend"], services=None) == 4          # never builds its own services
    assert "mc_cost_probe.sh" in capsys.readouterr().out
