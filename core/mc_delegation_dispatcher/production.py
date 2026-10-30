"""Production's authority, built only when production's configuration names it (host runtime P1-a).

Every production process that builds a ``DispatcherStore`` asks here first. With nothing named, each builds exactly
today's store (version 1, no authority), so production behaves as it does now. With the authority's file named, each
builds the same version 2 store over the same file, the same grant catalogue and the same card classes:

* the portal (``minimoi_portal/guild_ui/jobs_wiring.py`` and ``supervision.attach_runner_door``):
  ``MINIMOI_MC_DELEGATION_AUTHORITY_FILE``;
* the supervisor, whose runtime link and authority timers serve the same store (``core/supervision/runtime.py``
  ``from_environ`` and ``services/supervisor/main.py``): ``MINIMOI_RUNTIME_AUTHORITY_FILE``;
* the integrated test's controller (``tests/harness_acceptance/p1a.py``).

A file that is named and cannot be used is a refusal, never a quiet fall back to version 1: once version 2 items
exist, a version 1 claim would stall behind them (002's header). The caller logs the code and keeps the feature off.

The door's credentials (``MINIMOI_MC_DELEGATION_DOOR_CREDENTIALS``, ``minimoi.door_credentials.v1``: each host's and
each reader's token SHA-256, never a token) say which host each version 2 caller speaks for (server.answer binds every
runner id and facts host id to it) and who may read the snapshot. Version 1's single runner token stays as today.

Stdlib only at import; the grant catalogue (core.minimoi_actions, Python 3.10+) is imported when a store is built.
"""
from __future__ import annotations

from pathlib import Path

AUTHORITY_FILE_VAR = "MINIMOI_MC_DELEGATION_AUTHORITY_FILE"      # the portal's name for production's authority file
DOOR_CREDENTIALS_VAR = "MINIMOI_MC_DELEGATION_DOOR_CREDENTIALS"  # the version 2 hosts' and readers' digests
STATE_FILE_VAR = "MINIMOI_RUNTIME_STATE_FILE"                    # the published state.json the read path serves
# fakework's sink effect (core/mc_delegation_runner/fakework.py OPERATION, a runner-only file the portal never imports;
# a test keeps the two equal).
SINK_OPERATION = "fakework.sink_post"


class NotConfigured(ValueError):
    """Named, and refused: a fixed code (``str(exc)``), never a path's content."""


def named(environ, var=AUTHORITY_FILE_VAR) -> str | None:
    value = (environ.get(var) or "").strip()
    return value or None


def load(path, environment=None):
    """The named authority file, which must be version 2 with a sweeper and host values (and, given ``environment``,
    for that environment). Every production loader uses it: the portal, the supervisor's runtime link (review SF5)."""
    from .store import Authority, AuthorityRefused
    try:
        auth = Authority.load(Path(path))
    except (OSError, AuthorityRefused) as exc:
        code = str(exc) if isinstance(exc, AuthorityRefused) else type(exc).__name__
        raise NotConfigured(f"authority_file_unusable:{code}") from None
    if environment is not None and auth.environment != environment:
        raise NotConfigured("authority_environment_mismatch")
    if not auth.speaks_v2 or auth.sweeper is None or auth.hosts is None:
        raise NotConfigured("authority_not_version_2")
    return auth


def file_digest(path) -> str:
    """The authority file's SHA-256, logged by each production process that loads it (portal, supervisor) and printed
    by the switch-on check, so the three are seen to read one file (Codex's P1 brief review, S1)."""
    import hashlib
    with open(Path(path), "rb") as fh:
        return "sha256:" + hashlib.sha256(fh.read()).hexdigest()


def grant_store(authority):
    """The authority's grant catalogue, one for every production process: the two delegation capabilities (one exact
    approval each, as today), cards' ``delegate.card`` row, and, only where the file admits the test routes, the
    acceptance sink's effect (fakework.sink_post: I-2 and I-5 of the integrated test)."""
    from core.minimoi_actions.grants import CapabilityRule, GrantStore
    from . import cards
    rows = {"delegate.start_build": CapabilityRule(1, True, True), "delegate.start_research": CapabilityRule(1, True, True),
            **cards.grant_rows()}
    if authority.test_routes_in_production:
        rows[SINK_OPERATION] = CapabilityRule(1, False, True)
    return GrantStore(rows)


def store(connect, *, principal, environment, authority=None):
    """Today's store when ``authority`` is None; else production's version 2 store over it."""
    from .store import DispatcherStore
    if authority is None:
        return DispatcherStore(connect, principal=principal, environment=environment)
    return DispatcherStore(connect, principal=principal, environment=environment, authority=authority,
                           grants=grant_store(authority))


def door_credentials(environ, environment) -> tuple:
    """(hosts {host_id: sha256}, readers {name: {sha256, role}}) from the named credentials file, or ({}, {}) when
    none is named (then no version 2 host may speak and nobody may read). A named file that is not exactly
    ``minimoi.door_credentials.v1`` for this environment is refused."""
    path = named(environ, DOOR_CREDENTIALS_VAR)
    if path is None:
        return {}, {}
    from .server import load_credentials
    try:
        return load_credentials(path, environment)
    except ValueError as exc:
        raise NotConfigured(str(exc)) from None
