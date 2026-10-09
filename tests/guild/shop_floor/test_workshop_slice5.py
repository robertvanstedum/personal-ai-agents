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

from minimoi_portal.guild_ui.workshop_view import JOB_CAP, jobs, ops_strip, usage_this_month
from minimoi_portal.workshop.record import VERSION, Workshop

API = "/guild-next/api/v1"
PAGE = "/guild-next/guild/operate/build-host"   # the host diagnostics moved here (Workshop W1); /guild/workshop is the topic Workshop


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
    assert [c for c, _ in cards] == ["pr:289", "queue:12", "spec:rooms", "spec:workshop"]   # in review, running, queued, completed
    v = ws.owner().get(f"{API}/workshop").get_json()
    assert {k: v["jobs"][k] for k in ("known", "stale", "count", "more")} == {"known": True, "stale": False, "count": 4,
                                                                               "more": 0}
    assert [c["item"] for c in v["jobs"]["cards"]] == ["pr:289", "queue:12", "spec:rooms", "spec:workshop"]
    done = page[page.index('data-ws-job="spec:workshop"'):]
    done = done[:done.index("</article>")]
    assert 'data-ws-control="' not in done  # no nonfunctional controls


def test_an_empty_record_says_so(ws):
    _record(ws)
    page = ws.owner().get(PAGE).get_data(as_text=True)
    assert "No jobs in the workshop record yet." in page and "data-ws-job=" not in page


def test_jobs_unknown_without_a_record():
    assert jobs(None, "mac", None, "missing")["known"] is False


# ── read only: no write route, no form, controls labelled "not available" ──

def test_unconnected_remote_controls_are_not_presented_as_actions(ws):
    _record(ws, {"actor": "codex", "kind": "started", "item": "queue:12", "text": "running"})
    page = ws.owner().get(PAGE).get_data(as_text=True)
    assert 'data-ws-control="' not in page
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


def test_the_build_host_sits_in_the_operate_nav_and_the_old_address_moved_here(ws):
    _record(ws)
    page = ws.owner().get(PAGE).get_data(as_text=True)
    nav = page[page.index('<div class="guild-subnav guild-sections">'):page.index("</nav>")]
    assert re.search(r'class="subnav-link subnav-active" href="/guild-next/guild/operate/build-host">Build host</a>', nav)
    old = ws.owner().get("/guild-next/guild/workshop?item=12")
    assert old.status_code == 302 and old.headers["Location"].endswith("/guild/operate/build-host?item=12")      # old queue-item links keep working
    assert ws.owner().get("/guild-next/guild/workshop?item=").status_code == 302


# ── PR #289 review fixes ─────────────────────────────────────────────────────

def _ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat(timespec="seconds")


def _ahead(**kw):
    return (datetime.now(timezone.utc) + timedelta(**kw)).isoformat(timespec="seconds")


def _state_path(ws):
    return ws.extra["root"] / "mac" / "state.json"


def _edit_state(ws, change):
    """Make the screen read a state of the wrong shape. The state is now derived from the journal (state.json is a cache that
    never wins), so the wrong-shaped state is injected where the screen reads it, not by editing the cache file."""
    import minimoi_portal.guild_ui.workshop_view as wv
    inner = wv.load_state

    def edited(root, workshop_id):
        state, status = inner(root, workshop_id)
        if isinstance(state, dict):
            change(state)
            host = state.get("host")
            seen = wv.parse((host.get("observed_at") or host.get("at")) if isinstance(host, dict) else None)
            if seen is not None and status == "ok" and (wv.now() - seen > wv.STALE_AFTER or seen - wv.now() > wv.CLOCK_AHEAD):
                status = "stale"
        return state, status
    wv.load_state = edited


@pytest.fixture(autouse=True)
def _restore_load_state():
    import minimoi_portal.guild_ui.workshop_view as wv
    real = wv.load_state
    yield
    wv.load_state = real


def _both(ws):
    """The page, the item page and the API all answer (never a 500); returns the API body and the page."""
    client = ws.owner()
    for url in (f"{PAGE}?item=12", f"{API}/workshop?item=12"):
        assert client.get(url).status_code == 200, url
    page = client.get(PAGE)
    api = client.get(f"{API}/workshop")
    assert page.status_code == 200 and api.status_code == 200
    return api.get_json(), page.get_data(as_text=True)


