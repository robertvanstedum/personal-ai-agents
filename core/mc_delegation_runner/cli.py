"""Run the delegation runner on the Mac (journey 3).

    python -m core.mc_delegation_runner.cli --dsn-file ~/minimoi-runtime/runner/dev.dsn \\
        --principal robert --environment dev --mirror personal-ai-agents=~/minimoi-runtime/runner/mirrors/personal-ai-agents.git \\
        --workspace-root ~/minimoi-runtime/runner/work --artifact-root ~/minimoi-runtime/runner/artifacts [--once]

* The DSN comes from a private file (0600) or, with ``--dsn-env NAME``, from this process's environment (read once,
  then removed); never from the command line, and never passed to the child (its environment is an allowlist).
* Production has no database port for the Mac: ``--door https://minimoi.ai/internal/mc/v1/delegation-runner`` speaks
  the same calls over HTTPS (core/mc_delegation_dispatcher/door.py), with the runner token read from the login
  Keychain (service minimoi_delegation_runner, account <environment>); production keeps only its SHA-256.
* Before each claim the mirror is fetched, so the dispatcher's exact commit is present.
* Actors: only those with a real budget adapter (Claude Code today). Scopes: research by default; build with --scopes research,build.
* Each pass is one ``run_once``; a failed pass is logged by type and the loop continues.

Subcommands (host runtime spec v0.5 §3.3: one binary, two configurations). Without one, or with ``run`` and the flags
above, it is exactly today's version 1 runner (production's configuration). With ``--host-config`` it is a version 2
host runtime (runner.HostRuntime):

    mc-delegation-runner run --host-config ~/minimoi-runtime/dev/host.yaml [--once]
    mc-delegation-runner state --host-config PATH [--json]        the host's own view (host-state.json), with its age
    mc-delegation-runner state --host-config PATH --authority [--json]   the authority's snapshot, built live (R7.6-a)
    mc-delegation-runner state --file STATE_JSON [--json]         a published state.json, read as a view with its age
    mc-delegation-runner doctor --host-config PATH [--env-names] [--do-not-resume]
    mc-delegation-runner version [--host-config PATH]
    mc-delegation-runner journal --host-config PATH show | replay [--again]
    mc-delegation-runner authority --host-config PATH             the authority service: its door and its timers

With the ``authority`` role and the store reached by DSN (the Mac-only interim, R2.4-a), ``run`` also runs the
authority's own timers (core/mc_delegation_dispatcher/server.py: liveness, the sweeper and its watch, each a thread of
its own), because that process is the only one serving the store; SIGUSR1 stops the sweeper alone (E11). ``--once``
is one pass and runs no timer. ``authority`` serves the door named in host.yaml's ``door`` (gate D's
``minimoi-authority-<env>.service``) with the same timers.

``doctor`` fails closed (exit 1) on an interpreter that is not the pinned one, a host configuration or authority
values it cannot use, or a bundle it cannot digest; ``--env-names`` prints names only, never a value.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

from . import runner
from .cost import ENFORCERS
from .dispatcher_adapter import StoreDispatcher


def _dsn(path: Path) -> str:
    info = os.stat(path)
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise SystemExit("dsn file must not be readable by group or others")
    return path.read_text().strip()


def _dsn_from_env(name: str) -> str:
    value = os.environ.pop(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is not set")
    return value


def _keychain_token(environment: str) -> str:
    value = subprocess.run(["security", "find-generic-password", "-s", "minimoi_delegation_runner", "-a", environment,
                            "-w"], capture_output=True, text=True, timeout=10).stdout.strip()
    if not 32 <= len(value) <= 256:
        raise SystemExit("no runner token in the Keychain (scripts/mc_host/install_delegation_runner.sh)")
    return value


def _fetch(mirror: Path):
    subprocess.run(["git", "--git-dir", str(mirror), "fetch", "--quiet", "origin", "+refs/heads/*:refs/heads/*"],
                   check=False, timeout=120, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                                  "HOME": os.environ.get("HOME", ""), "GIT_TERMINAL_PROMPT": "0"})


SUBCOMMANDS = ("run", "state", "doctor", "version", "journal", "authority")


def main(argv=None) -> int:
    words = list(sys.argv[1:] if argv is None else argv)
    if words and words[0] in SUBCOMMANDS[1:]:
        return _SUB[words[0]](words[1:])
    if words and words[0] == "run":
        argv = words = words[1:]
    if any(w == "--host-config" or w.startswith("--host-config=") for w in words):
        return _run_v2(words)
    return _run_v1(argv)


def _run_v1(argv=None) -> int:
    """Today's runner, unchanged: version 1 over a DSN or the production door."""
    p = argparse.ArgumentParser(prog="mc-delegation-runner")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--dsn-file", type=Path)
    src.add_argument("--dsn-env", metavar="NAME")
    src.add_argument("--door", metavar="URL")
    p.add_argument("--principal", required=True)
    p.add_argument("--environment", required=True, choices=("dev", "prod"))
    p.add_argument("--mirror", action="append", required=True, help="name=path to a bare mirror")
    p.add_argument("--workspace-root", required=True, type=Path)
    p.add_argument("--artifact-root", required=True, type=Path)
    p.add_argument("--runner-id", default="mac-runner-1")
    p.add_argument("--max-cost-usd", type=float, default=5.0)
    p.add_argument("--interval", type=float, default=20.0)
    p.add_argument("--once", action="store_true")
    # research (read-only) by default; build only when the operator enables it here AND Robert confirmed a build
    # proposal for the job (commands.py: fresh checkout, edits only, no push or GitHub).
    p.add_argument("--scopes", default="research", help="comma list from: research, build")
    args = p.parse_args(argv)
    mirrors = {}
    for item in args.mirror:
        name, _, path = item.partition("=")
        mirrors[name] = Path(path).expanduser()
    store = None
    if not args.door:
        import psycopg2
        from core.mc_delegation_dispatcher.store import DispatcherStore
        dsn = _dsn(args.dsn_file.expanduser()) if args.dsn_file else _dsn_from_env(args.dsn_env)
        store = DispatcherStore(lambda: psycopg2.connect(dsn, connect_timeout=5), principal=args.principal,
                                environment=args.environment)
    scopes = tuple(dict.fromkeys(s.strip() for s in args.scopes.split(",") if s.strip()))
    if not scopes or not set(scopes) <= {"research", "build"}:
        raise SystemExit("--scopes takes research and/or build")
    cfg = runner.RunnerConfig(runner_id=args.runner_id, actors=tuple(ENFORCERS), scopes=scopes,
                              repositories={k: str(v) for k, v in mirrors.items()},
                              workspace_root=args.workspace_root.expanduser(),
                              artifact_root=args.artifact_root.expanduser(), max_cost_usd=args.max_cost_usd,
                              cost_enforcers=dict(ENFORCERS))
    if args.door:
        from core.mc_delegation_dispatcher.door import HttpDispatcher
        dispatcher = HttpDispatcher(args.door, _keychain_token(args.environment), cfg.artifact_root)
    else:
        dispatcher = StoreDispatcher(store, cfg.artifact_root)
    while True:
        for mirror in mirrors.values():
            _fetch(mirror)
        try:
            out = runner.run_once(dispatcher, cfg)
            print(json.dumps({k: out.get(k) for k in ("state", "request_id", "delivered", "code") if k in out}),
                  flush=True)
        except Exception as exc:
            print(json.dumps({"state": "pass_failed", "error": type(exc).__name__}), flush=True)
        if args.once:
            return 0
        time.sleep(args.interval)


