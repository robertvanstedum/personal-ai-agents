"""P1-a's integrated test, I-1 to I-7 (build-p1/P1_BRIEF.md §2), as a scripted harness: production's authority on EC2
and the Mac's production worker, with ``fakework`` only, through production's real check-in and Telegram.

Built and unit-tested against local fakes (``test_p1a_plan.py``). **Never run against production in this build.** It
runs later, on Robert's word, in his integrated test session, with him present for the Mac steps:

    # on EC2, in the portal image, with this commit's tests/ copied beside it (tests/ is not in the image):
    docker run --rm -it --network minimoi_default -v /opt/minimoi/harness/<commit>:/harness -w /harness \\
      -v /opt/minimoi/runtime:/run/minimoi/runtime:ro --env-file /opt/minimoi/.env \\
      -e MINIMOI_RUNTIME_AUTHORITY_FILE=/harness/core/mc_delegation_dispatcher/authority.prod.json \\
      <portal image> python -m tests.harness_acceptance.p1a run --ref <commit> --evidence /harness/evidence/<run-id> \\
      --production-session

Every admission is Robert's own exact approval, typed at the controller's console (``approve <what> <digest>``), and
recorded as the owner turn of the grant it issues: until P1-b's MC card path, the console is the owner path, as gate M's
owner test identity was. Every Mac step (sleep, Wi-Fi off, kickstart, the kill at a committed effect) is Robert's:
the controller says what to do and waits for "done". Every runtime decision is answered by Robert where production
takes answers (MiniMoi by default: "approve D-n" in the Guild chat; Telegram only if its reply path is switched on):
the controller says which code and waits for the authority to record the answer.

What it collects (the same pack as gate M's, from production): ``timeline.jsonl`` (every step, approval, Mac step and
wait), ``checks.jsonl`` (one line per check: pass, fail or not_run), ``snapshots/`` (state.json every ``poll_s``),
``audit.jsonl`` (this run's authority events), ``decisions.jsonl`` (the runtime D-codes and their receipts),
``notices.jsonl`` (the notice keys told), ``robert.jsonl`` (what Robert says he received on Telegram, I-1 and I-6),
and ``receipt.json`` (certifies nothing). The Mac's own files (the sink's ``effects.jsonl``, pending logs, census) are
copied in by Robert at the end (``p1a_mac.py pack``); the checks that read them are ``not_run`` until they are.

The scenarios use only Mac-side faults and production's normal operation; nothing stops production's authority or
portal (those are gate D's, on staging).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.harness_acceptance import checks as C  # noqa: E402

PLAN = "host_runtime_p1a"
SCENARIOS = ("I-1", "I-2", "I-3", "I-4", "I-5", "I-6", "I-7")
HOST_ID = "mac-prod-1"
LABEL = "ai.minimoi.runtime.prod"
SLEEP_MIN = 10                    # I-2: the Mac sleeps 10 minutes mid-step
OFFLINE_MIN = 5                   # I-3: Wi-Fi off 5 minutes
END_STATES = ("completed", "failed", "refused", "timed_out", "cancelled", "output_limit", "interrupted", "expired")


class Stop(Exception):
    """A scenario cannot go on (a fixed reason); its checks so far stand, the rest are not run."""


def program(*steps) -> str:
    return "\n".join(("fakework/1",) + steps)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def card_doc(card_id, *, briefs, ref, attempts=2, fix=1, amount=0.25):
    worker = {"actor": "fakework", "route": "fakework", "amount": amount, "cost_cap_usd": 1}
    return {"schema": "minimoi.card.v1", "card_id": card_id, "class": "build", "class_version": 1,
            "repository": "personal-ai-agents", "ref": ref, "workers": {"builder": worker, "reviewer": worker},
            "briefs": briefs, "attempts": attempts, "rounds": {"fix": fix}, "step_deadline_s": 3600}


def dup8_in(events, hosts, environment, *, need=1):
    """DUP-8 for one environment (gate M's ``checks.dup8`` is dev's): every event is the authority's, a host's or the
    owner's, every host event comes from this environment's own hosts, and every event is this environment's."""
    bad = [{"seq": e["seq"], "source": e["source"], "environment": e["environment"], "host_id": e.get("host_id")}
           for e in events if e["source"] not in ("authority", "host", "owner") or e["environment"] != environment
           or (e["source"] == "host" and e.get("host_id") not in hosts)]
    if len(events) < need:
        return False, len(events), {"no_facts": "no events"}
    return not bad, len(events), {"foreign": bad[:20]}


