"""Dispatcher store: delegated work in PostgreSQL, spoken to by the runner protocol
(core/mc_delegation_runner/runner.py) and by the portal's delegation relay.

Runner protocol, version 1 (what ``run_once`` calls; production speaks only this):
  heartbeat(runner_id, state, capabilities)          liveness only, never pickup
  claim(runner_id, capabilities) -> envelope | None  oldest queued job this runner can run; lease + new generation
  progress(request_id, generation, phase)            running
  cancel_requested(request_id, generation) -> bool   the owner pressed Stop
  renew(request_id, generation) -> bool              False: the claim is lost (stale generation, cancelled, expired)
  result(request_id, generation, *, event_id, status, artifacts, detail, output_text=None)
        -> {state: accepted|already_applied|refused, event_id}

Version 2 (host runtime spec v0.5 §3.2; the door's body carries ``protocol: 2``) is spoken only when the authority
configuration (``Authority``) enables it, and only about version 2 items: those that name a ``route`` (R6.7-e).
  heartbeat(runner_id, state, capabilities, protocol=2, facts={host_id, bundle_digest, roles, availability, grace,
            last_gap_s, announced}) -> {state: recorded}
        seen_at is the host's last contact; a change of facts is audited; announcing ``stopped`` pauses every item the
        host holds (owner_stopped). (Census batches ride here later.)
  claim(runner_id, capabilities, protocol=2) -> {envelope, route, unit, reserved, lease_until, card_id, step} | None
        nothing for a host with unreconciled items; the item's capabilities (actor, scope, repository and its
        required_capabilities) within the host's; its grant rechecked (GrantStore.covers_item: never used again);
        the (route, actor) gate; its spend reserved in the route's unit in the same transaction as the lease. A
        refusal is written to the item. Items that must wait (their route closed by unknown usage, or today's budget
        too small) are marked and left out in SQL, so they never hold up other routes. One connection per poll.
  progress(request_id, generation, phase, protocol=2, runner_id, event_id, at, checkpoint)
        -> {state: applied|superseded|already_applied|refused}
        stores phase, progress_at and checkpoint; an older event never overwrites a newer one; never touches the
        lease or heartbeat_at. After the lease lapsed, a record the host made while it ran is still taken (R5.1-c,
        _pre_lapse); a later one is refused lease_lapsed
  cancel_requested(request_id, generation, protocol=2) -> bool         unchanged
  renew(request_id, generation, protocol=2, runner_id) -> bool          also False when the grant is revoked or expired
  result(request_id, generation, *, ..., protocol=2, runner_id, at, route, usage={amount, unit, children_stopped})
        settles the reservation with the observed usage; once accepted, the identical report replayed under its event
        id is already_applied and anything else under that id is refused event_id_conflict (as every version 2 owner
        write: status, detail, usage, artifacts, output and time are compared); a route other than the reserved one
        is refused; unknown
        usage closes the route until a recorded resolution (resolve_usage_hold); after a revocation the item ends
        cancelled (grant_revoked), never completed (R4.3-e); a result made before a lapse is taken as progress is.
        With an effect of the item still open (a receipt dispatched or unknown), a result ends nothing: it is kept as
        evidence (artifacts, usage settled) and the item goes unknown with one decision (code effects_open); while a
        revocation waits on such an effect, a late result is refused (grant_revoked). A record made before a lapse
        the sweeper hasn't written yet has the lapse audited first. On a card step: a completed result writes the
        card's next step in the same transaction (R7.4-b); a definite failure (failed, with a known exit) within the
        card's attempt allowance answers {code: retry, attempt, generation}: the step goes back, queued, to the same
        owner under generation + 1, from its last checkpoint, with what remains of the item's one reservation (R4.3-d).
        A next step this authority refuses to write now ends the card with the reason; the result is kept
  reconcile(runner_id, request_id, generation, stage={child, checkpoint}, open_operations, protocol=2)
        -> continue | resume(generation + 1, lease_until, checkpoint, reserved, unit) | stop(condition, code)
           | decide(code, decision_id); a resume carries what remains of the item's one reservation
        the owner's question for each item it holds after a restart, wake or reconnect (§5.5 step 4). In order:
          not this owner's item at this generation          stop (identity): unknown_request, not_owner, reassigned,
                                                            superseded; a resume already decided for it is answered
                                                            again, so the generation rises once per decision (X5)
          ended                                             stop with its code; a stopped child releases a kept
                                                            reservation
          unknown                                           decide again: the same queued decision
          a receipt dispatched or unknown, or a child the   decide: the item becomes unknown, generation unchanged,
          host cannot verify                                workspace kept, one decision queued (decision.queued)
                                                            listing them, with the end it would otherwise have had
          the owner's Stop; its grant revoked or expired;   stop, and the item ends cancelled, expired, or
          past its grace                                    interrupted (owner_unreachable)
          its grant no longer covers it                     stop (authority), interrupted
          lease never lapsed and the child frozen           continue
          otherwise (lapsed, or the child no longer runs):  the other conditions, each named when it fails:
            deadline passed                                 stop (authority), timed_out
            reservation gone, route gate shut, or the       stop (budget), interrupted
            route held by unknown usage
            the host's checkpoint is not the authority's    stop (checkpoint), interrupted
            all hold                                        resume: generation + 1, same owner, fresh lease
        grace is judged on the database clock every time, after any outage the authority has not yet recorded is
        recorded (_account_outage), so it never waits on the sweeper
  effect(runner_id, request_id, generation, effect={op, key, grant_id, request, lease?, state?, evidence?}, protocol=2)
        op prepare | claim | dispatch | finish | reconcile, each one call of GrantStore.prepare_effect or ReceiptStore's
        claim, dispatch, finish, claim(reconcile), inside the authority (§4.5); the operation id is the authority's,
        from the item and the key; a new effect only by the owner at the current generation under its lease (R4.4),
        an outcome (finish, readback) by the owner whatever has happened since
The authority's liveness (authority_beat, run_liveness) is a row it beats on a timer of its own; a gap in it is the
authority's own outage, recorded as authority.suspended {from, to}, with every open grace extended by it (R5.2-a).
The sweeper (sweep, run_sweeper) writes lapse transitions and never writes running (§5.1): lapsed with its cause and a
grace deadline from the owner host's grace, and at grace end interrupted, or unknown with a decision while an effect is
open. No item ends while an effect of it is open, on any path: reconcile, the sweeper, a result, Stop, end, revoke.
Where it runs, relay.status derives a lapse instead of reaping it (R3.3, E11), and the sweeper reaps version 1 items.
A version 2 authority always has one.
The owner's acts: reassign (R5.1-d), end (R5.1-c), revoke (R4.3-e, end to end), resolve_usage_hold.
Admitting an item (enqueue with a route) uses one of its grant's operations in the same transaction that writes it
queued (GrantStore.use_for_item, R4.3-c). With no grant, none left, or one that doesn't cover it, the item is written
proposed with the reason instead.
Cards (cards.py, R4.3-c with N1, R7.4-a/b): admit_card, on the owner's one exact approval of a card's digest, issues
its delegate.card grant, records the card and admits its first step. Every later step is written by the authority
itself, in the transaction that accepted the step before it: compiled from the card's class template, its inputs the
outputs the authority recorded for earlier steps (the only free field), queued under the card grant, or proposed with
one decision (a step of another model family, or one the grant no longer admits); admit is the owner's act for such
an item. On this item path use_for_item at admission plays prepare_effect's part, and covers_item at claim, renew,
a new effect and reconcile plays recheck_execution's (R4.3-c): a card step's grant check is its template digest,
recomputed from its columns, and each operation of a card grant is bound to one step (grant.step_bound). Its
hand-out also checks its inputs against the records and its attempt against the card's allowance. One reservation
covers an item across all its generations and attempts. Revoking a card's grant ends the card and every open step of
it, whatever grant admitted the step; nothing is written or admitted for an ended card.
Only the owner host of the current generation, inside its lease, may progress, renew or report (R4.4), apart from
replaying what it made before a lapse. Every lease, expiry and grace decision uses the database clock; a host's own
time (``at``) orders that host's progress and places a replayed record inside or outside its lease (clock_skew_s).
Every version 2 transition writes one audit event (guild.mc_delegation_events, R7.6-d); its source is the host for
what a host reports (facts, progress, an accepted result), the owner for an owner's act, and the authority for what
it decides. A host's event keeps the id it sent: an identical replay is already_applied, anything else under that id
is refused (event_id_conflict). The authority's own events take random ids; one it must record only once carries a
once_key, which no host can write. An audit row that cannot be written stops the transaction; it is never dropped.
Without a version 2 configuration the store runs exactly today's statements; with one, version 1 calls keep their
meaning and touch only version 1 items.

Version 2 needs Python 3.10 or later (core.minimoi_actions uses slotted dataclasses), so a version 2 store refuses to
be built below it. This module, version 1 and the production runner's bundle still run on the Mac's Python 3.9.

Every connection is used for one transaction and closed, except a version 2 claim's poll, which runs its short
transactions on one connection. A stale generation, a lapsed lease or a cancelled job can never record a completion.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from core.mc_delegation_dispatcher import cards as card_mod
from core.mc_delegation_runner import envelope as env_mod

_log = logging.getLogger("mc_delegation")

LEASE_SECONDS = 120
RESULT_STATES = ("completed", "failed", "refused", "timed_out", "cancelled", "output_limit", "interrupted")
OUTPUT_MAX = 64 * 1024

# ── version 2 ──────────────────────────────────────────────────────────────
AUTHORITY_SCHEMA = "minimoi.authority.v1"
EVENT_SCHEMA = "minimoi.runtime.event.v1"
UNITS = ("usd", "usd_notional", "minutes", "test_units")
TEST_FAMILY = ("fakework", "fakejudge")       # R6.7-l: the test routes carry no key, and acceptance runs on them
TEST_ACTORS = ("fakework",)                   # the only worker a test route may run (fakejudge's arrives with S1-G)
ROLES = ("authority", "worker", "witness", "watch:staging")
V2_ACTORS = ("claude-code", "codex", "fakework")
GRANT_FAMILY = "delegate."                    # the capabilities whose grants may admit a delegation item
MIN_PYTHON_V2 = (3, 10)
CLAIM_SCAN = 16            # a poll judges at most this many claimable items; each is claimed or ended, none waits
FUTURE_SKEW_S = 60         # how far a host's event time may run ahead of the authority's clock
_ROUTE_LOCK = 0x4D434452   # advisory lock namespace ("MCDR"): every reservation change on one route, one at a time
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_ROUTE = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)*$")
_CAP = re.compile(r"^[a-z][a-z0-9_-]{0,15}:[A-Za-z0-9._:-]{1,64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_WORD = re.compile(r"^[a-z_]{1,40}$")
_PATH = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/@+-]{1,256}$")
_AT = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$")
TERMINAL = RESULT_STATES + ("expired",)
CHILD_STAGES = ("frozen", "stopped", "unverified")   # the host's account of its child for reconcile (§5.5 steps 2, 4)
EFFECT_OPS = ("prepare", "claim", "dispatch", "finish", "reconcile")
EFFECT_POLICY = "minimoi.runtime.effect.v1"   # the policy version an item's effect grant must be issued under
RECEIPT_LEASE_S = 30                           # the receipt's own maximum (receipts.py); never past the item's lease
SWEEP_BATCH = 200                              # items one sweep looks at; the rest wait for the next
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def request_id_for(job_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mc-delegation-job:{job_id}"))


class AuthorityRefused(ValueError):
    """A version 2 call or configuration the authority will not take: a fixed code, never the caller's content."""


def _need(ok, code):
    if not ok:
        raise AuthorityRefused(code)


# ── authority configuration (R2.3-b): one file, read by the store; a missing value admits nothing ──────────
@dataclass(frozen=True)
class Route:
    unit: str
    actors: tuple                      # the workers it may run; none listed, none admitted
    per_day: Decimal | None            # None: no daily cap is configured, so the route admits nothing
    per_item: Decimal | None           # None: likewise
    supervised_only: bool | None       # None: not stated, so treated as supervised-only


@dataclass(frozen=True)
class Sweeper:
    sweep_s: int                       # how often the sweeper runs, and the authority beats its own liveness
    gap_threshold_s: int               # a longer gap in the authority's liveness is its own outage (R5.2-a, N3)


@dataclass(frozen=True)
class Authority:
    environment: str
    protocols: frozenset
    launch_closure: str                # 'pending' until the owner records both gating N5 items (R6.7-l)
    lease_s: int | None
    routes: MappingProxyType
    sweeper: Sweeper | None = None     # version 2 needs one; version 1 only: none runs, and relay.status reaps (R3.3)
    reassign_across_hosts: bool = False   # False on a Mac-only build: no cross-host takeover at all (R2.4-c)
    clock_skew_s: int = 0              # how far a host's clock may be off when its pre-lapse records are judged (§2.5)

    @property
    def speaks_v2(self) -> bool:
        return 2 in self.protocols

    @classmethod
    def load(cls, path) -> "Authority":
        with open(Path(path), "rb") as fh:
            raw = fh.read(64 * 1024 + 1)
        _need(len(raw) <= 64 * 1024, "authority_config_too_large")
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise AuthorityRefused("authority_config_unreadable") from None
        return cls.parse(doc)

    @classmethod
    def parse(cls, doc) -> "Authority":
        _need(type(doc) is dict and set(doc) <= {"schema", "environment", "protocols", "launch_closure", "lease_s",
                                                  "routes", "note", "sweeper", "reassign_across_hosts",
                                                  "clock_skew_s"},
              "authority_config_invalid")
        _need(doc.get("schema") == AUTHORITY_SCHEMA, "authority_schema_invalid")
        _need(doc.get("environment") in ("dev", "prod"), "authority_environment_invalid")
        _need(type(doc.get("note", "")) is str and len(doc.get("note", "")) <= 2000, "authority_note_invalid")
        protocols = doc.get("protocols")
        _need(type(protocols) is list and all(type(p) is int for p in protocols) and len(set(protocols)) == len(protocols)
              and 1 in protocols and set(protocols) <= {1, 2}, "authority_protocols_invalid")   # version 1 always stays
        closure = doc.get("launch_closure", "pending")                                           # missing: pending
        _need(closure in ("pending", "verified"), "authority_launch_closure_invalid")
        lease = doc.get("lease_s")
        _need(lease is None or (type(lease) is int and 10 <= lease <= 3600), "authority_lease_invalid")
        _need(lease is not None or 2 not in protocols, "authority_lease_missing")    # no lease, no version 2 at all
        routes = doc.get("routes", {})
        _need(type(routes) is dict and len(routes) <= 64, "authority_routes_invalid")
        parsed = {}
        for name, r in routes.items():
            _need(type(name) is str and len(name) <= 64 and _ROUTE.match(name) and type(r) is dict
                  and set(r) <= {"unit", "actors", "per_day", "per_item", "supervised_only", "note"},
                  "authority_route_invalid")
            _need(r.get("unit") in UNITS, "authority_route_unit_invalid")
            actors = r.get("actors", [])
            _need(type(actors) is list and all(a in V2_ACTORS for a in actors) and len(set(actors)) == len(actors),
                  "authority_route_actors_invalid")
            # A test route is exempt from the closure gate because its worker carries no key: only that worker.
            _need(not in_test_family(name) or set(actors) <= set(TEST_ACTORS), "authority_test_route_actor_invalid")
            _need(r.get("supervised_only") is None or type(r.get("supervised_only")) is bool, "authority_route_invalid")
            _need(type(r.get("note", "")) is str and len(r.get("note", "")) <= 1000, "authority_route_invalid")
            per_day, per_item = (None if r.get(k) is None else _amount(r[k], zero=True) for k in ("per_day", "per_item"))
            _need(per_day is None or per_item is None or per_item <= per_day, "authority_route_caps_invalid")
            if doc["environment"] == "prod":           # test values never in production configuration
                _need(not in_test_family(name) and r["unit"] != "test_units" and not set(actors) & set(TEST_ACTORS),
                      "authority_test_route_in_production")
            parsed[name] = Route(r["unit"], tuple(actors), per_day, per_item, r.get("supervised_only"))
        sw = doc.get("sweeper")
        _need(sw is None or (type(sw) is dict and set(sw) == {"sweep_s", "gap_threshold_s"}
                             and all(type(sw[k]) is int for k in sw) and 1 <= sw["sweep_s"] < sw["gap_threshold_s"] <= 3600),
              "authority_sweeper_invalid")
        # Without a sweeper nothing would ever lapse or end at grace: version 2 refuses to run without one (R2.3-b).
        _need(sw is not None or 2 not in protocols, "authority_sweeper_missing")
        across = doc.get("reassign_across_hosts", False)                           # missing: refused (Mac-only)
        _need(type(across) is bool, "authority_reassign_invalid")
        skew = doc.get("clock_skew_s", 0)                                          # missing: 0, an exact window
        _need(type(skew) is int and 0 <= skew <= 60, "authority_clock_skew_invalid")
        return cls(doc["environment"], frozenset(protocols), closure, lease, MappingProxyType(parsed),
                   None if sw is None else Sweeper(sw["sweep_s"], sw["gap_threshold_s"]), across, skew)


def in_test_family(route: str) -> bool:
    """fakework and fakejudge, and their dotted sub-routes (fakework.api, fakework.subscription)."""
    return route in TEST_FAMILY or route.startswith(tuple(f + "." for f in TEST_FAMILY))


def route_refusal(authority: Authority, route: str, actor: str) -> dict | None:
    """Why an unattended item for ``actor`` on ``route`` may not run, or None. Every runtime item is unattended in
    this slice. The gate judges the pair: a route admits only the workers it names, and only the test worker on a
    test route escapes the closure gate."""
    r = authority.routes.get(route)
    if r is None:
        return {"code": "route_unknown"}
    if actor not in r.actors:
        return {"code": "route_actor_mismatch"}
    if r.supervised_only is not False:
        return {"code": "route_supervised_only", "reason": "route_configured"}
    if not (in_test_family(route) and actor in TEST_ACTORS) and authority.launch_closure != "verified":
        return {"code": "route_supervised_only", "reason": "closure_pending"}            # R6.7-l
    if r.per_day is None or r.per_item is None:
        return {"code": "route_cap_missing"}
    return None


def reservation_hold(row_unit, route_unit, reserved, observed, amount, per_day) -> str | None:
    """Why ``amount`` cannot be reserved on today's row, or None. Amounts in different units are never added."""
    if row_unit != route_unit:
        return "route_unit_changed"
    if Decimal(reserved) + Decimal(observed) + amount > per_day:
        return "reservation_cap"
    return None


def _amount(value, *, zero=False) -> Decimal:
    """A finite amount, at most 10^6, with at most four decimals: from a JSON number, or one already read."""
    _need((type(value) in (int, float) and math.isfinite(value)) or (type(value) is Decimal and value.is_finite()),
          "amount_invalid")
    d = value if type(value) is Decimal else Decimal(str(value))
    _need((d >= 0 if zero else d > 0) and d <= 10**6 and d == d.quantize(Decimal("0.0001")), "amount_invalid")
    return d


# ── version 2 value checks (the door checks shapes with these too) ────────────────────────────────────────
def check_capabilities(caps) -> list:
    _need(type(caps) is list and len(caps) <= 64 and all(type(c) is str and _CAP.match(c) for c in caps)
          and len(set(caps)) == len(caps), "capabilities_invalid")
    return list(caps)


def check_facts(facts) -> dict:
    keys = {"host_id", "bundle_digest", "roles", "availability", "grace", "last_gap_s", "announced"}
    _need(type(facts) is dict and set(facts) == keys, "facts_invalid")
    _need(type(facts["host_id"]) is str and _ID.match(facts["host_id"]), "facts_invalid")
    _need(type(facts["bundle_digest"]) is str and _DIGEST.match(facts["bundle_digest"]), "facts_invalid")
    roles = facts["roles"]
    _need(type(roles) is list and roles and all(r in ROLES for r in roles) and len(set(roles)) == len(roles),
          "facts_invalid")
    _need(facts["availability"] in ("always_on", "intermittent"), "facts_invalid")
    _need(type(facts["grace"]) is int and 1 <= facts["grace"] <= 7 * 86400, "facts_invalid")
    gap = facts["last_gap_s"]
    _need(type(gap) in (int, float) and math.isfinite(gap) and 0 <= gap <= 10**8, "facts_invalid")
    _need(facts["announced"] in ("online", "stopping", "stopped"), "facts_invalid")
    return dict(facts)


def check_checkpoint(cp) -> dict | None:
    if cp is None:
        return None
    _need(type(cp) is dict and set(cp) == {"name", "digest", "paths"}, "checkpoint_invalid")
    _need(type(cp["name"]) is str and _ID.match(cp["name"]), "checkpoint_invalid")
    _need(type(cp["digest"]) is str and _DIGEST.match(cp["digest"]), "checkpoint_invalid")
    paths = cp["paths"]
    _need(type(paths) is list and len(paths) <= 200 and all(type(p) is str and _PATH.match(p) for p in paths),
          "checkpoint_invalid")
    return {"name": cp["name"], "digest": cp["digest"], "paths": list(paths)}


