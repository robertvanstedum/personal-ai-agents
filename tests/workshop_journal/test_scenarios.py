"""The three owner scenarios (v0.7 section 8) on synthetic data, and the brief's honesty rules. Zero model calls, by test."""
from __future__ import annotations

import socket
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

import pytest

from conftest import WORKSHOP, envelope, new_id, progress
from core.workshop_journal import artifacts as art
from core.workshop_journal import brief as brief_view
from core.workshop_journal.journal import Journal
from fakes import NotASyntheticRoot, SyntheticOwnerResolver, Teammate, make_synthetic_root

MODEL_LIBS = ("anthropic", "openai", "litellm", "httpx", "requests", "aiohttp", "xai_sdk")


@pytest.fixture(autouse=True)
def no_outside_world(monkeypatch):
    """Any model call, network connection or child process fails the test (v0.7 Unit 3, G13)."""
    def boom(*a, **k):
        raise AssertionError("the brief and the check-in must make no model call, network call or child process")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    before = {m for m in MODEL_LIBS if m in sys.modules}
    yield
    assert {m for m in MODEL_LIBS if m in sys.modules} == before, "a model library was imported during the scenario"


@pytest.fixture
def world(root):
    make_synthetic_root(root)
    resolver = SyntheticOwnerResolver(root)
    j = Journal(root, WORKSHOP, lock_timeout=0.3, resolver=resolver)
    return j, Teammate("claude-code", j), Teammate("codex", j), Teammate("robert", j), Teammate("journeyman", j)


def spec(rev: int) -> tuple[art.Prepared, bytes]:
    data = f"# Spec revision {rev}\n\nDecision C replaces A. B rejected.\n".encode()
    return art.prepare(data), data


# ── the synthetic owner resolver cannot work outside a synthetic root (G3) ───────────────────────────────────────────
def test_G3_the_synthetic_owner_resolver_refuses_a_real_looking_root(tmp_path):
    real = tmp_path / "real-workshops"
    real.mkdir(mode=0o700)
    with pytest.raises(NotASyntheticRoot):
        SyntheticOwnerResolver(str(real))


def test_G3_the_synthetic_resolver_only_honours_the_owner_with_a_synthetic_reference(world):
    j, code, codex, robert, _ = world
    question = code.needs_you("Which destination?")
    forged = j.append(
        {"actor": "codex", "kind": "decision", "item": "topic:scenario", "text": "x", "authority_ref": {"type": "owner-control", "ref": "synthetic:x"},
         "payload": {"record_event_kind": "approved-direct", "resolves": [question.event_id], "reason": "forged"}})
    assert forged.committed                                                    # kept as evidence
    assert len(j.state()[0]["needs_you"]) == 1                                 # but it closes nothing
    assert j.brief()["decisions"]["owner"] == []                               # and the brief does not call it the owner's
    real_ref = j.append({"actor": "robert", "kind": "decision", "item": "topic:scenario", "text": "x",
                         "authority_ref": {"type": "owner-control", "ref": "owner-control-record-7"},
                         "payload": {"record_event_kind": "approved-direct", "resolves": [question.event_id], "reason": "x"}})
    assert real_ref.committed and len(j.state()[0]["needs_you"]) == 1          # not synthetic: the fake does not recognize it
    robert.decide_as_owner([question.event_id])
    assert j.state()[0]["needs_you"] == []


