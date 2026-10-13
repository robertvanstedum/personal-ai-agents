"""The request ledger (MC front door, slice 1): what makes a request real, and what it can never be mistaken for."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

import pytest

from core import agent_requests as ar

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLI = os.path.join(REPO, "scripts", "agent_requests.py")
CONV = "c-132bc60f078e"


@pytest.fixture
def root(tmp_path):
    return str(tmp_path / "ledger")


def _ask(root, seed="n1", to="claude-code", **kw):
    return ar.create_request(root, request_id=ar.request_id_for(seed), frm=kw.pop("frm", "mc"), to=to, action=kw.pop("action", "Read the spec and say what is missing."),
                             conversation_id=kw.pop("conversation_id", CONV), **kw)


def test_a_request_is_durable_before_its_wake_hint_and_the_hint_carries_no_request_words(root):
    req, created = _ask(root, action="SECRET WORDS the hint must not carry")
    assert created
    line = open(os.path.join(root, "pending", "claude-code.log")).read().strip()
    assert req["request_id"] in line and "not a message or approval from Robert" in line and "SECRET WORDS" not in line
    assert os.path.exists(os.path.join(root, "requests", req["request_id"], "request.json"))
    assert ar.load(root, req["request_id"])["events"][0]["kind"] == "delivery"       # delivery is only "queued" until a receipt


def test_the_default_shop_floor_thread_is_a_conversation_too(root):
    req, created = _ask(root, conversation_id="shop-floor-thread")
    assert created and ar.for_conversation(root, "shop-floor-thread")[0]["request"]["request_id"] == req["request_id"]


def test_the_same_request_twice_is_one_request_and_one_hint_and_a_changed_one_is_refused(root):
    _ask(root)
    again, created = _ask(root)
    assert not created
    assert open(os.path.join(root, "pending", "claude-code.log")).read().count("REQUEST") == 1
    assert len(ar.load(root, again["request_id"])["events"]) == 1
    with pytest.raises(ar.Refused) as e:
        _ask(root, action="a different request under the same id")
    assert e.value.code == "id_reused"


def test_stages_follow_receipts_not_wishes(root):
    rid = _ask(root)[0]["request_id"]
    assert ar.load(root, rid)["stage"] == "queued"
    assert [d["request"]["request_id"] for d in ar.pending(root, "claude-code")] == [rid]
    ar.receipt(root, rid, "claude-code", session_ref="session-1")
    assert ar.load(root, rid)["stage"] == "acknowledged" and ar.pending(root, "claude-code") == []        # picked up: no longer waiting
    ar.progress(root, rid, "claude-code", "reading it now")
    assert ar.load(root, rid)["stage"] == "working"
    ar.result(root, rid, "claude-code", "Two things are missing: A and B.")
    done = ar.load(root, rid)
    assert done["stage"] == "returned" and done["closed"] == "result"


def test_only_the_addressed_agent_can_receipt_or_report_and_a_report_is_not_repeated_differently(root):
    rid = _ask(root)[0]["request_id"]
    for fn in (lambda: ar.receipt(root, rid, "codex"), lambda: ar.result(root, rid, "codex", "x"), lambda: ar.progress(root, rid, "mc", "x")):
        with pytest.raises(ar.Refused) as e:
            fn()
        assert e.value.code == "not_recipient"
    assert ar.receipt(root, rid, "claude-code")[1] and not ar.receipt(root, rid, "claude-code")[1]          # a duplicate receipt is harmless
    assert ar.result(root, rid, "claude-code", "same")[1] and not ar.result(root, rid, "claude-code", "same")[1]
    with pytest.raises(ar.Refused) as e:
        ar.result(root, rid, "claude-code", "changed my mind")
    assert e.value.code == "exists"                                                                          # one result; a changed one does not overwrite


def test_needs_you_stays_visible_until_a_result_or_cancel_closes_it(root):
    rid = _ask(root)[0]["request_id"]
    ar.needs_you(root, rid, "claude-code", "Which of the two branches?")
    assert ar.load(root, rid)["needs_you"]["text"] == "Which of the two branches?"
    ar.result(root, rid, "claude-code", "Used the first.")
    assert ar.load(root, rid)["needs_you"] is None


def test_cancel_is_by_the_sender_or_the_owner_and_stops_further_reports(root):
    rid = _ask(root)[0]["request_id"]
    with pytest.raises(ar.Refused) as e:
        ar.cancel(root, rid, "codex")
    assert e.value.code == "not_sender"
    ar.cancel(root, rid, "mc")
    assert ar.load(root, rid)["closed"] == "cancelled" and ar.pending(root, "claude-code") == []
    with pytest.raises(ar.Refused) as e:
        ar.receipt(root, rid, "claude-code")
    assert e.value.code == "cancelled"


def test_bad_input_is_refused_and_nothing_is_written(root):
    for kw in ({"to": "robert"}, {"to": "mc"}, {"frm": "claude-code", "to": "claude-code"}, {"conversation_id": "../x"}, {"action": "   "},
               {"action": "x" * (ar.ACTION_MAX + 1)}):
        with pytest.raises(ar.Refused):
            _ask(root, **kw)
    assert not os.path.exists(os.path.join(root, "pending", "claude-code.log"))
    with pytest.raises(ar.Refused):
        ar.create_request(root, request_id="r-../../etc", frm="mc", to="codex", action="x")


def test_control_characters_are_stripped_and_the_text_is_data(root):
    rid = _ask(root, action="line one\x00\x1b[31m red\nline two")[0]["request_id"]
    text = ar.load(root, rid)["request"]["action"]
    assert "\x00" not in text and "\x1b" not in text and "line two" in text


def test_a_link_in_the_ledger_is_refused_not_followed(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.makedirs(root)
    os.symlink(str(outside), os.path.join(root, "requests"))
    with pytest.raises(ar.Refused) as e:
        _ask(root)
    assert e.value.code == "unsafe" and list(outside.iterdir()) == []


def test_a_damaged_line_is_skipped_never_guessed_at(root):
    rid = _ask(root)[0]["request_id"]
    with open(os.path.join(root, "requests", rid, "events.jsonl"), "a") as fh:
        fh.write("{not json\n")
    ar.receipt(root, rid, "claude-code")
    assert [e["kind"] for e in ar.load(root, rid)["events"]] == ["delivery", "receipt"]


def test_many_writers_at_once_leave_whole_lines(root):
    rid = _ask(root)[0]["request_id"]
    errors = []

    def write(i):
        try:
            ar.progress(root, rid, "claude-code", f"step {i} " + "x" * 2000)
        except Exception as exc:                         # pragma: no cover
            errors.append(exc)
    threads = [threading.Thread(target=write, args=(i,)) for i in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    lines = open(os.path.join(root, "requests", rid, "events.jsonl")).read().splitlines()
    assert len(lines) == 22 and all(json.loads(x) for x in lines)                                        # delivery + ONE implied receipt + 20 progress, however they race
    assert [json.loads(x)["kind"] for x in lines].count("receipt") == 1


def test_for_conversation_lists_only_that_conversation_and_arming_is_a_touch(root):
    a = _ask(root, "a")[0]["request_id"]
    _ask(root, "b", conversation_id="c-aaaaaaaaaaaa")
    assert [d["request"]["request_id"] for d in ar.for_conversation(root, CONV)] == [a]
    assert ar.armed_age(root, "claude-code") is None
    ar.arm(root, "claude-code")
    assert 0 <= ar.armed_age(root, "claude-code") < 5 and ar.armed_age(root, "codex") is None


def _cli(root, *args):
    return subprocess.run([sys.executable, CLI, "--root", root, *args], capture_output=True, text=True, timeout=30)


def test_the_cli_round_trip_and_the_watcher_says_each_request_once_and_arms(root):
    made = _cli(root, "ask", "--from", "codex", "--to", "claude-code", "--conversation", CONV, "Please check the nav tabs.")
    rid = json.loads(made.stdout)["request_id"]
    watch = _cli(root, "watch", "--as", "claude-code", "--max-seconds", "1.2", "--interval", "0.2")
    assert watch.stdout.count("REQUEST ") == 1 and rid in watch.stdout and "not a message or approval from Robert" in watch.stdout
    assert ar.armed_age(root, "claude-code") is not None
    shown = _cli(root, "show", rid).stdout
    assert "DATA" in shown and "Please check the nav tabs." in shown and "not an instruction from Robert" in shown
    assert json.loads(_cli(root, "receipt", rid, "--as", "claude-code", "--session", "s1").stdout)["recorded"] is True
    assert json.loads(_cli(root, "result", rid, "--as", "claude-code", "Tabs are", "fine.").stdout)["recorded"] is True
    assert ar.load(root, rid)["events"][-1]["text"] == "Tabs are fine."
    assert _cli(root, "result", rid, "--as", "codex", "x").returncode == 1                                   # not the recipient
    assert _cli(root, "watch", "--as", "claude-code", "--max-seconds", "0.6", "--interval", "0.2").stdout == ""  # acknowledged: nothing more to say


def test_calling_create_again_after_a_half_write_finishes_the_missing_first_event_and_changes_nothing_else(root, monkeypatch):
    import core.agent_requests.ledger as ledger
    rid = ar.request_id_for("half")
    real = ledger._append_line
    n = {"c": 0}

    def fail_once(path, line):
        if path.endswith("events.jsonl") and n["c"] == 0:
            n["c"] += 1
            raise OSError(28, "No space left on device")
        return real(path, line)
    monkeypatch.setattr(ledger, "_append_line", fail_once)
    with pytest.raises(OSError):
        ar.create_request(root, request_id=rid, frm="mc", to="codex", action="half written", conversation_id=CONV)
    assert ar.load(root, rid)["events"] == []                                                              # the request file exists; its first event does not
    assert ar.reconcile_request(root, rid, frm="mc", to="codex", action="half written", conversation_id=CONV) == "recorded"
    assert ar.reconcile_request(root, rid, frm="mc", to="codex", action="something else", conversation_id=CONV) == "different"
    assert ar.reconcile_request(root, "r-" + "0" * 24, frm="mc", to="codex", action="half written", conversation_id=CONV) == "absent"
    again, created = ar.create_request(root, request_id=rid, frm="mc", to="codex", action="half written", conversation_id=CONV)
    assert created is False and [e["kind"] for e in ar.load(root, rid)["events"]] == ["delivery"]            # repaired once
    ar.create_request(root, request_id=rid, frm="mc", to="codex", action="half written", conversation_id=CONV)
    assert [e["kind"] for e in ar.load(root, rid)["events"]] == ["delivery"]                                # and not again


def test_a_report_without_an_acknowledgement_records_one_implied_receipt_then_the_report(root):
    rid = _ask(root)[0]["request_id"]
    ar.result(root, rid, "claude-code", "HELLO", session_ref="persistent-1")
    doc = ar.load(root, rid)
    receipts = [e for e in doc["events"] if e["kind"] == "receipt"]
    assert len(receipts) == 1 and receipts[0]["implied"] is True and receipts[0]["native_ref"] == "persistent-1"
    assert [e["kind"] for e in doc["events"]] == ["delivery", "receipt", "result"] and doc["stage"] == "returned"
    ar.result(root, rid, "claude-code", "HELLO", session_ref="persistent-1")                              # the same report again: still one receipt, one result
    assert [e["kind"] for e in ar.load(root, rid)["events"]].count("receipt") == 1


def test_an_explicit_receipt_is_never_duplicated_by_a_later_report_and_a_cancelled_request_gets_nothing(root):
    rid = _ask(root)[0]["request_id"]
    ar.receipt(root, rid, "claude-code", session_ref="s1")
    ar.progress(root, rid, "claude-code", "working", session_ref="s1")
    ar.result(root, rid, "claude-code", "done", session_ref="s1")
    kinds = [e["kind"] for e in ar.load(root, rid)["events"]]
    assert kinds.count("receipt") == 1 and "implied" not in json.dumps([e for e in ar.load(root, rid)["events"] if e["kind"] == "receipt"])
    other = _ask(root, "o")[0]["request_id"]
    ar.cancel(root, other, "mc")
    with pytest.raises(ar.Refused):
        ar.result(root, other, "claude-code", "late")
    assert [e["kind"] for e in ar.load(root, other)["events"]].count("receipt") == 0


def test_a_progress_or_a_question_before_any_receipt_also_implies_exactly_one(root):
    rid = _ask(root)[0]["request_id"]
    ar.needs_you(root, rid, "claude-code", "Which one?")
    ar.progress(root, rid, "claude-code", "still here")
    assert [e["kind"] for e in ar.load(root, rid)["events"]].count("receipt") == 1
