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

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .confine import (
    ConfinedFile,
    NotFound,
    confine,
    iter_files,
    read_text,
    sha256_file,
)
from .envelope import InvalidRequest, WorkError
from .records import RecordInvalid, StaleHash, load_work_record, verify_sha256
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

#: Provenance class every configured source root carries.
CONFIGURED_ROOT_CONTEXT_CLASS = "robert_source"

_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)


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
    return isinstance(root_ref, str) and root_ref.startswith(APPROVED_ROOT_PREFIX)


def approved_root_ref(subject: str) -> str:
    """The virtual root reference for ``subject``."""
    return f"{APPROVED_ROOT_PREFIX}{subject}"


def tokenise(query: str) -> tuple[str, ...]:
    """Split a query into case-folded search tokens."""
    if not isinstance(query, str):
        return ()
    seen: list[str] = []
    for match in _TOKEN_PATTERN.finditer(query.casefold()):
        token = match.group(0)
        if token and token not in seen:
            seen.append(token)
    return tuple(seen)


class Accumulation:
    """The read side of the accumulation reference.

    Career is the first subject; nothing in this class is Career-shaped. A
    second subject supplies its own configured roots and needs no code here.
    """

    def __init__(self, configuration: RootConfiguration) -> None:
        self._configuration = configuration

    @property
    def configuration(self) -> RootConfiguration:
        """The validated deployment configuration this reads from."""
        return self._configuration

    # -- roots ---------------------------------------------------------

    def available_root_refs(self, subject: str) -> tuple[str, ...]:
        """Configured roots for ``subject`` plus its approved projection."""
        refs = list(self._configuration.root_refs(subject))
        if self._configuration.work_available:
            refs.append(approved_root_ref(subject))
        return tuple(refs)

    def _approved_base(self, subject: str) -> Path:
        root = self._configuration.require_work_root()
        return root / SUBJECTS_DIRNAME / subject / WORK_DIRNAME

    # -- the approved projection ---------------------------------------

    def approved_artifacts(
        self, subject: str
    ) -> tuple[tuple[ApprovedArtifact, ...], tuple[RetrievalIssue, ...]]:
        """Walk the subject's work records and project approved artifacts."""
        root_ref = approved_root_ref(subject)
        base = self._approved_base(subject)
        items: list[ApprovedArtifact] = []
        issues: list[RetrievalIssue] = []
        if not base.is_dir():
            return (), ()

        for work_dir in sorted(p for p in base.iterdir() if p.is_dir() and not p.is_symlink()):
            record_path = work_dir / WORK_RECORD_FILENAME
            shown_record = f"{work_dir.name}/{WORK_RECORD_FILENAME}"
            if not record_path.is_file() or record_path.is_symlink():
                continue
            try:
                record = load_work_record(record_path)
            except RecordInvalid as exc:
                issues.append(
                    RetrievalIssue(
                        code=exc.code,
                        root_ref=root_ref,
                        relative_path=shown_record,
                        message=exc.message,
                    )
                )
                continue

            approved_ref = record.approved_artifact_ref
            if approved_ref is None:
                continue
            artifact = record.artifact(approved_ref)
            if artifact is None:
                issues.append(
                    RetrievalIssue(
                        code="invalid_request",
                        root_ref=root_ref,
                        relative_path=shown_record,
                        message="the approved disposition names an artifact this record does not have",
                    )
                )
                continue

            relative = f"{work_dir.name}/{artifact.path}"
            try:
                confined = confine(base, relative)
            except WorkError as exc:
                issues.append(
                    RetrievalIssue(
                        code=exc.code,
                        root_ref=root_ref,
                        relative_path=relative,
                        message=exc.message,
                    )
                )
                continue

            if not verify_sha256(confined.absolute_path, artifact.sha256):
                issues.append(
                    RetrievalIssue(
                        code="stale_hash",
                        root_ref=root_ref,
                        relative_path=relative,
                        message="the stored file no longer matches the hash recorded for it",
                    )
                )
                continue

            items.append(
                ApprovedArtifact(
                    work_id=record.work_id,
                    relative_path=relative,
                    sha256=artifact.sha256,
                    context_class=artifact.context_class,
                    disposition=DispositionRef(
                        work_id=record.work_id,
                        operation_id=record.disposition.operation_id if record.disposition else None,
                        at=record.disposition.at if record.disposition else "",
                    ),
                )
            )

        return tuple(items), tuple(issues)

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
        _require_subject(subject)
        tokens = tokenise(query)
        if not tokens:
            raise InvalidRequest("a search needs at least one word to look for")
        _require_bound(max_results, MAX_RESULTS_CEILING, "max_results")
        _require_bound(max_excerpt_chars, MAX_EXCERPT_CHARS_CEILING, "max_excerpt_chars")

        selected = self._select_roots(subject, root_refs)

        scored: list[tuple[int, str, str, int, SearchHit]] = []
        issues: list[RetrievalIssue] = []

        for root_ref in selected:
            if is_approved_root(root_ref):
                items, item_issues = self.approved_artifacts(subject)
                issues.extend(item_issues)
                base = self._approved_base(subject)
                for item in items:
                    confined = confine(base, item.relative_path)
                    scored.extend(
                        _score_file(
                            subject=subject,
                            root_ref=root_ref,
                            confined=confined,
                            tokens=tokens,
                            sha256=item.sha256,
                            context_class=item.context_class,
                            disposition=item.disposition,
                            max_excerpt_chars=max_excerpt_chars,
                        )
                    )
            else:
                root = self._configuration.resolve(subject, root_ref)
                for confined in iter_files(root.path):
                    scored.extend(
                        _score_file(
                            subject=subject,
                            root_ref=root_ref,
                            confined=confined,
                            tokens=tokens,
                            sha256=None,
                            context_class=CONFIGURED_ROOT_CONTEXT_CLASS,
                            disposition=None,
                            max_excerpt_chars=max_excerpt_chars,
                        )
                    )

        scored.sort(key=lambda entry: (-entry[0], entry[1], entry[2], entry[3]))
        hits = tuple(entry[4] for entry in scored[:max_results])
        return SearchOutcome(hits=hits, issues=tuple(issues))

    def read_source(self, subject: str, root_ref: str, relative_path: str) -> ReadOutcome:
        """Read one confined file from one authorized root."""
        _require_subject(subject)
        if not isinstance(root_ref, str) or not root_ref.strip():
            raise InvalidRequest("a source root reference is required")

        if is_approved_root(root_ref):
            if root_ref != approved_root_ref(subject):
                raise InvalidRequest("that approved view belongs to a different subject")
            base = self._approved_base(subject)
            confined = confine(base, relative_path)
            items, _ = self.approved_artifacts(subject)
            match = next(
                (item for item in items if item.relative_path == confined.relative_path), None
            )
            if match is None:
                stale = self._stale_candidate(subject, confined.relative_path)
                if stale is not None:
                    raise StaleHash(confined.relative_path)
                raise NotFound(confined.relative_path)
            return ReadOutcome(
                subject=subject,
                root_ref=root_ref,
                relative_path=confined.relative_path,
                sha256=match.sha256,
                bytes=confined.size,
                content=read_text(confined),
                context_class=match.context_class,
                disposition=match.disposition,
            )

        root: SourceRoot = self._configuration.resolve(subject, root_ref)
        confined = confine(root.path, relative_path)
        return ReadOutcome(
            subject=subject,
            root_ref=root_ref,
            relative_path=confined.relative_path,
            sha256=sha256_file(confined.absolute_path),
            bytes=confined.size,
            content=read_text(confined),
            context_class=CONFIGURED_ROOT_CONTEXT_CLASS,
            disposition=None,
        )

    # -- internals -----------------------------------------------------

    def _select_roots(self, subject: str, root_refs: Sequence[str] | None) -> tuple[str, ...]:
        """Resolve the requested roots, refusing anything not authorized."""
        available = self.available_root_refs(subject)
        if root_refs is None:
            return available
        if isinstance(root_refs, (str, bytes)):
            raise InvalidRequest("root references must be given as a list")
        requested = list(root_refs)
        if not requested:
            raise InvalidRequest("at least one source root is required")
        for ref in requested:
            if is_approved_root(ref):
                if ref != approved_root_ref(subject):
                    raise InvalidRequest("that approved view belongs to a different subject")
                self._configuration.require_work_root()
                continue
            self._configuration.resolve(subject, ref)
        chosen = set(requested)
        return tuple(ref for ref in available if ref in chosen)

    def _stale_candidate(self, subject: str, relative_path: str) -> str | None:
        """Return the issue code when the requested item was dropped as stale."""
        _, issues = self.approved_artifacts(subject)
        for issue in issues:
            if issue.relative_path == relative_path and issue.code == "stale_hash":
                return issue.code
        return None


