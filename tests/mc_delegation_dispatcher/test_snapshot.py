"""The headless state contract and its rules, as pure functions over authority records (host runtime spec v0.5
§7.6, §7.7, R7.4-c/d/e, R5.1-b, R4.6-b, §7.2; S1-G): shown states and both clocks, the flags (stalled true for a
stalled worker and false for an idle one), unknowns never rendered as zero or false, notices once per key with the
next step, silence where nothing is needed, batched decisions (solo classes alone), owner-work counts, the read path's
credential check, state.json written atomically and read back as a view with its age."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.mc_delegation_dispatcher import snapshot as S  # noqa: E402

NOW = datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc)
VALUES = {"contact_timeout_s": 45, "gap_threshold_s": 30, "progress_timeout_s": {"build": 180, "review": 90},
          "judgment": {"route": "fakejudge", "unit": "test_units", "calls_per_day": 2, "per_day": 1.0}}


def ago(s):
    return NOW - timedelta(seconds=s)


def runner(host, *, seen=5, announced="online", availability="intermittent", runner_id=None, caps=None):
    return {"runner_id": runner_id or f"{host}:runner", "state": "online", "host_id": host, "environment": "dev",
            "roles": ["worker", "witness"], "bundle_digest": "sha256:" + "a" * 64,
            "expected_availability": availability, "unreachable_grace_s": 900, "announced": announced,
            "last_gap_s": 0, "protocol": 2, "seen_at": ago(seen),
            "capabilities": caps or ["actor:fakework", "scope:build", "scope:research", "repo:personal-ai-agents"]}


def item(state="running", *, host="mac-1", lease_left=40, progress=10, leased=100, kind="build", card="C-17",
         step=None, reservation=None, facts=None, deadline=3000, **over):
    rid = over.pop("request_id", str(uuid.uuid4()))
    f = {"leased_at": ago(leased).isoformat(), "picked_up_at": ago(leased).isoformat(),
         **({"card": {"kind": kind, "index": 0, "attempt": 1, "on_card": True}} if card else {}), **(facts or {})}
    row = {"request_id": rid, "job_id": f"{card}.{step or kind}" if card else "job-1", "actor": "fakework",
           "scope": "build", "repository": "personal-ai-agents", "state": state, "created_at": ago(300),
           "started_at": ago(leased), "finished_at": None, "heartbeat_at": ago(5), "deadline": ago(-deadline),
           "brief": "fakework/1", "result_detail": None, "runner_id": f"{host}:runner", "generation": 1,
           "lease_until": None if lease_left is None else ago(-lease_left), "cancel_requested": False,
           "card_id": card, "step": step or kind, "grant_id": None, "required_capabilities": [],
           "progress_at": None if progress is None else ago(progress), "progress_phase": "executing",
           "checkpoint": None, "lapsed_since": None, "lapse_cause": None, "grace_deadline": None, "route": "fakework",
           "reservation": reservation or {"requested": 1.0, "reserved": 1.0, "unit": "test_units"}, "facts": f,
           "created_by": "authority", "result_event_id": None}
    row.update(over)
    return row


def rec(items=(), runners=(), events=(), **over):
    out = {"now": NOW, "environment": "dev", "values": VALUES, "items": list(items), "card_items": list(items),
           "events": list(events), "runners": list(runners), "blocked": {}, "suspended": [], "grants": {},
           "liveness": {"sweeper": {"seen_at": ago(5)}, "authority": {"seen_at": ago(5)}}, "judgment_day": None,
           "codes": {}, "agents": [], "agents_unknown": None,
           "authority": {"endpoint_id": "dev-authority", "host_id": "mac-1"}}
    out.update(over)
    return out


def event(kind, *, source="authority", item_id=None, generation=0, at=None, body=None, event_id=None):
    return {"event_id": event_id or str(uuid.uuid4()), "kind": kind, "source": source, "item_id": item_id,
            "generation": generation, "host_id": None, "at": at or ago(60), "body": body or {}}


def by(doc, rid):
    return next(i for i in doc["items"] if i["item_id"] == rid)


# ── shown states and flags (R5.1-b, §7.7, T12) ────────────────────────────────────────────────────────────────
def test_t12_stalled_is_true_for_a_stalled_worker_and_false_for_an_idle_one():
    stalled = item(progress=200, leased=400)                          # no progress for 200 s, build timeout 180
    moving = item(progress=10)
    idle = item("queued", lease_left=None, progress=None, runner_id=None)
    doc = S.build(rec([stalled, moving, idle], [runner("mac-1")]))
    s, m, i = (by(doc, x["request_id"]) for x in (stalled, moving, idle))
    assert (s["shown_as"], s["flags"]["stalled"], s["progress_age_s"]) == ("running", True, 200.0)
    assert (m["flags"]["stalled"], i["flags"]["stalled"]) == (False, False)
    assert s["next_authorized_step"]["step"] == "continue" and i["next_authorized_step"]["step"] == "claim"
    assert s["lease_left_s"] == 40.0 and s["owner_reachability"] == "reachable"       # both clocks, with ages
    stall = [n for n in doc["notices"] if n["rule"] == "stalled"]
    assert [n["item_id"] for n in stall] == [stalled["request_id"]] and stall[0]["proposal"]
    assert [p["state"] for p in doc["proposals"]] == ["due"]            # one review_request, never a call
    again = S.build(rec([stalled, moving, idle], [runner("mac-1")]))
    assert [n["key"] for n in again["notices"]] == [n["key"] for n in doc["notices"]]   # the same stall, the same key
    assert again["proposals"][0]["proposal_id"] == doc["proposals"][0]["proposal_id"]


def test_an_unreachable_owner_shows_held_then_lapsed_never_running():
    held = item(host="mac-2", lease_left=20, progress=300)
    lapsed = item(host="mac-2", lease_left=-5)
    doc = S.build(rec([held, lapsed], [runner("mac-2", seen=140)]))
    h, l = by(doc, held["request_id"]), by(doc, lapsed["request_id"])
    assert (h["shown_as"], h["owner_reachability"], h["flags"]["stalled"]) == ("held", "unreachable", False)
    assert h["next_authorized_step"]["step"].startswith("renew on return")
    assert l["shown_as"] == "lapsed" and "reconcile" in l["next_authorized_step"]["step"]
    # R7.4-e: a portable owner's lapse within grace raises nothing; an always-on owner's lapse raises one notice.
    assert not [n for n in doc["notices"] if n["rule"] in ("lapsed", "stalled")]
    on = S.build(rec([lapsed], [runner("mac-2", seen=140, availability="always_on")]))
    assert [n["key"] for n in on["notices"] if n["rule"] == "lapsed"] == [f"runtime-lapsed:{lapsed['request_id']}:g1"]


def test_unknowns_are_named_never_rendered_as_zero_false_or_done():
    no_timeout = item(kind="review", progress=30)
    usage = item("completed", lease_left=None, reservation={"requested": 1.0, "reserved": 1.0, "unit": "test_units",
                                                            "state": "usage_unknown", "observed": None})
    doc = S.build(rec([no_timeout, usage], [runner("mac-1")], values={**VALUES, "progress_timeout_s": {"build": 180}}))
    n, u = by(doc, no_timeout["request_id"]), by(doc, usage["request_id"])
    assert n["flags"]["stalled"] is None and "progress_timeout_s:review" in n["unknowns"]
    assert u["reservation"]["observed"] is None and u["reservation"]["usage_unknown"] is True
    assert "observed_usage" in u["unknowns"] and u["flags"]["unknown"] is True
    nothing = S.build(rec([], [], liveness={"sweeper": None, "authority": None}, authority={}))
    assert nothing["authority"]["sweeper_age_s"] is None
    assert {"authority_endpoint_id", "authority_host_id"} <= set(nothing["unknowns"])
    assert any(u.startswith("sweeper_age") for u in nothing["unknowns"])


def test_spend_per_item_is_spent_plus_observed():
    retried = item("completed", lease_left=None, reservation={"requested": 1.5, "reserved": 0.25, "unit": "test_units",
                                                              "spent": 0.75, "observed": 0.5, "state": "settled"})
    doc = S.build(rec([retried], [runner("mac-1")]))
    assert by(doc, retried["request_id"])["reservation"]["observed"] == 1.25


def test_hosts_skip_the_authoritys_reserved_rows_and_show_blocked_tasks_with_the_next_step():
    rid = str(uuid.uuid4())
    rows = [runner("mac-1"), {**runner("mac-1", runner_id="sweeper/dev/robert"), "host_id": None},
            {**runner("x", runner_id="authority/dev/robert")}, {**runner("y", runner_id="reader/dev/collector")}]
    blocked = {"mac-1:runner": [{"item_id": rid, "generation": 2, "code": "authority_error:audit_event_not_written",
                                 "since": ago(120).isoformat()}]}
    doc = S.build(rec([], rows, blocked=blocked))
    assert [h["host_id"] for h in doc["hosts"]] == ["mac-1"]
    b = doc["hosts"][0]["blocked"][0]
    assert (b["code"], b["age_s"], b["next_step"].startswith("an operator restarts")) == \
        ("authority_error:audit_event_not_written", 120.0, True)
    notice = [n for n in doc["notices"] if n["rule"] == "blocked"]
    assert len(notice) == 1 and notice[0]["next_authorized_step"] == {"actor": "operator", "step": "restart the runtime",
                                                                       "authority": None}


def test_overdue_and_drift_raise_one_notice_each_with_the_next_step_and_stop_nothing():
    late = item("queued", lease_left=None, progress=None, deadline=-60, runner_id=None, card="C-9")
    drift = item(card="C-18", checkpoint={"name": "s1", "digest": "sha256:" + "b" * 64, "paths": ["core/x.py", "docs/y"]})
    card = event("card.admitted", source="owner", at=ago(900),
                 body={"card_id": "C-18", "card": {"paths_allowed": ["core/"], "estimate_s": 300, "attempts": 2}})
    doc = S.build(rec([late, drift], [runner("mac-1")], events=[card]))
    assert by(doc, late["request_id"])["flags"]["overdue"] is True
    d = by(doc, drift["request_id"])
    assert [x["kind"] for x in d["flags"]["drift"]] == ["out_of_scope_paths", "over_estimate"]
    assert d["flags"]["drift"][0]["paths"] == ["docs/y"] and d["shown_as"] == "running"
    rules = sorted(n["key"] for n in doc["notices"] if n["rule"] in ("overdue", "drift"))
    assert rules == sorted([f"runtime-overdue:{late['request_id']}:g1",
                            f"runtime-drift:{drift['request_id']}:out_of_scope_paths",
                            f"runtime-drift:{drift['request_id']}:over_estimate"])
    assert all(n["next_authorized_step"] for n in doc["notices"] if n["rule"] in ("overdue", "drift"))
    # a card that states no bound: named in unknowns, never read as "no drift"
    plain = S.build(rec([item(card="C-19")], [runner("mac-1")],
                        events=[event("card.admitted", body={"card_id": "C-19", "card": {"paths_allowed": []}})]))
    assert {"paths_allowed: the card states none", "estimate_s: the card states none"} <= set(plain["items"][0]["unknowns"])


def test_a_retry_waiting_and_a_portable_hosts_expected_sleep_raise_nothing():
    retry = item("queued", lease_left=None, progress=None, facts={"retry": {"attempt": 2}})
    asleep = item(host="mac-2", lease_left=10, progress=500)
    doc = S.build(rec([retry, asleep], [runner("mac-1"), runner("mac-2", seen=400)]))
    assert doc["notices"] == []
    assert by(doc, retry["request_id"])["next_authorized_step"]["actor"] == "mac-1:runner"


# ── decisions (R7.4-c, OWN-6) ─────────────────────────────────────────────────────────────────────────────────
def queued(row, cls, *, reason="grant_exhausted", **body):
    return event("decision.queued", item_id=row["request_id"], generation=row["generation"],
                 body={"class": cls, "solo": cls in S.SOLO, "reason": reason, **body})


def test_routine_decisions_share_one_notice_of_at_most_five_and_solo_classes_arrive_alone():
    proposed = [item("proposed", lease_left=None, progress=None, card=f"C-{n}", runner_id=None) for n in range(7)]
    unknown = item("unknown", lease_left=None, progress=None, card="C-99", result_detail={"decision_id": "x"})
    events = [queued(r, "step_admission") for r in proposed]
    events.append(queued(unknown, "unknown_resolution", reason="effect_unknown", operations=[str(uuid.uuid4())]))
    doc = S.build(rec(proposed + [unknown], [runner("mac-1")], events=events))
    batches = [n for n in doc["notices"] if n["rule"] in ("decision", "decisions")]
    assert [(n["rule"], len(n["decisions"])) for n in batches] == [("decision", 1), ("decisions", 5), ("decisions", 2)]
    assert batches[0]["decisions"] == [events[-1]["event_id"]]                # the unknown, alone
    for n in batches:                     # every decision in full words, each with its own digest
        for w in n["words"]:
            assert w["text_sha256"] == S.sha256(w["words"]) and len(w["words"]) > 40
    assert len({w["text_sha256"] for n in batches for w in n["words"]}) == 8
    # a decision already in a recorded notice stays there; only the rest are grouped anew
    raised = [event("notice.raised", body={"key": batches[1]["key"], "decisions": batches[1]["decisions"],
                                            "rule": "decisions"})]
    more = S.build(rec(proposed + [unknown], [runner("mac-1")], events=events + raised))
    keys = [n["key"] for n in more["notices"] if n["rule"] == "decisions"]
    assert keys[0] == batches[1]["key"] and more["notices"][[n["key"] for n in more["notices"]].index(keys[0])]["raised_at"]


def test_decision_words_say_what_yes_and_no_do_and_bind_by_digest():
    p = item("proposed", lease_left=None, progress=None, runner_id=None)
    u = item("unknown", lease_left=None, progress=None)
    cases = [("step_admission", {}, ("admit", "decline")),
             ("cross_vendor_admission", {"reason": "cross_vendor"}, ("admit", "decline")),
             ("step_redo", {"reason": "effect_failed", "operations": ["a"]}, ("admit", "decline")),
             ("reassignment", {"runner_id": "mac-9:runner"}, ("reassign", "withdraw")),
             ("unknown_resolution", {"reason": "effect_unknown"}, ("end", "leave")),
             ("unknown_resolution", {"reason": "result_reported", "reported": {"status": "completed"},
                                     "effects": {"verified": 1}}, ("accept", "leave")),        # SF1: no never discards it
             ("review_request", {"reason": "stalled", "since": "t", "timeout_s": 180, "owner_host_id": "mac-1"},
              ("authorize_review", "withdraw")),
             ("review_request", {"reason": "proposed", "proposed_by": "mc"}, ("authorize_review", "withdraw")),
             ("unknown_resolution", {"reason": "process_unverified", "item_state": "failed"}, ("acknowledge", "leave"))]
    for cls, body, actions in cases:
        d = {"decision_id": "d", "class": cls, "body": {"reason": "grant_exhausted", **body}, "generation": 1}
        row = u if cls == "unknown_resolution" else p
        assert S.decision_actions(d, row) == actions, cls
        words = S.decision_words(d, row)
        assert "Yes" in words and ("no " in words.lower()) and words == S.decision_words(d, row)
        assert "fakework/1" not in words                                  # never the brief


# ── owner work (§7.2, OWN-1…OWN-3) ───────────────────────────────────────────────────────────────────────────
def test_owner_work_counts_a_r_c_o_j_from_records():
    build = item("completed", lease_left=None, card="C-17", step="build", created_by="owner",
                 facts={"card": {"index": 0, "kind": "build"}})
    patch = {"ref": "b.patch", "sha256": "1" * 64}
    review = item("unknown", lease_left=None, card="C-17", step="review", input_artifacts=[patch],
                  facts={"card": {"index": 1, "kind": "review", "inputs_from": [{"from": "build", **patch}]}})
    relayed = item("queued", lease_left=None, card="C-17", step="fix-1", created_by="owner",
                   facts={"card": {"index": 2, "kind": "fix", "inputs_from": []}})
    fed = dict(review, request_id=str(uuid.uuid4()), input_artifacts=[{"ref": "x", "sha256": "2" * 64}])
    dq = queued(review, "unknown_resolution", reason="effect_unknown")
    events = [event("card.admitted", source="owner", at=ago(900), body={"card_id": "C-17", "card": {}}),
              dq,
              event("notice.raised", at=ago(100), body={"key": "decision:x", "decisions": [dq["event_id"]]}),
              event("decision.resolved", source="owner", item_id=review["request_id"], at=ago(50),
                    body={"decision_id": dq["event_id"], "answer": "approved", "action": "end"}),
              event("item.reassigned", source="owner", item_id=relayed["request_id"], at=ago(40),
                    body={"from_generation": 1}),
              event("owner.read", source="owner", at=ago(800), body={"reader": "owner"}),
              event("card.ready", at=ago(30), body={"card_id": "C-17", "step": "fix-1"}),
              event("notice.raised", at=ago(20), body={"key": "runtime-ready:C-17", "card_id": "C-17",
                                                        "rule": "ready"}),
              event("owner.act", source="owner", at=ago(10), body={"card_id": "C-17", "act": "merge"})]
    doc = S.build(rec([build, review, relayed], [runner("mac-1")], events=events))
    # A 1 (the admission); R 1 (a step after the first not written by the authority); C 2 (an unprompted read during
    # the card, and a reassignment no notice named); O 1 (the merge, after its ready notice); J 2 (the unknown, the
    # reassignment)
    assert doc["owner_work"] == [{"card_id": "C-17", "A": 1, "R": 1, "C": 2, "O": 1, "J": 2}]
    # created_by is a caller field: a step the authority "wrote" whose inputs are not its recorded sources is a relay
    fed_doc = S.build(rec([build, review, relayed, fed], [runner("mac-1")], events=events))
    assert fed_doc["owner_work"][0]["R"] == 2


# ── the read path, state.json, and views (R7.6-a, R4.6-b) ─────────────────────────────────────────────────────
def test_the_read_path_takes_only_a_read_credential_and_never_echoes_it():
    observer, owner, watch = "o" * 40, "w" * 40, "s" * 40
    readers = {"collector": {"sha256": hashlib.sha256(observer.encode()).hexdigest(), "role": "observer"},
               "robert": {"sha256": hashlib.sha256(owner.encode()).hexdigest(), "role": "owner"}}
    seen = []
    doc = {"schema": S.SCHEMA}
    for headers, status in (({}, 401), ({"Authorization": "Bearer " + watch}, 401), ({"Authorization": observer}, 401),
                            ({"authorization": "Bearer " + observer}, 200), ({"Authorization": "Bearer " + owner}, 200)):
        code, body = S.read_request(headers, readers=readers, provide=lambda: doc,
                                    note_read=lambda *a: seen.append(a))
        assert code == status and observer not in json.dumps(body) and owner not in json.dumps(body)
    assert seen == [("collector", "observer"), ("robert", "owner")]
    with pytest.raises(ValueError):
        S.check_readers({"x": {"sha256": "nothex", "role": "observer"}})
    with pytest.raises(ValueError):
        S.check_readers({"x": {"sha256": "a" * 64, "role": "host"}})      # a host or watch credential never reads


def test_state_json_is_written_atomically_and_read_back_as_a_view_with_its_age(tmp_path):
    doc = S.build(rec([item(progress=200, leased=400)], [runner("mac-1")]))
    S.write_state_file(tmp_path, doc)
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]          # no temporary file left behind
    view = S.read_state_file(tmp_path / "state.json", now=NOW + timedelta(seconds=7))
    assert view["view_age_s"] == 7.0 and not S.view_stale(view, 30) and S.view_stale(view, 5)
    assert S.view_stale(view, None)                                      # no bound configured: never fresh
    broken = tmp_path / "broken.json"
    broken.write_text("{")
    bad = S.read_state_file(broken, now=NOW)
    assert bad["view_age_s"] is None and S.view_stale(bad, 3600) and "state_file_unreadable" in bad["unknowns"]


def render_stub(path):
    """T12's stub renderer: it reads only state.json, and answers owned, stalled, admitted, unknown and the next step
    for each item, with ages, and nothing else (no database, no host file)."""
    doc = json.loads(Path(path).read_text())
    out = []
    for it in doc["items"]:
        out.append({"item": it["item_id"], "owned_by": it["owner_host_id"], "shown_as": it["shown_as"],
                    "stalled": it["flags"]["stalled"], "admitted": it["grant"] is not None or it["stored_state"] in
                    ("queued", "claimed", "running"), "unknown": it["flags"]["unknown"],
                    "next": it["next_authorized_step"], "progress_age_s": it["progress_age_s"],
                    "lease_left_s": it["lease_left_s"], "contact_age_s": next(
                        (h["contact_age_s"] for h in doc["hosts"] if h["host_id"] == it["owner_host_id"]), None)})
    return out


def test_t12_a_stub_renderer_answers_from_state_json_alone(tmp_path):
    stalled = item(progress=200, leased=400)
    S.write_state_file(tmp_path, S.build(rec([stalled], [runner("mac-1")])))
    (row,) = render_stub(tmp_path / "state.json")
    assert (row["owned_by"], row["shown_as"], row["stalled"], row["unknown"], row["next"]["step"]) == \
        ("mac-1", "running", True, False, "continue")
    assert (row["progress_age_s"], row["lease_left_s"], row["contact_age_s"]) == (200.0, 40.0, 5.0)


def test_legacy_items_are_todays_delegation_rules():
    now = NOW
    jobs = [{"request_id": "r1", "job_id": "j1", "actor": "codex", "scope": "build", "state": "queued",
             "created_at": now - timedelta(minutes=6), "started_at": None, "heartbeat_at": None, "brief": "b" * 90,
             "code": None, "now": now}]
    items, running = S.legacy_items(jobs)
    assert items == [{"key": "delegation-unclaimed:r1", "kind": "stalled",
                      "text": f"Codex build “{'b' * 60}” has waited 6 min and no runner on the Mac picked it up."}]
    assert running[0]["minutes"] == 6


def test_own4_an_ended_item_and_an_ended_card_each_raise_one_notice_with_the_next_step():
    plain = item("interrupted", lease_left=None, card=None, result_detail={"code": "owner_unreachable"},
                 finished_at=ago(30))
    step = item("failed", lease_left=None, card="C-30", finished_at=ago(30))
    ended = event("card.ended", at=ago(30), body={"card_id": "C-30", "step": "build", "state": "failed",
                                                  "code": "attempts_exhausted"})
    revoked = event("card.ended", at=ago(30), body={"card_id": "C-31", "state": "revoked", "code": "grant_revoked"})
    doc = S.build(rec([plain, step], [runner("mac-1")], events=[ended, revoked]))
    by_key = {n["key"]: n for n in doc["notices"]}
    assert set(by_key) == {f"delegation-ended:{plain['request_id']}", "runtime-card-ended:C-30"}   # the owner's own
    assert by_key[f"delegation-ended:{plain['request_id']}"]["next_authorized_step"]["step"] == "re-admit or leave it"
    assert by_key["runtime-card-ended:C-30"]["kind"] == "error" and "attempts_exhausted" in \
        by_key["runtime-card-ended:C-30"]["text"]


def test_n2_a_solo_class_is_never_batched_whatever_its_record_says():
    rows = [item("unknown", lease_left=None, progress=None, card=f"C-{n}") for n in range(3)]
    events = [queued(r, "unknown_resolution", reason="effect_unknown") for r in rows]
    for e in events:
        e["body"]["solo"] = False                          # a record that says otherwise is not believed
    doc = S.build(rec(rows, [runner("mac-1")], events=events))
    assert sorted((n["rule"], len(n["decisions"])) for n in doc["notices"] if n["rule"].startswith("decision")) == \
        [("decision", 1)] * 3


def test_a_stalls_one_notice_is_its_review_request_once_queued():
    stalled = item(progress=200, leased=400)
    live = S.build(rec([stalled], [runner("mac-1")]))
    (due,) = live["proposals"]
    assert [n["rule"] for n in live["notices"]] == ["stalled"]          # before the sweep: the stall's own, due
    review = queued(stalled, "review_request", reason="stalled", proposal_id=due["proposal_id"], since=due["since"],
                    timeout_s=180, owner_host_id="mac-1")
    published = S.build(rec([stalled], [runner("mac-1")], events=[review]))
    assert [n["rule"] for n in published["notices"]] == ["decisions"]   # after it: one notice, the decision's
    (d,) = published["decisions_waiting"]
    assert (d["class"], d["yes"], d["no"], d["solo"]) == ("review_request", "authorize_review", "withdraw", False)
    assert "has made no progress since" in d["words"] and "no leaves it as it is" in d["words"]
    resolved = event("decision.resolved", source="owner", body={"decision_id": review["event_id"], "answer": "declined",
                                                                "action": "withdraw"})
    later = S.build(rec([stalled], [runner("mac-1")], events=[review, resolved]))
    assert later["notices"] == []                                       # answered: the same stall is not told again


def test_t19b_a_refusal_that_names_its_login_says_run_it_first():
    """The launch contract's signin_missing carries the login command (S1-E); the ended notice quotes it (T19b's "one
    notice names the login step"). A refusal without one, or with anything but a short plain command, says nothing
    more."""
    signed_out = item("refused", card=None, lease_left=None,
                      result_detail={"code": "signin_missing", "tool": "fakecli", "login": "fakecli login"})
    plain = item("refused", card=None, lease_left=None, result_detail={"code": "route_supervised_only"})
    odd = item("refused", card=None, lease_left=None,
               result_detail={"code": "signin_missing", "login": "run this; then\nsomething else entirely"})
    failed = item("failed", card=None, lease_left=None, result_detail={"code": "exit_70", "login": "fakecli login"})
    doc = S.build(rec([signed_out, plain, odd, failed], [runner("mac-1")]))
    text = {n["item_id"]: n["text"] for n in doc["notices"] if n["rule"] == "ended"}
    assert "(signin_missing). Run `fakecli login` first. Next:" in text[signed_out["request_id"]]
    assert "Run" not in text[plain["request_id"]] and "Run" not in text[odd["request_id"]]
    assert "Run" not in text[failed["request_id"]]                     # only a refusal names its login step


def test_n3_the_login_step_comes_from_a_fixed_list_never_from_the_hosts_text():
    """S1-H review N3: the notice quoted the refusal's ``login`` text, which a host supplies, to Robert as a command
    (``rm -rf /`` passed the old pattern). Now the refusal's tool only picks a command from LOGIN_COMMANDS: a known
    tool gets its own command whatever text came with it, and an unknown tool gets none."""
    planted = item("refused", card=None, lease_left=None,
                   result_detail={"code": "signin_missing", "tool": "fakecli", "login": "rm -rf /"})
    unknown = item("refused", card=None, lease_left=None,
                   result_detail={"code": "signin_missing", "tool": "mytool", "login": "rm -rf /"})
    codex = item("refused", card=None, lease_left=None, result_detail={"code": "signin_missing", "tool": "codex"})
    other = item("refused", card=None, lease_left=None,
                 result_detail={"code": "route_supervised_only", "tool": "codex", "login": "codex login"})
    doc = S.build(rec([planted, unknown, codex, other], [runner("mac-1")]))
    text = {n["item_id"]: n["text"] for n in doc["notices"] if n["rule"] == "ended"}
    assert "rm -rf" not in " ".join(text.values())
    assert "Run `fakecli login` first." in text[planted["request_id"]]
    assert "Run" not in text[unknown["request_id"]] and "Run" not in text[other["request_id"]]
    assert "Run `codex login` first." in text[codex["request_id"]]


def test_n3_the_fixed_login_list_is_the_launch_policys_own():
    policy = json.loads((ROOT / "core" / "mc_delegation_runner" / "launch_policy.json").read_text())["tools"]
    assert {t: policy[t]["login_command"] for t in S.LOGIN_COMMANDS} == S.LOGIN_COMMANDS
    # The rest name no command Robert could run as written: Grok's browser sign-in, fakework's none.
    assert set(policy) - set(S.LOGIN_COMMANDS) == {"grok", "fakework"}
