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
    }
)


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


@dataclass(frozen=True)
class Receipt:
    """A content-free record of one operation's outcome."""

    operation_id: str
    effect: str
    outcome: str
    fields: Mapping[str, Any] = field(default_factory=dict)

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
    """Build a receipt, refusing any key outside :data:`RECEIPT_KEYS`."""
    require_uuid4(operation_id, "operation_id")
    require_effect(effect)
    unknown = set(fields) - RECEIPT_KEYS
    if unknown:
        raise InvalidRequest("receipt may not carry: " + ", ".join(sorted(unknown)))
    return Receipt(operation_id=operation_id, effect=effect, outcome=outcome, fields=dict(fields))


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
    """Build a successful version-1 response envelope."""
    require_effect(effect)
    require_uuid4(operation_id, "operation_id")
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
    """Build a failing version-1 response envelope."""
    require_effect(effect)
    require_uuid4(operation_id, "operation_id")
    return {
        "work_contract_version": WORK_CONTRACT_VERSION,
        "operation_id": operation_id,
        "effect": effect,
        "ok": False,
        "result": None,
        "receipt": receipt.as_dict() if receipt else None,
        "error": error.to_error(),
    }
