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
        seen_at is the host's last contact; a change of facts is audited. (Census batches ride here later.)
  claim(runner_id, capabilities, protocol=2) -> {envelope, route, unit, reserved, lease_until, card_id, step} | None
        nothing for a host with unreconciled items; the item's capabilities (actor, scope, repository and its
        required_capabilities) within the host's; its grant read through GrantStore; the route gate; its spend
        reserved in the route's unit in the same transaction as the lease. A refusal is written to the item.
  progress(request_id, generation, phase, protocol=2, runner_id, event_id, at, checkpoint)
        -> {state: applied|superseded|already_applied|refused}
        stores phase, progress_at and checkpoint; an older event never overwrites a newer one; never touches the
        lease or heartbeat_at
  cancel_requested(request_id, generation, protocol=2) -> bool         unchanged
  renew(request_id, generation, protocol=2, runner_id) -> bool          also False when the grant is revoked or expired
  result(request_id, generation, *, ..., protocol=2, runner_id, at, route, usage={amount, unit, children_stopped})
        settles the reservation with the observed usage; a route other than the reserved one is refused; unknown
        usage closes the route until a recorded resolution (resolve_usage_hold)
Only the owner host of the current generation may progress, renew or report (R4.4). Every version 2 transition writes
one audit event (guild.mc_delegation_events, R7.6-d); its source is the host for what a host reports (facts, progress,
an accepted result), the owner for an owner's act, and the authority for what it decides. Without a version 2
configuration the store runs exactly today's statements; with one, version 1 calls keep their meaning and touch only
version 1 items. Version 2's two new calls, reconcile and effect, are not here yet.

Every connection is used for one transaction and closed. A stale generation, a lapsed lease or a
cancelled job can never record a completion.
"""
from __future__ import annotations

import json
import math
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from core.mc_delegation_runner import envelope as env_mod

LEASE_SECONDS = 120
RESULT_STATES = ("completed", "failed", "refused", "timed_out", "cancelled", "output_limit", "interrupted")
OUTPUT_MAX = 64 * 1024

# ── version 2 ──────────────────────────────────────────────────────────────
AUTHORITY_SCHEMA = "minimoi.authority.v1"
EVENT_SCHEMA = "minimoi.runtime.event.v1"
UNITS = ("usd", "usd_notional", "minutes", "test_units")
TEST_FAMILY = ("fakework", "fakejudge")       # R6.7-l: the test routes carry no key, and acceptance runs on them
ROLES = ("authority", "worker", "witness", "watch:staging")
V2_ACTORS = ("claude-code", "codex", "fakework")
CLAIM_SCAN = 64            # a claim examines at most this many queued items, oldest first, before answering None
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
    per_day: Decimal | None            # None: no daily cap is configured, so the route admits nothing
    per_item: Decimal | None           # None: likewise
    supervised_only: bool | None       # None: not stated, so treated as supervised-only


@dataclass(frozen=True)
class Authority:
    environment: str
    protocols: frozenset
    launch_closure: str                # 'pending' until the owner records both gating N5 items (R6.7-l)
    lease_s: int | None
    routes: MappingProxyType

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
                                                  "routes", "note"}, "authority_config_invalid")
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
                  and set(r) <= {"unit", "per_day", "per_item", "supervised_only", "note"}, "authority_route_invalid")
            _need(r.get("unit") in UNITS, "authority_route_unit_invalid")
            _need(r.get("supervised_only") is None or type(r.get("supervised_only")) is bool, "authority_route_invalid")
            _need(type(r.get("note", "")) is str and len(r.get("note", "")) <= 1000, "authority_route_invalid")
            per_day, per_item = (None if r.get(k) is None else _amount(r[k], zero=True) for k in ("per_day", "per_item"))
            _need(per_day is None or per_item is None or per_item <= per_day, "authority_route_caps_invalid")
            if doc["environment"] == "prod":           # test values never in production configuration
                _need(not in_test_family(name) and r["unit"] != "test_units", "authority_test_route_in_production")
            parsed[name] = Route(r["unit"], per_day, per_item, r.get("supervised_only"))
        return cls(doc["environment"], frozenset(protocols), closure, lease, MappingProxyType(parsed))


def in_test_family(route: str) -> bool:
    """fakework and fakejudge, and their dotted sub-routes (fakework.api, fakework.subscription)."""
    return route in TEST_FAMILY or route.startswith(tuple(f + "." for f in TEST_FAMILY))


def route_refusal(authority: Authority, route: str) -> dict | None:
    """Why an unattended item on ``route`` may not run, or None. Every runtime item is unattended in this slice."""
    r = authority.routes.get(route)
    if r is None:
        return {"code": "route_unknown"}
    if r.supervised_only is not False:
        return {"code": "route_supervised_only", "reason": "route_configured"}
    if not in_test_family(route) and authority.launch_closure != "verified":
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
    _need(type(value) in (int, float) and math.isfinite(value) and (value >= 0 if zero else value > 0)
          and value <= 10**6, "amount_invalid")
    d = Decimal(str(value))
    _need(d == d.quantize(Decimal("0.0001")), "amount_invalid")
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


def parse_at(value) -> datetime:
    """A host's event time: ISO 8601 with a zone."""
    _need(type(value) is str and len(value) <= 40, "at_invalid")
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise AuthorityRefused("at_invalid") from None
    _need(when.tzinfo is not None, "at_invalid")
    return when


