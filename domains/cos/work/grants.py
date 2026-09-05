"""Turn-scoped, single-use, effect- and resource-specific authority.

A grant is the whole of the authority one turn holds. It names exactly one
effect and exactly one resource, it may be used exactly once, it expires, and
it can only ever *narrow* what deployment configuration already allows. There
is no ambient authority anywhere in this package: an operation with no grant
does nothing at all.

Grants live in memory. They are never written to the canonical tree, so a
process restart invalidates every outstanding one — the fail-closed
direction. The issuer here is in-process and imports no transport, no
runtime and no conversation machinery; a later gate replaces the issuer's
*caller*, not its contract.

Provenance is bound at mint, never chosen at call time. An inline capture's
stored class is the class whoever minted the grant chose, once, for that
single use; a grant whose data class is ``external_public`` may not mint
Robert-source evidence at all. An edit grant additionally pins the digest of
the bytes it authorises, so the authorized bytes cannot be substituted
between mint and commit.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from .confine import normalise_relative
from .envelope import (
    DATA_CLASSES,
    EGRESS_VALUES,
    SOURCE_CONTEXT_CLASSES,
    InvalidRequest,
    WorkError,
    is_identifier,
    is_uuid4,
    require_effect,
    require_identifier,
    require_uuid4,
)
from .retrieval import is_approved_root

#: Live and consumed entries together. One number, because a bound that only
#: covers half the table is not a bound: nothing stops a high mint rate
#: inside one expiry window.
MAX_GRANT_ENTRIES = 1024

#: How many expired entries one mint or verify may retire.
MAX_SWEEP_PER_CALL = 256

DEFAULT_TTL_SECONDS = 120
MAX_TTL_SECONDS = 600

MAX_TURN_ID_CHARS = 128

#: The binding fields a grant may carry, beyond the ones every grant has.
#: Anything outside this set is not a field the mint API knows about.
BINDING_FIELDS: tuple[str, ...] = (
    "work_id",
    "allow_create",
    "operation_id",
    "root_refs",
    "relative_path",
    "source_ref",
    "artifact_ref",
    "supersedes_ref",
    "pending_id",
    "source_class",
    "content_sha256",
    "content_bytes",
    "expected_input_sha256",
    "expected_sha256",
)

#: The exact bound-field sets each effect admits. A grant matches exactly one
#: variant: a field the effect does not use is refused at mint, and a missing
#: required field is refused at mint. An over-broad grant is not a
#: convenience — it is the thing this seam exists to prevent.
GRANT_BINDINGS: dict[str, tuple[frozenset[str], ...]] = {
    "open_work": (
        frozenset({"work_id"}),
        frozenset({"allow_create", "operation_id"}),
    ),
    "attach_source": (
        frozenset({"work_id", "root_refs", "relative_path"}),
        frozenset({"work_id", "source_class", "content_sha256", "content_bytes"}),
    ),
    "search_sources": (frozenset({"work_id", "root_refs"}),),
    "read_source": (
        frozenset({"work_id", "source_ref"}),
        frozenset({"work_id", "root_refs", "relative_path"}),
    ),
    "write_artifact": (frozenset({"work_id", "content_sha256", "content_bytes"}),),
    "request_disposition": (
        frozenset({"work_id"}),
        frozenset({"work_id", "artifact_ref"}),
    ),
    "record_disposition": (frozenset({"work_id", "pending_id"}),),
    "use_robert_edit": (
        frozenset(
            {
                "work_id",
                "supersedes_ref",
                "expected_sha256",
                "root_refs",
                "relative_path",
                "expected_input_sha256",
            }
        ),
        frozenset(
            {
                "work_id",
                "supersedes_ref",
                "expected_sha256",
                "content_sha256",
                "content_bytes",
            }
        ),
    ),
}

#: The effects that reach into an existing work item, and therefore answer to
#: the conversation binding rule.
BOUND_EFFECTS: frozenset[str] = frozenset(
    {
        "attach_source",
        "search_sources",
        "read_source",
        "write_artifact",
        "request_disposition",
        "record_disposition",
        "use_robert_edit",
    }
)

#: Fields compared against the request's *resolved* values at call time.
#: ``expected_input_sha256`` is deliberately absent: it is checked under the
#: work lock against the bytes actually read, so a file changed between
#: verification and read is stale context, not a silent substitution.
_RESOURCE_FIELDS: tuple[str, ...] = (
    "work_id",
    "operation_id",
    "root_refs",
    "relative_path",
    "source_ref",
    "artifact_ref",
    "supersedes_ref",
    "pending_id",
    "content_sha256",
    "content_bytes",
)

_SHA256_LENGTH = 64


class GrantError(WorkError):
    """A grant was missing, spent, expired, or did not fit the request."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True)
