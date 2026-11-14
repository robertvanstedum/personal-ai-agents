"""MC's chat shows its work (issue #545): the relay's tool steps become plain step names, the browser sees labels only,
the turn's record keeps the steps and the footer, and turns from before render as they always did."""
from __future__ import annotations

import json

import pytest

from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)
from test_mc_streaming import _events, _no_busy_wait_by_default, _fresh_dispatch_state, _stream, _wait_idle, nd, streaming  # noqa: F401
from test_mc_turns import _keep

from minimoi_portal.guild_ui.mc import stream as st
from minimoi_portal.guild_ui.mc.turn_log import TurnLog, done_text
from minimoi_portal.guild_ui.mc.turn_steps import StepTally, headline, label_for, outcome_of, view
from minimoi_portal.guild_ui.mc.usage_join import tokens_text

API = "/guild-next/api/v1"
SECRET = "ghp_SECRETVALUE1234567890"


def step(n, name, phase, **extra):
    return nd({"t": "step", "n": n, "name": name, "phase": phase, **extra})


# ── the relay's line ──────────────────────────────────────────────────────────

def test_a_step_line_reads_as_its_name_place_and_timing_only():
    assert st.parse_relay_line(json.dumps({"t": "step", "n": 1, "name": "exec", "phase": "start"})) == st.Step(1, "exec", "start")
    end = st.parse_relay_line(json.dumps({"t": "step", "n": 2, "name": "read", "phase": "end", "ok": False, "ms": 1200,
                                          "args": {"command": SECRET}, "result": SECRET}))
    assert end == st.Step(2, "read", "end", False, 1200)
    assert SECRET not in repr(end)                         # anything else on the line is dropped, never carried


@pytest.mark.parametrize("bad", [
    {"n": 0, "name": "exec", "phase": "start"}, {"n": True, "name": "exec", "phase": "start"},
    {"n": 1, "name": "rm -rf /", "phase": "start"}, {"n": 1, "name": "x" * 65, "phase": "start"},
    {"n": 1, "name": "exec", "phase": "running"}, {"n": 1, "phase": "start"},
])
def test_a_malformed_step_line_is_dropped(bad):
    assert st.parse_relay_line(json.dumps({"t": "step", **bad})) is None


def test_a_bad_duration_is_unknown_not_a_number():
    assert st.parse_relay_line(json.dumps({"t": "step", "n": 1, "name": "exec", "phase": "end", "ms": -5})).ms is None
    assert st.parse_relay_line(json.dumps({"t": "step", "n": 1, "name": "exec", "phase": "start", "ms": 5})).ms is None


# ── names into words ──────────────────────────────────────────────────────────

def test_tool_names_become_plain_labels():
    assert label_for("minimoi_github_get") == "Read GitHub"
    assert label_for("minimoi_github_issues") == "Looked up GitHub issues"
    assert label_for("exec") == "Ran a command"
    assert label_for("minimoi_something_new") == "Used something new"      # unknown: said from the name only
    assert label_for("not a name!") == "Used a tool"


def test_the_headline_counts_one_kind_and_calls_a_mix_steps():
    assert headline(["exec", "exec"]) == "Ran 2 commands"
    assert headline(["exec"]) == "Ran a command"
    assert headline(["read", "minimoi_host_read", "read"]) == "Read 3 files"
    assert headline(["exec", "read", "minimoi_github_issues"]) == "Ran 3 steps"
    assert headline(["minimoi_board_note_read"]) == "Ran 1 step"
    assert headline(["exec", "exec"], more=1) == "Ran 3 steps"


