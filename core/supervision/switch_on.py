"""Switching the runtime link on in production for the first time (host runtime P1-a, item 3b), its preflight, and
the check that must hold before switching version 2 off again.

The runtime link tells Robert every runtime notice never told, a few messages a tick (C-SF1's outbox). Production's
authority starts fresh, so the first switch-on normally has nothing old to tell. If the authority already has history
(an earlier switch-on, an integrated-test run, a rehearsal), the history is **baselined first**, bounded so that
nothing actionable is silenced (Codex's P1 brief review, S2):

* a **cutoff**: only notices raised up to the authority's last event when the baseline runs; anything raised after it
  (during or after the switch-on) is told as usual;
* only **historical information** is baselined: the stall kinds (stalled, lapsed, overdue, drift), and only for an
  item that has ended. A sweeper-stale notice is never baselined: its key names a time, not an item, so a past outage
  can't be told from one still going on (Codex's P1-a review S1);
* **kept, and told**: everything that waits on Robert or is blocked (an unknown, a ready card, an escalation), every
  ended or interrupted item's notice, and any notice of an item still live;
* **recorded**: which keys were baselined, and why, in a file the operator keeps with the release record.

Decisions still waiting are not notices: they stay waiting and go out as decisions.

    python -m core.supervision.switch_on check [--running]           what the switch-on would meet; exit 1 if not ready
                                                                     (--running: also the authority's two timers fresh,
                                                                     after the switch-on)
    python -m core.supervision.switch_on baseline --yes --record F   the bounded baseline above, its record written to F
    python -m core.supervision.switch_on rollback-check              before switching version 2 off: exit 1 while any
                                                                     version 2 item is not ended or any effect is open

All read the configuration the supervisor reads (DATABASE_URL and MINIMOI_RUNTIME_AUTHORITY_FILE,
MINIMOI_RUNTIME_PRINCIPAL), and print names, keys and counts only, never a value. ``check`` also answers the
operator's pre-checks: migrations 002 and 003 applied, ``Store.contacts_ready()`` true (before
MINIMOI_SUPERVISION_CONTACTS=1 is honoured), the authority's two timers' ages, and the authority file's SHA-256 (the
portal and the supervisor log the same digest at start, so all three read one file).
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


HISTORICAL_RULES = ("stalled", "lapsed", "overdue", "drift")   # information, once the item ended (never sweeper_stale)
TERMINAL = ("completed", "failed", "refused", "timed_out", "cancelled", "output_limit", "interrupted", "expired")


def raised(authority) -> list:
    """Every rule notice the authority has raised (``notice.raised``), whatever its age, oldest first:
    [{seq, key, rule, kind}]. Decision notices are left out: their decisions go out as decisions, while they wait."""
    out, last = [], 0
    while True:
        page = authority.notices_since(last, hours=None)
        for e in page:
            last = max(last, e["seq"])
            n = e["notice"] or {}
            if n.get("rule") in ("decision", "decisions") or not n.get("key"):
                continue
            out.append({"seq": e["seq"], "key": n["key"], "rule": n.get("rule"), "kind": n.get("kind")})
        if len(page) < PAGE:
            return out


def raised_keys(authority) -> list:
    return list(dict.fromkeys(n["key"] for n in raised(authority)))


def untold(authority, store) -> list:
    """The raised notices the link would tell at its first tick: raised, and not told before."""
    keys = raised_keys(authority)
    told = store.told_keys(keys) if keys else set()
    return [k for k in keys if k not in told]


def live_items(authority) -> set:
    """The version 2 items not ended, by id (request_id as text)."""
    with authority._tx() as cur:
        cur.execute("SELECT request_id::text AS id FROM guild.mc_delegations WHERE route IS NOT NULL "
                    "AND state <> ALL(%s)", (list(TERMINAL),))
        return {r["id"] for r in cur.fetchall()}


def plan_baseline(notices, live, cutoff) -> dict:
    """Which raised notices a first switch-on may record as told without telling (pure). -> {cutoff_seq,
    baselined: [{key, why}], kept: [{key, why}]}."""
    baselined, kept, seen = [], [], set()
    for n in notices:
        key = n["key"]
        if key in seen:
            continue
        seen.add(key)
        if n["seq"] > cutoff:
            kept.append({"key": key, "why": "after_cutoff"})
        elif n.get("kind") in ("waiting_on_robert", "blocked") or n.get("rule") not in HISTORICAL_RULES:
            kept.append({"key": key, "why": f"actionable:{n.get('rule')}"})
        elif any(i in key for i in live):
            kept.append({"key": key, "why": "item_live"})
        else:
            baselined.append({"key": key, "why": f"historical:{n.get('rule')}"})
    return {"cutoff_seq": cutoff, "baselined": baselined, "kept": kept}


def baseline(authority, store, *, record=None) -> dict:
    """The bounded baseline, applied: the baselined keys recorded as told (the check-in's once-per-key memory); the
    kept ones left for the link to tell. Idempotent. ``record``: a file the plan is written to first (0600)."""
    notices = raised(authority)
    cutoff = max((n["seq"] for n in notices), default=0)
    plan = plan_baseline(notices, live_items(authority), cutoff)
    if record is not None:
        import os as _os
        fd = _os.open(str(record), _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL, 0o600)
        with _os.fdopen(fd, "w") as fh:
            json.dump(plan, fh, indent=1, sort_keys=True)
    keys = [b["key"] for b in plan["baselined"]]
    fresh = store.new_alert_keys(keys) if keys else []
    return {"cutoff_seq": cutoff, "baselined": len(keys), "newly_marked": len(fresh), "kept": len(plan["kept"])}


def rollback_blockers(authority) -> dict:
    """Before switching version 2 off (unsetting the authority file in the portal and the supervisor): every version 2
    item must have ended and no effect of any may be open. A version 1 store's claim stalls behind a queued version 2
    item (002's header), and an open effect needs the authority to settle it. -> {live: [...], open_effects: [...]}"""
    live = sorted(live_items(authority))
    open_effects = []
    with authority._tx() as cur:
        cur.execute("SELECT request_id::text AS id FROM guild.mc_delegations WHERE route IS NOT NULL")
        for r in cur.fetchall():
            ops = authority._open_receipts(cur, r["id"])
            if ops:
                open_effects.append({"item_id": r["id"], "operations": [o["operation_id"] for o in ops]})
    return {"live": live, "open_effects": open_effects}


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
    out = {"migrations": migrations, "contacts_ready": None, "raised": None, "untold": None, "timers": None,
           "authority_file": None, "running": False}
    path = getattr(authority, "source_path", None)
    if path:
        from core.mc_delegation_dispatcher.production import file_digest
        out["authority_file"] = file_digest(path)
    if not all(migrations.values()):
        out["ready"] = False
        return out
    out["contacts_ready"] = bool(store.contacts_ready())
    keys = raised_keys(authority)
    pending = untold(authority, store)
    out.update(raised=len(keys), untold=len(pending), untold_keys=pending[:20])
    plan = plan_baseline([n for n in raised(authority) if n["key"] in set(pending)], live_items(authority),
                         max((n["seq"] for n in raised(authority)), default=0))
    out["would_baseline"] = len(plan["baselined"])
    try:
        from core.mc_delegation_dispatcher import server
        out["timers"] = server.ages(authority)
    except Exception as exc:
        out["timers"] = {"unreadable": type(exc).__name__}
    # Ready to switch on when nothing historical waits to be told: what the baseline would keep (actionable, live) is
    # told on purpose, so only baselinable history blocks a switch-on until the baseline has run. Running is
    # separate (Codex's P1-a review S1): after the switch-on, the authority's liveness and sweeper are both fresh.
    out["ready"] = out["contacts_ready"] and not plan["baselined"]
    timers = out["timers"] or {}
    out["running"] = bool(out["ready"] and all((timers.get(t) or {}).get("fresh") for t in ("liveness", "sweeper")))
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
    link.authority.source_path = environ["MINIMOI_RUNTIME_AUTHORITY_FILE"]
    return link.authority, Store(connect), connect


def main(argv=None, environ=None) -> int:
    words = list(sys.argv[1:] if argv is None else argv)
    environ = os.environ if environ is None else environ
    if words in (["check"], ["check", "--running"]):
        authority, store, connect = _from_environ(environ)
        out = check(authority, store, connect)
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out["running" if "--running" in words else "ready"] else 1
    if words[:2] == ["baseline", "--yes"] and len(words) == 4 and words[2] == "--record":
        authority, store, _ = _from_environ(environ)
        print(json.dumps(baseline(authority, store, record=words[3]), sort_keys=True))
        return 0
    if words == ["rollback-check"]:
        authority, _, _ = _from_environ(environ)
        out = rollback_blockers(authority)
        print(json.dumps({"live": len(out["live"]), "open_effects": len(out["open_effects"]), **out}, sort_keys=True))
        return 0 if not out["live"] and not out["open_effects"] else 1
    print("usage: python -m core.supervision.switch_on check [--running] | baseline --yes --record FILE | rollback-check",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