def _require_subject(subject: str) -> None:
    if not isinstance(subject, str) or not subject.strip():
        raise InvalidRequest("a subject is required")


def _require_bound(value: int, ceiling: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise InvalidRequest(f"{name} must be a positive whole number")
    if value > ceiling:
        raise InvalidRequest(f"{name} may not be greater than {ceiling}")


def _score_file(
    *,
    subject: str,
    root_ref: str,
    confined: ConfinedFile,
    tokens: Sequence[str],
    sha256: str | None,
    context_class: str,
    disposition: DispositionRef | None,
    max_excerpt_chars: int,
) -> list[tuple[int, str, str, int, SearchHit]]:
    """Score one file and build its candidate hits.

    A line matches when it contains any query token as a case-insensitive
    substring. A file's score is the number of distinct tokens it matches
    anywhere. Ordering is then ``(-score, root_ref, relative_path,
    line_start)`` — a total order, so results are deterministic rather than
    ranked by an opinion.
    """
    try:
        text = read_text(confined)
    except WorkError:
        return []

    matched_tokens: set[str] = set()
    lines: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        folded = line.casefold()
        hit_tokens = [token for token in tokens if token in folded]
        if not hit_tokens:
            continue
        matched_tokens.update(hit_tokens)
        lines.append((number, line))

    if not lines:
        return []

    digest = sha256 if sha256 is not None else sha256_file(confined.absolute_path)
    score = len(matched_tokens)
    candidates = []
    for number, line in lines:
        excerpt = line.strip()[:max_excerpt_chars]
        candidates.append(
            (
                score,
                root_ref,
                confined.relative_path,
                number,
                SearchHit(
                    subject=subject,
                    root_ref=root_ref,
                    relative_path=confined.relative_path,
                    sha256=digest,
                    line_start=number,
                    line_end=number,
                    excerpt=excerpt,
                    context_class=context_class,
                    disposition=disposition,
                ),
            )
        )
    return candidates