# ── the controller ─────────────────────────────────────────────────────────────────────────────────────────────────
class Run:
    """One integrated-test run over ``env`` (ProdEnv in production; a fake in the unit tests), its evidence written
    under ``evidence`` (which must not exist: never rerun into)."""

    def __init__(self, env, evidence, *, ref, seed=None, only=None, poll_s=10.0, clock=time.monotonic,
                 sleep=time.sleep, continue_after_fail=False):
        self.env, self.ev, self.ref = env, Path(evidence), ref
        if self.ev.exists():
            raise Stop("evidence_folder_exists")
        (self.ev / "snapshots").mkdir(parents=True)
        self.seed = seed if seed is not None else random.SystemRandom().randrange(2**31)
        self.tag = f"{self.seed % 100000:05d}"                 # this run's ids: never another run's card or job
        self.only = None if only is None else set(only)
        self.poll_s, self.clock, self.sleep = poll_s, clock, sleep
        self.snaps, self.results, self.scenario = [], [], "setup"
        self.started = datetime.now(timezone.utc)
        self.audit_mark = env.audit_mark()
        self.continue_after_fail = continue_after_fail

    # ── evidence ──
    def _append(self, name, doc):
        with open(self.ev / name, "a") as fh:
            fh.write(json.dumps(doc, sort_keys=True, default=str) + "\n")

    def log(self, kind, what, **fields):
        self._append("timeline.jsonl", {"at": datetime.now(timezone.utc).isoformat(), "scenario": self.scenario,
                                        "kind": kind, "what": what, **fields})

    def check(self, name, ok, detail=None, *, n=None, result=None):
        result = result or ("pass" if ok else "fail")
        line = {"check": name, "result": result, "scenario": self.scenario, "n": n, "detail": detail or {}}
        self.results.append(line)
        self._append("checks.jsonl", line)
        return ok

    def named(self, name, fn, *args, **kwargs):
        try:
            ok, n, detail = fn(*args, **kwargs)
        except Exception as exc:
            return self.check(name, False, {"error": type(exc).__name__})
        return self.check(name, ok, detail, n=n)

    def take(self, label="poll"):
        try:
            doc = self.env.snapshot()
        except Exception as exc:
            doc = {"unreadable": type(exc).__name__}
        n = len(self.snaps) + 1
        snap = {"at": time.time(), "n": n, "label": label, "scenario": self.scenario, "doc": doc}
        self.snaps.append(snap)
        (self.ev / "snapshots" / f"{n:05d}-{label}.json").write_text(json.dumps(snap, sort_keys=True, default=str))
        return snap

    def since(self, t) -> list:
        return [s for s in self.snaps if s["at"] >= t]

    def wait(self, pred, timeout_s, *, every=None, label="wait"):
        """Poll ``pred`` (a snapshot taken each time) until it gives something true, or None at the timeout."""
        deadline = self.clock() + timeout_s
        while True:
            self.take(label)
            got = pred()
            if got:
                return got
            if self.clock() >= deadline:
                return None
            self.sleep(every or self.poll_s)

    def watch(self, seconds, *, label="watch"):
        """Snapshots every poll for ``seconds`` (the evidence of what was shown while the Mac was away)."""
        self.wait(lambda: False, seconds, label=label)

    # ── Robert ──
    def approve(self, what, subject) -> str:
        """Robert's exact approval at the console: he types ``approve <what> <first 12 of the subject's digest>``; any
        other words refuse it (nothing is admitted). -> the owner turn id the grant records."""
        d = digest(subject)
        want = f"approve {what} {d[:12]}"
        said = self.env.ask(f"[{self.scenario}] Robert, to admit {what} exactly as shown:\n{subject}\n"
                            f"type exactly: {want}")
        ok = isinstance(said, str) and said.strip() == want
        self.log("owner", "approval", subject=what, subject_sha256=d, exact=ok)
        if not ok:
            raise Stop(f"not_approved:{what}")
        return f"console:{self.tag}:{digest(said.strip())[:24]}"

    def mac_step(self, step, instruction):
        """A step only Robert can take on the Mac; the controller waits for his "done"."""
        self.log("mac", step, instruction=instruction)
        said = self.env.ask(f"[{self.scenario}] Mac step {step}: {instruction}\nType done when it is done (or skip).")
        if not isinstance(said, str) or said.strip().lower() != "done":
            raise Stop(f"mac_step_skipped:{step}")
        self.log("mac", f"{step} done")
        return time.time()

    def robert_received(self, what) -> bool:
        said = self.env.ask(f"[{self.scenario}] Did your Telegram receive {what}? (yes/no)")
        got = isinstance(said, str) and said.strip().lower() == "yes"
        self._append("robert.jsonl", {"scenario": self.scenario, "what": what, "received": got})
        return got

    def answer(self, code, words, how):
        """Robert answers one runtime decision himself, where production takes answers; the controller waits."""
        self.log("owner", "asked to answer", code=code, how=how)
        self.env.ask(f"[{self.scenario}] Answer {code} with “{words}” ({how}). Type done when you have.")

    # ── the authority's records, through env ──
    def row(self, job):
        return self.env.row(job)

    def state(self, job):
        return (self.row(job) or {}).get("state")

    def wait_state(self, job, states, timeout_s):
        return self.wait(lambda: self.state(job) in states and self.row(job), timeout_s, label=f"state-{job}")

    def settle(self, card_id, timeout_s=1800):
        """Wait until no step of the card is live (the serial worker then takes the next scenario's work)."""
        got = self.wait(lambda: not [r for r in self.env.rows(card_id) if r["state"] not in END_STATES], timeout_s,
                        label=f"settle-{card_id}")
        self.check(f"{self.scenario}.card_settled", bool(got),
                   {"live": [r["job_id"] for r in self.env.rows(card_id) if r["state"] not in END_STATES]})

    def item_id(self, job):
        r = self.row(job)
        return None if r is None else str(r["request_id"])

    def shown(self, rid, since=None) -> list:
        return C.shown_sequence(self.snaps if since is None else self.since(since), rid)

    def notices(self, prefix) -> list:
        return [n for s in self.snaps for n in (s["doc"].get("notices") or []) if str(n.get("key", "")).startswith(prefix)]

    # ── a scenario ──
    def scenario_run(self, name, fn):
        if self.only is not None and name not in self.only:
            return
        self.scenario = name
        self.log("scenario", "start")
        try:
            fn(self)
            self.log("scenario", "ran")
        except Stop as exc:
            self.check(f"{name}.ran", False, {"stopped": str(exc)})
            self.log("scenario", "stopped", why=str(exc))
        except Exception as exc:
            self.check(f"{name}.ran", False, {"error": type(exc).__name__, "trace": traceback.format_exc()[-2000:]})
            self.log("scenario", "error", error=type(exc).__name__)

    def run(self) -> dict:
        self.log("run", "start", plan=PLAN, seed=self.seed, ref=self.ref, environment=self.env.environment)
        self.preflight()
        if not [r for r in self.results if r["result"] == "fail"]:
            for name, fn in ((s, SCENARIO_FNS[s]) for s in SCENARIO_ORDER if s in SCENARIO_FNS):
                self.scenario_run(name, fn)
                # Abort criterion: a failed check stops the run (the rest not run), unless Robert said to go on; a
                # later run takes the remaining scenarios (--only) into a new evidence folder, never this one.
                if not self.continue_after_fail and [r for r in self.results
                                                     if r["result"] == "fail" and r["scenario"] == name]:
                    self.log("run", "aborted after a failed check", scenario=name)
                    break
        self.scenario = "end"
        self.finish()
        return self.receipt()

    def preflight(self):
        """Before anything is admitted: production's authority answers, its sweeper is fresh, the Mac's worker is
        in contact and runs the bundle the release names, and nothing of this run exists yet."""
        self.scenario = "preflight"
        doc = self.take("preflight")["doc"]
        auth = doc.get("authority") or {}
        self.check("preflight.snapshot_readable", doc.get("schema") == "minimoi.runtime.state.v1",
                   {"schema": doc.get("schema"), "unknowns": doc.get("unknowns")})
        self.check("preflight.environment_prod", doc.get("environment") == self.env.environment,
                   {"environment": doc.get("environment")})
        age = auth.get("sweeper_age_s")
        self.check("preflight.sweeper_fresh", isinstance(age, (int, float)) and age <= self.env.gap_threshold_s,
                   {"sweeper_age_s": age})
        host = next((h for h in doc.get("hosts") or [] if h.get("host_id") == HOST_ID), None)
        self.check("preflight.mac_in_contact", bool(host) and host.get("reachability") == "reachable",
                   {"host": host and {k: host.get(k) for k in ("reachability", "roles", "availability", "grace_s")}})
        self.check("preflight.mac_roles", bool(host) and set(host.get("roles") or []) == {"worker", "witness"}
                   and host.get("availability") == "intermittent", {})

    def finish(self):
        try:
            self.take("final")
        except Exception:
            pass
        events = self.env.events(after=self.audit_mark)
        with open(self.ev / "audit.jsonl", "w") as fh:
            for e in events:
                fh.write(json.dumps(e, sort_keys=True, default=str) + "\n")
        try:
            for r in self.env.decisions():
                self._append("decisions.jsonl", r)
            for k in self.env.told(""):
                self._append("notices.jsonl", {"key": k})
        except Exception as exc:
            self.log("end", "supervision rows unreadable", error=type(exc).__name__)
        leftover = self.env.live_items(self.tag)
        self.check("end.no_leftover_items", not leftover, {"live": leftover,
                   "next": "Robert ends each through the owner path (store.end) before the reservations settle"})
        self.named("end.DUP-3", C.dup3, events)
        self.named("end.DUP-4", C.dup4, events)
        self.named("end.DUP-6", C.dup6, events)
        self.named("end.DUP-8", dup8_in, events, {HOST_ID}, self.env.environment)
        effects = self.ev / "mac" / "effects.jsonl"
        if effects.exists():
            lines = [json.loads(x) for x in effects.read_text().splitlines() if x.strip()]
            self.named("end.DUP-1", C.dup1, lines, need=1)
            self.named("end.DUP-2", C.dup2, lines, events, need=1)
        else:
            self.check("end.DUP-1", False, {"why": "the Mac's effects.jsonl is not in mac/ (p1a_mac.py pack)"},
                       result="not_run")

    def receipt(self) -> dict:
        failed = sorted(r["check"] for r in self.results if r["result"] == "fail")
        doc = {"receipt_id": f"rcpt-p1a-integrated-{self.tag}", "certifies": "nothing", "plan": PLAN,
               "seed": self.seed, "ref": self.ref, "environment": self.env.environment,
               "started": self.started.isoformat(), "ended": datetime.now(timezone.utc).isoformat(),
               "passed": sum(r["result"] == "pass" for r in self.results), "failed": failed,
               "not_run": sum(r["result"] == "not_run" for r in self.results), "snapshots": len(self.snaps)}
        (self.ev / "receipt.json").write_text(json.dumps(doc, indent=1, sort_keys=True))
        return doc


