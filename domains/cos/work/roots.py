"""Root configuration and fail-closed validation.

Two distinct kinds of root exist, with distinct rules:

``COS_WORK_ROOT``
    The canonical write root (used by the write-side service in a later
    gate; validated here so the read side can project approved outputs out
    of it). It must be absolute, an existing directory, owner-private, free
    of symlinks at every component, and **outside** the repository checkout.
    Any failure makes Work unavailable — ``work_root_unavailable`` — without
    disturbing ordinary conversation, and never falls back to another
    location.

Authorized read-only source roots
    Declared **only** by deployment configuration, never by a model. Each
    must be absolute, an existing directory, owner-private and non-symlinked.
    A source root may live inside the checkout only when the enclosing public
    repository ignores it *and* tracks no file inside it. A nested private
    repository inside a source root is allowed; its version-control metadata
    is never searched or exposed.

Validation happens once at load time and fails closed per root: an invalid
root is dropped and reported as an error record, never silently used. A work
grant may *narrow* a configured root set; it can never add or widen one.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .envelope import WorkError

#: Absolute path of the canonical write root.
ENV_WORK_ROOT = "COS_WORK_ROOT"

#: Authorized read-only source roots, inline JSON:
#: ``{"<subject>": {"<root_ref>": "/absolute/path", ...}, ...}``
ENV_SOURCE_ROOTS = "COS_WORK_SOURCE_ROOTS"

#: Path to a file holding the same JSON document. Used when the inline
#: variable is absent; the inline variable wins when both are set.
ENV_SOURCE_ROOTS_FILE = "COS_WORK_SOURCE_ROOTS_FILE"

#: Permission bits that must be clear on every root: no group, no other.
NON_OWNER_PERMISSION_BITS = 0o077

_GIT_TIMEOUT_SECONDS = 15


class RootUnavailable(WorkError):
    """The canonical write root is missing or unsafe."""

    def __init__(self, message: str) -> None:
        super().__init__("work_root_unavailable", message)


class SourceRootUnavailable(WorkError):
    """A named source root is unknown, invalid, or not granted."""

    def __init__(self, message: str, *, root_ref: str | None = None) -> None:
        super().__init__("source_root_unavailable", message, root_ref=root_ref)


@dataclass(frozen=True)
class SourceRoot:
    """One validated, read-only source root."""

    subject: str
    ref: str
    path: Path
    inside_checkout: bool = False


@dataclass(frozen=True)
class RootIssue:
    """A dropped root, reported rather than silently used.

    Deliberately content-free: the subject, the configured reference, the
    error code and a short plain-language reason. No absolute path.
    """

    subject: str
    ref: str
    code: str
    reason: str


@dataclass(frozen=True)
class RootConfiguration:
    """The validated result of reading deployment configuration."""

    work_root: Path | None
    work_root_issue: RootIssue | None
    source_roots: Mapping[str, Mapping[str, SourceRoot]]
    issues: tuple[RootIssue, ...]
    checkout_root: Path | None

    @property
    def work_available(self) -> bool:
        """True when the canonical write root passed validation."""
        return self.work_root is not None

    @property
    def source_root_issues(self) -> tuple[RootIssue, ...]:
        """Only the dropped source roots, without the write-root outcome."""
        return tuple(issue for issue in self.issues if issue.code == "source_root_unavailable")

    def require_work_root(self) -> Path:
        """Return the validated write root or fail closed."""
        if self.work_root is None:
            reason = self.work_root_issue.reason if self.work_root_issue else "not configured"
            raise RootUnavailable(f"the work area is unavailable: {reason}")
        return self.work_root

    def subjects(self) -> tuple[str, ...]:
        """Subjects that have at least one valid configured source root."""
        return tuple(subject for subject, refs in self.source_roots.items() if refs)

    def root_refs(self, subject: str) -> tuple[str, ...]:
        """Configured, valid source-root references for ``subject``."""
        return tuple(self.source_roots.get(subject, {}))

    def resolve(self, subject: str, ref: str) -> SourceRoot:
        """Return one configured source root, or fail closed."""
        root = self.source_roots.get(subject, {}).get(ref)
        if root is None:
            raise SourceRootUnavailable(
                "that source is not one of the authorized roots", root_ref=ref
            )
        return root


def find_checkout_root(start: Path | None = None) -> Path | None:
    """Locate the enclosing repository checkout.

    Uses ``git rev-parse --show-toplevel`` from this module's directory and
    falls back to walking up for a directory that contains ``.git``.
    """
    origin = (start or Path(__file__).resolve().parent)
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(origin),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return Path(completed.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    for candidate in [origin, *origin.parents]:
        if (candidate / ".git").exists():
            return candidate.resolve()
    return None


def is_inside(candidate: Path, ancestor: Path) -> bool:
    """True when ``candidate`` is ``ancestor`` or lives beneath it."""
    candidate = Path(os.path.realpath(candidate))
    ancestor = Path(os.path.realpath(ancestor))
    return candidate == ancestor or ancestor in candidate.parents


def has_symlink_component(path: Path) -> bool:
    """True when any component of ``path`` is a symbolic link."""
    current = Path(path.anchor or os.sep)
    for part in path.parts[1:] if path.is_absolute() else path.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def is_owner_private(path: Path) -> bool:
    """True when ``path`` is owned by this process and closed to others.

    "Owner-private" means the process user owns it and no group or other
    permission bit is set. A ``0700`` directory passes; ``0750`` and ``0755``
    do not.
    """
    try:
        info = path.stat()
    except OSError:
        return False
    if info.st_uid != os.geteuid():
        return False
    return stat.S_IMODE(info.st_mode) & NON_OWNER_PERMISSION_BITS == 0


def validate_directory(path: Path) -> str | None:
    """Return a plain-language reason the directory is unusable, or None."""
    if not path.is_absolute():
        return "the configured path is not absolute"
    if has_symlink_component(path):
        return "the configured path contains a symbolic link"
    if not path.exists():
        return "the configured directory does not exist"
    if not path.is_dir():
        return "the configured path is not a directory"
    if not is_owner_private(path):
        return "the configured directory is not owner-private"
    return None


def is_git_ignored(path: Path, checkout_root: Path) -> bool:
    """True when the enclosing public repository ignores ``path``.

    ``--no-index`` keeps this answering the question actually asked — do the
    ignore rules cover this path — rather than silently reporting "not
    ignored" for a directory that happens to contain a tracked file. Tracking
    is the separate gate in :func:`has_tracked_files`.
    """
    try:
        completed = subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", "--", str(path)],
            cwd=str(checkout_root),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def has_tracked_files(path: Path, checkout_root: Path) -> bool:
    """True when the enclosing public repository tracks any file inside."""
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--", str(path)],
            cwd=str(checkout_root),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if completed.returncode != 0:
        return True
    return bool(completed.stdout.strip())


def validate_work_root(
    raw: str | None, checkout_root: Path | None
) -> tuple[Path | None, RootIssue | None]:
    """Validate the canonical write root. Fails closed."""

    def refuse(reason: str) -> tuple[None, RootIssue]:
        return None, RootIssue(
            subject="", ref=ENV_WORK_ROOT, code="work_root_unavailable", reason=reason
        )

    if raw is None or not str(raw).strip():
        return refuse("no work root is configured")
    path = Path(str(raw).strip())
    reason = validate_directory(path)
    if reason is not None:
        return refuse(reason)
    if checkout_root is not None and is_inside(path, checkout_root):
        return refuse("the work root must be outside the repository checkout")
    return Path(os.path.realpath(path)), None


def validate_source_root(
    subject: str,
    ref: str,
    raw: str,
    checkout_root: Path | None,
) -> tuple[SourceRoot | None, RootIssue | None]:
    """Validate one configured read-only source root. Fails closed."""

    def refuse(reason: str) -> tuple[None, RootIssue]:
        return None, RootIssue(
            subject=subject, ref=ref, code="source_root_unavailable", reason=reason
        )

    if not isinstance(raw, str) or not raw.strip():
        return refuse("the configured path is empty")
    path = Path(raw.strip())
    reason = validate_directory(path)
    if reason is not None:
        return refuse(reason)

    inside = checkout_root is not None and is_inside(path, checkout_root)
    if inside:
        assert checkout_root is not None
        if not is_git_ignored(path, checkout_root):
            return refuse(
                "a source root inside the checkout must be ignored by the repository"
            )
        if has_tracked_files(path, checkout_root):
            return refuse(
                "a source root inside the checkout must contain no tracked files"
            )
    return (
        SourceRoot(
            subject=subject, ref=ref, path=Path(os.path.realpath(path)), inside_checkout=inside
        ),
        None,
    )


def parse_source_root_declaration(raw: str) -> Mapping[str, Mapping[str, str]]:
    """Parse the deployment JSON declaration of source roots."""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("source-root declaration is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("source-root declaration must map subjects to roots")
    declaration: dict[str, dict[str, str]] = {}
    for subject, refs in parsed.items():
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("each subject must be a non-empty name")
        if not isinstance(refs, dict):
            raise ValueError("each subject must map root references to paths")
        entries: dict[str, str] = {}
        for ref, path in refs.items():
            if not isinstance(ref, str) or not ref.strip():
                raise ValueError("each root reference must be a non-empty name")
            if not isinstance(path, str):
                raise ValueError("each root path must be a string")
            entries[ref] = path
        declaration[subject] = entries
    return declaration


def load_root_configuration(
    env: Mapping[str, str] | None = None,
    *,
    checkout_root: Path | None = None,
) -> RootConfiguration:
    """Read and validate every configured root once, failing closed per root."""
    env = os.environ if env is None else env
    if checkout_root is None:
        checkout_root = find_checkout_root()

    work_root, work_root_issue = validate_work_root(env.get(ENV_WORK_ROOT), checkout_root)

    issues: list[RootIssue] = []
    if work_root_issue is not None:
        issues.append(work_root_issue)

    raw_declaration = env.get(ENV_SOURCE_ROOTS)
    if raw_declaration is None:
        declaration_file = env.get(ENV_SOURCE_ROOTS_FILE)
        if declaration_file:
            try:
                raw_declaration = Path(declaration_file).read_text("utf-8")
            except OSError:
                raw_declaration = None
                issues.append(
                    RootIssue(
                        subject="",
                        ref=ENV_SOURCE_ROOTS_FILE,
                        code="source_root_unavailable",
                        reason="the source-root declaration file could not be read",
                    )
                )

    source_roots: dict[str, dict[str, SourceRoot]] = {}
    if raw_declaration:
        try:
            declaration = parse_source_root_declaration(raw_declaration)
        except ValueError as exc:
            declaration = {}
            issues.append(
                RootIssue(
                    subject="",
                    ref=ENV_SOURCE_ROOTS,
                    code="source_root_unavailable",
                    reason=str(exc),
                )
            )
        for subject, refs in declaration.items():
            valid: dict[str, SourceRoot] = {}
            for ref, raw_path in refs.items():
                root, issue = validate_source_root(subject, ref, raw_path, checkout_root)
                if issue is not None:
                    issues.append(issue)
                    continue
                assert root is not None
                valid[ref] = root
            source_roots[subject] = valid

    return RootConfiguration(
        work_root=work_root,
        work_root_issue=work_root_issue,
        source_roots=source_roots,
        issues=tuple(issues),
        checkout_root=checkout_root,
    )


def is_narrowing(configured_refs: Iterable[str], requested_refs: Iterable[str]) -> bool:
    """True when the request is a non-empty subset of what is configured.

    This is the only direction a grant may move: a model can select fewer
    roots than deployment configured, never more, and never a new one.
    """
    configured = set(configured_refs)
    requested = list(requested_refs)
    if not requested:
        return False
    return set(requested).issubset(configured)


def narrow(
    configuration: RootConfiguration,
    subject: str,
    requested_refs: Iterable[str] | None,
) -> tuple[SourceRoot, ...]:
    """Resolve a requested subset of a subject's configured roots."""
    configured = configuration.root_refs(subject)
    if requested_refs is None:
        return tuple(configuration.resolve(subject, ref) for ref in configured)
    requested = list(requested_refs)
    if not is_narrowing(configured, requested):
        unknown = sorted(set(requested) - set(configured))
        raise SourceRootUnavailable(
            "that source is not one of the authorized roots",
            root_ref=unknown[0] if unknown else None,
        )
    return tuple(configuration.resolve(subject, ref) for ref in configured if ref in set(requested))
