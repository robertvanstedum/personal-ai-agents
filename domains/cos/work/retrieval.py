"""Provenance-preserving bounded search and read. Read only.

Two kinds of root can be addressed:

configured source roots
    Named in deployment configuration. Each declares its own provenance class
    explicitly, and every result carries that declared class unchanged. A root
    declared ``external_source``, ``agent_draft`` or ``coauthored_output`` is
    never relabelled as Robert's own writing, because there is no default to
    fall back to.

the virtual root ``approved:<subject>``
    A bounded, read-only projection over the subject's canonical work
    records. It exposes **only** the exact artifact named by a disposition
    whose state is ``approved_text`` on a record whose own state agrees —
    never continuing work, never an unapproved draft, never another artifact
    of the same work item. Each result keeps the artifact's original
    authorship class and carries the disposition reference that made it
    eligible.

Membership in that projection is the *authorization* boundary for reading it,
not merely a listing convenience. A read names a relative path; the path is
resolved against a freshly derived projection and must be an exact eligible
member whose pinned digest still matches the stored bytes. Anything else is
``not_found``: knowing an unapproved artifact's path buys nothing. The one
distinction made is for an artifact that *is* pinned but whose bytes have
changed underneath: that is reported as ``stale_context`` rather than passed
off as current, because concealing it would hide a real problem with material
Robert already approved.

Search is deliberately simple and deterministic: any-token substring matching,
a total ordering, and hard ceilings on results, excerpt length, files examined
and bytes examined. A traversal that hits a budget reports a content-free
``search_truncated`` issue, so a partial answer is never presented as a
complete one. There is no relevance engine and no index.

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
    UnsupportedMedia,
    confine,
    iter_files,
    normalise_relative,
    read_bytes,
    read_text,
    sha256_bytes,
    sha256_confined,
)
from .envelope import (
    SEARCH_TRUNCATED,
    InvalidRequest,
    WorkError,
    is_identifier,
    require_identifier,
)
from .records import RecordInvalid, StaleContext, load_work_record
from .roots import APPROVED_REF_PREFIX, RootConfiguration, SourceRoot

#: Prefix that addresses the subject-scoped approved-output projection.
APPROVED_ROOT_PREFIX = APPROVED_REF_PREFIX

#: Ceilings. A request above any of these is refused, not silently clamped.
MAX_RESULTS_CEILING = 10
MAX_EXCERPT_CHARS_CEILING = 800

#: Traversal budget. Result and excerpt ceilings bound what comes *back*;
#: these bound the work done to produce it, so a very large loose root cannot
#: turn one search into an unbounded walk.
MAX_QUERY_CHARS = 256
MAX_QUERY_TOKENS = 16
DEFAULT_MAX_FILES_EXAMINED = 2_000
MAX_FILES_EXAMINED_CEILING = 20_000
DEFAULT_MAX_BYTES_EXAMINED = 32 * 1024 * 1024
MAX_BYTES_EXAMINED_CEILING = 256 * 1024 * 1024

#: Layout of the canonical tree this projection reads.
SUBJECTS_DIRNAME = "subjects"
WORK_DIRNAME = "work"
WORK_RECORD_FILENAME = "work.json"
CONVERSATIONS_DIRNAME = "conversations"

_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class DispositionRef:
    """Why an approved artifact is eligible for reuse."""

    work_id: str
    operation_id: str | None
    decided_at: str


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
    """A per-item failure or partial-search notice, surfaced not dropped."""

    code: str
    root_ref: str
    relative_path: str
    message: str


@dataclass(frozen=True)
class SearchOutcome:
    """The bounded result of one search."""

    hits: tuple[SearchHit, ...]
    issues: tuple[RetrievalIssue, ...]

    @property
    def truncated(self) -> bool:
        """True when a budget stopped a root short of a full walk."""
        return any(issue.code == SEARCH_TRUNCATED for issue in self.issues)


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


class _Budget:
    """A deterministic traversal allowance, spent as files are examined."""

    def __init__(self, max_files: int, max_bytes: int) -> None:
        self.max_files = max_files
        self.max_bytes = max_bytes
        self.files = 0
        self.bytes = 0
        self.exhausted = False

    def take(self, size: int) -> bool:
        """Charge one file. False means the budget is spent; stop walking."""
        if self.files + 1 > self.max_files or self.bytes + size > self.max_bytes:
            self.exhausted = True
            return False
        self.files += 1
        self.bytes += size
        return True


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
        """Walk the subject's work records and project approved artifacts.

        Freshly derived on every call. Nothing is cached, so a record edited
        or a file changed since the last read is seen now, not later.
        """
        require_identifier(subject, "subject")
        root_ref = approved_root_ref(subject)
        base = self._approved_base(subject)
        items: list[ApprovedArtifact] = []
        issues: list[RetrievalIssue] = []
        if not base.is_dir() or _has_link_below(
            self._configuration.require_work_root(), base
        ):
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
                        message=(
                            "the approved disposition names an artifact this record "
                            "does not have"
                        ),
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

            # Content authenticity comes first: a file whose bytes have
            # changed is stale, whatever its recorded size says. The byte
            # count is then checked as a record invariant of its own, so a
            # record that disagrees with an unchanged file is still refused.
            if sha256_confined(confined) != artifact.sha256:
                issues.append(
                    RetrievalIssue(
                        code="stale_context",
                        root_ref=root_ref,
                        relative_path=relative,
                        message="the stored file no longer matches the hash recorded for it",
                    )
                )
                continue

            if artifact.bytes is not None and artifact.bytes != confined.size:
                issues.append(
                    RetrievalIssue(
                        code="invalid_request",
                        root_ref=root_ref,
                        relative_path=relative,
                        message="the recorded size does not match the stored file",
                    )
                )
                continue

            disposition = record.disposition
            items.append(
                ApprovedArtifact(
                    work_id=record.work_id,
                    relative_path=relative,
                    sha256=artifact.sha256,
                    context_class=artifact.context_class,
                    disposition=DispositionRef(
                        work_id=record.work_id,
                        operation_id=disposition.operation_id if disposition else None,
                        decided_at=disposition.decided_at if disposition else "",
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
        max_files_examined: int = DEFAULT_MAX_FILES_EXAMINED,
        max_bytes_examined: int = DEFAULT_MAX_BYTES_EXAMINED,
    ) -> SearchOutcome:
        """Bounded, deterministic, confined search across the given roots."""
        require_identifier(subject, "subject")
        if not isinstance(query, str):
            raise InvalidRequest("a search needs a text query")
        if len(query) > MAX_QUERY_CHARS:
            raise InvalidRequest(
                f"a query may not be longer than {MAX_QUERY_CHARS} characters"
            )
        tokens = tokenise(query)
        if not tokens:
            raise InvalidRequest("a search needs at least one word to look for")
        if len(tokens) > MAX_QUERY_TOKENS:
            raise InvalidRequest(
                f"a query may not carry more than {MAX_QUERY_TOKENS} distinct words"
            )
        _require_bound(max_results, MAX_RESULTS_CEILING, "max_results")
        _require_bound(max_excerpt_chars, MAX_EXCERPT_CHARS_CEILING, "max_excerpt_chars")
        _require_bound(max_files_examined, MAX_FILES_EXAMINED_CEILING, "max_files_examined")
        _require_bound(max_bytes_examined, MAX_BYTES_EXAMINED_CEILING, "max_bytes_examined")

        selected = self._select_roots(subject, root_refs)

        scored: list[tuple[int, str, str, int, SearchHit]] = []
        issues: list[RetrievalIssue] = []

        for root_ref in selected:
            budget = _Budget(max_files_examined, max_bytes_examined)
            if is_approved_root(root_ref):
                items, item_issues = self.approved_artifacts(subject)
                issues.extend(item_issues)
                base = self._approved_base(subject)
                for item in items:
                    confined = confine(base, item.relative_path)
                    if not budget.take(confined.size):
                        break
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
                    if not budget.take(confined.size):
                        break
                    scored.extend(
                        _score_file(
                            subject=subject,
                            root_ref=root_ref,
                            confined=confined,
                            tokens=tokens,
                            sha256=None,
                            context_class=root.context_class,
                            disposition=None,
                            max_excerpt_chars=max_excerpt_chars,
                        )
                    )
            if budget.exhausted:
                issues.append(
                    RetrievalIssue(
                        code=SEARCH_TRUNCATED,
                        root_ref=root_ref,
                        relative_path="",
                        message="this source was not searched all the way through",
                    )
                )

        scored.sort(key=lambda entry: (-entry[0], entry[1], entry[2], entry[3]))
        hits = tuple(entry[4] for entry in scored[:max_results])
        return SearchOutcome(hits=hits, issues=tuple(issues))

    def read_source(self, subject: str, root_ref: str, relative_path: str) -> ReadOutcome:
        """Read one confined file from one authorized root."""
        require_identifier(subject, "subject")
        if not isinstance(root_ref, str) or not root_ref.strip():
            raise InvalidRequest("a source root reference is required")

        if is_approved_root(root_ref):
            return self._read_approved(subject, root_ref, relative_path)

        require_identifier(root_ref, "root_ref")
        root: SourceRoot = self._configuration.resolve(subject, root_ref)
        confined = confine(root.path, relative_path)
        raw = read_bytes(confined)
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedMedia(confined.relative_path) from exc
        return ReadOutcome(
            subject=subject,
            root_ref=root_ref,
            relative_path=confined.relative_path,
            sha256=sha256_bytes(raw),
            bytes=len(raw),
            content=content,
            context_class=root.context_class,
            disposition=None,
        )

    def _read_approved(self, subject: str, root_ref: str, relative_path: str) -> ReadOutcome:
        """Read one artifact the approved projection actually authorizes.

        The requested path is matched against the freshly derived projection
        *before* anything on disk is opened. A path that is not an exact
        eligible member is ``not_found`` whether it exists or not, so a caller
        who has guessed an unapproved artifact's name learns nothing from the
        answer.
        """
        if root_ref != approved_root_ref(subject):
            raise InvalidRequest("that approved view belongs to a different subject")
        shown = str(normalise_relative(relative_path))

        items, _ = self.approved_artifacts(subject)
        match = next((item for item in items if item.relative_path == shown), None)
        if match is None:
            if self._is_pinned_but_stale(subject, shown):
                raise StaleContext(shown)
            raise NotFound(shown)

        base = self._approved_base(subject)
        confined = confine(base, match.relative_path)
        raw = read_bytes(confined)
        if sha256_bytes(raw) != match.sha256:
            raise StaleContext(shown)
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedMedia(confined.relative_path) from exc
        return ReadOutcome(
            subject=subject,
            root_ref=root_ref,
            relative_path=confined.relative_path,
            sha256=match.sha256,
            bytes=len(raw),
            content=content,
            context_class=match.context_class,
            disposition=match.disposition,
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
                remainder = ref[len(APPROVED_ROOT_PREFIX) :]
                if not is_identifier(remainder):
                    raise InvalidRequest("root_ref is not a valid name")
                if ref != approved_root_ref(subject):
                    raise InvalidRequest("that approved view belongs to a different subject")
                self._configuration.require_work_root()
                continue
            require_identifier(ref, "root_ref")
            self._configuration.resolve(subject, ref)
        chosen = set(requested)
        return tuple(ref for ref in available if ref in chosen)

    def _is_pinned_but_stale(self, subject: str, relative_path: str) -> bool:
        """True when the requested item is approved but its bytes have changed.

        Only a disposition-pinned artifact can reach this state, so saying so
        reveals nothing an approval had not already made retrievable — and
        staying silent would hide a real change to approved material.
        """
        _, issues = self.approved_artifacts(subject)
        return any(
            issue.relative_path == relative_path and issue.code == "stale_context"
            for issue in issues
        )


def _has_link_below(root: Path, path: Path) -> bool:
    """True when any component between ``root`` and ``path`` is a symlink."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


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

    digest = sha256 if sha256 is not None else sha256_confined(confined)
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
