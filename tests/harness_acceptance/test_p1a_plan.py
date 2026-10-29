"""P1-a's integrated test harness (tests/harness_acceptance/p1a.py), unit-tested against a local fake of production:
a small simulator of the authority's records (items, cards, decisions, notices told, the census) and of the Mac's
faults, driven by the same steps Robert takes. Nothing here reaches production, a database, the Mac or Telegram.

What this proves: the controller's sequence and its checks. A world that behaves as P1-a requires passes every check;
a world that breaks one (an item shown running while the Mac sleeps, an agent running during the sleep, a notice told
during the expected sleep, a second ready notice, a batched answer not bound to its own words) fails exactly that
check; an approval not typed exactly admits nothing; the run refuses to start outside Robert's production session and
never reruns into an evidence folder."""
import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.harness_acceptance import p1a, p1a_mac  # noqa: E402

REF = "a" * 40


class FakeProduction:
    """Enough of production for the controller: each ``snapshot()`` is one tick of the simulated authority."""
    environment = "prod"
    gap_threshold_s = 60
    answer_channel = "in MiniMoi: say it to MC in the Guild chat"

    def __init__(self, **faults):
        self.faults = faults
        self.items, self.order, self.events_, self.told_, self.decisions_ = {}, [], [], {}, {}
        self.agents_, self.codes, self.status = {}, {}, {}
        self.mac = "awake"
        self.kill_armed, self.paused, self.seq, self.code_seq = False, set(), 0, 0
        self.asked = []
        self.notices_ = []

    # ── the records ──
    def _event(self, kind, item=None, generation=None, source="authority", body=None, host=None):
        self.seq += 1
        self.events_.append({"seq": self.seq, "kind": kind, "item_id": item, "generation": generation,
                             "source": source, "environment": "prod", "host_id": host, "body": body or {},
                             "at": datetime.now(timezone.utc).isoformat(), "event_id": str(uuid.uuid4())})

    def _tell(self, key):
        self.told_.setdefault(key, __import__("time").time())

    def _add(self, job, brief, *, card=None, step=None, created_by="owner", inputs=()):
        rid = str(uuid.uuid4())
        self.items[job] = {"job_id": job, "request_id": rid, "state": "queued", "generation": 0, "card_id": card,
                           "step": step, "created_by": created_by, "input_artifacts": list(inputs), "brief": brief,
                           "checkpoint": None, "result_detail": {}, "ticks": 2, "shown": "queued", "lapse": 0}
        self.order.append(job)
        self._event("item.admitted", rid, 0, source="owner" if created_by == "owner" else "authority")
        return rid

    # ── the controller's interface ──
    def audit_mark(self):
        return 0

    def ask(self, prompt):
        self.asked.append(prompt)
        m = re.search(r"type exactly: (approve \S+(?: effects)? [0-9a-f]{12})", prompt)
        if m:
            return "approve wrongly" if self.faults.get("refuse_approvals") else m.group(1)
        if "Mac step sleep" in prompt or "Mac step wifi-off" in prompt:
            self.mac = "away"
            return "done"
        if "Mac step woke" in prompt or "Mac step wifi-on" in prompt:
            self.mac = "waking"
            return "done"
        if "Mac step kickstart" in prompt:
            self.paused.add(next(j for j in self.order if self.items[j]["state"] == "running"))
            return "done"
        if "Mac step kill-at-commit" in prompt:
            self.kill_armed = True
            return "done"
        if prompt.endswith("(yes/no)"):
            return "yes"
        m = re.search(r"Answer (D-\d+)", prompt)
        if m:
            self._answer(m.group(1))
            return "done"
        return "done"

    def _answer(self, code):
        did = next(d for d, c in self.codes.items() if c == code)
        d = self.decisions_[did]
        words = d["words"]
        sha = hashlib.sha256(words.encode()).hexdigest()
        bound = sha if not self.faults.get("unbound_receipt") else "0" * 64
        d.update(state="approved", receipt={"decision_sha256": bound}, text_sha256=sha)
        if d["class"] == "unknown_resolution":
            row = self.items[d["job"]]
            row.update(state="interrupted", result_detail={"code": "owner_decision"})
            self._event("decision.resolved", row["request_id"], row["generation"], source="owner")
            self.status[did] = {"action": "end"}
        else:
            self.status[did] = {"action": "authorize_review"}

    def snapshot(self):
        self._tick()
        reach = "reachable" if self.mac == "awake" else "unreachable"
        items = []
        for job in self.order:
            r = self.items[job]
            items.append({"item_id": r["request_id"], "shown_as": r["shown"], "owner_reachability": reach,
                          "stored_state": r["state"], "generation": r["generation"]})
        agents = [dict(a, state="unknown" if reach != "reachable" and not self.faults.get("agent_running_asleep")
                       and a["state"] == "running" else a["state"]) for v in self.agents_.values() for a in v]
        waiting = [{"decision_id": did, "item_id": self.items[d["job"]]["request_id"], "class": d["class"],
                    "solo": d["class"] == "unknown_resolution"} for did, d in self.decisions_.items()
                   if d["state"] == "new"]
        work = [{"card_id": c, "A": 1, "R": 0, "C": 0} for c in {r["card_id"] for r in self.items.values() if r["card_id"]}]
        return {"schema": "minimoi.runtime.state.v1", "environment": "prod", "authority": {"sweeper_age_s": 5},
                "hosts": [{"host_id": p1a.HOST_ID, "roles": ["worker", "witness"], "availability": "intermittent",
                           "reachability": reach, "grace_s": 28800}],
                "items": items, "agents": agents, "notices": list(self.notices_), "decisions_waiting": waiting,
                "owner_work": work}

    def _tick(self):
        if self.mac == "waking":
            for job in self.order:
                r = self.items[job]
                if r["state"] in ("running", "lapsed"):
                    self._event("item.reconciled", r["request_id"], r["generation"], source="host", host=p1a.HOST_ID)
                    r["generation"] += 1
                    r.update(state="running", shown="running")
                    self._event("item.resumed", r["request_id"], r["generation"])
            for v in self.agents_.values():
                for a in v:
                    if a["state"] == "running" and "long1" in a["agent_id"]:
                        a["state"] = "running"
            self.mac = "awake"
            return
        if self.mac == "away":
            for r in self.items.values():
                if r["state"] == "running":
                    r["lapse"] += 1
                    if self.faults.get("running_while_asleep"):
                        r["shown"] = "running"
                    else:
                        r["shown"] = "held" if r["lapse"] == 1 else "lapsed"
                    if r["lapse"] == 1 and self.faults.get("notice_during_sleep"):
                        self._tell(f"runtime-lapsed:{r['request_id']}")
            return
        running = [j for j in self.order if self.items[j]["state"] in ("claimed", "running")]
        if not running:
            queued = [j for j in self.order if self.items[j]["state"] == "queued"]
            if queued:
                r = self.items[queued[0]]
                r.update(state="running", shown="running", generation=1)
                self._event("item.claimed", r["request_id"], 1, source="authority")
                if "spawn" in r["brief"]:
                    root = f"root-{r['request_id']}-g1"
                    self.agents_[r["request_id"]] = [
                        {"agent_id": f"{root}/t{n}", "parent_id": root, "role": "explore", "state": "done",
                         "ended_at": "x", "item_id": r["request_id"], "reason": None} for n in range(1, 6)] + [
                        {"agent_id": f"{root}/long1", "parent_id": root, "role": "worker", "state": "running",
                         "ended_at": None, "item_id": r["request_id"], "reason": None}]
                if "stale_effect_on_wake" in r["brief"]:
                    self._event("effect.claimed", r["request_id"], 1, source="authority",
                                body={"operation_id": self.effect_operation_id(r["request_id"], "e2b")})
                if r["brief"].startswith("fakework/1\ncheckpoint s1 hold_s=240"):
                    r["checkpoint"] = {"name": "s1"}
            return
        job = running[0]
        r = self.items[job]
        if job in self.paused:
            self.paused.discard(job)
            r.update(shown="paused")
            r["generation"] += 1
            self._event("item.resumed", r["request_id"], r["generation"])
            return
        if self.kill_armed and "effect e1" in r["brief"]:
            self.kill_armed = False
            r.update(state="unknown", shown="unknown")
            self._event("item.unknown", r["request_id"], r["generation"])
            did = str(uuid.uuid4())
            self.decisions_[did] = {"job": job, "class": "unknown_resolution", "state": "new",
                                    "words": f"unknown {did}", "request_key": f"rt-{did}"}
            self._code(did)
            return
        if "hold_s=900" in r["brief"] and not r.get("released") and len(self.decisions_) < 3:
            return                                    # I-6's builds hold until the reviews are asked for
        r["ticks"] -= 1
        r["shown"] = "running"
        if r["ticks"] > 0:
            return
        r.update(state="completed", shown="completed")
        self._event("result.accepted", r["request_id"], r["generation"], source="host", host=p1a.HOST_ID)
        if r["card_id"]:
            self._next(r)

    def _code(self, did):
        self.code_seq += 1
        self.codes[did] = f"D-{self.code_seq}"

    def _next(self, r):
        card = r["card_id"]
        nxt = {"build": "review", "review": "fix-1" if "needs_fix" in self.cards[card]["review"] else None,
               "fix-1": None}.get(r["step"])
        if nxt is None:
            self._tell(f"runtime-ready:{card}")
            if self.faults.get("second_ready_notice"):
                self._tell(f"runtime-ready:{card}:again")
            return
        brief = self.cards[card]["fix" if nxt.startswith("fix") else nxt]
        self._add(f"{card}.{nxt}", brief, card=card, step=nxt, created_by="authority", inputs=["patch"])

    def told(self, prefix):
        return sorted(k for k in self.told_ if k.startswith(prefix))

    def told_at(self, key):
        return self.told_.get(key)

    def events(self, item=None, after=None, kind=None, operation_id=None):
        out = self.events_
        if item is not None:
            out = [e for e in out if e["item_id"] == item]
        if kind is not None:
            out = [e for e in out if e["kind"] == kind]
        if operation_id is not None:
            out = [e for e in out if e["body"].get("operation_id") == operation_id]
        return list(out)

    def row(self, job):
        return self.items.get(job)

    def rows(self, card_id):
        return [r for j, r in self.items.items() if r["card_id"] == card_id]

    def agents(self, rid):
        return self.agents_.get(rid, [])

    def census_root(self, rid, generation):
        return f"root-{rid}-g{generation}"

    cards = {}

    def admit_card(self, doc, turn):
        assert turn.startswith("console:")
        self.cards = {**self.cards, doc["card_id"]: doc["briefs"]}
        self._add(f"{doc['card_id']}.build", doc["briefs"]["build"], card=doc["card_id"], step="build")
        return {"state": "admitted"}

    def admit_item(self, job, brief, turn):
        assert turn.startswith("console:")
        self._add(job, brief)
        return "accepted"

    def effect_grant(self, job, keys, turn):
        assert turn.startswith("console:")
        return str(uuid.uuid4())

    def effect_operation_id(self, rid, key):
        return hashlib.sha256(f"{rid}:{key}".encode()).hexdigest()[:32]

    def propose_review(self, job):
        did = str(uuid.uuid4())
        self.decisions_[did] = {"job": job, "class": "review_request", "state": "new", "words": f"review {job}",
                                "request_key": f"rt-{did}"}
        waiting = [d for d, x in self.decisions_.items() if x["class"] == "review_request"]
        if len(waiting) == 3:
            self.notices_.append({"key": "decisions:abc", "rule": "decisions", "decisions": waiting})
            for d in waiting:
                self._code(d)
            for r in self.items.values():
                r["released"] = True
        return did

    def decision_code(self, did):
        return self.codes.get(did)

    def decision_status(self, did):
        return self.status.get(did)

    def decisions(self, ids=None, item=None):
        rows = [{"request_key": d["request_key"], "state": d["state"], "receipt": d.get("receipt"),
                 "text_sha256": d.get("text_sha256"), "did": did} for did, d in self.decisions_.items()]
        if ids is not None:
            rows = [r for r in rows if r["did"] in ids]
        if item is not None:
            rows = [r for r in rows if self.items[self.decisions_[r["did"]]["job"]]["request_id"] == item]
        return rows


