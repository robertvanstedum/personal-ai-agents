"""The gate M acceptance controller (host runtime spec v0.5 §9; S1-H):

    python -m harness_acceptance run --plan host_runtime_v2 --evidence evidence/<run-id>/ [--only T1,T4b,...]
        [--dsn-env MC_BINDING_TEST_DSN] [--authority-container NAME] [--acceptance DIR]

(``harness-acceptance`` in the spec; run from ``tests/``, or by ``test_gate_m_subset_postgres.py``.)

**What it runs.** The automated subset of gate M, on the Mac, without launchd, sleep or logout: T1 (shortened), T4b,
T4c, T4d, T8, T9, T10, T11m, T11b, T12–T17, T18a/b/d/e, T19a–T19e and X5. Every worker is ``fakework``.

**The topology** is gate M's (§9.2, R2.4-a): one host ``mac-test-1`` with the roles authority, worker and witness,
reaching the acceptance database ``minimoi_runtime_acceptance`` by DSN, built by the same migrations as dev. The
runtime is the installer's own render (``install_delegation_runner.sh --host-config``, nothing loaded): this
controller starts exactly the plist's ``ProgramArguments`` with its ``EnvironmentVariables``, as launchd would, so the
authority's own timers (liveness, the sweeper and its watch) run inside it. ``HOME`` is the acceptance folder, so
``fakecli``'s sign-in and configuration never sit in Robert's home; the test credential store is a 0600 file there,
named by absolute path in host.yaml; gate M reaches its authority by DSN file, so no door credential and no Keychain
read exist. A second runner identity (``mac-test-9``, ``instruments.ScriptedHost``) speaks the store as a host does
for the protocol-level parts; its items carry ``repo:scripted``, which the runtime under test lacks.

**Injections** (each logged in ``timeline.jsonl``): signals to the runtime process (SIGKILL, SIGTERM, SIGUSR1 for the
sweeper alone); host.yaml changes (an operator's edit, then a restart); ``docker pause``/``unpause`` of the
acceptance database's container for T8 and X5 (the Mac-only authority's store stopped as a process; without
``--authority-container`` those two are recorded ``not_run``); and, where a scenario says so, a time moved in a
database column (a lease or a grant's end), named as such.

**The evidence pack** (§9.6): ``timeline.jsonl``, ``checks.jsonl`` (every check, pass, fail or not_run, one line
each), ``snapshots/``, ``audit.jsonl`` (the exported audit), ``effects.jsonl``, the pending logs, ``tripwire.jsonl``,
``egress.json`` (the pf commands; not run without sudo), ``notify.jsonl``, ``fakecli-env.jsonl``, the launch records,
the census files, ``owner_counts.md``, ``test_values.yaml``, every ``host.yaml``, ``digests.json``, the P0 inventory
and ``receipt.json``, which certifies nothing. A failure is kept with its seed and timeline; nothing is rerun.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import random
import shutil
import signal
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for extra in (ROOT, HERE.parent, ROOT / "tests" / "mc_delegation_dispatcher", ROOT / "tests" / "mc_delegation_runner"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from harness_acceptance import checks as C, instruments as I  # noqa: E402

PLAN = "host_runtime_v2"
DB = "minimoi_runtime_acceptance"
MIGRATIONS = ("core/mc_delegation_dispatcher/sql/001_delegations.sql",
              "core/mc_delegation_dispatcher/sql/002_host_runtime_v2.sql",
              "core/minimoi_actions/sql/004_action_receipts.sql", "core/minimoi_actions/sql/005_action_grants.sql",
              "core/supervision/sql/001_supervision.sql", "core/supervision/sql/002_grok_and_exchanges.sql",
              "core/supervision/sql/003_runtime_decisions_and_watch.sql")
SCENARIOS = ("T1", "T4b", "T4c", "T4d", "T8", "T9", "T10", "T11b", "T12", "T13", "T14", "T15", "T16", "T17",
             "T18a", "T18b", "T18d", "T18e", "T19a", "T19b", "T19c", "T19d", "T19e", "T11m", "X5")
HOST_ID, SCRIPTED = "mac-test-1", "mac-test-9"
CAPS = ["repo:personal-ai-agents", "scope:research", "scope:build", "actor:fakework", "host:mac-local"]
P0 = ROOT / "planning-studio" / "initiatives" / "INIT-2026-0011-agent-harness" / "evidence" / "p0-2026-10-28"
TEST_VALUES = ROOT / "tests" / "mc_delegation_dispatcher" / "authority.test_values.json"
SCRIPTED_REF = "a" * 40
T1_INTERVALS = 6                       # T1, shortened: six contact intervals idle (90 s), not 30 minutes


class Stop(Exception):
    """A scenario cannot go on (a precondition failed); recorded, never retried."""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class Run:
    def __init__(self, evidence: Path, *, server_dsn, container=None, only=None, acceptance=None, ref="HEAD",
                 python=sys.executable, seed=None):
        self.ev = Path(evidence).resolve()
        if self.ev.exists() and any(self.ev.iterdir()):
            raise SystemExit("refused: the evidence folder is not empty (a run is never rerun into it)")
        self.ev.mkdir(parents=True, exist_ok=True)
        self.acc = Path(acceptance or str(self.ev) + ".acceptance").resolve()
        if self.acc.exists():
            raise SystemExit("refused: the acceptance folder exists (each run starts fresh)")
        self.server_dsn, self.container, self.ref_name, self.python = server_dsn, container, ref, python
        self.only = set(only) if only else None
        self.seed = seed if seed is not None else random.SystemRandom().randrange(2**31)
        random.seed(self.seed)
        self.home, self.base = self.acc / "home", self.acc / "runtime"
        self.artifacts, self.sink_dir = self.acc / "runtime" / "artifacts", self.acc / "sink"
        self.proc, self.seq, self.scenario = None, 0, None
        self.timeline, self.checks_path = self.ev / "timeline.jsonl", self.ev / "checks.jsonl"
        self.results, self.host_variants, self.planted, self.route_key = {}, {}, {}, None
        self.check_lines = []

    # ── records ──
    def log(self, kind, what, **fields):
        self.seq += 1
        line = {"seq": self.seq, "at": now_iso(), "scenario": self.scenario, "kind": kind, "what": what, **fields}
        with open(self.timeline, "a") as fh:
            fh.write(json.dumps(line, sort_keys=True, default=str) + "\n")

    def check(self, name, ok, detail=None, *, n=None, result=None):
        line = {"check": name, "scenario": self.scenario, "result": result or ("pass" if ok else "fail"),
                "n": n, "detail": detail or {}, "at": now_iso()}
        self.check_lines.append(line)
        with open(self.checks_path, "a") as fh:
            fh.write(json.dumps(line, sort_keys=True, default=str) + "\n")
        return ok

    def named(self, name, fn, *args, **kwargs):
        ok, n, detail = fn(*args, **kwargs)
        return self.check(name, ok, detail, n=n)

    # ── the database ──
    def connect(self):
        import psycopg2
        return psycopg2.connect(self.dsn, connect_timeout=5)

    def sql(self, query, args=()):
        from psycopg2.extras import RealDictCursor
        with self.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, args)
            return cur.fetchall() if cur.description else None

    def db_now(self):
        return self.sql("SELECT clock_timestamp() AS now")[0]["now"]

    def events(self, *, since=0, item=None, kinds=None):
        rows = self.sql("SELECT * FROM guild.mc_delegation_events WHERE seq > %s ORDER BY seq", (since,))
        return [r for r in rows if (item is None or str(r["item_id"]) == str(item))
                and (kinds is None or r["kind"] in kinds)]

    def max_seq(self):
        return self.sql("SELECT coalesce(max(seq), 0) AS s FROM guild.mc_delegation_events")[0]["s"]

    def row(self, job):
        return self.store.by_job(job)

    def _create_database(self):
        import psycopg2
        from psycopg2.extensions import make_dsn, parse_dsn
        params = parse_dsn(self.server_dsn)
        host = params.get("host", "")
        if not params.get("dbname", "").startswith("mc12_test_") or host not in ("127.0.0.1", "localhost", "::1"):
            raise SystemExit("refused: the acceptance server must be the local throwaway one (an mc12_test_* DSN)")
        admin = psycopg2.connect(self.server_dsn, connect_timeout=5)
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
            cur.execute(f"CREATE DATABASE {DB}")
        admin.close()
        self.dsn = make_dsn(self.server_dsn, dbname=DB)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS guild")
            for m in MIGRATIONS:
                cur.execute((ROOT / m).read_text())
        self.log("setup", "database", database=DB, migrations=list(MIGRATIONS))

    # ── the acceptance folder ──
    def _git(self, *args, cwd):
        return subprocess.run(["git", "-c", "user.email=acceptance@example.invalid", "-c", "user.name=acceptance",
                               *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def _mirror(self):
        m = self.acc / "mirror"
        m.mkdir(parents=True)
        self._git("init", "-q", "-b", "main", cwd=m)
        (m / "README.md").write_text("acceptance mirror\n")
        self._git("add", ".", cwd=m)
        self._git("commit", "-qm", "init", cwd=m)
        self.ref = self._git("rev-parse", "HEAD", cwd=m)
        self._git("checkout", "-q", "-b", "t19c", cwd=m)              # T19c: a key setting in the project's config
        (m / ".fakecli").mkdir()
        (m / ".fakecli" / "settings.json").write_text(json.dumps({"apiKeyHelper": "/bin/echo acceptance"}))
        self._git("add", ".", cwd=m)
        self._git("commit", "-qm", "project key setting", cwd=m)
        self.ref_t19c = self._git("rev-parse", "HEAD", cwd=m)
        self._git("checkout", "-q", "main", cwd=m)
        self.mirror = self.acc / "mirror.git"                     # bare, as the installer's mirrors are
        self._git("clone", "-q", "--bare", str(m), str(self.mirror), cwd=self.acc)

    def values(self) -> dict:
        """§9.2's test values: S1-A's file, which carries fakework.plan unattended for T19a–T19d (S1-E N8)."""
        return json.loads(TEST_VALUES.read_text())

    def template(self) -> dict:
        return {"schema": "minimoi.host.v1", "host_id": HOST_ID, "environment": "dev", "principal": "robert",
                "roles": ["authority", "worker", "witness"], "expected_availability": "intermittent",
                "unreachable_grace_s": 900, "service_manager": "launchd",
                "authority": {"endpoint_id": "dev-authority", "dsn_file": str(self.base / "acceptance.dsn"),
                              "values": str(self.acc / "test_values.yaml")},
                "capabilities": CAPS, "concurrency": 1, "offline_scope_default": "none",
                "artifact_root": str(self.artifacts), "workspace_root": str(self.base / "work"),
                "mirrors": {"personal-ai-agents": str(self.mirror)}, "gap_detection": "on", "stop_timeout_s": 20,
                "effect_sink": {"path": str(self.sink_dir), "lookup": True},
                "note": "gate M acceptance (S1-H): mac-test-1, §9.2's test values"}

    def setup(self):
        self.log("setup", "run", plan=PLAN, seed=self.seed, evidence=str(self.ev), acceptance=str(self.acc))
        for d in (self.home, self.base, self.sink_dir):
            d.mkdir(parents=True, mode=0o700)
        self._create_database()
        (self.base / "acceptance.dsn").write_text(self.dsn)
        os.chmod(self.base / "acceptance.dsn", 0o600)
        (self.acc / "test_values.yaml").write_text(json.dumps(self.values(), indent=1, sort_keys=True))
        self._mirror()
        self.trip_log = self.ev / "tripwire.jsonl"
        self.trip = I.install_tripwire(self.acc / "tripwire" / "bin", self.trip_log)
        tpl = self.acc / "host.template.yaml"
        tpl.write_text(json.dumps(self.template(), indent=1, sort_keys=True))
        out = subprocess.run(["/bin/bash", str(ROOT / "scripts" / "mc_host" / "install_delegation_runner.sh"),
                              "--host-config", str(tpl), "--python", self.python, "--ref", self.ref_name,
                              "--base", str(self.base), "--home", str(self.home), "--path-prefix", str(self.trip)],
                             capture_output=True, text=True, timeout=300,
                             env={**os.environ, "MINIMOI_INSTALL_PYTHON": self.python})
        if out.returncode != 0:
            raise SystemExit("the installer refused: " + out.stderr[-500:])
        self.install = json.loads((self.base / "install.json").read_text())
        self.bundle_digest = self.install["bundle_digest"]
        self.plist = plistlib.loads((self.base / "service" / "ai.minimoi.runtime.dev.plist").read_bytes())
        self.host_doc = json.loads((self.base / "host.yaml").read_text())
        self.keep_host("installed")
        self.log("setup", "installed (rendered, not loaded)", commit=self.install["commit"],
                 bundle_digest=self.bundle_digest, label=self.plist["Label"])
        from core.mc_delegation_dispatcher import cards
        from core.mc_delegation_dispatcher.store import Authority, DispatcherStore
        from core.mc_delegation_runner import fakework
        from core.minimoi_actions.grants import CapabilityRule, GrantStore
        self.grants = GrantStore({"delegate.start_build": CapabilityRule(1, True, True),
                                  "delegate.start_research": CapabilityRule(1, True, True), **cards.grant_rows(),
                                  fakework.OPERATION: CapabilityRule(1, False, True)})
        self.auth = Authority.load(self.acc / "test_values.yaml")
        self.store = DispatcherStore(self.connect, principal="robert", environment="dev", authority=self.auth,
                                     grants=self.grants)
        from harness_acceptance import effect_sink
        self.sink_server, _ = effect_sink.serve(self.sink_dir, lookup=True)
        self.sink_url = f"http://127.0.0.1:{self.sink_server.server_address[1]}"
        self.collector = I.Collector(self.store, self.ev / "snapshots", endpoint_id="dev-authority", host_id=HOST_ID)
        self.notifier = I.Notifier(self.connect, self.store, self.ev / "notify.jsonl")
        self.owner = I.Owner(self)
        self.scripted = I.ScriptedHost(self)
        self.runtime_start("first start")
        self.collector.start()
        self.notifier.start()

    # ── host.yaml variants (an operator's edit, then a restart) ──
    def keep_host(self, name):
        text = (self.base / "host.yaml").read_text()
        self.host_variants[name] = text
        (self.ev / "hosts").mkdir(exist_ok=True)
        (self.ev / "hosts" / f"host.{name}.yaml").write_text(text)

    def set_host(self, name, **over):
        doc = {**self.host_doc, **over}
        (self.base / "host.yaml").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        self.keep_host(name)
        self.log("injection", "host.yaml changed", variant=name, keys=sorted(over))

    # ── the runtime process, as launchd starts it ──
    def runtime_start(self, why, extra_env=None, wait=True):
        if self.proc is not None and self.proc.poll() is None:
            raise Stop("runtime_already_running")
        env = dict(self.plist["EnvironmentVariables"])
        env.update(extra_env or {})
        self.runtime_log = open(self.base / "runtime.log", "ab")
        started = time.time()
        self.proc = subprocess.Popen(self.plist["ProgramArguments"], env=env, stdout=self.runtime_log,
                                     stderr=subprocess.STDOUT, cwd="/", start_new_session=True)
        self.log("runtime", "start", why=why, pid=self.proc.pid, planted=sorted(extra_env or {}))
        if wait and not self.wait(lambda: self._contacted_since(started), 60, every=0.5):
            raise Stop("runtime_never_contacted")
        return self.proc

    def _contacted_since(self, t):
        if self.proc.poll() is not None:
            raise Stop(f"runtime_exited:{self.proc.returncode}")
        r = self.sql("SELECT seen_at FROM guild.mc_delegation_runners WHERE runner_id=%s", (f"{HOST_ID}:runner",))
        return bool(r) and r[0]["seen_at"].timestamp() >= t - 1

    def runtime_signal(self, sig, why):
        self.log("injection", f"signal {signal.Signals(sig).name} to the runtime", why=why, pid=self.proc.pid)
        os.kill(self.proc.pid, sig)

    def runtime_stop(self, why="stop", timeout=40):
        if self.proc is None or self.proc.poll() is not None:
            return None
        self.runtime_signal(signal.SIGTERM, why)
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.runtime_signal(signal.SIGKILL, "did not stop within its bound")
            self.proc.wait(10)
        self.log("runtime", "exited", code=self.proc.returncode)
        return self.proc.returncode

    def runtime_kill(self, why):
        self.runtime_signal(signal.SIGKILL, why)
        self.proc.wait(10)

    def host_state(self):
        try:
            return json.loads((self.artifacts / "host-state.json").read_text())
        except (OSError, ValueError):
            return {}

    # ── waiting ──
    def wait(self, pred, timeout, every=1.0):
        end = time.time() + timeout
        while True:
            try:
                got = pred()
            except Stop:
                raise
            except Exception:
                got = None
            if got:
                return got
            if time.time() >= end:
                return None
            time.sleep(every)

    def wait_state(self, job, states, timeout):
        return self.wait(lambda: (self.row(job) or {}).get("state") in states and self.row(job), timeout)

    def sink_lines(self):
        from core.mc_delegation_runner.fakework import FileSink
        return FileSink(self.sink_dir).lines()

    def notify_since(self, n):
        return self.notifier.lines()[n:]

    def program(self, *steps):
        return "\n".join(("fakework/1",) + steps)

    # ── a scenario ──
    def run_scenario(self, name, fn):
        if self.only is not None and name not in self.only:
            return
        self.scenario = name
        self.collector.scenario = name
        t0, seq0, notify0 = time.time(), self.max_seq(), len(self.notifier.lines())
        self.window = {"t0": t0, "seq0": seq0, "notify0": notify0, "trip0": len(I.tripwire_lines(self.trip_log))}
        self.log("scenario", "start")
        try:
            fn()
            outcome = "ran"
        except Stop as exc:
            outcome = f"stopped: {exc}"
            self.check(f"{name}.ran", False, {"stopped": str(exc)})
        except Exception as exc:
            outcome = f"error: {type(exc).__name__}"
            self.check(f"{name}.ran", False, {"error": type(exc).__name__, "trace": traceback.format_exc()[-2000:]})
        self.results[name] = {"outcome": outcome, "seconds": round(time.time() - t0, 1)}
        self.log("scenario", "end", outcome=outcome)
        if self.proc is None or self.proc.poll() is not None:
            try:
                self.runtime_start("after the scenario: the runtime runs between scenarios")
            except Stop as exc:
                self.check(f"{name}.runtime_restarted", False, {"stopped": str(exc)})
        self.scenario = None
        self.collector.scenario = None

    def window_events(self):
        return self.events(since=self.window["seq0"])

    def mc0(self, *, judgment_allowed=False, env_names_planted=False):
        """MC-0: the tripwire is empty; egress counters (not run: sudo); doctor --env-names names no credential
        beyond the runtime's own reference; no judgment call in the window (except T16's fakejudge)."""
        trips = I.tripwire_lines(self.trip_log)
        self.check("MC-0.tripwire", not trips, {"lines": trips[:5]}, n=len(trips))
        self.check("MC-0.egress", False, {"why": "pf counters need sudo: Robert's gate M session"}, result="not_run")
        doc = self.doctor(env_names=True)
        names = doc.get("env_names") or {}
        runtime_shaped = names.get("runtime_credential_shaped")
        allowed = set(I.PLANTED) if env_names_planted else set()
        ok = names.get("child_credential_shaped") == [] and runtime_shaped is not None \
            and set(runtime_shaped) <= allowed
        self.check("MC-0.env_names", ok, {"child": names.get("child_credential_shaped"),
                                          "runtime": runtime_shaped, "planted_by_design": env_names_planted},
                   n=len(names.get("child", [])))
        calls = [e for e in self.window_events() if e["kind"] == "judgment.reserved"]
        self.check("MC-0.judgment", judgment_allowed or not calls, {"calls": len(calls)}, n=len(calls))

    def doctor(self, *, env_names=False, extra_env=None):
        env = {**self.plist["EnvironmentVariables"], **(extra_env or {})}
        cmd = [self.python, "-I", str(self.base / "bundle" / "run_runner.py"), "doctor", "--host-config",
               str(self.base / "host.yaml")] + (["--env-names"] if env_names else [])
        out = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60, cwd="/")
        try:
            return json.loads(out.stdout)
        except ValueError:
            return {"unreadable": out.returncode}

    # ── helpers shared by scenarios ──
    def snapshots_between(self, t0, t1=None):
        return [s for s in self.collector.since(t0) if t1 is None or s["at"] <= t1]

    def watch(self, seconds, every=3.0, label="watch"):
        """Take a snapshot every ``every`` s for ``seconds`` (a scenario's own close observation)."""
        end = time.time() + seconds
        while time.time() < end:
            self.collector.take(label)
            time.sleep(every)

    def item_in(self, doc, rid):
        return next((i for i in (doc or {}).get("items", []) if i["item_id"] == str(rid)), None)

    def notices_raised(self, *, prefix=None):
        return [e for e in self.window_events() if e["kind"] == "notice.raised"
                and (prefix is None or e["body"].get("key", "").startswith(prefix))]

    def delivered(self, text_part):
        return [m for m in self.notify_since(self.window["notify0"]) if text_part in m["text"]]

    def owner_calls_in_window(self):
        lines = [json.loads(x) for x in self.timeline.read_text().splitlines()]
        return [x for x in lines if x["kind"] == "owner" and x.get("scenario") == self.scenario and not x.get("go")]

    def end_leftover(self, job, why):
        """A scenario's item still open at its end is stopped or ended by the owner, logged as clean-up (not one of
        the scenario's owner calls), so the serial host is free for the next scenario."""
        row = self.row(job)
        if row is None or row["state"] in ("completed", "failed", "refused", "timed_out", "cancelled", "output_limit",
                                           "interrupted", "expired"):
            return
        self.log("cleanup", f"{job} {row['state']}: {why}")
        try:
            if row["state"] in ("queued", "claimed", "running", "proposed"):
                self.store.stop(job)
            if self.row(job)["state"] in ("lapsed", "paused", "unknown") or (
                    self.row(job)["state"] in ("claimed", "running") and row["lease_until"] is not None
                    and row["lease_until"] < self.db_now()):
                self.store.end(str(row["request_id"]), by=I.OWNER_ID, note="acceptance clean-up")
        except Exception as exc:
            self.log("cleanup", f"{job}: {type(exc).__name__}")

    def card_doc(self, card_id, *, briefs, attempts=2, fix=1, reviewer=None, repository="personal-ai-agents",
                 ref=None, amount=0.05, **extra):
        worker = {"actor": "fakework", "route": "fakework", "amount": amount, "cost_cap_usd": 1}
        doc = {"schema": "minimoi.card.v1", "card_id": card_id, "class": "build", "class_version": 1,
               "repository": repository, "ref": ref or self.ref,
               "workers": {"builder": worker, "reviewer": reviewer or worker}, "briefs": briefs,
               "attempts": attempts, "rounds": {"fix": fix}, "step_deadline_s": 1800}
        doc.update(extra)
        return doc

    # ── the end of the run ──
    def finish(self):
        self.scenario = "end"
        try:
            self.notifier.tick()
            self.collector.take("final")
        except Exception:
            pass
        self.collector.stop()
        self.notifier.stop()
        self.runtime_stop("end of the run")
        try:
            self._final_checks()
        except Exception as exc:
            self.check("end.final_checks", False, {"error": type(exc).__name__, "trace": traceback.format_exc()[-2000:]})
        self._reap_orphans()
        self.sink_server.shutdown()
        self._pack()

    def _final_checks(self):
        events = self.events()
        self.audit = events
        effects = self.sink_lines()
        self.named("DUP-1", C.dup1, effects, need=1)
        self.named("DUP-2", C.dup2, effects, events, need=1)
        self.named("DUP-3", C.dup3, events, need=1)
        self.named("DUP-4", C.dup4, events, need=1)
        replay = subprocess.run([self.python, "-I", str(self.base / "bundle" / "run_runner.py"), "journal",
                                 "--host-config", str(self.base / "host.yaml"), "replay", "--again"],
                                env=dict(self.plist["EnvironmentVariables"]), capture_output=True, text=True,
                                timeout=300, cwd="/")
        try:
            got = json.loads(replay.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            got = {"unreadable": replay.returncode}
        self.check("DUP-5", got.get("applied") == 0 and got.get("sent", 0) > 0 and not got.get("damaged"),
                   got, n=got.get("sent"))
        self.named("DUP-6", C.dup6, events, need=1)
        self.named("DUP-7", C.dup7, events, self.notifier.lines(), need=1)
        self.named("DUP-8", C.dup8, events, {HOST_ID, SCRIPTED, "mac-test-8"}, need=1)
        values = list(self.planted.values()) + ([self.route_key] if self.route_key else [])
        if values:
            records = [json.loads(p.read_text()) for p in self.artifacts.glob("*.launch.json")]
            files = [p for root in (self.ev, self.acc) for p in root.rglob("*")
                     if p.is_file() and not p.is_symlink() and p.name != "test-credentials.json"]
            self.named("LCH-6.end", C.lch6, records, files, values, need=1)

    def _reap_orphans(self):
        """Any fakework left by a SIGKILLed runtime: verified by its command line before it is signalled."""
        for st in self.artifacts.glob("*.stage.json"):
            try:
                pgid = json.loads(st.read_text()).get("pgid")
                cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pgid)], capture_output=True,
                                     text=True).stdout
                if pgid and ("fakework" in cmd or "fakecli" in cmd):
                    os.killpg(int(pgid), signal.SIGKILL)
                    self.log("cleanup", "orphan stopped", pgid=pgid)
            except (OSError, ValueError, TypeError):
                pass

    def _pack(self):
        ev = self.ev
        (ev / "audit.jsonl").write_text("".join(json.dumps(e, sort_keys=True, default=str) + "\n"
                                                for e in getattr(self, "audit", [])))
        shutil.copy(self.sink_dir / "effects.jsonl", ev / "effects.jsonl") if (self.sink_dir / "effects.jsonl").exists() \
            else (ev / "effects.jsonl").write_text("")
        if not self.trip_log.exists():
            self.trip_log.write_text("")
        for sub, pattern in (("pending", "*.pending.jsonl"), ("launch", "*.launch.json")):
            (ev / sub).mkdir(exist_ok=True)
            for p in self.artifacts.glob(pattern):
                shutil.copy(p, ev / sub / p.name)
        if (self.artifacts / "census").exists():
            shutil.copytree(self.artifacts / "census", ev / "census", dirs_exist_ok=True)
        cli_env = self.acc / "tools" / "fakecli" / "var" / "fakecli-env.jsonl"
        (ev / "fakecli-env.jsonl").write_text(cli_env.read_text() if cli_env.exists() else "")
        (ev / "egress.json").write_text(json.dumps({"state": "not_run", "why": "pf needs sudo (Robert's session)",
                                                    **I.egress_commands()}, indent=1))
        shutil.copy(self.acc / "test_values.yaml", ev / "test_values.yaml")
        shutil.copy(self.base / "install.json", ev / "install.json")
        shutil.copy(self.base / "runtime.log", ev / "runtime.log")
        if (P0 / "P0_LIVE_INVENTORY.md").exists():
            shutil.copy(P0 / "P0_LIVE_INVENTORY.md", ev / "P0_LIVE_INVENTORY.md")
        self._owner_counts()
        digests = {"bundle_digest": self.bundle_digest, "commit": self.install["commit"],
                   "launch_policy": self._policy_digest(),
                   "test_values": "sha256:" + hashlib.sha256((ev / "test_values.yaml").read_bytes()).hexdigest(),
                   "hosts": {n: "sha256:" + hashlib.sha256(t.encode()).hexdigest() for n, t in self.host_variants.items()},
                   "migrations": {m: "sha256:" + hashlib.sha256((ROOT / m).read_bytes()).hexdigest() for m in MIGRATIONS}}
        (ev / "digests.json").write_text(json.dumps(digests, indent=1, sort_keys=True))
        summary = {}
        for line in self.check_lines:
            summary[line["result"]] = summary.get(line["result"], 0) + 1
        receipt = {"receipt_id": f"rcpt-gate-m-subset-{ev.name}", "certifies": "nothing", "plan": PLAN,
                   "seed": self.seed, "scenarios": self.results, "checks": summary,
                   "failed": [c["check"] for c in self.check_lines if c["result"] == "fail"],
                   "not_run": [c["check"] for c in self.check_lines if c["result"] == "not_run"],
                   "commit": self.install["commit"], "bundle_digest": self.bundle_digest, "at": now_iso()}
        (ev / "receipt.json").write_text(json.dumps(receipt, indent=1, sort_keys=True))
        self.receipt = receipt

    def _policy_digest(self):
        from core.mc_delegation_runner import launch_env
        return launch_env.POLICY_DIGEST

    def _owner_counts(self):
        doc = (self.collector.taken[-1]["doc"] if self.collector.taken else {}) or {}
        lines = ["# Owner work per card (§7.2), beside §7.3's first-slice column", "",
                 "| card | A | R | C | O | J | first slice |", "|---|---|---|---|---|---|---|"]
        for o in doc.get("owner_work", []):
            lines.append(f"| {o['card_id']} | {o['A']} | {o['R']} | {o['C']} | {o['O']} | {o['J']} | "
                         f"A 1, R 0, C 0, O 1, J per unknown |")
        (self.ev / "owner_counts.md").write_text("\n".join(lines) + "\n")
        (self.ev / "owner_counts.json").write_text(json.dumps(doc.get("owner_work", []), indent=1))


