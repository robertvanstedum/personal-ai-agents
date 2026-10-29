"""Switching the runtime link on in production for the first time (host runtime P1-a, item 3b), and its preflight.

The runtime link tells Robert every runtime notice never told, a few messages a tick (C-SF1's outbox). Production's
authority starts fresh, so the first switch-on has nothing old to tell. If the authority already has history (an
earlier switch-on, an integrated-test run, a rehearsal), every notice it raised before is marked as told **first**, so
switching the link on sends no burst of stale notices. Decisions still waiting are not notices: they stay waiting and
go out as decisions.

    python -m core.supervision.switch_on check                 what the switch-on would meet; exit 1 if not ready
    python -m core.supervision.switch_on mark-told --yes       every notice raised so far, recorded as told (once)

Both read the configuration the supervisor reads (DATABASE_URL and MINIMOI_RUNTIME_AUTHORITY_FILE,
MINIMOI_RUNTIME_PRINCIPAL), and print names and counts only, never a value. ``check`` also answers the operator's
pre-checks: migrations 002 and 003 applied, ``Store.contacts_ready()`` true (before MINIMOI_SUPERVISION_CONTACTS=1 is
honoured), and the authority's two timers' ages.
"""
from __future__ import annotations

import json
import os
import sys

MIGRATION_OBJECTS = {
    "002": ("guild.mc_delegation_events", "guild.mc_delegation_reservations"),
    "003": ("guild.supervision_decision_messages", "guild.supervision_contacts", "guild.supervision_watch",
            "guild.supervision_outbox"),
}
PAGE = 500


def raised_keys(authority) -> list:
    """Every rule notice the authority has raised (``notice.raised``), whatever its age, oldest first. Decision notices
    are left out: their decisions go out as decisions, while they wait."""
    keys, last = [], 0
    while True:
        page = authority.notices_since(last, hours=None)
        for e in page:
            last = max(last, e["seq"])
            n = e["notice"] or {}
            if n.get("rule") in ("decision", "decisions") or not n.get("key"):
                continue
            keys.append(n["key"])
        if len(page) < PAGE:
            return list(dict.fromkeys(keys))


def untold(authority, store) -> list:
    """The raised notices the link would tell at its first tick: raised, and not told before."""
    keys = raised_keys(authority)
    told = store.told_keys(keys) if keys else set()
    return [k for k in keys if k not in told]


def mark_history_told(authority, store) -> dict:
    """Record every notice raised so far as told, before the link's first switch-on (3b). Idempotent: a notice told
    before stays told; a later notice is told as usual. -> {raised, marked}."""
    keys = raised_keys(authority)
    fresh = store.new_alert_keys(keys) if keys else []
    return {"raised": len(keys), "marked": len(fresh)}


def _present(cur, name) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (name,))
    row = cur.fetchone()
    return bool(row["present"] if isinstance(row, dict) else row[0])


def check(authority, store, connect) -> dict:
    """What a first switch-on would meet. ``ready`` is true only when 002 and 003 are applied, contacts are ready,
    and no old notice is untold (else mark them told first)."""
    from psycopg2.extras import RealDictCursor
    with connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        migrations = {m: all(_present(cur, t) for t in names) for m, names in MIGRATION_OBJECTS.items()}
        cur.execute("""SELECT count(*) AS n FROM information_schema.columns WHERE table_schema='guild'
            AND table_name='mc_delegations' AND column_name='queue_seq'""")
        migrations["002"] = migrations["002"] and cur.fetchone()["n"] == 1
    out = {"migrations": migrations, "contacts_ready": None, "raised": None, "untold": None, "timers": None}
    if not all(migrations.values()):
        out["ready"] = False
        return out
    out["contacts_ready"] = bool(store.contacts_ready())
    keys = raised_keys(authority)
    pending = untold(authority, store)
    out.update(raised=len(keys), untold=len(pending), untold_keys=pending[:20])
    try:
        from core.mc_delegation_dispatcher import server
        out["timers"] = server.ages(authority)
    except Exception as exc:
        out["timers"] = {"unreadable": type(exc).__name__}
    out["ready"] = out["contacts_ready"] and not pending
    return out


def _from_environ(environ):
    import psycopg2
    from core.supervision.store import Store
    from core.supervision.runtime import from_environ
    dsn = environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("refused: DATABASE_URL is not set")
    connect = lambda: psycopg2.connect(dsn, connect_timeout=5, options="-c statement_timeout=10000")  # noqa: E731
    if not (environ.get("MINIMOI_RUNTIME_AUTHORITY_FILE") and environ.get("MINIMOI_RUNTIME_PRINCIPAL")):
        raise SystemExit("refused: MINIMOI_RUNTIME_AUTHORITY_FILE and MINIMOI_RUNTIME_PRINCIPAL are not set")
    env = {"MINIMOI_RUNTIME_OWNER": "robert", "MINIMOI_RUNTIME_VIEW_BOUND_S": "120", **environ}
    link = from_environ(env, connect)
    return link.authority, Store(connect), connect


def main(argv=None, environ=None) -> int:
    words = list(sys.argv[1:] if argv is None else argv)
    environ = os.environ if environ is None else environ
    if words[:1] == ["check"] and len(words) == 1:
        authority, store, connect = _from_environ(environ)
        out = check(authority, store, connect)
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out["ready"] else 1
    if words == ["mark-told", "--yes"]:
        authority, store, _ = _from_environ(environ)
        print(json.dumps(mark_history_told(authority, store), sort_keys=True))
        return 0
    print("usage: python -m core.supervision.switch_on check | mark-told --yes", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