def _run(tmp_path, **faults):
    env = FakeProduction(**faults)
    run = p1a.Run(env, tmp_path / "evidence", ref=REF, seed=12345, poll_s=0, sleep=lambda s: None)
    # the fake's time: each wait polls a bounded number of times, and each watch takes a few snapshots
    ticks = iter(range(10**6))
    run.clock = lambda: next(ticks) * 30.0
    return env, run, run.run()


def _checks(tmp_path):
    return {json.loads(x)["check"]: json.loads(x) for x in (tmp_path / "evidence" / "checks.jsonl").read_text()
            .splitlines()}


def test_a_world_that_behaves_as_p1a_requires_passes_every_check(tmp_path):
    env, run, receipt = _run(tmp_path)
    checks = _checks(tmp_path)
    failed = {k: v["detail"] for k, v in checks.items() if v["result"] == "fail"}
    assert not failed, failed
    for name in ("I-1.OWN-1_A1", "I-1.OWN-2_R0", "I-1.OWN-3_C0", "I-1.one_ready_notice_told",
                 "I-1.next_steps_by_the_authority", "I-2.held_then_lapsed_never_running",
                 "I-2.no_notice_during_expected_sleep", "I-7.CEN-3_no_agent_running_while_asleep", "I-7.CEN-1",
                 "I-2.reconciled_then_resumed", "I-3.never_running_while_unreachable", "I-4.paused_then_resumed",
                 "I-5.unknown_generation_unchanged", "I-5.one_solo_decision", "I-5.ended_by_robert",
                 "I-6.one_batched_notice", "I-6.three_receipts_each_bound_to_its_words", "end.DUP-8", "end.DUP-4",
                 "end.DUP-6"):
        assert checks[name]["result"] == "pass", (name, checks[name])
    assert checks["end.DUP-1"]["result"] == "not_run"                  # the Mac's effects.jsonl not packed yet
    assert receipt["certifies"] == "nothing" and receipt["failed"] == [] and receipt["environment"] == "prod"
    ev = tmp_path / "evidence"
    for name in ("timeline.jsonl", "audit.jsonl", "decisions.jsonl", "notices.jsonl", "robert.jsonl", "receipt.json"):
        assert (ev / name).exists(), name
    assert len(list((ev / "snapshots").iterdir())) == len(run.snaps)
    # every admission was Robert's exact typed approval: two cards' worth in I-1 and I-6, items and effect grants
    approvals = [json.loads(x) for x in (ev / "timeline.jsonl").read_text().splitlines()
                 if json.loads(x)["kind"] == "owner" and json.loads(x)["what"] == "approval"]
    assert approvals and all(a["exact"] for a in approvals) and len(approvals) == 1 + 2 + 1 + 1 + 2 + 3