def test_the_browser_event_carries_labels_never_the_tool_name_and_a_second_run_counts_on():
    tally = StepTally()
    first = tally.add(st.Step(1, "minimoi_github_issues", "start"))
    assert first == {"t": "step", "n": 1, "label": "Looked up GitHub issues", "phase": "start", "count": 1,
                     "headline": "Checked GitHub"}
    assert "minimoi_github_issues" not in json.dumps(first)
    tally.add(st.Step(1, "minimoi_github_issues", "end", True, 900))
    assert tally.add(st.Step(1, "exec", "end", True, 5)) is None            # the same place, another tool: not this step
    tally.new_run()
    again = tally.add(st.Step(1, "exec", "start"))
    assert again["n"] == 2 and again["headline"] == "Ran 2 steps"
    record = tally.record()
    assert record == {"v": 1, "more": 0, "items": [
        {"tool": "minimoi_github_issues", "label": "Looked up GitHub issues", "ok": True, "ms": 900},
        {"tool": "exec", "label": "Ran a command"}]}


@pytest.mark.parametrize("block", [None, {}, {"v": 2, "items": [{"tool": "exec"}]}, {"v": 1, "items": "exec"},
                                   {"v": 1, "items": [{"tool": "no such tool!"}]}, {"v": 1, "items": []}])
def test_an_old_or_unknown_block_shows_no_steps(block):
    assert view(block) is None


def test_a_kept_block_is_relabelled_from_the_tool_name_not_the_stored_words():
    shown = view({"v": 1, "items": [{"tool": "exec", "label": f"<b>{SECRET}</b>"}, {"tool": "exec"}], "more": 0})
    assert shown == {"headline": "Ran 2 commands", "labels": ["Ran a command", "Ran a command"], "count": 2}



# ── a refused step reads as refused (#599) ────────────────────────────────────

def test_a_step_line_carries_a_refusal_only_on_an_end_that_was_not_ok():
    refused = st.parse_relay_line(json.dumps({"t": "step", "n": 2, "name": "minimoi_work_save", "phase": "end",
                                              "ok": False, "outcome": "refused", "ms": 11_000}))
    assert refused == st.Step(2, "minimoi_work_save", "end", False, 11_000, "refused")
    for line in ({"ok": True, "outcome": "refused"}, {"ok": False, "outcome": "tool_not_in_policy"}, {"outcome": "refused"}):
        assert st.parse_relay_line(json.dumps({"t": "step", "n": 1, "name": "minimoi_work_save", "phase": "end",
                                               **line})).outcome is None
    assert st.parse_relay_line(json.dumps({"t": "step", "n": 1, "name": "minimoi_work_save", "phase": "start",
                                           "ok": False, "outcome": "refused"})).outcome is None


def test_a_step_that_did_not_succeed_says_how_and_the_headline_counts_it():
    assert label_for("minimoi_work_save", "refused") == "Saved a working document (refused)"
    assert label_for("minimoi_agent_status", "unknown") == "Checked a builder's reply (not confirmed)"
    assert label_for("exec", "failed") == "Ran a command (failed)"
    assert label_for("minimoi_work_save", "made up") == "Saved a working document"
    assert outcome_of({"ok": False, "outcome": "refused"}) == "refused"
    assert outcome_of({"ok": False}) == "failed" and outcome_of({"ok": False, "outcome": "<b>"}) == "failed"
    assert outcome_of({"ok": True, "outcome": "refused"}) is None and outcome_of({"tool": "exec"}) is None
    assert headline(["minimoi_open_card", "minimoi_work_save", "minimoi_working_write"],
                    outcomes=[None, "refused", None]) == "Ran 3 steps, 1 refused"
    assert headline(["exec", "exec"], outcomes=["failed", "failed"]) == "Ran 2 commands, 2 failed"
    assert headline(["exec"], outcomes=[None]) == "Ran a command"


