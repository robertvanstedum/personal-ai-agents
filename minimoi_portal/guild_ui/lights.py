"""Status lights for the real Shop floor (spec §3, S4). Pure functions, tested.

Four states, each with a word and a shape, so colour is never the only
signal. Precedence for a summary: unknown > red > yellow > green. Any failed,
missing or not-instrumented source is unknown, never green and never zero.
Every light carries a one-line reason of at most 40 characters (C22 review:
Claude Chat §2); the rest is in its detail lines.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .adapters.contract import NOT_INSTRUMENTED, SourceResult

STATES = {
    "green": {"word": "OK", "shape": "circle"},
    "yellow": {"word": "Watch", "shape": "triangle"},
    "red": {"word": "Problem", "shape": "square"},
    "unknown": {"word": "Unknown", "shape": "ring"},
}
PRECEDENCE = ("unknown", "red", "yellow", "green")
SOURCE_MARK = {"live": "live", NOT_INSTRUMENTED: "not instrumented"}
REASON_MAX = 40
SYSTEMS_FRESH_S = 600  # the Operations agent must have checked in within 10 minutes
SYSTEMS_SKEW_S = 60    # a check-in further ahead of this portal's clock is clock skew, not fresh


def short(text: str, limit: int = REASON_MAX) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def make(state: str, reason: str, res: SourceResult | None, detail=None) -> dict:
    source = res.source if res is not None else NOT_INSTRUMENTED
    return {
        "state": state, **STATES[state], "reason": short(reason),
        "source": source, "source_mark": SOURCE_MARK.get(source, source),
        "observed_at": res.observed_at if res is not None else None,
        "evidence": res.evidence if res is not None else "",
        "error": res.error if res is not None else None,
        "detail": list(detail or []),
    }


def queue_light(res: SourceResult) -> dict:
    """Read failed → unknown; any blocked → red; unknown-status rows → yellow; else green."""
    if res.status != "ok":
        return make("unknown", "queue read failed", res, [f"treat as unknown — {res.error or 'read failed'}"])
    rows = res.data or []
    blocked = sum(1 for i in rows if i.get("status_known") and i.get("status") == "blocked")
    odd = sum(1 for i in rows if not i.get("status_known"))
    active = sum(1 for i in rows if i.get("status_known") and i.get("status") in ("spec_ready", "in_build"))
    detail = [f"{active} active (Spec Ready + In Build)", f"{blocked} blocked", f"{len(rows)} rows in the queue file"]
    if odd:
        detail.append(f"{odd} unknown row{'s' if odd != 1 else ''} (status or fields unreadable)")
    if blocked:
        return make("red", f"{blocked} blocked · {active} active", res, detail)
    if odd:
        return make("yellow", f"{odd} unknown row{'s' if odd != 1 else ''}", res, detail)
    return make("green", f"{active} active · 0 blocked", res, detail)


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def systems_light(res: SourceResult, now: datetime | None = None) -> dict:
    """Green only when the Operations agent is reachable, running and checked in
    within 10 minutes. Everything else is unknown (grey), never green, with
    the reason; a check-in time in the future is clock skew (review F6). The
    agent's escalation count is shown as reported and never counts toward
    green, because it reads 0 when the agent's own database read fails (C22)."""
    if res.source == NOT_INSTRUMENTED:
        return make("unknown", "Operations agent not configured", res,
                    ["no GUILD_OPERATIONS_STATUS_URL on this portal"])
    if res.status != "ok" or not isinstance(res.data, dict):
        return make("unknown", "Operations agent unreachable", res,
                    [f"treat as unknown — {res.error or 'no answer'}"])
    data = res.data
    state = data.get("state")
    escalations = data.get("open_escalations")
    detail = [f"agent state: {state}"]
    detail.append(f"escalations: {escalations if isinstance(escalations, int) else 'not reported'}"
                  " (reported by the agent; never counts toward green)")
    checkin = _parse_time(data.get("last_checkin"))
    now = now or datetime.now(timezone.utc)
    if state != "running":
        return make("unknown", f"agent reports {short(state, 20)}", res, detail)
    if checkin is None:
        detail.append("last check-in: missing or unreadable")
        return make("unknown", "no readable check-in time", res, detail)
    age_s = (now - checkin).total_seconds()
    if age_s < -SYSTEMS_SKEW_S:
        ahead_min = int(-age_s // 60)
        detail.append(f"last check-in: {ahead_min} min in the future (clock skew; not fresh)")
        return make("unknown", "clock skew · check-in in the future", res, detail)
    age_min = max(0, int(age_s // 60))
    detail.append(f"last check-in: {age_min} min ago")
    if age_s > SYSTEMS_FRESH_S:
        return make("unknown", f"last check-in {age_min} min ago", res, detail)
    return make("green", f"running · checked in {age_min} min ago", res, detail)


SKEW_S = 60


def memory_copy_light(res: SourceResult, now: datetime | None = None) -> dict:
    """The Agents light, today carrying the memory copy (Spec 160 §7).

    The reason always says "memory copy" because this light will later carry agent
    run state too. Never green unless the scheduler answered, the copier is on,
    its worst source is green, and the answer is not from the future. A stale or
    failed copy is yellow or red as the scheduler says; no answer is unknown.
    """
    if res.source == NOT_INSTRUMENTED:
        return make("unknown", "memory copy not configured", res, ["no GUILD_MEMORY_STATUS_URL on this portal"])
    if res.status != "ok" or not isinstance(res.data, dict):
        return make("unknown", "memory copy no answer", res, [f"treat as unknown — {res.error or 'no answer'}"])
    data = res.data
    detail = [f"{name}: {row['state']} · {row['reason']}" for name, row in sorted(data.get("sources", {}).items())]
    for name, row in sorted(data.get("turn_log", {}).items()):
        bits = [f"last saved {row['last_success_at']}" if row.get("last_success_at") else "never saved",
                f"last failure {row['last_failure_code']}" if row.get("last_failure_code") else None]
        detail.append(f"turn log ({name}): " + " · ".join(b for b in bits if b))
    if not data.get("enabled"):
        return make("unknown", "memory copy off", res, detail + ["the copier is switched off on the scheduler"])
    as_of = _parse_time(data.get("as_of"))
    if as_of is not None and (as_of - (now or datetime.now(timezone.utc))).total_seconds() > SKEW_S:
        return make("unknown", "memory copy clock skew", res, detail + ["the answer is dated in the future"])
    state = data.get("state") if data.get("state") in STATES else "unknown"
    reason = data.get("reason") or "memory copy"
    if state == "unknown":
        return make("unknown", reason, res, detail)
    return make(state, reason, res, detail)


def not_instrumented_light(res: SourceResult) -> dict:
    return make("unknown", res.error or "not instrumented", res, ["not instrumented — no source yet"])


def worst(lights) -> str:
    states = {l["state"] for l in lights}
    return next((s for s in PRECEDENCE if s in states), "unknown")


def derive_lights(cfg_lights, services, queue_res: SourceResult, now: datetime | None = None) -> list[dict]:
    out = []
    for c in cfg_lights:
        rule = c["rule"]
        if rule == "queue":
            light = queue_light(queue_res)
        elif rule == "systems":
            light = systems_light(services.systems.status(), now)
        elif rule == "memory_copy":
            light = memory_copy_light(services.memory_copy.status(), now)
        else:
            light = not_instrumented_light(services.not_instrumented[c["id"]].read())
        out.append({**{k: v for k, v in c.items() if not k.startswith("_")}, **light})
    return out