def _two_jobs(ws):
    return _record(ws, {"actor": "codex", "kind": "started", "item": "queue:12", "text": "Building"},
                   {"actor": "robert", "kind": "next", "item": "spec:rooms", "text": "Rooms walkthrough"})


def _raw_line(ws, line: dict):
    with open(ws.extra["root"] / "mac" / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


# F1: a malformed but valid JSON record line is "unknown" for that line or item, never a 500.

def test_an_event_line_without_an_item_is_skipped(ws):
    _two_jobs(ws)
    _raw_line(ws, {"v": VERSION, "at": _ago(minutes=1), "actor": "codex", "kind": "progress", "text": "no item"})
    v, page = _both(ws)
    assert v["jobs"]["known"] is True and {c["item"] for c in v["jobs"]["cards"]} == {"queue:12", "spec:rooms"}
    assert "no item" not in page


def test_an_event_line_with_a_numeric_time_is_skipped(ws):
    _two_jobs(ws)
    _raw_line(ws, {"v": VERSION, "at": 1727740800, "actor": "codex", "kind": "progress", "item": "queue:12",
                   "text": "numeric time"})
    v, page = _both(ws)
    card = next(c for c in v["jobs"]["cards"] if c["item"] == "queue:12")
    assert card["state"] == "running" and "numeric time" not in page


def test_record_items_as_a_list_make_job_states_unknown(ws):
    _two_jobs(ws)
    _edit_state(ws, lambda s: s.update(items=[{"at": _ago(minutes=1), "kind": "started"}]))
    v, page = _both(ws)
    assert v["jobs"]["known"] is False and v["jobs"]["cards"] == []
    assert "Job states unknown (the workshop record could not be read)." in page
    assert "No jobs in the workshop record yet." not in page


def test_an_item_entry_that_is_a_string_is_unknown_for_that_item_only(ws):
    _two_jobs(ws)
    _edit_state(ws, lambda s: s["items"].update({"spec:rooms": "garbled"}))
    v, page = _both(ws)
    cards = {c["item"]: c for c in v["jobs"]["cards"]}
    assert cards["spec:rooms"]["state"] == "unknown" and cards["spec:rooms"]["pill"] == "unknown"
    assert cards["queue:12"]["state"] == "running"
    assert "This item&#39;s entry in the workshop record could not be read." in page


def test_a_queue_ref_of_non_ascii_digits_is_not_a_queue_link(ws):
    _two_jobs(ws)
    _edit_state(ws, lambda s: s["items"].update({"queue:²": {"at": _ago(minutes=1), "kind": "started", "text": "odd ref"}}))
    v, page = _both(ws)
    card = next(c for c in v["jobs"]["cards"] if c["item"] == "queue:²")
    assert card["queue_id"] is None and '<span class="ws-ref">queue:²</span>' in page


@pytest.mark.parametrize("change", [
    lambda s: s.update(host=["not", "a", "reading"]),
    lambda s: s.update(needs_you={"item": "queue:12"}, next="soon", last_event="text"),
    lambda s: s["host"].update(runs=["x", {"kind": ["list"]}], clients=[1], sessions="two", reasons="text"),
], ids=["host-a-list", "lists-of-the-wrong-shape", "host-fields-of-the-wrong-shape"])
def test_other_sections_of_the_wrong_shape_read_as_unknown_never_a_500(ws, change):
    _two_jobs(ws)
    _edit_state(ws, change)
    _both(ws)


def test_job_cards_never_raise_on_a_bad_record():
    for state in ({"items": ["x"]}, {"items": "x"}, {"items": {"queue:1": 3, "queue:2": {"at": 5, "kind": ["x"]}}}):
        out = jobs(None, "mac", state, "ok")
        assert out["known"] in (True, False)


# F4: a reading dated ahead of the server's clock is unknown (clock ahead), not "just now".

def test_a_reading_dated_hours_ahead_is_unknown_clock_ahead(ws):
    _two_jobs(ws).append({"workshop": "mac", "actor": "codex", "kind": "progress", "item": "queue:12",
                          "text": "From the future", "at": _ahead(hours=3)})
    _edit_state(ws, lambda s: s["host"].update(observed_at=_ahead(hours=3)))
    v, page = _both(ws)
    assert v["record_status"] == "stale" and v["admission"]["verdict"] == "unknown"
    assert "ahead of this server (clock skew)" in v["admission"]["reasons"][0]
    assert v["admission"]["age"] == "unknown (clock ahead)"
    ops = _ops(v)
    assert ops["memory"]["state"] == "unknown" and ops["memory"]["text"] == "Memory free · unknown (clock ahead)"
    assert ops["memory"]["fresh"]["age"] == "unknown (clock ahead)" and "just now" not in str(ops)
    card = next(c for c in v["jobs"]["cards"] if c["item"] == "queue:12")
    assert card["pill"] == "unknown (clock ahead)" and card["last_contact"]["age"] == "unknown (clock ahead)"
    assert v["jobs"]["stale_text"].startswith("The host reading is dated ahead of this server")
    assert "Memory free · unknown (clock ahead)" in page


def test_a_reading_a_minute_ahead_is_still_fresh(ws):
    _record(ws, obs=_obs(observed_at=_ahead(minutes=1)))
    ops = _ops(ws.owner().get(f"{API}/workshop").get_json())
    assert ops["memory"]["state"] == "measured" and ops["memory"]["fresh"]["age"] == "just now"


# F6: only finite, non-negative numbers are figures; anything else is unknown.

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -5, "lots", {"x": 1}, True])
def test_a_bad_host_value_is_unknown_never_a_number(ws, bad):
    _record(ws)
    _edit_state(ws, lambda s: s["host"].update(memory_free_pct=bad))
    v, page = _both(ws)
    memory = _ops(v)["memory"]
    assert memory["state"] == "unknown" and memory["value"] is None
    assert memory["text"] == "Memory free · unknown (unreadable value)"
    assert "nan" not in memory["text"].lower() and "Memory free · unknown (unreadable value)" in page
    assert "memory free unknown" in (v["headroom"] or "")