def test_the_production_turn_of_14_nov_shows_the_refused_save_as_refused():
    """The turn #599 found: MiniMoi refused minimoi_work_save (tool_not_in_policy), and the step read "Saved"."""
    tally = StepTally()
    tally.add(st.Step(1, "minimoi_open_card", "start"))
    tally.add(st.Step(1, "minimoi_open_card", "end", True, 3990))
    started = tally.add(st.Step(2, "minimoi_work_save", "start"))
    assert started["label"] == "Saved a working document"                    # while it runs, the tool's name alone
    ended = tally.add(st.Step(2, "minimoi_work_save", "end", False, 10_999, "refused"))
    assert ended["label"] == "Saved a working document (refused)" and ended["headline"] == "Ran 2 steps, 1 refused"
    tally.add(st.Step(3, "minimoi_working_write", "start"))
    tally.add(st.Step(3, "minimoi_working_write", "end", True, 7399))
    record = tally.record()
    assert record["items"][1] == {"tool": "minimoi_work_save", "label": "Saved a working document (refused)",
                                  "ok": False, "ms": 10_999, "outcome": "refused"}
    assert "outcome" not in record["items"][0] and "outcome" not in record["items"][2]
    assert view(record) == {"headline": "Ran 3 steps, 1 refused", "count": 3, "labels": [
        "Used open card", "Saved a working document (refused)", "Saved a working file"]}


def test_a_kept_step_that_failed_reads_failed_and_a_kept_refusal_reads_refused():
    assert view({"v": 1, "items": [{"tool": "exec", "ok": False}, {"tool": "exec", "ok": True}]}) == {
        "headline": "Ran 2 commands, 1 failed", "labels": ["Ran a command (failed)", "Ran a command"], "count": 2}
    assert view({"v": 1, "items": [{"tool": "minimoi_work_save", "ok": False, "outcome": "refused",
                                    "label": "Saved a working document"}]})["labels"] == ["Saved a working document (refused)"]

# ── the footer ────────────────────────────────────────────────────────────────

def test_the_footer_words():
    assert done_text(39_400) == "Done in 39s" and done_text(1234) == "Done in 1.2s"
    assert tokens_text(1) == "1 output token" and tokens_text(96) == "96 output tokens"
    assert tokens_text(1100) == "1.1k output tokens" and tokens_text(1000) == "1k output tokens"
    assert tokens_text(23_456) == "23k output tokens"


def test_the_turn_record_keeps_the_steps_and_an_old_line_has_none(tmp_path):
    log = TurnLog(str(tmp_path))
    tally = StepTally()
    tally.add(st.Step(1, "exec", "start"))
    log.record(turn_id="t1", status="answered", backend_kind="openclaw", duration_ms=39_000, reply_request_id="mc-a",
               mode="stream", steps=tally.record())
    log.record(turn_id="t2", status="answered", backend_kind="openclaw", duration_ms=2_000, reply_request_id="mc-b")
    found = log.turns_for(["mc-a", "mc-b"])
    assert found["mc-a"]["steps"] == {"headline": "Ran a command", "labels": ["Ran a command"], "count": 1}
    assert found["mc-a"]["done_text"] == "Done in 39s"
    assert "steps" not in found["mc-b"]
    line = json.loads((tmp_path / "mc_turns.jsonl").read_text().splitlines()[0])
    assert line["steps"]["v"] == 1


# ── end to end through the portal ─────────────────────────────────────────────

STEPPED = [nd({"t": "delta", "text": "Checking GitHub Actions on your repos now. "}),
           step(1, "minimoi_github_issues", "start"), step(1, "minimoi_github_issues", "end", ok=True, ms=800),
           step(2, "exec", "start"), step(2, "exec", "end", ok=True, ms=1500),
           nd({"t": "delta", "text": "All green."}), nd({"t": "answer", "text": "All green."}),
           nd({"t": "finish", "reason": "stop"}), nd({"t": "usage", "prompt_tokens": 50, "completion_tokens": 1100})]