# ── the scenarios ──────────────────────────────────────────────────────────────────────────────────────────────────
def i1(run):
    """A reference card (build -> same-family review -> fix) on fakework, admitted by Robert's one exact approval.
    A = 1, R = 0, C = 0; each next step written by the authority; one "ready" notice reaches Robert's Telegram."""
    card_id = f"C-I1-{run.tag}"
    doc = card_doc(card_id, ref=run.ref, briefs={
        "build": program("checkpoint s1", "checkpoint s2", "result ok"),
        "review": program("findings needs_fix", "result ok"),
        "fix": program("checkpoint f1", "result ok")})
    turn = run.approve(card_id, json.dumps(doc, sort_keys=True))
    got = run.env.admit_card(doc, turn)
    run.check("I-1.admitted_once", got.get("state") == "admitted", {"state": got.get("state")})
    fix = run.wait_state(f"{card_id}.fix-1", ("completed",), 1800)
    rows = run.env.rows(card_id)
    later = [r for r in rows if r["step"] != "build"]
    run.check("I-1.three_steps_ran", bool(fix) and len(rows) == 3, {"steps": [(r["step"], r["state"]) for r in rows]})
    run.check("I-1.next_steps_by_the_authority", len(later) == 2 and all(
        r["created_by"] == "authority" and r["input_artifacts"] for r in later),
        {"steps": [(r["step"], r["created_by"], len(r["input_artifacts"] or [])) for r in rows]})
    ready = run.wait(lambda: run.env.told(f"runtime-ready:{card_id}"), 300, label="i1-ready")
    run.check("I-1.one_ready_notice_told", bool(ready) and len(ready) == 1, {"told": ready})
    run.check("I-1.ready_reached_robert", run.robert_received(f"one “ready” notice for {card_id}"), {})
    doc_now = run.take("i1-owner-work")["doc"]
    work = next((o for o in doc_now.get("owner_work") or [] if o.get("card_id") == card_id), None)
    run.check("I-1.OWN-1_A1", bool(work) and work.get("A") == 1, {"owner_work": work})
    run.check("I-1.OWN-2_R0", bool(work) and work.get("R") == 0, {"owner_work": work})
    run.check("I-1.OWN-3_C0", bool(work) and work.get("C") == 0, {"owner_work": work})


