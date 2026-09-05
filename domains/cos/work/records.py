"""The canonical record shapes, read side.

The person-owned private file tree is canonical. This gate owns the *read*
schema that the write-side service must produce: ``work.json`` version 1 and
the minimal conversation-to-active-work binding.

The record deliberately does not exist to describe a product. It carries no
provider, model, runtime or adapter field, no opaque subject extension, no
workflow state, no score and no agent claim of approval. Unknown fields are
refused rather than tolerated, so a later gate cannot quietly widen the
schema.

Approval changes an artifact's eligibility for later retrieval; it does not
change authorship. An approved ``agent_draft`` stays an agent draft.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .confine import sha256_file
from .envelope import WorkError, is_uuid4

SCHEMA_VERSION = 1

#: Common lifecycle states. "Do not apply" is a successful outcome recorded as
#: a free-text reason with state ``closed``, not a new state.
WORK_STATES: frozenset[str] = frozenset({"continuing", "approved_text", "closed", "unresolved"})

#: Stored provenance classes for captured sources.
SOURCE_CONTEXT_CLASSES: frozenset[str] = frozenset({"robert_source", "external_source"})

#: Stored provenance classes for produced artifacts.
ARTIFACT_CONTEXT_CLASSES: frozenset[str] = frozenset({"agent_draft", "coauthored_output"})

#: Fields removed from the contract by review. Their presence is an error with
#: a specific message so a regression is obvious rather than merely "unknown".
FORBIDDEN_FIELDS: frozenset[str] = frozenset({"subject_extension", "adapter_binding"})

_WORK_REQUIRED = (
    "schema_version",
    "work_id",
    "subject",
    "label",
    "intent",
    "state",
    "created_at",
    "updated_at",
    "sources",
    "artifacts",
    "pending_approval",
    "disposition",
)
_WORK_OPTIONAL = ("work_contract_version",)

_SOURCE_REQUIRED = ("ref", "path", "sha256", "context_class", "created_at")
_SOURCE_OPTIONAL = ("bytes", "origin_note", "operation_id")

_ARTIFACT_REQUIRED = (
    "ref",
    "path",
    "sha256",
    "context_class",
    "based_on",
    "revision",
    "created_at",
)
_ARTIFACT_OPTIONAL = ("bytes", "operation_id", "supersedes_ref")

_PENDING_REQUIRED = ("pending_id", "proposed_state", "artifact_ref", "issued_at", "expires_at")
_PENDING_OPTIONAL = ("artifact_sha256",)

_DISPOSITION_REQUIRED = ("state", "at", "artifact_ref", "reason", "operation_id")

_BINDING_REQUIRED = ("schema_version", "conversation_id", "subject", "work_id", "updated_at")

_BASED_ON_REQUIRED = ("ref", "sha256")


class RecordInvalid(WorkError):
    """A canonical record failed strict validation."""

    def __init__(self, message: str, *, relative_path: str | None = None) -> None:
        super().__init__("invalid_request", message, relative_path=relative_path)


class StaleHash(WorkError):
    """Stored bytes no longer match the hash the record pinned."""

    def __init__(self, relative_path: str) -> None:
        super().__init__(
            "stale_hash",
            "the stored file no longer matches the hash recorded for it",
            relative_path=relative_path,
        )


@dataclass(frozen=True)
class SourceRef:
    """An immutable captured source."""

    ref: str
    path: str
    sha256: str
    context_class: str
    created_at: str
    bytes: int | None = None
    origin_note: str | None = None
    operation_id: str | None = None


@dataclass(frozen=True)
class BasedOn:
    """A hash-pinned input an artifact was built from."""

    ref: str
    sha256: str


@dataclass(frozen=True)
class ArtifactRef:
    """An immutable artifact revision."""

    ref: str
    path: str
    sha256: str
    context_class: str
    revision: int
    created_at: str
    based_on: tuple[BasedOn, ...] = ()
    bytes: int | None = None
    operation_id: str | None = None
    supersedes_ref: str | None = None


@dataclass(frozen=True)
class PendingApproval:
    """A pinned, expiring approval request awaiting Robert's confirmation."""

    pending_id: str
    proposed_state: str
    artifact_ref: str
    issued_at: str
    expires_at: str
    artifact_sha256: str | None = None


@dataclass(frozen=True)
class Disposition:
    """Robert's recorded decision about a work item."""

    state: str
    at: str
    artifact_ref: str | None
    reason: str | None
    operation_id: str | None


