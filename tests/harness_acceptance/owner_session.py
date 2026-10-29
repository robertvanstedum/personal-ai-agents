"""Gate M's owner-present steps (host runtime spec v0.5 §9.5; S1-H), attached to the launchd job Robert loaded:
T0m, T3, T3b/X6, T4a under launchd, T6m/X7, T6c and T18c on the Mac, and the pf egress counters' reading. Built in
S1, never run in S1: each runs only in Robert's session, on his word, in the order of
build-s1/GATE_M_OWNER_SESSION_PLAN.md.

    python -m harness_acceptance prepare --plan host_runtime_v2 --evidence E --python PY --ref COMMIT
    (Robert loads the job: install_delegation_runner.sh ... --load, printed by prepare)
    python -m harness_acceptance owner-step <step> --evidence E

``prepare`` is the automated run's setup without starting a runtime: the acceptance database, folder, mirror,
tripwire and the installer's render, and ``session.json`` in the evidence folder. Each ``owner-step`` reattaches to
that evidence (its timeline and checks go on), controls the runtime only through ``launchctl`` on the dev label
(``kickstart``, ``kill``), and prints what Robert does by hand (quit an app, log out, sleep the Mac) and waits for it.
Nothing here runs ``sudo``, ``pmset`` or a logout itself: it prints those commands for Robert.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from harness_acceptance import checks as C, controller as K  # noqa: E402

STEPS = ("T0m", "T3-before", "T3-after", "T3b-before", "T3b-after", "T4a", "T6m", "T6c", "T18c", "egress")


class Session(K.Plan):
    """The run's evidence and acceptance folder, reattached; the runtime is launchd's."""
    attached = True

    def __init__(self, evidence):
        doc = json.loads((Path(evidence) / "session.json").read_text())
        super().__init__(evidence, server_dsn=None, acceptance=doc["acceptance"], ref=doc["ref"],
                         python=doc["python"], seed=doc["seed"], resume=True)
        self.session = doc
        self.dsn = (self.base / "acceptance.dsn").read_text().strip()
        self.seq = sum(1 for _ in open(self.timeline)) if self.timeline.exists() else 0
        self.install = json.loads((self.base / "install.json").read_text())
        self.bundle_digest = self.install["bundle_digest"]
        self.plist = json.loads(json.dumps(doc["plist"]))
        self.host_doc = json.loads((self.base / "host.yaml").read_text())
        self.trip_log = self.ev / "tripwire.jsonl"
        self.ref, self.mirror = doc["mirror_ref"], self.acc / "mirror.git"
        self.label, self.domain = self.plist["Label"], f"gui/{os.getuid()}"
        self.instruments()

    # ── the runtime, through launchd only ──
    def launchctl(self, *args, why=""):
        self.log("injection", "launchctl " + " ".join(args), why=why)
        return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=30)

    def pid(self):
        out = subprocess.run(["launchctl", "print", f"{self.domain}/{self.label}"], capture_output=True, text=True,
                             timeout=10).stdout
        m = re.search(r"^\s*pid = (\d+)", out, re.M)
        return int(m.group(1)) if m else None

    def runtime_start(self, why, extra_env=None, wait=True):
        if self.pid() is None:
            self.launchctl("kickstart", f"{self.domain}/{self.label}", why=why)
        started = time.time()
        if wait and not self.wait(lambda: self._contacted_since(started - 30), 90, every=1):
            raise K.Stop("runtime_never_contacted")

    def _contacted_since(self, t):
        r = self.sql("SELECT seen_at FROM guild.mc_delegation_runners WHERE runner_id=%s", (f"{K.HOST_ID}:runner",))
        return bool(r) and r[0]["seen_at"].timestamp() >= t - 1

    def runtime_signal(self, sig, why):
        self.launchctl("kill", signal.Signals(sig).name, f"{self.domain}/{self.label}", why=why)

    def runtime_kill(self, why):
        self.runtime_signal(signal.SIGKILL, why)

    def run_scenario(self, name, fn):
        self.only = None
        super().run_scenario(name, fn)

    def ask(self, words):
        """What Robert does by hand, printed; the step waits for his Enter."""
        self.log("prompt", words)
        print("\n>>> " + words + "\n    Press Enter when done.", flush=True)
        input()

    # ── T0m: install by launchd; kill once; doctor --do-not-resume; the resume refusal ──
    def m_t0m(self):
        facts = self.sql("SELECT bundle_digest FROM guild.mc_delegation_runners WHERE runner_id=%s",
                         (f"{K.HOST_ID}:runner",))
        self.check("T0m.digest_reported", bool(facts) and facts[0]["bundle_digest"] == self.bundle_digest,
                   {"reported": facts and facts[0]["bundle_digest"], "installed": self.bundle_digest})
        before = self.pid()
        self.runtime_kill("T0m: kill once")
        back = self.wait(lambda: (self.pid() or before) != before and self.pid(), 60, every=1)
        self.check("T0m.restarted_within_the_throttle", bool(back), {"before": before, "after": back})
        doc = self._doctor_dnr()
        ids = doc.get("do_not_resume") or []
        self.check("T0m.both_ids_listed", {"01a17dde-fb26-7923-8105-c7478428d17d",
                                           "8c4e1a20-6f3b-4d9e-a7c1-2b5d9e0f4a11"} <= set(ids), {"ids": ids})
        job = "T0m.resume"
        self.owner.item(job, self.program("resume_session 01a17dde-fb26-7923-8105-c7478428d17d", "result ok"))
        row = self.wait_state(job, ("refused",), 90)
        rid = row and str(row["request_id"])
        self.check("T0m.resume_refused_before_any_spawn", bool(row) and (row["result_detail"] or {}).get("code")
                   == "do_not_resume" and not (self.artifacts / f"{rid}-g1.log").exists(),
                   {"detail": row and row["result_detail"]})
        self.mc0()

    def _doctor_dnr(self):
        cmd = [self.python, "-I", str(self.base / "bundle" / "run_runner.py"), "doctor", "--host-config",
               str(self.base / "host.yaml"), "--do-not-resume"]
        out = subprocess.run(cmd, env=dict(self.plist["EnvironmentVariables"]), capture_output=True, text=True,
                             timeout=60, cwd="/")
        try:
            return json.loads(out.stdout)
        except ValueError:
            return {}

    # ── T3: quit the Claude app, close every setup terminal ──
    def m_t3_before(self):
        self.owner.item("T3.W-M", self.program(*[f"checkpoint c{n} hold_s=30" for n in range(1, 13)], "result ok"))
        self.wait(lambda: (self.row("T3.W-M")["checkpoint"] or {}).get("name"), 90)
        self._note({"t3_pid": self.pid(), "t3_at": time.time()})
        print("\n>>> Now quit the Claude app and close every setup terminal. Then, from a fresh terminal, run:\n"
              f"    python -m harness_acceptance owner-step T3-after --evidence {self.ev}\n", flush=True)

    def m_t3_after(self):
        note = self._read_note()
        pid = self.pid()
        cps = [e for e in self.events(item=self.row("T3.W-M")["request_id"]) if e["kind"] == "item.progress"
               and e["at"].timestamp() > note["t3_at"]]
        ppid = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        self.check("T3.progress_kept_advancing", len(cps) >= 2, {"checkpoints_since": len(cps)}, n=len(cps))
        self.check("T3.same_pid_parent_launchd", pid == note["t3_pid"] and ppid == "1",
                   {"before": note["t3_pid"], "now": pid, "ppid": ppid})
        self.mc0()

    # ── T3b / X6: log out mid-checkpoint, log in ──
    def m_t3b_before(self):
        self.owner.item("T3b.W-M", self.program("checkpoint s1 hold_s=120", "checkpoint s2", "result ok"))
        self.wait(lambda: (self.row("T3b.W-M")["checkpoint"] or {}).get("name") == "s1", 90)
        self._note({"t3b_at": time.time(), "t3b_seq": self.max_seq()})
        print("\n>>> Now log out of the Mac (Apple menu > Log Out) while s1 is held. Log back in, start Docker (or "
              "Colima) so the acceptance database is back, then run:\n"
              f"    python -m harness_acceptance owner-step T3b-after --evidence {self.ev}\n", flush=True)

    def m_t3b_after(self):
        self.runtime_start("T3b: after login (launchd starts the job at login)")
        row = self.wait_state("T3b.W-M", ("completed", "failed", "interrupted", "unknown"), 300)
        evs = self.events(item=row["request_id"]) if row else []
        paused = [e for e in evs if e["kind"] == "item.paused"]
        lapsed = [e for e in evs if e["kind"] == "item.lapsed"]
        self.check("T3b.paused_or_held_then_lapsed", bool(paused) and paused[0]["body"].get("cause", "owner_stopped")
                   or bool(lapsed), {"paused": [e["body"] for e in paused], "lapsed": [e["body"] for e in lapsed]})
        self.check("T3b.reconciled_and_resumed", bool(row) and row["state"] == "completed" and row["generation"] >= 2,
                   {"state": row and row["state"], "generation": row and row["generation"]})
        self.named("T3b.DUP-1", C.dup1, [x for x in self.sink_lines() if row and x["item_id"] == str(row["request_id"])])
        self.named("T3b.DUP-3", C.dup3, evs, need=1)
        owner = [json.loads(x) for x in self.timeline.read_text().splitlines()]
        calls = [x for x in owner if x["kind"] == "owner" and not x.get("go") and x["seq"] > 0
                 and x.get("scenario") in ("T3b-before", "T3b-after")]
        self.check("T3b.OWN-5", not calls, {"owner_calls": len(calls)}, n=len(calls))

    # ── T4a: launchctl kickstart -k mid-checkpoint ──
    def m_t4a(self):
        job = "T4a.W-M"
        self.owner.item(job, self.program("checkpoint s1 hold_s=40", "checkpoint s2", "result ok"))
        if not self.wait(lambda: (self.row(job)["checkpoint"] or {}).get("name") == "s1", 90, every=0.5):
            raise K.Stop("s1_never_recorded")
        self.launchctl("kickstart", "-k", f"{self.domain}/{self.label}", why="T4a: mid-checkpoint")
        row = self.wait_state(job, ("completed", "failed", "interrupted", "unknown"), 240)
        evs = self.events(item=row["request_id"]) if row else []
        kinds = [e["kind"] for e in evs]
        self.check("T4a.paused_then_resume_gen2", bool(row) and row["state"] == "completed" and row["generation"] == 2
                   and "item.paused" in kinds and kinds.index("item.paused") < kinds.index("item.resumed"),
                   {"kinds": kinds[-12:], "generation": row and row["generation"]})
        self.named("T4a.DUP-3", C.dup3, evs, need=1)
        self.mc0()

    # ── T6m / X7 and T6c: whole-runtime sleep ──
    def _sleep(self, minutes, label):
        wake = time.strftime("%m/%d/%y %H:%M:%S", time.localtime(time.time() + 60 + minutes * 60))
        self.log("prompt", f"sleep for {minutes} min", wake=wake)
        print(f"\n>>> Sleep the Mac for {minutes} minutes ({label}). Run, in another terminal:\n"
              f"    sudo pmset schedule wake \"{wake}\" && pmset sleepnow\n"
              "    This step notices its own wake; do nothing else.\n", flush=True)
        last = time.time()
        while True:
            time.sleep(1)
            now = time.time()
            if now - last > minutes * 60 * 0.8:
                self.log("observe", "woke", gap_s=round(now - last))
                return now - last
            last = now

    def _stale_item(self, job):
        """An item whose worker holds e2b's receipt lease and waits (up to 30 minutes) for its own wake gap; the sleep
        is asked for only once that lease is held, so the stale send is the one T6a/T6m describe."""
        from core.mc_delegation_dispatcher.store import effect_operation_id
        gid = self.owner.effect_grant(job, "e2b", "e2")
        self.owner.item(job, self.program("checkpoint s1", f"stale_effect_on_wake e2b grant={gid} wait_s=1800 gap_s=60",
                                          f"effect e2 grant={gid}", "result ok"))
        if not self.wait(lambda: self.row(job)["state"] in ("claimed", "running"), 90, every=0.5):
            raise K.Stop("never_claimed")
        rid = str(self.row(job)["request_id"])
        op = effect_operation_id(rid, "e2b")
        if not self.wait(lambda: [e for e in self.events(item=rid) if e["kind"] == "effect.claimed"
                                  and e["body"].get("operation_id") == op], 120, every=0.5):
            raise K.Stop("e2b_lease_never_held")
        return rid

    def _after_wake(self, name, job, rid, *, must_refuse):
        from core.mc_delegation_dispatcher.store import effect_operation_id
        row = self.wait_state(job, ("completed", "failed", "interrupted", "unknown"), 300)
        stale, good = effect_operation_id(rid, "e2b"), effect_operation_id(rid, "e2")
        sink = [x for x in self.sink_lines() if x["item_id"] == rid]
        refused = [e for e in self.events(item=rid) if e["kind"] == "effect.refused"
                   and e["body"].get("operation_id") == stale]
        codes = sorted({e["body"].get("code") for e in refused})
        never_committed = not [x for x in sink if x["operation_id"] == stale and x["answer"] == "committed"]
        once = len([x for x in sink if x["operation_id"] == good and x["answer"] == "committed"]) == 1
        if must_refuse:                         # T6c: the refusal itself is observed (detection off)
            ok = bool(refused) and set(codes) <= {"lease_lapsed", "receipt_lease_stale"} and never_committed and once
        else:                                   # T6m: refused or never sent, never committed; e2 once
            ok = set(codes) <= {"lease_lapsed", "receipt_lease_stale"} and never_committed and once
        self.check(f"{name}.stale_effect_refused_or_unsent_e2_once", ok,
                   {"refusals": codes, "never_committed": never_committed, "e2_once": once})
        self.check(f"{name}.resumed", bool(row) and row["state"] == "completed", {"state": row and row["state"]})
        suspended = [e for e in self.window_events() if e["kind"] == "authority.suspended"]
        self.check(f"{name}.suspended_recorded", bool(suspended), {"outages": [e["body"] for e in suspended]})
        notices = [m for m in self.notify_since(self.window["notify0"]) if rid[:8] in m["text"]]
        witness = [m["key"] for m in self.witness_lines() if m["at"] >= self.window["at0"]]
        calls = self.owner_calls_in_window()
        self.check(f"{name}.no_notice_no_owner_call", not notices and not witness and not calls,
                   {"notices": len(notices), "witness": witness, "owner_calls": len(calls)})
        self.named(f"{name}.DUP-1", C.dup1, sink, need=1)
        self.named(f"{name}.DUP-2", C.dup2, sink, self.events(item=rid), need=1)
        self.mc0()

    def m_t6m(self):
        for minutes in (10, 20):
            job = f"T6m.W-M.{minutes}"
            rid = self._stale_item(job)
            self._sleep(minutes, f"T6m/X7, {minutes} min")
            self._after_wake(f"T6m.{minutes}min", job, rid, must_refuse=False)

    def m_t6c(self):
        self.set_host("t6c-detection-off", gap_detection="off")
        self.launchctl("kickstart", "-k", f"{self.domain}/{self.label}", why="T6c: gap detection off")
        self.runtime_start("T6c")
        try:
            job = "T6c.W-M"
            rid = self._stale_item(job)
            self._sleep(10, "T6c, detection off")
            self._after_wake("T6c", job, rid, must_refuse=True)
        finally:
            self.set_host("installed-after-t6c", gap_detection="on")
            self.launchctl("kickstart", "-k", f"{self.domain}/{self.label}", why="after T6c")

    # ── T18c: the census through a sleep ──
    def m_t18c(self):
        rid, root = self._root("T18c.root", "spawn t1 role=explore hold_s=2",
                               "spawn long1 role=worker hold_s=3600 beat_s=5 long", "checkpoint s1 hold_s=120",
                               "result ok")
        self.wait(lambda: any(a["agent_id"] == root + "/long1" for a in self.store.agents()), 60, every=3)
        self._sleep(10, "T18c")
        first = self.collector.take("t18c-first-after-wake")
        reconciled = [e for e in self.events(item=rid) if e["kind"] == "item.reconciled"]
        running = [a["agent_id"] for a in (first["doc"] or {}).get("agents", []) if a["state"] == "running"
                   and a.get("item_id") == rid]
        before = not reconciled or reconciled[0]["recorded_at"].timestamp() > first["at"]
        self.check("T18c.CEN-3_none_running_before_reconcile", not before or not running,
                   {"running": running, "snapshot_before_reconcile": before})
        self.wait_state("T18c.root", ("completed", "failed", "interrupted"), 300)
        self.mc0()

    # ── the egress counters, read by Robert with sudo ──
    def m_egress(self):
        path = self.ev / "egress-counters.txt"
        if not path.exists():
            self.check("MC-0.egress", False, {"why": f"{path.name} is missing (sudo pfctl ... -vvs rules > it)"},
                       result="not_run")
            return
        text = path.read_text()
        counts = [int(x) for x in re.findall(r"Packets:\s*(\d+)", text)] + [int(x) for x in re.findall(r"Bytes:\s*(\d+)", text)]
        self.check("MC-0.egress", bool(counts) and not any(counts), {"counters": counts}, n=len(counts))

    def _note(self, fields):
        p = self.ev / "owner-session-notes.json"
        doc = json.loads(p.read_text()) if p.exists() else {}
        doc.update(fields)
        p.write_text(json.dumps(doc, indent=1))

    def _read_note(self):
        return json.loads((self.ev / "owner-session-notes.json").read_text())


def prepare(evidence, *, dsn, python, ref, acceptance=None, seed=None):
    """The acceptance database, folder and render (nothing started, nothing loaded), and session.json."""
    run = K.Plan(evidence, server_dsn=dsn, acceptance=acceptance, ref=ref, python=python, seed=seed)
    run.runtime_start = lambda *a, **k: None                     # launchd starts it, on Robert's --load
    run.setup()
    run.collector.stop()
    run.notifier.stop()
    run.sink_server.shutdown()
    doc = {"acceptance": str(run.acc), "ref": ref, "python": python, "seed": run.seed, "mirror_ref": run.ref,
           "plist": run.plist}
    (run.ev / "session.json").write_text(json.dumps(doc, indent=1, sort_keys=True))
    load = (f"bash scripts/mc_host/install_delegation_runner.sh --host-config {run.acc}/host.template.yaml "
            f"--python {python} --ref {ref} --base {run.base} --home {run.home} --path-prefix {run.trip} --load")
    print(f"prepared {run.acc}\nRobert loads the job (his word):\n    {load}")
    return load


def owner_step(name, evidence):
    s = Session(evidence)
    fn = {"T0m": s.m_t0m, "T3-before": s.m_t3_before, "T3-after": s.m_t3_after, "T3b-before": s.m_t3b_before,
          "T3b-after": s.m_t3b_after, "T4a": s.m_t4a, "T6m": s.m_t6m, "T6c": s.m_t6c, "T18c": s.m_t18c,
          "egress": s.m_egress}[name]
    s.collector.start()
    s.notifier.start()
    try:
        s.run_scenario(name, fn)
    finally:
        s.collector.stop()
        s.notifier.stop()
        s.sink_server.shutdown()
    failed = [c["check"] for c in s.check_lines if c["result"] == "fail"]
    print(json.dumps({"step": name, "checks": len(s.check_lines), "failed": failed}, sort_keys=True))
    return 0 if not failed else 1