# ═════════════════════════════════════════════ the scenarios ═════════════════════════════════════════════════════
class Plan(Run):

    def run_all(self):
        self.setup()
        order = {"T1": self.t1, "T4b": self.t4b, "T4c": self.t4c, "T4d": self.t4d, "T8": self.t8, "T9": self.t9,
                 "T10": self.t10, "T11b": self.t11b, "T12": self.t12, "T13": self.t13, "T14": self.t14,
                 "T15": self.t15, "T16": self.t16, "T17": self.t17, "T18a": self.t18a, "T18b": self.t18b,
                 "T18d": self.t18d, "T18e": self.t18e, "T19a": self.t19a, "T19b": self.t19b, "T19c": self.t19c,
                 "T19d": self.t19d, "T19e": self.t19e, "T11m": self.t11m, "X5": self.x5}
        try:
            for name in SCENARIOS:
                self.run_scenario(name, order[name])
        finally:
            self.finish()
        return self.receipt

    # ── T1: idle ──
    def t1(self):
        interval = self.auth.hosts.contact_interval_s
        before = (self.host_state().get("hosts") or [{}])[0].get("calls") or {}
        t0 = time.time()
        time.sleep(interval * T1_INTERVALS)
        after = (self.host_state().get("hosts") or [{}])[0].get("calls") or {}
        snaps = self.snapshots_between(t0)
        reach = [((s["doc"] or {}).get("hosts") or [{}]) for s in snaps]
        mine = [next((h for h in hs if h.get("host_id") == HOST_ID), {}) for hs in reach]
        self.check("T1.reachable", bool(mine) and all(h.get("reachability") == "reachable" for h in mine),
                   {"snapshots": len(mine), "seen": sorted({h.get("reachability") for h in mine})}, n=len(mine))
        calls = sum(after.values()) - sum(before.values())
        intervals = (time.time() - t0) / interval
        self.check("T1.calls_per_interval", bool(after) and calls <= 2 * intervals + 2,
                   {"calls": calls, "intervals": round(intervals, 1), "by_kind_after": after}, n=calls)
        self.mc0()

    # ── T4b / T4c: the runtime SIGKILLed at a committed effect ──
    def _crash_at_e1(self, job):
        from core.mc_delegation_dispatcher.store import effect_operation_id
        gid = self.owner.effect_grant(job, "e1")
        self.owner.item(job, self.program("checkpoint s1", f"effect e1 grant={gid}", "checkpoint s2 hold_s=2",
                                          "result ok"))
        rid = str(self.row(job)["request_id"])
        op = effect_operation_id(rid, "e1")
        if not self.wait(lambda: any(x["operation_id"] == op and x["answer"] == "committed" for x in self.sink_lines()),
                         90, every=0.05):
            raise Stop("e1_never_committed")
        self.runtime_kill("T4: the moment e1 is committed")
        t_kill = time.time()
        lease_end = self.row(job)["lease_until"]
        self.log("observe", "killed", lease_until=lease_end, item=rid)
        self.watch(max(0, lease_end.timestamp() - time.time()) + self.auth.sweeper.sweep_s + 5, every=3,
                   label="after-kill")
        return rid, op, t_kill

    def t4b(self):
        job = "T4b.w"
        rid, op, t_kill = self._crash_at_e1(job)
        snaps = self.snapshots_between(t_kill)
        seq = C.shown_sequence(snaps, rid)
        self.check("T4b.held_then_lapsed", "held" in seq and "lapsed" in seq and seq.index("held") < seq.index("lapsed"),
                   {"shown": seq}, n=len(snaps))
        self.check("T4b.never_running_unreachable", not C.never_running_while_unreachable(snaps, rid),
                   {"snapshots": C.never_running_while_unreachable(snaps, rid)}, n=len(snaps))
        self.runtime_start("T4b: restart after the crash")
        row = self.wait_state(job, ("completed", "failed", "unknown", "interrupted"), 180)
        self.check("T4b.resumed_after_e1", bool(row) and row["state"] == "completed" and row["generation"] == 2,
                   {"state": row and row["state"], "generation": row and row["generation"]})
        receipt = self.sql("SELECT state FROM guild.mc_action_receipts WHERE operation_id=%s", (op,))
        self.check("T4b.receipt_verified", bool(receipt) and receipt[0]["state"] == "verified",
                   {"receipt": receipt and receipt[0]["state"]})
        log = (self.artifacts / f"{rid}-g2.log").read_text() if (self.artifacts / f"{rid}-g2.log").exists() else ""
        self.check("T4b.not_sent_again", "effect e1: already verified, not sent again" in log, {})
        mine = [x for x in self.sink_lines() if x["item_id"] == rid]
        self.named("T4b.DUP-1", C.dup1, mine, need=1)
        self.named("T4b.DUP-6", C.dup6, self.events(item=rid), need=0)
        self.mc0()

    def t4c(self):
        job = "T4c.w"
        self.runtime_stop("T4c: the sink without lookup")
        self.set_host("t4c-no-lookup", effect_sink={"path": str(self.sink_dir), "lookup": False})
        self.runtime_start("T4c: the sink answers no lookup")
        try:
            rid, op, t_kill = self._crash_at_e1(job)
            notify_mark = len(self.notifier.lines())
            self.runtime_start("T4c: restart after the crash")
            row = self.wait_state(job, ("unknown",), 120)
            self.check("T4c.unknown_generation_unchanged", bool(row) and row["generation"] == 1,
                       {"state": row and row["state"], "generation": row and row["generation"]})
            queued = [e for e in self.events(item=rid) if e["kind"] == "decision.queued"]
            self.check("T4c.one_decision_naming_e1", len(queued) == 1 and op in json.dumps(queued[0]["body"]),
                       {"decisions": len(queued)}, n=len(queued))
            work = list((self.base / "work").glob(f"{rid}-g1*"))
            self.check("T4c.workspace_kept", bool(work), {"found": [p.name for p in work]})
            msg = self.wait(lambda: next((m for m in self.notifier.lines()[notify_mark:] if "on its own" in m["text"]
                                          or rid[:8] in m["text"]), None), 90)
            if not msg:
                raise Stop("no_decision_notice")
            self.owner.reply(msg["message_id"], "yes", after_notice=msg["message_id"])
            row = self.wait_state(job, ("interrupted",), 90)
            code = row and (row["result_detail"] or {}).get("code")
            self.check("T4c.ended_owner_decision", bool(row) and code == "owner_decision",
                       {"state": row and row["state"], "code": code})
            mine = [x for x in self.sink_lines() if x["item_id"] == rid]
            self.named("T4c.DUP-1", C.dup1, mine, need=1)
            self.named("T4c.DUP-6", C.dup6, self.events(item=rid), need=1)
        finally:
            self.runtime_stop("T4c: back to the sink with lookup")
            self.set_host("installed-again", effect_sink={"path": str(self.sink_dir), "lookup": True})
            self.runtime_start("after T4c")

    # ── T4d: a definite failure retried within the allowance; a second card past it ──
    def t4d(self):
        first = self.card_doc("C-T4D-1", attempts=2, briefs={
            "build": self.program("checkpoint s1", "crash: once", "checkpoint s2", "result ok"),
            "review": self.program("result ok"), "fix": self.program("result ok")})
        second = self.card_doc("C-T4D-2", attempts=1, briefs={
            "build": self.program("checkpoint s1", "crash: self"), "review": self.program("result ok"),
            "fix": self.program("result ok")})
        self.owner.card(first)
        self.owner.card(second)
        b1 = self.wait_state("C-T4D-1.build", ("completed", "failed"), 180)
        r1 = self.wait_state("C-T4D-1.review", ("completed", "failed"), 120)
        b2 = self.wait_state("C-T4D-2.build", ("completed", "failed"), 120)
        rid1 = b1 and str(b1["request_id"])
        evs = self.window_events()
        retried = [e for e in evs if e["kind"] == "item.retried" and str(e["item_id"]) == rid1]
        self.check("T4d.retried_from_checkpoint", bool(b1) and b1["state"] == "completed" and b1["generation"] == 2
                   and len(retried) == 1, {"state": b1 and b1["state"], "generation": b1 and b1["generation"],
                                           "retried": len(retried)})
        self.check("T4d.card_goes_on", bool(r1) and r1["state"] == "completed", {"review": r1 and r1["state"]})
        ended = [e for e in evs if e["kind"] == "card.ended" and e["body"].get("card_id") == "C-T4D-2"]
        self.check("T4d.second_card_failed_no_retry", bool(b2) and b2["state"] == "failed" and b2["generation"] == 1
                   and not [e for e in evs if e["kind"] == "item.retried" and str(e["item_id"]) == str(b2["request_id"])]
                   and len(ended) == 1, {"state": b2 and b2["state"], "card_ended": len(ended)})
        self.named("T4d.DUP-4", C.dup4, evs, need=2)
        calls = self.owner_calls_in_window()
        self.check("T4d.OWN-5", not calls, {"owner_calls": calls}, n=len(calls))
        self.mc0()

    # ── T8: the authority stopped while effects are due ──
    def _pause(self, why):
        out = subprocess.run(["docker", "pause", self.container], capture_output=True, text=True, timeout=30)
        self.log("injection", "authority stopped (docker pause of its database)", why=why, ok=out.returncode == 0)
        if out.returncode != 0:
            raise Stop("pause_failed")

    def _unpause(self, why):
        out = subprocess.run(["docker", "unpause", self.container], capture_output=True, text=True, timeout=30)
        self.log("injection", "authority back (docker unpause)", why=why, ok=out.returncode == 0)

    def t8(self):
        if not self.container:
            self.check("T8", False, {"why": "no --authority-container: the authority cannot be stopped here"},
                       result="not_run")
            return
        job = "T8.w"
        gid = self.owner.effect_grant(job, "e1")
        self.owner.item(job, self.program("checkpoint s1 hold_s=8", f"effect e1 grant={gid}", "checkpoint s2",
                                          "result ok"))
        if not self.wait(lambda: (self.row(job)["checkpoint"] or {}).get("name") == "s1", 90, every=0.2):
            raise Stop("s1_never_recorded")
        rid = str(self.row(job)["request_id"])
        committed_before = len([x for x in self.sink_lines() if x["item_id"] == rid and x["answer"] == "committed"])
        self._pause("T8: while e1 is due")
        t_down, during = time.time(), []
        try:
            end = t_down + self.auth.lease_s + self.auth.sweeper.gap_threshold_s
            while time.time() < end:
                hs = self.host_state()
                during.append({"at": time.time(), "reachable": ((hs.get("hosts") or [{}])[0]).get("authority_reachable"),
                               "children": [i.get("child") for i in hs.get("items", [])]})
                time.sleep(3)
        finally:
            self._unpause("T8: the authority back")
        committed_during = [x for x in self.sink_lines() if x["item_id"] == rid and x["answer"] == "committed"
                            and t_down <= datetime.fromisoformat(x["at"]).timestamp() <= time.time()]
        self.check("T8.no_commit_during_outage", committed_before == 0 and not committed_during,
                   {"before": committed_before, "during": len(committed_during)})
        self.check("T8.host_view_authority_unreachable", any(d["reachable"] is False for d in during),
                   {"samples": len(during)}, n=len(during))
        self.check("T8.children_frozen", any("frozen" in d["children"] for d in during),
                   {"seen": sorted({c for d in during for c in d["children"] if c})}, n=len(during))
        row = self.wait_state(job, ("completed", "failed", "unknown", "interrupted"), 240)
        evs = self.events(item=rid)
        lapsed = [e for e in evs if e["kind"] == "item.lapsed"]
        self.check("T8.lapsed_authority_down", any(e["body"].get("cause") == "authority_down" for e in lapsed),
                   {"causes": [e["body"].get("cause") for e in lapsed]}, n=len(lapsed))
        suspended = [e for e in self.window_events() if e["kind"] == "authority.suspended"]
        self.check("T8.outage_recorded", bool(suspended), {"outages": [e["body"] for e in suspended]}, n=len(suspended))
        self.check("T8.grace_extended", any(e["kind"] == "item.grace_extended" for e in evs), {})
        self.check("T8.resumed_completed", bool(row) and row["state"] == "completed" and row["generation"] >= 2,
                   {"state": row and row["state"], "generation": row and row["generation"]})
        mine = [x for x in self.sink_lines() if x["item_id"] == rid]
        self.named("T8.DUP-1", C.dup1, mine, need=1)
        self.named("T8.DUP-3", C.dup3, evs, need=1)
        witness = [m for m in self.notify_since(self.window["notify0"]) if "authority" in m["text"].lower()
                   and ("unreachable" in m["text"].lower() or "down" in m["text"].lower())]
        self.check("T8.one_notice", len(witness) == 1, {"notices": len(witness),
                   "why_if_none": "the host-side witness notifier (R5.6-a) is not built in S1"}, n=len(witness))
        self.mc0()

    # ── T9: the sweeper alone killed; a witness stopped ──
    def t9(self):
        job = "T9.w"
        self.owner.item(job, self.program("checkpoint s1", "result ok"), repository="scripted", ref=SCRIPTED_REF)
        got = self.scripted.claim()
        if not got or got["envelope"]["work_id"] != job:
            raise Stop("scripted_claim_failed")
        env = got["envelope"]
        self.scripted.progress(env, "s1")
        self.runtime_signal(signal.SIGUSR1, "T9: the sweeper alone stops (E11)")
        t_kill = time.time()
        self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '1 second' "
                 "WHERE job_id=%s", (job,))
        self.log("injection", "time moved: T9.w's lease ended (the scripted host fell silent)", item=env["request_id"])
        threshold = self.auth.sweeper.gap_threshold_s
        self.watch(threshold + 2 * self.auth.sweeper.sweep_s + 20, every=5, label="sweeper-dead")
        snaps = self.snapshots_between(t_kill)
        late = [s for s in snaps if s["at"] > t_kill + 5]
        shown = [(self.item_in(s["doc"], env["request_id"]) or {}) for s in late]
        self.check("T9.lapsed_shown_with_sweeper_dead",
                   bool(shown) and all(i.get("shown_as") == "lapsed" and i.get("stored_state") in ("claimed", "running")
                                       for i in shown), {"shown": sorted({(i.get("stored_state"), i.get("shown_as"))
                                                                          for i in shown})}, n=len(shown))
        stale = self.notices_raised(prefix="runtime-sweeper-stale")
        delivered = self.delivered("sweeper has not run since")
        self.check("T9.sweeper_stale_once", len(stale) == 1 and len(delivered) == 1,
                   {"raised": len(stale), "delivered": len(delivered)}, n=len(stale))
        alive = [s["doc"]["authority"]["sweeper_age_s"] for s in late if s.get("doc")]
        self.check("T9.liveness_went_on", not [e for e in self.window_events() if e["kind"] == "authority.suspended"],
                   {"sweeper_ages": alive[-3:]})
        hosts = [next((h for h in s["doc"]["hosts"] if h["host_id"] == SCRIPTED), None) for s in late if s.get("doc")]
        witness = [h for h in hosts if h]
        self.check("T9.witness_stale_shown", bool(witness) and witness[-1]["reachability"] in ("unreachable", "stopped")
                   and (witness[-1]["contact_age_s"] or 0) > self.auth.hosts.contact_timeout_s,
                   {"last": witness and {k: witness[-1][k] for k in ("reachability", "contact_age_s", "roles")}},
                   n=len(witness))
        self.named("T9.DUP-7", C.dup7, self.window_events(), self.notify_since(self.window["notify0"]), need=1)
        self.runtime_stop("T9: the operator restarts the sweeper")
        self.runtime_start("T9: the sweeper back")
        fresh = self.wait(lambda: (self.store.sweeper_status() or {}).get("fresh"), 60)
        self.check("T9.sweeper_back", bool(fresh), {})
        self.end_leftover(job, "T9's item, owned by the silent scripted host")

    # ── T10: an expired grant before claim; a revocation mid-run ──
    def t10(self):
        exp = "T10.W-EXP"
        gid = self.owner.item(exp, self.program("result ok"), repository="scripted", ref=SCRIPTED_REF)
        self.sql("UPDATE guild.mc_action_grants SET expires_at = clock_timestamp() - interval '1 second' "
                 "WHERE grant_id=%s", (gid,))
        self.log("injection", "time moved: W-EXP's grant ended before any claim", grant_id=gid)
        got = self.scripted.claim()
        row = self.row(exp)
        self.check("T10.W-EXP_expired_never_claimed", got is None and row["state"] == "expired"
                   and row["generation"] == 0, {"state": row["state"], "generation": row["generation"]})
        rev = "T10.W-REV"
        egid = self.owner.effect_grant(rev, "e1")
        igid = self.owner.item(rev, self.program("checkpoint s1 hold_s=45", f"effect e1 grant={egid}", "result ok"))
        if not self.wait(lambda: (self.row(rev)["checkpoint"] or {}).get("name") == "s1", 90, every=0.3):
            raise Stop("s1_never_recorded")
        rid = str(self.row(rev)["request_id"])
        self.owner.revoke(igid, after_notice=None)
        row = self.wait_state(rev, ("cancelled",), 60)
        evs = self.events(item=rid)
        self.check("T10.W-REV_renew_false_cancelled", bool(row) and (row["result_detail"] or {}).get("code")
                   == "grant_revoked" and any(e["kind"] == "lease.renew_refused" for e in evs),
                   {"state": row and row["state"], "renew_refused": sum(e["kind"] == "lease.renew_refused"
                                                                        for e in evs)})
        self.check("T10.W-REV_effect_never_sent", not [x for x in self.sink_lines() if x["item_id"] == rid], {})
        # The protocol half: a late result and a pending dispatch on a revoked grant (a scripted host's item).
        rev2 = "T10.W-REV2"
        egid2 = self.owner.effect_grant(rev2, "e1")
        igid2 = self.owner.item(rev2, self.program("result ok"), repository="scripted", ref=SCRIPTED_REF)
        got = self.scripted.claim()
        env = got["envelope"]
        self.scripted.effect(env, "prepare", "e1", egid2)
        lease = self.scripted.effect(env, "claim", "e1", egid2)["lease"]
        self.owner.revoke(igid2, after_notice=None)
        lease = {k: lease[k] for k in ("receipt_generation", "token", "mode")}
        dispatch = self.scripted.effect(env, "dispatch", "e1", egid2, lease=lease)
        late = self.scripted.result(env)
        self.check("T10.pending_dispatch_refused", dispatch.get("state") == "refused", {"answer": dispatch})
        self.check("T10.late_result_refused", late.get("state") == "refused", {"answer": late})
        self.named("T10.DUP-4", C.dup4, self.window_events(), need=0)

    # ── T11b: the route gate ──
    def t11b(self):
        sup = "T11b.supervised"
        self.owner.item(sup, self.program("result ok"), route="fakework.subscription")
        row = self.wait_state(sup, ("refused",), 60)
        self.check("T11b.route_supervised_only", bool(row) and (row["result_detail"] or {}).get("code")
                   == "route_supervised_only", {"detail": row and row["result_detail"]})
        mis = "T11b.mismatch"
        self.owner.item(mis, self.program("result ok"), repository="scripted", ref=SCRIPTED_REF)
        env = self.scripted.claim()["envelope"]
        got = self.scripted.result(env, route="fakework.api", unit="usd")
        self.check("T11b.route_mismatch_refused", got.get("state") == "refused", {"answer": got})
        self.end_leftover(mis, "the mismatched result was refused")
        api = "T11b.api"
        self.owner.item(api, self.program("result ok"), route="anthropic.api", actor="claude-code", amount=1.0,
                        repository="scripted", ref=SCRIPTED_REF)
        coder = I.ScriptedHost(self, "mac-test-8")
        coder.caps = coder.caps + ["actor:claude-code"]
        claimed = coder.claim()
        row = self.row(api)
        self.check("T11b.closure_pending", claimed is None and row["state"] == "refused"
                   and (row["result_detail"] or {}).get("reason") == "closure_pending",
                   {"state": row["state"], "detail": row["result_detail"]})
        ok = "T11b.fakework"
        self.owner.item(ok, self.program("result ok"))
        row = self.wait_state(ok, ("completed",), 90)
        self.check("T11b.fakework_runs", bool(row), {"state": row and row["state"]})

    # ── T12: a stall the snapshot answers alone ──
    def t12(self):
        job, idle = "T12.W-STALL", "T12.W-IDLE"
        self.owner.item(job, self.program("checkpoint s1", "hang_s 300", "result ok"))
        if not self.wait(lambda: (self.row(job)["checkpoint"] or {}).get("name") == "s1", 90, every=0.5):
            raise Stop("s1_never_recorded")
        self.owner.item(idle, self.program("result ok"))
        rid = str(self.row(job)["request_id"])
        timeout = self.auth.progress_timeout_s["build"]
        stalled = self.wait(lambda: (self.item_in(self.collector.take("t12")["doc"], rid) or {})
                            .get("flags", {}).get("stalled"), timeout + 60, every=10)
        doc = self.collector.take("t12-stalled")["doc"]
        it, other = self.item_in(doc, rid), self.item_in(doc, self.row(idle)["request_id"])
        self.check("T12.stalled_true_idle_false", bool(stalled) and other is not None
                   and other["flags"]["stalled"] is False, {"stall": it and it["flags"], "idle": other and other["flags"]})
        answers = it is not None and all(k in it for k in ("shown_as", "flags", "unknowns", "next_authorized_step",
                                                           "progress_age_s", "lease_left_s", "who_may_act"))
        self.check("T12.snapshot_answers", answers and it["progress_age_s"] is not None,
                   {"fields": sorted(it or {})[:40]})
        state = self.artifacts / "authority" / "state.json"
        rendered = _render_stub(state)
        self.check("T12.stub_renderer_reads_state_json", rid[:8] in rendered and "stalled" in rendered,
                   {"lines": rendered.count("\n")})
        time.sleep(self.auth.sweeper.sweep_s * 2 + 5)
        props = [e for e in self.events(item=rid) if e["kind"] == "proposal.review_request"]
        self.check("T12.one_review_request_after_a_second_tick", len(props) == 1, {"proposals": len(props)},
                   n=len(props))
        raised = self.notices_raised(prefix=f"runtime-stalled:{rid}")
        delivered = self.delivered("has made no progress")
        self.check("T12.OWN-4", len(raised) == 1 and len(delivered) == 1 and "Next" in raised[0]["body"]["text"],
                   {"raised": len(raised), "delivered": len(delivered)}, n=len(raised))
        self.mc0()
        self.owner.stop(job, after_notice=raised[0]["body"]["key"] if raised else None)
        self.wait_state(job, ("cancelled", "interrupted"), 90)
        self.wait_state(idle, ("completed",), 90)

    # ── T13: a cross-vendor review is proposed alone ──
    def t13(self):
        reviewer = {"actor": "claude-code", "route": "anthropic.api", "amount": 1.0, "cost_cap_usd": 1}
        doc = self.card_doc("C-T13", reviewer=reviewer, briefs={"build": self.program("checkpoint s1", "result ok"),
                                                                "review": "review the patch", "fix": self.program("result ok")})
        self.owner.card(doc)
        build = self.wait_state("C-T13.build", ("completed",), 120)
        review = self.wait(lambda: self.row("C-T13.review"), 60)
        evs = self.events(item=review["request_id"]) if review else []
        queued = [e for e in evs if e["kind"] == "decision.queued"]
        self.check("T13.proposed_one_solo_decision", bool(build) and review is not None and review["state"] == "proposed"
                   and len(queued) == 1 and queued[0]["body"].get("class") == "cross_vendor_admission",
                   {"state": review and review["state"], "decisions": [e["body"].get("class") for e in queued]})
        time.sleep(20)
        self.check("T13.no_claim_without_grant", not [e for e in self.events(item=review["request_id"])
                                                      if e["kind"] == "item.claimed"], {})
        solo = self.wait(lambda: next((m for m in self.notify_since(self.window["notify0"])
                                       if "C-T13" in m["text"] and not m["text"].startswith("Decisions ")), None), 60)
        self.check("T13.arrives_alone", bool(solo), {})
        self.named("T13.DUP-8", C.dup8, self.window_events(), {HOST_ID, SCRIPTED, "mac-test-8"}, need=1)
        self.end_leftover("C-T13.review", "the cross-vendor review is never admitted in acceptance")

    # ── T14: the reference card ──
    def t14(self):
        card = self.card_doc("C-T14", briefs={"build": self.program("checkpoint s1", "checkpoint s2", "result ok"),
                                              "review": self.program("findings needs_fix", "result ok"),
                                              "fix": self.program("checkpoint f1", "result ok")})
        self.owner.card(card)
        fix = self.wait(lambda: self.row("C-T14.fix") and self.row("C-T14.fix")["state"] == "completed"
                        and self.row("C-T14.fix"), 300, every=2)
        self.check("T14.three_steps_ran", bool(fix), {"fix": fix and fix["state"]})
        rows = self.sql("""SELECT step, created_by, input_artifacts, result_detail, state FROM guild.mc_delegations
            WHERE card_id='C-T14' ORDER BY created_at""")
        later = [r for r in rows if r["step"] != "build"]
        self.check("T14.next_steps_by_the_authority", len(later) == 2 and all(r["created_by"] == "authority"
                                                                               and r["input_artifacts"] for r in later),
                   {"steps": [(r["step"], r["created_by"], len(r["input_artifacts"] or [])) for r in rows]})
        ready = self.wait(lambda: self.notices_raised(prefix="runtime-ready:C-T14"), 60)
        delivered = self.wait(lambda: self.delivered("C-T14"), 60)
        self.check("T14.ready_one_notice", bool(ready) and len(ready) == 1 and bool(delivered),
                   {"raised": len(ready or []), "delivered": len(delivered or [])})
        if ready:
            self.owner.merge("C-T14", after_notice=ready[0]["body"]["key"])
        doc = self.collector.take("t14")["doc"]
        work = next((o for o in doc.get("owner_work", []) if o["card_id"] == "C-T14"), None)
        self.check("T14.OWN-1", bool(work) and work["A"] <= 1, {"owner_work": work})
        self.check("T14.OWN-2", bool(work) and work["R"] == 0, {"owner_work": work})
        self.check("T14.OWN-3", bool(work) and work["C"] == 0, {"owner_work": work})

    # ── T15: three routine decisions in one notice; an unknown alone ──
    def t15(self):
        briefs = {"build": self.program("result ok"), "review": self.program("result ok"),
                  "fix": self.program("result ok")}
        for n in (1, 2, 3):
            self.owner.card(self.card_doc(f"C-T15-{n}", repository="scripted", ref=SCRIPTED_REF, briefs=briefs))
        self.sql("""UPDATE guild.mc_action_grants SET consumed_operations = max_operations
            WHERE grant_id IN (SELECT grant_id FROM guild.mc_delegations WHERE card_id LIKE 'C-T15-%%')""")
        self.log("injection", "T15: the three cards' approvals used up, so their next steps need Robert")
        self.owner.card(self.card_doc("C-T15-4", repository="scripted", ref=SCRIPTED_REF, briefs=briefs))
        egid = self.owner.effect_grant("C-T15-4", "e1")
        if not self.wait(lambda: (self.store.sweeper_status() or {}).get("age_s", 99) < 2, 30, every=0.2):
            raise Stop("no_fresh_sweep")
        for n in (1, 2, 3):
            env = self.scripted.claim()["envelope"]
            art = {"kind": "patch", "ref": f"t15-{n}.patch", "sha256": hashlib.sha256(f"t15-{n}".encode()).hexdigest(),
                   "bytes": 10, "author": "fakework", "review_status": "unreviewed"}
            self.scripted.result(env, artifacts=[art])
        env4 = self.scripted.claim()["envelope"]
        self.scripted.effect(env4, "prepare", "e1", egid)
        lease = self.scripted.effect(env4, "claim", "e1", egid)["lease"]
        self.scripted.effect(env4, "dispatch", "e1", egid, lease={k: lease[k] for k in ("receipt_generation", "token",
                                                                                       "mode")})
        self.scripted.result(env4)
        self.log("observe", "three routine decisions and one unknown queued inside one sweep")
        batch = self.wait(lambda: next((m for m in self.notify_since(self.window["notify0"])
                                        if m["text"].startswith("Decisions ")), None), 90)
        solo = self.wait(lambda: next((m for m in self.notify_since(self.window["notify0"])
                                       if "on its own" in m["text"]), None), 60)
        self.check("T15.one_batched_notice", bool(batch) and all(f"C-T15-{n}" in batch["text"] for n in (1, 2, 3))
                   and "C-T15-4" not in batch["text"], {"batch": bool(batch)})
        self.check("T15.unknown_alone", bool(solo) and "C-T15-4" in solo["text"], {"solo": bool(solo)})
        if not batch:
            raise Stop("no_batched_notice")
        self.owner.reply(batch["message_id"], "yes", after_notice=batch["message_id"])
        reviews = self.wait(lambda: all((self.row(f"C-T15-{n}.review") or {}).get("state") == "queued"
                                        for n in (1, 2, 3)), 60)
        decided = self.sql("""SELECT receipt, text_sha256 FROM guild.supervision_decisions
            WHERE requester->>'agent'='runtime' AND state='approved'""")
        bound = [d for d in decided if (d["receipt"] or {}).get("decision_sha256") == d["text_sha256"]]
        self.check("T15.OWN-6", bool(reviews) and len(decided) == 3 and len(bound) == 3
                   and len({d["text_sha256"] for d in decided}) == 3,
                   {"approved": len(decided), "bound_to_own_digest": len(bound)}, n=len(decided))
        self.check("T15.unknown_still_waiting", (self.row("C-T15-4.build") or {}).get("state") == "unknown", {})
        self.named("T15.DUP-7", C.dup7, self.window_events(), self.notify_since(self.window["notify0"]), need=2)
        for n in (1, 2, 3):
            self.end_leftover(f"C-T15-{n}.review", "nobody runs the scripted cards' reviews")

    # ── T16: MC's judgment, capped and coalesced (fakejudge) ──
    def t16(self):
        from fakejudge import FakeJudge
        briefs = {"build": self.program("result ok"), "review": self.program("result ok"),
                  "fix": self.program("result ok")}
        self.owner.card(self.card_doc("C-T16-1", repository="scripted", ref=SCRIPTED_REF, briefs=briefs))
        self.owner.card(self.card_doc("C-T16-2", repository="scripted", ref=SCRIPTED_REF, briefs=briefs))
        env = self.scripted.claim()["envelope"]
        other = str(self.row("C-T16-2.build")["request_id"])
        judge = FakeJudge(self.store, self.ev / "judge.jsonl")
        owner = {"kind": "owner", "turn_id": "owner-turn-t16"}
        self.log("owner", "asks MC for a review (judgment)", item=env["request_id"])
        first = judge.review([env["request_id"]], owner)
        flags = [judge.review([env["request_id"]], {"kind": "flag", "flag": "stalled"}).get("code") for _ in range(3)]
        coalesced = [judge.review([env["request_id"]], owner).get("state") for _ in range(3)]
        self.check("T16.one_call_per_window", first.get("state") == "reserved" and len(judge.calls()) == 1
                   and flags == ["judgment_trigger_not_allowed"] * 3 and coalesced == ["coalesced"] * 3,
                   {"first": first.get("state"), "flags": flags, "coalesced": coalesced})
        self.sql("UPDATE guild.mc_delegation_events SET at = at - interval '200 seconds' WHERE kind='judgment.reserved'")
        self.log("injection", "time moved: T16's review window (build, 180 s) has passed")
        second = judge.review([env["request_id"]], owner)
        esc = judge.review([env["request_id"]], owner).get("code")
        cap = judge.review([other], owner).get("code")
        self.check("T16.escalation_then_cap", second.get("state") == "reserved"
                   and (second.get("recorded") or {}).get("escalated") == [env["request_id"]]
                   and esc == "mc_escalated" and cap == "mc_judgment_cap" and len(judge.calls()) == 2,
                   {"second": second.get("state"), "escalated": esc, "over_cap": cap, "calls": len(judge.calls())})
        trips = I.tripwire_lines(self.trip_log)
        self.check("T16.MC-0_tripwire", not trips, {}, n=len(trips))
        self.end_leftover("C-T16-1.build", "the scripted host's T16 item")
        self.end_leftover("C-T16-2.build", "the scripted host's T16 item")

    # ── T17: drift, one notice each, work not stopped ──
    def t17(self):
        card = self.card_doc("C-T17", paths_allowed=["fakework/"], estimate_s=20, briefs={
            "build": self.program("checkpoint s1 paths=outside/x.py hold_s=60", "checkpoint s2", "result ok"),
            "review": self.program("result ok"), "fix": self.program("result ok")})
        self.owner.card(card)
        build = self.wait_state("C-T17.build", ("completed", "failed"), 180)
        rid = build and str(build["request_id"])
        time.sleep(self.auth.sweeper.sweep_s + 5)
        drift = self.notices_raised(prefix=f"runtime-drift:{rid}")
        kinds = sorted(e["body"]["key"].rsplit(":", 1)[1] for e in drift)
        self.check("T17.one_drift_notice_each", kinds == ["out_of_scope_paths", "over_estimate"],
                   {"kinds": kinds}, n=len(drift))
        self.check("T17.work_not_stopped", bool(build) and build["state"] == "completed", {"state": build and build["state"]})
        self.check("T17.OWN-4", all("Next" in e["body"]["text"] for e in drift) and bool(drift), {}, n=len(drift))

    # ── T18: the census ──
    def _root(self, job, *steps):
        self.owner.item(job, self.program(*steps))
        if not self.wait(lambda: (self.row(job) or {}).get("state") in ("claimed", "running"), 60, every=0.5):
            raise Stop("never_claimed")
        from core.mc_delegation_runner import runner
        rid = str(self.row(job)["request_id"])
        return rid, runner.census_agent_id(f"{rid}-g{self.row(job)['generation']}")

    def t18a(self):
        steps = [f"spawn t{n} role=explore hold_s=5" for n in range(1, 6)]
        rid, root = self._root("T18a.root", *steps, "spawn long1 role=worker hold_s=600 beat_s=5 long",
                               "checkpoint s1 hold_s=50", "result ok")
        got = self.wait(lambda: C.cen1(self.store.agents(), root, transient=5, long_ids={"long1"})[0], 75, every=5)
        ok, n, detail = C.cen1(self.store.agents(), root, transient=5, long_ids={"long1"})
        long_running = [x for x in detail["long"] if x[1] == "running"]
        self.check("T18a.CEN-1", bool(got) and ok and bool(long_running), detail, n=n)
        self.wait_state("T18a.root", ("completed",), 90)
        traces = self.artifacts / "census" / "otlp" / "traces.json"
        doc = json.loads(traces.read_text()) if traces.exists() else {}
        self.named("T18a.CEN-5", C.cen5, doc, self.store.agents())
        self.mc0()

    def t18b(self):
        rid, root = self._root("T18b.root", "spawn t1 role=explore hold_s=2",
                               "spawn long1 role=worker hold_s=600 beat_s=3 long", "checkpoint s1 hold_s=60", "result ok")
        self.wait(lambda: any(a["agent_id"] == root + "/long1" for a in self.store.agents()), 60, every=3)
        self.runtime_kill("T18b: the runtime killed and restarted (launchctl kickstart -k, here by signal)")
        self.runtime_start("T18b: restart")
        self.wait_state("T18b.root", ("completed", "failed", "interrupted", "unknown"), 180)
        time.sleep(self.auth.hosts.contact_interval_s * 2)
        agents = self.store.agents()
        ids = [a["agent_id"] for a in agents]
        self.check("T18b.CEN-2_each_agent_once", len(ids) == len(set(ids)), {"duplicates": len(ids) - len(set(ids))},
                   n=len(ids))
        on_file = [json.loads(x)["event_id"] for p in (self.artifacts / "census").glob("*.jsonl")
                   for x in p.read_text().splitlines() if '"type":"event"' in x]
        stored = [str(r["event_id"]) for r in self.sql("SELECT event_id FROM guild.mc_delegation_events "
                                                       "WHERE kind LIKE 'census.%%'")]
        self.check("T18b.CEN-2_each_event_once", len(stored) == len(set(stored)) and set(on_file) <= set(stored),
                   {"on_file": len(on_file), "stored": len(stored), "unsent": len(set(on_file) - set(stored))},
                   n=len(stored))
        long_row = next((a for a in agents if a["agent_id"] == root + "/long1"), {})
        self.check("T18b.long_one_capture_gap_or_end_unobserved",
                   (long_row.get("state"), long_row.get("reason")) in (("unknown", "end_unobserved"),
                                                                       ("unknown", "capture_gap"),
                                                                       ("running", None)),
                   {"long": (long_row.get("state"), long_row.get("reason"))})
        self.mc0()

    def t18d(self):
        rid, root = self._root("T18d.root", "spawn t1 role=explore hold_s=1",
                               "spawn long1 role=worker hold_s=600 beat_s=3 long", "checkpoint s1 hold_s=90", "result ok")
        self.wait(lambda: any(a["agent_id"] == root + "/long1" for a in self.store.agents()), 60, every=3)
        self.owner.stop("T18d.root", after_notice=None)
        self.wait_state("T18d.root", ("cancelled",), 90)
        time.sleep(self.auth.hosts.contact_interval_s * 2)
        self.named("T18d.CEN-4", C.cen4, self.store.agents(), root)

    def t18e(self):
        steps = [f"spawn s{n} role=swarm hold_s=0" for n in range(1, 51)]
        rid, root = self._root("T18e.root", *steps, "checkpoint s1 hold_s=5", "result ok")
        row = self.wait_state("T18e.root", ("completed",), 120)
        time.sleep(self.auth.hosts.contact_interval_s * 3)
        kids = [a for a in self.store.agents() if a["parent_id"] == root]
        self.check("T18e.all_recorded", len(kids) == 50, {"children": len(kids)}, n=len(kids))
        kinds = [e["kind"] for e in self.events(item=rid)]
        spend = [k for k in kinds if k.startswith("reservation.")]
        refused = [e for e in self.window_events() if e["kind"].startswith("census.") and e["item_id"] is not None]
        self.check("T18e.CEN-6", bool(row) and not any(k.startswith("census.") for k in kinds) and len(spend) <= 1
                   and not refused, {"item_kinds": sorted(set(kinds)), "spend_events": spend})
        self.mc0()

    # ── T19: the launch contract with fakecli and planted credentials ──
    def _t19_setup(self):
        import fakecli
        self.planted = I.planted_values()
        self.route_key = "test-route-key-" + uuid.uuid4().hex
        self.cli_root = self.acc / "tools" / "fakecli"
        self.cli_exe, self.cli_sha = fakecli.install(self.cli_root, planted=[*self.planted.values(), self.route_key])
        fakecli.sign_in(self.home)
        store = self.acc / "store" / "test-credentials.json"
        store.parent.mkdir(exist_ok=True)
        store.write_text(json.dumps({"fakework-api": self.route_key}))
        store.chmod(0o600)
        self.runtime_stop("T19: the runtime restarts with fakecli pinned and planted credentials")
        self.set_host("t19-fakecli", tools={"fakework": {"path": self.cli_exe, "sha256": "sha256:" + self.cli_sha}},
                      route_keys={"fakework.api": "test:fakework-api"}, test_credentials=str(store))
        self.runtime_start("T19: planted credentials in the runtime's parent environment", extra_env=self.planted)
        self.t19_ready = True

    def _t19_run(self, job, *, route="fakework.plan", ref=None, amount=0.05, unit_ok=("completed",)):
        if not getattr(self, "t19_ready", False):
            self._t19_setup()
        self.owner.item(job, self.program("report_usage 0.01", "result ok"), route=route, ref=ref, amount=amount)
        return self.wait_state(job, unit_ok + ("refused", "failed"), 120)

    def _record(self, job, g=1):
        rid = str(self.row(job)["request_id"])
        p = self.artifacts / f"{rid}-g{g}.launch.json"
        return json.loads(p.read_text()) if p.exists() else None

    def _lch1(self, name):
        import fakecli
        from core.mc_delegation_runner import launch_env as L
        records = [json.loads(p.read_text()) for p in self.artifacts.glob("*.launch.json")
                   if json.loads(p.read_text()).get("tool") == "fakecli"]
        reviewed = L.DENY_OVERRIDES | L.INHERIT_OVERRIDES["unattended"]
        self.named(name, C.lch1, records, fakecli.starts(self.cli_root), denied=L.denied, reviewed=reviewed)

    def t19a(self):
        row = self._t19_run("T19a.plan")
        rec = self._record("T19a.plan")
        self.check("T19a.started", bool(row) and row["state"] == "completed", {"state": row and row["state"]})
        self.check("T19a.planted_names_removed", bool(rec) and set(I.PLANTED) <= set(rec["removed"])
                   and rec["route"] == "fakework.plan" and rec["key"] is None, {"removed": rec and rec["removed"]})
        self._lch1("T19a.LCH-1")
        agents = [a for a in self.store.agents() if a.get("item_id") == str(self.row("T19a.plan")["request_id"])
                  and a["parent_id"] is None]
        self.check("T19a.LCH-5", bool(agents) and all(a.get("start_path") and a.get("route") and a.get("launch")
                                                      and a.get("transcript_ref") for a in agents),
                   {"roots": len(agents)}, n=len(agents))
        self.mc0(env_names_planted=True)

    def t19b(self):
        import fakecli
        if not getattr(self, "t19_ready", False):
            self._t19_setup()
        fakecli.sign_out(self.home)
        self.log("injection", "T19b: fakecli's sign-in file removed")
        starts = len(fakecli.starts(self.cli_root))
        row = self._t19_run("T19b.signed_out")
        detail = (row or {}).get("result_detail") or {}
        self.check("T19b.signin_missing_before_spawn", bool(row) and row["state"] == "refused"
                   and detail.get("code") == "signin_missing" and len(fakecli.starts(self.cli_root)) == starts,
                   {"state": row and row["state"], "code": detail.get("code")})
        note = self.wait(lambda: self.delivered("signin_missing") or self.delivered("login"), 60)
        self.check("T19b.one_notice_names_login", bool(note) and len(note) == 1, {"notices": len(note or [])})
        fakecli.sign_in(self.home)
        self.mc0(env_names_planted=True)

    def t19c(self):
        if not getattr(self, "t19_ready", False):
            self._t19_setup()
        settings = self.home / ".fakecli" / "settings.json"
        settings.write_text(json.dumps({"apiKeyHelper": "/bin/echo acceptance-user"}))
        self.log("injection", "T19c: a key helper in fakecli's user configuration")
        row = self._t19_run("T19c.user")
        detail = (row or {}).get("result_detail") or {}
        self.check("T19c.user_key_config_present", bool(row) and detail.get("code") == "key_config_present"
                   and "acceptance-user" not in json.dumps(detail), {"detail": detail})
        settings.unlink()
        self.log("injection", "T19c: the project configuration names a key helper (ref t19c)")
        row = self._t19_run("T19c.project", ref=self.ref_t19c)
        detail = (row or {}).get("result_detail") or {}
        self.check("T19c.project_key_config_present", bool(row) and detail.get("code") == "key_config_present"
                   and "acceptance" not in json.dumps(detail.get("setting", "")), {"detail": detail})

    def t19d(self):
        import fakecli
        if not getattr(self, "t19_ready", False):
            self._t19_setup()
        exe = Path(self.cli_exe)
        original = exe.read_bytes()
        exe.write_bytes(original + b"\n# changed after pinning\n")
        self.log("injection", "T19d: the installed fakecli changed after pinning")
        starts = len(fakecli.starts(self.cli_root))
        try:
            row = self._t19_run("T19d.changed")
        finally:
            exe.write_bytes(original)
        detail = (row or {}).get("result_detail") or {}
        self.check("T19d.binary_unpinned_before_spawn", bool(row) and detail.get("code") == "binary_unpinned"
                   and len(fakecli.starts(self.cli_root)) == starts, {"detail": detail})

    def t19e(self):
        import fakecli
        if not getattr(self, "t19_ready", False):
            self._t19_setup()
        before = len(fakecli.starts(self.cli_root))
        api = self._t19_run("T19e.api", route="fakework.api", amount=1.0)
        sub = self._t19_run("T19e.sub")
        starts = fakecli.starts(self.cli_root)[before:]
        keyed = [s for s in starts if "FAKEWORK_API_KEY" in s["names"]]
        digest = hashlib.sha256(self.route_key.encode()).hexdigest()
        self.check("T19e.key_to_the_api_child_only", bool(api) and bool(sub) and len(starts) == 2 and len(keyed) == 1
                   and any(h["sha256"] == digest for h in keyed[0]["planted"]),
                   {"starts": len(starts), "keyed": len(keyed)})
        res = self.sql("SELECT unit FROM guild.mc_delegation_reservations WHERE route='fakework.api'")
        self.check("T19e.reservation_in_the_routes_unit", bool(res) and res[0]["unit"] == "usd", {"rows": res})
        self._lch1("T19e.LCH-1")
        self.mc0(env_names_planted=True)
        self.runtime_stop("after T19: the runtime without planted credentials")
        self.set_host("installed-after-t19")
        self.runtime_start("after T19")

    # ── T11m: two identities, one reservation ──
    def t11m(self):
        self.runtime_stop("T11m: two identities claim in the same second")
        rival = I.ScriptedHost(self, "mac-test-8", repos=("personal-ai-agents",))
        for job in ("T11m.a", "T11m.b"):
            self.owner.item(job, self.program("report_usage unknown", "result ok"), amount=1.5)
        rival.beat()
        self.runtime_start("T11m: the runtime claims on its first pass", wait=False)
        got = rival.claim()
        self.log("observe", "the rival identity's claim", claimed=bool(got))
        if got:
            env = got["envelope"]
            rival.result(env, amount=None)
        self.wait(lambda: self._contacted_since(time.time() - 60), 60)
        done = self.wait(lambda: [j for j in ("T11m.a", "T11m.b") if self.row(j)["state"] == "completed"], 90)
        held = [j for j in ("T11m.a", "T11m.b") if self.row(j)["state"] == "queued"]
        self.check("T11m.one_admitted_one_held", bool(done) and len(done) == 1 and len(held) == 1,
                   {"completed": done, "queued": held})
        small = "T11m.small"
        self.owner.item(small, self.program("result ok"), amount=0.1)
        time.sleep(self.auth.hosts.contact_interval_s * 2 + 5)
        self.check("T11m.route_closed_after_unknown", self.row(small)["state"] == "queued", {})
        self.owner.resolve_usage_hold("fakework", after_notice=None)
        row = self.wait_state(small, ("completed",), 90)
        self.check("T11m.runs_after_resolution", bool(row), {"state": self.row(small)["state"]})
        for j in held:
            self.end_leftover(j, "the held item has no budget left today")

    # ── X5: the authority restarts between the replay and the reconcile answer ──
    def x5(self):
        if not self.container:
            self.check("X5", False, {"why": "no --authority-container"}, result="not_run")
            return
        job = "X5.w"
        self.owner.item(job, self.program("checkpoint s1", "result ok"), repository="scripted", ref=SCRIPTED_REF)
        env = self.scripted.claim()["envelope"]
        got, eid, at = self.scripted.progress(env, "s1")
        self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '1 second' WHERE job_id=%s",
                 (job,))
        self.log("injection", "time moved: X5.w's lease ended while its host was away")
        replay1, _, _ = self.scripted.progress(env, "s1", event_id=eid, at=at)
        cp = {"name": "s1", "digest": "sha256:" + hashlib.sha256(b"s1").hexdigest()}
        first = self.scripted.reconcile(env, checkpoint=cp)
        self.log("observe", "X5: the reconcile answer is lost with the authority's restart", answer=first.get("answer"))
        self._pause("X5: the authority restarts before its answer arrives")
        time.sleep(3)
        self._unpause("X5: the authority back")
        self.wait(lambda: self.db_now(), 30)
        replay2, _, _ = self.scripted.progress(env, "s1", event_id=eid, at=at)
        second = self.scripted.reconcile(env, checkpoint=cp)
        row = self.row(job)
        resumed = [e for e in self.events(item=row["request_id"]) if e["kind"] == "item.resumed"]
        self.check("X5.replay_applies_nothing", replay1.get("state") == "already_applied"
                   and replay2.get("state") == "already_applied", {"first": replay1, "second": replay2})
        self.check("X5.generation_rises_once", first.get("answer") == "resume" and second.get("answer") == "resume"
                   and first.get("generation") == second.get("generation") == 2 and row["generation"] == 2
                   and len(resumed) == 1, {"first": first.get("generation"), "second": second.get("generation"),
                                           "stored": row["generation"], "resumed": len(resumed)})
        calls = self.owner_calls_in_window()
        self.check("X5.OWN-5", not calls, {"owner_calls": calls}, n=len(calls))
        self.end_leftover(job, "X5's scripted item")
        self.mc0()


