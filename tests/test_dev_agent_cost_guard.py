"""Cost guards for the Guild dev agent (xAI spend incident, 2026-09-26).

The dev agent once sent every .py/.json file created under _working/
(worktrees, virtualenvs, archives) to a reasoning model: ~18,000 paid calls in
three days. These tests pin the guards. No network: the OpenAI client,
keyring, requests and every downstream side effect (DB, Telegram, memory,
agenda) are replaced with mocks, and BASE_DIR points at a temp directory.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import domains.guild.agents.dev_agent as dev_agent

_ORIGINAL_NOTIFY = dev_agent.notify_parallel


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _fake_response(*_args, **_kwargs):
    body = {"doc_type": "spec", "summary": "A spec", "agent_source": "claude_code",
            "spec_title": "Test spec", "referenced_files": []}
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(body)))],
        usage=SimpleNamespace(prompt_tokens=412, completion_tokens=37,
                              completion_tokens_details=SimpleNamespace(reasoning_tokens=0)),
    )


class FakeTimer:
    """Stands in for threading.Timer: records timers, fires them on demand."""
    created: list = []

    def __init__(self, interval, fn):
        self.interval, self.fn = interval, fn
        self.cancelled = self.started = False
        FakeTimer.created.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    @classmethod
    def fire_all(cls):
        for t in list(cls.created):
            if t.started and not t.cancelled:
                t.fn()


@pytest.fixture
def agent(tmp_path, monkeypatch):
    base = tmp_path.resolve()
    receipts = base / "logs" / "devagent_model_calls.jsonl"
    monkeypatch.setattr(dev_agent, "BASE_DIR", base)
    monkeypatch.setattr(dev_agent, "_cfg", {})
    monkeypatch.setattr(dev_agent, "_budget", dev_agent.ModelCallBudget())
    monkeypatch.setattr(dev_agent, "_classified_by_hash", {})
    monkeypatch.setattr(dev_agent, "_last_processed", {})
    monkeypatch.setattr(dev_agent, "_pending_events", {})
    monkeypatch.setattr(dev_agent, "_state", {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "events_processed": 0, "docs_archived": 0,
        "model_calls": 0, "model_calls_skipped_rate_limit": 0,
    })
    # Downstream side effects: never touch DB, Telegram, CoS, memory or agenda.
    for name in ("log_to_db", "append_to_memory", "notify_parallel",
                 "maybe_archive_superseded", "_notify_incomplete",
                 "_send_telegram", "_write_agenda_direct"):
        monkeypatch.setattr(dev_agent, name, MagicMock(name=name))
    monkeypatch.setattr(dev_agent, "requests", MagicMock(name="requests"))
    monkeypatch.setattr(dev_agent, "keyring",
                        MagicMock(get_password=MagicMock(return_value="test-key")))
    client = MagicMock(name="xai_client")
    client.chat.completions.create.side_effect = _fake_response
    monkeypatch.setattr(dev_agent, "OpenAI", MagicMock(return_value=client))
    FakeTimer.created = []
    monkeypatch.setattr(dev_agent.threading, "Timer", FakeTimer)

    def write(rel: str, content: str) -> str:
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return str(p)

    return SimpleNamespace(base=base, client=client, receipts=receipts, write=write,
                           handler=dev_agent.DesignDocHandler())


def _event(path):
    return SimpleNamespace(src_path=path, is_directory=False)


def _calls(agent):
    return agent.client.chat.completions.create.call_count


# ── 1. Exclusions ─────────────────────────────────────────────────────────────

EXCLUDED = [
    "_working/worktrees/foo/.venv/lib/python3.11/site-packages/x/__init__.py",
    "_working/worktrees/foo/_working/spec_copy.md",
    "_working/venv/lib/spec_a.md",
    "_working/env/spec_b.md",
    "_working/node_modules/pkg/spec_c.md",
    "_working/__pycache__/spec_d.md",
    "_working/.git/spec_e.md",
    "_working/.pytest_cache/spec_f.md",
    "_working/deployments/2026-09-25/spec_g.md",
    "_working/m0-preservation/branch/spec_h.md",
    "_working/tour-capture/spec_i.md",
    "_working/guild-prototype-evidence/spec_j.md",
    "_working/dist/spec_k.md",
    "_working/build/spec_l.md",
    "_working/trash/spec_m.md",
    "_working/archive/2026-09/spec_n.md",
]


@pytest.mark.parametrize("rel", EXCLUDED)
def test_excluded_dirs_never_reach_the_model(agent, rel):
    path = agent.write(rel, f"# Spec in {rel}\n")
    assert dev_agent.is_excluded(path)
    agent.handler.on_created(_event(path))
    agent.handler.on_modified(_event(path))
    assert FakeTimer.created == []
    assert dev_agent.process_doc(path, "doc_created") is None
    assert _calls(agent) == 0


def test_exclusion_matches_directory_components_only(agent):
    for rel in ("_working/spec_ok.md", "_working/environment/spec_ok.md",
                "_working/building/spec_ok.md", "docs/specs/spec_ok.md"):
        assert not dev_agent.is_excluded(str(agent.base / rel)), rel


def test_exclusion_list_is_configurable(agent):
    dev_agent._cfg["watch_exclude_dirs"] = ["scratch"]
    assert dev_agent.is_excluded(str(agent.base / "_working/scratch/spec_x.md"))
    assert not dev_agent.is_excluded(str(agent.base / "_working/build/spec_x.md"))


# ── 2. Code and config files never reach a model ─────────────────────────────

@pytest.mark.parametrize("rel,doc_type", [
    ("_working/tools/exceptions.py", "code"),
    ("_working/tools/schema.sql", "code"),
    ("_working/tools/HANDOFF_STATE.json", "config"),
])
def test_code_and_config_files_make_no_model_call(agent, rel, doc_type):
    path = agent.write(rel, "import os\n# spec handoff design\n")
    agent.handler.on_created(_event(path))
    assert FakeTimer.created == []            # no read, no timer
    cl = dev_agent.process_doc(path, "doc_created")
    assert cl["doc_type"] == doc_type
    assert dev_agent.classify_doc(path, "x")["doc_type"] == doc_type
    assert _calls(agent) == 0
    dev_agent.log_to_db.assert_not_called()
    dev_agent.notify_parallel.assert_not_called()


# ── 3. Spec docs: one call, configured model, explicit effort ────────────────

def test_spec_md_makes_one_call_with_default_cheap_model(agent):
    path = agent.write("_working/spec_alpha.md", "# Alpha\n## Definition of Done\n## Commit\n")
    cl = dev_agent.process_doc(path, "doc_created")
    assert cl["doc_type"] == "spec"
    assert _calls(agent) == 1
    kwargs = agent.client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "grok-4.3"
    assert kwargs["reasoning_effort"] == "none"
    # Queue behaviour for spec docs is unchanged: logged and notified.
    dev_agent.log_to_db.assert_called_once()
    assert dev_agent.log_to_db.call_args.kwargs["build_status"] == "spec_ready"
    dev_agent.notify_parallel.assert_called_once()


def test_spec_md_uses_configured_model_and_effort(agent):
    dev_agent._cfg.update({"classify_model": "grok-test-model",
                           "classify_reasoning_effort": "low"})
    path = agent.write("_working/build_plan_beta.md", "# Beta\n")
    dev_agent.process_doc(path, "doc_created")
    kwargs = agent.client.chat.completions.create.call_args.kwargs
    assert (kwargs["model"], kwargs["reasoning_effort"]) == ("grok-test-model", "low")


# ── 4. Rate limit and daily cap ───────────────────────────────────────────────

def test_burst_of_100_specs_is_capped_per_minute(agent, caplog):
    paths = [agent.write(f"_working/spec_burst_{i:03d}.md", f"# Burst {i}\n") for i in range(100)]
    with caplog.at_level(logging.WARNING, logger="devagent"):
        for p in paths:
            agent.handler.on_modified(_event(p))
        FakeTimer.fire_all()
    assert _calls(agent) == 6
    assert dev_agent._state["model_calls_skipped_rate_limit"] == 94
    # Dropped, not queued: nothing pending, nothing retried later.
    assert dev_agent._pending_events == {}
    assert _calls(agent) == 6
    # Logged once for the window, not 94 times.
    assert sum("per-minute limit" in r.getMessage() for r in caplog.records) == 1
    # Rate-limited events have no downstream footprint.
    assert dev_agent.log_to_db.call_count == 6


def test_rate_limited_result_is_marked(agent):
    dev_agent._cfg["model_calls_per_minute"] = 1
    a = agent.write("_working/spec_a.md", "# A\n")
    b = agent.write("_working/spec_b.md", "# B\n")
    dev_agent.process_doc(a, "doc_created")
    cl = dev_agent.process_doc(b, "doc_created")
    assert cl["rate_limited"] is True
    assert cl["doc_type"] == "unclassified"
    assert cl["summary"] == "unclassified: rate-limited"
    # The path can be classified on a later save once the window allows it.
    assert str(b) not in dev_agent._last_processed


def test_daily_cap(agent):
    dev_agent._cfg.update({"model_calls_per_minute": 1000, "model_calls_per_day": 60})
    for i in range(100):
        dev_agent.process_doc(agent.write(f"_working/spec_day_{i:03d}.md", f"# Day {i}\n"),
                              "doc_created")
    assert _calls(agent) == 60
    assert dev_agent._state["model_calls_skipped_rate_limit"] == 40


def test_budget_windows_roll_over(agent):
    dev_agent._cfg.update({"model_calls_per_minute": 6, "model_calls_per_day": 10})
    budget = dev_agent.ModelCallBudget()
    t0 = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc).timestamp()
    assert [budget.try_acquire(t0) for _ in range(7)] == [None] * 6 + ["minute"]
    assert [budget.try_acquire(t0 + 61) for _ in range(5)] == [None] * 4 + ["day"]
    assert budget.try_acquire(t0 + 86400) is None          # next UTC day


def test_daily_cap_survives_restart_via_receipts(agent):
    dev_agent._cfg["model_calls_per_day"] = 60
    agent.receipts.parent.mkdir(parents=True)
    today = datetime.now(timezone.utc).isoformat()
    lines = [json.dumps({"ts": today, "outcome": "ok"})] * 60
    lines.append(json.dumps({"ts": "2020-01-01T00:00:00+00:00", "outcome": "ok"}))
    agent.receipts.write_text("\n".join(lines) + "\n")
    assert dev_agent._budget.seed_from_receipts(agent.receipts) == 60
    cl = dev_agent.process_doc(agent.write("_working/spec_after.md", "# After\n"), "doc_created")
    assert cl["rate_limited"] and cl["limit"] == "day"
    assert _calls(agent) == 0


def test_reservation_is_durable_before_the_paid_request(agent):
    seen = []

    def create(*a, **k):
        # At request time the reservation must already be on disk.
        seen.append([json.loads(l) for l in agent.receipts.read_text().splitlines()])
        return _fake_response()

    agent.client.chat.completions.create.side_effect = create
    dev_agent.process_doc(agent.write("_working/spec_reserve.md", "# R\n"), "doc_created")
    assert len(seen) == 1 and [r["kind"] for r in seen[0]] == ["reservation"]


def test_unwritable_receipt_log_means_no_paid_call(agent, caplog):
    # The receipt path is a directory: nothing can be appended to it.
    agent.receipts.mkdir(parents=True)
    path = agent.write("_working/spec_no_log.md", "# No log\n")
    with caplog.at_level("ERROR", logger=dev_agent.log.name):
        cl = dev_agent.process_doc(path, "doc_created")
    assert _calls(agent) == 0                            # fail closed
    assert cl["doc_type"] == "spec"                      # filename heuristic
    assert dev_agent._state["model_calls_refused_unrecorded"] == 1
    assert "no paid call made" in caplog.text


def test_outcome_write_failure_still_counts_after_restart(agent, monkeypatch):
    dev_agent._cfg["model_calls_per_day"] = 2
    real = dev_agent._append_durable
    # Reservations succeed; every outcome write fails (e.g. disk full mid-call).
    monkeypatch.setattr(dev_agent, "_append_durable",
                        lambda rec: real(rec) if rec.get("kind") == "reservation" else False)
    for i in range(2):
        dev_agent.process_doc(agent.write(f"_working/spec_o{i}.md", f"# O{i}\n"), "doc_created")
    assert _calls(agent) == 2
    # Restart: a fresh budget seeded from disk still sees both calls.
    monkeypatch.setattr(dev_agent, "_budget", dev_agent.ModelCallBudget())
    assert dev_agent._budget.seed_from_receipts(agent.receipts) == 2
    cl = dev_agent.process_doc(agent.write("_working/spec_o2.md", "# O2\n"), "doc_created")
    assert cl["rate_limited"] and cl["limit"] == "day"
    assert _calls(agent) == 2


def test_crash_between_request_and_outcome_still_counts(agent):
    # A process that died after reserving (no outcome line) still used the call.
    agent.receipts.parent.mkdir(parents=True)
    today = datetime.now(timezone.utc).isoformat()
    agent.receipts.write_text(json.dumps({"ts": today, "kind": "reservation", "id": "x"}) + "\n")
    assert dev_agent._budget.seed_from_receipts(agent.receipts) == 1


def test_outcome_lines_are_not_double_counted(agent):
    for i in range(3):
        dev_agent.process_doc(agent.write(f"_working/spec_d{i}.md", f"# D{i}\n"), "doc_created")
    lines = agent.receipts.read_text().strip().splitlines()
    assert len(lines) == 6                               # 3 reservations + 3 outcomes
    fresh = dev_agent.ModelCallBudget()
    assert fresh.seed_from_receipts(agent.receipts) == 3


# ── 5. Dedupe and debounce ───────────────────────────────────────────────────

def test_identical_content_is_classified_once(agent):
    content = "# Same spec\n## Definition of Done\n"
    a = agent.write("_working/spec_same_a.md", content)
    b = agent.write("docs/specs/spec_same_b.md", content)
    first = dev_agent.process_doc(a, "doc_created")
    second = dev_agent.process_doc(b, "doc_created")
    again = dev_agent.process_doc(a, "doc_modified")        # same path, same content
    assert _calls(agent) == 1
    assert first["doc_type"] == second["doc_type"] == "spec"
    assert again is None


def test_same_path_within_10s_is_one_event(agent, monkeypatch):
    fired = []
    monkeypatch.setattr(dev_agent, "process_doc", lambda p, e: fired.append((p, e)))
    path = str(agent.base / "_working/spec_saves.md")
    for _ in range(5):
        agent.handler.on_modified(_event(path))
    other = str(agent.base / "_working/spec_other.md")
    agent.handler.on_created(_event(other))
    assert all(t.interval == 10 for t in FakeTimer.created)
    live = [t for t in FakeTimer.created if not t.cancelled]
    assert len(live) == 2
    FakeTimer.fire_all()
    assert fired == [(path, "doc_modified"), (other, "doc_created")]


# ── 6. Receipt per paid call ─────────────────────────────────────────────────

def test_receipt_line_written_without_content(agent):
    secret = "SECRET-CONTENT-MARKER-7f3a"
    path = agent.write("_working/spec_receipt.md", f"# Receipt\n{secret}\n")
    dev_agent.process_doc(path, "doc_created")
    text = agent.receipts.read_text()
    assert secret not in text
    lines = text.strip().splitlines()
    assert len(lines) == 2                               # reservation, then outcome
    res, rec = json.loads(lines[0]), json.loads(lines[1])
    assert set(res) == {"ts", "kind", "id", "path", "model", "reasoning_effort"}
    assert res["kind"] == "reservation" and res["path"] == "_working/spec_receipt.md"
    assert rec["kind"] == "outcome" and rec["reservation_id"] == res["id"]
    assert rec["path"] == "_working/spec_receipt.md"
    assert rec["model"] == "grok-4.3"
    assert rec["reasoning_effort"] == "none"
    assert (rec["input_tokens"], rec["output_tokens"]) == (412, 37)
    assert rec["outcome"] == "ok"
    assert set(rec) == {"ts", "kind", "reservation_id", "path", "model", "reasoning_effort",
                        "input_tokens", "output_tokens", "reasoning_tokens", "outcome"}


def test_failed_call_still_writes_receipt_and_falls_back(agent):
    agent.client.chat.completions.create.side_effect = RuntimeError("boom")
    path = agent.write("_working/spec_fail.md", "# Fail\n")
    cl = dev_agent.process_doc(path, "doc_created")
    assert cl["doc_type"] == "spec"                      # filename heuristic
    rec = json.loads(agent.receipts.read_text().strip().splitlines()[-1])
    assert rec["outcome"] == "error: RuntimeError"
    assert rec["input_tokens"] is None


# ── 7. Startup guard ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("watch_path", [".", "./", "../"])
def test_startup_guard_refuses_repo_root(agent, monkeypatch, caplog, watch_path):
    observer = MagicMock(name="Observer")
    monkeypatch.setattr(dev_agent, "Observer", observer)
    dev_agent._cfg["watch_paths"] = [watch_path]
    with caplog.at_level(logging.INFO, logger="devagent"):
        assert dev_agent._start_watcher() is False
    observer.assert_not_called()
    assert any("Refusing to start watcher" in r.getMessage() for r in caplog.records)
    # Effective settings are logged before the decision.
    assert any("Excluded dirs:" in r.getMessage() for r in caplog.records)


def test_startup_guard_refuses_home_directory(agent):
    assert dev_agent._unsafe_watch_path(Path.home().resolve())
    assert dev_agent._unsafe_watch_path(Path("/Users/someone"))
    assert dev_agent._unsafe_watch_path(agent.base / "_working") is None


# ── CoS event URL (configured backend, not hard-coded :8769) ─────────────────

def test_dev_agent_posts_cos_events_to_configured_backend(agent, monkeypatch):
    monkeypatch.setenv("COS_BACKEND", "http://cos.test:18769/")
    # The fixture mocks notify_parallel; exercise the real one here.
    monkeypatch.setattr(dev_agent, "notify_parallel", _ORIGINAL_NOTIFY)
    dev_agent._cfg["telegram_notify"] = False
    dev_agent.notify_parallel("_working/spec_x.md", "doc_created", "spec", "summary")
    post = dev_agent.requests.post
    post.assert_called_once()
    assert post.call_args.args[0] == "http://cos.test:18769/event"
    assert post.call_args.kwargs["timeout"] == 2


def test_dev_agent_cos_url_default_matches_portal_config(monkeypatch):
    monkeypatch.delenv("COS_BACKEND", raising=False)
    assert dev_agent._cos_event_url() == "http://localhost:8769/event"


OWNER = {"username": "owner", "tier": "owner", "display_name": "Robert", "auth_id": 1}


@pytest.fixture
def owner_portal(portal_client, monkeypatch):
    import minimoi_portal.app as portal_app
    monkeypatch.setattr(portal_app, "_guild_db_execute", MagicMock())
    monkeypatch.setattr(portal_app._cfg, "COS_BACKEND", "http://cos.test:18769/")
    with portal_client.session_transaction() as session:
        session["user"] = OWNER
    yield portal_client
    with portal_client.session_transaction() as session:
        session.pop("user", None)


def test_portal_career_close_posts_to_configured_cos_backend(owner_portal, monkeypatch):
    import requests
    post = MagicMock()
    monkeypatch.setattr(requests, "post", post)
    resp = owner_portal.post("/guild/career/positions/7/status",
                             data={"status": "closed", "close_reason": "accepted"})
    assert resp.status_code == 302
    post.assert_called_once()
    assert post.call_args.args[0] == "http://cos.test:18769/event"
    assert post.call_args.kwargs["timeout"] == 2


def test_portal_cos_post_failure_does_not_break_request(owner_portal, monkeypatch, caplog):
    import requests
    monkeypatch.setattr(requests, "post", MagicMock(side_effect=ConnectionError("down")))
    with caplog.at_level(logging.WARNING):
        resp = owner_portal.post("/guild/career/positions/7/status",
                                 data={"status": "closed", "close_reason": "accepted"})
    assert resp.status_code == 302
    assert any("CoS event post failed" in r.getMessage() for r in caplog.records)
