"""Builds the job manager for a mounted Guild UI and starts its ticker, only when ``MINIMOI_GUILD_JOBS`` is on.

With the switch off nothing here runs: no manager, no thread, no file is touched, and every job route answers that jobs
are off. The manager's relay client is the chat path's own connection (same URL and caller token), so jobs never need a
second credential."""
from __future__ import annotations

import logging
import os

from .conversations import conversations_of
from .jobs import JOBS_VAR, JobManager, Ticker, jobs_enabled
from .mc.job_relay import JobRelay

log = logging.getLogger("guild_ui.jobs")
TICKER_VAR = "MINIMOI_GUILD_JOBS_TICKER"
TICK_SECONDS_VAR = "MINIMOI_GUILD_JOBS_TICK_SECONDS"


def build_manager(services, *, environ=None, clock=None, relay=None) -> JobManager:
    from .payment_scrub import scrub
    folder = getattr(services.store, "folder", None)
    if relay is None:
        mc = services.mc
        if getattr(mc, "kind", "") == "openclaw":
            relay = JobRelay(getattr(mc, "url", ""), getattr(mc, "_token", ""), http_get=getattr(mc, "_get", None),
                             http_post=getattr(mc, "_post", None))
        else:
            relay = JobRelay("", "")
    conversations = conversations_of(services)
    base = services.floor

    def floor_for(conv):
        nf = (conv or {}).get("notes_floor")
        return base if not nf or nf == base.floor else base.for_floor(nf)

    def touch(conv, principal):
        if conv and not conv.get("unfiled"):
            conversations.touch(conv["id"], principal)
    kwargs = {"clock": clock} if clock is not None else {}
    relay = _with_delegation(relay, environ if environ is not None else os.environ)
    return JobManager(folder, relay, conversations=conversations, floor_for=floor_for, scrub=scrub, touch=touch, **kwargs)


def _with_delegation(relay, environ):
    """When MINIMOI_MC_DELEGATION=1 (with the owner principal, environment and DATABASE_URL), jobs that name a delegate
    go to the delegation dispatcher; every other job is unchanged."""
    if str(environ.get("MINIMOI_MC_DELEGATION", "")).strip() != "1":
        return relay
    principal, env_name, dsn = (environ.get("MINIMOI_MC_DELEGATION_PRINCIPAL"), environ.get("MINIMOI_MC_DELEGATION_ENV"),
                                environ.get("DATABASE_URL"))
    if not principal or env_name not in ("dev", "prod") or not dsn:
        log.warning("guild jobs: delegation requested but not configured; delegation stays off")
        return relay
    import psycopg2
    from core.mc_delegation_dispatcher.relay import DelegationRelay, RoutingRelay
    from core.mc_delegation_dispatcher.store import DispatcherStore
    store = DispatcherStore(lambda: psycopg2.connect(dsn, connect_timeout=5), principal=principal, environment=env_name)
    return RoutingRelay(relay, DelegationRelay(store, ref_for=_main_head))


def _main_head(repository):
    """The exact commit a delegate starts from: the public repository's main head."""
    import re as _re
    import requests
    if repository != "personal-ai-agents":
        raise ValueError("repository_unknown")
    resp = requests.get("https://api.github.com/repos/robertvanstedum/personal-ai-agents/commits/main",
                        headers={"Accept": "application/vnd.github.sha"}, timeout=(5, 10))
    sha = resp.text.strip() if resp.status_code == 200 else ""
    if not _re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("ref_unavailable")
    return sha


def attach_jobs(app, blueprint_name: str, services, environ=None) -> JobManager | None:
    """Called once when the blueprint is mounted. Returns the manager, or None when jobs are off."""
    environ = os.environ if environ is None else environ
    if not jobs_enabled(environ):
        return None
    manager = build_manager(services, environ=environ)
    app.extensions[blueprint_name]["jobs"] = manager
    if str(environ.get(TICKER_VAR, "1")).strip().lower() not in ("0", "false", "off", "no"):
        try:
            interval = max(0.2, min(30.0, float(environ.get(TICK_SECONDS_VAR) or 3.0)))
        except ValueError:
            interval = 3.0
        ticker = Ticker(manager, interval_s=interval)
        ticker.start()
        app.extensions[blueprint_name]["jobs_ticker"] = ticker
        log.info("guild jobs: switched on; the ticker is running")
    else:
        log.info("guild jobs: switched on; no ticker (tests drive it)")
    return manager
