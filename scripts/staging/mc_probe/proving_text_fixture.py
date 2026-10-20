"""Operator-pinned frozen text fixtures, readable through the existing mc_repo_read tool.

Some exam material is a frozen document, not a file in Git (for example the plan MC reviews in X1).
The operator pins each one by an exact revision label and path label to a file on disk and its
SHA256. ``FixtureTools`` wraps ``RepositoryTools``:

- ``read_file`` on a fixture revision label returns the same shape as a Git read (revision, path,
  blob, sha256, start, total_lines, lines), with ``fixture: True``;
- everything else, and every ``diff``, ``run_suite`` and ``changes``, goes to the wrapped tools unchanged.

No new tool, no caller-chosen file: a fixture is reachable only by its exact pinned labels. Each
read opens the file again without following symlinks and checks size, hash and UTF-8, so a file
changed after pinning is refused, never served. The path is walked one component at a time from
"/" with O_NOFOLLOW, so a parent folder swapped for a symlink after pinning is refused too.
"""
import errno
import hashlib
import os
from pathlib import Path
import re
import stat

from proving_tools import ToolRefused

MAX_BYTES = 256_000                       # RepositoryTools' source size cap
_REVISION = re.compile(r"[a-z][a-z0-9-]{0,39}")
_PATH = re.compile(r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class FixtureTools:
    def __init__(self, repository_tools, fixtures):
        """``fixtures``: {revision_label: {path_label: {"file": "/absolute/path", "sha256": "<64 hex>"}}}."""
        self.tools = repository_tools
        if not isinstance(fixtures, dict) or not fixtures:
            raise ToolRefused("fixtures_required")
        self.fixtures = {}
        for revision, paths in fixtures.items():
            if not isinstance(revision, str) or not _REVISION.fullmatch(revision) or re.fullmatch(r"[0-9a-f]{7,40}", revision):
                raise ToolRefused("fixture_revision_label_invalid")
            if revision in getattr(repository_tools, "revisions", {}):
                raise ToolRefused("fixture_revision_label_collides_with_git_revision")
            if not isinstance(paths, dict) or not paths:
                raise ToolRefused("fixture_paths_required")
            pinned = {}
            for path, spec in paths.items():
                if not isinstance(path, str) or not _PATH.fullmatch(path) or ".." in path.split("/"):
                    raise ToolRefused("fixture_path_label_invalid")
                if not isinstance(spec, dict) or set(spec) != {"file", "sha256"}:
                    raise ToolRefused("fixture_spec_invalid")
                file, sha = spec["file"], spec["sha256"]
                if not isinstance(file, str) or not os.path.isabs(file) or not isinstance(sha, str) or not _SHA256.fullmatch(sha):
                    raise ToolRefused("fixture_spec_invalid")
                if str(Path(file).resolve()) != os.path.normpath(file):
                    raise ToolRefused("fixture_path_must_not_traverse_symlinks")
                pinned[path] = (file, sha)
                _load(file, sha)                  # refuse a bad pin at startup, not on first use
            self.fixtures[revision] = pinned

    def read_file(self, revision, path, start=1, count=120):
        if revision not in self.fixtures:
            return self.tools.read_file(revision, path, start, count)
        if type(start) is not int or type(count) is not int or start < 1 or not 1 <= count <= 200:
            raise ToolRefused("invalid_line_window")
        if not isinstance(path, str) or path not in self.fixtures[revision]:
            raise ToolRefused("path_not_allowlisted")
        file, sha = self.fixtures[revision][path]
        raw = _load(file, sha)
        lines = raw.decode("utf-8").splitlines()
        return {"revision": revision, "fixture": True, "path": path, "blob": _git_blob_id(raw), "sha256": sha,
                "start": start, "total_lines": len(lines), "lines": lines[start - 1:start - 1 + count]}

    def diff(self, before, after, path):
        if before in self.fixtures or after in self.fixtures:
            raise ToolRefused("fixture_diff_not_supported")
        return self.tools.diff(before, after, path)

    def run_suite(self, suite):
        return self.tools.run_suite(suite)

    def changes(self, revision):
        if revision in self.fixtures:
            raise ToolRefused("fixture_has_no_changes")
        return self.tools.changes(revision)


def _open_no_symlinks(file):
    """Open an absolute path one component at a time, never following a symlink anywhere on it."""
    parts = [part for part in file.split("/") if part]
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)  # a FIFO must not block
    finally:
        os.close(directory)


def _load(file, sha):
    """A regular, small, UTF-8 file whose hash is the pinned one, reached without any symlink."""
    try:
        fd = _open_no_symlinks(file)
    except OSError as exc:
        # O_NOFOLLOW on a symlink: ELOOP for the last component; ENOTDIR (or ELOOP) for a folder.
        if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
            raise ToolRefused("fixture_path_must_not_traverse_symlinks") from None
        raise ToolRefused("fixture_unavailable") from None
    if not stat.S_ISREG(os.fstat(fd).st_mode):        # before fdopen, which refuses a directory itself
        os.close(fd)
        raise ToolRefused("fixture_not_a_regular_file")
    with os.fdopen(fd, "rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ToolRefused("fixture_too_large")
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ToolRefused("fixture_hash_mismatch")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ToolRefused("fixture_not_utf8") from None
    return raw


def _git_blob_id(raw):
    """The id ``git hash-object`` would give these bytes, so a reader can compare like a Git read."""
    return hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest()
