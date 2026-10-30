"""The named checks of §9.4 (host runtime spec v0.5; S1-H), as pure functions over the run's evidence: the exported
audit (``guild.mc_delegation_events``), the sink's ``effects.jsonl``, the notify files, the census, the launch records
and ``fakecli-env.jsonl``. Each returns ``(ok, n, detail)``: ``n`` is how many facts it examined. A check whose
scenario must produce facts is given ``need``; with fewer than ``need`` facts it fails (``no_facts``), so no check
passes vacuously. ``detail`` names what failed, never a value from a credential.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime


def _at(value):
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _vacuous(n, need):
    return n < need


def dup1(effects, *, need=0):
    """DUP-1: one ``committed`` per operation id (the sink's own lines)."""
    committed = defaultdict(int)
    for line in effects:
        if line["answer"] == "committed":
            committed[line["operation_id"]] += 1
    n = sum(committed.values())
    twice = sorted(op for op, c in committed.items() if c > 1)
    if _vacuous(n, need):
        return False, n, {"no_facts": f"{n} committed, {need} expected"}
    return not twice, n, {"committed_twice": twice}


def dup2(effects, events, *, need=0):
    """DUP-2: every committed line matches a receipt dispatched for its operation at its generation, sent before its
    lease ended; every send at or after its lease's end is ``lease_expired``."""
    dispatched = {(e["body"].get("operation_id"), e["generation"]) for e in events if e["kind"] == "effect.dispatched"}
    bad, n = [], 0
    for line in effects:
        late = line["lease_until"] is None or _at(line["at"]) >= _at(line["lease_until"])
        if line["answer"] == "committed":
            n += 1
            if late or (line["operation_id"], line["generation"]) not in dispatched:
                bad.append({"operation_id": line["operation_id"], "generation": line["generation"], "late": late})
        elif late and line["answer"] not in ("lease_expired",):
            bad.append({"operation_id": line["operation_id"], "late_send_answered": line["answer"]})
    if _vacuous(n, need):
        return False, n, {"no_facts": f"{n} committed, {need} expected"}
    return not bad, n, {"stale": bad}


def lease_spans(events) -> dict:
    """item -> [(host, start, end)] from the audit: per item, generation and host, the claim (or resume) to the last
    lease end that host was given."""
    spans = {}
    for e in events:
        if e["kind"] in ("item.claimed", "item.resumed", "lease.renewed") and e["body"].get("lease_until") \
                and e["host_id"]:
            key = (str(e["item_id"]), e["generation"], e["host_id"])
            start = spans.get(key, (_at(e["at"]), None))[0]
            spans[key] = (start, _at(e["body"]["lease_until"]))
    by_item = defaultdict(list)
    for (item, _, host), (start, end) in spans.items():
        by_item[item].append((host, start, end))
    return by_item


def overlaps(spans) -> list:
    out = []
    for i, (ha, sa, ea) in enumerate(spans):
        for hb, sb, eb in spans[i + 1:]:
            if ha != hb and sa < eb and sb < ea:
                out.append((ha, hb))
    return out


def dup3(events, *, need=0):
    """DUP-3: lease intervals held by different hosts never overlap, per item, from the audit."""
    by_item = lease_spans(events)
    n = sum(len(v) for v in by_item.values())
    bad = {item: overlaps(s) for item, s in by_item.items() if overlaps(s)}
    if _vacuous(n, need):
        return False, n, {"no_facts": f"{n} lease spans, {need} expected"}
    return not bad, n, {"overlapping": bad}


def dup4(events, *, need=0):
    """DUP-4: at most one accepted result per item."""
    accepted = defaultdict(int)
    for e in events:
        if e["kind"] == "result.accepted":
            accepted[str(e["item_id"])] += 1
    twice = sorted(i for i, c in accepted.items() if c > 1)
    n = len(accepted)
    if _vacuous(n, need):
        return False, n, {"no_facts": f"{n} items with a result, {need} expected"}
    return not twice, n, {"accepted_twice": twice}


def dup6(events, *, need=0):
    """DUP-6: an item that entered ``unknown`` gets no new generation before a recorded owner decision on it."""
    bad, n = [], 0
    by_item = defaultdict(list)
    for e in events:
        if e["item_id"] is not None:
            by_item[str(e["item_id"])].append(e)
    for item, evs in by_item.items():
        evs.sort(key=lambda e: e["seq"])
        for i, e in enumerate(evs):
            if e["kind"] != "item.unknown":
                continue
            n += 1
            g = e["generation"]
            for later in evs[i + 1:]:
                if later["kind"] == "decision.resolved" and later["source"] == "owner":
                    break
                if later["generation"] is not None and g is not None and later["generation"] > g:
                    bad.append({"item": item, "unknown_at_generation": g, "then": later["kind"],
                                "generation": later["generation"]})
                    break
    if _vacuous(n, need):
        return False, n, {"no_facts": f"{n} unknown items, {need} expected"}
    return not bad, n, {"new_generation_before_decision": bad}


def dup7(events, notify_lines, *, need=0):
    """DUP-7: one notice per key: each key raised once at the authority, and each raised notice's words delivered at
    most once to Robert (the notify file)."""
    keys = [e["body"].get("key") for e in events if e["kind"] == "notice.raised"]
    twice = sorted({k for k in keys if keys.count(k) > 1})
    texts = [m["text"] for m in notify_lines]
    delivered_twice = []
    for e in events:
        if e["kind"] != "notice.raised" or not e["body"].get("text") or e["body"].get("rule") in ("decision",
                                                                                                    "decisions"):
            continue
        times = sum(t.count(e["body"]["text"]) for t in texts)
        if times > 1:
            delivered_twice.append(e["body"]["key"])
    n = len(keys)
    if _vacuous(n, need):
        return False, n, {"no_facts": f"{n} notices, {need} expected"}
    return not twice and not delivered_twice, n, {"raised_twice": twice, "delivered_twice": delivered_twice}


_ITEM_NAMED = re.compile(r"\bItem (\S+) ")


def notices_about(notify_lines, *, job, item_id) -> list:
    """The notices an owner-step item's "no notice" check counts (spec T6m/X7, OWN-5; gate M review B1, 30 Oct): every
    notice in its window except one that names another item. A notice text names its item by job (``Item T6m.W-M.10
    …``), seldom by id, so matching the id alone saw none of the night's three. A notice naming no item (the
    authority's own: its sweeper, its liveness) counts: on the Mac-only gate the authority sleeps with the Mac, so it
    too must stay quiet. A stall before the sleep counts as well: it is a real notice to Robert; the step asks for the
    lid to be closed well inside the progress timeout."""
    out = []
    for m in notify_lines:
        text = str(m.get("text", ""))
        named = _ITEM_NAMED.findall(text)
        if job in text or item_id in text or item_id[:8] in text or not named or job in named:
            out.append(m)
    return out


def dup8(events, dev_hosts, *, need=0):
    """DUP-8: every authority event is the authority's, a host's or the owner's (never a sync, relay, mirror or
    witness identity's), every host event comes from a dev host, and every event is dev's."""
    bad = []
    for e in events:
        if e["source"] not in ("authority", "host", "owner") or e["environment"] != "dev":
            bad.append({"seq": e["seq"], "source": e["source"], "environment": e["environment"]})
        elif e["source"] == "host" and e["host_id"] not in dev_hosts:
            bad.append({"seq": e["seq"], "host_id": e["host_id"]})
    n = len(events)
    if _vacuous(n, need):
        return False, n, {"no_facts": "no events"}
    return not bad, n, {"foreign": bad[:20]}


def shown_sequence(snapshots, item_id) -> list:
    """What an item was shown as, snapshot by snapshot, repeats folded: e.g. ['running', 'held', 'lapsed']."""
    out = []
    for s in snapshots:
        doc = s.get("doc") or {}
        for it in doc.get("items", []):
            if it["item_id"] == item_id:
                if not out or out[-1] != it["shown_as"]:
                    out.append(it["shown_as"])
    return out


def never_running_while_unreachable(snapshots, item_id):
    """R5.1-b: a claimed or running item whose owner is not reachable is never shown running."""
    bad = []
    for s in snapshots:
        for it in (s.get("doc") or {}).get("items", []):
            if it["item_id"] == item_id and it["shown_as"] == "running" and it.get("owner_reachability") != "reachable":
                bad.append(s["n"])
    return bad


def cen1(agents, root_id, *, transient, long_ids):
    """CEN-1: a root that spawned ``transient`` subagents and the ``long_ids`` long ones shows them all as its
    children, each with its parent and role; the transient ones ended, with ``ended_at``."""
    kids = [a for a in agents if a["parent_id"] == root_id]
    done = [a for a in kids if a["state"] == "done" and a["ended_at"]]
    long_rows = [a for a in kids if a["agent_id"].rsplit("/", 1)[-1] in long_ids]
    ok = len(kids) == transient + len(long_ids) and len(done) >= transient and len(long_rows) == len(long_ids) \
        and all(a["role"] for a in kids)
    return ok, len(kids), {"children": len(kids), "done": len(done),
                           "long": [(a["agent_id"].rsplit("/", 1)[-1], a["state"], a["reason"]) for a in long_rows]}


def cen4(agents, root_id):
    """CEN-4: after the root ended, no child is running; an end not observed is ``unknown: end_unobserved``."""
    kids = [a for a in agents if a["parent_id"] == root_id]
    running = [a["agent_id"] for a in kids if a["state"] == "running"]
    unobserved = [a for a in kids if a["state"] == "unknown"]
    wrong = [a["agent_id"] for a in unobserved if a["reason"] != "end_unobserved" or a["ended_at"] is not None]
    return bool(kids) and not running and not wrong, len(kids), {"running": running, "unknown_not_end_unobserved": wrong}


CONTENT_ATTRIBUTES = ("gen_ai.prompt", "gen_ai.completion", "gen_ai.input.messages", "gen_ai.output.messages",
                      "gen_ai.system_instructions", "gen_ai.tool.call.arguments", "gen_ai.tool.call.result")


def cen5(traces, agents, *, need=1):
    """CEN-5: in the OTLP/JSON file every span's parent resolves to a census record (a span of it), and no content
    attribute is present."""
    spans = [sp for rs in traces.get("resourceSpans", []) for ss in rs.get("scopeSpans", []) for sp in ss.get("spans", [])]
    ids = {sp["spanId"] for sp in spans}
    dangling = [sp["spanId"] for sp in spans if sp.get("parentSpanId") and sp["parentSpanId"] not in ids]
    content = sorted({a["key"] for sp in spans for a in sp.get("attributes", [])
                      if a["key"] in CONTENT_ATTRIBUTES or "prompt" in a["key"] or "message" in a["key"]})
    if _vacuous(len(spans), need):
        return False, len(spans), {"no_facts": "no spans"}
    return not dangling and not content, len(spans), {"dangling_parent": dangling[:10], "content": content,
                                                      "agents": len(agents)}


def lch1(records, starts, *, denied, reviewed, need=1):
    """LCH-1: no child's environment holds a deny-pattern name, except the one key an API-route item names, in that
    item's child only: from every launch record and from fakecli-env.jsonl."""
    bad = []
    for r in records:
        allowed = set(reviewed) | ({r["key"]["name"]} if r.get("key") else set())
        extra = [n for n in r["env_names"] if denied(n) and n not in allowed]
        if extra:
            bad.append({"task": r.get("task"), "names": extra})
    keyed_records = sum(1 for r in records if r.get("key"))
    for s in starts:
        extra = [n for n in s["names"] if denied(n) and n not in set(reviewed) | {"FAKEWORK_API_KEY"}]
        if extra:
            bad.append({"start": s["pid"], "names": extra})
    keyed_starts = sum(1 for s in starts if "FAKEWORK_API_KEY" in s["names"])
    if keyed_starts != keyed_records:
        bad.append({"key_in_children": keyed_starts, "api_items": keyed_records})
    n = len(records) + len(starts)
    if _vacuous(len(records), need):
        return False, n, {"no_facts": "no launch records"}
    return not bad, n, {"denied": bad}


def lch6(records, files, values, *, need=1):
    """LCH-6: every launch record carries the same policy digest; no planted value appears in any file searched."""
    digests = sorted({(r["policy"]["version"], r["policy"]["sha256"]) for r in records})
    found = []
    searched = 0
    raw = [v.encode() for v in values]
    for p in files:
        try:
            data = p.read_bytes()
        except OSError:
            continue
        searched += 1
        for i, v in enumerate(raw):
            if v in data:
                found.append({"file": str(p), "value": f"planted value #{i}"})
    if _vacuous(len(records), need) or searched == 0:
        return False, searched, {"no_facts": f"{len(records)} records, {searched} files"}
    return len(digests) == 1 and not found, searched, {"policy_digests": len(digests), "planted_found": found[:10]}