def _admit_card(run, card_id, build, *, effects=()):
    """One card whose build step is the scenario's program (its review reports no findings, so it then reaches ready),
    admitted by Robert's one exact approval through the authority's own owner path (``admit_card``: the card's
    delegate.card grant bound to its digest), never a grant seeded beside it (Codex's P1 brief review, B3). When the
    program posts effects, Robert first approves them exactly too (the sink's effect, fakework.sink_post). -> the build
    step's job id."""
    job = f"{card_id}.build"
    if effects:
        eturn = run.approve(f"{card_id}-effects", f"{job}: the acceptance sink's effects {', '.join(effects)}")
        build = build.replace("{grant}", run.env.effect_grant(job, effects, eturn))
    doc = card_doc(card_id, ref=run.ref, briefs={"build": build, "review": program("result ok"),
                                                 "fix": program("result ok")})
    got = run.env.admit_card(doc, run.approve(card_id, json.dumps(doc, sort_keys=True)))
    if got.get("state") != "admitted":
        raise Stop(f"not_admitted:{got.get('state')}")
    return job


def i2_i7(run):
    """I-2 with I-7 inside it (the serial worker runs one item, so I-7's census tree is I-2's item): the Mac sleeps 10
    minutes mid-step. Shown held, then lapsed, never running; no notice during the expected sleep; on wake freeze,
    replay, reconcile, resume; the effect once, the stale one refused or never sent. I-7: during the sleep no agent is
    shown running."""
    job = _admit_card(run, f"C-I2-{run.tag}", program(
        *[f"spawn t{n} role=explore hold_s=5" for n in range(1, 6)],
        "spawn long1 role=worker hold_s=3600 beat_s=5 long",
        "checkpoint s1",
        "stale_effect_on_wake e2b grant={grant} wait_s=3600 gap_s=60",
        "effect e2 grant={grant}", "result ok"), effects=("e2b", "e2"))
    if not run.wait_state(job, ("claimed", "running"), 600):
        raise Stop("never_claimed")
    rid = run.item_id(job)
    op = run.env.effect_operation_id(rid, "e2b")
    if not run.wait(lambda: run.env.events(item=rid, kind="effect.claimed", operation_id=op), 300, label="i2-lease"):
        raise Stop("e2b_lease_never_held")
    if not run.wait(lambda: len([a for a in run.env.agents(rid)]) >= 6, 120, label="i7-tree"):
        run.check("I-7.tree_seen_before_sleep", False, {"agents": len(run.env.agents(rid))})
    asleep_at = run.mac_step("sleep", f"put the Mac to sleep for {SLEEP_MIN} minutes now: "
                                      f"sudo pmset schedule wake \"$(date -v+{SLEEP_MIN}M '+%m/%d/%y %H:%M:%S')\" "
                                      "&& pmset sleepnow")
    run.watch(SLEEP_MIN * 60 - 30, label="i2-asleep")
    woke = run.mac_step("woke", "the Mac is awake and logged in")
    during = [s for s in run.snaps if asleep_at <= s["at"] <= woke]
    seq = C.shown_sequence(during, rid)
    run.check("I-2.held_then_lapsed_never_running", "held" in seq and "lapsed" in seq
              and seq.index("held") < seq.index("lapsed") and "running" not in seq[seq.index("held"):],
              {"shown": seq}, n=len(during))
    run.check("I-2.never_running_while_unreachable", not C.never_running_while_unreachable(during, rid),
              {"snapshots": C.never_running_while_unreachable(during, rid)}, n=len(during))
    told = [k for k in run.env.told("") if rid in k and asleep_at <= (run.env.told_at(k) or 0) <= woke]
    run.check("I-2.no_notice_during_expected_sleep", not told, {"told": told})
    agents_running = [(s["at"], a.get("agent_id")) for s in during for a in (s["doc"].get("agents") or [])
                      if a.get("item_id") == rid and a.get("state") == "running"]
    run.check("I-7.CEN-3_no_agent_running_while_asleep", not agents_running, {"running": agents_running[:10]},
              n=len(during))
    row = run.wait_state(job, END_STATES, 900)
    events = run.env.events(item=rid)
    kinds = [e["kind"] for e in events]
    run.check("I-2.reconciled_then_resumed", "item.reconciled" in kinds and "item.resumed" in kinds
              and kinds.index("item.reconciled") <= kinds.index("item.resumed"), {"kinds": kinds[-20:]})
    run.check("I-2.completed", bool(row) and row["state"] == "completed", {"state": row and row["state"]})
    refused = [e for e in events if e["kind"] == "effect.refused" and e["body"].get("operation_id") == op]
    run.check("I-2.stale_effect_refused_or_unsent", set(e["body"].get("code") for e in refused)
              <= {"lease_lapsed", "receipt_lease_stale"}, {"refusals": [e["body"].get("code") for e in refused]})
    run.check("I-2.no_owner_call", not run.env.decisions(item=rid), {})
    run.named("I-7.CEN-1", C.cen1, run.env.agents(rid), run.env.census_root(rid, 1), transient=5,
              long_ids={"long1"})
    run.settle(job.rsplit(".", 1)[0])