@dataclass(frozen=True)
class WorkRecord:
    """``work.json`` version 1."""

    schema_version: int
    work_id: str
    subject: str
    label: str
    intent: str
    state: str
    created_at: str
    updated_at: str
    sources: tuple[SourceRef, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    pending_approval: PendingApproval | None = None
    disposition: Disposition | None = None

    def artifact(self, ref: str) -> ArtifactRef | None:
        """Return the artifact with ``ref``, if the record has one."""
        for artifact in self.artifacts:
            if artifact.ref == ref:
                return artifact
        return None

    @property
    def approved_artifact_ref(self) -> str | None:
        """The exact artifact pinned by an ``approved_text`` disposition."""
        if self.disposition is None or self.disposition.state != "approved_text":
            return None
        return self.disposition.artifact_ref


@dataclass(frozen=True)
class ConversationBinding:
    """The minimal conversation-to-active-work pointer.

    It names the active work only. It is operational continuity, never
    relationship-memory content, and it is not a list-all-work API.
    """

    schema_version: int
    conversation_id: str
    subject: str
    work_id: str
    updated_at: str


def _reject_unknown(
    data: Mapping[str, Any], required: Sequence[str], optional: Sequence[str], what: str
) -> None:
    """Refuse forbidden and unknown keys, and require the required ones."""
    if not isinstance(data, Mapping):
        raise RecordInvalid(f"{what} must be an object")
    keys = set(data)
    forbidden = sorted(keys & FORBIDDEN_FIELDS)
    if forbidden:
        raise RecordInvalid(
            f"{what} may not contain {', '.join(forbidden)}: "
            "that field was removed from the contract"
        )
    unknown = sorted(keys - set(required) - set(optional))
    if unknown:
        raise RecordInvalid(f"{what} has fields that are not part of the contract: {', '.join(unknown)}")
    missing = sorted(set(required) - keys)
    if missing:
        raise RecordInvalid(f"{what} is missing: {', '.join(missing)}")


def _text(data: Mapping[str, Any], key: str, what: str, *, allow_empty: bool = False) -> str:
    value = data.get(key)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise RecordInvalid(f"{what} needs a text value for {key}")
    return value


def _optional_text(data: Mapping[str, Any], key: str, what: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RecordInvalid(f"{what} needs a text value for {key}")
    return value


def _optional_int(data: Mapping[str, Any], key: str, what: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise RecordInvalid(f"{what} needs a whole number for {key}")
    return value


def _sha256(data: Mapping[str, Any], what: str) -> str:
    value = data.get("sha256")
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise RecordInvalid(f"{what} needs a lowercase hex sha256 digest")
    return value


def _optional_operation_id(data: Mapping[str, Any], what: str) -> str | None:
    value = data.get("operation_id")
    if value is None:
        return None
    if not is_uuid4(value):
        raise RecordInvalid(f"{what} needs a UUID4 operation_id")
    return value


def _relative_path(data: Mapping[str, Any], what: str) -> str:
    value = _text(data, "path", what)
    if value.startswith("/") or ".." in value.split("/"):
        raise RecordInvalid(f"{what} needs a relative path inside the work directory")
    return value


def parse_source_ref(data: Any) -> SourceRef:
    """Validate one ``sources[]`` entry."""
    what = "a source entry"
    _reject_unknown(data, _SOURCE_REQUIRED, _SOURCE_OPTIONAL, what)
    context_class = _text(data, "context_class", what)
    if context_class not in SOURCE_CONTEXT_CLASSES:
        raise RecordInvalid(f"{what} has an unknown provenance class")
    return SourceRef(
        ref=_text(data, "ref", what),
        path=_relative_path(data, what),
        sha256=_sha256(data, what),
        context_class=context_class,
        created_at=_text(data, "created_at", what),
        bytes=_optional_int(data, "bytes", what),
        origin_note=_optional_text(data, "origin_note", what),
        operation_id=_optional_operation_id(data, what),
    )


def _parse_based_on(value: Any) -> tuple[BasedOn, ...]:
    what = "an artifact input reference"
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RecordInvalid("an artifact needs a list for based_on")
    entries = []
    for item in value:
        _reject_unknown(item, _BASED_ON_REQUIRED, (), what)
        entries.append(BasedOn(ref=_text(item, "ref", what), sha256=_sha256(item, what)))
    return tuple(entries)


def parse_artifact_ref(data: Any) -> ArtifactRef:
    """Validate one ``artifacts[]`` entry."""
    what = "an artifact entry"
    _reject_unknown(data, _ARTIFACT_REQUIRED, _ARTIFACT_OPTIONAL, what)
    context_class = _text(data, "context_class", what)
    if context_class not in ARTIFACT_CONTEXT_CLASSES:
        raise RecordInvalid(f"{what} has an unknown provenance class")
    revision = data.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise RecordInvalid(f"{what} needs a revision number of at least one")
    return ArtifactRef(
        ref=_text(data, "ref", what),
        path=_relative_path(data, what),
        sha256=_sha256(data, what),
        context_class=context_class,
        revision=revision,
        created_at=_text(data, "created_at", what),
        based_on=_parse_based_on(data.get("based_on")),
        bytes=_optional_int(data, "bytes", what),
        operation_id=_optional_operation_id(data, what),
        supersedes_ref=_optional_text(data, "supersedes_ref", what),
    )


def parse_pending_approval(data: Any) -> PendingApproval | None:
    """Validate ``pending_approval``; ``None`` is legal."""
    if data is None:
        return None
    what = "a pending approval"
    _reject_unknown(data, _PENDING_REQUIRED, _PENDING_OPTIONAL, what)
    proposed_state = _text(data, "proposed_state", what)
    if proposed_state not in WORK_STATES:
        raise RecordInvalid(f"{what} proposes an unknown state")
    pending_id = data.get("pending_id")
    if not is_uuid4(pending_id):
        raise RecordInvalid(f"{what} needs a UUID4 pending_id")
    sha = data.get("artifact_sha256")
    if sha is not None:
        _sha256({"sha256": sha}, what)
    return PendingApproval(
        pending_id=str(pending_id),
        proposed_state=proposed_state,
        artifact_ref=_text(data, "artifact_ref", what),
        issued_at=_text(data, "issued_at", what),
        expires_at=_text(data, "expires_at", what),
        artifact_sha256=sha,
    )


def parse_disposition(data: Any) -> Disposition | None:
    """Validate ``disposition``; ``None`` is legal."""
    if data is None:
        return None
    what = "a disposition"
    _reject_unknown(data, _DISPOSITION_REQUIRED, (), what)
    state = _text(data, "state", what)
    if state not in WORK_STATES:
        raise RecordInvalid(f"{what} records an unknown state")
    artifact_ref = _optional_text(data, "artifact_ref", what)
    if state == "approved_text" and not artifact_ref:
        raise RecordInvalid("an approved disposition must name the exact artifact it approved")
    return Disposition(
        state=state,
        at=_text(data, "at", what),
        artifact_ref=artifact_ref,
        reason=_optional_text(data, "reason", what),
        operation_id=_optional_operation_id(data, what),
    )


def parse_work_record(data: Any) -> WorkRecord:
    """Validate a full ``work.json`` document, strictly."""
    what = "a work record"
    _reject_unknown(data, _WORK_REQUIRED, _WORK_OPTIONAL, what)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise RecordInvalid("this work record uses an unsupported schema version")
    work_id = data.get("work_id")
    if not is_uuid4(work_id):
        raise RecordInvalid("a work record needs a UUID4 work_id")
    state = _text(data, "state", what)
    if state not in WORK_STATES:
        raise RecordInvalid("a work record has an unknown state")
    for key in ("sources", "artifacts"):
        if not isinstance(data.get(key), list):
            raise RecordInvalid(f"a work record needs a list for {key}")

    record = WorkRecord(
        schema_version=SCHEMA_VERSION,
        work_id=str(work_id),
        subject=_text(data, "subject", what),
        label=_text(data, "label", what),
        intent=_text(data, "intent", what, allow_empty=True),
        state=state,
        created_at=_text(data, "created_at", what),
        updated_at=_text(data, "updated_at", what),
        sources=tuple(parse_source_ref(item) for item in data["sources"]),
        artifacts=tuple(parse_artifact_ref(item) for item in data["artifacts"]),
        pending_approval=parse_pending_approval(data.get("pending_approval")),
        disposition=parse_disposition(data.get("disposition")),
    )
    approved = record.approved_artifact_ref
    if approved is not None and record.artifact(approved) is None:
        raise RecordInvalid("the approved disposition names an artifact this record does not have")
    return record


def load_work_record(path: Path) -> WorkRecord:
    """Read and strictly validate ``work.json`` from disk."""
    try:
        raw = Path(path).read_text("utf-8")
    except OSError as exc:
        raise RecordInvalid("the work record could not be read") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise RecordInvalid("the work record is not valid JSON") from exc
    return parse_work_record(data)


def parse_conversation_binding(data: Any) -> ConversationBinding:
    """Validate a conversation binding document."""
    what = "a conversation binding"
    _reject_unknown(data, _BINDING_REQUIRED, (), what)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise RecordInvalid("this conversation binding uses an unsupported schema version")
    work_id = data.get("work_id")
    if not is_uuid4(work_id):
        raise RecordInvalid("a conversation binding needs a UUID4 work_id")
    return ConversationBinding(
        schema_version=SCHEMA_VERSION,
        conversation_id=_text(data, "conversation_id", what),
        subject=_text(data, "subject", what),
        work_id=str(work_id),
        updated_at=_text(data, "updated_at", what),
    )


def load_conversation_binding(path: Path) -> ConversationBinding:
    """Read and validate ``conversations/<conversation_id>.json``."""
    try:
        raw = Path(path).read_text("utf-8")
    except OSError as exc:
        raise RecordInvalid("the conversation binding could not be read") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise RecordInvalid("the conversation binding is not valid JSON") from exc
    return parse_conversation_binding(data)


def verify_sha256(path: Path, expected: str) -> bool:
    """True when the file's raw bytes hash to ``expected``."""
    try:
        return sha256_file(Path(path)) == expected
    except OSError:
        return False


def require_sha256(path: Path, expected: str, relative_path: str) -> None:
    """Raise :class:`StaleHash` unless the stored bytes still match."""
    if not verify_sha256(path, expected):
        raise StaleHash(relative_path)
