"""The v2 event envelope (v0.6 sections 5 and the exact payload rules; v0.7 Unit 1 kinds).

An envelope is what a caller submits; an event is what the journal persists. Only the helper supplies ``stream``, ``seq``,
``recorded_at``, ``origin`` and ``intent_hash``; a caller that sends any of them is refused. The intent hash covers exactly
the fields the caller means (so a retry with the same intent is a no-op and a changed payload under the same ID is a
conflict) and is frozen in a fixture: see ``INTENT_KEYS`` and ``intent_hash``.

Nothing here reads a file, a clock or the network.
"""
from __future__ import annotations

import hashlib
import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from core.workshop_journal import strictjson
from core.workshop_journal import payment
from utils.credential_scrub import SECRET

VERSION = 2
EVENT_MAX_BYTES = 32 * 1024
TEXT_MAX = 2_000

WORKSHOP_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
STREAM_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}\.[a-z][a-z0-9-]{1,31}$")
ACTOR_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
ITEM_RE = re.compile(r"^(pr|spec|queue|init|host|room|topic):[A-Za-z0-9._-]{1,60}$")
CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
ADAPTER_RE = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
RESOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

DEFAULT_ACTORS = ("robert", "codex", "claude-code", "claude-chat", "grok-cli", "grok-chat", "journeyman", "mc", "host")
STAGES = ("design", "review", "build", "test", "staging", "merged", "production")
# The beta set (v0.7 Unit 1). review, cancel and withdraw are named in v0.6 but not built in the beta.
KINDS = ("request", "receipt", "result", "needs_you", "decision", "claim", "release", "started", "progress", "blocked",
         "done", "next", "health", "delivery", "recovery")
SOFTWARE_ONLY_KINDS = ("delivery", "recovery")           # emitted by software entry points, never through general input
MODEL_OUTCOME_KINDS = ("started", "progress", "result", "done", "blocked")   # where model.actual may be recorded
RECORD_EVENT_KINDS = ("proposed", "approved-direct", "approved-under-mandate", "verified", "rejected", "superseded", "withdrawn")
APPROVAL_KINDS = ("approved-direct", "approved-under-mandate")
ORIGIN_BASES = ("claimed", "adapter", "owner-control")

CLIENT_FORBIDDEN = ("stream", "seq", "recorded_at", "origin", "intent_hash")
INTENT_KEYS = ("event_id", "at", "actor", "kind", "item", "topic", "exchange_id", "recipients", "in_reply_to", "supersedes",
               "stage", "text", "refs", "payload", "authority_ref", "model", "workshop", "stream")
PERSISTED_KEYS = ("v",) + INTENT_KEYS + ("intent_hash", "seq", "recorded_at", "origin")


class SchemaError(ValueError):
    """The envelope or event breaks the contract. ``field`` and ``reason`` are fixed codes; the value is never quoted."""

    def __init__(self, field: str, reason: str):
        super().__init__(f"{field}: {reason}")
        self.field, self.reason = field, reason


# ── scalar rules ───────────────────────────────────────────────────────────────────────────────────────────────────
def _no_control(value: str, field: str, *, allow_text_breaks: bool = False) -> None:
    for ch in value:
        o = ord(ch)
        if o < 0x20 or o == 0x7F:
            if allow_text_breaks and ch in "\n\t":
                continue
            raise SchemaError(field, "control_character")
        if 0xD800 <= o <= 0xDFFF:
            raise SchemaError(field, "invalid_unicode")


def _ident(value: Any, field: str, pattern: re.Pattern) -> str:
    if not isinstance(value, str):
        raise SchemaError(field, "not_text")
    _no_control(value, field)
    if not pattern.fullmatch(value):
        raise SchemaError(field, "bad_format")
    return value


def _free_text(value: Any, field: str, lo: int, hi: int) -> str:
    if not isinstance(value, str):
        raise SchemaError(field, "not_text")
    _no_control(value, field, allow_text_breaks=True)
    if not (lo <= len(value) <= hi):
        raise SchemaError(field, "length")
    if SECRET.search(value):
        raise SchemaError(field, "credential_shaped")
    if payment.found(value):
        raise SchemaError(field, "payment_detail_shaped")
    return value


def uuid_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise SchemaError(field, "not_text")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise SchemaError(field, "bad_uuid") from None
    if str(parsed) != value:
        raise SchemaError(field, "uuid_not_canonical")
    return value