def i3(run):
    """The Mac's network drops for 5 minutes: it freezes and keeps pending records; on restore it replays once and
    resumes. No owner call."""
    job = _admit_card(run, f"C-I3-{run.tag}", program("checkpoint s1", f"checkpoint s2 hold_s={OFFLINE_MIN * 60 + 300}",
                                                      "result ok"))
    if not run.wait_state(job, ("running",), 600):
        raise Stop("never_running")
    rid = run.item_id(job)
    off = run.mac_step("wifi-off", f"turn the Mac's Wi-Fi off now, for {OFFLINE_MIN} minutes (keep it awake)")
    run.watch(OFFLINE_MIN * 60 - 30, label="i3-offline")
    on = run.mac_step("wifi-on", "turn Wi-Fi back on")
    during = [s for s in run.snaps if off <= s["at"] <= on]
    run.check("I-3.never_running_while_unreachable", not C.never_running_while_unreachable(during, rid),
              {"snapshots": C.never_running_while_unreachable(during, rid)}, n=len(during))
    row = run.wait_state(job, END_STATES, 1200)
    events = run.env.events(item=rid)
    replays = [e for e in events if e.get("replayed")]
    run.check("I-3.resumed_and_completed", bool(row) and row["state"] == "completed" and row["generation"] >= 2,
              {"state": row and row["state"], "generation": row and row["generation"]})
    run.check("I-3.each_pending_event_once", len({e["event_id"] for e in events}) == len(events),
              {"events": len(events), "replayed": len(replays)})
    run.check("I-3.no_owner_call", not run.env.decisions(item=rid), {})
    run.settle(job.rsplit(".", 1)[0])


def i4(run):
    """``launchctl kickstart -k`` the Mac's production worker mid-checkpoint: paused, then resume at the next
    generation."""
    job = _admit_card(run, f"C-I4-{run.tag}", program("checkpoint s1 hold_s=240", "checkpoint s2", "result ok"))
    if not run.wait(lambda: (run.row(job) or {}).get("state") == "running"
                    and ((run.row(job) or {}).get("checkpoint") or {}).get("name") == "s1", 600, label="i4-s1"):
        raise Stop("never_at_s1")
    rid = run.item_id(job)
    t = run.mac_step("kickstart", f"launchctl kickstart -k gui/$(id -u)/{LABEL}")
    row = run.wait_state(job, END_STATES, 900)
    seq = run.shown(rid, since=t)
    # kickstart -k stops the job: a graceful stop announces it (paused), a kill is a crash (held, then lapsed, then
    # reconciled). Either way it resumes at the next generation from its checkpoint (Codex's P1 brief review, S3).
    path = "graceful" if "paused" in seq else "crash" if ("held" in seq or "lapsed" in seq) else "unseen"
    kinds = [e["kind"] for e in run.env.events(item=rid)]
    run.check("I-4.resumed_at_the_next_generation", bool(row) and row["state"] == "completed"
              and row["generation"] >= 2 and "item.resumed" in kinds and path != "unseen",
              {"shown": seq, "path": path, "generation": row and row["generation"]})
    run.check("I-4.no_owner_call", not run.env.decisions(item=rid), {})
    run.settle(job.rsplit(".", 1)[0])


