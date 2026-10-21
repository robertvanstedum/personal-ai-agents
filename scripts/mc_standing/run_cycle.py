#!/usr/bin/env python3
"""Run one MC standing morning cycle (standing spec v0.3.1; plan v0.3 P0).

Scheduled by the platform at 05:40 America/Chicago (Codex's scheduler registration). Reads an
approved watchlist, sanitized sources under --source-root (read-only), the owner control file and
the emergency disable file; writes only its own spool; delivers through the ingress listener.

A watchlist whose approval_ref still says "candidate" is refused unless --dev-fixture is given
(dev fixture runs only). The producer token is read from --token-file (0600), never argv.
Exit codes: 0 delivered / already final / paused / skipped; 2 configuration error; 3 the cycle
ended failed, held or with delivery still pending/unknown (the next run reconciles).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.jobs import registry as jobs_registry, status as jobs_status  # noqa: E402
from core.mc_standing import registry, runner  # noqa: E402
from core.mc_standing.control import Control  # noqa: E402
from core.mc_standing.ingress_client import IngressClient  # noqa: E402
from core.mc_standing.spool import Spool  # noqa: E402
from core.mc_standing.telegram_copy import TelegramBotSender  # noqa: E402


def _token(path: Path) -> str:
    info = os.stat(path)
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise SystemExit("token file must not be readable by group or others")
    value = path.read_text().strip()
    if not 32 <= len(value) <= 256:
        raise SystemExit("token file content invalid")
    return value


def _telemetry():
    """Spec 161 adopter: only when MINIMOI_TELEMETRY_ENABLED=1 and the shared library is present.
    Off (or unavailable) means no telemetry, never a failed cycle."""
    if os.environ.get("MINIMOI_TELEMETRY_ENABLED") != "1":
        return None
    try:
        from core.observability import configure
        return configure(service_name="minimoi-standing-checks")
    except Exception:
        return None


def _chat_binding(path: Path) -> str:
    info = os.stat(path)
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise SystemExit("chat binding file must not be readable by group or others")
    value = path.read_text().strip()
    if not re.fullmatch(r"-?[0-9]{1,20}", value):
        raise SystemExit("chat binding content invalid")
    return value


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--watchlist", required=True, type=Path)
    p.add_argument("--state-root", required=True, type=Path)
    p.add_argument("--source-root", required=True, type=Path)
    p.add_argument("--environment", required=True, choices=("dev", "prod"))
    p.add_argument("--policy-id", required=True)
    p.add_argument("--destination", required=True)
    p.add_argument("--ingress-url", required=True)
    p.add_argument("--token-file", required=True, type=Path)
    p.add_argument("--control-file", required=True, type=Path)
    p.add_argument("--disable-file", required=True, type=Path)
    p.add_argument("--details-link")
    p.add_argument("--dev-fixture", action="store_true")
    p.add_argument("--telegram-copy", action="store_true",
                   help="also send the counts-only Telegram copy (off unless given; a token alone never enables it)")
    p.add_argument("--telegram-token-file", type=Path)
    p.add_argument("--telegram-chat-file", type=Path, help="the owner-approved destination chat binding")
    p.add_argument("--max-wall-ms", type=int, help="approved resource envelope (P-07); excess makes the job warn")
    p.add_argument("--max-cpu-ms", type=int)
    p.add_argument("--max-rss-kb", type=int)
    p.add_argument("--jobs-root", type=Path, help="write the scheduled-jobs status file here")
    p.add_argument("--job-id", default="mc-standing-brief")
    p.add_argument("--jobs-registry", type=Path, help="default: config/scheduled_jobs.json")
    p.add_argument("--ack-control-only", action="store_true",
                   help="read the owner control document, record the worker acknowledgement, exit")
    args = p.parse_args(argv)
    if args.ack_control_only:
        # Cheap and frequent (e.g. every minute): makes the owner's pause/resume show as applied
        # promptly, since the worker itself only runs at 05:40. Reads and sends nothing else.
        # It acknowledges only for an idle worker: it must hold the cycle lock (non-blocking). An
        # active cycle holds the lock and acknowledges at its own next checkpoint, so a pause is
        # never reported applied while work is still in flight.
        spool = Spool(args.state_root)
        lock_fd = os.open(spool.root / ".cycle.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(json.dumps({"state": "worker_active", "in_flight": True,
                                  "note": "the running cycle acknowledges at its next checkpoint"}))
                return 0
            control = Control(args.control_file, args.disable_file, spool)
            paused = control.paused()
            print(json.dumps({"state": "ack", "in_flight": False,
                              "applied_state": "paused" if paused else "enabled", "killed": control.killed()}))
            return 0
        finally:
            os.close(lock_fd)
    if args.jobs_root:
        return _with_job_status(args)
    return _cycle(args)[0]


def _emit(doc: dict, code: int) -> tuple[int, dict]:
    print(json.dumps(doc))
    return code, doc


def _cycle(args) -> tuple[int, dict]:
    try:
        wl = registry.load(args.watchlist)
    except (OSError, registry.RegistryError) as exc:
        return _emit({"state": "config_error", "reason": str(exc)[:120]}, 2)
    if any(w.approval_ref.startswith("candidate") for w in wl.watches) and not args.dev_fixture:
        return _emit({"state": "config_error", "reason": "watchlist_not_approved"}, 2)
    if args.dev_fixture and args.environment != "dev":
        return _emit({"state": "config_error", "reason": "dev_fixture_only_in_dev"}, 2)
    token = _token(args.token_file)
    spool = Spool(args.state_root)
    control = Control(args.control_file, args.disable_file, spool)
    deps = {}
    dsn_file = os.environ.get("MC_STANDING_GUEST_DSN_FILE")
    if dsn_file:
        from minimoi_portal import guest_requests
        dsn = Path(dsn_file).read_text().strip()
        deps["guest_summary_fn"] = lambda: guest_requests.pending_summary(guest_requests.PostgresStore(dsn))
    telegram = None
    if args.telegram_copy:
        if not (args.telegram_token_file and args.telegram_chat_file):
            return _emit({"state": "config_error", "reason": "telegram_copy_needs_token_and_chat_binding"}, 2)
        tg_token = _token(args.telegram_token_file)
        chat = _chat_binding(args.telegram_chat_file)
        telegram = {"sender": TelegramBotSender(lambda: tg_token), "destination_id": chat}
    result = runner.run_cycle(
        spool=spool, watchlist=wl, policy_id=args.policy_id, environment=args.environment,
        now=datetime.now(timezone.utc), source_root=args.source_root,
        ingress=IngressClient(args.ingress_url, lambda: token), destination_binding=args.destination,
        kill=control.killed, paused=control.paused, details_link=args.details_link, telegram=telegram,
        envelope={k: v for k, v in (("max_wall_ms", args.max_wall_ms), ("max_cpu_ms", args.max_cpu_ms),
                                    ("max_rss_kb", args.max_rss_kb)) if v is not None} or None,
        telemetry=_telemetry(), deps=deps,
        clock=lambda: datetime.now(timezone.utc))
    out = {k: result.get(k) for k in ("cycle_key", "state", "reason", "channels", "resources",
                                      "envelope_exceeded") if k in result}
    return _emit(out, 0 if result.get("state") in ("delivered", "paused", "skipped")
                 or result.get("reason") == "already_final" else 3)



# The scheduled-jobs status contract (core/jobs/status.py): a "running" file at start and an
# ok/warn/failed file at the end, with fixed phrases and codes only (never brief content).
_OUTCOME = {"delivered": ("ok", "brief delivered"), "paused": ("warn", "paused by owner"),
            "skipped": ("warn", "overlapping run skipped"), "delivery_pending": ("warn", "brief delivery pending"),
            "delivery_unknown": ("warn", "brief delivery unconfirmed"), "held": ("failed", "brief held for operator"),
            "missed": ("failed", "brief missed")}


def _job_outcome(out: dict) -> tuple[str, str, dict]:
    state = out.get("state") or "error"
    job_state, summary = _OUTCOME.get(state, ("failed", "brief cycle failed"))
    results = {"brief": state if re.fullmatch(r"[a-z0-9][a-z0-9_]{0,39}", state) else "error"}
    telegram = (out.get("channels") or {}).get("telegram")
    if telegram:
        results["telegram"] = telegram
        if job_state == "ok" and telegram != "acknowledged":
            job_state, summary = "warn", "brief delivered; telegram copy " + telegram.replace("_", " ")
    if out.get("envelope_exceeded"):
        results["resources"] = "over_envelope"
        if job_state == "ok":
            job_state, summary = "warn", "brief delivered; resource envelope exceeded"
    return job_state, summary, results


def _with_job_status(args) -> int:
    try:
        jobs = jobs_registry.load(args.jobs_registry) if args.jobs_registry else jobs_registry.load()
    except jobs_registry.RegistryError:
        return _emit({"state": "config_error", "reason": "jobs_registry_unreadable"}, 2)[0]
    job = jobs.get(args.job_id)
    if job is None:
        return _emit({"state": "config_error", "reason": "job_not_registered"}, 2)[0]
    started = datetime.now(timezone.utc)
    running = jobs_status.start_run(args.jobs_root, job, started.strftime("%Y%m%dT%H%M%SZ"), started)
    try:
        code, out = _cycle(args)
    except BaseException:
        jobs_status.finish_run(args.jobs_root, job, running, state="failed", now=datetime.now(timezone.utc),
                               exit_code=1, summary="brief cycle crashed", results={"brief": "crashed"},
                               next_due_by=None)
        raise
    state, summary, results = _job_outcome(out)
    jobs_status.finish_run(args.jobs_root, job, running, state=state, now=datetime.now(timezone.utc),
                           exit_code=code, summary=summary, results=results, next_due_by=None)
    return code

if __name__ == "__main__":
    raise SystemExit(main())
