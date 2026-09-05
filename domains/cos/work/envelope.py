"""Version-1 request/response envelope, closed error vocabulary, identifiers.

This module is provider-neutral by contract: no runtime, vendor, product or
model identity appears here or anywhere else in this package. Receipts and
errors are content-free — they carry identifiers, relative paths, hashes,
outcomes and timestamps, never a body or an absolute filesystem path.

W0a implements only the two read-side effects (``search_sources`` and
``read_source``). The full effect and error vocabularies are declared here so
that the contract is closed from the start and W0b adds behaviour, not names.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

WORK_CONTRACT_VERSION = 1

#: The complete, closed operation set. No list, delete, rename, move, export,
#: send, schedule or delegate effect exists, and none may be added without a
#: contract version change.
EFFECTS: frozenset[str] = frozenset(
    {
        "open_work",
        "attach_source",
        "search_sources",
        "read_source",
        "write_artifact",
        "request_disposition",
        "record_disposition",
        "use_robert_edit",
    }
)

#: Effects this gate actually implements. The rest are declared, not served.
READ_EFFECTS: frozenset[str] = frozenset({"search_sources", "read_source"})

#: Error codes raised by this gate. These are the final contract's names:
#: ``path_denied``, ``unsupported_media`` and ``stale_context``. No synonym
#: exists, and the vocabulary is closed — W0b adds behaviour, not names.
W0A_ERROR_CODES: frozenset[str] = frozenset(
    {
        "invalid_request",
        "work_root_unavailable",
        "source_root_unavailable",
        "path_denied",
        "not_found",
        "unsupported_media",
        "too_large",
        "stale_context",
        "egress_denied",
        "runtime_profile_unavailable",
    }
)

#: Codes reserved for the write-side service. Declared so the vocabulary is
#: closed now; nothing in this gate raises them.
RESERVED_ERROR_CODES: frozenset[str] = frozenset(
    {
        "contract_version_unsupported",
        "grant_invalid",
        "grant_expired",
        "grant_effect_mismatch",
        "grant_resource_mismatch",
        "locked",
        "ambiguous_work",
        "pending_expired",
        "pending_target_changed",
        "internal_error",
    }
)

#: The closed error vocabulary. Any code outside this set is a programming
#: error and is refused at construction time.
ERROR_CODES: frozenset[str] = W0A_ERROR_CODES | RESERVED_ERROR_CODES

#: A partial-result notice. It is deliberately *not* an error code: the
#: operation succeeded, and the caller is told only that a root was not walked
#: to the end. It carries no path and no content.
SEARCH_TRUNCATED = "search_truncated"

#: Codes a per-item issue may carry: every error code, plus the partial-search
#: notice. Issues are reported alongside results; they never carry content.
ISSUE_CODES: frozenset[str] = ERROR_CODES | {SEARCH_TRUNCATED}

#: The only permitted egress value. A future capability that needs egress must
#: add a value here and re-open review rather than flip a boolean.
EGRESS_VALUES: frozenset[str] = frozenset({"none"})

#: Stored provenance classes for captured sources.
SOURCE_CONTEXT_CLASSES: frozenset[str] = frozenset({"robert_source", "external_source"})

#: Stored provenance classes for produced artifacts.
ARTIFACT_CONTEXT_CLASSES: frozenset[str] = frozenset({"agent_draft", "coauthored_output"})

#: Every provenance class. A configured root declares exactly one of these;
#: there is no default, because a wrong default would present someone else's
#: writing, or the system's own draft, as Robert's own words.
CONTEXT_CLASSES: frozenset[str] = SOURCE_CONTEXT_CLASSES | ARTIFACT_CONTEXT_CLASSES

#: Grant data classes. Career is a subject, not a platform privacy type.
DATA_CLASSES: frozenset[str] = frozenset({"private_personal", "external_public"})

#: Keys a receipt may carry. Everything here is an identifier, a relative
#: path, a hash, a byte count, an outcome or a timestamp.
#:
#: The first ten are the read side. The nine that follow complete the same
#: version-1 allowlist for the write side: a receipt naming a work item, a
#: revision or a pending approval cannot be built without them. Every one is
#: an identifier, a closed-set member, an integer or a timestamp, so none can
#: carry prose. The contract version does not move.
RECEIPT_KEYS: frozenset[str] = frozenset(
    {
        "operation_id",
        "effect",
        "outcome",
        "subject",
        "root_ref",
        "relative_path",
        "sha256",
        "bytes",
        "result_count",
        "created_at",
        # write side
        "work_id",
        "ref",
        "revision",
        "state",
        "proposed_state",
        "pending_id",
        "supersedes_ref",
        "context_class",
        "expires_at",
    }
)

#: What a *receipt* may say. ``ok`` is an operation that persists no terminal
#: marker; ``committed`` is the single, unchanging statement a write makes,
#: returned identically on first completion, on recovery and on a retry.
OUTCOMES: frozenset[str] = frozenset({"ok", "committed"})

#: What a terminal marker's own ``outcome`` field may say. This is not a
#: receipt vocabulary: an abandoned or quarantined operation surfaces to the
#: caller as a failure response with no receipt at all.
TERMINAL_OUTCOMES: frozenset[str] = frozenset({"committed", "abandoned", "quarantined"})

#: The states a disposition may be asked to confirm. ``continuing`` is the
#: initial active state of a work item, not something to propose.
PROPOSED_STATES: frozenset[str] = frozenset({"approved_text", "closed", "unresolved"})

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_DERIVED_REF_PATTERN = re.compile(r"^(?:src|art)-[0-9]{4,}$")
_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

#: The extra keys the two disposition effects carry only when an artifact is
#: actually named — an approval. A close or an unresolved decision has no
#: artifact to name and no digest to pin.
_DISPOSITION_ARTIFACT_EXTRA: frozenset[str] = frozenset({"ref", "sha256"})

#: A read of a source captured inside the work item. It is not the configured
#: shape plus extras: it drops ``root_ref``, which does not exist for bytes
#: that live in the work directory rather than under a configured root.
_READ_SOURCE_CAPTURED_SHAPE: frozenset[str] = frozenset(
    {"subject", "work_id", "ref", "relative_path", "sha256", "bytes", "context_class"}
)

#: The exact ``fields`` key set for every effect and outcome. Validation is
#: set *equality*, so a missing key and an extra key are the same error and
#: neither can pass. Two effects — and, since both read modes exist,
#: ``read_source`` — select their row with a discriminant the receipt already
#: has to carry, so there is no separate mode input to drift out of step.
RECEIPT_SHAPES: dict[tuple[str, str], frozenset[str]] = {
    ("open_work", "committed"): frozenset({"subject", "work_id", "state", "created_at"}),
    ("open_work", "ok"): frozenset({"subject", "work_id", "state"}),
    ("attach_source", "committed"): frozenset(
        {
            "subject",
            "work_id",
            "ref",
            "relative_path",
            "sha256",
            "bytes",
            "context_class",
            "created_at",
        }
    ),
    ("search_sources", "ok"): frozenset({"subject", "result_count"}),
    ("read_source", "ok"): frozenset(
        {"subject", "root_ref", "relative_path", "sha256", "bytes"}
    ),
    ("write_artifact", "committed"): frozenset(
        {
            "subject",
            "work_id",
            "ref",
            "revision",
            "relative_path",
            "sha256",
            "bytes",
            "context_class",
            "created_at",
        }
    ),
    ("request_disposition", "committed"): frozenset(
        {
            "subject",
            "work_id",
            "pending_id",
            "proposed_state",
            "state",
            "expires_at",
            "created_at",
        }
    ),
    ("record_disposition", "committed"): frozenset(
        {"subject", "work_id", "pending_id", "state", "created_at"}
    ),
    ("use_robert_edit", "committed"): frozenset(
        {
            "subject",
            "work_id",
            "ref",
            "revision",
            "relative_path",
            "sha256",
            "bytes",
            "context_class",
            "supersedes_ref",
            "created_at",
        }
    ),
}


def _record_states() -> frozenset[str]:
    """The record's own state vocabulary.

    Imported on use rather than at module load: the record module reads its
    identifier and provenance vocabularies from here, so a module-level
    import in this direction would be a cycle. The set is the one the record
    schema defines; this module does not keep a second copy of it.
    """
    from .records import WORK_STATES

    return WORK_STATES


def _receipt_shape(effect: str, outcome: str, fields: Mapping[str, Any]) -> frozenset[str]:
    """The exact ``fields`` key set this effect, outcome and mode must carry."""
    try:
        shape = RECEIPT_SHAPES[(effect, outcome)]
    except KeyError:
        raise InvalidRequest("that effect cannot report that outcome") from None
    if effect == "request_disposition":
        proposed = fields.get("proposed_state")
        if proposed not in PROPOSED_STATES:
            raise InvalidRequest("receipt is missing a valid proposed_state")
        if proposed == "approved_text":
            shape = shape | _DISPOSITION_ARTIFACT_EXTRA
    elif effect == "record_disposition":
        state = fields.get("state")
        if state not in _record_states():
            raise InvalidRequest("receipt is missing a valid state")
        if state == "approved_text":
            shape = shape | _DISPOSITION_ARTIFACT_EXTRA
    elif effect == "read_source":
        configured = "root_ref" in fields
        captured = "ref" in fields
        if configured == captured:
            raise InvalidRequest(
                "a read_source receipt names exactly one of root_ref or ref"
            )
        if captured:
            shape = _READ_SOURCE_CAPTURED_SHAPE
    return shape


def _require_non_negative_int(value: Any, key: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InvalidRequest(f"receipt {key} must be a whole number of zero or more")


def _check_receipt_value(key: str, value: Any) -> None:
    """Apply the closed per-key grammar. No key may carry prose."""
    if key in ("work_id", "pending_id"):
        require_uuid4(value, key)
    elif key == "sha256":
        if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
            raise InvalidRequest("receipt sha256 must be a lowercase hex digest")
    elif key in ("bytes", "result_count"):
        _require_non_negative_int(value, key)
    elif key == "revision":
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise InvalidRequest("receipt revision must be a whole number of at least one")
    elif key == "state":
        if value not in _record_states():
            raise InvalidRequest("receipt state is not a known work state")
    elif key == "proposed_state":
        if value not in PROPOSED_STATES:
            raise InvalidRequest("receipt proposed_state is not a proposable state")
    elif key == "context_class":
        if value not in CONTEXT_CLASSES:
            raise InvalidRequest("receipt context_class is not a known provenance class")
    elif key == "subject":
        require_identifier(value, "subject")
    elif key in ("ref", "supersedes_ref"):
        if not isinstance(value, str) or _DERIVED_REF_PATTERN.fullmatch(value) is None:
            raise InvalidRequest(f"receipt {key} is not a record reference")
    elif key == "relative_path":
        from .confine import normalise_relative

        normalise_relative(value)
    elif key in ("created_at", "expires_at"):
        if not isinstance(value, str) or _TIMESTAMP_PATTERN.fullmatch(value) is None:
            raise InvalidRequest(f"receipt {key} must be a UTC timestamp")
    elif key == "root_ref":
        if isinstance(value, str) and value.startswith("approved:"):
            require_identifier(value[len("approved:") :], "subject")
        else:
            require_identifier(value, "root_ref")
    else:  # pragma: no cover - the allowlist has no other member
        raise InvalidRequest(f"receipt may not carry: {key}")


#: The closed grammar for a subject name and for a configured root
#: reference. Lower-case letters, digits, ``_`` and ``-`` only; it must begin
#: with a letter or digit; at most 64 characters. Path separators, ``..``,
#: control characters, whitespace, ``:`` and the empty string are all outside
#: it, so an identifier can never become a path fragment or a reserved prefix.
IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def is_identifier(value: Any) -> bool:
    """True when ``value`` satisfies the closed identifier grammar."""
    return isinstance(value, str) and IDENTIFIER_PATTERN.fullmatch(value) is not None


class WorkError(Exception):
    """Base error for the Work foundation.

    ``code`` is always a member of :data:`ERROR_CODES`. ``message`` is plain
    language, safe to show, and must not reveal an absolute path or root
    layout. ``relative_path`` is the only filesystem detail an error may
    carry.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        relative_path: str | None = None,
        root_ref: str | None = None,
    ) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code: {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.relative_path = relative_path
        self.root_ref = root_ref

    def to_error(self) -> dict[str, Any]:
        """Render the content-free error object for a response envelope."""
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.root_ref is not None:
            payload["root_ref"] = self.root_ref
        if self.relative_path is not None:
            payload["relative_path"] = self.relative_path
        return payload


class InvalidRequest(WorkError):
    """A malformed or out-of-bounds request. Nothing is read."""

    def __init__(self, message: str) -> None:
        super().__init__("invalid_request", message)


def freeze(value: Any) -> Any:
    """Return a deeply immutable copy of ``value``.

    A frozen dataclass freezes its *attributes*, not the objects they point
    at. A plain ``dict`` reached through a validated object is a hole in the
    validation: whatever was checked at construction can be changed
    afterwards. Every mapping becomes a read-only proxy over its own copy and
    every list becomes a tuple, so a validated authority or receipt states
    exactly what it stated when it was validated.

    An existing ``MappingProxyType`` is copied like any other mapping rather
    than passed through. A proxy is read-only *through the proxy*; whoever
    holds the dictionary behind it can still change what the proxy shows.
    Returning such a proxy unchanged would leave that alias in place after
    validation, so a caller could rewrite a validated subject — or add a
    body-carrying key the allowlist has already accepted the absence of —
    and every later read would agree with the change. The copy is made
    unconditionally so that no input, however wrapped, is shared.
    """
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (str, bytes, bytearray)):
        return value
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(value)
    return value