def _uuid_text(value, code) -> str:
    _need(type(value) is str and _UUID.match(value), code)
    return value


def _eid(*parts) -> str:
    """A deterministic event id, so a transition that can be found twice is recorded once."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "mc-delegation-event:" + ":".join(map(str, parts))))


def _iso(value):
    return value.astimezone(timezone.utc).isoformat() if isinstance(value, datetime) else value


class DispatcherStore:
    def __init__(self, connect, *, principal: str, environment: str, authority: Authority | None = None, grants=None):
        """``authority`` (Authority.load) enables version 2 when its protocols say so; a version 2 claim then reads
        each item's grant through ``grants`` (core.minimoi_actions.grants.GrantStore with the delegation catalogue)."""
        self._connect, self.principal, self.environment = connect, principal, environment
        self._v2 = authority is not None and authority.speaks_v2
        if authority is not None and authority.environment != environment:
            raise ValueError("authority_environment_mismatch")
        if self._v2:
            from core.minimoi_actions.grants import GrantStore
            if type(grants) is not GrantStore:
                raise ValueError("authority_grant_store_missing")     # a version 2 claim always reads the grant
        self.authority, self._grants = authority, grants

    @contextmanager
    def _tx(self):
        from psycopg2.extras import RealDictCursor
        conn = self._connect()
        try:
            with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                yield cur
        finally:
            conn.close()

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
        version 2 item, written by the authority: ``queued`` under its grant, or ``proposed`` (waits on a decision)
        without one."""
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
        cfg = self.authority.routes.get(route) if type(route) is str else None
        _need(cfg is not None, "route_unknown")
        amount = _amount(amount)                          # the item's own cap, in its route's unit (§8.1 record 1)
        _need(cfg.per_item is not None, "route_cap_missing")
        _need(amount <= cfg.per_item, "item_cap_exceeded")
        _need(actor in V2_ACTORS and scope in env_mod.SCOPES, "item_invalid")
        caps = check_capabilities(list(required_capabilities or []))
        arts = check_artifacts(list(input_artifacts or []))
        _need(grant_id is None or (type(grant_id) is str and _UUID.match(grant_id)), "grant_id_invalid")
        _need(all(v is None or (type(v) is str and _ID.match(v)) for v in (card_id, step)), "card_invalid")
        created_by = created_by or "owner"
        _need(created_by in ("owner", "authority"), "created_by_invalid")
        state = "queued" if grant_id else "proposed"       # §4.2: queued means admitted under a grant
        with self._tx() as cur:
            cur.execute("""INSERT INTO guild.mc_delegations (request_id, job_id, principal_id, environment, actor, scope,
                repository, ref, brief, cost_cap_usd, deadline, state, route, reservation, grant_id,
                required_capabilities, input_artifacts, card_id, step, created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, clock_timestamp() + %s * interval '1 second', %s,%s,%s::jsonb,%s,
                        %s::jsonb,%s::jsonb,%s,%s,%s)
                ON CONFLICT (job_id) DO NOTHING RETURNING request_id""",
                (request_id_for(job_id), job_id, self.principal, self.environment, actor, scope, repository, ref, brief,
                 cost_cap_usd, int(deadline_s), state, route, json.dumps({"requested": float(amount)}), grant_id,
                 json.dumps(caps), json.dumps(arts), card_id, step, created_by))
            inserted = cur.fetchone()
            if inserted is None:
                return "duplicate"
            self._event(cur, f"item.{state}", item=inserted["request_id"], generation=0,
                        source="owner" if created_by == "owner" else "authority",
                        body={"route": route, "requested": float(amount), "grant_id": grant_id, "card_id": card_id,
                              "step": step, "required_capabilities": caps, "input_artifacts": arts,
                              "created_by": created_by})
            return "accepted"

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
            if row["state"] == "queued":
                cur.execute("""UPDATE guild.mc_delegations SET state='cancelled', cancel_requested=TRUE,
                    finished_at=clock_timestamp() WHERE job_id=%s""", (job_id,))
                if audited:      # never claimed, so nothing was reserved
                    self._event(cur, "item.cancelled", item=row["request_id"], generation=row["generation"],
                                source="owner", body={"code": "owner_stop"})
                return "confirmed"
            if row["state"] in ("claimed", "running"):
                cur.execute("UPDATE guild.mc_delegations SET cancel_requested=TRUE WHERE job_id=%s", (job_id,))
                if audited:
                    self._event(cur, "item.cancel_requested", item=row["request_id"], generation=row["generation"],
                                event_id=_eid("cancel_requested", row["request_id"], row["generation"]),
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
        _need(type(route) is str and _ROUTE.match(route), "route_invalid")
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
    def _event(self, cur, kind, *, item=None, generation=None, host_id=None, source="authority", at=None, body=None,
               event_id=None) -> bool:
        """One audit event (R7.6-d). False when ``event_id`` is already recorded: each event applies once."""
        cur.execute("""INSERT INTO guild.mc_delegation_events (event_id, schema, kind, principal_id, environment,
            item_id, generation, host_id, source, at, body)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, COALESCE(%s::timestamptz, clock_timestamp()), %s::jsonb)
            ON CONFLICT (event_id) DO NOTHING RETURNING event_id""",
            (event_id or str(uuid.uuid4()), EVENT_SCHEMA, kind, self.principal, self.environment,
             None if item is None else str(item), generation, host_id, source, at, json.dumps(body or {})))
        return cur.fetchone() is not None

    def _item(self, cur, request_id, *, lock=True):
        cur.execute("""SELECT *, clock_timestamp() AS now FROM guild.mc_delegations
            WHERE request_id=%s AND principal_id=%s AND environment=%s""" + (" FOR UPDATE" if lock else ""),
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
            # S1-F: the census batch this heartbeat carries is applied here, in the same transaction, as events
            # (kind census.*) whose unique event_id applies each once.
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

    def _claim_v2(self, runner_id, capabilities):
        _need(type(runner_id) is str and _ID.match(runner_id), "runner_id_invalid")
        caps = check_capabilities(capabilities)
        with self._tx() as cur:
            cur.execute("SELECT * FROM guild.mc_delegation_runners WHERE runner_id=%s", (runner_id,))
            host = cur.fetchone()
            # Work goes only to a host whose facts the authority holds: a worker, speaking version 2, online.
            if (host is None or host["protocol"] != 2 or host["environment"] != self.environment
                    or "worker" not in (host["roles"] or []) or host["announced"] != "online"):
                return None
            stuck = self._unreconciled(cur, runner_id)
            if stuck:
                self._event(cur, "host.claim_withheld", host_id=host["host_id"], event_id=_eid("withheld", runner_id, *stuck),
                            body={"runner_id": runner_id, "code": "unreconciled_items", "items": stuck})
                return None
        seen = []
        for _ in range(CLAIM_SCAN):
            # One candidate per transaction: a competing host locks (and judges) the next one, not the same one; a
            # refusal or hold commits on its own; and no transaction ever holds two items' grants, so two claims
            # can never wait on each other.
            with self._tx() as cur:
                cur.execute("""SELECT *, clock_timestamp() AS now FROM guild.mc_delegations
                    WHERE state='queued' AND NOT cancel_requested AND principal_id=%s AND environment=%s
                      AND route IS NOT NULL AND deadline > clock_timestamp() AND NOT (request_id = ANY(%s::uuid[]))
                      AND (required_capabilities || jsonb_build_array('actor:' || actor, 'scope:' || scope,
                                                                      'repo:' || repository)) <@ %s::jsonb
                    ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED""",
                    (self.principal, self.environment, seen, json.dumps(caps)))
                row = cur.fetchone()
                if row is None:
                    return None
                seen.append(str(row["request_id"]))
                claimed = self._admit(cur, row, runner_id, host["host_id"])
            if claimed is not None:
                return claimed
        return None

    def _grant_refusal(self, cur, row) -> str | None:
        """None while the item's grant is current, else the code. Read only, through GrantStore: its locked and
        validated read of the grant row, and its catalogue rule. S1-C's card-step template check belongs here."""
        from core.minimoi_actions.context import ActionContractError
        if row["grant_id"] is None:
            return "grant_missing"
        try:
            grant_row, grant, now = self._grants._locked(cur, str(row["grant_id"]), row["principal_id"],
                                                         row["environment"])
            self._grants._rule(grant)
        except ActionContractError as exc:
            return str(exc)
        if grant_row["revoked_at"] is not None:
            return "grant_revoked"
        if grant.expires_at <= now:
            return "grant_expired"
        if not grant.capability.startswith("delegate."):
            return "grant_not_delegation"
        if grant.work_id is not None and grant.work_id not in (row["card_id"], row["job_id"]):
            return "grant_work_mismatch"
        return None

    def _end(self, cur, row, state, detail, host_id):
        """The authority ends an item it will not hand out (a refusal written to the item)."""
        cur.execute("""UPDATE guild.mc_delegations SET state=%s, result_detail=%s::jsonb, finished_at=clock_timestamp(),
            lease_until=NULL WHERE request_id=%s""", (state, json.dumps(detail), str(row["request_id"])))
        self._event(cur, f"item.{state}", item=row["request_id"], generation=row["generation"], host_id=host_id,
                    event_id=_eid(state, row["request_id"], row["generation"]), body=detail)

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
        code = self._grant_refusal(cur, row)
        if code is not None:                 # W-EXP: expired before the claim, so never claimed
            state = {"grant_expired": "expired", "grant_revoked": "cancelled"}.get(code, "refused")
            self._end(cur, row, state, {"code": code}, host_id)
            return None
        refusal = route_refusal(self.authority, row["route"])
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
        if hold is not None:
            self._hold(cur, row, hold, host_id)
            return None
        generation = row["generation"] + 1
        res = {"requested": float(amount), "reserved": float(amount), "unit": route.unit, "day": day.isoformat(),
               "generation": generation}
        cur.execute("""UPDATE guild.mc_delegations SET state='claimed', generation=%s, runner_id=%s,
            lease_until=clock_timestamp() + %s * interval '1 second', heartbeat_at=clock_timestamp(), reservation=%s::jsonb,
            facts=COALESCE(facts, '{}'::jsonb) || jsonb_build_object('picked_up_at', clock_timestamp())
            WHERE request_id=%s RETURNING lease_until""",
            (generation, runner_id, self.authority.lease_s, json.dumps(res), str(row["request_id"])))
        lease_until = cur.fetchone()["lease_until"]
        self._event(cur, "item.claimed", item=row["request_id"], generation=generation, host_id=host_id,
                    event_id=_eid("claimed", row["request_id"], generation),
                    body={"runner_id": runner_id, "lease_until": _iso(lease_until), "route": row["route"], **res})
        return {"envelope": self._envelope(row, generation, list(row["input_artifacts"] or [])),
                "route": row["route"], "unit": route.unit, "reserved": float(amount), "lease_until": _iso(lease_until),
                "card_id": row["card_id"], "step": row["step"]}

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

    def _owner_refusal(self, row, generation, runner_id, when) -> str | None:
        """Why this owner write is refused (R4.4, R5.1-c), or None. A write made while the lease held, sent later,
        is a replay of a record from before the lapse and is taken; one made after the lease ran out is not."""
        if row["generation"] != generation:
            return "stale_generation"
        if row["runner_id"] != runner_id:
            return "not_owner"
        if row["state"] not in ("claimed", "running", "lapsed"):
            return "item_ended" if row["finished_at"] is not None else "not_owned"
        if when > row["now"] + timedelta(seconds=FUTURE_SKEW_S):
            return "event_time_invalid"
        if row["lease_until"] is None or when >= row["lease_until"]:
            return "lease_lapsed"
        return None

    def _replayed(self, cur, event_id, request_id, kind) -> dict:
        cur.execute("SELECT item_id, kind FROM guild.mc_delegation_events WHERE event_id=%s", (event_id,))
        found = cur.fetchone()
        if found is not None and str(found["item_id"]) == str(request_id) and found["kind"] == kind:
            return {"state": "already_applied", "event_id": event_id}
        return {"state": "refused", "event_id": event_id, "code": "event_id_reused"}

    def _progress_v2(self, request_id, generation, phase, runner_id, event_id, at, checkpoint) -> dict:
        rid = _uuid_text(request_id, "request_id_invalid")
        _need(type(generation) is int and not isinstance(generation, bool), "generation_invalid")
        _need(type(runner_id) is str and _ID.match(runner_id), "runner_id_invalid")
        eid = _uuid_text(event_id, "event_id_invalid")
        _need(type(phase) is str and _WORD.match(phase), "phase_invalid")
        when, cp = parse_at(at), check_checkpoint(checkpoint)
        with self._tx() as cur:
            row = self._item(cur, rid)
            if row is None or row["route"] is None:
                return {"state": "refused", "event_id": eid,
                        "code": "unknown_request" if row is None else "protocol_mismatch"}
            code = self._owner_refusal(row, generation, runner_id, when)
            if code is not None:
                return {"state": "refused", "event_id": eid, "code": code}
            if not self._event(cur, "item.progress", event_id=eid, item=rid, generation=generation,
                               host_id=self._host_id(cur, runner_id), source="host", at=when,
                               body={"phase": phase, **({"checkpoint": cp} if cp else {})}):
                return self._replayed(cur, eid, rid, "item.progress")
            # Phase and checkpoint are each kept newest by the host's event time, so a replayed older record never
            # replaces a newer one (and an older checkpoint still lands if no newer checkpoint is stored).
            newer = row["progress_at"] is None or when > row["progress_at"]
            stored = row["checkpoint"]
            cp_newer = cp is not None and (stored is None or when > parse_at(stored["at"]))
            if not newer and not cp_newer:
                return {"state": "superseded", "event_id": eid}
            live = row["state"] == "claimed" and row["lease_until"] > row["now"]
            cur.execute("""UPDATE guild.mc_delegations SET
                progress_at=CASE WHEN %s THEN %s ELSE progress_at END,
                progress_phase=CASE WHEN %s THEN %s ELSE progress_phase END,
                checkpoint=CASE WHEN %s THEN %s::jsonb ELSE checkpoint END,
                state=CASE WHEN %s THEN 'running' ELSE state END,
                started_at=CASE WHEN %s THEN COALESCE(started_at, clock_timestamp()) ELSE started_at END
                WHERE request_id=%s""",
                (newer, when, newer, phase, cp_newer, json.dumps({**(cp or {}), "at": _iso(when), "event_id": eid}),
                 live, live, rid))
            return {"state": "applied", "event_id": eid}

    def _renew_v2(self, request_id, generation, runner_id) -> bool:
        rid = _uuid_text(request_id, "request_id_invalid")
        _need(type(runner_id) is str and _ID.match(runner_id), "runner_id_invalid")
        with self._tx() as cur:
            row = self._item(cur, rid)
            if (row is None or row["route"] is None or row["generation"] != generation or row["runner_id"] != runner_id
                    or row["state"] not in ("claimed", "running") or row["lease_until"] is None
                    or row["lease_until"] <= row["now"] or row["cancel_requested"]):
                return False
            code = self._grant_refusal(cur, row)          # revoked or expired since the claim: read in this transaction
            if code is not None:
                self._event(cur, "lease.renew_refused", item=rid, generation=generation,
                            host_id=self._host_id(cur, runner_id), event_id=_eid("renew_refused", rid, generation, code),
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
        _need(type(route) is str and _ROUTE.match(route), "route_invalid")
        when, used = parse_at(at), check_usage(usage)
        if status not in RESULT_STATES:
            return {"state": "refused", "event_id": eid, "code": "status_invalid"}
        with self._tx() as cur:
            row = self._item(cur, rid)
            if row is None or row["route"] is None:
                return {"state": "refused", "event_id": eid,
                        "code": "unknown_request" if row is None else "protocol_mismatch"}
            if row["result_event_id"] is not None:
                same = str(row["result_event_id"]) == eid
                return {"state": "already_applied" if same else "refused", "event_id": eid,
                        **({} if same else {"code": "different_result_already_recorded"})}
            res = dict(row["reservation"] or {})
            code = self._owner_refusal(row, generation, runner_id, when)
            if code is None and route != row["route"]:
                code = "route_mismatch"                    # R8.3-c: usage is only ever settled on the reserved route
            if code is None and used["unit"] != res.get("unit"):
                code = "unit_mismatch"
            host_id = self._host_id(cur, runner_id)
            if code is not None:
                self._event(cur, "result.refused", item=rid, generation=row["generation"], host_id=host_id,
                            event_id=_eid("result_refused", eid, code),
                            body={"code": code, "status": status, "route": route, "generation": generation})
                return {"state": "refused", "event_id": eid, "code": code}
            if row["cancel_requested"] and status == "completed":
                status = "cancelled"                       # Stop was pressed: the result is kept, not claimed as done
            if not self._event(cur, "result.accepted", event_id=eid, item=rid, generation=generation, host_id=host_id,
                               source="host", at=when, body={"status": status, "route": route,
                               "usage": {**used, "amount": None if used["amount"] is None else float(used["amount"])}}):
                return {"state": "refused", "event_id": eid, "code": "event_id_reused"}
            res = self._settle(cur, row, res, used, eid, host_id)
            text = output_text[:OUTPUT_MAX] if isinstance(output_text, str) else None
            fact = "jsonb_build_object('reported_at', clock_timestamp())" + (
                " || jsonb_build_object('released_at', clock_timestamp())" if res["state"] == "settled" else "")
            cur.execute("""UPDATE guild.mc_delegations SET state=%s, result_event_id=%s, result_detail=%s::jsonb,
                artifacts=%s::jsonb, output_text=%s, finished_at=clock_timestamp(), lease_until=NULL,
                reservation=%s::jsonb, facts=COALESCE(facts, '{}'::jsonb) || """ + fact + " WHERE request_id=%s",
                (status, eid, json.dumps(detail or {}), json.dumps(artifacts or []), text, json.dumps(res), rid))
            # S1-C: on an accepted card step, the card's next step is written here, in this same transaction.
            return {"state": "accepted", "event_id": eid}

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