# ── scenario 1: design session, cold handoff, review comes back ──────────────────────────────────────────────────────
def test_scenario_1_design_session_cold_handoff_and_resume(world):
    j, code, codex, robert, _ = world
    rev1, data1 = spec(1)
    rev2, data2 = spec(2)
    a = j.append({"actor": "claude-code", "kind": "decision", "item": "spec:design", "topic": "scenario", "text": "Decision A: one journal file.",
                  "refs": [rev1.ref("spec-rev-1")], "payload": {"record_event_kind": "proposed", "resolves": [], "reason": "A"}},
                 artifacts=[rev1])
    b = code.propose("Decision B: a database for the journal.")
    rejected = robert.decide_as_owner([b.event_id], kind="rejected", ref="synthetic:b")
    c = j.append({"actor": "robert", "kind": "decision", "item": "spec:design", "topic": "scenario", "text": "Correction C: files stay authoritative.",
                  "supersedes": a.event_id, "refs": [rev2.ref("spec-rev-2")], "authority_ref": {"type": "owner-control", "ref": "synthetic:c"},
                  "payload": {"record_event_kind": "approved-direct", "resolves": [], "reason": "C replaces A"}}, artifacts=[rev2])
    assert rejected.committed and c.committed
    # the working file changes after review; the reviewed bytes must stay pinned
    req = j.append({"actor": "claude-code", "kind": "request", "item": "spec:design", "topic": "scenario", "recipients": ["codex"],
                    "text": "Review spec revision 2.", "refs": [rev2.ref("spec-rev-2")],
                    "payload": {"action": "review", "expected_result": "Numbered findings.", "required_refs": [rev2.retained_sha256]}})
    # --- Codex arrives cold: it has the journal and nothing else ---
    cold = Journal(j.root, WORKSHOP, resolver=SyntheticOwnerResolver(j.root))
    brief = cold.brief(topic="scenario")
    by_id = {p["event_id"]: p for p in brief["decisions"]["proposals"]}
    assert by_id[a.event_id]["status"] == "superseded" and by_id[a.event_id]["settled_by"] == c.event_id
    assert by_id[b.event_id]["status"] == "rejected" and by_id[b.event_id]["settled_by"] == rejected.event_id
    assert [o["record_event_kind"] for o in brief["decisions"]["owner"]] == ["rejected", "approved-direct"]
    assert any(l["older"] == a.event_id and l["newer"] == c.event_id for l in brief["decisions"]["superseded"])
    open_req = brief["requests"][0]
    assert open_req["state"] == "open" and open_req["recipients"]["codex"]["status"] == "pending"
    assert any(g["code"] == "no_receipt" for g in brief["gaps"])
    # the exact bytes it was asked to review, not whatever the working copy says now
    pinned = cold.get(req.event_id).evidence["event"]["payload"]["required_refs"][0]
    assert cold.open_artifact(pinned) == data2 and cold.open_artifact(rev1.retained_sha256) == data1
    codex.receive(req.event_id)
    codex.result(req.event_id, "completed", ["did not run the build"], "Two findings.")
    # --- Claude Code resumes from the journal alone ---
    back = j.brief(topic="scenario")
    assert back["requests"][0]["state"] == "closed" and back["results"][0]["limitations"] == ["did not run the build"]
    assert back["results"][0]["note"] == "a worker report, not an independent check"
    assert not any(g["code"] in ("no_receipt", "no_result") for g in back["gaps"])
    text = brief_view.render_markdown(back)
    assert "proposal by claude-code (superseded)" in text and "owner approved-direct" in text and "rejected" in text