@dataclass(frozen=True)
class Receipt:
    """A content-free record of one operation's outcome.

    Validation lives here rather than in a builder, so there is no
    construction path that skips it. The global allowlist runs first, so a
    body-carrying key is always reported as such; the per-key grammar runs
    next; and the exact per-effect shape is then checked as set equality, so
    a missing key and a globally-legal-but-effect-illegal key are both
    refused before anything can be published.
    """

    operation_id: str
    effect: str
    outcome: str
    fields: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_uuid4(self.operation_id, "operation_id")
        require_effect(self.effect)
        if self.outcome not in OUTCOMES:
            raise InvalidRequest("unknown receipt outcome")
        if not isinstance(self.fields, Mapping):
            raise InvalidRequest("receipt fields must be an object")
        keys = set(self.fields)
        unknown = keys - RECEIPT_KEYS
        if unknown:
            raise InvalidRequest("receipt may not carry: " + ", ".join(sorted(unknown)))
        for key, value in self.fields.items():
            _check_receipt_value(key, value)
        shape = _receipt_shape(self.effect, self.outcome, self.fields)
        if keys != shape:
            missing = ", ".join(sorted(shape - keys))
            extra = ", ".join(sorted(keys - shape))
            raise InvalidRequest(
                "receipt does not match this effect: "
                + (f"missing {missing}; " if missing else "")
                + (f"not permitted here: {extra}" if extra else "")
            )
        object.__setattr__(self, "fields", freeze(self.fields))

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "operation_id": self.operation_id,
            "effect": self.effect,
            "outcome": self.outcome,
        }
        payload.update(self.fields)
        return payload