def check_route(route) -> str:
    _need(type(route) is str and len(route) <= 64 and _ROUTE.match(route), "route_invalid")
    return route


def check_usage(usage) -> dict:
    """{amount: observed usage in the route's unit, or None when unknown; unit; children_stopped}."""
    _need(type(usage) is dict and set(usage) == {"amount", "unit", "children_stopped"}, "usage_invalid")
    _need(usage["unit"] in UNITS and type(usage["children_stopped"]) is bool, "usage_invalid")
    amount = None if usage["amount"] is None else _amount(usage["amount"], zero=True)
    return {"amount": amount, "unit": usage["unit"], "children_stopped": usage["children_stopped"]}


def check_artifacts(arts) -> list:
    """Input artifacts by digest, as the envelope carries them (envelope.py)."""
    _need(type(arts) is list and len(arts) <= env_mod.MAX_ARTIFACTS and all(
        type(a) is dict and set(a) == {"ref", "sha256"} and type(a["ref"]) is str and _ID.match(a["ref"])
        and type(a["sha256"]) is str and _SHA.match(a["sha256"]) for a in arts), "input_artifacts_invalid")
    return [{"ref": a["ref"], "sha256": a["sha256"]} for a in arts]


def check_stage(stage) -> dict:
    """The host's account at reconcile: its child for this generation (``frozen``: verified and stopped with SIGSTOP,
    so it can be thawed; ``stopped``: none runs; ``unverified``: a process group it cannot verify), and the newest
    checkpoint the authority acknowledged that the host can rebuild (stage file and patch digest verified), or None."""
    _need(type(stage) is dict and set(stage) == {"child", "checkpoint"} and stage["child"] in CHILD_STAGES,
          "stage_invalid")
    cp = stage["checkpoint"]
    _need(cp is None or (type(cp) is dict and set(cp) == {"name", "digest"} and type(cp["name"]) is str
                         and _ID.match(cp["name"]) and type(cp["digest"]) is str and _DIGEST.match(cp["digest"])),
          "stage_invalid")
    return {"child": stage["child"], "checkpoint": None if cp is None else {"name": cp["name"], "digest": cp["digest"]}}


def check_operations(ops) -> list:
    _need(type(ops) is list and len(ops) <= 32 and all(type(o) is str and _UUID.match(o) for o in ops)
          and len(set(ops)) == len(ops), "open_operations_invalid")
    return list(ops)


def _small(value, limit) -> bool:
    try:
        return len(json.dumps(value)) <= limit
    except (TypeError, ValueError):
        return False


def check_effect(eff) -> dict:
    """One effect step (R4.5-a), as the owner host sends it: the step, the item's own key for the effect (the authority
    derives the operation id from it), the effect's grant, the exact request, and per step its receipt lease (dispatch,
    finish) and outcome (finish: verified or failed with the target's answer as evidence, or unknown)."""
    _need(type(eff) is dict and eff.get("op") in EFFECT_OPS, "effect_invalid")
    extra = {"dispatch": {"lease"}, "finish": {"lease", "state", "evidence"}}.get(eff["op"], set())
    _need(set(eff) == {"op", "key", "grant_id", "request"} | extra, "effect_invalid")
    _need(type(eff["key"]) is str and _ID.match(eff["key"]), "effect_invalid")
    _need(type(eff["grant_id"]) is str and _UUID.match(eff["grant_id"]), "effect_invalid")
    req = eff["request"]
    _need(type(req) is dict and set(req) == {"operation", "operation_version", "arguments"}
          and type(req["operation"]) is str and len(req["operation"]) <= 128 and _CAPABILITY.match(req["operation"])
          and not req["operation"].startswith(GRANT_FAMILY)               # an effect never admits work
          and type(req["operation_version"]) is int and 1 <= req["operation_version"] < 2**31
          and type(req["arguments"]) is dict and _small(req["arguments"], 8192), "effect_invalid")
    out = {"op": eff["op"], "key": eff["key"], "grant_id": eff["grant_id"],
           "request": {"operation": req["operation"], "operation_version": req["operation_version"],
                       "arguments": req["arguments"]}}
    if "lease" in extra:
        lease = eff["lease"]
        _need(type(lease) is dict and set(lease) == {"receipt_generation", "token", "mode"}
              and type(lease["receipt_generation"]) is int and 1 <= lease["receipt_generation"] < 2**62
              and type(lease["token"]) is str and _TOKEN.match(lease["token"])
              and lease["mode"] in (("execute",) if eff["op"] == "dispatch" else ("execute", "reconcile")),
              "effect_invalid")
        out["lease"] = dict(lease)
    if eff["op"] == "finish":
        ev = eff["evidence"]
        _need(eff["state"] in ("verified", "failed", "unknown"), "effect_invalid")
        _need((ev is None and eff["state"] == "unknown") or (type(ev) is dict and set(ev) == {"kind", "outcome"}
              and all(type(ev[k]) is str and _WORD.match(ev[k]) for k in ev)), "effect_invalid")
        out.update(state=eff["state"], evidence=None if ev is None else dict(ev))
    return out


def effect_operation_id(request_id, key) -> str:
    """The receipt's operation id, assigned by the authority from the item and the effect's key: never the host's
    choice, never another item's, and the same for every generation, so a resumed attempt that reaches the same effect
    finds its receipt (verified, never sent again; unknown, never retried) instead of making a second one."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mc-delegation-effect:{request_id}:{key}"))


def grace_deadline(since, grace_s, outages) -> tuple:
    """(deadline, lapsed inside an outage) for a lease that ran out at ``since``. Time while the authority itself was
    down or suspended does not count (R5.2-a): a lease that ran out inside an outage gets its grace from the restart
    (X2a), and any later outage that begins before the deadline moves it on by its length."""
    grace = timedelta(seconds=grace_s)
    deadline, down = since + grace, False
    for a, b in sorted(outages):
        if a < since <= b:
            deadline, down = b + grace, True
        elif since < a < deadline:
            deadline += b - a
    return deadline, down


def lapse_cause(authority_down, owner_seen_at, lease_until) -> str:
    """Why a lease ran out without a result: the authority was down (E8); the owner went silent before it did (E3, E6,
    E7); or the owner stayed in contact but stopped renewing it."""
    if authority_down:
        return "authority_down"
    if owner_seen_at is None or owner_seen_at <= lease_until:
        return "owner_contact_lost"
    return "lease_not_renewed"


def parse_at(value) -> datetime:
    """A host's event time: ISO 8601 with seconds and a zone (``Z`` or ±HH:MM), up to six fraction digits. Parsed
    here rather than by datetime.fromisoformat, whose rules differ between Python 3.9 and later."""
    m = _AT.match(value) if type(value) is str and len(value) <= 40 else None
    _need(m is not None, "at_invalid")
    year, month, day, hour, minute, second, fraction, zone = m.groups()
    try:
        tz = timezone.utc if zone == "Z" else timezone(
            (1 if zone[0] == "+" else -1) * timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6])))
        return datetime(int(year), int(month), int(day), int(hour), int(minute), int(second),
                        int((fraction or "0").ljust(6, "0")), tzinfo=tz)
    except ValueError:
        raise AuthorityRefused("at_invalid") from None


def item_subject_digest(*, actor, scope, repository, ref, brief, route, amount, required_capabilities=(),
                        input_artifacts=(), card_id=None, step=None) -> str:
    """The digest an owner's grant names (one of its argument digests) to admit an item: the work itself, not the
    item's id, so a grant of k operations admits k items of the same work. A card step's template digest takes its
    place with S1-C."""
    doc = {"actor": actor, "scope": scope, "repository": repository, "ref": ref, "brief": brief, "route": route,
           "amount": format(_amount(amount).quantize(Decimal("0.0001")), "f"),
           "required_capabilities": sorted(required_capabilities), "input_artifacts": list(input_artifacts),
           "card_id": card_id, "step": step}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def _row_subject(row) -> str | None:
    """The digest the item's grant must name: a card step's template digest (recomputed from its columns, R4.3-c),
    otherwise the work's subject digest, input artifacts included."""
    if ((row.get("facts") or {}).get("card") or {}).get("on_card"):
        return card_mod.row_step_digest(row)
    try:
        return item_subject_digest(actor=row["actor"], scope=row["scope"], repository=row["repository"], ref=row["ref"],
                                   brief=row["brief"], route=row["route"],
                                   amount=(row["reservation"] or {}).get("requested"),
                                   required_capabilities=row["required_capabilities"] or [],
                                   input_artifacts=row["input_artifacts"] or [], card_id=row["card_id"],
                                   step=row["step"])
    except AuthorityRefused:
        return None


def _uuid_text(value, code) -> str:
    _need(type(value) is str and _UUID.match(value), code)
    return value


def _once(*parts) -> str:
    """The key of a transition the authority records only once, however often it is found."""
    return ":".join(map(str, parts))


def _iso(value):
    return value.astimezone(timezone.utc).isoformat() if isinstance(value, datetime) else value


# The queued version 2 items of this principal and environment, each with the reason it must wait, if any. An item
# waits only when its (route, actor) pair is open (``pairs``) and its route is closed by unknown usage, today's row is
# in another unit, or today's budget can't take its amount. Any other item is claimable, or is refused outright, so a
# claim never spends its turns on items that can only wait (R8.3: other routes keep moving). An item reassigned to a
# named runner (runner_id set) is that runner's alone, through _hand_over, and is never anyone's candidate.
_WAITING_SQL = """WITH pairs AS (SELECT * FROM jsonb_to_recordset(%(pairs)s::jsonb)
                              AS p(route text, actor text, unit text, per_day numeric, per_item numeric)),
  today AS (SELECT route, unit, reserved + observed AS used FROM guild.mc_delegation_reservations
            WHERE environment = %(env)s AND day = (clock_timestamp() AT TIME ZONE 'America/Chicago')::date),
  closed AS (SELECT DISTINCT route FROM guild.mc_delegation_reservations
             WHERE environment = %(env)s AND usage_hold IS NOT NULL),
  w AS (SELECT d.request_id, CASE
          WHEN p.route IS NULL OR jsonb_typeof(d.reservation -> 'requested') IS DISTINCT FROM 'number' THEN NULL
          WHEN (d.reservation ->> 'requested')::numeric > p.per_item THEN NULL
          WHEN d.route IN (SELECT route FROM closed) THEN 'usage_unknown'
          WHEN t.route IS NULL THEN NULL
          WHEN t.unit <> p.unit THEN 'route_unit_changed'
          WHEN t.used + (d.reservation ->> 'requested')::numeric > p.per_day THEN 'reservation_cap'
        END AS wait
        FROM guild.mc_delegations d
        LEFT JOIN pairs p ON p.route = d.route AND p.actor = d.actor
        LEFT JOIN today t ON t.route = d.route
        WHERE d.state = 'queued' AND d.route IS NOT NULL AND d.principal_id = %(principal)s
          AND d.environment = %(env)s AND d.runner_id IS NULL)"""
# Each waiting item's reason, written where it changed (and cleared when it no longer waits); rows another claim is
# judging right now are skipped, so this never waits on a lock.
_MARK_SQL = """,
  chg AS (SELECT d.request_id, w.wait FROM guild.mc_delegations d JOIN w ON w.request_id = d.request_id
          WHERE d.state = 'queued' AND (d.reservation ->> 'held') IS DISTINCT FROM w.wait
          ORDER BY d.request_id FOR UPDATE OF d SKIP LOCKED)
UPDATE guild.mc_delegations d SET reservation = CASE WHEN chg.wait IS NULL THEN d.reservation - 'held'
                                                ELSE d.reservation || jsonb_build_object('held', chg.wait) END
FROM chg WHERE d.request_id = chg.request_id
RETURNING d.request_id, d.generation, d.route, chg.wait"""
# The oldest item this host can run that doesn't wait. state is checked again here, on the row as locked.
_CANDIDATE_SQL = """
SELECT d.*, clock_timestamp() AS now FROM guild.mc_delegations d JOIN w ON w.request_id = d.request_id
WHERE w.wait IS NULL AND d.state = 'queued' AND d.route IS NOT NULL AND NOT d.cancel_requested
  AND d.deadline > clock_timestamp() AND NOT (d.request_id = ANY(%(seen)s::uuid[]))
  AND (d.required_capabilities || jsonb_build_array('actor:' || d.actor, 'scope:' || d.scope,
                                                    'repo:' || d.repository)) <@ %(caps)s::jsonb
ORDER BY d.created_at LIMIT 1 FOR UPDATE OF d SKIP LOCKED"""