def test_a_tool_using_turn_streams_labels_then_keeps_steps_and_footer_for_a_reload(streaming):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "Are the deploys green?")
    streaming.extra["runtime"].script = list(STEPPED)
    resp = _stream(client, token, note["request_id"])
    raw = resp.get_data(as_text=True)
    events = _events(resp)
    steps = [e for e in events if e["t"] == "step"]
    assert [(e["n"], e["phase"], e["label"]) for e in steps] == [
        (1, "start", "Looked up GitHub issues"), (1, "end", "Looked up GitHub issues"),
        (2, "start", "Ran a command"), (2, "end", "Ran a command")]
    assert steps[-1]["headline"] == "Ran 2 steps"
    assert "minimoi_github_issues" not in raw and '"exec"' not in raw          # tool names never reach the browser
    renders = [e for e in events if e["t"] == "render"]
    assert all("Checking GitHub" not in e["html"] for e in renders if events.index(e) > events.index(steps[0]))
    done = events[-1]
    assert done["t"] == "done" and done["status"] == "answered"
    turn = done["reply_note"]["turn"]
    assert turn["steps"] == {"headline": "Ran 2 steps", "labels": ["Looked up GitHub issues", "Ran a command"], "count": 2}
    assert turn["tokens_text"] == "1.1k output tokens" and turn["done_text"].startswith("Done in")
    assert done["reply_note"]["text"] == "All green."                         # the narration was the status, not the answer
    _wait_idle()
    reply = next(n for n in client.get(f"{API}/notes").get_json()["notes"] if n["id"] == done["reply_note"]["id"])
    assert reply["turn"]["steps"]["labels"] == ["Looked up GitHub issues", "Ran a command"]
    assert reply["turn"]["tokens_text"] == "1.1k output tokens"              # the footer is kept with the turn
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert '<summary data-turn-steps-headline>Ran 2 steps</summary>' in page
    assert '<li>Looked up GitHub issues</li><li>Ran a command</li>' in page
    assert "<details class=\"msg-steps\" data-turn-steps>" in page and "data-turn-steps open" not in page    # folded



def test_a_refused_step_streams_and_reloads_as_refused(streaming):
    """#599 end to end: a refusal from the relay reaches the browser's label, the kept turn and the page as refused."""
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "Save this as a working document.")
    streaming.extra["runtime"].script = [
        step(1, "minimoi_work_save", "start"), step(1, "minimoi_work_save", "end", ok=False, outcome="refused", ms=11_000),
        nd({"t": "delta", "text": "It was refused."}), nd({"t": "answer", "text": "It was refused."}),
        nd({"t": "finish", "reason": "stop"})]
    events = _events(_stream(client, token, note["request_id"]))
    ends = [e for e in events if e["t"] == "step" and e["phase"] == "end"]
    assert [(e["label"], e["headline"]) for e in ends] == [("Saved a working document (refused)", "Ran 1 step, 1 refused")]
    done = events[-1]
    assert done["reply_note"]["turn"]["steps"] == {"headline": "Ran 1 step, 1 refused",
                                                   "labels": ["Saved a working document (refused)"], "count": 1}
    _wait_idle()
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert "<summary data-turn-steps-headline>Ran 1 step, 1 refused</summary>" in page
    assert "<li>Saved a working document (refused)</li>" in page

def test_a_turn_without_tools_renders_as_before(streaming):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "hello")
    done = _events(_stream(client, token, note["request_id"]))[-1]
    assert done["status"] == "answered" and "steps" not in done["reply_note"]["turn"]
    _wait_idle()
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert "data-turn-steps>" not in page.split('<template id="tpl-turn-steps">')[0]     # only the template has one
    assert "data-turn-foot>" in page                                                       # the footer shows


def test_no_secret_from_a_tool_reaches_the_page_even_if_the_relay_sent_one(streaming):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "check")
    streaming.extra["runtime"].script = [
        step(1, "exec", "start", args={"command": f"curl -H 'Authorization: {SECRET}'"}),
        step(1, "exec", "end", ok=True, ms=10, result=SECRET, label=SECRET),
        nd({"t": "delta", "text": "Done."}), nd({"t": "finish", "reason": "stop"})]
    raw = _stream(client, token, note["request_id"]).get_data(as_text=True)
    _wait_idle()
    assert SECRET not in raw
    assert SECRET not in client.get("/guild-next/guild/build").get_data(as_text=True)
    assert SECRET not in json.dumps(client.get(f"{API}/notes").get_json())