@pytest.mark.parametrize("fault,check", [
    ("running_while_asleep", "I-2.never_running_while_unreachable"),
    ("agent_running_asleep", "I-7.CEN-3_no_agent_running_while_asleep"),
    ("notice_during_sleep", "I-2.no_notice_during_expected_sleep"),
    ("second_ready_notice", "I-1.one_ready_notice_told"),
    ("unbound_receipt", "I-6.three_receipts_each_bound_to_its_words"),
])
def test_a_world_that_breaks_one_requirement_fails_that_check(tmp_path, fault, check):
    _, _, receipt = _run(tmp_path, **{fault: True})
    checks = _checks(tmp_path)
    assert checks[check]["result"] == "fail", checks[check]
    assert check in receipt["failed"]


def test_an_approval_not_typed_exactly_admits_nothing(tmp_path):
    env, _, receipt = _run(tmp_path, refuse_approvals=True)
    assert env.items == {}                                            # nothing admitted anywhere
    checks = _checks(tmp_path)
    assert checks["I-1.ran"]["result"] == "fail" and "not_approved" in checks["I-1.ran"]["detail"]["stopped"]


def test_a_preflight_failure_admits_nothing(tmp_path):
    class Asleep(FakeProduction):
        def snapshot(self):
            doc = super().snapshot()
            doc["hosts"][0]["reachability"] = "unreachable"
            return doc
    env = Asleep()
    run = p1a.Run(env, tmp_path / "evidence", ref=REF, seed=1, poll_s=0, sleep=lambda s: None)
    receipt = run.run()
    assert "preflight.mac_in_contact" in receipt["failed"] and env.items == {} and not env.asked


