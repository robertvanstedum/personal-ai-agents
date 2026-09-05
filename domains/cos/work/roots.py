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

from .envelope import InvalidRequest, WorkError

#: Absolute path of the canonical write root.
ENV_WORK_ROOT = "COS_WORK_ROOT"

#: Authorized read-only source roots, inline JSON:
#: ``{"<subject>": {"<root_ref>": "/absolute/path", ...}, ...}``
ENV_SOURCE_ROOTS = "COS_WORK_SOURCE_ROOTS"

#: Path to a file holding the same JSON document. Used when the inline
#: variable is absent; the inline variable wins when both are set.
ENV_SOURCE_ROOTS_FILE = "COS_WORK_SOURCE_ROOTS_FILE"

#: Directory names never searched or exposed inside any root.
EXCLUDED_DIRECTORY_NAMES: frozenset[str] = frozenset({".git"})

#: Permission bits that must be clear on every root: no group, no other.
NON_OWNER_PERMISSION_BITS = 0o077


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

    def require_work_root(self) -> Path:
        """Return the validated write root or fail closed."""
        raise NotImplementedError

    def subjects(self) -> tuple[str, ...]:
        """Subjects that have at least one valid configured source root."""
        raise NotImplementedError

    def root_refs(self, subject: str) -> tuple[str, ...]:
        """Configured, valid source-root references for ``subject``."""
        raise NotImplementedError

    def resolve(self, subject: str, ref: str) -> SourceRoot:
        """Return one configured source root, or fail closed."""
        raise NotImplementedError


def find_checkout_root(start: Path | None = None) -> Path | None:
    """Locate the enclosing repository checkout.

    Uses ``git rev-parse --show-toplevel`` from this module's directory and
    falls back to walking up for a directory that contains ``.git``.
    """
    raise NotImplementedError


def is_inside(candidate: Path, ancestor: Path) -> bool:
    """True when ``candidate`` is ``ancestor`` or lives beneath it."""
    raise NotImplementedError


def has_symlink_component(path: Path) -> bool:
    """True when any component of ``path`` is a symbolic link."""
    raise NotImplementedError


def is_owner_private(path: Path) -> bool:
    """True when ``path`` is owned by this process and closed to others."""
    raise NotImplementedError


def validate_directory(path: Path) -> str | None:
    """Return a plain-language reason the directory is unusable, or None."""
    raise NotImplementedError


def is_git_ignored(path: Path, checkout_root: Path) -> bool:
    """True when the enclosing public repository ignores ``path``."""
    raise NotImplementedError


def has_tracked_files(path: Path, checkout_root: Path) -> bool:
    """True when the enclosing public repository tracks any file inside."""
    raise NotImplementedError


def validate_work_root(raw: str | None, checkout_root: Path | None) -> tuple[Path | None, RootIssue | None]:
    """Validate the canonical write root. Fails closed."""
    raise NotImplementedError


def validate_source_root(
    subject: str,
    ref: str,
    raw: str,
    checkout_root: Path | None,
) -> tuple[SourceRoot | None, RootIssue | None]:
    """Validate one configured read-only source root. Fails closed."""
    raise NotImplementedError


def parse_source_root_declaration(raw: str) -> Mapping[str, Mapping[str, str]]:
    """Parse the deployment JSON declaration of source roots."""
    raise NotImplementedError


def load_root_configuration(
    env: Mapping[str, str] | None = None,
    *,
    checkout_root: Path | None = None,
) -> RootConfiguration:
    """Read and validate every configured root once, failing closed per root."""
    raise NotImplementedError


def is_narrowing(configured_refs: Iterable[str], requested_refs: Iterable[str]) -> bool:
    """True when the request is a non-empty subset of what is configured.

    This is the only direction a grant may move: a model can select fewer
    roots than deployment configured, never more, and never a new one.
    """
    raise NotImplementedError


def narrow(
    configuration: RootConfiguration,
    subject: str,
    requested_refs: Iterable[str] | None,
) -> tuple[SourceRoot, ...]:
    """Resolve a requested subset of a subject's configured roots."""
    raise NotImplementedError