# ── scenario 2: build session, owner correction, honest test report ──────────────────────────────────────────────────
def test_scenario_2_build_session_states_what_changed_what_was_tested_and_what_was_not(world):
    j, code, codex, robert, _ = world
    req = codex.request(["claude-code"], "build", "Build Unit 1 from the plan.", resource="workshop-build")
    code.receive(req.event_id)
    claim = code.claim(req.event_id, "workshop-build", 1)
    code.progress("Journal engine written.", claim_id=claim.event_id, request_id=req.event_id)
    j.append(
        {"actor": "robert", "kind": "decision", "item": "topic:scenario", "topic": "scenario", "text": "Correction: record decisions in a log, do not stop to ask.",
         "authority_ref": {"type": "owner-control", "ref": "synthetic:fix"},
         "payload": {"record_event_kind": "approved-direct", "resolves": [], "reason": "owner direction mid-build"}})
    diff = art.prepare(b"diff --git a/core/x.py b/core/x.py\n+print('changed')\n", source_class="diff")
    pin = "f" * 64
    code.progress("Tests written.", claim_id=claim.event_id, request_id=req.event_id)
    done = j.append({"actor": "claude-code", "kind": "result", "item": "topic:scenario", "topic": "scenario", "text": "Unit 1 built.",
                     "refs": [diff.ref("unit1-diff"), {"type": "test", "id": "pytest-run", "sha256": pin, "availability": "pointer_only"}],
                     "payload": {"request_id": req.event_id, "recipient": "claude-code", "outcome": "completed", "claim_id": claim.event_id,
                                 "limitations": ["12 tests not run: they need Docker"], "test_summary": {"reported": 1650, "run": 1638, "passed": 1638,
                                                                                                          "failed": 0, "not_run": 12}}},
                    artifacts=[diff])
    assert done.committed
    code.release(claim.event_id, 1, True)
    code.next("codex", "review the Unit 1 diff")
    # --- the next teammate, cold ---
    brief = Journal(j.root, WORKSHOP, resolver=SyntheticOwnerResolver(j.root)).brief(topic="scenario")
    assert [o["text"] for o in brief["decisions"]["owner"]] == ["Correction: record decisions in a log, do not stop to ask."]
    [report] = brief["test_reports"]
    assert report["reported_by"] == "claude-code" and (report["summary"]["passed"], report["summary"]["not_run"]) == (1638, 12)
    assert report["pin"][0]["sha256"] == pin
    assert brief["results"][0]["limitations"] == ["12 tests not run: they need Docker"]
    assert brief["results"][0]["evidence"][0]["sha256"] == diff.retained_sha256
    assert brief["claims"][0]["state"] == "stopped" and brief["next"][0]["next_actor"] == "codex"
    assert j.open_artifact(diff.retained_sha256).startswith(b"diff --git")
    text = brief_view.render_markdown(brief).lower()
    assert "not run 12" in text and "worker report" in text and "all clear" not in text and "all tests" not in text


def test_the_brief_never_adds_test_counts_together_or_invents_a_pin(world):
    j, code, codex, robert, _ = world
    r1, r2 = code.request(["codex", "grok-cli"], "test", "run tests"), code.request(["codex"], "test", "again")
    for rid, reporter in ((r1.event_id, codex), (r2.event_id, codex)):
        reporter.result(rid, "completed", [], "tests ran", test_summary={"reported": 100, "run": 100, "passed": 100, "failed": 0, "not_run": 0})
    brief = j.brief()
    assert [t["summary"]["passed"] for t in brief["test_reports"]] == [100, 100]
    assert all(t["pin"] == [] for t in brief["test_reports"])
    assert "no pin given" in brief_view.render_markdown(brief) and "200" not in brief_view.render_markdown(brief)


# ── scenario 3: the check-in, with no model call ────────────────────────────────────────────────────────────────────
def test_scenario_3_checkin_reports_last_entry_waiting_action_missing_updates_and_a_correlated_refresh(world):
    j, code, codex, robert, journeyman = world
    code.progress("Unit 2 under way.")
    q = code.needs_you("Which backup destination?")
    stale = codex.request(["claude-code"], "review", "Look at the diff.")
    now = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)
    brief = j.brief(now=now, teammates=("claude-code", "codex", "grok-cli"))
    rows = {t["actor"]: t for t in brief["teammates"]}
    assert rows["claude-code"]["last"]["kind"] == "needs_you" and rows["codex"]["last"]["kind"] == "request"
    assert rows["grok-cli"]["last"] is None and rows["grok-cli"]["note"] == "no entry in this scope"
    assert [n["event_id"] for n in brief["needs_you"]] == [q.event_id]
    assert any(g["code"] == "no_receipt" and g["recipient"] == "claude-code" for g in brief["gaps"])
    assert any(g["code"] == "owner_question_open" for g in brief["gaps"])
    # a refresh request is an ordinary addressed request; the brief stays honest while it waits
    refresh = journeyman.request(["codex"], "refresh", "Where are you on the review?", expected="One line: done, in progress, or blocked.")
    waiting = j.brief(now=now)
    mine = next(r for r in waiting["requests"] if r["id"] == refresh.event_id)
    assert mine["recipients"]["codex"]["status"] == "pending"
    assert rows["codex"]["note"].startswith("nothing newer than")
    codex.receive(refresh.event_id)
    answer = codex.result(refresh.event_id, "completed", [], "In progress: two findings so far.")
    after = j.brief(now=now)
    got = next(r for r in after["requests"] if r["id"] == refresh.event_id)
    assert got["state"] == "closed" and got["recipients"]["codex"]["result_seq"] == answer.seq
    assert next(x for x in after["results"] if x["request_id"] == refresh.event_id)["outcome"] == "completed"