# ── version 2: the host runtime ───────────────────────────────────────────────────────────────────────────────────
V2_MAX_COST_USD = 3.0      # the envelope's own USD ceiling for any one task: Robert's per-item cap (Q2, §8.2)


def _host(path):
    from . import host as host_mod
    try:
        return host_mod.load(Path(path).expanduser())
    except host_mod.HostRefused as exc:
        raise SystemExit(f"refused: {exc}")


def _values(host):
    """The environment's values for this host: the authority's own file (R2.3-b). (Authority, problem or None)."""
    from core.mc_delegation_dispatcher.store import Authority, AuthorityRefused
    try:
        auth = Authority.load(host.authority.values)
    except (OSError, AuthorityRefused) as exc:
        return None, f"authority_values_unreadable:{type(exc).__name__}"
    if auth.environment != host.environment:
        return None, "authority_values_environment_mismatch"
    if auth.hosts is None or auth.sweeper is None or not auth.speaks_v2:
        return None, "host_values_missing"                     # a missing value admits nothing
    return auth, None


def _grant_store(host):
    """The authority's GrantStore when this process builds the authority's store in-process (the Mac-only interim):
    the delegation capabilities (one exact approval each), cards' row, and the test sink's effect where configured."""
    from core.minimoi_actions.grants import CapabilityRule, GrantStore
    from core.mc_delegation_dispatcher import cards
    rows = {"delegate.start_build": CapabilityRule(1, True, True), "delegate.start_research": CapabilityRule(1, True, True),
            **cards.grant_rows()}
    if host.effect_sink is not None:
        from .fakework import OPERATION
        rows[OPERATION] = CapabilityRule(1, False, True)
    return GrantStore(rows)