def i5(run):
    """A fake effect sink with lookup off and a kill at the committed effect: one ``unknown`` decision reaches Robert
    alone; his "end" (yes) ends it; generation unchanged."""
    job = _admit_card(run, f"C-I5-{run.tag}", program("checkpoint s1", "effect e1 grant={grant}",
                                                      "checkpoint s2 hold_s=2", "result ok"), effects=("e1",))
    if not run.wait_state(job, ("claimed", "running"), 600):
        raise Stop("never_claimed")
    rid = run.item_id(job)
    op = run.env.effect_operation_id(rid, "e1")
    run.mac_step("kill-at-commit", "already running since before this item was admitted: "
                                   f"python3 tests/harness_acceptance/p1a_mac.py kill-at-commit --op {op} "
                                   "(it kills the worker the moment the sink commits e1; launchd restarts it)")
    row = run.wait_state(job, ("unknown",), 900)
    run.check("I-5.unknown_generation_unchanged", bool(row) and row["generation"] == 1,
              {"state": row and row["state"], "generation": row and row["generation"]})
    waiting = run.wait(lambda: [d for d in (run.take("i5")["doc"].get("decisions_waiting") or [])
                                if d.get("item_id") == rid], 300, label="i5-decision")
    run.check("I-5.one_solo_decision", bool(waiting) and len(waiting) == 1 and waiting[0].get("solo") is True
              and waiting[0].get("class") == "unknown_resolution", {"decisions": waiting})
    if not waiting:
        raise Stop("no_decision")
    code = run.wait(lambda: (run.env.decision_code(waiting[0]["decision_id"])), 300, label="i5-code")
    run.check("I-5.told_alone", bool(code) and run.robert_received(f"{code} on its own"), {"code": code})
    run.answer(code, f"approve {code}", run.env.answer_channel)
    row = run.wait_state(job, ("interrupted",), 600)
    detail = (row or {}).get("result_detail") or {}
    run.check("I-5.ended_by_robert", bool(row) and detail.get("code") == "owner_decision" and row["generation"] == 1,
              {"state": row and row["state"], "code": detail.get("code")})
    run.settle(job.rsplit(".", 1)[0])


def i6(run):
    """Three routine decisions in one sweep: one batched notice; one answer per decision gives three receipts, each
    bound to its own words. (MC's review requests for three running cards: routine, R7.4-c. Production takes answers
    in MiniMoi by default, one code at a time; with Telegram's reply path on, one "yes" answers all three.)"""
    jobs = []
    for n in (1, 2, 3):
        card_id = f"C-I6-{run.tag}-{n}"
        doc = card_doc(card_id, ref=run.ref, briefs={"build": program("checkpoint s1 hold_s=120", "result ok"),
                                                     "review": program("result ok"), "fix": program("result ok")})
        run.env.admit_card(doc, run.approve(card_id, json.dumps(doc, sort_keys=True)))
        jobs.append(f"{card_id}.build")
    if not run.wait(lambda: all(run.state(j) in ("queued", "claimed", "running") for j in jobs), 300, label="i6-live"):
        raise Stop("cards_not_live")
    ids = [run.env.propose_review(j) for j in jobs]          # as MC, inside one sweep
    run.log("mc", "asks to review three items (propose_review)", decisions=ids)
    batch = run.wait(lambda: [n for n in (run.take("i6")["doc"].get("notices") or [])
                              if n.get("rule") == "decisions" and set(ids) <= set(n.get("decisions") or [])],
                     300, label="i6-batch")
    run.check("I-6.one_batched_notice", bool(batch) and len(batch) == 1, {"notices": len(batch or [])})
    codes = run.wait(lambda: [c for c in (run.env.decision_code(i) for i in ids) if c] if all(
        run.env.decision_code(i) for i in ids) else None, 300, label="i6-codes")
    run.check("I-6.batched_reached_robert", bool(codes) and run.robert_received(
        f"one message listing {', '.join(codes or [])}"), {"codes": codes})
    if not codes:
        raise Stop("no_codes")
    for code in codes:
        run.answer(code, f"approve {code}", run.env.answer_channel)
    done = run.wait(lambda: all((run.env.decision_status(i) or {}).get("action") == "authorize_review" for i in ids),
                    600, label="i6-applied")
    rows = run.env.decisions(ids=ids)
    bound = [r for r in rows if r.get("state") == "approved"
             and (r.get("receipt") or {}).get("decision_sha256") == r.get("text_sha256")]
    run.check("I-6.three_receipts_each_bound_to_its_words", bool(done) and len(bound) == 3
              and len({r["text_sha256"] for r in bound}) == 3, {"approved_bound": len(bound)}, n=len(rows))
    cards = [j.rsplit(".", 1)[0] for j in jobs]
    finished = run.wait(lambda: all(run.state(f"{c}.review") == "completed" for c in cards), 1800, label="i6-done")
    run.check("I-6.cards_went_on_to_ready", bool(finished), {"reviews": [run.state(f"{c}.review") for c in cards]})