class DispatcherStore:
    def __init__(self, connect, *, principal: str, environment: str, authority: Authority | None = None, grants=None,
                 cards=None):
        """``authority`` (Authority.load) enables version 2 when its protocols say so; a version 2 claim then reads
        each item's grant through ``grants`` (core.minimoi_actions.grants.GrantStore with the delegation catalogue,
        delegate.card's row among it: cards.grant_rows()). ``cards`` is the card class catalogue (cards.Catalogue);
        a version 2 store without one reads the one shipped beside this file (card_classes.json)."""
        self._connect, self.principal, self.environment = connect, principal, environment
        self._v2 = authority is not None and authority.speaks_v2
        if authority is not None and authority.environment != environment:
            raise ValueError("authority_environment_mismatch")
        if self._v2:
            if tuple(sys.version_info[:2]) < MIN_PYTHON_V2:
                raise RuntimeError("authority_requires_python_3_10")   # before importing what cannot load below it
            from core.minimoi_actions.grants import GrantStore
            from core.minimoi_actions.receipts import ReceiptStore
            if type(grants) is not GrantStore:
                raise ValueError("authority_grant_store_missing")     # a version 2 claim always reads the grant
            if authority.sweeper is None:
                raise ValueError("authority_sweeper_missing")         # nothing would ever lapse or end at grace
            self._receipts = ReceiptStore()
            if cards is None:
                cards = card_mod.Catalogue.load()
            if type(cards) is not card_mod.Catalogue:
                raise ValueError("authority_card_catalogue_invalid")
        self.authority, self._grants, self._cards = authority, grants, cards
        # The authority's liveness and the sweeper's heartbeat are rows among the contacts. A '/' is outside every id a
        # host can send (_ID), so no host can write either, and neither can be made to look alive.
        self._alive_id = f"authority/{environment}/{principal}"
        self._sweeper_id = f"sweeper/{environment}/{principal}"

    @property
    def sweeps(self) -> bool:
        """A sweeper runs for this authority (every version 2 one): it, not relay.status, writes lapse transitions."""
        return self._v2 and self.authority.sweeper is not None

    @contextmanager
    def _session(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    @contextmanager
    def _txn(conn):
        from psycopg2.extras import RealDictCursor
        with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur

    @contextmanager
    def _tx(self):
        with self._session() as conn, self._txn(conn) as cur:
            yield cur

    def _protocol(self, protocol, extra) -> bool:
        """True for a version 2 call. A version 1 call carries none of version 2's fields."""
        _need(type(protocol) is int and protocol in (1, 2), "protocol_invalid")
        if protocol == 1:
            _need(all(v is None for v in extra), "protocol_fields_invalid")
            return False
        _need(self._v2, "protocol_not_enabled")
        return True

    # ── portal side (the delegation relay) ─────────────────────────────────
    def enqueue(self, *, job_id, actor, scope, repository, ref, brief, cost_cap_usd, deadline_s, route=None,
                amount=None, grant_id=None, required_capabilities=None, input_artifacts=None, card_id=None, step=None,
                created_by=None) -> str:
        """'accepted' or 'duplicate' (this job id is already known: never queued twice). With ``route`` it is a
        version 2 item, written by the authority: ``queued`` when one of its grant's operations admits it, else
        ``proposed`` (it waits on a decision) with the reason in result_detail."""
        if route is not None:
            return self._enqueue_v2(job_id, actor, scope, repository, ref, brief, cost_cap_usd, deadline_s, route,
                                    amount, grant_id, required_capabilities, input_artifacts, card_id, step, created_by)
        _need(all(v is None for v in (amount, grant_id, required_capabilities, input_artifacts, card_id, step,
                                      created_by)), "protocol_fields_invalid")
        with self._tx() as cur:
            cur.execute("""INSERT INTO guild.mc_delegations (request_id, job_id, principal_id, environment, actor, scope,
                repository, ref, brief, cost_cap_usd, deadline)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, clock_timestamp() + %s * interval '1 second')
                ON CONFLICT (job_id) DO NOTHING RETURNING request_id""",
                (request_id_for(job_id), job_id, self.principal, self.environment, actor, scope, repository, ref,
                 brief, cost_cap_usd, int(deadline_s)))
            return "accepted" if cur.fetchone() else "duplicate"

    def _enqueue_v2(self, job_id, actor, scope, repository, ref, brief, cost_cap_usd, deadline_s, route, amount,
                    grant_id, required_capabilities, input_artifacts, card_id, step, created_by) -> str:
        _need(self._v2, "protocol_not_enabled")
        item = self._v2_item(job_id, actor, scope, repository, ref, brief, cost_cap_usd, deadline_s, route, amount,
                             grant_id, required_capabilities, input_artifacts, card_id, step, created_by)
        with self._tx() as cur:
            return "duplicate" if self._write(cur, item, subject=item["subject"]) is None else "accepted"

    def _v2_item(self, job_id, actor, scope, repository, ref, brief, cost_cap_usd, deadline_s, route, amount, grant_id,
                 required_capabilities, input_artifacts, card_id, step, created_by) -> dict:
        """A version 2 item's values, checked, with the work's subject digest: what enqueue writes, and what a card
        step compiled by the authority writes the same way."""
        cfg = self.authority.routes.get(route) if type(route) is str else None
        _need(cfg is not None, "route_unknown")
        _need(actor in V2_ACTORS and scope in env_mod.SCOPES, "item_invalid")
        _need(type(job_id) is str and _ID.match(job_id), "job_id_invalid")
        _need(actor in cfg.actors, "route_actor_mismatch")           # a route runs only the workers it names
        amount = _amount(amount)                          # the item's own cap, in its route's unit (§8.1 record 1)
        _need(cfg.per_item is not None, "route_cap_missing")
        _need(amount <= cfg.per_item, "item_cap_exceeded")
        caps = check_capabilities(list(required_capabilities or []))
        arts = check_artifacts(list(input_artifacts or []))
        _need(grant_id is None or (type(grant_id) is str and _UUID.match(grant_id)), "grant_id_invalid")
        _need(all(v is None or (type(v) is str and _ID.match(v)) for v in (card_id, step)), "card_invalid")
        created_by = created_by or "owner"
        _need(created_by in ("owner", "authority"), "created_by_invalid")
        subject = item_subject_digest(actor=actor, scope=scope, repository=repository, ref=ref, brief=brief,
                                      route=route, amount=amount, required_capabilities=caps, input_artifacts=arts,
                                      card_id=card_id, step=step)
        return {"job_id": job_id, "actor": actor, "scope": scope, "repository": repository, "ref": ref, "brief": brief,
                "cost_cap_usd": cost_cap_usd, "deadline_s": int(deadline_s), "route": route, "amount": amount,
                "grant_id": grant_id, "caps": caps, "arts": arts, "card_id": card_id, "step": step,
                "created_by": created_by, "subject": subject}

    def _write(self, cur, item, *, subject, facts=None, admit=True, decision=None) -> dict | None:
        """Insert one version 2 item (``_v2_item``) and admit it in this same transaction. §4.2, R4.3-c: queued means
        admitted under a grant, so admission uses one of its operations (GrantStore.use_for_item for ``subject``).
        Without a grant, with none left, with one that doesn't cover it, or not to be admitted here (``admit``
        false, with the decision's code), it is written proposed with the reason, and, given ``decision`` (its class
        and whether it arrives solo, R7.4-c), one decision is queued for it. None when the job id is already known:
        a retry of the same item uses nothing. ``facts`` is the authority's record of a card step; a card step's
        operation is bound to that step (_use)."""
        cur.execute("""INSERT INTO guild.mc_delegations (request_id, job_id, principal_id, environment, actor, scope,
            repository, ref, brief, cost_cap_usd, deadline, state, route, reservation, grant_id,
            required_capabilities, input_artifacts, card_id, step, created_by, facts)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, clock_timestamp() + %s * interval '1 second', 'proposed',%s,
                    %s::jsonb,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s::jsonb)
            ON CONFLICT (job_id) DO NOTHING RETURNING request_id""",
            (request_id_for(item["job_id"]), item["job_id"], self.principal, self.environment, item["actor"],
             item["scope"], item["repository"], item["ref"], item["brief"], item["cost_cap_usd"], item["deadline_s"],
             item["route"], json.dumps({"requested": float(item["amount"])}), item["grant_id"],
             json.dumps(item["caps"]), json.dumps(item["arts"]), item["card_id"], item["step"], item["created_by"],
             None if facts is None else json.dumps(facts)))
        inserted = cur.fetchone()
        if inserted is None:
            return None                                     # a retry of the same item uses no grant operation
        rid, gid = inserted["request_id"], item["grant_id"]
        if not admit:
            code = decision["code"]
        else:
            on_card = ((facts or {}).get("card") or {}).get("on_card")
            code = "grant_missing" if gid is None else self._use(
                cur, gid, subject, tuple(w for w in (item["card_id"], item["job_id"]) if w),
                step={"item_id": str(rid), "card_id": item["card_id"], "step": item["step"]} if on_card else None)
        state = "proposed" if code else "queued"
        decision_id = str(uuid.uuid4()) if code and decision is not None else None
        detail = None if not code else {"code": code, **({"decision_id": decision_id, "class": decision["class"]}
                                                         if decision_id else {})}
        cur.execute("UPDATE guild.mc_delegations SET state=%s, result_detail=%s::jsonb WHERE request_id=%s",
                    (state, None if detail is None else json.dumps(detail), rid))
        self._event(cur, f"item.{state}", item=rid, generation=0,
                    source="owner" if item["created_by"] == "owner" else "authority",
                    body={"route": item["route"], "requested": float(item["amount"]), "grant_id": gid,
                          "card_id": item["card_id"], "step": item["step"], "required_capabilities": item["caps"],
                          "input_artifacts": item["arts"], "created_by": item["created_by"], "subject_digest": subject,
                          **({"code": code} if code else {}), **({"card": facts["card"]} if facts else {})})
        if decision_id is not None:
            # One decision, waiting on the owner's admission (R7.4-b; S1-G gives it its D-code and notice).
            self._event(cur, "decision.queued", item=rid, generation=0, once=_once("admission", rid),
                        event_id=decision_id, body={"class": decision["class"], "solo": decision["solo"],
                                                    "reason": code, "card_id": item["card_id"], "step": item["step"],
                                                    "subject_digest": subject})
        return {"request_id": str(rid), "job_id": item["job_id"], "state": state, "code": code,
                "decision_id": decision_id}

    def by_job(self, job_id) -> dict | None:
        with self._tx() as cur:
            cur.execute("SELECT *, clock_timestamp() AS now FROM guild.mc_delegations WHERE job_id=%s", (job_id,))
            return cur.fetchone()

    def reap_lapsed(self, job_id) -> bool:
        """A claim whose runner stopped renewing for a full extra lease is ended as interrupted (runner_lost), so
        the job and its Stop never hang on a dead runner. A late result for the same generation still records.
        A version 2 item is never reaped here: it lapses with its owner unchanged (spec §5.1, the sweeper's)."""
        with self._tx() as cur:
            cur.execute("""UPDATE guild.mc_delegations SET state='interrupted', finished_at=clock_timestamp(),
                result_detail='{"code": "runner_lost"}'::jsonb, lease_until=NULL
                WHERE job_id=%s AND state IN ('claimed','running') AND result_event_id IS NULL
                  AND lease_until < clock_timestamp() - %s * interval '1 second'"""
                        + (" AND route IS NULL" if self._v2 else ""), (job_id, LEASE_SECONDS))
            return cur.rowcount == 1

    def stop(self, job_id) -> str:
        """'confirmed' (never started), 'requested' (the runner stops at its next check), 'not_running', 'not_found'."""
        with self._tx() as cur:
            cur.execute("SELECT " + ("*" if self._v2 else "state") + " FROM guild.mc_delegations WHERE job_id=%s FOR UPDATE",
                        (job_id,))
            row = cur.fetchone()
            if row is None:
                return "not_found"
            audited = self._v2 and row["route"] is not None
            if row["state"] == "queued" and audited and "reserved" in (row["reservation"] or {}):
                # A reassigned item waiting for its new owner: it never ran there, and holds its reservation.
                self._close(cur, row, "cancelled", {"code": "owner_stop"}, children_stopped=True, source="owner")
                return "confirmed"
            if row["state"] == "queued":
                cur.execute("""UPDATE guild.mc_delegations SET state='cancelled', cancel_requested=TRUE,
                    finished_at=clock_timestamp() WHERE job_id=%s""", (job_id,))
                if audited:      # never claimed, so nothing was reserved
                    self._event(cur, "item.cancelled", item=row["request_id"], generation=row["generation"],
                                source="owner", body={"code": "owner_stop"})
                    self._card_ended(cur, row, "cancelled", "owner_stop")
                return "confirmed"
            if row["state"] in ("claimed", "running"):
                cur.execute("UPDATE guild.mc_delegations SET cancel_requested=TRUE WHERE job_id=%s", (job_id,))
                if audited:
                    self._event(cur, "item.cancel_requested", item=row["request_id"], generation=row["generation"],
                                once=_once("cancel_requested", row["request_id"], row["generation"]),
                                source="owner", body={})
                return "requested"
            return "not_running"

    # ── runner protocol ────────────────────────────────────────────────────
    def heartbeat(self, runner_id, state, capabilities, *, protocol=1, facts=None):
        if self._protocol(protocol, (facts,)):
            return self._heartbeat_v2(runner_id, state, capabilities, facts)
        with self._tx() as cur:
            # With version 2 on, the row also says the runner last spoke version 1 (so it claims nothing as a host).
            cur.execute("""INSERT INTO guild.mc_delegation_runners (runner_id, state, capabilities, seen_at)
                VALUES (%s,%s,%s::jsonb, clock_timestamp()) ON CONFLICT (runner_id) DO UPDATE
                SET state=EXCLUDED.state, capabilities=EXCLUDED.capabilities, seen_at=EXCLUDED.seen_at"""
                        + (", protocol=1" if self._v2 else ""), (runner_id, state, json.dumps(capabilities)))

    def claim(self, runner_id, capabilities, *, protocol=1):
        if self._protocol(protocol, ()):
            return self._claim_v2(runner_id, capabilities)
        actors = list(capabilities.get("cost_enforced_actors") or [])
        scopes = list(capabilities.get("scopes") or [])
        repos = list(capabilities.get("repositories") or [])
        if not actors or not scopes or not repos:
            return None
        with self._tx() as cur:
            cur.execute("""SELECT * FROM guild.mc_delegations
                WHERE state='queued' AND NOT cancel_requested AND principal_id=%s AND environment=%s
                  AND actor = ANY(%s) AND scope = ANY(%s) AND repository = ANY(%s) AND deadline > clock_timestamp()"""
                        + (" AND route IS NULL" if self._v2 else "") + """
                ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED""",
                (self.principal, self.environment, actors, scopes, repos))
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute("""UPDATE guild.mc_delegations SET state='claimed', generation=generation+1, runner_id=%s,
                lease_until=clock_timestamp() + %s * interval '1 second', heartbeat_at=clock_timestamp()
                WHERE request_id=%s RETURNING generation""", (runner_id, LEASE_SECONDS, row["request_id"]))
            generation = cur.fetchone()["generation"]
        return self._envelope(row, generation, [])

    @staticmethod
    def _envelope(row, generation, input_artifacts) -> dict:
        deadline = row["deadline"].astimezone(timezone.utc).isoformat()
        return {"schema": env_mod.SCHEMA, "request_id": str(row["request_id"]), "work_id": row["job_id"],
                "generation": generation, "actor": row["actor"], "principal_id": row["principal_id"],
                "environment": row["environment"], "scope": row["scope"], "repository": row["repository"],
                "ref": row["ref"], "brief": row["brief"], "input_artifacts": input_artifacts, "deadline": deadline,
                "cost_cap_usd": float(row["cost_cap_usd"])}

    def _current(self, cur, request_id, generation, *, lock=True):
        cur.execute("SELECT *, clock_timestamp() AS now FROM guild.mc_delegations WHERE request_id=%s"
                    + (" FOR UPDATE" if lock else ""), (str(request_id),))
        row = cur.fetchone()
        if row is None or row["generation"] != generation or row["state"] not in ("claimed", "running") \
                or row["lease_until"] is None or row["lease_until"] <= row["now"]:
            return None
        if self._v2 and row["route"] is not None:
            return None                    # a version 2 item answers only version 2 calls from its owner
        return row

    def progress(self, request_id, generation, phase, *, protocol=1, runner_id=None, event_id=None, at=None,
                 checkpoint=None):
        if self._protocol(protocol, (runner_id, event_id, at, checkpoint)):
            return self._progress_v2(request_id, generation, phase, runner_id, event_id, at, checkpoint)
        with self._tx() as cur:
            if self._current(cur, request_id, generation) is not None:
                cur.execute("""UPDATE guild.mc_delegations SET state='running',
                    started_at=COALESCE(started_at, clock_timestamp()), heartbeat_at=clock_timestamp()
                    WHERE request_id=%s""", (str(request_id),))

    def cancel_requested(self, request_id, generation, *, protocol=1) -> bool:
        v2 = self._protocol(protocol, ())
        with self._tx() as cur:
            if v2:
                row = self._item(cur, _uuid_text(request_id, "request_id_invalid"), lock=False)
                if row is None or row["route"] is None or row["generation"] != generation \
                        or row["state"] not in ("claimed", "running") or row["lease_until"] is None \
                        or row["lease_until"] <= row["now"]:
                    row = None
            else:
                row = self._current(cur, request_id, generation, lock=False)
            # Only the owner's Stop reads as cancelled. A lost claim is found by renew() and ends lease_lost, so it is
            # never recorded as a Stop that was not pressed (independent review P3-d).
            return row is not None and bool(row["cancel_requested"])

    def renew(self, request_id, generation, *, protocol=1, runner_id=None) -> bool:
        if self._protocol(protocol, (runner_id,)):
            return self._renew_v2(request_id, generation, runner_id)
        with self._tx() as cur:
            row = self._current(cur, request_id, generation)
            if row is None or row["cancel_requested"]:
                return False
            cur.execute("""UPDATE guild.mc_delegations SET lease_until=clock_timestamp() + %s * interval '1 second',
                heartbeat_at=clock_timestamp() WHERE request_id=%s""", (LEASE_SECONDS, str(request_id)))
            return True

    def result(self, request_id, generation, *, event_id, status, artifacts, detail, output_text=None, protocol=1,
               runner_id=None, at=None, route=None, usage=None) -> dict:
        if self._protocol(protocol, (runner_id, at, route, usage)):
            return self._result_v2(request_id, generation, runner_id, event_id, at, status, artifacts, detail,
                                   output_text, route, usage)
        if status not in RESULT_STATES:
            return {"state": "refused", "event_id": event_id, "code": "status_invalid"}
        with self._tx() as cur:
            cur.execute("SELECT * FROM guild.mc_delegations WHERE request_id=%s FOR UPDATE", (str(request_id),))
            row = cur.fetchone()
            if row is None:
                return {"state": "refused", "event_id": event_id, "code": "unknown_request"}
            if self._v2 and row["route"] is not None:
                return {"state": "refused", "event_id": event_id, "code": "protocol_mismatch"}
            if row["state"] == "interrupted" and (row["result_detail"] or {}).get("code") == "runner_lost":
                # The owner was already told this job did not complete; a late result never rewrites that.
                return {"state": "already_applied", "event_id": event_id, "code": "job_already_ended"}
            if row["result_event_id"] is not None:
                same = str(row["result_event_id"]) == str(event_id)
                return {"state": "already_applied" if same else "refused", "event_id": event_id,
                        **({} if same else {"code": "different_result_already_recorded"})}
            # Refusals may arrive without a claim (an envelope the runner rejected). Anything else needs the
            # current generation; a stale worker never completes someone else's claim.
            if row["generation"] != generation and (status != "refused" or row["state"] not in ("queued", "claimed")):
                return {"state": "refused", "event_id": event_id, "code": "stale_generation"}
            if row["cancel_requested"] and status == "completed":
                status = "cancelled"                       # Stop was pressed: the result is kept, not claimed as done
            text = output_text[:OUTPUT_MAX] if isinstance(output_text, str) else None
            cur.execute("""UPDATE guild.mc_delegations SET state=%s, result_event_id=%s, result_detail=%s::jsonb,
                artifacts=%s::jsonb, output_text=%s, finished_at=clock_timestamp(), lease_until=NULL
                WHERE request_id=%s""", (status, str(event_id), json.dumps(detail or {}), json.dumps(artifacts or []),
                                         text, str(request_id)))
            return {"state": "accepted", "event_id": event_id}

    # ── version 2: the owner's acts at the authority ──────────────────────────
    def resolve_usage_hold(self, route, *, note, by) -> bool:
        """Lift the unknown-usage hold on ``route``, on the record (as connection.resolve_usage_hold): who and what was
        reconciled go into the audit, then the hold is cleared. The unknown usage itself stays unknown: those items'
        reserved amounts stay counted on their day, never released as zero. True when a hold was lifted."""
        _need(self._v2, "protocol_not_enabled")
        check_route(route)
        _need(type(by) is str and _ID.match(by), "owner_invalid")
        if not isinstance(note, str) or not note.strip():
            raise AuthorityRefused("say_what_was_reconciled")
        with self._tx() as cur:
            self._lock_route(cur, route)
            cur.execute("""SELECT day, usage_hold FROM guild.mc_delegation_reservations
                WHERE environment=%s AND route=%s AND usage_hold IS NOT NULL ORDER BY day FOR UPDATE""",
                        (self.environment, route))
            lifted = cur.fetchall()
            if not lifted:
                return False
            cur.execute("""UPDATE guild.mc_delegation_reservations SET usage_hold=NULL, updated_at=clock_timestamp()
                WHERE environment=%s AND route=%s AND usage_hold IS NOT NULL""", (self.environment, route))
            self._event(cur, "reservation.hold_resolved", source="owner", body={
                "route": route, "by": by, "note": note.strip()[:300],
                "holds": [{"day": r["day"].isoformat(), **r["usage_hold"]} for r in lifted]})
            return True

    # ── version 2: the calls ───────────────────────────────────────────────
    def _event(self, cur, kind, *, item=None, generation=None, host_id=None, source="authority", body=None,
               once=None, event_id=None) -> bool:
        """One audit event the authority writes (R7.6-d), under a random id, so no host can predict or take it (the
        caller may make that id itself, to cite it). ``once`` names a transition recorded only once: False when it
        already is. Anything else that keeps the row out raises, so no transition commits without its audit."""
        _need(once is None or source != "host", "event_once_invalid")
        cur.execute("""INSERT INTO guild.mc_delegation_events (event_id, schema, kind, principal_id, environment,
            item_id, generation, host_id, source, at, body, once_key)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, clock_timestamp(), %s::jsonb, %s)
            ON CONFLICT DO NOTHING RETURNING event_id""",
            (event_id or str(uuid.uuid4()), EVENT_SCHEMA, kind, self.principal, self.environment,
             None if item is None else str(item), generation, host_id, source, json.dumps(body or {}), once))
        if cur.fetchone() is not None:
            return True
        if once is not None:
            cur.execute("""SELECT 1 FROM guild.mc_delegation_events WHERE principal_id=%s AND environment=%s
                AND once_key=%s""", (self.principal, self.environment, once))
            if cur.fetchone() is not None:
                return False
        raise RuntimeError("audit_event_not_written")

    def _host_event(self, cur, kind, event_id, *, item, generation, host_id, at, body) -> str | None:
        """A host's own event, under the id it sent (the replay log): None once written, 'already_applied' when that
        exact event is already recorded, 'event_id_conflict' when the id names anything else."""
        cur.execute("""INSERT INTO guild.mc_delegation_events (event_id, schema, kind, principal_id, environment,
            item_id, generation, host_id, source, at, body)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'host',%s,%s::jsonb) ON CONFLICT DO NOTHING RETURNING event_id""",
            (event_id, EVENT_SCHEMA, kind, self.principal, self.environment, str(item), generation, host_id, at,
             json.dumps(body)))
        if cur.fetchone() is not None:
            return None
        return self._replay(cur, event_id, kind, item, generation, at, body) or "event_id_conflict"

    def _replay(self, cur, event_id, kind, item, generation, at, body) -> str | None:
        """None while ``event_id`` is unused; 'already_applied' when it records exactly this host event (DUP-5);
        otherwise 'event_id_conflict'."""
        cur.execute("""SELECT source, kind, principal_id, environment, item_id, generation, at, body
            FROM guild.mc_delegation_events WHERE event_id=%s""", (event_id,))
        found = cur.fetchone()
        if found is None:
            return None
        same = (found["source"] == "host" and found["kind"] == kind and found["principal_id"] == self.principal
                and found["environment"] == self.environment and str(found["item_id"]) == str(item)
                and found["generation"] == generation and found["at"] == at and found["body"] == body)
        return "already_applied" if same else "event_id_conflict"

    def _item(self, cur, request_id, *, lock=True, skip=False):
        cur.execute("""SELECT *, clock_timestamp() AS now FROM guild.mc_delegations
            WHERE request_id=%s AND principal_id=%s AND environment=%s"""
                    + (" FOR UPDATE" + (" SKIP LOCKED" if skip else "") if lock else ""),
                    (str(request_id), self.principal, self.environment))
        return cur.fetchone()

    @staticmethod
    def _host_id(cur, runner_id):
        cur.execute("SELECT host_id FROM guild.mc_delegation_runners WHERE runner_id=%s", (runner_id,))
        row = cur.fetchone()
        return row["host_id"] if row else None

    def _lock_route(self, cur, route):
        cur.execute("SELECT pg_advisory_xact_lock(%s, hashtext(%s))", (_ROUTE_LOCK, f"{self.environment}:{route}"))

    def _heartbeat_v2(self, runner_id, state, capabilities, facts) -> dict:
        _need(type(runner_id) is str and _ID.match(runner_id), "runner_id_invalid")
        _need(type(state) is str and _WORD.match(state), "state_invalid")
        caps, facts = check_capabilities(capabilities), check_facts(facts)
        with self._tx() as cur:
            cur.execute("SELECT * FROM guild.mc_delegation_runners WHERE runner_id=%s FOR UPDATE", (runner_id,))
            old = cur.fetchone()
            # The owner identity is the runner id: another host, or another environment, never takes it over.
            _need(old is None or old["host_id"] in (None, facts["host_id"]), "runner_host_mismatch")
            _need(old is None or old["environment"] in (None, self.environment), "runner_environment_mismatch")
            cur.execute("""INSERT INTO guild.mc_delegation_runners AS r (runner_id, state, capabilities, seen_at, host_id,
                environment, roles, bundle_digest, expected_availability, unreachable_grace_s, announced, announced_at,
                last_gap_s, protocol)
                VALUES (%s,%s,%s::jsonb, clock_timestamp(), %s,%s,%s::jsonb,%s,%s,%s,%s, clock_timestamp(),%s, 2)
                ON CONFLICT (runner_id) DO UPDATE SET state=EXCLUDED.state, capabilities=EXCLUDED.capabilities,
                  seen_at=EXCLUDED.seen_at, host_id=EXCLUDED.host_id, environment=EXCLUDED.environment,
                  roles=EXCLUDED.roles, bundle_digest=EXCLUDED.bundle_digest,
                  expected_availability=EXCLUDED.expected_availability, unreachable_grace_s=EXCLUDED.unreachable_grace_s,
                  announced_at=CASE WHEN r.announced IS DISTINCT FROM EXCLUDED.announced THEN EXCLUDED.announced_at
                                    ELSE r.announced_at END,
                  announced=EXCLUDED.announced, last_gap_s=EXCLUDED.last_gap_s, protocol=2""",
                (runner_id, state, json.dumps(caps), facts["host_id"], self.environment, json.dumps(facts["roles"]),
                 facts["bundle_digest"], facts["availability"], facts["grace"], facts["announced"],
                 float(facts["last_gap_s"])))
            before = {} if old is None else {
                "host_id": old["host_id"], "bundle_digest": old["bundle_digest"], "roles": old["roles"],
                "availability": old["expected_availability"], "grace": old["unreachable_grace_s"],
                "last_gap_s": old["last_gap_s"], "announced": old["announced"]}
            changed = sorted(k for k in facts if before.get(k) != facts[k]
                             and not (k == "last_gap_s" and before.get(k) is not None
                                      and float(before[k]) == float(facts[k])))
            if changed or old is None or old["protocol"] != 2:
                self._event(cur, "host.facts", host_id=facts["host_id"], source="host",
                            body={"runner_id": runner_id, "changed": changed, **facts})
            if facts["announced"] == "stopped":
                # The owner stopped its children at a checkpoint and said so (G4; E2, E9, E14): each item it holds is
                # paused, owner unchanged, its lease ended now, and it reconciles on its return. Said here, not at a
                # sweep, so a restart quicker than a sweep still shows it.
                cur.execute("""SELECT *, clock_timestamp() AS now FROM guild.mc_delegations
                    WHERE principal_id=%s AND environment=%s AND runner_id=%s AND route IS NOT NULL
                      AND state IN ('claimed','running') ORDER BY request_id FOR UPDATE""",
                            (self.principal, self.environment, runner_id))
                for row in cur.fetchall():
                    self._lapse(cur, row, row["now"], paused=True)
            # S1-F: the census batch this heartbeat carries is applied here, in the same transaction, as host events
            # (kind census.*) under their own ids, each applied once (_host_event).
        return {"state": "recorded"}

    def _unreconciled(self, cur, runner_id) -> list:
        """Items this runner owns that it must reconcile before it may take new work (§5.5 step 6): lapsed or paused
        by record, or claimed/running with the lease already past by the clock. An item it reconciled to ``unknown``
        waits on the owner, not on the host, and does not count."""
        cur.execute("""SELECT request_id, generation FROM guild.mc_delegations
            WHERE principal_id=%s AND environment=%s AND runner_id=%s
              AND (state IN ('lapsed','paused')
                   OR (state IN ('claimed','running') AND lease_until <= clock_timestamp()))
            ORDER BY request_id""", (self.principal, self.environment, runner_id))
        return [f"{r['request_id']}:{r['generation']}" for r in cur.fetchall()]

    def _withheld(self, cur, runner_id, host_id) -> bool:
        stuck = self._unreconciled(cur, runner_id)
        if stuck:
            self._event(cur, "host.claim_withheld", host_id=host_id,
                        once=_once("withheld", runner_id, hashlib.sha256(" ".join(stuck).encode()).hexdigest()),
                        body={"runner_id": runner_id, "code": "unreconciled_items", "items": stuck})
        return bool(stuck)

    def _open_pairs(self) -> str:
        """The (route, actor) pairs the gate admits, with their unit and caps, for the claim's SQL."""
        return json.dumps([{"route": name, "actor": actor, "unit": r.unit, "per_day": str(r.per_day),
                            "per_item": str(r.per_item)}
                           for name, r in self.authority.routes.items() for actor in r.actors
                           if route_refusal(self.authority, name, actor) is None])

    def _mark_waiting(self, cur, open_pairs):
        """Write each waiting item's reason to it (reservation.held), only where it changed, and audit the change."""
        cur.execute(_WAITING_SQL + _MARK_SQL, {"pairs": open_pairs, "env": self.environment, "principal": self.principal})
        for r in cur.fetchall():
            self._event(cur, "item.held", item=r["request_id"], generation=r["generation"],
                        body={"code": r["wait"], "route": r["route"]})

    def _claim_v2(self, runner_id, capabilities):
        _need(type(runner_id) is str and _ID.match(runner_id), "runner_id_invalid")
        caps = check_capabilities(capabilities)
        open_pairs = self._open_pairs()
        with self._session() as conn:                   # one connection for the whole poll
            with self._txn(conn) as cur:
                cur.execute("SELECT * FROM guild.mc_delegation_runners WHERE runner_id=%s", (runner_id,))
                host = cur.fetchone()
                # Work goes only to a host whose facts the authority holds: a worker, speaking version 2, online.
                if (host is None or host["protocol"] != 2 or host["environment"] != self.environment
                        or "worker" not in (host["roles"] or []) or host["announced"] != "online"):
                    return None
                self._mark_waiting(cur, open_pairs)
            with self._txn(conn) as cur:
                if self._withheld(cur, runner_id, host["host_id"]):
                    return None
                handed = self._hand_over(cur, runner_id, host["host_id"])
            if handed is not None:
                return handed
            seen = []
            for _ in range(CLAIM_SCAN):
                # One candidate per transaction: a competing host locks (and judges) the next one, not the same one; a
                # refusal commits on its own; and no transaction ever holds two items' grants, so two claims never wait
                # on each other. Waiting items are left out in SQL, so each candidate is claimed or ended.
                with self._txn(conn) as cur:
                    if self._withheld(cur, runner_id, host["host_id"]):
                        return None
                    cur.execute(_WAITING_SQL + _CANDIDATE_SQL, {
                        "pairs": open_pairs, "env": self.environment, "principal": self.principal, "seen": seen,
                        "caps": json.dumps(caps)})
                    row = cur.fetchone()
                    if row is None:
                        return None
                    seen.append(str(row["request_id"]))
                    claimed = self._admit(cur, row, runner_id, host["host_id"])
                if claimed is not None:
                    return claimed
        return None

    def _use(self, cur, grant_id, subject, work_ids, *, step=None) -> str | None:
        """One of the grant's operations, used for ``subject`` in this transaction (GrantStore.use_for_item, R4.3-c:
        admission is where the item path plays prepare_effect's part), or the refusal code. For a card step, the
        operation is bound to that one step: recorded with the step's template digest (grant.step_bound, once per grant
        and step digest, under the grant's row lock), so no operation of a card grant is ever spent twice on the same
        step (grant_step_used, nothing used) (N2)."""
        if step is None:
            return self._grants.use_for_item(cur.connection, self.principal, self.environment, grant_id,
                                             subject_digest=subject, capability_prefix=GRANT_FAMILY, work_ids=work_ids)
        cur.execute("SAVEPOINT mc_card_step")
        code = self._grants.use_for_item(cur.connection, self.principal, self.environment, grant_id,
                                         subject_digest=subject, capability_prefix=GRANT_FAMILY, work_ids=work_ids)
        if code is None and not self._event(
                cur, "grant.step_bound", item=step["item_id"], generation=0,
                once=_once("grant_step", grant_id, subject), body={"grant_id": grant_id, "card_id": step["card_id"], "step": step["step"],
                      "template_digest": subject, "operation_id": card_mod.step_operation_id(grant_id, subject)}):
            cur.execute("ROLLBACK TO SAVEPOINT mc_card_step")               # the operation goes back unused
            code = "grant_step_used"
        cur.execute("RELEASE SAVEPOINT mc_card_step")
        return code

    def _grant_refusal(self, cur, row) -> str | None:
        """None while the item's grant is still current and covers it (GrantStore.covers_item, read only: admission
        already used its operation; at claim, renew, a new effect and reconcile the item path plays recheck_execution's
        part). A card step is covered by its template digest, recomputed from its columns, so a step whose fixed fields
        differ from what was compiled is not covered (R4.3-c). An item a revocation is ending (facts.ending: one under
        the revoked grant, or a step of the revoked card under its own grant) answers that revocation's code."""
        ending = (row["facts"] or {}).get("ending")
        if ending:
            return ending.get("code") or "grant_revoked"
        if row["grant_id"] is None:
            return "grant_missing"
        subject = _row_subject(row)
        if subject is None:
            return "reservation_missing"
        return self._grants.covers_item(cur.connection, row["principal_id"], row["environment"], str(row["grant_id"]),
                                        subject_digest=subject, capability_prefix=GRANT_FAMILY,
                                        work_ids=tuple(w for w in (row["card_id"], row["job_id"]) if w))

    def _end(self, cur, row, state, detail, host_id):
        """The authority ends an item it will not hand out (a refusal written to the item)."""
        cur.execute("""UPDATE guild.mc_delegations SET state=%s, result_detail=%s::jsonb, finished_at=clock_timestamp(),
            lease_until=NULL WHERE request_id=%s""", (state, json.dumps(detail), str(row["request_id"])))
        self._event(cur, f"item.{state}", item=row["request_id"], generation=row["generation"], host_id=host_id,
                    once=_once(state, row["request_id"], row["generation"]), body=detail)
        self._card_ended(cur, row, state, detail.get("code"))

    def _hold(self, cur, row, code, host_id):
        """A claimable item waits (it stays queued): the reason is written to its reservation once, when it changes."""
        res = dict(row["reservation"] or {})
        if res.get("held") == code:
            return
        res["held"] = code
        cur.execute("UPDATE guild.mc_delegations SET reservation=%s::jsonb WHERE request_id=%s",
                    (json.dumps(res), str(row["request_id"])))
        self._event(cur, "item.held", item=row["request_id"], generation=row["generation"], host_id=host_id,
                    body={"code": code, "route": row["route"]})

    def _admit(self, cur, row, runner_id, host_id):
        code = self._grant_refusal(cur, row) or self._card_refusal(cur, row)
        if code is not None:                 # W-EXP: expired before the claim, so never claimed
            state = {"grant_expired": "expired", "grant_revoked": "cancelled", "card_ended": "cancelled"}.get(
                code, "refused")
            self._end(cur, row, state, {"code": code}, host_id)
            return None
        refusal = route_refusal(self.authority, row["route"], row["actor"])
        route = self.authority.routes.get(row["route"])
        try:
            amount = _amount((row["reservation"] or {}).get("requested"))
        except AuthorityRefused:
            amount = None
        if refusal is None and amount is None:
            refusal = {"code": "reservation_missing"}
        elif refusal is None and amount > route.per_item:
            refusal = {"code": "item_cap_exceeded"}
        if refusal is not None:
            self._end(cur, row, "refused", refusal, host_id)
            return None
        hold, day = self._reserve(cur, row["route"], route, amount)
        if hold is not None:                 # another host took the last amount since this poll's query
            self._hold(cur, row, hold, host_id)
            return None
        generation = row["generation"] + 1
        res = {"requested": float(amount), "reserved": float(amount), "unit": route.unit, "day": day.isoformat(),
               "generation": generation}
        cur.execute("""UPDATE guild.mc_delegations SET state='claimed', generation=%s, runner_id=%s,
            lease_until=clock_timestamp() + %s * interval '1 second', heartbeat_at=clock_timestamp(), reservation=%s::jsonb,
            facts=COALESCE(facts, '{}'::jsonb) || jsonb_build_object('picked_up_at', clock_timestamp(),
                                                                     'leased_at', clock_timestamp())
            WHERE request_id=%s RETURNING lease_until""",
            (generation, runner_id, self.authority.lease_s, json.dumps(res), str(row["request_id"])))
        lease_until = cur.fetchone()["lease_until"]
        self._event(cur, "item.claimed", item=row["request_id"], generation=generation, host_id=host_id,
                    once=_once("claimed", row["request_id"], generation),
                    body={"runner_id": runner_id, "lease_until": _iso(lease_until), "route": row["route"], **res})
        return {"envelope": self._envelope(row, generation, list(row["input_artifacts"] or [])),
                "route": row["route"], "unit": route.unit, "reserved": float(amount), "lease_until": _iso(lease_until),
                "card_id": row["card_id"], "step": row["step"], "attempt": _attempt(row), "resume_from": None}

    def _hand_over(self, cur, runner_id, host_id):
        """An item waiting, queued, for this runner alone: one the owner reassigned to it (R5.1-d), or a card step it
        failed definitely and the authority gave a retry from its last checkpoint (R4.3-d). Its claim takes it at the
        generation already set, and only now does its lease start. Either keeps the item's one reservation and gets
        what remains of it (a retry waits, marked, while its route is held by unknown usage). None if none can be
        taken now."""
        cur.execute("""SELECT d.*, clock_timestamp() AS now FROM guild.mc_delegations d
            WHERE d.principal_id=%s AND d.environment=%s AND d.runner_id=%s AND d.route IS NOT NULL
              AND d.state = 'queued' AND (d.facts ? 'reassigned' OR d.facts ? 'retry')
            ORDER BY d.request_id LIMIT %s FOR UPDATE SKIP LOCKED""",
                    (self.principal, self.environment, runner_id, CLAIM_SCAN))
        for row in cur.fetchall():
            taken = self._hand_over_one(cur, row, runner_id, host_id)
            if taken is not None:
                return taken
        return None

    def _hand_over_one(self, cur, row, runner_id, host_id):
        code = self._grant_refusal(cur, row) or self._card_refusal(cur, row)
        if code is None and row["deadline"] <= row["now"]:
            code = "deadline_passed"
        retry, res = (row["facts"] or {}).get("retry"), row["reservation"] or {}
        if code is None and retry is not None:   # a retry is a fresh launch: the route gate is judged again
            code = (route_refusal(self.authority, row["route"], row["actor"]) or {}).get("code")
        if code is None and ("reserved" not in res or res.get("state") is not None):
            code = "reservation_missing"
        if code is not None:                  # revoked, expired or overdue since: it ends here, never started here
            self._close(cur, row, {"grant_expired": "expired", "grant_revoked": "cancelled",
                                   "deadline_passed": "timed_out", "card_ended": "cancelled"}.get(code, "interrupted"),
                        {"code": code}, children_stopped=True, host_id=host_id)
            return None
        if retry is not None:
            cur.execute("""SELECT 1 FROM guild.mc_delegation_reservations WHERE environment=%s AND route=%s
                AND usage_hold IS NOT NULL LIMIT 1""", (self.environment, row["route"]))
            if cur.fetchone() is not None:
                self._hold(cur, row, "usage_unknown", host_id)
                return None
        res = {**{k: v for k, v in res.items() if k not in ("held", "accounted")}, "generation": row["generation"]}
        cur.execute("""UPDATE guild.mc_delegations SET state='claimed', lease_until=clock_timestamp() + %s * interval '1 second',
            heartbeat_at=clock_timestamp(), reservation=%s::jsonb,
            facts=(facts - 'retry') || jsonb_build_object('picked_up_at', clock_timestamp(), 'leased_at', clock_timestamp())
            WHERE request_id=%s RETURNING lease_until""",
            (self.authority.lease_s, json.dumps(res), str(row["request_id"])))
        lease_until = cur.fetchone()["lease_until"]
        self._event(cur, "item.claimed", item=row["request_id"], generation=row["generation"], host_id=host_id,
                    once=_once("claimed", row["request_id"], row["generation"]),
                    body={"runner_id": runner_id, "lease_until": _iso(lease_until), "route": row["route"],
                          "handover": True, **({"retry": retry} if retry else {}), **res})
        return {"envelope": self._envelope(row, row["generation"], list(row["input_artifacts"] or [])),
                "route": row["route"], "unit": res["unit"], "reserved": res["reserved"], "lease_until": _iso(lease_until),
                "card_id": row["card_id"], "step": row["step"], "attempt": _attempt(row),
                "resume_from": None if retry is None else retry["checkpoint"]}

    def _reserve(self, cur, route_name, route, amount):
        """(hold code, None) or (None, day): ``amount`` reserved on today's row, under the route's lock, so two hosts
        competing for the last amount get one claim between them (R8.3-b)."""
        self._lock_route(cur, route_name)
        cur.execute("""SELECT 1 FROM guild.mc_delegation_reservations WHERE environment=%s AND route=%s
            AND usage_hold IS NOT NULL LIMIT 1""", (self.environment, route_name))
        if cur.fetchone() is not None:
            return "usage_unknown", None
        cur.execute("SELECT (clock_timestamp() AT TIME ZONE 'America/Chicago')::date AS day")
        day = cur.fetchone()["day"]
        cur.execute("""INSERT INTO guild.mc_delegation_reservations (environment, route, day, unit) VALUES (%s,%s,%s,%s)
            ON CONFLICT DO NOTHING""", (self.environment, route_name, day, route.unit))
        cur.execute("""SELECT * FROM guild.mc_delegation_reservations WHERE environment=%s AND route=%s AND day=%s
            FOR UPDATE""", (self.environment, route_name, day))
        today = cur.fetchone()
        hold = reservation_hold(today["unit"], route.unit, today["reserved"], today["observed"], amount, route.per_day)
        if hold is not None:
            return hold, None
        cur.execute("""UPDATE guild.mc_delegation_reservations SET reserved=reserved + %s, cap=%s,
            updated_at=clock_timestamp() WHERE environment=%s AND route=%s AND day=%s""",
            (amount, route.per_day, self.environment, route_name, day))
        return None, day

    @staticmethod
    def _owner_refusal(row, generation, runner_id) -> str | None:
        """Why an owner write (progress, renew, result, a new effect) is refused, or None (R4.4): the current
        generation, its owner, and a lease still running by the database clock. ``lease_lapsed`` is final for renew and
        a new effect; progress and a result may still be a record from before the lapse (_pre_lapse)."""
        if row["generation"] != generation:
            return "stale_generation"
        if row["runner_id"] != runner_id:
            return "not_owner"
        if row["state"] in ("lapsed", "paused"):
            return "lease_lapsed"                  # the owner reconciles it; nothing else is taken (R5.1-c)
        if row["state"] not in ("claimed", "running"):
            return "item_ended" if row["finished_at"] is not None else "not_owned"
        if row["lease_until"] is None or row["lease_until"] <= row["now"]:
            return "lease_lapsed"
        return None

    def _pre_lapse(self, cur, row, when) -> str | None:
        """R5.1-c: once a lease has lapsed, the owner may still replay a record it made while the lease ran (R3.2-c,
        DUP-5, X1): progress, a checkpoint or a result. Taken while the item is within its grace (outages accounted
        first), when the record's host time lies in the generation's lease, from the moment the authority granted it
        to the moment it ended, each bound widened by clock_skew_s, both on the database's clock (§2.5 bounds the
        host's offset). Otherwise ``lease_lapsed``, or ``grace_ended`` past the grace, as R5.1-c's interrupted row
        says. None: take it."""
        skew = timedelta(seconds=self.authority.clock_skew_s)
        facts = row["facts"] or {}
        start = facts.get("leased_at") or facts.get("picked_up_at")
        if start is None or row["lease_until"] is None or not (parse_at(start) - skew <= when
                                                               <= row["lease_until"] + skew):
            return "lease_lapsed"
        return "grace_ended" if self._grace_ended(cur, row) else None

    def _progress_v2(self, request_id, generation, phase, runner_id, event_id, at, checkpoint) -> dict:
        rid = _uuid_text(request_id, "request_id_invalid")
        _need(type(generation) is int and not isinstance(generation, bool), "generation_invalid")
        _need(type(runner_id) is str and _ID.match(runner_id), "runner_id_invalid")
        eid = _uuid_text(event_id, "event_id_invalid")
        _need(type(phase) is str and _WORD.match(phase), "phase_invalid")
        when, cp = parse_at(at), check_checkpoint(checkpoint)
        body = {"runner_id": runner_id, "phase": phase, **({"checkpoint": cp} if cp else {})}
        with self._session() as conn:
            self._account_outage(conn)
            with self._txn(conn) as cur:
                return self._progress_in(cur, rid, generation, phase, runner_id, eid, when, cp, body)

    def _progress_in(self, cur, rid, generation, phase, runner_id, eid, when, cp, body) -> dict:
        # A replay is answered first, whatever has happened to the item since (DUP-5).
        replay = self._replay(cur, eid, "item.progress", rid, generation, when, body)
        if replay is not None:
            return {"state": "already_applied", "event_id": eid} if replay == "already_applied" else \
                {"state": "refused", "event_id": eid, "code": replay}
        row = self._item(cur, rid)
        if row is None or row["route"] is None:
            return {"state": "refused", "event_id": eid,
                    "code": "unknown_request" if row is None else "protocol_mismatch"}
        code = self._owner_refusal(row, generation, runner_id)
        if code == "lease_lapsed":
            code = self._pre_lapse(cur, row, when)
        if code is None and when > row["now"] + timedelta(seconds=FUTURE_SKEW_S):
            code = "event_time_invalid"
        if code is not None:
            return {"state": "refused", "event_id": eid, "code": code}
        written = self._host_event(cur, "item.progress", eid, item=rid, generation=generation,
                                   host_id=self._host_id(cur, runner_id), at=when, body=body)
        if written is not None:
            return {"state": "already_applied", "event_id": eid} if written == "already_applied" else \
                {"state": "refused", "event_id": eid, "code": written}
        # The host's time only orders its own events: phase and checkpoint are each kept newest by it, so a
        # replayed older record never replaces a newer one. progress_at is the authority's clock, when the newest
        # progress arrived, so ages and stalls are read on one clock; a record replayed after a lapse is dated no
        # later than the lease's end, so it never reads as fresh progress.
        last = (row["facts"] or {}).get("progress_host_at")
        newer = last is None or when > parse_at(last)
        stored = row["checkpoint"]
        cp_newer = cp is not None and (stored is None or when > parse_at(stored["at"]))
        if not newer and not cp_newer:
            return {"state": "superseded", "event_id": eid}
        cur.execute("""UPDATE guild.mc_delegations SET
            progress_at=CASE WHEN %s THEN LEAST(clock_timestamp(), lease_until) ELSE progress_at END,
            progress_phase=CASE WHEN %s THEN %s ELSE progress_phase END,
            facts=CASE WHEN %s THEN COALESCE(facts, '{}'::jsonb) || jsonb_build_object('progress_host_at', %s::text)
                       ELSE facts END,
            checkpoint=CASE WHEN %s THEN %s::jsonb ELSE checkpoint END,
            state=CASE WHEN state IN ('claimed','running') AND lease_until > clock_timestamp() THEN 'running'
                       ELSE state END,                     -- a replay after a lapse never makes it look live
            started_at=COALESCE(started_at, clock_timestamp())
            WHERE request_id=%s""",
            (newer, newer, phase, newer, _iso(when), cp_newer,
             json.dumps({**(cp or {}), "at": _iso(when), "event_id": eid}), rid))
        return {"state": "applied", "event_id": eid}

    def _renew_v2(self, request_id, generation, runner_id) -> bool:
        rid = _uuid_text(request_id, "request_id_invalid")
        _need(type(runner_id) is str and _ID.match(runner_id), "runner_id_invalid")
        with self._tx() as cur:
            row = self._item(cur, rid)
            if (row is None or row["route"] is None or row["cancel_requested"]
                    or self._owner_refusal(row, generation, runner_id) is not None):
                return False
            code = self._grant_refusal(cur, row)          # revoked or expired since the claim: read in this transaction
            if code is not None:
                self._event(cur, "lease.renew_refused", item=rid, generation=generation,
                            host_id=self._host_id(cur, runner_id), once=_once("renew_refused", rid, generation, code),
                            body={"code": code})
                return False
            cur.execute("""UPDATE guild.mc_delegations SET lease_until=clock_timestamp() + %s * interval '1 second',
                heartbeat_at=clock_timestamp() WHERE request_id=%s RETURNING lease_until""", (self.authority.lease_s, rid))
            lease_until = cur.fetchone()["lease_until"]
            self._event(cur, "lease.renewed", item=rid, generation=generation, host_id=self._host_id(cur, runner_id),
                        body={"runner_id": runner_id, "lease_until": _iso(lease_until)})
            return True

    def _result_v2(self, request_id, generation, runner_id, event_id, at, status, artifacts, detail, output_text,
                   route, usage) -> dict:
        rid = _uuid_text(request_id, "request_id_invalid")
        _need(type(generation) is int and not isinstance(generation, bool), "generation_invalid")
        _need(type(runner_id) is str and _ID.match(runner_id), "runner_id_invalid")
        eid = _uuid_text(event_id, "event_id_invalid")
        when, used, route = parse_at(at), check_usage(usage), check_route(route)
        if status not in RESULT_STATES:
            return {"state": "refused", "event_id": eid, "code": "status_invalid"}
        with self._session() as conn:
            self._account_outage(conn)
            with self._txn(conn) as cur:
                return self._result_in(cur, rid, generation, runner_id, eid, when, status, artifacts, detail,
                                       output_text, route, used)

    def _result_in(self, cur, rid, generation, runner_id, eid, when, status, artifacts, detail, output_text, route,
                   used) -> dict:
        row = self._item(cur, rid)
        if row is None or row["route"] is None:
            return {"state": "refused", "event_id": eid,
                    "code": "unknown_request" if row is None else "protocol_mismatch"}
        retried = self._retried(cur, eid, rid, generation, when, status, route, used, detail, artifacts, output_text)
        if retried is not None:      # a definite failure already given its retry (R4.3-d), whatever came after: DUP-5
            return retried
        report = _report_digest(status, artifacts, detail, output_text, route, used)
        if row["result_event_id"] is not None:
            if str(row["result_event_id"]) != eid:
                return {"state": "refused", "event_id": eid, "code": "different_result_already_recorded"}
            # S1-A's rule for every version 2 owner write: the identical report replayed is already_applied; anything
            # else under that event id (status, exit code, usage, artifacts, output, time, generation) is refused.
            if self._same_result(cur, eid, rid, generation, when, report):
                return {"state": "already_applied", "event_id": eid}
            return {"state": "refused", "event_id": eid, "code": "event_id_conflict"}
        res = dict(row["reservation"] or {})
        code = self._owner_refusal(row, generation, runner_id)
        if code == "lease_lapsed":
            code = self._pre_lapse(cur, row, when)      # a result made before the lapse settles as any other
        if code is None and when > row["now"] + timedelta(seconds=FUTURE_SKEW_S):
            code = "event_time_invalid"
        if code is None and route != row["route"]:
            code = "route_mismatch"                    # R8.3-c: usage is only ever settled on the reserved route
        if code is None and used["unit"] != res.get("unit"):
            code = "unit_mismatch"
        if code is None and self._replay(cur, eid, "result.accepted", rid, generation, when, None) is not None:
            code = "event_id_conflict"                 # the id already names another event
        open_ops = [] if code is not None else self._open_receipts(cur, rid)
        if open_ops and ((row["facts"] or {}).get("ending") or self._grant_refusal(cur, row) == "grant_revoked"):
            code = "grant_revoked"     # R4.3-e, T10: a revocation waits on its open effect; a late result is refused
        host_id = self._host_id(cur, runner_id)
        if code is not None:
            self._event(cur, "result.refused", item=rid, generation=row["generation"], host_id=host_id,
                        once=_once("result_refused", eid, code),
                        body={"code": code, "status": status, "route": route, "generation": generation})
            return {"state": "refused", "event_id": eid, "code": code}
        # A record made before a lapse the sweeper hasn't written yet: the lapse goes into the audit first, with its
        # cause, so the audit never shows an item ending after its lease ran out without the lapse (N-C4).
        row = self._lapse_written(cur, row)
        outcome = {}
        if self._grant_refusal(cur, row) == "grant_revoked":
            # R4.3-e: a revoked item ends cancelled; a late result never turns it into completed. What the host
            # reports is kept (artifacts, usage) and its own status is noted.
            detail, status = {**(detail or {}), "code": "grant_revoked", "reported_status": status}, "cancelled"
            outcome = {"code": "grant_revoked"}
        elif row["cancel_requested"] and status == "completed":
            status = "cancelled"                       # Stop was pressed: the result is kept, not claimed as done
        no_retry = None
        if status == "failed" and not open_ops and _attempt(row) is not None:
            no_retry = self._retry_refusal(cur, row, detail, used)
            if no_retry is None:
                return self._retry(cur, row, eid, when, host_id, route, used, detail, artifacts, output_text)
        body = {"status": status, "route": route,
                "usage": {**used, "amount": None if used["amount"] is None else float(used["amount"])},
                "report": report}
        written = self._host_event(cur, "result.accepted", eid, item=rid, generation=generation, host_id=host_id,
                                   at=when, body=body)
        if written is not None:
            return {"state": "refused", "event_id": eid, "code": "event_id_conflict"}
        res = self._settle(cur, row, res, used, eid, host_id)
        text = output_text[:OUTPUT_MAX] if isinstance(output_text, str) else None
        fact = "jsonb_build_object('reported_at', clock_timestamp())" + (
            " || jsonb_build_object('released_at', clock_timestamp())" if res["state"] == "settled" else "")
        if open_ops:
            # SF1 on the result path: an effect's outcome is still open, so the item does not end. What the host
            # reports is kept as evidence (artifacts, output, its usage settled), and the item goes to the owner as
            # unknown with one decision listing the open operations; that decision's resolution ends it. Never
            # completed with an undecided effect (R5.1-e, DUP-6), and so never a next card step (S1-C).
            cur.execute("""UPDATE guild.mc_delegations SET result_event_id=%s, artifacts=%s::jsonb, output_text=%s,
                reservation=%s::jsonb, facts=COALESCE(facts, '{}'::jsonb) || """ + fact + " WHERE request_id=%s",
                (eid, json.dumps(artifacts or []), text, json.dumps(res), rid))
            d = self._decide(cur, row, "effect_unknown", open_ops, [], (status, None, "result_reported"),
                             reported={"status": status, "detail": detail or {}})
            return {"state": "accepted", "event_id": eid, "code": "effects_open", "decision_id": d["decision_id"]}
        cur.execute("""UPDATE guild.mc_delegations SET state=%s, result_event_id=%s, result_detail=%s::jsonb,
            artifacts=%s::jsonb, output_text=%s, finished_at=clock_timestamp(), lease_until=NULL,
            reservation=%s::jsonb, facts=COALESCE(facts, '{}'::jsonb) || """ + fact + " WHERE request_id=%s",
            (status, eid, json.dumps(detail or {}), json.dumps(artifacts or []), text, json.dumps(res), rid))
        if (row["facts"] or {}).get("card"):
            self._card_advance(cur, row, status, artifacts, no_retry)   # R7.4-b: in this same transaction
        return {"state": "accepted", "event_id": eid, **outcome}

    def _same_result(self, cur, eid, rid, generation, when, report) -> bool:
        """Whether the result accepted under ``eid`` is exactly this one: the host's own event for this item and
        generation, made at the same time, with the same report digest (what the host reported, before any status the
        authority recorded instead)."""
        cur.execute("""SELECT source, kind, item_id, generation, at, body FROM guild.mc_delegation_events
            WHERE event_id=%s AND principal_id=%s AND environment=%s""", (eid, self.principal, self.environment))
        found = cur.fetchone()
        return (found is not None and found["source"] == "host" and found["kind"] == "result.accepted"
                and str(found["item_id"]) == str(rid) and found["generation"] == generation and found["at"] == when
                and found["body"].get("report") == report)

    def _settle(self, cur, row, res, used, event_id, host_id) -> dict:
        """The reservation after a result. Observed usage is settled on the reserved day's row. Unknown usage keeps
        the amount reserved and closes the route until a recorded resolution. While the item's children are not
        confirmed stopped (a cancelled item's included, R8.3-c), the amount stays reserved: ``kept``."""
        self._lock_route(cur, row["route"])
        day = date.fromisoformat(res["day"])
        cur.execute("""SELECT * FROM guild.mc_delegation_reservations WHERE environment=%s AND route=%s AND day=%s
            FOR UPDATE""", (self.environment, row["route"], day))
        day_row = cur.fetchone()
        amount, reserved = used["amount"], Decimal(str(res["reserved"]))
        where = (self.environment, row["route"], day)
        if amount is None:
            hold = dict(day_row["usage_hold"] or {"since": _iso(row["now"]), "items": []})
            hold["items"] = [*hold["items"], {"item_id": str(row["request_id"]), "generation": row["generation"],
                                              "event_id": event_id}]
            cur.execute("""UPDATE guild.mc_delegation_reservations SET usage_hold=%s::jsonb, updated_at=clock_timestamp()
                WHERE environment=%s AND route=%s AND day=%s""", (json.dumps(hold), *where))
            res.update(state="usage_unknown", observed=None)
        elif not used["children_stopped"]:
            res.update(state="kept", observed=float(amount), kept="children_unconfirmed")
        else:
            cur.execute("""UPDATE guild.mc_delegation_reservations SET reserved=reserved - %s, observed=observed + %s,
                updated_at=clock_timestamp() WHERE environment=%s AND route=%s AND day=%s""", (reserved, amount, *where))
            res.update(state="settled", observed=float(amount))
        self._event(cur, f"reservation.{res['state']}", item=row["request_id"], generation=row["generation"],
                    host_id=host_id, body={"route": row["route"], "day": res["day"], "unit": res["unit"],
                                           "reserved": res["reserved"], "observed": res["observed"]})
        return res

    # ── version 2: cards (spec v0.5 R4.3-c, R4.3-d, R4.3-f, R7.4-a/b; cards.py) ──
    def admit_card(self, card, *, grant_id, owner_turn_ids, proposal_id, approved_operation_id, approval_expires_at,
                   by, note=None) -> dict:
        """The owner's admission of a card (R4.3-b, R4.3-c, R7.4-a), on his one exact approval of its digest. One
        transaction: the card's delegate.card grant is issued (its card steps' template digests, one operation each;
        its work id the card; its lifetime the card's own owner-approved one, else a day, R4.3-f; the approval's window
        at most 15 minutes, GrantStore); the card is recorded (card.admitted, source owner: the card's one admission,
        OWN-1's A), pinning its class row; and its first step is written and admitted by one of the grant's operations
        (use_for_item), queued, created_by owner. Every step's worker and route must run on this authority. The owner
        path calls it (S1-G: Robert's yes to the card's D-code, or an exact Confirm in MC; the owner test identity in
        acceptance), with that turn's id. The same card again answers duplicate; another card under its id is refused
        card_id_reused.
        -> {state: admitted | duplicate, card_id, card_digest, grant_id, steps: [{step, kind, digest, on_card}],
            first: {job_id, request_id, state}}"""
        _need(self._v2, "protocol_not_enabled")
        _need(type(by) is str and _ID.match(by), "owner_invalid")
        _need(note is None or type(note) is str, "say_why")
        from core.minimoi_actions.context import ActionContractError
        try:
            compiled = self._cards.compile(card)
        except card_mod.CardRefused as exc:
            raise AuthorityRefused(str(exc)) from None
        for step in compiled.steps:                      # every step can run here, before anything is written
            self._v2_item(f"{compiled.card_id}.{step.name}", step.actor, step.scope, compiled.card["repository"],
                          compiled.card["ref"], step.brief, step.cost_cap_usd, compiled.card["step_deadline_s"],
                          step.route, step.amount, None, list(step.required_capabilities), [], compiled.card_id,
                          step.name, "authority")
        steps = [{"step": s.name, "kind": s.kind, "index": s.index, "digest": s.digest, "on_card": s.on_card}
                 for s in compiled.steps]
        answer = {"card_id": compiled.card_id, "card_digest": compiled.digest, "steps": steps,
                  "first": {"job_id": f"{compiled.card_id}.{compiled.steps[0].name}"}}
        with self._tx() as cur:
            found = self._recorded(cur, _once("card", compiled.card_id))
            if found is not None:
                _need(found.get("card_digest") == compiled.digest, "card_id_reused")
                return {"state": "duplicate", **answer, "grant_id": found["grant_id"]}
            cur.execute("SELECT clock_timestamp() AS now")
            now = cur.fetchone()["now"]
            try:
                self._grants.issue(cur.connection, card_mod.card_grant(
                    compiled, grant_id=grant_id, principal=self.principal, environment=self.environment,
                    owner_turn_ids=owner_turn_ids, proposal_id=proposal_id,
                    approved_operation_id=approved_operation_id, approval_expires_at=approval_expires_at, now=now))
            except ActionContractError as exc:
                raise AuthorityRefused(str(exc)) from None
            self._event(cur, "card.admitted", source="owner", once=_once("card", compiled.card_id),
                        body={"card_id": compiled.card_id, "card_digest": compiled.digest, "card": compiled.card,
                              "class": compiled.class_name, "class_version": compiled.card["class_version"],
                              "class_row": compiled.class_row, "families": compiled.families,
                              "class_digest": compiled.class_digest, "grant_id": grant_id, "steps": steps,
                              "proposal_id": proposal_id, "owner_turn_ids": list(owner_turn_ids), "by": by,
                              "note": (note or "").strip()[:300]})
            first = self._write_step(cur, compiled, compiled.steps[0], grant_id, created_by="owner", earlier=[])
            _need(first is not None, "card_step_exists")
            _need(first["state"] == "queued", first["code"] or "card_not_admitted")      # rolls the whole card back
            return {"state": "admitted", **answer, "grant_id": grant_id,
                    "first": {"job_id": first["job_id"], "request_id": first["request_id"], "state": first["state"]}}

    def admit(self, request_id, grant_id, *, by, note) -> dict:
        """The owner admits a proposed item under a grant he issued for it (R4.3-b): a step of another model family
        than its card (a cross-vendor review: its own exact approval, T13), or a card step its card's grant no longer
        admitted. One of that grant's operations is used for the digest the item's grant must name (a card step's
        template digest, otherwise its work's), the item becomes queued, and its queued decision is resolved. The
        grant's refusal code otherwise; the item stays proposed. -> {state: queued, grant_id}"""
        _need(self._v2, "protocol_not_enabled")
        rid, gid = _uuid_text(request_id, "request_id_invalid"), _uuid_text(grant_id, "grant_id_invalid")
        by, note = _owner_act(by, note)
        with self._tx() as cur:
            row = self._item(cur, rid)
            _need(row is not None and row["route"] is not None, "unknown_request")
            _need(row["state"] == "proposed", "item_not_proposed")
            _need(not self._card_finished(cur, row), "card_ended")
            subject = _row_subject(row)
            _need(subject is not None, "item_invalid")
            on_card = ((row["facts"] or {}).get("card") or {}).get("on_card")
            code = self._use(cur, gid, subject, tuple(w for w in (row["card_id"], row["job_id"]) if w),
                             step={"item_id": rid, "card_id": row["card_id"], "step": row["step"]} if on_card else None)
            _need(code is None, code or "")
            cur.execute("""UPDATE guild.mc_delegations SET state='queued', grant_id=%s, result_detail=NULL
                WHERE request_id=%s""", (gid, rid))
            decision = (row["result_detail"] or {}).get("decision_id")
            self._event(cur, "item.admitted", item=rid, generation=row["generation"], source="owner",
                        body={"grant_id": gid, "card_id": row["card_id"], "step": row["step"], "by": by, "note": note,
                              "subject_digest": subject, "decision_id": decision,
                              "prior": (row["result_detail"] or {}).get("code")})
            if decision is not None:
                self._event(cur, "decision.resolved", item=rid, generation=row["generation"], source="owner",
                            once=_once("decision_resolved", decision),
                            body={"decision_id": decision, "answer": "admitted", "by": by})
            return {"state": "queued", "grant_id": gid}

    def _card_finished(self, cur, row) -> bool:
        """Whether ``row`` is a step of a card that has ended (card.ready or card.ended): nothing hands it out,
        admits it or moves it again; it can only be ended (NSF1)."""
        return bool((row["facts"] or {}).get("card")) and \
            self._recorded(cur, _once("card_done", row["card_id"])) is not None

    def _card_record(self, cur, card_id):
        """(the card's admission record, the card recompiled from it), or (None, None): the authority's own record is
        the only source a step is compiled from."""
        record = self._recorded(cur, _once("card", card_id)) if card_id else None
        if record is None:
            return None, None
        try:
            return record, card_mod.recompile(record)
        except card_mod.CardRefused:
            return record, None

    def _card_done(self, cur, card_id) -> list:
        """The card's completed steps as recorded: [{step, item_id, artifacts}] (their accepted results' outputs)."""
        cur.execute("""SELECT step, request_id, artifacts FROM guild.mc_delegations
            WHERE principal_id=%s AND environment=%s AND card_id=%s AND state='completed' AND facts ? 'card'
            ORDER BY (facts->'card'->>'index')::int""", (self.principal, self.environment, card_id))
        return [{"step": r["step"], "item_id": r["request_id"], "artifacts": r["artifacts"]} for r in cur.fetchall()]

    def _card_refusal(self, cur, row) -> str | None:
        """For a step of a card, at its hand-out: None while it is still the step its card compiles now, from the
        authority's own records: the same template digest, input artifacts that are the outputs recorded for the
        card's earlier steps as its template selects them (its only free field), and an attempt within the card's
        allowance, of a card that has not ended (a step of an ended card is never handed out, NSF1). Otherwise the code;
        the item is not handed out."""
        if not (row["facts"] or {}).get("card"):
            return None
        if self._card_finished(cur, row):
            return "card_ended"
        _, compiled = self._card_record(cur, row["card_id"])
        step = None if compiled is None else compiled.step(row["step"])
        if step is None:
            return "card_record_invalid"
        if card_mod.row_step_digest(row) != step.digest:
            return "card_step_mismatch"
        try:
            arts, _ = compiled.inputs(step, self._card_done(cur, row["card_id"]))
        except card_mod.CardRefused as exc:
            return str(exc)
        if list(row["input_artifacts"] or []) != arts:
            return "card_input_not_recorded"
        if (_attempt(row) or 0) > compiled.card["attempts"]:
            return "card_attempts_exhausted"
        return None

    def _write_step(self, cur, compiled, step, grant_id, *, created_by, earlier) -> dict | None:
        """Write one enumerated step of a card as its template compiles it: its brief, worker, route and caps fixed;
        its input artifacts, the only free field, filled from the authority's records of the card's earlier steps;
        created_by authority for every step after the first (OWN-2). A card step is admitted by one of the card
        grant's operations and queued, or, when the grant no longer admits it, written proposed with one routine
        decision. A step of another model family is never a card step: it is written proposed with one solo decision
        (R7.4-c) and waits for its own admission (T13)."""
        arts, sources = compiled.inputs(step, earlier)
        card = compiled.card
        item = self._v2_item(f"{compiled.card_id}.{step.name}", step.actor, step.scope, card["repository"], card["ref"],
                             step.brief, step.cost_cap_usd, card["step_deadline_s"], step.route, step.amount,
                             grant_id if step.on_card else None, list(step.required_capabilities), arts,
                             compiled.card_id, step.name, created_by)
        facts = {"card": {"digest": compiled.digest, "class_digest": compiled.class_digest, "kind": step.kind,
                          "index": step.index, "attempt": 1, "on_card": step.on_card, "template_digest": step.digest,
                          "inputs_from": sources}}
        if step.on_card:
            return self._write(cur, item, subject=step.digest, facts=facts,
                               decision={"class": "step_admission", "solo": False})
        return self._write(cur, item, subject=item["subject"], facts=facts, admit=False,
                           decision={"class": "cross_vendor_admission", "solo": True, "code": "cross_vendor"})

    def _card_advance(self, cur, row, status, artifacts, no_retry=None):
        """R7.4-b, in the transaction of the result that ended a card's step: a completed step is followed by the next
        step its card enumerates, written by the authority (card.ready when there is none: every step done, ready for
        the owner's merge); a step that ended otherwise ends the card (card.ended, with why no retry was given). A card
        that has already ended (revoked, or stopped) gets nothing more. A next step this authority refuses to write or
        admit now (its route, worker or cap changed since the card was admitted) ends the card with that reason, and the
        result that completed the step before it is kept: a refusal of the next step never aborts the result. A step
        whose result turned the item unknown never reaches here: nothing follows it until its decision is resolved."""
        rid, g, card_id = row["request_id"], row["generation"], row["card_id"]
        once = _once("card_done", card_id)
        if self._recorded(cur, once) is not None:
            return                                            # the card has ended: nothing is written after it
        record, compiled = self._card_record(cur, card_id)
        step = None if compiled is None else compiled.step(row["step"])
        if step is None:
            self._event(cur, "card.ended", item=rid, generation=g, once=once,
                        body={"card_id": card_id, "step": row["step"], "state": status, "code": "card_record_invalid"})
            return
        if status != "completed":
            self._event(cur, "card.ended", item=rid, generation=g, once=once,
                        body={"card_id": card_id, "step": step.name, "state": status, "attempt": _attempt(row),
                              "code": no_retry or status})
            return
        nxt = compiled.following(step, artifacts)
        if nxt is None:
            self._event(cur, "card.ready", item=rid, generation=g, once=once, body={"card_id": card_id,
                                                                                   "step": step.name})
            return
        from core.minimoi_actions.context import ActionContractError
        cur.execute("SAVEPOINT mc_card_next")
        try:
            out = self._write_step(cur, compiled, nxt, record["grant_id"], created_by="authority",
                                   earlier=self._card_done(cur, card_id))
        except (card_mod.CardRefused, AuthorityRefused, ActionContractError) as exc:
            cur.execute("ROLLBACK TO SAVEPOINT mc_card_next")     # a refusal, never a fault: the result stands
            out = {"code": str(exc)}
        cur.execute("RELEASE SAVEPOINT mc_card_next")
        if out is None or out.get("request_id") is None:
            self._event(cur, "card.ended", item=rid, generation=g, once=once,
                        body={"card_id": card_id, "step": nxt.name, "state": "not_written",
                              "code": "card_step_exists" if out is None else out["code"]})
            return
        self._event(cur, "card.step_written", item=out["request_id"], generation=0,
                    body={"card_id": card_id, "step": nxt.name, "after": step.name, "after_item": str(rid),
                          "state": out["state"], **({"code": out["code"]} if out["code"] else {})})

    def _card_ended(self, cur, row, state, code):
        """A card's step ended other than by its result (a refusal at hand-out, Stop, end, revocation, the grace end):
        the card ends with it, recorded once (card.ended). Nothing follows it."""
        if (row["facts"] or {}).get("card"):
            self._event(cur, "card.ended", item=row["request_id"], generation=row["generation"],
                        once=_once("card_done", row["card_id"]),
                        body={"card_id": row["card_id"], "step": row["step"], "state": state, "code": code,
                              "attempt": _attempt(row)})

    # ── attempts (R4.3-d) ──
    def _retried(self, cur, event_id, request_id, generation, when, status, route, used, detail, artifacts,
                 output_text):
        """None unless ``event_id`` already recorded a failed attempt of this item that was given its retry. Then S1-A's
        rule for every owner write: the identical report replayed answers already_applied, with the retry it was given
        (DUP-5); anything else under that id is refused event_id_conflict (review N5)."""
        cur.execute("""SELECT generation, body FROM guild.mc_delegation_events WHERE event_id=%s AND kind='attempt.failed'
            AND item_id=%s AND principal_id=%s AND environment=%s""",
                    (event_id, str(request_id), self.principal, self.environment))
        found = cur.fetchone()
        if found is None:
            return None
        n = found["body"]["attempt"]
        body = _attempt_body(status, route, used, n, detail, artifacts, output_text)
        if self._replay(cur, event_id, "attempt.failed", request_id, generation, when, body) != "already_applied":
            return {"state": "refused", "event_id": event_id, "code": "event_id_conflict"}
        return {"state": "already_applied", "event_id": event_id, "code": "retry", "attempt": n + 1,
                "generation": found["generation"] + 1}

    def _retry_refusal(self, cur, row, detail, used) -> str | None:
        """None when a card step's definite failure may be retried from its last checkpoint without asking the owner
        (R4.3-d, E4): a step of the card grant itself, a known exit, its children confirmed stopped, its Stop not
        pressed, an attempt left in the card's allowance, its grant still covering it, and something left of the
        item's one reservation once this attempt's known usage is counted (§8.1 record 2; the per-item cap is the
        item's, never an attempt's). Otherwise why not."""
        if not row["facts"]["card"].get("on_card"):
            return "not_a_card_step"           # admitted by its own approval, which covers one attempt
        code = (detail or {}).get("exit_code")
        if type(code) is not int:
            return "exit_unknown"              # an unknown outcome is never retried by code (R4.1-c)
        if not used["children_stopped"]:
            return "children_unconfirmed"
        if row["cancel_requested"]:
            return "owner_stop"
        if self._card_finished(cur, row):
            return "card_ended"
        _, compiled = self._card_record(cur, row["card_id"])
        if compiled is None:
            return "card_record_invalid"
        if _attempt(row) >= compiled.card["attempts"]:
            return "attempts_exhausted"
        code = self._grant_refusal(cur, row)
        if code is not None:
            return code
        res = row["reservation"] or {}
        if "reserved" not in res or res.get("state") is not None:
            return "reservation_missing"
        if used["amount"] is None:
            return "usage_unknown"             # what is left of the reservation can't be known, so never assumed
        if Decimal(str(res["reserved"])) - used["amount"] <= 0:
            return "reservation_exhausted"     # the item's cap is spent: no attempt gets a fresh one
        return None

    def _retry(self, cur, row, eid, when, host_id, route, used, detail, artifacts, output_text) -> dict:
        """The retry (R4.3-d), in the failed attempt's result transaction: the attempt's report is recorded (its own
        host event, attempt.failed; it is not the item's result, so an item keeps at most one accepted result,
        DUP-4). Its known usage moves from the item's reservation to the day's observed spend, and the rest of that
        one reservation stays held for the next attempt, which gets only what remains (never a fresh per-item
        amount). The item goes back, queued, to the same owner (its last checkpoint is there) under generation + 1
        with a fresh step deadline. Its effects keep their operation ids, so one already done is never sent again (as
        at a resume)."""
        rid, g, n = row["request_id"], row["generation"], _attempt(row)
        _, compiled = self._card_record(cur, row["card_id"])
        written = self._host_event(cur, "attempt.failed", eid, item=rid, generation=g, host_id=host_id, at=when,
                                   body=_attempt_body("failed", route, used, n, detail, artifacts, output_text))
        if written is not None:
            return {"state": "refused", "event_id": eid, "code": "event_id_conflict"}
        res, amount = dict(row["reservation"]), used["amount"]
        remaining = Decimal(str(res["reserved"])) - amount
        self._lock_route(cur, row["route"])
        cur.execute("""UPDATE guild.mc_delegation_reservations SET reserved=reserved - %s, observed=observed + %s,
            updated_at=clock_timestamp() WHERE environment=%s AND route=%s AND day=%s""",
                    (amount, amount, self.environment, row["route"], date.fromisoformat(res["day"])))
        res.update(reserved=float(remaining), spent=float(Decimal(str(res.get("spent", 0))) + amount), accounted=True,
                   attempts=[*res.get("attempts", []), {"generation": g, "observed": float(amount), "event_id": eid}])
        self._event(cur, "reservation.attempt_settled", item=rid, generation=g, host_id=host_id,
                    body={"route": row["route"], "day": res["day"], "unit": res["unit"], "observed": float(amount),
                          "remaining": float(remaining)})
        cp = row["checkpoint"]
        retry = {"attempt": n + 1, "from_generation": g, "event_id": eid,
                 "checkpoint": None if cp is None else {"name": cp["name"], "digest": cp["digest"]}}
        cur.execute("""UPDATE guild.mc_delegations SET state='queued', generation=%s, lease_until=NULL,
            lapsed_since=NULL, lapse_cause=NULL, grace_deadline=NULL,
            deadline=clock_timestamp() + %s * interval '1 second', reservation=%s::jsonb,
            facts=(COALESCE(facts, '{}'::jsonb) - 'picked_up_at' - 'leased_at')
                  || jsonb_build_object('retry', %s::jsonb,
                                        'card', (facts->'card') || jsonb_build_object('attempt', %s::int))
            WHERE request_id=%s""",
            (g + 1, compiled.card["step_deadline_s"], json.dumps(res), json.dumps(retry), n + 1, rid))
        self._event(cur, "item.retried", item=rid, generation=g + 1, host_id=host_id, once=_once("retried", rid, g),
                    body={"runner_id": row["runner_id"], "from_generation": g, "generation": g + 1, "attempt": n + 1,
                          "attempts": compiled.card["attempts"], "exit_code": detail["exit_code"],
                          "checkpoint": retry["checkpoint"], "prior_lease_until": _iso(row["lease_until"]),
                          "remaining": float(remaining)})
        return {"state": "accepted", "event_id": eid, "code": "retry", "attempt": n + 1, "generation": g + 1}

    # ── version 2: recovery (spec v0.5 §5): reconcile, effect, the authority's liveness, the sweeper, the owner's acts ──
    def reconcile(self, runner_id, request_id, generation, stage, open_operations, *, protocol=2) -> dict:
        """The owner host's question for one item it holds, after a restart, a wake or a reconnect (§5.5 step 4), once
        it has frozen its child, replayed its pending records and read back its open effects. One answer, decided in
        one transaction, after any outage of the authority's not yet recorded is recorded (_account_outage):
          {answer: continue, generation, lease_until}                 the lease never lapsed; thaw the child
          {answer: resume, generation: g+1, lease_until, checkpoint}  rebuild at that checkpoint and restart from it
          {answer: stop, condition, code, state}                      a known outcome is recorded; workspace kept
          {answer: decide, code, decision_id, operations}             the item is unknown; one decision is queued
        ``condition`` names the one of R6.3's four conditions (checkpoint, identity, authority, budget) that failed,
        or None for an item that had ended, was stopped by its owner, or ran past its grace. The table, in order, is in
        the module docstring. Idempotent per (request_id, generation): a decision that raised the generation is
        answered again, never made twice (X5); no other answer raises it."""
        _need(self._protocol(protocol, ()), "protocol_invalid")
        rid = _uuid_text(request_id, "request_id_invalid")
        _need(type(generation) is int and not isinstance(generation, bool) and 0 <= generation < 2**31,
              "generation_invalid")
        _need(type(runner_id) is str and _ID.match(runner_id), "runner_id_invalid")
        stage, told = check_stage(stage), check_operations(open_operations)
        with self._session() as conn:
            self._account_outage(conn)
            with self._txn(conn) as cur:
                row = self._item(cur, rid)
                if row is None or row["route"] is None:
                    return _stop("identity", "unknown_request" if row is None else "protocol_mismatch")
                answer = self._reconcile(cur, row, runner_id, generation, stage, told)
                self._event(cur, "item.reconciled", item=rid, generation=generation,
                            host_id=self._host_id(cur, runner_id),
                            body={"runner_id": runner_id, "stage": stage, "open_operations": told, **answer})
                return answer

    def _reconcile(self, cur, row, runner_id, g, stage, told) -> dict:
        rid = row["request_id"]
        if row["generation"] > g:
            # This very decision, asked again (X5): answered as made, even if that generation has lapsed since (the
            # host then renews, is refused, and asks about it in turn).
            done = self._recorded(cur, _once("resumed", rid, g))
            if (done is not None and done.get("runner_id") == runner_id and row["generation"] == g + 1
                    and row["runner_id"] == runner_id and row["state"] in ("claimed", "running", "lapsed", "paused")):
                return {"answer": "resume", "generation": row["generation"], "lease_until": _iso(row["lease_until"]),
                        "checkpoint": done.get("checkpoint"), **_remaining(row)}
            return _stop("identity", "reassigned" if row["runner_id"] != runner_id else "superseded")
        if row["generation"] < g:
            return _stop("identity", "generation_unknown")
        if row["runner_id"] != runner_id:
            return _stop("identity", "not_owner")
        state, stopped = row["state"], stage["child"] == "stopped"
        if state in TERMINAL:
            if stopped:
                self._confirm_stopped(cur, row)
            if stage["child"] == "unverified":         # §5.5 step 4: an unverifiable process goes to the owner
                return self._decide_ended(cur, row, state)
            return _stop(None, (row["result_detail"] or {}).get("code") or state, state)
        if state not in ("claimed", "running", "lapsed", "paused", "unknown"):
            return _stop("identity", "not_owned")
        if state == "unknown":                                         # waits on the owner: the same decision
            d = row["result_detail"] or {}
            return {"answer": "decide", "code": d.get("reason"), "decision_id": d.get("decision_id"),
                    "operations": d.get("operations", [])}
        row = self._lapse_written(cur, row)
        state = row["state"]
        ending = self._ending(cur, row) or (self._grace_end(row) if self._grace_ended(cur, row) else None)
        open_ops = self._open_receipts(cur, rid)
        if open_ops or stage["child"] == "unverified":
            # Never ended with an effect's outcome open or a process unverified: the owner decides (R5.1-e).
            return self._decide(cur, row, "effect_unknown" if open_ops else "process_unverified", open_ops, told,
                                ending)
        if ending is not None:
            return self._stop_item(cur, row, *ending, stopped)
        code = self._grant_refusal(cur, row)
        if code is not None:
            return self._stop_item(cur, row, "interrupted", "authority", code, stopped)
        lapsed = state in ("lapsed", "paused") or row["lease_until"] <= row["now"]
        if not lapsed and stage["child"] == "frozen":
            return {"answer": "continue", "generation": g, "lease_until": _iso(row["lease_until"])}
        # Resume: a lapsed lease, or a child no longer running. The other three conditions must hold.
        if row["deadline"] <= row["now"]:
            return self._stop_item(cur, row, "timed_out", "authority", "deadline_passed", stopped)
        code = self._budget_refusal(cur, row)
        if code is not None:
            return self._stop_item(cur, row, "interrupted", "budget", code, stopped)
        held = row["checkpoint"]
        if (None if held is None else {"name": held["name"], "digest": held["digest"]}) != stage["checkpoint"]:
            return self._stop_item(cur, row, "interrupted", "checkpoint", "checkpoint_mismatch", stopped)
        return self._resume(cur, row, runner_id)

    def _resume(self, cur, row, runner_id) -> dict:
        """reconcile's ``resume``: generation + 1, a fresh lease, the same owner, from the last checkpoint the
        authority holds. The once-key makes a second rise for the same decision impossible."""
        g, cp = row["generation"], row["checkpoint"]
        checkpoint = None if cp is None else {"name": cp["name"], "digest": cp["digest"]}
        cur.execute("""UPDATE guild.mc_delegations SET state='claimed', generation=%s,
            lease_until=clock_timestamp() + %s * interval '1 second', heartbeat_at=clock_timestamp(), lapsed_since=NULL,
            lapse_cause=NULL, grace_deadline=NULL, reservation=%s::jsonb,
            facts=COALESCE(facts, '{}'::jsonb) || jsonb_build_object('resumed_at', clock_timestamp(),
                                                                     'leased_at', clock_timestamp())
            WHERE request_id=%s RETURNING lease_until""",
            (g + 1, self.authority.lease_s, json.dumps({**row["reservation"], "generation": g + 1}),
             str(row["request_id"])))
        lease_until = cur.fetchone()["lease_until"]
        if not self._event(cur, "item.resumed", item=row["request_id"], generation=g + 1,
                           host_id=self._host_id(cur, runner_id), once=_once("resumed", row["request_id"], g),
                           body={"runner_id": runner_id, "from_generation": g, "generation": g + 1,
                                 "lease_until": _iso(lease_until), "checkpoint": checkpoint, "prior_state": row["state"],
                                 "lapse_cause": row["lapse_cause"], "prior_lease_until": _iso(row["lease_until"])}):
            raise RuntimeError("generation_raised_twice")      # cannot happen under the item's lock; never commit it
        return {"answer": "resume", "generation": g + 1, "lease_until": _iso(lease_until), "checkpoint": checkpoint,
                **_remaining(row)}

    def _decide(self, cur, row, reason, open_ops, told, ending=None, *, reported=None) -> dict:
        """``decide`` (§5.5 step 4, R4.1-c, R5.1-e): the item becomes ``unknown``, its generation unchanged and its
        workspace kept, and exactly one decision is queued, listing its open operations: an unknown resolution, which
        never shares a batched approval (R7.4-c). ``ending`` is how the item would otherwise have ended (a Stop, a
        revocation, a grace end), kept for the owner. Today the decision is the ``decision.queued`` audit event, whose
        id is the decision's id; S1-G gives it its D-code and notice. Nothing is retried."""
        decision_id = str(uuid.uuid4())
        detail = {"code": "decide", "reason": reason, "operations": open_ops, "workspace": "kept",
                  "decision_id": decision_id, **({"host_open_operations": told} if told else {}),
                  **({"ending": {"state": ending[0], "code": ending[2]}} if ending else {}),
                  **({"reported": reported} if reported else {})}
        cur.execute("""UPDATE guild.mc_delegations SET state='unknown', result_detail=%s::jsonb, lease_until=NULL
            WHERE request_id=%s""", (json.dumps(detail), str(row["request_id"])))
        rid, g = row["request_id"], row["generation"]
        self._event(cur, "item.unknown", item=rid, generation=g, once=_once("unknown", rid, g), body=detail)
        self._event(cur, "decision.queued", item=rid, generation=g, once=_once("decision", rid, g), event_id=decision_id,
                    body={"class": "unknown_resolution", "solo": True, "reason": reason, "operations": open_ops,
                          **({"ending": detail["ending"]} if ending else {})})
        return {"answer": "decide", "code": reason, "decision_id": decision_id, "operations": open_ops}

    def _decide_ended(self, cur, row, state) -> dict:
        """An item that has already ended, whose child the host cannot verify: it stays as it ended, and one decision
        (process_unverified) is queued for it, once per generation; its reservation stays kept."""
        once = _once("decision_unverified", row["request_id"], row["generation"])
        cur.execute("""SELECT event_id FROM guild.mc_delegation_events WHERE principal_id=%s AND environment=%s
            AND once_key=%s""", (self.principal, self.environment, once))
        found = cur.fetchone()
        decision_id = str(found["event_id"]) if found else str(uuid.uuid4())
        if found is None:
            self._event(cur, "decision.queued", item=row["request_id"], generation=row["generation"], once=once,
                        event_id=decision_id, body={"class": "unknown_resolution", "solo": True,
                                                    "reason": "process_unverified", "operations": [],
                                                    "item_state": state})
        return {"answer": "decide", "code": "process_unverified", "decision_id": decision_id, "operations": [],
                "state": state}

    def _stop_item(self, cur, row, state, condition, code, stopped) -> dict:
        """reconcile's ``stop``: the known outcome is recorded and the workspace kept."""
        self._close(cur, row, state, {"code": code, **({"condition": condition} if condition else {}),
                                      "workspace": "kept"}, children_stopped=stopped)
        return _stop(condition, code, state)

    def _ending(self, cur, row):
        """How the owner's will ends this item now, as (state, condition, code), or None: a revocation waiting on an
        open effect, a revoked or expired grant, or the owner's Stop."""
        pending = (row["facts"] or {}).get("ending")
        if pending:
            return pending["state"], "authority", pending["code"]
        code = self._grant_refusal(cur, row)
        if code in ("grant_revoked", "grant_expired"):
            return ("cancelled" if code == "grant_revoked" else "expired"), "authority", code
        if row["cancel_requested"]:
            return "cancelled", None, "owner_stop"
        return None

    @staticmethod
    def _grace_end(row):
        """Past its grace: an owner that announced its stop and never came back is told apart (R5.1-b)."""
        return "interrupted", None, "owner_stopped_past_grace" if row["state"] == "paused" else "owner_unreachable"

    def _grace(self, cur, row, since) -> tuple:
        """(deadline, inside an outage, grace_s, owner) for a lease that ended at ``since``: the owner host's grace,
        less every recorded outage of the authority's (grace_deadline)."""
        owner = self._runner(cur, row["runner_id"])
        grace_s = 0 if owner is None or owner["unreachable_grace_s"] is None else owner["unreachable_grace_s"]
        cur.execute("""SELECT (body->>'from')::timestamptz AS a, (body->>'to')::timestamptz AS b
            FROM guild.mc_delegation_events WHERE principal_id=%s AND environment=%s AND kind='authority.suspended'
              AND (body->>'to')::timestamptz > %s""", (self.principal, self.environment, since))
        deadline, down = grace_deadline(since, grace_s, [(r["a"], r["b"]) for r in cur.fetchall()])
        return deadline, down, grace_s, owner

    def _grace_ended(self, cur, row) -> bool:
        """Past its grace by the database clock: as stored once lapsed, else from its lease (outages recorded first)."""
        if row["state"] in ("lapsed", "paused"):
            return row["grace_deadline"] is not None and row["grace_deadline"] <= row["now"]
        if row["state"] in ("claimed", "running") and row["lease_until"] is not None \
                and row["lease_until"] <= row["now"]:
            return self._grace(cur, row, row["lease_until"])[0] <= row["now"]
        return False

    def _budget_refusal(self, cur, row) -> str | None:
        """R6.3's budget condition: the item's reservation still held, its (route, actor) pair still open, and no
        unknown-usage hold on its route."""
        res = row["reservation"] or {}
        if "reserved" not in res or res.get("state") is not None:
            return "reservation_missing"
        gate = route_refusal(self.authority, row["route"], row["actor"])
        if gate is not None:
            return gate["code"]
        cur.execute("""SELECT 1 FROM guild.mc_delegation_reservations WHERE environment=%s AND route=%s
            AND usage_hold IS NOT NULL LIMIT 1""", (self.environment, row["route"]))
        return "usage_unknown" if cur.fetchone() is not None else None

    def _operations(self, cur, request_id) -> list:
        """The receipts this item prepared through ``effect``: the audit records each, in the same transaction."""
        cur.execute("""SELECT DISTINCT body->>'operation_id' AS op FROM guild.mc_delegation_events
            WHERE principal_id=%s AND environment=%s AND item_id=%s AND kind='effect.prepared' ORDER BY 1""",
                    (self.principal, self.environment, str(request_id)))
        return [r["op"] for r in cur.fetchall()]

    def _open_receipts(self, cur, request_id) -> list:
        """The item's effects whose outcome is not established: a receipt dispatched or unknown (ReceiptStore.read)."""
        out = []
        for op in self._operations(cur, request_id):
            state = self._receipts.read(cur.connection, self.principal, self.environment, op)["state"]
            if state in ("dispatched", "unknown"):
                out.append({"operation_id": op, "state": state})
        return out

    def open_operations(self, request_id) -> list:
        """The item's effects whose outcome is not established. Re-admitting work from an item (S1-C, S1-G) must refuse
        while this is not empty, so an effect is never sent twice under a new item's operation ids."""
        _need(self._v2, "protocol_not_enabled")
        rid = _uuid_text(request_id, "request_id_invalid")
        with self._tx() as cur:
            return self._open_receipts(cur, rid)

    def _recorded(self, cur, once) -> dict | None:
        cur.execute("""SELECT body FROM guild.mc_delegation_events WHERE principal_id=%s AND environment=%s
            AND once_key=%s""", (self.principal, self.environment, once))
        found = cur.fetchone()
        return None if found is None else found["body"]

    @staticmethod
    def _runner(cur, runner_id):
        cur.execute("SELECT * FROM guild.mc_delegation_runners WHERE runner_id=%s", (runner_id,))
        return cur.fetchone()

    # ── ending an item, and its reservation (R8.3-c) ──
    def _close(self, cur, row, state, detail, *, children_stopped, source="authority", host_id=None):
        """End a version 2 item. A reservation it holds stays reserved until its children are confirmed stopped.
        Callers end an item only with no receipt of it open (_open_receipts); with one open it goes to _decide."""
        res = self._release(cur, row, children_stopped)
        released = res.get("state") == "settled" and (row["reservation"] or {}).get("state") != "settled"
        cur.execute("""UPDATE guild.mc_delegations SET state=%s, result_detail=%s::jsonb, finished_at=clock_timestamp(),
            lease_until=NULL, reservation=%s::jsonb, facts=COALESCE(facts, '{}'::jsonb)
              || CASE WHEN %s THEN jsonb_build_object('released_at', clock_timestamp()) ELSE '{}'::jsonb END
            WHERE request_id=%s""", (state, json.dumps(detail), json.dumps(res), released, str(row["request_id"])))
        self._event(cur, f"item.{state}", item=row["request_id"], generation=row["generation"], host_id=host_id,
                    source=source, once=_once(state, row["request_id"], row["generation"]),
                    body={**detail, "reservation": res.get("state")})
        self._card_ended(cur, row, state, detail.get("code"))

    def _release(self, cur, row, children_stopped) -> dict:
        """The reservation of an item that ends without a settled result. While its children are not confirmed stopped
        it is ``kept``. Once they are, usage a result already reported is settled; with no report, the amount stays
        counted on its day as ``unreported``, never released as zero (§8.1). Every route that can be claimed bounds a
        launch by the item's cap, which the claim reserved in full, so the route stays open."""
        res = dict(row["reservation"] or {})
        if "reserved" not in res or res.get("state") in ("settled", "usage_unknown", "unreported"):
            return res
        if res.get("accounted") and children_stopped:
            # A retry that ends before it is picked up: every earlier attempt's usage is settled and nothing has run
            # since, so what remains of its reservation is released.
            self._lock_route(cur, row["route"])
            cur.execute("""UPDATE guild.mc_delegation_reservations SET reserved=reserved - %s,
                updated_at=clock_timestamp() WHERE environment=%s AND route=%s AND day=%s""",
                        (Decimal(str(res["reserved"])), self.environment, row["route"], date.fromisoformat(res["day"])))
            res.pop("accounted")
            res.update(state="settled", observed=0.0)
            self._event(cur, "reservation.settled", item=row["request_id"], generation=row["generation"],
                        body={"route": row["route"], "day": res["day"], "unit": res.get("unit"),
                              "reserved": res["reserved"], "observed": 0.0, "retry_released": True})
            return res
        if not children_stopped:
            if res.get("state") != "kept":
                res.update(state="kept", kept="children_unconfirmed")
                self._event(cur, "reservation.kept", item=row["request_id"], generation=row["generation"],
                            body={"route": row["route"], "day": res.get("day"), "reserved": res["reserved"]})
            return res
        if res.pop("kept", None) is not None and res.get("observed") is not None:
            self._lock_route(cur, row["route"])
            cur.execute("""UPDATE guild.mc_delegation_reservations SET reserved=reserved - %s, observed=observed + %s,
                updated_at=clock_timestamp() WHERE environment=%s AND route=%s AND day=%s""",
                (Decimal(str(res["reserved"])), Decimal(str(res["observed"])), self.environment, row["route"],
                 date.fromisoformat(res["day"])))
            res["state"] = "settled"
        else:
            res["state"] = "unreported"
        self._event(cur, f"reservation.{res['state']}", item=row["request_id"], generation=row["generation"],
                    body={"route": row["route"], "day": res.get("day"), "unit": res.get("unit"),
                          "reserved": res["reserved"], "observed": res.get("observed")})
        return res

    def _confirm_stopped(self, cur, row):
        """The owner confirms an ended item's children stopped (reconcile, child ``stopped``): a kept reservation is
        released by _release's rule. The release path for every ``kept`` reservation, the result's included."""
        if (row["reservation"] or {}).get("state") != "kept":
            return
        res = self._release(cur, row, True)
        cur.execute("""UPDATE guild.mc_delegations SET reservation=%s::jsonb, facts=COALESCE(facts, '{}'::jsonb)
              || CASE WHEN %s THEN jsonb_build_object('released_at', clock_timestamp()) ELSE '{}'::jsonb END
            WHERE request_id=%s""", (json.dumps(res), res["state"] == "settled", str(row["request_id"])))

    # ── effects (R3.2-a, §4.5) ──
    def effect(self, runner_id, request_id, generation, effect, *, protocol=2) -> dict:
        """One step of an effect, run inside the authority and mapped one to one onto the existing receipts and grants:
          prepare    GrantStore.prepare_effect: one of the effect grant's operations used, and the receipt prepared
          claim      ReceiptStore.claim (execute): a receipt lease of at most 30 s, never past the item's own lease
          dispatch   ReceiptStore.dispatch: committed before anything is sent; refused on an expired receipt lease
          finish     ReceiptStore.finish: verified or failed with the target's answer as evidence, or unknown
          reconcile  ReceiptStore.claim (reconcile): dispatched becomes unknown, with a lease to finish it from readback
        A new effect (prepare, claim, dispatch) needs the owner host at the item's current generation, its lease
        running, its Stop unpressed and its own grant still valid, so a revocation refuses a dispatch at once (R4.3-e).
        finish and reconcile only record what became of an effect dispatched under a lease that was current then, so
        they are taken whatever the item, its grant or its lease are now: only the owner's identity, and the
        receipt's own lease (ReceiptStore), are checked (R5.1-c). Every step is an authority transaction, so with no
        authority there is no effect (R4.5-e); an unknown receipt can never be claimed to execute again (R4.5-a).
        -> {state: ok, op, operation_id, receipt_state, lease?: {receipt_generation, token, mode, lease_until}}
         | {state: already_applied, op, operation_id} (a finish replayed, DUP-5) | {state: refused, op, operation_id, code}"""
        _need(self._protocol(protocol, ()), "protocol_invalid")
        rid = _uuid_text(request_id, "request_id_invalid")
        _need(type(generation) is int and not isinstance(generation, bool) and 0 <= generation < 2**31,
              "generation_invalid")
        _need(type(runner_id) is str and _ID.match(runner_id), "runner_id_invalid")
        eff = check_effect(effect)
        from core.minimoi_actions.context import ActionContractError
        from core.minimoi_actions.requests import ToolRequest
        try:
            req = ToolRequest(eff["request"]["operation"], eff["request"]["operation_version"],
                              json.dumps(eff["request"]["arguments"]).encode("utf-8"))
        except ActionContractError:
            raise AuthorityRefused("effect_invalid") from None
        op, op_id = eff["op"], effect_operation_id(rid, eff["key"])
        answer = {"op": op, "operation_id": op_id}
        with self._tx() as cur:
            row = self._item(cur, rid)
            if row is None or row["route"] is None:
                return {"state": "refused", **answer, "code": "unknown_request" if row is None else "protocol_mismatch"}
            host_id = self._host_id(cur, runner_id)
            body = {"operation_id": op_id, "key": eff["key"], "capability": req.operation}
            code, out = self._effect_refusal(cur, row, runner_id, generation, op, op_id), None
            if code is None:
                cur.execute("SAVEPOINT mc_effect")
                try:
                    out = self._effect_step(cur, row, runner_id, eff, req, op_id)
                    cur.execute("RELEASE SAVEPOINT mc_effect")
                except ActionContractError as exc:
                    cur.execute("ROLLBACK TO SAVEPOINT mc_effect")
                    code = str(exc)
                    if op == "finish" and self._receipts.read(cur.connection, self.principal, self.environment,
                                                              op_id)["state"] == eff["state"]:
                        return {"state": "already_applied", **answer}      # this outcome is already recorded
            if code is not None:
                self._event(cur, "effect.refused", item=rid, generation=row["generation"], host_id=host_id,
                            once=_once("effect_refused", op_id, op, generation, code),
                            body={**body, "op": op, "code": code, "generation": generation})
                return {"state": "refused", **answer, "code": code}
            said = {**(out.get("lease") or {}), **out}                    # never the lease's token
            self._event(cur, "effect." + _EFFECT_EVENT[op], item=rid, generation=row["generation"], host_id=host_id,
                        once=_once("effect_prepared", op_id) if op == "prepare" else None,
                        body={**body, **{k: said[k] for k in ("receipt_state", "receipt_generation", "mode",
                                                              "lease_until", "recorded_as") if k in said}})
            return {"state": "ok", **answer, **out}

    def _effect_refusal(self, cur, row, runner_id, generation, op, op_id) -> str | None:
        if op in ("prepare", "claim", "dispatch"):          # a new effect: the owner, now, under its lease and grant
            code = self._owner_refusal(row, generation, runner_id)
            if code is None and row["cancel_requested"]:
                code = "cancel_requested"
            if code is None:
                code = self._grant_refusal(cur, row)          # the item's own grant: a revocation refuses at once
            if code is not None:
                return code
        elif row["runner_id"] != runner_id:                  # recording an outcome: the owner, whatever else changed
            return "not_owner"
        if op != "prepare" and op_id not in self._operations(cur, row["request_id"]):
            return "operation_not_of_item"
        return None

    def _effect_step(self, cur, row, runner_id, eff, req, op_id) -> dict:
        """The ReceiptStore/GrantStore call itself. The effect's context is built here from the authority's own record
        of the item, never from the host: the item's work (its card, or its job) is the context's conversation and
        work id, the item's request id its turn, its route the budget account. A new effect's context expires with
        the item's lease; a finish's is bounded by the receipt's own lease, which ReceiptStore checks. So an effect
        grant names that work, the item's worker and EFFECT_POLICY."""
        from core.minimoi_actions.context import ActionContext, ActionContractError
        from core.minimoi_actions.grants import GrantExecution
        from core.minimoi_actions.receipts import EffectIntent, EffectLease
        conn, p, e, op = cur.connection, self.principal, self.environment, eff["op"]
        work = row["card_id"] or row["job_id"]
        expires = row["lease_until"] if op in ("prepare", "claim", "dispatch") else \
            row["now"] + timedelta(seconds=RECEIPT_LEASE_S)
        context = ActionContext(p, row["actor"], e, work, str(row["request_id"]),
                                f"{row['request_id']}/{row['generation']}", max(1, row["generation"]),
                                expires or row["now"], EFFECT_POLICY, "host-runtime", "recorded", row["route"],
                                (eff["grant_id"],), work_id=work)
        intent = EffectIntent(op_id, req, grant_id=eff["grant_id"])
        execute = GrantExecution(self._grants, context, intent, EFFECT_POLICY)
        if op == "prepare":
            return {"receipt_state": self._grants.prepare_effect(conn, context, intent,
                                                                 current_policy_version=EFFECT_POLICY)["state"]}
        if op in ("claim", "reconcile"):
            if op == "claim":                  # the receipt's lease never outlives the item's
                left = int((row["lease_until"] - row["now"]).total_seconds())
                lease = self._receipts.claim(conn, p, e, op_id, runner_id, mode="execute",
                                             seconds=max(1, min(RECEIPT_LEASE_S, left)), authority=execute)
            else:
                lease = self._receipts.claim(conn, p, e, op_id, runner_id, mode="reconcile", seconds=RECEIPT_LEASE_S)
            return {"receipt_state": self._receipts.read(conn, p, e, op_id)["state"],
                    "lease": {"receipt_generation": lease.generation, "token": lease.token, "mode": lease.mode,
                              "lease_until": _iso(self._receipts.lease_until(conn, p, e, op_id))}}
        lease = EffectLease(p, e, op_id, eff["lease"]["receipt_generation"], runner_id, eff["lease"]["token"],
                            eff["lease"]["mode"])
        if op == "dispatch":
            self._receipts.dispatch(conn, lease, authority=execute)
            return {"receipt_state": "dispatched",
                    "lease_until": _iso(self._receipts.lease_until(conn, p, e, op_id))}   # the target refuses after it
        refs = ()
        if eff["evidence"] is not None:        # the target's answer, recorded in the audit and cited by its id
            refs = (str(uuid.uuid4()),)
            self._event(cur, "effect.evidence", item=row["request_id"], generation=row["generation"], event_id=refs[0],
                        body={"operation_id": op_id, "reported_by": runner_id, **eff["evidence"]})
        try:
            self._receipts.finish(conn, lease, eff["state"], evidence_refs=refs,
                                  authority=execute if lease.mode == "execute" else None)
            return {"receipt_state": eff["state"]}
        except ActionContractError as exc:
            if lease.mode != "execute" or not str(exc).startswith("grant_"):
                raise
        # The effect's grant changed after the dispatch (revoked, expired): the outcome is recorded all the same, the
        # way a readback is (unknown, then a reconcile lease, then the outcome), under this same live receipt lease.
        self._receipts.finish(conn, lease, "unknown")
        again = self._receipts.claim(conn, p, e, op_id, runner_id, mode="reconcile", seconds=RECEIPT_LEASE_S)
        self._receipts.finish(conn, again, eff["state"], evidence_refs=refs)
        return {"receipt_state": eff["state"], "recorded_as": "readback"}

    # ── the authority's own liveness (R5.2-a, R2.4-b) and the sweeper (§5.1, §5.2) ──
    def authority_beat(self) -> dict | None:
        """The authority records that it is serving, on a timer of its own (run_liveness), whatever hosts or the
        sweeper are doing. A gap in these beats longer than gap_threshold_s is the authority's own outage: it was down
        or suspended, nobody could act, and it is recorded as authority.suspended {from, to} with every open grace
        deadline moved on by its length. Returns that outage, or None."""
        _need(self.sweeps, "sweeper_not_configured")
        with self._tx() as cur:
            return self._alive(cur)

    def run_liveness(self, stop, *, log=None) -> None:
        """authority_beat every sweep_s until ``stop`` is set. S1-H runs it in the authority's process, beside and apart
        from run_sweeper, so a sweeper that dies leaves the authority's liveness intact."""
        self._loop(self.authority_beat, stop, log, "authority liveness beat")

    def run_sweeper(self, stop, *, log=None) -> None:
        """sweep every sweep_s until ``stop`` (a threading.Event) is set."""
        self._loop(self.sweep, stop, log, "sweeper pass")

    def _loop(self, step, stop, log, what) -> None:
        """A failed step is logged by its type only and the loop goes on; a step that can't run leaves its row old,
        which is what witnesses read."""
        _need(self.sweeps, "sweeper_not_configured")
        while not stop.is_set():
            try:
                step()
            except Exception as exc:
                (log or _log).warning("%s failed (%s)", what, type(exc).__name__)
            stop.wait(self.authority.sweeper.sweep_s)

    def _account_outage(self, conn):
        """Before a judgement of grace: an outage the authority has not recorded yet (its liveness older than the gap
        threshold) is recorded now, in a transaction of its own taken before any item is locked, so no judgement
        waits on the liveness timer or the sweeper having run first."""
        with self._txn(conn) as cur:
            cur.execute("""SELECT seen_at < clock_timestamp() - %s * interval '1 second' AS stale
                FROM guild.mc_delegation_runners WHERE runner_id=%s""",
                        (self.authority.sweeper.gap_threshold_s, self._alive_id))
            found = cur.fetchone()
            if found is None or found["stale"]:
                self._alive(cur)

    def _alive(self, cur) -> dict | None:
        me, threshold = self._alive_id, self.authority.sweeper.gap_threshold_s
        cur.execute("""INSERT INTO guild.mc_delegation_runners (runner_id, state, capabilities, seen_at, environment,
            roles, announced, announced_at, last_gap_s, protocol)
            VALUES (%s, 'serving', '[]'::jsonb, clock_timestamp(), %s, '["authority"]'::jsonb, 'online',
                    clock_timestamp(), 0, 2) ON CONFLICT (runner_id) DO NOTHING""", (me, self.environment))
        cur.execute("""SELECT seen_at, environment, clock_timestamp() AS now FROM guild.mc_delegation_runners
            WHERE runner_id=%s FOR UPDATE""", (me,))
        beat = cur.fetchone()
        _need(beat["environment"] == self.environment, "authority_environment_mismatch")
        a, b = beat["seen_at"], beat["now"]
        gap = max(0.0, (b - a).total_seconds())
        suspended = None
        if gap > threshold:
            suspended = {"from": _iso(a), "to": _iso(b), "outage_s": gap}
            self._event(cur, "authority.suspended", body=suspended)
            # Every grace still running when the outage began moves on by the part of the outage that fell inside it:
            # the whole outage for an item already lapsed, the rest of it for one that lapsed (or paused) during it.
            # The items are locked in request_id order, the order revoke and the heartbeat take them (N8).
            cur.execute("""SELECT request_id FROM guild.mc_delegations
                WHERE principal_id=%s AND environment=%s AND route IS NOT NULL AND state IN ('lapsed','paused')
                  AND grace_deadline > %s AND lapsed_since < %s ORDER BY request_id FOR UPDATE""",
                        (self.principal, self.environment, a, b))
            held = [str(r["request_id"]) for r in cur.fetchall()]
            if held:
                cur.execute("""UPDATE guild.mc_delegations
                    SET grace_deadline = grace_deadline + (%(b)s::timestamptz - GREATEST(%(a)s::timestamptz, lapsed_since))
                    WHERE request_id = ANY(%(ids)s::uuid[])
                    RETURNING request_id, generation, grace_deadline,
                              EXTRACT(EPOCH FROM %(b)s::timestamptz - GREATEST(%(a)s::timestamptz, lapsed_since)) AS by_s""",
                            {"a": a, "b": b, "ids": held})
                for r in cur.fetchall():
                    self._event(cur, "item.grace_extended", item=r["request_id"], generation=r["generation"],
                                body={"by_s": float(r["by_s"]), "grace_deadline": _iso(r["grace_deadline"]),
                                      "outage": {"from": suspended["from"], "to": suspended["to"]}})
        cur.execute("UPDATE guild.mc_delegation_runners SET seen_at=%s, last_gap_s=%s WHERE runner_id=%s", (b, gap, me))
        return suspended

    def sweep(self) -> dict:
        """One pass of the sweeper. run_sweeper repeats it every sweep_s; S1-H runs that beside door.handle.
        1. The authority's liveness (its heartbeat is one input to it) and the sweeper's own heartbeat, its age.
        2. Version 1 items: the write-on-read reap moves here unchanged (interrupted: runner_lost), since relay.status
           no longer reaps where a sweeper runs (R3.3).
        3. Version 2 items, one short transaction each, skipping any a caller holds right now (_sweep_item).
        It never writes running: liveness stays derived at read time, so a dead sweeper leaves no item looking live
        (E11): every call judges a lease and a grace by the database clock, whatever the sweeper has written."""
        _need(self.sweeps, "sweeper_not_configured")
        done = {}
        with self._session() as conn:
            with self._txn(conn) as cur:
                suspended = self._alive(cur)
                cur.execute("""INSERT INTO guild.mc_delegation_runners AS r (runner_id, state, capabilities, seen_at,
                    environment, roles, announced, announced_at, last_gap_s, protocol)
                    VALUES (%s, 'sweeping', '[]'::jsonb, clock_timestamp(), %s, '["authority"]'::jsonb, 'online',
                            clock_timestamp(), 0, 2)
                    ON CONFLICT (runner_id) DO UPDATE SET seen_at=EXCLUDED.seen_at,
                      last_gap_s=GREATEST(0, EXTRACT(EPOCH FROM EXCLUDED.seen_at - r.seen_at))""",
                            (self._sweeper_id, self.environment))
            with self._txn(conn) as cur:
                cur.execute("""UPDATE guild.mc_delegations SET state='interrupted', finished_at=clock_timestamp(),
                    result_detail='{"code": "runner_lost"}'::jsonb, lease_until=NULL
                    WHERE principal_id=%s AND environment=%s AND route IS NULL AND state IN ('claimed','running')
                      AND result_event_id IS NULL AND lease_until < clock_timestamp() - %s * interval '1 second'""",
                            (self.principal, self.environment, LEASE_SECONDS))
                done["runner_lost"] = cur.rowcount
            with self._txn(conn) as cur:
                cur.execute("""SELECT request_id FROM guild.mc_delegations
                    WHERE principal_id=%s AND environment=%s AND route IS NOT NULL
                      AND ((state IN ('claimed','running') AND lease_until <= clock_timestamp())
                        OR (state IN ('lapsed','paused') AND grace_deadline <= clock_timestamp())
                        OR (state IN ('claimed','running','lapsed','paused')
                            AND (cancel_requested OR COALESCE(facts, '{}'::jsonb) ? 'ending')))
                    ORDER BY request_id LIMIT %s""", (self.principal, self.environment, SWEEP_BATCH))
                due = [r["request_id"] for r in cur.fetchall()]
            for rid in due:
                with self._txn(conn) as cur:
                    row = self._item(cur, rid, skip=True)
                    moved = [] if row is None else self._sweep_row(cur, row)
                for m in moved:
                    done[m] = done.get(m, 0) + 1
        return {"suspended": suspended, **done}

    def sweeper_status(self) -> dict | None:
        """The sweeper's heartbeat as read (S1-G's sweeper_age_s; a witness's check, R5.6-a). None before its first pass."""
        _need(self.sweeps, "sweeper_not_configured")
        with self._tx() as cur:
            cur.execute("""SELECT seen_at, last_gap_s, clock_timestamp() AS now FROM guild.mc_delegation_runners
                WHERE runner_id=%s""", (self._sweeper_id,))
            r = cur.fetchone()
        if r is None:
            return None
        age = (r["now"] - r["seen_at"]).total_seconds()
        return {"seen_at": _iso(r["seen_at"]), "age_s": age, "last_gap_s": r["last_gap_s"],
                "fresh": age <= self.authority.sweeper.gap_threshold_s}

    def _sweep_row(self, cur, row) -> list:
        """_sweep_item until the item rests, so a lease long past whose grace is past too ends in one pass."""
        moved = []
        while True:
            step = self._sweep_item(cur, row)
            if step is None:
                return moved
            moved.append(step)
            row = self._item(cur, row["request_id"])

    def _sweep_item(self, cur, row) -> str | None:
        """The sweeper's transition for one version 2 item under its lock, or None:
        - its lease still running: nothing (held: nobody may act before it runs out, R5.1-c; the owner sees its own
          Stop), unless a revocation waits only on effects that are now settled: cancelled: grant_revoked;
        - the owner's will (a Stop, a revoked or expired grant) and no receipt open: it ends so (cancelled, expired);
        - its lease past, otherwise: lapsed with its cause, owner unchanged, grace from the owner host's
          unreachable_grace_s; it waits there for its owner to finish or read back an open effect (SF5);
        - lapsed or paused past its grace deadline: with a receipt still open, unknown and one decision listing it;
          otherwise interrupted (owner_unreachable, or owner_stopped_past_grace for a paused item), or as the
          owner's will says. A reservation is kept while children are not confirmed stopped."""
        if row["route"] is None or row["state"] not in ("claimed", "running", "lapsed", "paused"):
            return None
        owned = row["state"] in ("claimed", "running")
        live = owned and row["lease_until"] > row["now"]
        if live and not (row["facts"] or {}).get("ending"):
            return None
        ending, open_ops = self._ending(cur, row), self._open_receipts(cur, row["request_id"])
        if ending is not None and not open_ops:
            self._close(cur, row, ending[0], {"code": ending[2], **({"condition": ending[1]} if ending[1] else {})},
                        children_stopped=False)
            return ending[0]
        if live:
            return None
        if owned:
            return self._lapse(cur, row, row["lease_until"])
        if not self._grace_ended(cur, row):
            return None
        end = ending or self._grace_end(row)
        if open_ops:
            self._decide(cur, row, "effect_unknown", open_ops, [], end)
            return "unknown"
        self._close(cur, row, end[0], {"code": end[2]}, children_stopped=False)
        return end[0]

    def _lapse_written(self, cur, row):
        """A lease that ran out before the sweeper saw it is written as a lapse, with its cause, before anything else
        acts on the item, so the audit never shows a generation rise or an end without the lapse behind it."""
        if row["state"] in ("claimed", "running") and row["lease_until"] is not None \
                and row["lease_until"] <= row["now"]:
            self._lapse(cur, row, row["lease_until"])
            row = self._item(cur, row["request_id"])
        return row

    def _lapse(self, cur, row, since, *, paused=False) -> str:
        """lapsed (the lease ran out at ``since``) or paused (its owner announced a stop at ``since``): owner unchanged,
        the lease ended, a grace deadline from the owner's grace (none stated: none given) less any outage."""
        deadline, down, grace_s, owner = self._grace(cur, row, since)
        state = "paused" if paused else "lapsed"
        cause = "owner_stopped" if paused else lapse_cause(down, None if owner is None else owner["seen_at"], since)
        cur.execute("""UPDATE guild.mc_delegations SET state=%s, lapsed_since=%s, lapse_cause=%s, grace_deadline=%s,
            lease_until=LEAST(lease_until, %s) WHERE request_id=%s""",
                    (state, since, cause, deadline, since, str(row["request_id"])))
        self._event(cur, f"item.{state}", item=row["request_id"], generation=row["generation"],
                    host_id=None if owner is None else owner["host_id"],
                    once=_once(state, row["request_id"], row["generation"]),
                    body={"cause": cause, "runner_id": row["runner_id"], "lapsed_since": _iso(since),
                          "grace_s": grace_s, "grace_deadline": _iso(deadline),
                          "lease_until": _iso(min(row["lease_until"], since))})
        return state

    # ── the owner's acts on a held-up item (R5.1-c, R5.1-d, R4.3-e); the owner path that calls them is S1-G's ──
    def reassign(self, request_id, runner_id, *, by, note) -> dict:
        """Reassign an item whose lease has run out (R5.1-d). One transaction sets the new owner and generation + 1.
        It starts no lease and no grace: the item waits, queued, claimable only by the named runner, and its lease
        starts when that runner's claim picks it up (_hand_over), so it never reads as running before then. Its
        checkpoint lives on the old host, so the new owner starts from the item's inputs; an effect already verified
        is found again by its operation id and never sent again. A card step's retry waiting for its owner (R4.3-d)
        is reassigned the same way, keeping what remains of its reservation. Refused while the lease still runs (held:
        nobody may act), while any of its receipts is dispatched or unknown (decide those first), and to another host
        unless the authority allows it: a Mac-only build refuses cross-host takeover outright (R2.4-c). Nothing else
        ever moves an item (R4.1-c). An item queued past its deadline is S1-G's overdue flag (§7.7); nothing here ends
        it."""
        _need(self._v2, "protocol_not_enabled")
        rid = _uuid_text(request_id, "request_id_invalid")
        _need(type(runner_id) is str and _ID.match(runner_id), "runner_id_invalid")
        by, note = _owner_act(by, note)
        with self._tx() as cur:
            row = self._item(cur, rid)
            _need(row is not None and row["route"] is not None, "unknown_request")
            _need(row["state"] in ("claimed", "running", "lapsed", "paused", "unknown") or _awaiting(row),
                  "item_not_reassignable")
            _need(row["state"] not in ("claimed", "running") or row["lease_until"] <= row["now"], "lease_current")
            # A revocation that is ending the item, or the end of its card, leaves the owner one act: end (NSF1). A
            # reassignment would clear that mark and hand the work out again.
            _need(not self._card_finished(cur, row), "card_ended")
            _need(not (row["facts"] or {}).get("ending"), "item_ending")
            row = self._lapse_written(cur, row)
            res = dict(row["reservation"] or {})
            _need("reserved" in res and res.get("state") is None, "reservation_missing")
            _need(not self._open_receipts(cur, rid), "receipts_open")
            target = self._runner(cur, runner_id)
            _need(target is not None and target["protocol"] == 2 and target["environment"] == self.environment
                  and "worker" in (target["roles"] or []), "target_invalid")
            needs = {f"actor:{row['actor']}", f"scope:{row['scope']}", f"repo:{row['repository']}",
                     *(row["required_capabilities"] or [])}
            _need(needs <= set(target["capabilities"] or []), "target_not_capable")
            old_host = self._host_id(cur, row["runner_id"])
            _need(target["host_id"] == old_host or self.authority.reassign_across_hosts, "cross_host_refused")
            g = row["generation"]
            cur.execute("""UPDATE guild.mc_delegations SET state='queued', generation=%s, runner_id=%s,
                lease_until=NULL, lapsed_since=NULL, lapse_cause=NULL, grace_deadline=NULL, checkpoint=NULL,
                result_detail=NULL, cancel_requested=FALSE, reservation=%s::jsonb,
                facts=(COALESCE(facts, '{}'::jsonb) - 'picked_up_at' - 'leased_at' - 'ending' - 'retry')
                      || jsonb_build_object('reassigned', %s::jsonb)
                WHERE request_id=%s""",
                (g + 1, runner_id, json.dumps({**res, "generation": g + 1}),
                 json.dumps({"from_runner_id": row["runner_id"], "from_generation": g, "by": by}), rid))
            self._event(cur, "item.reassigned", item=rid, generation=g + 1, host_id=target["host_id"], source="owner",
                        once=_once("reassigned", rid, g),
                        body={"from_runner_id": row["runner_id"], "from_host_id": old_host, "runner_id": runner_id,
                              "host_id": target["host_id"], "from_generation": g, "generation": g + 1,
                              "prior_state": row["state"], "prior_checkpoint": row["checkpoint"], "by": by,
                              "note": note})
            self._resolve_decision(cur, row, "reassigned", by)
            return {"state": "reassigned", "generation": g + 1, "runner_id": runner_id}

    def end(self, request_id, *, by, note) -> dict:
        """The owner's "end" for an item no host may continue on its own: lapsed, paused or unknown, or claimed with
        its lease already past (R5.1-c). It ends interrupted: owner_decision, workspace and checkpoint kept, its
        reservation kept until its children are confirmed stopped. Refused while its lease runs (Stop is for that).
        With an effect's outcome still open, the item first becomes unknown with one decision listing it, so the owner
        decides knowing that; "end" on an unknown item is that decision (DUP-6), and its detail keeps the open
        operations, which a re-admission must not send again (open_operations)."""
        _need(self._v2, "protocol_not_enabled")
        rid = _uuid_text(request_id, "request_id_invalid")
        by, note = _owner_act(by, note)
        with self._tx() as cur:
            row = self._item(cur, rid)
            _need(row is not None and row["route"] is not None, "unknown_request")
            _need(row["state"] in ("claimed", "running", "lapsed", "paused", "unknown") or _awaiting(row),
                  "item_not_endable")
            _need(row["state"] not in ("claimed", "running") or row["lease_until"] <= row["now"], "lease_current")
            row = self._lapse_written(cur, row)
            open_ops = self._open_receipts(cur, rid)
            if open_ops and row["state"] != "unknown":
                d = self._decide(cur, row, "effect_unknown", open_ops, [], ("interrupted", None, "owner_decision"))
                return {"state": "unknown", "decision_id": d["decision_id"], "operations": open_ops}
            self._close(cur, row, "interrupted", {"code": "owner_decision", "by": by, "note": note, "workspace": "kept",
                                                  **({"open_operations": open_ops} if open_ops else {})},
                        children_stopped=_awaiting(row), source="owner")     # never ran under a pickup it awaits
            self._resolve_decision(cur, row, "end", by)
            return {"state": "interrupted", "code": "owner_decision"}

    def revoke(self, grant_id, *, by, note) -> dict:
        """Revocation end to end (R4.3-e, E13). One transaction: every open item under the grant is locked first (items
        before grants, as every path takes them), then GrantStore.revoke. From then on renew is false and any new
        dispatch is refused at once. Each item ends cancelled: grant_revoked, and a late result is refused, unless an
        effect of its outcome is still open: then it waits, marked, for its owner to finish or read it back (a finish
        is always taken), and ends once none is open (the sweeper, reconcile), or goes to the owner as a decision at
        its grace end. A claimed item keeps its reservation until its children are confirmed stopped (R8.3-c).
        Revoking a card's grant ends the card (card.ended, grant_revoked, once): every open step of it is treated so,
        a step admitted under its own grant (a cross-vendor review) included, a decision waiting to admit one of its
        steps is withdrawn, and nothing is written or admitted for the card again."""
        _need(self._v2, "protocol_not_enabled")
        gid = _uuid_text(grant_id, "grant_id_invalid")
        by, note = _owner_act(by, note)
        from core.minimoi_actions.context import ActionContractError
        with self._tx() as cur:
            cur.execute("""SELECT DISTINCT body->>'card_id' AS card_id FROM guild.mc_delegation_events
                WHERE principal_id=%s AND environment=%s AND kind='card.admitted' AND body->>'grant_id'=%s
                ORDER BY 1""", (self.principal, self.environment, gid))
            cards = [r["card_id"] for r in cur.fetchall()]
            cur.execute("""SELECT *, clock_timestamp() AS now FROM guild.mc_delegations
                WHERE principal_id=%s AND environment=%s AND route IS NOT NULL
                  AND (grant_id=%s OR (card_id = ANY(%s) AND facts ? 'card'))
                  AND state IN ('proposed','queued','claimed','running','lapsed','paused','unknown')
                ORDER BY request_id FOR UPDATE""", (self.principal, self.environment, gid, cards))
            rows = cur.fetchall()
            try:
                self._grants.revoke(cur.connection, self.principal, self.environment, gid)
            except ActionContractError as exc:
                raise AuthorityRefused(str(exc)) from None
            ended, waiting = [], []
            for row in rows:
                open_ops = self._open_receipts(cur, row["request_id"])
                if open_ops or row["state"] == "unknown":
                    cur.execute("""UPDATE guild.mc_delegations SET facts=COALESCE(facts, '{}'::jsonb)
                        || jsonb_build_object('ending', %s::jsonb) WHERE request_id=%s""",
                                (json.dumps({"state": "cancelled", "code": "grant_revoked", "by": by}),
                                 str(row["request_id"])))
                    self._event(cur, "item.ending", item=row["request_id"], generation=row["generation"],
                                source="owner", once=_once("ending", row["request_id"], row["generation"]),
                                body={"state": "cancelled", "code": "grant_revoked", "open_operations": open_ops})
                    waiting.append(str(row["request_id"]))
                    continue
                row = self._lapse_written(cur, row)       # a lease already past ends after its lapse (N-C4)
                decision = (row["result_detail"] or {}).get("decision_id") if row["state"] == "proposed" else None
                if decision is not None:                  # never left among routine decisions a "yes" approves
                    self._event(cur, "decision.resolved", item=row["request_id"], generation=row["generation"],
                                source="owner", once=_once("decision_resolved", decision),
                                body={"decision_id": decision, "answer": "withdrawn", "code": "grant_revoked",
                                      "by": by})
                self._close(cur, row, "cancelled", {"code": "grant_revoked", "by": by},
                            children_stopped=row["state"] in ("proposed", "queued"), source="owner")
                ended.append(str(row["request_id"]))
            for card_id in cards:                         # once per card, whatever already ended it
                self._event(cur, "card.ended", source="owner", once=_once("card_done", card_id),
                            body={"card_id": card_id, "state": "revoked", "code": "grant_revoked", "grant_id": gid,
                                  "by": by})
            self._event(cur, "grant.revoked", source="owner", body={"grant_id": gid, "by": by, "note": note,
                                                                     "items": ended, "waiting": waiting,
                                                                     "cards": cards})
            return {"state": "revoked", "grant_id": gid, "items": ended, "waiting": waiting, "cards": cards}

    def _resolve_decision(self, cur, row, answer, by):
        """An owner act that settles an unknown item's queued decision records that it did."""
        decision = (row["result_detail"] or {}).get("decision_id") if row["state"] == "unknown" else None
        if decision is not None:
            self._event(cur, "decision.resolved", item=row["request_id"], generation=row["generation"], source="owner",
                        once=_once("decision_resolved", decision), body={"decision_id": decision, "answer": answer,
                                                                        "by": by})


_EFFECT_EVENT = {"prepare": "prepared", "claim": "claimed", "dispatch": "dispatched", "finish": "finished",
                 "reconcile": "reconciling"}


def _awaiting(row) -> bool:
    """An item waiting, queued, for its named runner's pickup: reassigned (R5.1-d), or a card step's retry (R4.3-d)."""
    facts = row["facts"] or {}
    return row["state"] == "queued" and row["runner_id"] is not None and ("reassigned" in facts or "retry" in facts)


def _remaining(row) -> dict:
    """What remains of an item's one reservation, for a resumed or retried child's cap (S1-D enforces it, less what
    its own durable cost record says earlier generations used): {reserved, unit}."""
    res = row["reservation"] or {}
    return {"reserved": res.get("reserved"), "unit": res.get("unit")}


def _report_digest(status, artifacts, detail, output_text, route, used) -> str:
    """One digest of everything a host reported in a version 2 result (status, artifacts, detail with its exit code,
    output, route, usage), as it sent them: a replay under the same event id must match it."""
    doc = {"status": status, "artifacts": artifacts or [], "detail": detail or {},
           "output_text": output_text if isinstance(output_text, str) else None, "route": route,
           "usage": {**used, "amount": None if used["amount"] is None else float(used["amount"])}}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
                          .encode("utf-8")).hexdigest()


