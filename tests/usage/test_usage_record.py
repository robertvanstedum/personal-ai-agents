"""usage-record U1: the standard record, its file-first store and the gateway
recorder (design note USAGE_RECORD_DESIGN_NOTE_2026-09-29.md, approved)."""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "usage"))
recorder_mod = importlib.import_module("litellm_recorder")
ur = recorder_mod.usage_record          # the same module object the recorder writes through


def _rec(**over):
    base = {"emitter": "gateway", "actor": "mc", "kind": "model", "route": "minimoi-mc-agent", "status": "ok",
            "cost_source": "price_table", "occurred_at": "2026-09-29T10:00:00.000+00:00", "env": "staging",
            "input_tokens": 50, "output_tokens": 5, "cost_usd": 0.000075}
    base.update(over)
    return base


# ── the record ────────────────────────────────────────────────────────────────

def test_a_valid_record_is_normalized_and_unknown_numbers_stay_null():
    out = ur.validate(_rec(cached_tokens=None))
    assert out["v"] == 1 and out["cached_tokens"] is None and out["output_tokens"] == 5
    assert set(out) == ur.FIELDS
    assert ur.validate(_rec(input_tokens=None, output_tokens=None))["output_tokens"] is None


@pytest.mark.parametrize("extra", [{"prompt": "hello"}, {"messages": []}, {"response": "x"}, {"api_key": "x"}])
def test_unknown_fields_are_refused(extra):
    with pytest.raises(ValueError, match="unknown fields"):
        ur.validate(_rec(**extra))


@pytest.mark.parametrize("field,value", [
    ("key_ref", "sk-abcdefghijklmnop"), ("route", "Bearer abc.def"), ("model", "xai-" + "a" * 20),
    ("error_class", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."), ("correlation_id", "tvly-abcdefghij"),
])
def test_credential_shaped_values_are_refused(field, value):
    with pytest.raises(ValueError, match="credential") as err:
        ur.validate(_rec(**{field: value}))
    assert value not in str(err.value)                   # never echoed


def test_a_value_equal_to_a_secret_in_the_environment_is_refused(monkeypatch):
    monkeypatch.setenv("LITELLM_MASTER_KEY", "probe-master-0123456789abcdef")
    with pytest.raises(ValueError, match="credential"):
        ur.validate(_rec(key_ref="probe-master-0123456789abcdef"))


@pytest.mark.parametrize("over", [{"status": "maybe"}, {"kind": "chat"}, {"emitter": "anything"},
                                  {"output_tokens": -1}, {"output_tokens": 1.5}, {"cost_usd": -0.1},
                                  {"units": {"Searches": 1}}, {"actor": None}])
def test_bad_values_are_refused(over):
    with pytest.raises(ValueError):
        ur.validate(_rec(**over))


# ── the store ─────────────────────────────────────────────────────────────────

def test_append_writes_one_owner_only_line_per_record_in_a_monthly_file(tmp_path):
    ur.append(_rec(), str(tmp_path))
    ur.append(_rec(actor="cos", route="minimoi-cos-agent"), str(tmp_path))
    path = tmp_path / "usage-2026-09.jsonl"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    assert [l["actor"] for l in lines] == ["mc", "cos"]
    assert [r["actor"] for r in ur.read(str(tmp_path))] == ["mc", "cos"]


def test_record_is_fire_and_forget_and_never_raises(tmp_path, caplog, monkeypatch):
    monkeypatch.delenv("MINIMOI_USAGE_DIR", raising=False)
    assert ur.record(**_rec()) is False                                       # no store configured (production): nothing
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("x")                                                   # writes into it fail
    assert ur.record(folder=str(blocked), **_rec()) is True                   # queued; the failure is the writer's
    ur.record(folder=str(tmp_path), **_rec(prompt="never"))                   # invalid: dropped by the writer
    ur.flush()
    assert "usage record not written" in caplog.text and "never" not in caplog.text
    ur.record(folder=str(tmp_path), **_rec())
    ur.flush()
    assert len(ur.read(str(tmp_path))) == 1


def test_a_full_queue_drops_without_blocking(tmp_path, monkeypatch):
    import queue as q
    writer = ur._Writer()
    writer.q = q.Queue(maxsize=1)
    writer.thread = type("T", (), {"is_alive": lambda self: True})()          # a stuck writer
    assert writer.put(_rec(), str(tmp_path)) is True
    assert writer.put(_rec(), str(tmp_path)) is False


def test_read_skips_torn_and_foreign_lines(tmp_path):
    ur.append(_rec(), str(tmp_path))
    with open(tmp_path / "usage-2026-09.jsonl", "a") as f:
        f.write('{"v": 99}\n{"torn": ')
    assert len(ur.read(str(tmp_path))) == 1
    assert ur.read(str(tmp_path / "missing")) == []


# ── the gateway recorder ──────────────────────────────────────────────────────

T0 = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)


def _kwargs(route, alias=None, call_id="call-1", ok=True, cost=0.000075, deployment="mc-anthropic-haiku"):
    return {"litellm_call_id": call_id, "litellm_params": {"model_info": {"id": deployment, "fallback_position": None}},
            "standard_logging_object": {
                "litellm_call_id": call_id, "model_group": route, "model_id": deployment,
                "custom_llm_provider": "anthropic", "model": "claude-haiku-4-5-20251001",
                "prompt_tokens": 50, "completion_tokens": 5, "response_cost": cost if ok else 0.0,
                "call_type": "acompletion",
                "metadata": {"user_api_key_alias": alias, "usage_object": {"prompt_tokens_details": {"cached_tokens": 0}}},
                "error_information": None if ok else {"error_code": "500", "error_class": "InternalServerError"},
                "messages": [{"role": "user", "content": "SECRET NOTE TEXT"}], "response": "SECRET ANSWER"}}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMOI_USAGE_DIR", str(tmp_path))
    monkeypatch.setenv("MINIMOI_ENV", "staging")
    monkeypatch.setattr(recorder_mod, "REFUSAL_DELAY_S", 0.01)
    return tmp_path