def test_scenario_3_a_replacement_teammate_runs_the_same_operations(world):
    j, code, codex, robert, journeyman = world
    grok = Teammate("grok-cli", j)
    req = journeyman.request(["grok-cli"], "refresh", "Status?")
    grok.receive(req.event_id)
    claim = grok.claim(req.event_id, "grok-sandbox", 1)
    grok.release(claim.event_id, 1, True)
    assert grok.result(req.event_id, "declined", ["analysis only; no build"]).committed
    assert next(r for r in j.brief()["requests"] if r["id"] == req.event_id)["recipients"]["grok-cli"]["result_outcome"] == "declined"


# ── honesty rules ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_same_events_and_time_give_the_same_brief_byte_for_byte(world):
    j, code, codex, robert, journeyman = world
    req = code.request(["codex"], "review", "x", due_at="2026-10-08T12:00:00Z")
    code.needs_you("Question?")
    now = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)
    a, b = j.brief(now=now), Journal(j.root, WORKSHOP, resolver=j.resolver).brief(now=now)
    assert a == b and brief_view.render_markdown(a) == brief_view.render_markdown(b)
    assert a["requests"][0]["overdue"] is True and any(g["code"] == "overdue" for g in a["gaps"])
    assert j.brief()["requests"][0]["overdue"] is None                         # no clock given: not evaluated, not guessed


def test_the_brief_for_a_topic_includes_the_lifecycle_of_its_requests_only(world):
    j, code, codex, robert, _ = world
    mine = Teammate("claude-code", j, topic="alpha")
    other = Teammate("claude-code", j, topic="beta")
    r1, r2 = mine.request(["codex"], "review", "alpha request"), other.request(["codex"], "review", "beta request")
    codex.receive(r1.event_id)
    codex.receive(r2.event_id)
    alpha = j.brief(topic="alpha")
    assert [r["text"] for r in alpha["requests"]] == ["alpha request"]
    assert alpha["counts"]["events_in_scope"] == 2                             # the request and its receipt, nothing of beta
    assert alpha["as_of"]["seq"] == 4                                          # but "as of" is the whole journal
    rows = j.history(topic="alpha")["events"]
    assert [r["kind"] for r in rows] == ["request", "receipt"]
    assert [r["seq"] for r in j.history(topic="alpha", through_seq=1)["events"]] == [1]


def test_the_brief_refuses_a_damaged_journal_and_flags_a_torn_tail(world):
    j, code, codex, robert, _ = world
    code.progress("fine")
    path = f"{j.dir}/events.jsonl"
    data = open(path, "rb").read()
    open(path, "ab").write(b'{"v":2,"cut')
    torn = j.brief()
    assert torn["journal_status"] == "torn_tail" and any(g["code"] == "journal_torn_tail" for g in torn["gaps"])
    open(path, "wb").write(data + b"garbage\n")
    refused = j.brief()
    assert (refused["status"], refused["reason"]) == ("refused", "journal_damaged") and "teammates" not in refused
    assert j.history()["status"] == "refused"


def test_a_missing_workshop_has_no_brief_not_an_empty_one(root):
    j = Journal(root, WORKSHOP)
    assert j.brief()["status"] == "missing" and j.history()["status"] == "missing"