class Grant:
    """One turn's authority over one effect and one resource."""

    grant_ref: str
    turn_id: str
    effect: str
    subject: str
    data_class: str
    egress: str
    issued_at: datetime
    expires_at: datetime
    conversation_id: str | None = None
    bindings: Mapping[str, Any] = field(default_factory=dict)

    def bound(self, name: str) -> Any:
        """The value bound for ``name``, or ``None`` when it is unbound."""
        return self.bindings.get(name)

    @property
    def work_id(self) -> str | None:
        return self.bindings.get("work_id")

    @property
    def allow_create(self) -> bool:
        return bool(self.bindings.get("allow_create"))

    @property
    def operation_id(self) -> str | None:
        return self.bindings.get("operation_id")

    @property
    def root_refs(self) -> frozenset[str] | None:
        return self.bindings.get("root_refs")

    @property
    def source_class(self) -> str | None:
        return self.bindings.get("source_class")

    def as_public(self) -> dict[str, Any]:
        """A content-free description, for a caller that wants to see it."""
        payload: dict[str, Any] = {
            "effect": self.effect,
            "subject": self.subject,
            "data_class": self.data_class,
            "egress": self.egress,
            "expires_at": _stamp(self.expires_at),
        }
        if self.conversation_id is not None:
            payload["conversation_id"] = self.conversation_id
        return payload


def _normalise_root_refs(value: Any) -> frozenset[str]:
    if isinstance(value, (str, bytes)):
        raise InvalidRequest("root references must be given as a list")
    try:
        refs = list(value)
    except TypeError as exc:
        raise InvalidRequest("root references must be given as a list") from exc
    if not refs:
        raise InvalidRequest("at least one source root is required")
    for ref in refs:
        if is_approved_root(ref):
            require_identifier(ref[len("approved:") :], "subject")
            continue
        require_identifier(ref, "root_ref")
    return frozenset(refs)


def _normalise_binding(name: str, value: Any) -> Any:
    """Validate one bound field's own shape, before any policy applies."""
    if name == "work_id":
        return require_uuid4(value, "work_id")
    if name == "operation_id":
        return require_uuid4(value, "operation_id")
    if name == "pending_id":
        return require_uuid4(value, "pending_id")
    if name == "allow_create":
        if value is not True:
            raise InvalidRequest("allow_create is either present and true, or absent")
        return True
    if name == "root_refs":
        return _normalise_root_refs(value)
    if name == "relative_path":
        return str(normalise_relative(value))
    if name in ("source_ref", "artifact_ref", "supersedes_ref"):
        if not isinstance(value, str) or not value.strip():
            raise InvalidRequest(f"{name} must name a record entry")
        return value
    if name == "source_class":
        if value not in SOURCE_CONTEXT_CLASSES:
            raise InvalidRequest("source_class must be a stored source provenance class")
        return value
    if name in ("content_sha256", "expected_input_sha256", "expected_sha256"):
        if not _is_digest(value):
            raise InvalidRequest(f"{name} must be a lowercase hex sha256 digest")
        return value
    if name == "content_bytes":
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise InvalidRequest("content_bytes must be a whole number of zero or more")
        return value
    raise InvalidRequest(f"a grant may not bind {name}")