def _render_stub(path: Path) -> str:
    """T12's stub renderer: reads only state.json and answers owned, stalled, admitted, unknown and the next step."""
    from core.mc_delegation_dispatcher import snapshot
    doc = snapshot.read_state_file(path)
    lines = [f"as of {doc.get('as_of')} ({doc.get('view_age_s')} s old)"]
    for it in doc.get("items", []):
        lines.append(f"{it['item_id'][:8]} owned by {it.get('owner_host_id')} shown {it['shown_as']} "
                     f"{'stalled' if it['flags'].get('stalled') else 'moving'} progress {it.get('progress_age_s')} s "
                     f"unknown {it.get('unknowns')} next {it.get('next_authorized_step')}")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="harness-acceptance")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--plan", required=True, choices=(PLAN,))
    r.add_argument("--evidence", required=True, type=Path)
    r.add_argument("--only", help="comma list of scenarios")
    r.add_argument("--dsn-env", default="MC_BINDING_TEST_DSN", help="the throwaway server's DSN, by its name")
    r.add_argument("--authority-container", help="the acceptance database's container (T8, X5)")
    r.add_argument("--acceptance", type=Path, help="the acceptance folder (default: beside the evidence)")
    r.add_argument("--ref", default="HEAD", help="the commit the bundle is built from")
    r.add_argument("--seed", type=int)
    a = p.parse_args(argv)
    dsn = os.environ.get(a.dsn_env)
    if not dsn:
        raise SystemExit(f"refused: {a.dsn_env} is not set")
    only = [x.strip() for x in a.only.split(",")] if a.only else None
    if only and set(only) - set(SCENARIOS):
        raise SystemExit("refused: unknown scenario " + ",".join(sorted(set(only) - set(SCENARIOS))))
    run = Plan(a.evidence, server_dsn=dsn, container=a.authority_container, only=only, acceptance=a.acceptance,
               ref=a.ref, seed=a.seed)
    receipt = run.run_all()
    print(json.dumps({"checks": receipt["checks"], "failed": receipt["failed"], "not_run": receipt["not_run"],
                      "scenarios": receipt["scenarios"]}, sort_keys=True))
    return 0 if not receipt["failed"] else 1