def _attempt_body(status, route, used, attempt, detail, artifacts, output_text) -> dict:
    """A failed attempt's report as attempt.failed records it: what a replay of it must match exactly. ``report`` is
    the same whole-report digest result.accepted records (status, detail, usage, artifacts, output), so one
    exact-match rule holds on both paths (NSF2); the rest is kept readable for the audit."""
    return {"status": status, "route": route,
            "usage": {**used, "amount": None if used["amount"] is None else float(used["amount"])},
            "attempt": attempt, "exit_code": (detail or {}).get("exit_code"),
            "artifacts": [{"kind": a["kind"], "ref": a["ref"], "sha256": a["sha256"]}
                          for a in card_mod.recorded_outputs(artifacts)],
            "report": _report_digest(status, artifacts, detail, output_text, route, used)}


def _attempt(row):
    """A card step's attempt (1, then one more per retry after a definite failure, R4.3-d); None for other items."""
    return ((row["facts"] or {}).get("card") or {}).get("attempt")


def _stop(condition, code, state=None) -> dict:
    return {"answer": "stop", "condition": condition, "code": code, "state": state}


def _owner_act(by, note) -> tuple:
    _need(type(by) is str and _ID.match(by), "owner_invalid")
    if not isinstance(note, str) or not note.strip():
        raise AuthorityRefused("say_why")
    return by, note.strip()[:300]