class GrantIssuer:
    """Mints, verifies and spends grants, in memory, for one process.

    The issuer knows about deployment configuration only through the
    accumulation reference it is given: it asks which roots a subject has,
    and what provenance class a root declares. It never touches the
    filesystem itself and it never writes a grant anywhere.
    """

    def __init__(self, accumulation: Any) -> None:
        self._accumulation = accumulation
        self._live: dict[str, Grant] = {}
        self._consumed: dict[str, datetime] = {}

    # -- table maintenance ---------------------------------------------

    def _sweep(self, now: datetime) -> None:
        """Retire a bounded number of entries that can no longer verify."""
        expired = [
            ref
            for ref, grant in list(self._live.items())[:MAX_SWEEP_PER_CALL]
            if grant.expires_at <= now
        ]
        for ref in expired:
            self._live.pop(ref, None)
        stale = [
            ref
            for ref, expires_at in list(self._consumed.items())[:MAX_SWEEP_PER_CALL]
            if expires_at <= now
        ]
        for ref in stale:
            self._consumed.pop(ref, None)

    @property
    def entry_count(self) -> int:
        """Live plus consumed entries — the number the capacity bounds."""
        return len(self._live) + len(self._consumed)

    # -- minting --------------------------------------------------------

    def mint(
        self,
        *,
        effect: str,
        subject: str,
        turn_id: str = "turn",
        conversation_id: str | None = None,
        data_class: str = "private_personal",
        egress: str = "none",
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        now: datetime | None = None,
        **bindings: Any,
    ) -> Grant:
        """Issue one single-use grant, or refuse."""
        require_effect(effect)
        require_identifier(subject, "subject")
        if not isinstance(turn_id, str) or not turn_id or len(turn_id) > MAX_TURN_ID_CHARS:
            raise InvalidRequest("turn_id must be a short opaque string")
        if data_class not in DATA_CLASSES:
            raise InvalidRequest("data_class is not one of the declared classes")
        if egress not in EGRESS_VALUES:
            raise GrantError("egress_denied", "this authority may not send anything")
        if conversation_id is not None and not is_identifier(conversation_id):
            raise InvalidRequest("conversation_id is not a valid name")
        if effect in BOUND_EFFECTS and conversation_id is None:
            raise InvalidRequest("this effect needs the conversation it belongs to")
        if (
            not isinstance(ttl_seconds, int)
            or isinstance(ttl_seconds, bool)
            or ttl_seconds < 1
            or ttl_seconds > MAX_TTL_SECONDS
        ):
            raise InvalidRequest("ttl_seconds must be a positive number within the ceiling")

        supplied = {name: value for name, value in bindings.items() if value is not None}
        unknown = sorted(set(supplied) - set(BINDING_FIELDS))
        if unknown:
            raise InvalidRequest("a grant may not bind: " + ", ".join(unknown))
        resolved = {name: _normalise_binding(name, value) for name, value in supplied.items()}

        variants = GRANT_BINDINGS[effect]
        present = frozenset(resolved)
        if present not in variants:
            raise InvalidRequest(
                "this grant does not bind exactly one resource for that effect"
            )

        self._check_policy(effect, subject, data_class, resolved)

        moment = now or _now()
        self._sweep(moment)
        if self.entry_count >= MAX_GRANT_ENTRIES:
            raise GrantError("grant_invalid", "no grant capacity is available")

        grant = Grant(
            grant_ref=secrets.token_urlsafe(32),
            turn_id=turn_id,
            effect=effect,
            subject=subject,
            data_class=data_class,
            egress=egress,
            issued_at=moment,
            expires_at=moment + timedelta(seconds=ttl_seconds),
            conversation_id=conversation_id,
            bindings=dict(resolved),
        )
        self._live[grant.grant_ref] = grant
        return grant

    def _check_policy(
        self, effect: str, subject: str, data_class: str, resolved: Mapping[str, Any]
    ) -> None:
        """Apply the rules that make provenance a mint-time decision."""
        source_class = resolved.get("source_class")
        if source_class == "robert_source" and data_class == "external_public":
            raise InvalidRequest(
                "an external-public turn may not mint personally authored evidence"
            )

        root_refs = resolved.get("root_refs")
        if root_refs is not None:
            available = set(self._accumulation.available_root_refs(subject))
            if not root_refs or not root_refs.issubset(available):
                raise GrantError(
                    "source_root_unavailable",
                    "that source is not one of the authorized roots",
                    root_ref=sorted(root_refs - available)[0] if root_refs - available else None,
                )
            if effect == "use_robert_edit":
                for ref in sorted(root_refs):
                    if is_approved_root(ref):
                        raise InvalidRequest(
                            "an edit may only be adopted from a personally authored root"
                        )
                    root = self._accumulation.configuration.resolve(subject, ref)
                    if root.context_class != "robert_source":
                        raise InvalidRequest(
                            "an edit may only be adopted from a personally authored root"
                        )

    # -- verification ---------------------------------------------------

    def verify(
        self,
        grant_ref: Any,
        *,
        effect: str,
        subject: str,
        resolved: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> Grant:
        """Run every check, in order, and return the grant. Nothing is spent.

        The bounded sweep runs *after* the checks, not before: retiring an
        expired entry first would turn "that authority has expired" into
        "this turn holds no authority", which is a less accurate answer to
        the same question.
        """
        moment = now or _now()
        if not isinstance(grant_ref, str) or not grant_ref:
            raise GrantError("grant_invalid", "this turn holds no authority for that")
        if grant_ref in self._consumed:
            raise GrantError("grant_invalid", "this turn holds no authority for that")
        grant = self._live.get(grant_ref)
        if grant is None:
            raise GrantError("grant_invalid", "this turn holds no authority for that")
        if moment > grant.expires_at:
            self._live.pop(grant_ref, None)
            raise GrantError("grant_expired", "that authority has expired")
        if grant.effect != effect:
            raise GrantError("grant_effect_mismatch", "that authority is for another operation")
        if grant.egress not in EGRESS_VALUES:
            raise GrantError("egress_denied", "this authority may not send anything")
        if grant.subject != subject:
            raise GrantError(
                "grant_resource_mismatch", "that authority belongs to different work"
            )

        supplied = dict(resolved or {})
        for name in _RESOURCE_FIELDS:
            bound = grant.bindings.get(name)
            if bound is None:
                continue
            if name not in supplied:
                raise GrantError(
                    "grant_resource_mismatch", "that authority belongs to different work"
                )
            value = supplied[name]
            if name == "root_refs":
                # A request may narrow further; it may never reach outside
                # what the grant already narrowed to.
                requested = frozenset(value)
                if not requested or not requested.issubset(bound):
                    raise GrantError(
                        "grant_resource_mismatch", "that authority belongs to different work"
                    )
                continue
            if value != bound:
                raise GrantError(
                    "grant_resource_mismatch", "that authority belongs to different work"
                )

        root_refs = grant.root_refs
        if root_refs is not None:
            available = set(self._accumulation.available_root_refs(subject))
            if not root_refs.issubset(available):
                raise GrantError(
                    "source_root_unavailable",
                    "that source is not one of the authorized roots",
                    root_ref=sorted(root_refs - available)[0],
                )
        self._sweep(moment)
        return grant

    def consume(self, grant: Grant) -> None:
        """Spend a grant. Never rolled back, whatever the effect then does."""
        self._live.pop(grant.grant_ref, None)
        self._consumed[grant.grant_ref] = grant.expires_at

    def peek(self, grant_ref: Any) -> Grant | None:
        """The live grant for this reference, without verifying or spending it.

        A caller needs the subject a grant names before it can resolve the
        request's own resources against it. Looking is not using: nothing is
        checked and nothing is consumed here.
        """
        if not isinstance(grant_ref, str):
            return None
        return self._live.get(grant_ref)

    def is_consumed(self, grant_ref: str) -> bool:
        """True when this reference has already been spent."""
        return grant_ref in self._consumed


def narrowed_root_refs(
    grant: Grant, requested: Iterable[str] | None
) -> tuple[str, ...] | None:
    """The roots a request may actually reach: the narrower of the two.

    A grant can only ever select fewer roots than deployment configured. A
    request may select fewer still; it may never reach outside the grant.
    """
    bound = grant.root_refs
    if requested is None:
        return tuple(sorted(bound)) if bound is not None else None
    chosen = list(requested)
    if not chosen:
        raise InvalidRequest("at least one source root is required")
    if bound is not None and not set(chosen).issubset(bound):
        raise GrantError("grant_resource_mismatch", "that authority belongs to different work")
    return tuple(sorted(set(chosen)))


def is_grant_ref(value: Any) -> bool:
    """True when ``value`` has the shape of a grant reference."""
    return isinstance(value, str) and bool(value)


__all__ = [
    "BINDING_FIELDS",
    "BOUND_EFFECTS",
    "DEFAULT_TTL_SECONDS",
    "GRANT_BINDINGS",
    "Grant",
    "GrantError",
    "GrantIssuer",
    "MAX_GRANT_ENTRIES",
    "MAX_TTL_SECONDS",
    "is_grant_ref",
    "is_uuid4",
    "narrowed_root_refs",
]
