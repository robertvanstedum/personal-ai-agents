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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .envelope import InvalidRequest, WorkError, is_uuid4

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
        raise NotImplementedError

    @property
    def approved_artifact_ref(self) -> str | None:
        """The exact artifact pinned by an ``approved_text`` disposition."""
        raise NotImplementedError


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


def _reject_unknown(data: Mapping[str, Any], required: Sequence[str], optional: Sequence[str], what: str) -> None:
    """Refuse forbidden and unknown keys, and require the required ones."""
    raise NotImplementedError


def parse_source_ref(data: Any) -> SourceRef:
    """Validate one ``sources[]`` entry."""
    raise NotImplementedError


def parse_artifact_ref(data: Any) -> ArtifactRef:
    """Validate one ``artifacts[]`` entry."""
    raise NotImplementedError


def parse_pending_approval(data: Any) -> PendingApproval | None:
    """Validate ``pending_approval``; ``None`` is legal."""
    raise NotImplementedError


def parse_disposition(data: Any) -> Disposition | None:
    """Validate ``disposition``; ``None`` is legal."""
    raise NotImplementedError


def parse_work_record(data: Any) -> WorkRecord:
    """Validate a full ``work.json`` document, strictly."""
    raise NotImplementedError


def load_work_record(path: Path) -> WorkRecord:
    """Read and strictly validate ``work.json`` from disk."""
    raise NotImplementedError


def parse_conversation_binding(data: Any) -> ConversationBinding:
    """Validate a conversation binding document."""
    raise NotImplementedError


def load_conversation_binding(path: Path) -> ConversationBinding:
    """Read and validate ``conversations/<conversation_id>.json``."""
    raise NotImplementedError


def verify_sha256(path: Path, expected: str) -> bool:
    """True when the file's raw bytes hash to ``expected``."""
    raise NotImplementedError


def require_sha256(path: Path, expected: str, relative_path: str) -> None:
    """Raise :class:`StaleHash` unless the stored bytes still match."""
    raise NotImplementedError