def witness_delivery(run):
    """Production's two witnesses deliver, proved without taking production's authority down (Codex's P1 brief
    review, S3): each sends one probe notice through its installed configuration (its own notifier, its references),
    and Robert says whether it reached his Telegram. An outage itself is gate D's, on staging."""
    run.mac_step("ec2-witness-probe", "on EC2: docker exec minimoi-runtime-witness python -m "
                                      "core.mc_delegation_runner.witness probe")
    run.check("W.ec2_witness_delivers", run.robert_received("the EC2 witness's probe"), {})
    run.mac_step("mac-witness-probe", "on the Mac: /usr/bin/python3 -I -c \"import sys; sys.path.insert(0, "
                                      "'$HOME/minimoi-runtime/prod/bundle'); from core.mc_delegation_runner import "
                                      "witness; sys.exit(witness.main(['probe', '--host-config', "
                                      "'$HOME/minimoi-runtime/prod/host.yaml']))\"")
    run.check("W.mac_witness_delivers", run.robert_received("the Mac witness's probe"), {})


SCENARIO_FNS = {"W": witness_delivery, "I-1": i1, "I-2": i2_i7, "I-3": i3, "I-4": i4, "I-5": i5, "I-6": i6}
SCENARIO_ORDER = ("W",) + SCENARIOS
# I-7 runs inside I-2 (the serial worker), so it has no driver of its own; selecting I-7 alone runs I-2.


def selected(only):
    if not only:
        return None
    names = {s.strip() for s in only.split(",") if s.strip()}
    if not names <= set(SCENARIO_ORDER):
        raise SystemExit("unknown scenario")
    if "I-7" in names:
        names.add("I-2")
    return names