def _dispatcher(host, auth):
    from .dispatcher_adapter import StoreDispatcherV2
    if host.authority.transport == "dsn":
        import psycopg2
        from core.mc_delegation_dispatcher.store import DispatcherStore
        dsn = _dsn(host.authority.dsn_file)
        store = DispatcherStore(lambda: psycopg2.connect(dsn, connect_timeout=5), principal=host.principal,
                                environment=host.environment, authority=auth, grants=_grant_store(host))
        return StoreDispatcherV2(store)
    from core.mc_delegation_dispatcher.door import HttpDispatcherV2
    service, _, account = host.authority.credential_ref.split(":", 1)[1].partition("/")
    value = subprocess.run(["security", "find-generic-password", "-s", service, "-a", account, "-w"],
                           capture_output=True, text=True, timeout=10).stdout.strip()
    if not 32 <= len(value) <= 256:
        raise SystemExit("no host credential at its reference")
    return HttpDispatcherV2(host.authority.url, value, host.artifact_root, timeout=min(20, host.stop_timeout_s // 4 or 1))


def runtime_for(host, auth, dispatcher, *, digest=None):
    """A HostRuntime from a host configuration (the run and journal commands; tests build it the same way)."""
    from . import census, host as host_mod
    from .fakework import FileSink
    if digest is None:
        digest, _ = host_mod.tree_digest(host.bundle_root)
    cfg = runner.RunnerConfig(runner_id=host.runner_id, actors=host.actors(), scopes=host.scopes(),
                              repositories={k: str(v) for k, v in host.mirrors.items()},
                              workspace_root=host.workspace_root, artifact_root=host.artifact_root,
                              max_cost_usd=V2_MAX_COST_USD, lease_renew_s=float(auth.hosts.renew_s), tick_s=0.5)
    sink = None if host.effect_sink is None else FileSink(host.effect_sink.path, lookup=host.effect_sink.lookup)
    from . import witness
    return runner.HostRuntime(dispatcher, cfg, host, auth.hosts, auth.sweeper.gap_threshold_s, bundle_digest=digest,
                              sink=sink, stores=_stores(host), census=census.CensusFile(host.artifact_root / "census"),
                              witness=witness.build(host, auth.hosts.contact_timeout_s, host.artifact_root))


def _stores(host) -> dict:
    """The credential stores an API-route launch reads its key from, by reference (R6.7-f): the login Keychain, and
    in dev the acceptance run's test file. Nothing is read here but the test file's own (test) values."""
    from . import launch
    stores = {"keychain": launch.KeychainCredentialStore()}
    if host.test_credentials is not None:
        try:
            stores["test"] = launch.TestCredentialStore.from_file(host.test_credentials)
        except (OSError, ValueError) as exc:
            raise SystemExit(f"refused: {getattr(exc, 'code', 'route_key_store_unreadable')}")
    return stores


def _start(host):
    """Everything that must hold before a version 2 runtime may act: the pinned interpreter, the environment's
    values, the one-instance lock. (auth, lock)."""
    from . import host as host_mod
    problems = host_mod.interpreter_problems(host)
    if problems:
        raise SystemExit("refused: " + ",".join(problems))
    auth, problem = _values(host)
    if problem:
        raise SystemExit(f"refused: {problem}")
    lock = host_mod.HostLock(host)
    try:
        lock.acquire(runner._now().isoformat())
    except host_mod.HostRefused as exc:
        raise SystemExit(f"refused: {exc}")
    return auth, lock


class CountingDispatcher:
    """The runtime's calls to its authority, counted by call (T1: at most 2 per contact interval idle), shown in
    host-state.json as ``calls``. Everything else passes through unchanged."""

    def __init__(self, inner):
        import threading
        self._inner, self.counts, self._count_lock = inner, {}, threading.Lock()

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def counted(*args, **kwargs):
            with self._count_lock:
                self.counts[name] = self.counts.get(name, 0) + 1
            return attr(*args, **kwargs)
        return counted


def authority_loops(host, dispatcher):
    """The authority's own timers for a host that holds the authority role and serves the store itself by DSN (the
    Mac-only interim, R2.4-a), or None: a host that reaches its authority through a door runs none."""
    store = getattr(dispatcher, "store", None)
    if "authority" not in host.roles or host.authority.transport != "dsn" or store is None:
        return None
    from core.mc_delegation_dispatcher import server
    return server.AuthorityLoops(store, state_dir=server.state_dir_of(host), endpoint_id=host.authority.endpoint_id,
                                 host_id=host.host_id)


def _run_v2(words) -> int:
    import signal
    p = argparse.ArgumentParser(prog="mc-delegation-runner run")
    p.add_argument("--host-config", required=True)
    p.add_argument("--once", action="store_true")
    args = p.parse_args(words)
    host = _host(args.host_config)
    auth, lock = _start(host)
    loops = None
    try:
        dispatcher = CountingDispatcher(_dispatcher(host, auth))
        rt = runtime_for(host, auth, dispatcher)
        signal.signal(signal.SIGTERM, rt.request_stop)        # G4: launchd and systemd stop with SIGTERM
        signal.signal(signal.SIGINT, rt.request_stop)
        if not args.once:
            loops = authority_loops(host, dispatcher)
        if loops is not None:
            signal.signal(signal.SIGUSR1, lambda *_: loops.stop_sweeper())     # E11: the sweeper alone stops
            loops.start()                     # the authority serves (its liveness beats) before the host's contact
        rt.freeze_left_children()             # §5.5 step 2 before any contact: an earlier process's children (X3)
        try:
            rt.contact(announced="online")
        except runner._AuthorityError as exc:
            raise SystemExit(f"refused: {exc.code}")
        rt.start_contact()
        stopped = False
        while True:
            for mirror in host.mirrors.values():
                _fetch(mirror)
            try:
                out = runner.run_once(rt.dispatcher, rt.cfg)
                print(json.dumps({k: out.get(k) for k in ("state", "request_id", "delivered", "code") if k in out}),
                      flush=True)
                stopped = stopped or out.get("state") == "stopped" and rt.stop_requested.is_set()
            except Exception as exc:
                print(json.dumps({"state": "pass_failed", "error": type(exc).__name__}), flush=True)
            if rt.stop_requested.is_set() or args.once:
                break
            rt.stop_requested.wait(auth.hosts.contact_interval_s)    # ≤ 2 authority calls per contact interval idle
        if rt.stop_requested.is_set() and not stopped:
            rt.graceful_stop()                                        # idle: announce stopping, then stopped
        rt.stop_contact()
        return 0
    finally:
        if loops is not None:
            loops.stop()
        lock.release()


def _authority(words) -> int:
    """The authority service (gate D: ``minimoi-authority-<env>.service``): the door named in host.yaml's ``door``,
    and the authority's own timers. One instance per (host, environment), under a lock of its own beside the
    runtime's. SIGTERM or SIGINT stops it; SIGUSR1 stops the sweeper alone (E11)."""
    import signal
    import threading
    p = argparse.ArgumentParser(prog="mc-delegation-runner authority")
    p.add_argument("--host-config", required=True)
    args = p.parse_args(words)
    host = _host(args.host_config)
    if host.door is None:
        raise SystemExit("refused: host_door_missing")
    from . import host as host_mod
    from core.mc_delegation_dispatcher import server
    problems = host_mod.interpreter_problems(host)
    if problems:
        raise SystemExit("refused: " + ",".join(problems))
    auth, problem = _values(host)
    if problem:
        raise SystemExit(f"refused: {problem}")
    try:
        hosts, readers = server.load_credentials(host.door.credentials, host.environment)
    except ValueError as exc:
        raise SystemExit(f"refused: {exc}")
    lock = host_mod.HostLock(host, service="authority")
    try:
        lock.acquire(runner._now().isoformat())
    except host_mod.HostRefused as exc:
        raise SystemExit(f"refused: {exc}")
    loops = srv = serving = None
    try:
        import psycopg2
        from core.mc_delegation_dispatcher.store import DispatcherStore
        dsn = _dsn(host.door.dsn_file)
        store = DispatcherStore(lambda: psycopg2.connect(dsn, connect_timeout=5), principal=host.principal,
                                environment=host.environment, authority=auth, grants=_grant_store(host))
        state_dir = server.state_dir_of(host)
        loops = server.AuthorityLoops(store, state_dir=state_dir, endpoint_id=host.authority.endpoint_id,
                                      host_id=host.host_id).start()
        srv = server.DoorServer((host.door.host, host.door.port), store, hosts=hosts, readers=readers,
                                state_file=state_dir / "state.json",
                                tls=server.tls_context(host.door.tls_cert, host.door.tls_key))
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGUSR1, lambda *_: loops.stop_sweeper())
        serving = threading.Thread(target=srv.serve_forever, name="mc-authority-door", daemon=True)
        serving.start()
        print(json.dumps({"state": "serving", "host_id": host.host_id, "environment": host.environment,
                          "listen": f"{host.door.host}:{host.door.port}"}), flush=True)
        stop.wait()
        return 0
    finally:
        if srv is not None:
            if serving is not None:
                srv.shutdown()                       # waits for serve_forever, so only once it was started
            srv.server_close()
        if loops is not None:
            loops.stop()
        lock.release()


def _state(words) -> int:
    p = argparse.ArgumentParser(prog="mc-delegation-runner state")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--host-config")
    src.add_argument("--file", help="a published <authority_state_dir>/state.json, read as a view with its age")
    p.add_argument("--authority", action="store_true",
                   help="with --host-config: the authority's snapshot (minimoi.runtime.state.v1), built live")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(words)
    if args.file or args.authority:
        if args.file:
            from core.mc_delegation_dispatcher.snapshot import read_state_file
            doc = read_state_file(Path(args.file).expanduser())
        else:
            doc = _authority_state(_host(args.host_config))
        if args.json:
            print(json.dumps(doc, sort_keys=True, default=str))
        else:
            print(f"{doc.get('environment')} runtime state as of {doc.get('as_of')} ({doc.get('view_age_s')} s old): "
                  f"{len(doc.get('items', []))} item(s), {len(doc.get('notices', []))} notice(s), "
                  f"{len(doc.get('decisions_waiting', []))} decision(s) waiting; unknown: "
                  f"{', '.join(doc.get('unknowns') or []) or 'nothing'}")
        return 0
    host = _host(args.host_config)
    doc = _host_state(host)
    if args.json:
        print(json.dumps(doc, sort_keys=True))
    else:
        h = (doc.get("hosts") or [{}])[0]
        blocked = [i for i in doc.get("items", []) if i.get("blocked")]
        print(f"{host.host_id} ({host.environment}), host-local view, {doc.get('view_age_s')} s old: "
              f"announced {h.get('announced')}, authority reachable {h.get('authority_reachable')}, "
              f"{len(doc.get('items', []))} owned item(s)"
              + "".join(f"; {i['task']} blocked ({i['blocked']['code']}) since {i['blocked']['since']}, "
                        "cleared by an operator restart" for i in blocked))
    return 0


def _authority_state(host) -> dict:
    """The authority's snapshot (R7.6-a's ``state --json``), where this host holds the authority role and reaches its
    store by DSN (the Mac-only interim). Read only: no lock, no write; the snapshot itself writes nothing."""
    if "authority" not in host.roles or host.authority.transport != "dsn":
        raise SystemExit("refused: state_authority_not_here")
    auth, problem = _values(host)
    if problem:
        raise SystemExit(f"refused: {problem}")
    import psycopg2
    from core.mc_delegation_dispatcher.store import DispatcherStore
    dsn = _dsn(host.authority.dsn_file)
    store = DispatcherStore(lambda: psycopg2.connect(dsn, connect_timeout=5), principal=host.principal,
                            environment=host.environment, authority=auth, grants=_grant_store(host))
    return store.snapshot(endpoint_id=host.authority.endpoint_id, host_id=host.host_id)


def _host_state(host) -> dict:
    path = host.artifact_root / "host-state.json"
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"schema": "minimoi.runtime.state.v1", "environment": host.environment, "source": "host-local",
                "as_of": None, "view_age_s": None, "hosts": [], "items": [], "unknowns": ["host_state_missing"]}
    from datetime import datetime, timezone
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(doc["as_of"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        age = None
    doc["view_age_s"] = None if age is None else round(age, 1)      # never rendered as zero when unknown
    doc["source"] = "host-local"
    return doc


def _doctor(words) -> int:
    from . import host as host_mod, launch_env
    p = argparse.ArgumentParser(prog="mc-delegation-runner doctor")
    p.add_argument("--host-config", required=True)
    p.add_argument("--env-names", action="store_true")
    p.add_argument("--do-not-resume", action="store_true")
    args = p.parse_args(words)
    host = _host(args.host_config)
    problems = host_mod.interpreter_problems(host)
    req = host_mod.required_python(host.roles, host.authority.transport)
    out = {"host_id": host.host_id, "environment": host.environment, "runner_id": host.runner_id,
           "interpreter": {"pinned": str(host.interpreter.path),
                           "pinned_version": "%d.%d" % host.interpreter.version, "running": sys.executable,
                           "running_version": "%d.%d" % sys.version_info[:2], "requires": "%d.%d" % req,
                           "problems": list(problems)},
           "lock": host_mod.HostLock(host).holder()}
    auth, problem = _values(host)
    out["values"] = ({"file": str(host.authority.values), "problem": problem} if problem else
                     {"file": str(host.authority.values), "gap_threshold_s": auth.sweeper.gap_threshold_s,
                      **auth.hosts.__dict__, "gap_detection": "on" if host.gap_detection else "off"})
    if problem:
        problems.append(problem)
    state = _host_state(host)
    h = (state.get("hosts") or [{}])[0]
    out["contact"] = {"last_contact_at": h.get("last_contact_at"), "view_age_s": state.get("view_age_s"),
                      "authority_reachable": h.get("authority_reachable")}
    # A blocked task (an authority error on a known outcome, or on reconcile): the host claims nothing until an
    # operator restarts the runtime, which replays its pending records (N-E3).
    out["blocked"] = [{"task": i.get("task"), "item_id": i.get("item_id"), **i["blocked"]}
                      for i in state.get("items", []) if i.get("blocked")]
    problems.extend(f"task_blocked:{b['task']}" for b in out["blocked"])
    # The census (spec v0.5 §6.6): it never gates work, so a gap in it is shown here plainly instead (CEN-6, R6.6-e).
    out["census"] = state.get("census")
    if out["census"] and out["census"].get("status") not in (None, "ok"):
        problems.append(f"census_{out['census']['status']}:{out['census'].get('reason')}")
    problems.extend(f"census_foreign_schema:{v}" for v in sorted((out["census"] or {}).get("foreign_schema") or {}))
    # The witness (R5.6-a): a notice it could not send is shown, never dropped (S1-H review N1).
    out["witness"] = h.get("witness")
    if (out["witness"] or {}).get("undelivered"):
        problems.append("witness_send_failing:{}".format(((out["witness"] or {}).get("last_failure") or {})
                                                          .get("code")))
    problems.extend(f"census_unrecorded:{a}" for a in sorted((out["census"] or {}).get("unrecorded") or {}))
    try:
        digest, files = host_mod.tree_digest(host.bundle_root)
        out["bundle"] = {"root": str(host.bundle_root), "digest": digest, "files": files}
    except OSError as exc:
        out["bundle"] = {"root": str(host.bundle_root), "problem": type(exc).__name__}
        problems.append("bundle_unreadable")
    # The launch contract (R6.7-d): the policy's digest, and each pinned tool checked against its digest. A tool the
    # host offers with no pin can never start (binary_unpinned); fakework without a pin is the bundle's own.
    from . import launch
    out["launch_policy"] = {"version": launch_env.POLICY_VERSION, "sha256": launch_env.POLICY_DIGEST}
    out["tools"] = {}
    for actor in sorted(set(host.actors()) | set(host.tools)):
        pin = host.tools.get(actor)
        if pin is None:
            out["tools"][actor] = {"pinned": False, "runs": "bundle fakework.py" if actor == "fakework" else None}
            if actor != "fakework":
                problems.append(f"binary_unpinned:{actor}")
            continue
        try:
            launch.verify_binary(pin.path, pin.sha256)
            out["tools"][actor] = {"pinned": True, "path": pin.path, "sha256": pin.sha256, "matches": True}
        except launch.LaunchRefused as exc:
            out["tools"][actor] = {"pinned": True, "path": pin.path, "sha256": pin.sha256, "matches": False,
                                   "reason": exc.detail.get("reason")}
            problems.append(f"binary_unpinned:{actor}")
    out["route_keys"] = {r: ref for r, ref in sorted(host.route_keys.items())}        # references, never values
    # The authority's own two timers, where this host's store is served here (the authority role by DSN, or the
    # authority service's door): each one's age from its own row. A stale one is a problem; the runtime (or the
    # authority service) restarted brings it back (S1-B's liveness apart from the sweeper; E11). A store doctor cannot
    # read is shown by type and left to the contact check above, which already says whether the authority answers.
    dsn_file = host.door.dsn_file if host.door is not None else \
        host.authority.dsn_file if "authority" in host.roles and host.authority.transport == "dsn" else None
    if dsn_file is not None and auth is not None:
        try:
            import psycopg2
            from core.mc_delegation_dispatcher import server
            from core.mc_delegation_dispatcher.store import DispatcherStore
            dsn = _dsn(dsn_file)
            store = DispatcherStore(lambda: psycopg2.connect(dsn, connect_timeout=5), principal=host.principal,
                                    environment=host.environment, authority=auth, grants=_grant_store(host))
            out["authority_timers"] = server.ages(store)
            problems.extend(f"authority_{name}_stale" for name, a in sorted(out["authority_timers"].items())
                            if not a["fresh"])
        except Exception as exc:
            out["authority_timers"] = {"unreadable": type(exc).__name__}
    if args.do_not_resume:
        out["do_not_resume"] = list(host.do_not_resume)
    if args.env_names:                                   # names only (MC-0, LCH-6): never a value
        names = sorted(os.environ)
        child = sorted(launch_env.child_env({"GIT_TERMINAL_PROMPT": "0"}))

        def shaped(ns):                    # a deny pattern outside the reviewed base list (HOME is a base name)
            return [n for n in ns if launch_env.denied(n) and n not in launch_env.BASE_ALLOW]
        out["env_names"] = {"runtime": names, "runtime_credential_shaped": shaped(names), "child": child,
                            "child_credential_shaped": shaped(child)}
        if out["env_names"]["child_credential_shaped"]:
            problems.append("child_environment_credential_shaped")
    out["ok"] = not problems
    out["problems"] = problems
    print(json.dumps(out, sort_keys=True, indent=1))
    return 0 if out["ok"] else 1


def _version(words) -> int:
    from . import host as host_mod
    p = argparse.ArgumentParser(prog="mc-delegation-runner version")
    p.add_argument("--host-config")
    args = p.parse_args(words)
    out = {"program": "mc-delegation-runner", "protocols": [1, 2], "python": "%d.%d.%d" % sys.version_info[:3]}
    if args.host_config:
        host = _host(args.host_config)
        out["bundle_digest"], out["bundle_files"] = host_mod.tree_digest(host.bundle_root)
    print(json.dumps(out, sort_keys=True))
    return 0


def _journal(words) -> int:
    from .journal import Journal, JournalDamaged
    p = argparse.ArgumentParser(prog="mc-delegation-runner journal")
    p.add_argument("--host-config", required=True)
    p.add_argument("action", choices=("show", "replay"))
    p.add_argument("--again", action="store_true", help="resend every record, acknowledged or not (DUP-5: applies 0)")
    args = p.parse_args(words)
    host = _host(args.host_config)
    paths = sorted(host.artifact_root.glob("*.pending.jsonl"), key=lambda p: (p.name == "claims.pending.jsonl", p.name))
    if args.action == "replay":                       # claim requests are resent only by the run, under its lock
        paths = [p for p in paths if p.name != "claims.pending.jsonl"]
    if args.action == "show":
        for path in paths:
            task = path.name[:-len(".pending.jsonl")]
            try:
                calls = Journal(path).calls()
                notes = Journal(path).notes()
            except JournalDamaged as exc:
                print(json.dumps({"task": task, "damaged": str(exc)}))
                continue
            print(json.dumps({"task": task, "notes": [n["kind"] for n in notes],
                              "records": [{"event_id": c["event_id"], "kind": c["kind"], "at": c["at"],
                                           "mark": c["mark"], "code": (c["answer"] or {}).get("code")}
                                          for c in calls]}, sort_keys=True))
        return 0
    auth, lock = _start(host)                       # never beside a running runtime: one writer per journal
    try:
        rt = runtime_for(host, auth, _dispatcher(host, auth))
        totals = {"sent": 0, "applied": 0, "already_applied": 0, "final": 0, "pending": 0, "damaged": 0}
        for path in paths:
            try:
                counts = rt.replay(path.name[:-len(".pending.jsonl")], again=args.again)
            except JournalDamaged:
                totals["damaged"] += 1
                continue
            except runner._Offline:
                print(json.dumps({"state": "offline", **totals}))
                return 1
            for k, v in counts.items():
                totals[k] += v
        print(json.dumps({"state": "replayed", "again": args.again, **totals}, sort_keys=True))
        return 1 if totals["damaged"] else 0
    finally:
        lock.release()


_SUB = {"state": _state, "doctor": _doctor, "version": _version, "journal": _journal, "authority": _authority}


if __name__ == "__main__":
    sys.exit(main())