def _records(folder):
    ur.flush()
    return ur.read(str(folder))


def test_mc_and_cos_calls_both_appear_with_actor_tokens_and_cost(store):
    rec = recorder_mod.UsageRecorder()
    asyncio.run(rec.async_log_success_event(_kwargs("minimoi-mc-agent", alias="mc-agent-3f9a1c"), None, T0, T0 + timedelta(seconds=1.2)))
    asyncio.run(rec.async_log_success_event(_kwargs("minimoi-cos-agent", call_id="call-2", deployment="cos-anthropic-haiku-primary"),
                                            None, T0, T0 + timedelta(seconds=2)))
    got = _records(store)
    assert [(r["actor"], r["route"], r["status"]) for r in got] == [("mc", "minimoi-mc-agent", "ok"),
                                                                     ("cos", "minimoi-cos-agent", "ok")]
    mc = got[0]
    assert mc["key_ref"] == "mc-agent-3f9a1c" and mc["input_tokens"] == 50 and mc["output_tokens"] == 5
    assert mc["cost_usd"] == 0.000075 and mc["cost_source"] == "price_table" and mc["latency_ms"] == 1200.0
    assert mc["env"] == "staging" and mc["emitter"] == "gateway" and mc["deployment_id"] == "mc-anthropic-haiku"
    text = (store / os.listdir(store)[0]).read_text()
    assert "SECRET" not in text                                                # no content, ever


def test_a_provider_failure_is_recorded_once_even_when_the_refusal_hook_also_fires(store):
    rec = recorder_mod.UsageRecorder()

    async def both():
        exc = type("InternalServerError", (Exception,), {"status_code": 500})()
        await rec.async_post_call_failure_hook({"model": "minimoi-mc-agent", "litellm_call_id": "call-9"}, exc, None)
        await rec.async_log_failure_event(_kwargs("minimoi-mc-agent", alias="mc-agent-3f9a1c", call_id="call-9", ok=False),
                                          None, T0, T0 + timedelta(seconds=1))
        await asyncio.sleep(0.05)
    asyncio.run(both())
    got = _records(store)
    assert len(got) == 1 and got[0]["status"] == "error" and got[0]["http_status"] == 500
    assert got[0]["cost_usd"] is None and got[0]["cost_source"] == "none" and got[0]["output_tokens"] is None


def test_a_refusal_before_any_deployment_is_recorded_as_refused(store):
    rec = recorder_mod.UsageRecorder()
    key = type("Key", (), {"key_alias": "mc-agent-3f9a1c"})()

    async def refuse():
        budget = type("BudgetExceededError", (Exception,), {"status_code": 429})()
        out = await rec.async_post_call_failure_hook({"model": "minimoi-mc-agent"}, budget, key)
        denied = type("ProxyException", (Exception,), {"code": "401"})()
        await rec.async_post_call_failure_hook({"model": "minimoi-cos-agent"}, denied, key)
        await asyncio.sleep(0.05)
        return out
    assert asyncio.run(refuse()) is None                                        # never changes what the caller sees
    got = _records(store)
    assert [(r["status"], r["http_status"], r["error_class"], r["actor"]) for r in got] == [
        ("refused", 429, "BudgetExceededError", "mc"), ("refused", 401, "ProxyException", "mc")]


def test_a_broken_event_never_raises_into_litellm(store, caplog):
    rec = recorder_mod.UsageRecorder()
    asyncio.run(rec.async_log_success_event({"standard_logging_object": {"model_group": "sk-" + "x" * 20}}, None, T0, T0))
    asyncio.run(rec.async_log_success_event(None, None, None, None))
    assert _records(store) == []


def test_actor_of():
    assert recorder_mod.actor_of("mc-agent-1", "minimoi-cos-agent") == "mc"        # the key decides first
    assert recorder_mod.actor_of("cos-agent-1", None) == "cos"
    assert recorder_mod.actor_of(None, "minimoi-cos-web-search") == "cos"
    assert recorder_mod.actor_of(None, "gpt-4o") == "unknown"


# ── staging only ──────────────────────────────────────────────────────────────

def test_production_never_builds_or_loads_the_usage_recorder_yet():
    """The day a production image or config uses services/usage/, this fails,
    and scripts/ci/classify_release.py must map it to a real service."""
    users = []
    for path in list((REPO / "docker").glob("Dockerfile.*")) + [
            REPO / "docker-compose.prod.yml", REPO / "docker-compose.yml",
            REPO / "services/model_gateway/litellm.prod.yaml", REPO / "services/model_gateway/litellm.yaml",
            REPO / ".github/workflows/deploy.yml", REPO / "scripts/operations/deploy_scoped_release.sh"]:
        text = path.read_text()
        if "services/usage" in text or "usage_recorder" in text or "usage_record" in text:
            users.append(path.name)
    assert users == []
    staging = (REPO / "services/model_gateway/litellm.staging.yaml").read_text()
    assert "usage_recorder.usage_recorder" in staging