def new_operation_id() -> str:
    """Return a fresh UUID4 operation identifier."""
    return str(uuid.uuid4())


def is_uuid4(value: Any) -> bool:
    """True when ``value`` is a canonical UUID4 string."""
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return parsed.version == 4 and str(parsed) == value.lower()


def require_uuid4(value: Any, field_name: str) -> str:
    """Return ``value`` when it is a UUID4 string, else raise."""
    if not is_uuid4(value):
        raise InvalidRequest(f"{field_name} must be a UUID4 value")
    return str(value).lower()


def require_identifier(value: Any, field_name: str) -> str:
    """Return ``value`` when it satisfies the identifier grammar, else raise."""
    if not is_identifier(value):
        raise InvalidRequest(f"{field_name} is not a valid name")
    return str(value)


def require_effect(effect: Any) -> str:
    """Return ``effect`` when it belongs to the closed operation set."""
    if effect not in EFFECTS:
        raise InvalidRequest("unknown effect")
    return str(effect)


def make_receipt(
    operation_id: str,
    effect: str,
    outcome: str,
    **fields: Any,
) -> Receipt:
    """Build a receipt. A convenience, not the gate: the dataclass validates."""
    return Receipt(operation_id=operation_id, effect=effect, outcome=outcome, fields=dict(fields))