# ── production's environment (never used by the unit tests) ────────────────────────────────────────────────────────
class ProdEnv:
    """Production, read and written only through the authority's own store and the supervision store, over the
    portal's database (DATABASE_URL): the owner path for admissions and effect grants (each with Robert's console
    turn), MC's review proposals, and reads. Never constructed without ``--production-session``."""
    environment = "prod"

    def __init__(self, environ):
        import psycopg2
        from core.mc_delegation_dispatcher import production
        from core.supervision.store import Store
        dsn = environ.get("DATABASE_URL")
        path = environ.get("MINIMOI_RUNTIME_AUTHORITY_FILE")
        state = environ.get("MINIMOI_RUNTIME_STATE_FILE")
        if not (dsn and path and state):
            raise Stop("prod_env_incomplete")
        self.principal = environ.get("MINIMOI_RUNTIME_PRINCIPAL") or "robert"
        self._connect = lambda: psycopg2.connect(dsn, connect_timeout=5)       # noqa: E731
        self.authority = production.load(path, "prod")
        self.store = production.store(self._connect, principal=self.principal, environment="prod",
                                      authority=self.authority)
        self.sup = Store(self._connect)
        self.state_file = state
        self.gap_threshold_s = self.authority.sweeper.gap_threshold_s
        self.answer_channel = ("in MiniMoi: say it to MC in the Guild chat" if
                               environ.get("MINIMOI_RUNTIME_TELEGRAM_ANSWERS") != "1"
                               else "a reply to its Telegram message")

    def ask(self, prompt):
        print("\n" + prompt, flush=True)
        return input("> ")

    def snapshot(self):
        from core.mc_delegation_dispatcher.snapshot import read_state_file
        return read_state_file(self.state_file)

    def _sql(self, sql, args=()):
        from psycopg2.extras import RealDictCursor
        with self._connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, args)
            return cur.fetchall() if cur.description else []

    def audit_mark(self):
        rows = self._sql("SELECT coalesce(max(seq), 0) AS s FROM guild.mc_delegation_events WHERE principal_id=%s "
                         "AND environment='prod'", (self.principal,))
        return rows[0]["s"]

    def events(self, item=None, after=None, kind=None, operation_id=None):
        sql = ["SELECT * FROM guild.mc_delegation_events WHERE principal_id=%s AND environment='prod'"]
        args = [self.principal]
        if item is not None:
            sql.append("AND item_id=%s")
            args.append(item)
        if after is not None:
            sql.append("AND seq > %s")
            args.append(after)
        if kind is not None:
            sql.append("AND kind=%s")
            args.append(kind)
        if operation_id is not None:
            sql.append("AND body->>'operation_id'=%s")
            args.append(operation_id)
        return self._sql(" ".join(sql) + " ORDER BY seq", tuple(args))

    def row(self, job):
        return self.store.by_job(job)

    def rows(self, card_id):
        return self._sql("SELECT * FROM guild.mc_delegations WHERE card_id=%s ORDER BY queue_seq", (card_id,))

    def agents(self, rid):
        return [a for a in self.store.agents() if a.get("item_id") == rid]

    def census_root(self, rid, generation):
        from core.mc_delegation_runner import runner
        return runner.census_agent_id(f"{rid}-g{generation}")

    def admit_card(self, doc, turn):
        """The owner path for P1-a's test cards only: class ``build``, every worker fakework on the fakework route,
        this run's ref; anything else is refused before the authority sees it."""
        workers = (doc.get("workers") or {}).values()
        if doc.get("class") != "build" or not workers or not all(
                w.get("actor") == "fakework" and w.get("route") == "fakework" for w in workers) \
                or doc.get("ref") != self.ref:
            raise Stop("card_not_a_p1a_test_card")
        now = datetime.now(timezone.utc)
        return self.store.admit_card(doc, grant_id=str(uuid.uuid4()), owner_turn_ids=(turn,),
                                     proposal_id=str(uuid.uuid4()), approved_operation_id=str(uuid.uuid4()),
                                     approval_expires_at=now + timedelta(seconds=300), by=self.principal,
                                     note="P1-a integrated test, Robert's console approval")

    def effect_grant(self, job, keys, turn):
        from core.mc_delegation_dispatcher.production import SINK_OPERATION
        from core.mc_delegation_dispatcher.store import EFFECT_POLICY
        from core.minimoi_actions.grants import GrantDefinition
        from core.minimoi_actions.requests import ToolRequest
        gid = str(uuid.uuid4())
        digests = tuple(ToolRequest(SINK_OPERATION, 1, json.dumps({"op": k}).encode()).arguments_sha256 for k in keys)
        with self._connect() as conn:
            self.store._grants.issue(conn, GrantDefinition(
                gid, self.principal, "prod", job, (turn,), ("fakework",), EFFECT_POLICY, SINK_OPERATION, 1, digests,
                len(keys), datetime.now(timezone.utc) + timedelta(hours=2), work_id=job))
        return gid

    def effect_operation_id(self, rid, key):
        from core.mc_delegation_dispatcher.store import effect_operation_id
        return effect_operation_id(rid, key)

    def propose_review(self, job):
        return self.store.propose_review(str(self.row(job)["request_id"]), by="mc")["decision_id"]

    def decision_status(self, did):
        return self.store.decisions_status([did]).get(did)

    def decision_code(self, did):
        rows = self._sql("SELECT id FROM guild.supervision_decisions WHERE request_key=%s ORDER BY id DESC LIMIT 1",
                         ("rt-" + did,))
        return rows and f"D-{rows[0]['id']}"

    def decisions(self, ids=None, item=None):
        sql = ("SELECT d.id, d.request_key, d.state, d.receipt, d.text_sha256, d.telegram_message_id, m.message_id "
               "AS batch_message_id, d.requester FROM guild.supervision_decisions d LEFT JOIN "
               "guild.supervision_decision_messages m ON m.decision_id = d.id WHERE d.request_key LIKE 'rt-%%'")
        rows = self._sql(sql)
        if ids is not None:
            rows = [r for r in rows if r["request_key"].split(".")[0][3:] in set(ids)]
        if item is not None:
            mine = {str(e["event_id"]) for e in self.events(item=item, kind="decision.queued")}
            rows = [r for r in rows if r["request_key"].split(".")[0][3:] in mine]
        return rows

    def live_items(self, tag):
        return [r["job_id"] for r in self._sql(
            "SELECT job_id FROM guild.mc_delegations WHERE route IS NOT NULL AND job_id LIKE %s AND state <> ALL(%s)",
            (f"%{tag}%", list(END_STATES) + ["expired"]))]

    def told(self, prefix):
        return [r["key"] for r in self._sql("SELECT key FROM guild.supervision_alerts WHERE key LIKE %s ORDER BY key",
                                            (prefix.replace("%", "") + "%",))]

    def told_at(self, key):
        rows = self._sql("SELECT first_at FROM guild.supervision_alerts WHERE key=%s", (key,))
        return rows[0]["first_at"].timestamp() if rows and rows[0].get("first_at") else None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m tests.harness_acceptance.p1a")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="the integrated test, in production, with Robert present")
    r.add_argument("--ref", required=True, help="the release commit (40 hex): every item starts from it")
    r.add_argument("--evidence", required=True, type=Path)
    r.add_argument("--only", help="comma list of I-1..I-7")
    r.add_argument("--continue-after-fail", action="store_true", help="go on to the next scenario after a failure")
    r.add_argument("--production-session", action="store_true",
                   help="required: says this is Robert's integrated test session in production, on his word")
    args = p.parse_args(argv)
    if not args.production_session:
        print("refused: the integrated test runs only in Robert's production session (--production-session)",
              file=sys.stderr)
        return 2
    if len(args.ref) != 40 or any(c not in "0123456789abcdef" for c in args.ref):
        print("refused: --ref must be a 40-hex commit", file=sys.stderr)
        return 2
    env = ProdEnv(os.environ)
    env.ref = args.ref
    out = Run(env, args.evidence, ref=args.ref, only=selected(args.only),
              continue_after_fail=args.continue_after_fail).run()
    print(json.dumps(out, sort_keys=True))
    return 0 if not out["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
