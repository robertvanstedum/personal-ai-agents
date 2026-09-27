"""Stoplight rules for the Shop floor (SPEC rev 3 §2a). Pure functions, tested.

Four states. Every state has a word and a shape, so colour is never the only
signal. Precedence: unknown > red > yellow > green. Any failed, missing,
stale or not-instrumented source is unknown, never green.
"""
from __future__ import annotations

STATES = {
    "green": {"word": "OK", "shape": "circle"},
    "yellow": {"word": "Watch", "shape": "triangle"},
    "red": {"word": "Problem", "shape": "square"},
    "unknown": {"word": "Unknown", "shape": "ring"},
}
SOURCE_MARK = {"live": "live", "sample": "sample", "not_instrumented": "not instrumented"}


def make(state: str, reason: str, source: str) -> dict:
    return {"state": state, **STATES[state], "reason": reason, "source": source,
            "source_mark": SOURCE_MARK.get(source, source)}


def queue_light(res) -> dict:
    """Build queue: read failed → unknown; blocked → red; unknown-status rows → yellow; else green."""
    if res.status != "ok":
        return make("unknown", "queue read failed — treat as unknown", res.source)
    rows = res.data or []
    blocked = sum(1 for i in rows if i.get("status_known") and i.get("status") == "blocked")
    odd = sum(1 for i in rows if not i.get("status_known"))
    active = sum(1 for i in rows if i.get("status_known") and i.get("status") in ("spec_ready", "in_build"))
    if blocked:
        return make("red", f"{blocked} blocked", res.source)
    if odd:
        return make("yellow", f"{odd} row{'s' if odd != 1 else ''} with unknown status", res.source)
    return make("green", f"{active} active · 0 blocked", res.source)


def rollout_light(counts, source: str = "sample") -> dict:
    """Rollouts: enabled > 0 and active 0 → red; access gap or unreached enabled users → yellow; else green."""
    if not counts:
        return make("unknown", "no rollout data", "not_instrumented")
    n = {c["label"].lower(): c["n"] for c in counts}
    try:
        intended, enabled, reached, active = n["intended"], n["enabled"], n["reached"], n["active"]
    except KeyError:
        return make("unknown", "incomplete rollout data", source)
    gap = intended - enabled
    if enabled > 0 and active == 0:
        return make("red", "enabled but nobody active", source)
    if gap > 0 or reached < enabled:
        parts = []
        if gap > 0:
            parts.append(f"access gap {gap}")
        if reached < enabled:
            parts.append(f"{enabled - reached} enabled not reached")
        return make("yellow", " · ".join(parts), source)
    return make("green", f"all {enabled} enabled have reached it · {active} active", source)


def _unusable(res):
    if res.status == "stale":
        return make("unknown", "stale — treat as unknown", res.source)
    if res.status != "ok" or res.data is None:
        return make("unknown", "not instrumented" if res.source == "not_instrumented" else "read failed — treat as unknown",
                    res.source)
    return None


def tile_light(res) -> dict:
    """A tile with no rule of its own can only say unknown or OK-by-source; today only Systems uses it, which is not instrumented."""
    bad = _unusable(res)
    if bad:
        return bad
    return make("unknown", "no rule for this tile yet", res.source)


def agents_light(res) -> dict:
    bad = _unusable(res)
    if bad:
        return bad
    d = res.data
    if not isinstance(d.get("needs_robert"), int) or not isinstance(d.get("failed"), int):
        return make("unknown", "agent counts missing", res.source)
    if d["failed"] > 0:
        return make("red", f"{d['failed']} failed", res.source)
    if d["needs_robert"] > 0:
        return make("yellow", f"{d['needs_robert']} needs you · {d.get('running', 0)} running", res.source)
    return make("green", f"{d.get('running', 0)} running · none need you", res.source)


def usage_variants(sources, cfg, clocks=("sat", "mon")) -> list[dict]:
    """Usage & limits (rev 3.1): one variant per simulated clock; rules in usage.py."""
    from .usage import DEFAULT_PRECEDENCE, summarize
    res = sources.operate.usage()
    if res.status != "ok" or not res.data:
        return [{"when": None, **make("unknown", "usage sources unavailable", res.source)}]
    prec = tuple(cfg.get("precedence") or DEFAULT_PRECEDENCE)
    out = []
    for k in clocks:
        lt = summarize(res.data, k, prec)["light"]
        out.append({"when": {"clock": k}, **make(lt["state"], lt["reason"], "sample")})
    return out


def derive_lights(cfg_lights, sources, queue_res, clocks=("sat", "mon")) -> list[dict]:
    """One entry per configured light. Clock-dependent lights (Rollouts) carry
    one variant per simulated clock, shown by the page's show-when."""
    out = []
    for c in cfg_lights:
        rule = c["rule"]
        if rule == "queue":
            variants = [{"when": None, **queue_light(queue_res)}]
        elif rule == "rollout":
            ro = sources.operate.rollout()
            by_clock = (ro.data or {}).get("by_clock", {}) if ro.status == "ok" else {}
            variants = [{"when": {"clock": k}, **rollout_light((by_clock.get(k) or {}).get("counts"), ro.source)}
                        for k in clocks]
        elif rule == "usage":
            variants = usage_variants(sources, c, clocks)
        else:
            res = sources.operate.tile(c["tile"])
            fn = {"agents": agents_light}.get(rule, tile_light)
            variants = [{"when": None, **fn(res)}]
        out.append({**c, "variants": variants})
    return out
