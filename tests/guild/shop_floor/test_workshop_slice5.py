"""Guild 1.1 slice 5 (spec §7, §11, §12): the Workshop restyled. Light job
cards from the workshop record, an ops strip from the host reading and the
usage store where every figure carries its source and freshness or says "not
measured", honest unknown and stale states, and still read only: remote
start, stop and comment are labelled "not available", and no write route or
form exists. No contribution totals, no new metrics."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone

import pytest

from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored  # noqa: F401  (pytest fixtures)
from test_workshop_4a import _obs, _record, ws  # noqa: F401  (fixtures and helpers)

from minimoi_portal.guild_ui.workshop_view import jobs, ops_strip
from minimoi_portal.workshop.record import Workshop

API = "/guild-next/api/v1"
PAGE = "/guild-next/guild/workshop"


def _ops(body):
    return {o["id"]: o for o in body["ops"]}


# ── the ops strip: source and freshness, or "not measured" ───────────────────

def test_a_fresh_reading_gives_measured_figures_with_their_source_and_time(ws):
    seen = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _record(ws, obs=_obs(observed_at=seen))
    ops = _ops(ws.owner().get(f"{API}/workshop").get_json())
    assert [k for k in ops] == ["memory", "swap", "disk", "load", "spend", "production"]
    for key, value in (("memory", "46.0%"), ("swap", "1.2 GB"), ("disk", "80.0 GB"), ("load", "2.1")):
        o = ops[key]
        assert o["state"] == "measured" and o["value"] == value and o["fresh"]["at"] == seen
        assert o["fresh"]["age"] == "just now" and "workshop.py observe" in o["source"]
    assert ops["production"] == {**ops["production"], "state": "not_measured", "value": None,
                                 "text": "Production host · not measured", "source": "no source yet"}
    assert ops["spend"]["state"] == "not_measured" and ops["spend"]["source"] == "no usage store here"


def test_a_failed_probe_is_unknown_never_zero(ws):
    _record(ws, obs=_obs(memory_free_pct=None, load_1m=None))
    ops = _ops(ws.owner().get(f"{API}/workshop").get_json())
    for key in ("memory", "load"):
        assert ops[key]["state"] == "unknown" and ops[key]["value"] is None and "unknown" in ops[key]["text"]
    assert ops["disk"]["state"] == "measured"


def test_a_stale_reading_keeps_its_values_marked_stale_with_their_age(ws):
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    _record(ws, obs=_obs(observed_at=old))
    ops = _ops(ws.owner().get(f"{API}/workshop").get_json())
    assert ops["memory"]["state"] == "stale" and ops["memory"]["text"] == "Memory free 46.0% · stale"
    assert ops["memory"]["fresh"] == {"at": old, "age": "2 h ago"}
    page = ws.owner().get(PAGE).get_data(as_text=True)
    assert 'data-ws-op="memory" data-state="stale"' in page and "46.0% (stale)" in page
    assert "The host reading is stale, so these job states may be out of date." in page


@pytest.mark.parametrize("setup", ["missing", "unreadable"])
def test_no_readable_record_means_not_measured_never_empty(ws, setup):
    if setup == "unreadable":
        _record(ws)
        (ws.extra["root"] / "mac" / "state.json").write_text("{ torn")
    ops = _ops(ws.owner().get(f"{API}/workshop").get_json())
    for key in ("memory", "swap", "disk", "load"):
        assert ops[key]["state"] == "not_measured" and ops[key]["fresh"] == {"at": None, "age": None}
    page = ws.owner().get(PAGE).get_data(as_text=True)
    assert "Memory free · not measured" in page and "Job states unknown (no workshop record could be read)." in page
    assert "No jobs in the workshop record yet." not in page


def test_spend_is_measured_from_the_usage_store_with_its_file_time(ws):
    _record(ws)
    ws.extra["usage"].mkdir()
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    rows = [{"emitter": "gateway", "actor": "mc", "cost_usd": 0.25}, {"emitter": "gateway", "actor": "cos", "cost_usd": 0.5}]
    (ws.extra["usage"] / f"usage-{month}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    spend = _ops(ws.owner().get(f"{API}/workshop").get_json())["spend"]
    assert spend["state"] == "measured" and spend["value"] == "$0.75" and spend["fresh"]["at"]
    assert spend["source"] == "usage store, gateway records" and spend["text"] == f"Model spend $0.75 in {month}"


def test_the_details_table_names_every_source_and_freshness(ws):
    _record(ws)
    page = ws.owner().get(PAGE).get_data(as_text=True)
    table = page[page.index("data-ws-ops-table"):page.index("</table>")]
    for label in ("Memory free", "Swap used", "Disk free", "Load (1 min)", "Model spend", "Production host"):
        assert f'<th scope="row">{label}</th>' in table, label
    assert table.count("host reading on the Mac (workshop.py observe, then sync)") == 4
    assert "no source yet" in table and "not measured" in table and "—" in table
    assert "just now" in table


def test_the_ops_function_is_pure_and_model_free():
    assert [o["state"] for o in ops_strip(None, "missing", {"known": False})] == ["not_measured"] * 6


# ── job states from the record ─────────────────────────────────────────────

def test_job_cards_show_state_reporter_last_contact_and_evidence(ws):
    _record(ws, {"actor": "claude-code", "kind": "started", "item": "queue:12", "stage": "build",
                 "text": "Slice 5 restyle", "next_actor": "codex"},
            *[{"actor": "claude-code", "kind": "progress", "item": "queue:12", "text": f"step {n}"} for n in range(6)],
            {"actor": "codex", "kind": "review", "item": "pr:289", "stage": "review", "text": "Reviewing the strip"},
            {"actor": "claude-code", "kind": "done", "item": "spec:workshop", "text": "Spec read"},
            {"actor": "robert", "kind": "next", "item": "spec:rooms", "text": "Rooms walkthrough"})
    page = ws.owner().get(PAGE).get_data(as_text=True)
    cards = re.findall(r'data-ws-job="([^"]+)" data-state="([^"]+)"', page)
    assert dict(cards) == {"queue:12": "running", "pr:289": "in review", "spec:workshop": "completed",
                           "spec:rooms": "queued"}
    card = page[page.index('data-ws-job="queue:12"'):]
    card = card[:card.index("</article>")]
    assert 'href="/guild-next/guild/build/log?item=12">#12</a>' in card and "step 5" in card
    assert card.count("<li>") == 5 and "step 0" not in card                 # the last five events only
    assert "claude-code" in card and "last contact" in card and ", reported" in card
    v = ws.owner().get(f"{API}/workshop").get_json()
    assert v["jobs"] == {"known": True, "stale": False, "count": 4}


def test_an_empty_record_says_so(ws):
    _record(ws)
    page = ws.owner().get(PAGE).get_data(as_text=True)
    assert "No jobs in the workshop record yet." in page and "data-ws-job=" not in page


def test_jobs_unknown_without_a_record():
    assert jobs(None, "mac", None, "missing")["known"] is False


# ── read only: no write route, no form, controls labelled "not available" ──

def test_remote_controls_are_labelled_not_available_and_disabled(ws):
    _record(ws, {"actor": "codex", "kind": "started", "item": "queue:12", "text": "running"})
    page = ws.owner().get(PAGE).get_data(as_text=True)
    for control, label in (("start", "Start a job · not available"), ("comment", "Comment · not available"),
                           ("stop", "Stop · not available")):
        assert re.search(rf'data-ws-control="{control}" disabled>{re.escape(label)}<', page), control
    assert "Remote start, stop and comment are not available yet" in page
    main = page[page.index('<main id="main"'):page.index("</main>")].lower()   # the Workshop itself (the shell's chat is apart)
    for word in ("<form", 'method="post"', "data-launch", "contribution", "productivity", "leaderboard"):
        assert word not in main, word
    low = page.lower()
    for word in ("data-launch", "contribution", "productivity", "leaderboard"):
        assert word not in low, word


def test_the_workshop_has_no_write_routes(ws):
    rules = [r for r in ws.app.url_map.iter_rules() if "workshop" in r.rule]
    assert rules and all(r.methods <= {"GET", "HEAD", "OPTIONS"} for r in rules), [(r.rule, r.methods) for r in rules]
    token = ws.csrf(ws.owner())
    for method in ("post", "put", "patch", "delete"):
        r = getattr(ws.owner(), method)(f"{API}/workshop", json={}, headers={"X-CSRF-Token": token,
                                                                           "X-Record-Mode": "on_record"})
        assert r.status_code in (404, 405), method


def test_rendering_never_writes_the_workshop_record(ws):
    _record(ws, {"actor": "codex", "kind": "started", "item": "queue:12", "text": "running"})
    folder = ws.extra["root"] / "mac"
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file()}
    os.chmod(folder, 0o555)                                              # mounted read only on staging
    try:
        for url in (PAGE, f"{PAGE}?item=12", f"{API}/workshop", f"{API}/workshop?item=12"):
            assert ws.owner().get(url).status_code == 200, url
    finally:
        os.chmod(folder, 0o700)
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file()}
    assert after == before


def test_the_workshop_sits_in_the_shell_under_more(ws):
    _record(ws)
    page = ws.owner().get(PAGE).get_data(as_text=True)
    nav = page[page.index('<div class="guild-subnav guild-sections">'):page.index("</nav>")]
    assert re.search(r'data-more="workshop" aria-current="page"', nav)
    assert "subnav-more-on" in nav
