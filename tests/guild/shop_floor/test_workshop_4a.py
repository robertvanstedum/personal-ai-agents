"""Guild 1.1 dev, slice 4a: the focused Workshop screen, opened from a queue
item. Read only and model free: everything comes from the synced workshop
files, the Build Queue and the usage store. Honest states: missing, unreadable
or stale readings are "unknown", never "nothing running". The relay is mocked
(FakeRuntime) to prove no status refresh reaches Master Craftsman."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored  # noqa: F401  (pytest fixtures)
from test_mc_turns import FakeRuntime, _openclaw, _turn_on

from minimoi_portal.workshop.observer import Observation, health_event
from minimoi_portal.workshop.record import Workshop

API = "/guild-next/api/v1"
PAGE = "/guild-next/guild/workshop"


def _obs(**over):
    base = dict(observed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), memory_free_pct=46.0,
                swap_used_gb=1.2, disk_free_gb=80.0, load_1m=2.1,
                clients=[{"kind": "codex", "label": "Codex CLI", "pid": 11, "elapsed": "05:00", "counted": True},
                         {"kind": "codex-desktop", "label": "Codex desktop (ChatGPT app)", "pid": 12, "elapsed": "09:00",
                          "counted": False}], clients_known=True)
    base.update(over)
    return Observation(**base)


@pytest.fixture
def ws(floored, tmp_path, monkeypatch):
    root = tmp_path / "workshops"
    monkeypatch.setenv("MINIMOI_WORKSHOPS_DIR", str(root))
    monkeypatch.setenv("MINIMOI_WORKSHOP_ID", "mac")
    monkeypatch.setenv("MINIMOI_USAGE_DIR", str(tmp_path / "usage"))
    runtime = FakeRuntime()
    _turn_on(floored, _openclaw(runtime))
    floored.extra.update(runtime=runtime, root=root, usage=tmp_path / "usage")
    return floored


def _record(portal, *events, obs=None):
    w = Workshop(str(portal.extra["root"]), "mac")
    if obs is not False:
        w.append(health_event(obs or _obs(), "mac"))
    for e in events:
        w.append({"workshop": "mac", **e})
    return w


def test_the_workshop_opens_from_a_queue_item_with_its_scope_and_the_host(ws):
    _record(ws, {"actor": "claude-code", "kind": "started", "item": "queue:12", "stage": "build",
                 "text": "Slice 4a tests", "next_actor": "codex"},
            {"actor": "claude-code", "kind": "needs_you", "item": "queue:12", "text": "Pick the refresh cadence",
             "next_actor": "robert"},
            {"actor": "claude-code", "kind": "next", "item": "spec:streaming", "text": "Streaming S1 after review"})
    client = ws.owner()
    item_page = client.get("/guild-next/guild/build/items/12").get_data(as_text=True)
    assert 'href="/guild-next/guild/workshop?item=12"' in item_page and "data-open-workshop" in item_page
    page = client.get(f"{PAGE}?item=12").get_data(as_text=True)
    assert "#12 Floor API" in page and "Approved for build" in page and "spec_floor_api.md" in page
    assert 'data-verdict="tight"' in page and "a new run would share the host" in page
    assert "1 agent session running on this Mac (outside Docker)" in page and "Codex CLI" in page
    assert "Also running, not counted as build sessions: Codex desktop (ChatGPT app)." in page
    assert "tight at 1" in page and "Limits: memory free tight under 20%, blocked under 10%" in page
    assert "Next actor: <strong>robert</strong>" in page                  # the item's latest event
    assert "Pick the refresh cadence" in page                              # Needs you, from the workshop
    assert "#7 Queue lock hardening" in page and "Streaming S1 after review" in page   # queued work
    assert "Headroom on this Mac (outside Docker): memory free 46.0% · swap used 1.2 GB · disk free 80.0 GB" in page
    assert "Workshop</a>" in page or ">Workshop<" in page                  # in the section nav


def test_an_item_not_approved_says_so_and_a_missing_item_is_named(ws):
    _record(ws)
    client = ws.owner()
    assert "Not approved for build: the queue says" in client.get(f"{PAGE}?item=31").get_data(as_text=True)
    assert "Queue item #999 is not in the Build Queue" in client.get(f"{PAGE}?item=999").get_data(as_text=True)


@pytest.mark.parametrize("setup, reason", [
    ("missing", "no workshop record synced yet"),
    ("unreadable", "the workshop record could not be read"),
    ("stale", "old (over 15 min): it said ok"),
    ("no-host", "no host reading yet"),
])
def test_missing_unreadable_or_stale_is_unknown_never_nothing_running(ws, setup, reason):
    root = ws.extra["root"]
    if setup == "unreadable":
        _record(ws)
        (root / "mac" / "state.json").write_text("{ torn")
    elif setup == "stale":
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
        _record(ws, obs=_obs(observed_at=old, clients=[]))
    elif setup == "no-host":
        _record(ws, {"actor": "codex", "kind": "progress", "item": "queue:12", "text": "reviewing"}, obs=False)
    page = ws.owner().get(f"{PAGE}?item=12").get_data(as_text=True)
    assert 'data-verdict="unknown"' in page and reason in page
    assert "Running agents on this Mac (outside Docker) unknown" in page and "No agent session running" not in page
    assert "Headroom unknown" in page
    assert "Nothing needs you here" not in page                           # the workshop's needs are not known
    assert ("Workshop needs unknown" in page) if setup in ("missing", "unreadable") else ("Workshop needs may be out of date" in page)
    body = ws.owner().get(f"{API}/workshop?item=12").get_json()
    assert body["admission"]["verdict"] == "unknown" and body["runs"]["known"] is False and body["runs"]["runs"] == []


def test_a_fresh_reading_with_no_clients_says_nothing_is_running(ws):
    _record(ws, obs=_obs(clients=[]))
    body = ws.owner().get(f"{API}/workshop").get_json()
    assert body["admission"]["verdict"] == "ok" and body["runs"]["known"] is True and body["runs"]["runs"] == []
    assert body["runs"]["text"] == "No agent session running on this Mac (outside Docker)" and body["runs"]["sessions"] == 0
    page = ws.owner().get(PAGE).get_data(as_text=True)
    assert "Nothing needs you here right now." in page                     # fresh record, queue read: it may say so


def test_budget_is_this_months_gateway_spend_and_unknown_without_a_store(ws):
    _record(ws)
    client = ws.owner()
    assert "Model spend unknown" in client.get(PAGE).get_data(as_text=True)
    ws.extra["usage"].mkdir()
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    rows = [{"emitter": "gateway", "actor": "mc", "cost_usd": 0.0123}, {"emitter": "gateway", "actor": "mc", "cost_usd": 0.01},
            {"emitter": "gateway", "actor": "cos", "cost_usd": 0.5}, {"emitter": "direct", "actor": "mc", "cost_usd": 9}]
    (ws.extra["usage"] / f"usage-{month}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n{torn\n")
    body = client.get(f"{API}/workshop").get_json()
    assert body["budget"] == {"known": True, "month": month, "by_actor": {"cos": 0.5, "mc": 0.0223}}
    assert "<strong>mc</strong> $0.02" in client.get(PAGE).get_data(as_text=True)


def test_status_refresh_and_check_ins_make_zero_model_calls(ws):
    _record(ws, {"actor": "claude-code", "kind": "needs_you", "item": "queue:12", "text": "check in"})
    client = ws.owner()
    for _ in range(3):
        assert client.get(f"{PAGE}?item=12").status_code == 200
        assert client.get(f"{API}/workshop?item=12").status_code == 200
        assert client.get(f"{API}/workshop").status_code == 200
    Workshop(str(ws.extra["root"]), "mac").append(health_event(_obs(clients=[]), "mac"))   # a check-in
    assert client.get(f"{API}/workshop").get_json()["admission"]["verdict"] == "ok"
    assert ws.extra["runtime"].sent == []                                  # the relay never saw a request


def test_the_refresh_carries_no_raw_state_and_the_workshop_is_owner_only(ws):
    _record(ws, {"actor": "claude-code", "kind": "progress", "item": "queue:12", "text": "x"})
    body = ws.owner().get(f"{API}/workshop?item=12").get_json()
    assert set(body) == {"workshop", "record_status", "admission", "runs", "last_event", "next_actor", "needs",
                         "headroom", "recovery", "budget", "observed_at"}
    assert ws.guest().get(PAGE).status_code in (302, 403)
    assert ws.guest().get(f"{API}/workshop").status_code == 403


def test_the_page_has_no_launch_control(ws):
    _record(ws)
    page = ws.owner().get(f"{PAGE}?item=12").get_data(as_text=True).lower()
    assert "launching comes later" in page
    for word in ("data-launch", ">start run<", ">launch<", 'method="post"'):
        assert word not in page


def test_needs_from_a_stale_record_are_marked_stale(ws):
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    _record(ws, {"actor": "codex", "kind": "needs_you", "item": "queue:12", "text": "Approve the fix round"},
            obs=_obs(observed_at=old))
    page = ws.owner().get(f"{PAGE}?item=12").get_data(as_text=True)
    assert "Approve the fix round" in page and "from a stale record" in page and 'data-stale="true"' in page


def test_an_old_health_event_without_session_fields_still_reads(ws):
    w = Workshop(str(ws.extra["root"]), "mac")
    ev = health_event(_obs(), "mac")
    for k in ("sessions", "scope"):
        ev["health"].pop(k)
    for r in ev["health"]["runs"]:
        r.pop("counted")
    w.append(ev)
    body = ws.owner().get(f"{API}/workshop").get_json()
    assert body["runs"]["sessions"] == 1 and [r["kind"] for r in body["runs"]["background"]] == ["codex-desktop"]