def _require_matching_receipt(
    effect: str, operation_id: str, receipt: Receipt | None
) -> Receipt | None:
    """Refuse a receipt that belongs to another operation or another effect."""
    if receipt is None:
        return None
    if not isinstance(receipt, Receipt):
        raise InvalidRequest("receipt must be a Receipt")
    if receipt.operation_id != operation_id or receipt.effect != effect:
        raise InvalidRequest("receipt does not belong to this operation")
    return receipt


def build_request(
    effect: str,
    params: Mapping[str, Any],
    *,
    operation_id: str | None = None,
    grant_ref: str | None = None,
) -> dict[str, Any]:
    """Build a version-1 request envelope."""
    require_effect(effect)
    operation_id = operation_id or new_operation_id()
    require_uuid4(operation_id, "operation_id")
    if not isinstance(params, Mapping):
        raise InvalidRequest("params must be an object")
    return {
        "work_contract_version": WORK_CONTRACT_VERSION,
        "operation_id": operation_id,
        "grant_ref": grant_ref,
        "effect": effect,
        "params": dict(params),
    }


def success_response(
    effect: str,
    operation_id: str,
    result: Mapping[str, Any],
    receipt: Receipt | None = None,
) -> dict[str, Any]:
    """Build a successful version-1 response envelope.

    Every effect returns a receipt on success — the read effects and a
    non-mutating open return an ephemeral ``ok`` one, the rest return the
    persisted ``committed`` one — so ``receipt=None`` is refused here.
    """
    require_effect(effect)
    require_uuid4(operation_id, "operation_id")
    if receipt is None:
        raise InvalidRequest("a successful response must carry a receipt")
    _require_matching_receipt(effect, operation_id, receipt)
    return {
        "work_contract_version": WORK_CONTRACT_VERSION,
        "operation_id": operation_id,
        "effect": effect,
        "ok": True,
        "result": dict(result),
        "receipt": receipt.as_dict() if receipt else None,
        "error": None,
    }


def error_response(
    effect: str,
    operation_id: str,
    error: WorkError,
    receipt: Receipt | None = None,
) -> dict[str, Any]:
    """Build a failing version-1 response envelope.

    ``receipt=None`` stays legal: an abandoned or quarantined operation
    deliberately carries no receipt at all.
    """
    require_effect(effect)
    require_uuid4(operation_id, "operation_id")
    _require_matching_receipt(effect, operation_id, receipt)
    return {
        "work_contract_version": WORK_CONTRACT_VERSION,
        "operation_id": operation_id,
        "effect": effect,
        "ok": False,
        "result": None,
        "receipt": receipt.as_dict() if receipt else None,
        "error": error.to_error(),
    }
