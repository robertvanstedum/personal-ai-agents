"""Provenance-preserving bounded search and read. Read only.

Two kinds of root can be addressed:

configured source roots
    Named in deployment configuration. Everything found in one carries the
    provenance class ``robert_source``.

the virtual root ``approved:<subject>``
    A bounded, read-only projection over the subject's canonical work
    records. It exposes **only** the exact artifact named by a disposition
    whose state is ``approved_text`` — never continuing work, never an
    unapproved draft, never another artifact of the same work item. Each
    result keeps the artifact's original authorship class and carries the
    disposition reference that made it eligible. The bytes are re-hashed
    against the record before anything is returned; a mismatch is reported as
    ``stale_hash`` for that item and never passed off as current.

There is no list-all-work operation. The projection is internal to search and
read. Nothing here performs network input or output.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .confine import ConfinedFile
from .envelope import InvalidRequest, WorkError
from .records import WorkRecord
from .roots import RootConfiguration, SourceRoot

#: Prefix that addresses the subject-scoped approved-output projection.
APPROVED_ROOT_PREFIX = "approved:"

#: Ceilings. A request above either is refused, not silently clamped.
MAX_RESULTS_CEILING = 10
MAX_EXCERPT_CHARS_CEILING = 800

#: Layout of the canonical tree this projection reads.
SUBJECTS_DIRNAME = "subjects"
WORK_DIRNAME = "work"
WORK_RECORD_FILENAME = "work.json"
CONVERSATIONS_DIRNAME = "conversations"


@dataclass(frozen=True)
class DispositionRef:
    """Why an approved artifact is eligible for reuse."""

    work_id: str
    operation_id: str | None
    at: str


@dataclass(frozen=True)
class SearchHit:
    """One bounded excerpt, with everything needed to trust it."""

    subject: str
    root_ref: str
    relative_path: str
    sha256: str
    line_start: int
    line_end: int
    excerpt: str
    context_class: str
    disposition: DispositionRef | None = None


@dataclass(frozen=True)
class RetrievalIssue:
    """A per-item failure surfaced rather than dropped."""

    code: str
    root_ref: str
    relative_path: str
    message: str


@dataclass(frozen=True)
class SearchOutcome:
    """The bounded result of one search."""

    hits: tuple[SearchHit, ...]
    issues: tuple[RetrievalIssue, ...]


@dataclass(frozen=True)
class ReadOutcome:
    """One file's bytes, with its provenance."""

    subject: str
    root_ref: str
    relative_path: str
    sha256: str
    bytes: int
    content: str
    context_class: str
    disposition: DispositionRef | None = None


@dataclass(frozen=True)
class ApprovedArtifact:
    """One disposition-pinned artifact inside the approved projection."""

    work_id: str
    relative_path: str
    sha256: str
    context_class: str
    disposition: DispositionRef


def is_approved_root(root_ref: str) -> bool:
    """True when ``root_ref`` addresses the approved-output projection."""
    raise NotImplementedError


def approved_root_ref(subject: str) -> str:
    """The virtual root reference for ``subject``."""
    raise NotImplementedError


def tokenise(query: str) -> tuple[str, ...]:
    """Split a query into case-folded search tokens."""
    raise NotImplementedError


class Accumulation:
    """The read side of the accumulation reference.

    Career is the first subject; nothing in this class is Career-shaped. A
    second subject supplies its own configured roots and needs no code here.
    """

    def __init__(self, configuration: RootConfiguration) -> None:
        raise NotImplementedError

    # -- roots ---------------------------------------------------------

    def available_root_refs(self, subject: str) -> tuple[str, ...]:
        """Configured roots for ``subject`` plus its approved projection."""
        raise NotImplementedError

    # -- the approved projection ---------------------------------------

    def approved_artifacts(self, subject: str) -> tuple[tuple[ApprovedArtifact, ...], tuple[RetrievalIssue, ...]]:
        """Walk the subject's work records and project approved artifacts."""
        raise NotImplementedError

    # -- operations ----------------------------------------------------

    def search_sources(
        self,
        subject: str,
        root_refs: Sequence[str] | None,
        query: str,
        *,
        max_results: int = MAX_RESULTS_CEILING,
        max_excerpt_chars: int = MAX_EXCERPT_CHARS_CEILING,
    ) -> SearchOutcome:
        """Bounded, deterministic, confined search across the given roots."""
        raise NotImplementedError

    def read_source(self, subject: str, root_ref: str, relative_path: str) -> ReadOutcome:
        """Read one confined file from one authorized root."""
        raise NotImplementedError