def test_the_run_never_reruns_into_an_evidence_folder_and_needs_roberts_session(tmp_path, capsys):
    (tmp_path / "evidence").mkdir()
    with pytest.raises(p1a.Stop, match="evidence_folder_exists"):
        p1a.Run(FakeProduction(), tmp_path / "evidence", ref=REF)
    assert p1a.main(["run", "--ref", REF, "--evidence", str(tmp_path / "e2")]) == 2      # no --production-session
    assert "production session" in capsys.readouterr().err
    assert p1a.selected("I-7") == {"I-7", "I-2"}                    # I-7 runs inside I-2
    with pytest.raises(SystemExit):
        p1a.selected("I-9")


def test_dup8_is_per_environment():
    good = [{"seq": 1, "source": "host", "environment": "prod", "host_id": "mac-prod-1"},
            {"seq": 2, "source": "authority", "environment": "prod", "host_id": None}]
    assert p1a.dup8_in(good, {"mac-prod-1"}, "prod")[0]
    assert not p1a.dup8_in(good, {"mac-prod-1"}, "dev")[0]
    assert not p1a.dup8_in(good + [{"seq": 3, "source": "witness", "environment": "prod", "host_id": None}],
                           {"mac-prod-1"}, "prod")[0]


# ── the Mac's helpers ──
def test_kill_at_commit_kills_the_lock_holder_only_once_the_operation_is_committed(tmp_path):
    effects, lock = tmp_path / "effects.jsonl", tmp_path / "mac-prod-1.prod.lock"
    lock.write_text(json.dumps({"pid": 4242, "since": "x"}))
    effects.write_text(json.dumps({"operation_id": "op-other", "answer": "committed"}) + "\n")
    killed, polls = [], []

    def sleep(_):
        polls.append(1)
        if len(polls) == 3:
            with open(effects, "a") as fh:
                fh.write(json.dumps({"operation_id": "op-e1", "answer": "committed"}) + "\n")
    ticks = iter(range(10**6))
    out = p1a_mac.kill_at_commit("op-e1", effects=effects, lock=lock, kill=lambda pid, sig: killed.append((pid, sig)),
                                 clock=lambda: next(ticks) * 0.05, sleep=sleep)
    assert out["state"] == "killed" and killed == [(4242, p1a_mac.signal.SIGKILL)] and len(polls) == 3
    lock.write_text("{}")
    out = p1a_mac.kill_at_commit("op-e1", effects=effects, lock=lock, kill=lambda *a: killed.append(a))
    assert out == {"state": "no_worker_pid"} and len(killed) == 1        # no pid: nothing signalled
    out = p1a_mac.kill_at_commit("op-never", effects=effects, lock=lock, kill=lambda *a: killed.append(a),
                                 timeout_s=0.2, every_s=0.01)
    assert out == {"state": "timed_out"} and len(killed) == 1


def test_pack_copies_only_the_workers_own_files(tmp_path):
    base = tmp_path / "prod"
    (base / "acceptance").mkdir(parents=True)
    (base / "acceptance" / "effects.jsonl").write_text("{}\n")
    (base / "artifacts" / "pending").mkdir(parents=True)
    (base / "artifacts" / "pending" / "t.jsonl").write_text("{}\n")
    out = p1a_mac.pack(tmp_path / "out", base=base)
    assert out["copied"] == ["acceptance/effects.jsonl", "artifacts/pending"]
    assert (tmp_path / "out" / "effects.jsonl").exists() and (tmp_path / "out" / "pending" / "t.jsonl").exists()