def test_an_absent_figure_says_so(ws):
    _record(ws)
    _edit_state(ws, lambda s: s["host"].pop("load_1m"))
    assert _ops(ws.owner().get(f"{API}/workshop").get_json())["load"]["text"] == "Load (1 min) · unknown (not in the reading)"


@pytest.mark.parametrize("bad", ["NaN", "-0.5", '"cheap"', "Infinity"])
def test_a_bad_cost_makes_spend_unknown_never_a_total(ws, bad, tmp_path):
    _record(ws)
    ws.extra["usage"].mkdir()
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    (ws.extra["usage"] / f"usage-{month}.jsonl").write_text(
        '{"emitter": "gateway", "actor": "mc", "cost_usd": 0.25}\n'
        f'{{"emitter": "gateway", "actor": "mc", "cost_usd": {bad}}}\n'
        '{"emitter": "gateway", "actor": "mc", "cost_usd": null}\n')
    v, page = _both(ws)
    spend = _ops(v)["spend"]
    assert spend["state"] == "unknown" and spend["value"] is None
    assert spend["text"] == "Model spend · unknown (unreadable value in 1 gateway record)"
    assert v["budget"]["bad"] == 1 and "$0.25" not in page
    assert "1 gateway record had an unreadable cost" in page


# F7: no usage file yet this month is "not measured yet", and an old file shows its date.

def test_no_usage_file_this_month_is_not_measured_yet_never_zero(ws):
    _record(ws)
    ws.extra["usage"].mkdir()
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    v, page = _both(ws)
    spend = _ops(v)["spend"]
    assert spend["state"] == "not_measured" and spend["value"] is None
    assert spend["text"] == "Model spend · not measured yet this month"
    assert spend["source"] == f"usage store: no gateway records yet in {month}"
    assert "$0.00" not in page and "none recorded" not in page
    assert f"no gateway records yet in {month}" in page