def utc_text(value: Any, field: str) -> str:
    """An ISO 8601 time with an explicit offset, normalised to UTC and written ``...Z``."""
    if not isinstance(value, str):
        raise SchemaError(field, "not_text")
    _no_control(value, field)
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise SchemaError(field, "bad_time") from None
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise SchemaError(field, "time_needs_offset")
    moment = moment.astimezone(timezone.utc)
    stamp = moment.strftime("%Y-%m-%dT%H:%M:%S")
    return stamp + (f".{moment.microsecond:06d}" if moment.microsecond else "") + "Z"


def _int(value: Any, field: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(field, "not_integer")
    if not (lo <= value <= hi):
        raise SchemaError(field, "out_of_range")
    return value


def _number(value: Any, field: str, lo: float, hi: float) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaError(field, "not_number")
    if isinstance(value, float) and not math.isfinite(value):
        raise SchemaError(field, "non_finite")
    if not (lo <= value <= hi):
        raise SchemaError(field, "out_of_range")
    return value


# ── references ─────────────────────────────────────────────────────────────────────────────────────────────────────
REF_TYPES = ("artifact", "shelf", "source", "event", "diff", "test")
REF_AVAILABILITY = ("retained", "pointer_only", "missing", "excluded")
REF_KEYS = ("type", "id", "edition", "sha256", "availability", "locator")


def refs_value(value: Any, field: str) -> list[dict]:
    """Up to 16 structured references. The locator is inert display text, never an instruction to read or fetch."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 16:
        raise SchemaError(field, "too_many_or_not_list")
    out = []
    for ref in value:
        if not isinstance(ref, dict) or set(ref) - set(REF_KEYS):
            raise SchemaError(field, "bad_reference_keys")
        kind = ref.get("type")
        if kind not in REF_TYPES:
            raise SchemaError(field, "bad_reference_type")
        availability = ref.get("availability", "pointer_only")
        if availability not in REF_AVAILABILITY:
            raise SchemaError(field, "bad_availability")
        ident = ref.get("id")
        if not isinstance(ident, str) or not (1 <= len(ident) <= 128):
            raise SchemaError(field, "bad_reference_id")
        _no_control(ident, field)
        sha = ref.get("sha256")
        if sha is not None and (not isinstance(sha, str) or not SHA256_RE.fullmatch(sha)):
            raise SchemaError(field, "bad_sha256")
        if availability == "retained" and sha is None:
            raise SchemaError(field, "retained_needs_hash")
        edition = ref.get("edition")
        if edition is not None:
            edition = _int(edition, field, 1, 10**6)
        if kind == "shelf" and edition is None:
            raise SchemaError(field, "shelf_needs_edition")
        locator = ref.get("locator")
        if locator is not None:
            if not isinstance(locator, str) or len(locator) > 300:
                raise SchemaError(field, "bad_locator")
            _no_control(locator, field)
        out.append({"type": kind, "id": ident, "edition": edition, "sha256": sha, "availability": availability,
                    "locator": locator})
    return out


# ── payloads ───────────────────────────────────────────────────────────────────────────────────────────────────────
class _Ctx:
    def __init__(self, actors: tuple[str, ...] | None):
        self.actors = actors


Validator = Callable[[Any, str, _Ctx], Any]


def _s(lo: int = 1, hi: int = 500, pattern: re.Pattern | None = None) -> Validator:
    def check(value, field, ctx):
        if pattern is not None:
            return _ident(value, field, pattern)
        return _free_text(value, field, lo, hi)
    return check


def _i(lo: int = 0, hi: int = 2**31 - 1) -> Validator:
    return lambda value, field, ctx: _int(value, field, lo, hi)


def _n(lo: float = 0, hi: float = 1e12) -> Validator:
    return lambda value, field, ctx: _number(value, field, lo, hi)


def _enum(*allowed: str) -> Validator:
    def check(value, field, ctx):
        if value not in allowed:
            raise SchemaError(field, "not_allowed")
        return value
    return check


def _bool() -> Validator:
    def check(value, field, ctx):
        if not isinstance(value, bool):
            raise SchemaError(field, "not_boolean")
        return value
    return check


def _uuid() -> Validator:
    return lambda value, field, ctx: uuid_text(value, field)


def _utc() -> Validator:
    return lambda value, field, ctx: utc_text(value, field)


def _actor() -> Validator:
    def check(value, field, ctx):
        _ident(value, field, ACTOR_RE)
        if ctx.actors is not None and value not in ctx.actors:
            raise SchemaError(field, "unregistered_actor")
        return value
    return check


def _list(item: Validator, hi: int) -> Validator:
    def check(value, field, ctx):
        if not isinstance(value, list) or len(value) > hi:
            raise SchemaError(field, "bad_list")
        return [item(v, field, ctx) for v in value]
    return check


def _refs() -> Validator:
    return lambda value, field, ctx: refs_value(value, field)


def _sha() -> Validator:
    return lambda value, field, ctx: _ident(value, field, SHA256_RE)


def _ulid() -> Validator:
    return lambda value, field, ctx: _ident(value, field, ULID_RE)


def _test_summary() -> Validator:
    keys = ("reported", "run", "passed", "failed", "not_run")

    def check(value, field, ctx):
        if not isinstance(value, dict) or set(value) - set(keys):
            raise SchemaError(field, "bad_summary_keys")
        out = {k: None for k in keys}
        for k, v in value.items():
            out[k] = None if v is None else _int(v, field, 0, 10**9)            # counts are reported, never inferred
        return out
    return check


def _counts() -> Validator:
    def check(value, field, ctx):
        if not isinstance(value, dict) or len(value) > 16:
            raise SchemaError(field, "bad_counts")
        return {_ident(k, field, CODE_RE): _number(v, field, 0, 1e12) for k, v in sorted(value.items())}
    return check


# kind -> (required payload keys, optional payload keys); all payload keys are closed.
PAYLOADS: dict[str, tuple[dict[str, Validator], dict[str, Validator]]] = {
    "request": ({"action": _enum("review", "design", "build", "test", "refresh"), "expected_result": _s(1, 1000)},
                {"due_at": _utc(), "resource": _s(pattern=RESOURCE_RE), "required_refs": _list(_sha(), 16)}),
    "receipt": ({"request_id": _uuid(), "recipient": _actor()}, {"native_correlation": _s(1, 256)}),
    "claim": ({"request_id": _uuid(), "resource": _s(pattern=RESOURCE_RE), "generation": _i(), "claimant": _actor()},
              {"lease_until": _utc(), "native_correlation": _s(1, 256)}),
    "release": ({"claim_id": _uuid(), "generation": _i(), "stopped": _bool(), "reason": _s(1, 500)}, {"result_id": _uuid()}),
    "started": ({"action": _s(1, 200)}, {"request_id": _uuid(), "claim_id": _uuid(), "percent": _n(0, 100),
                                         "evidence_refs": _refs()}),
    "progress": ({"action": _s(1, 200)}, {"request_id": _uuid(), "claim_id": _uuid(), "percent": _n(0, 100),
                                          "evidence_refs": _refs()}),
    "result": ({"request_id": _uuid(), "recipient": _actor(),
                "outcome": _enum("completed", "blocked", "declined", "failed", "uncertain"),
                "limitations": _list(_s(1, 500), 32)},
               {"claim_id": _uuid(), "evidence_refs": _refs(), "test_summary": _test_summary()}),
    "needs_you": ({"reason_code": _s(pattern=CODE_RE), "requested_action": _s(1, 500), "incident_id": _uuid()},
                  {"due_at": _utc(), "blocked_request_ids": _list(_uuid(), 16)}),
    "decision": ({"record_event_kind": _enum(*RECORD_EVENT_KINDS), "resolves": _list(_uuid(), 16), "reason": _s(1, 500)},
                 {"predecessor_id": _uuid(), "successor_shelf_id": _ulid(), "authority_evidence_ref": _s(1, 128)}),
    "delivery": ({"request_id": _uuid(), "recipient": _actor(), "attempt_id": _uuid(), "adapter": _s(pattern=ADAPTER_RE),
                  "status": _enum("queued", "accepted", "failed", "unknown", "unavailable")},
                 {"native_correlation": _s(1, 256), "reason_code": _s(pattern=CODE_RE), "next_attempt_at": _utc()}),
    "recovery": ({"manifest_hash": _sha(), "affected_byte_offset": _i(), "action": _enum("completed_lf", "quarantined_truncated")},
                 {"recovered_event_id": _uuid()}),
    "blocked": ({"reason_code": _s(pattern=CODE_RE)},
                {"request_id": _uuid(), "incident_id": _uuid(), "dependency_ids": _list(_uuid(), 16)}),
    "done": ({"outcome": _enum("reported_completed", "reported_failed")}, {"request_id": _uuid(), "evidence_refs": _refs()}),
    "next": ({"next_actor": _actor(), "action": _s(1, 200)}, {"request_id": _uuid()}),
    "health": ({"component": _s(pattern=CODE_RE), "status": _enum("ok", "warn", "failed", "unknown"), "observed_at": _utc()},
               {"reason_code": _s(pattern=CODE_RE), "counts": _counts()}),
}
assert set(PAYLOADS) == set(KINDS)


def payload_value(kind: str, value: Any, ctx: _Ctx) -> dict:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise SchemaError("payload", "not_object")
    required, optional = PAYLOADS[kind]
    unknown = set(value) - set(required) - set(optional)
    if unknown:
        raise SchemaError("payload", "unknown_keys")
    out: dict = {}
    for key, check in required.items():
        if key not in value or value[key] is None:
            raise SchemaError(f"payload.{key}", "required")
        out[key] = check(value[key], f"payload.{key}", ctx)
    for key, check in optional.items():
        out[key] = None if value.get(key) is None else check(value[key], f"payload.{key}", ctx)
    return dict(sorted(out.items()))


# ── model provenance ───────────────────────────────────────────────────────────────────────────────────────────────
def model_value(kind: str, value: Any) -> dict | None:
    """``null`` for software-only events, else ``{provider, requested, actual, evidence_ref, basis}``.

    ``actual``, ``evidence_ref`` and ``basis`` are outcome provenance: allowed only on events that report an outcome,
    never on the request that started the work. An unknown actual stays null."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"provider", "requested", "actual", "evidence_ref", "basis"}:
        raise SchemaError("model", "bad_keys")
    out = {"provider": _ident(value.get("provider"), "model.provider", PROFILE_RE),
           "requested": _ident(value.get("requested"), "model.requested", PROFILE_RE),
           "actual": None, "evidence_ref": None, "basis": None}
    outcome = {k: value.get(k) for k in ("actual", "evidence_ref", "basis")}
    if any(v is not None for v in outcome.values()):
        if kind not in MODEL_OUTCOME_KINDS:
            raise SchemaError("model", "outcome_not_allowed_here")
        if outcome["actual"] is not None:
            out["actual"] = _ident(outcome["actual"], "model.actual", PROFILE_RE)
            if outcome["basis"] not in ("claimed", "adapter-observed"):
                raise SchemaError("model.basis", "not_allowed")
            out["basis"] = outcome["basis"]
        if outcome["evidence_ref"] is not None:
            out["evidence_ref"] = _free_text(outcome["evidence_ref"], "model.evidence_ref", 1, 200)
    return out


def _origin_value(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != {"adapter", "session_ref", "basis"}:
        raise SchemaError("origin", "bad_keys")
    session = value["session_ref"]
    if session is not None:
        if not isinstance(session, str) or not (1 <= len(session) <= 128):
            raise SchemaError("origin.session_ref", "bad_format")
        _no_control(session, "origin.session_ref")
    if value["basis"] not in ORIGIN_BASES:
        raise SchemaError("origin.basis", "not_allowed")
    return {"adapter": _ident(value["adapter"], "origin.adapter", ADAPTER_RE), "session_ref": session, "basis": value["basis"]}


def make_origin(adapter: str, session_ref: str | None = None, basis: str = "claimed") -> dict:
    return _origin_value({"adapter": adapter, "session_ref": session_ref, "basis": basis})


# ── intent ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def normalize_intent(envelope: dict, *, workshop: str, stream: str, actors: tuple[str, ...] | None, resolved_at: str | None,
                     event_id: str | None, allow_software: bool = False, strict_client: bool = True) -> dict:
    """The caller's meaning, normalised: absent optionals become null or empty, times become UTC, payload keys are closed.

    ``resolved_at`` and ``event_id`` fill in what the caller left out (the helper resolves each once and keeps it in the
    prepare receipt). A caller that supplies a helper-only field is refused."""
    if not isinstance(envelope, dict):
        raise SchemaError("envelope", "not_object")
    if strict_client:
        for key in CLIENT_FORBIDDEN:
            if key in envelope:
                raise SchemaError(key, "helper_only_field")
    allowed = set(INTENT_KEYS) | {"v"} | set(CLIENT_FORBIDDEN)
    if set(envelope) - allowed:
        raise SchemaError("envelope", "unknown_fields")
    if "v" in envelope and envelope["v"] != VERSION:
        raise SchemaError("v", "unrecognized_schema_version")
    ctx = _Ctx(actors)
    kind = envelope.get("kind")
    if kind not in KINDS:
        raise SchemaError("kind", "not_allowed")
    if kind in SOFTWARE_ONLY_KINDS and not allow_software:
        raise SchemaError("kind", "software_entry_point_only")
    wk = _ident(envelope.get("workshop", workshop), "workshop", WORKSHOP_RE)
    if wk != workshop:
        raise SchemaError("workshop", "not_this_workshop")
    eid = envelope.get("event_id", event_id)
    if eid is None:
        raise SchemaError("event_id", "required")
    at = envelope.get("at", resolved_at)
    if at is None:
        raise SchemaError("at", "required")
    recipients = envelope.get("recipients") or []
    if not isinstance(recipients, list) or len(recipients) > 8 or len(set(map(str, recipients))) != len(recipients):
        raise SchemaError("recipients", "bad_list")
    recipients = [_actor()(r, "recipients", ctx) for r in recipients]
    if kind == "request" and not recipients:
        raise SchemaError("recipients", "required_on_request")

    def _opt_uuid(name: str):
        v = envelope.get(name)
        return None if v is None else uuid_text(v, name)

    stage = envelope.get("stage")
    if stage is not None and stage not in STAGES:
        raise SchemaError("stage", "not_allowed")
    authority = envelope.get("authority_ref")
    if authority is not None:
        if not isinstance(authority, dict) or set(authority) != {"type", "ref"} or authority["type"] not in ("owner-control", "mandate"):
            raise SchemaError("authority_ref", "bad_shape")
        authority = {"type": authority["type"], "ref": _free_text(authority["ref"], "authority_ref.ref", 1, 128)}
    topic = envelope.get("topic")
    return {"event_id": uuid_text(eid, "event_id"), "at": utc_text(at, "at"), "actor": _actor()(envelope.get("actor"), "actor", ctx),
            "kind": kind, "item": _ident(envelope.get("item"), "item", ITEM_RE),
            "topic": None if topic is None else _ident(topic, "topic", TOPIC_RE),
            "exchange_id": _opt_uuid("exchange_id"), "recipients": recipients, "in_reply_to": _opt_uuid("in_reply_to"),
            "supersedes": _opt_uuid("supersedes"), "stage": stage,
            "text": _free_text(envelope.get("text"), "text", 1, TEXT_MAX), "refs": refs_value(envelope.get("refs"), "refs"),
            "payload": payload_value(kind, envelope.get("payload"), ctx), "authority_ref": authority,
            "model": model_value(kind, envelope.get("model")), "workshop": wk, "stream": _ident(stream, "stream", STREAM_RE)}


def intent_hash(intent: dict) -> str:
    """SHA-256 over the canonical intent. The model is hashed as ``null`` or ``{provider, requested}`` only: what actually
    ran is outcome provenance and is recorded on a later event, so it must not change the identity of the request."""
    doc = {key: intent[key] for key in INTENT_KEYS}
    model = doc["model"]
    doc["model"] = None if model is None else {"provider": model["provider"], "requested": model["requested"]}
    return hashlib.sha256(strictjson.canonical_bytes(doc)).hexdigest()


# ── events ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def build_event(intent: dict, *, seq: int, recorded_at: str, origin: dict) -> dict:
    event = {"v": VERSION, **{key: intent[key] for key in INTENT_KEYS}, "intent_hash": intent_hash(intent), "seq": seq,
             "recorded_at": utc_text(recorded_at, "recorded_at"), "origin": _origin_value(origin)}
    _int(seq, "seq", 1, 10**12)
    if len(strictjson.canonical_bytes(event)) + 1 > EVENT_MAX_BYTES:
        raise SchemaError("event", "too_large")
    return event


def event_bytes(event: dict) -> bytes:
    return strictjson.canonical_bytes(event) + b"\n"


def validate_persisted(row: dict, *, actors: tuple[str, ...] | None = None) -> dict:
    """Check a stored v2 row: closed keys, canonical normalised form, and an intent hash that matches its own fields."""
    if not isinstance(row, dict) or set(row) != set(PERSISTED_KEYS):
        raise SchemaError("event", "bad_keys")
    if row["v"] != VERSION:
        raise SchemaError("v", "unrecognized_schema_version")
    intent = normalize_intent({k: row[k] for k in INTENT_KEYS}, workshop=row["workshop"], stream=row["stream"], actors=actors,
                              resolved_at=None, event_id=None, allow_software=True, strict_client=False)
    if row["intent_hash"] != intent_hash(intent):                  # valid JSON, valid shape, but not the intent that was prepared
        raise SchemaError("intent_hash", "mismatch")
    if build_event(intent, seq=row["seq"], recorded_at=row["recorded_at"], origin=row["origin"]) != row:
        raise SchemaError("event", "not_canonical")
    return row


__all__ = ["VERSION", "KINDS", "STAGES", "DEFAULT_ACTORS", "INTENT_KEYS", "PERSISTED_KEYS", "EVENT_MAX_BYTES", "SchemaError",
           "normalize_intent", "intent_hash", "build_event", "event_bytes", "validate_persisted", "make_origin", "uuid_text",
           "utc_text", "refs_value", "payload_value", "model_value", "SOFTWARE_ONLY_KINDS", "RECORD_EVENT_KINDS", "APPROVAL_KINDS"]