def test_an_old_usage_file_is_labelled_last_record_with_its_date(ws):
    _record(ws)
    ws.extra["usage"].mkdir()
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    path = ws.extra["usage"] / f"usage-{month}.jsonl"
    path.write_text('{"emitter": "gateway", "actor": "mc", "cost_usd": 0.25}\n')
    old = datetime.now(timezone.utc) - timedelta(days=3)
    os.utime(path, (old.timestamp(), old.timestamp()))
    v, page = _both(ws)
    fresh = _ops(v)["spend"]["fresh"]
    assert fresh["label"] == "last record" and fresh["date"] == old.strftime("%d %b").lstrip("0")
    assert fresh["age"] == "72 h ago"
    assert f"last record {fresh['date']} <span data-iso" in page
    today = usage_this_month(str(ws.extra["usage"]))
    os.utime(path, None)
    today = usage_this_month(str(ws.extra["usage"]))
    assert "date" not in ops_strip(None, "missing", today)[4]["fresh"]


# F5: a job not heard from in 48 hours is not "running"; needs you first; at most 12 cards.

def test_a_job_silent_for_days_says_last_seen_not_running(ws):
    _record(ws, {"actor": "codex", "kind": "started", "item": "queue:12", "text": "Building", "at": _ago(days=5)},
            {"actor": "codex", "kind": "done", "item": "queue:13", "text": "Shipped", "at": _ago(days=5)})
    v, page = _both(ws)
    cards = {c["item"]: c for c in v["jobs"]["cards"]}
    assert cards["queue:12"]["pill"] == "last seen 5d ago (stale)" and cards["queue:12"]["flag"] == "stale"
    assert cards["queue:13"]["pill"] == "completed" and cards["queue:13"]["flag"] is None   # done stays done
    assert 'data-ws-job="queue:12" data-state="stale"' in page and "last reported: running" in page
    assert ">running<" not in page


def test_needs_you_comes_first_and_the_cards_are_capped(ws):
    events = [{"actor": "codex", "kind": "done", "item": f"queue:{n}", "text": f"done {n}"} for n in range(100, 114)]
    events.insert(0, {"actor": "codex", "kind": "needs_you", "item": "queue:12", "text": "Choose", "at": _ago(hours=1)})
    _record(ws, *events)
    v, page = _both(ws)
    cards = v["jobs"]["cards"]
    assert len(cards) == JOB_CAP == 12 and v["jobs"]["more"] == 3
    assert cards[0]["item"] == "queue:12" and cards[0]["state"] == "needs you"
    assert page.count("<article class=\"ws-job\"") == 12
    assert "+3 more in the workshop record" in page


# F2: the refresh carries the cards, so the page can redraw them.

def test_the_refresh_carries_the_cards_and_their_stale_wording(ws):
    _record(ws, {"actor": "codex", "kind": "started", "item": "queue:12", "text": "Building"}, obs=_obs(observed_at=_ago(hours=2)))
    jobs_body = ws.owner().get(f"{API}/workshop").get_json()["jobs"]
    assert jobs_body["stale"] is True and jobs_body["stale_text"] == "The host reading is stale, so these job states may be out of date."
    card = jobs_body["cards"][0]
    assert {"item", "state", "pill", "flag", "title", "actor", "last_contact", "evidence", "queue_id"} <= set(card)


# F9: the strip's figures are read out, not hidden behind a label.

def test_the_strip_summary_has_no_aria_label_hiding_its_figures(ws):
    _record(ws)
    page = ws.owner().get(PAGE).get_data(as_text=True)
    start = page.index('<summary class="ws-strip"')
    summary = page[start:page.index("</summary>", start)]
    assert "aria-label" not in summary and "Memory free 46.0%" in summary
    assert '<span class="visually-hidden">Ops strip:</span>' in summary


def test_an_old_needs_you_item_keeps_saying_needs_you(ws):
    """#289 re-check R1: waiting on Robert for days is urgent, not stale."""
    _record(ws, {"actor": "codex", "kind": "needs_you", "item": "queue:14", "text": "Pick the art", "at": _ago(days=3)})
    v, _page = _both(ws)
    card = {c["item"]: c for c in v["jobs"]["cards"]}["queue:14"]
    assert card["pill"] == "needs you · 3d" and card["flag"] is None and card["state"] == "needs you"
    assert v["jobs"]["cards"][0]["item"] == "queue:14"


@pytest.mark.parametrize("iso", ["9999-12-31T23:59:59-14:00", "0001-01-01T00:00:00+14:00"])
def test_a_date_at_the_edge_of_the_calendar_is_shown_as_text_not_a_500(iso):
    """#289 re-check R2."""
    from minimoi_portal.guild_ui.pages import hhmm
    assert hhmm(iso) == iso
