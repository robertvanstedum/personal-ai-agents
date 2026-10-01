"""Spec author, derived at read time and never stored (spec §4.3).

``spec_file`` is a basename. It is looked up in docs/specs/, then docs/design/,
then docs/ (never _working/ or anywhere else), and the first 30 lines are read
for an explicit author line: ``**Author:** <name>`` on its own line, or an
inline ``**Prepared by:** <name>``. No match, no file, or a spec_file that is
not a plain basename: the author is blank. Nothing is inferred from git,
commits or models. Results are cached by (path, mtime).
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

DOCS_ROOT = Path(__file__).resolve().parents[3] / "docs"
SEARCH = ("specs", "design", "")
HEAD_LINES = 30
AUTHOR = re.compile(r"^\*\*Author:\*\*\s*(.+)$")
PREPARED_BY = re.compile(r"\*\*Prepared by:\*\*\s*([^.*]+)")
NAME_MAX = 80

_cache: dict[tuple[str, int], str] = {}
_lock = threading.Lock()


def resolve(spec_file, docs_root: Path | None = None) -> Path | None:
    """The spec's path under docs/specs, docs/design or docs, or None."""
    if not isinstance(spec_file, str) or not spec_file.strip():
        return None
    name = spec_file.strip()
    if name != os.path.basename(name) or name in (".", "..") or "\\" in name:
        return None
    root = Path(docs_root or DOCS_ROOT)
    for sub in SEARCH:
        candidate = root / sub / name if sub else root / name
        try:
            if candidate.is_file():
                return candidate
        except (OSError, ValueError):
            # e.g. a name longer than the filesystem allows, or an unreadable
            # directory: the author is simply unknown (blank), never a 500.
            return None
    return None


def _read_author(path: Path) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as stream:
            for n, line in enumerate(stream):
                if n >= HEAD_LINES:
                    break
                line = line.rstrip("\n")
                found = AUTHOR.match(line) or PREPARED_BY.search(line)
                if found:
                    return found.group(1).strip()[:NAME_MAX]
    except OSError:
        return ""
    return ""


def spec_author(spec_file, docs_root: Path | None = None) -> str:
    """The explicit author of the spec, or "" (blank, never a guess)."""
    path = resolve(spec_file, docs_root)
    if path is None:
        return ""
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return ""
    key = (str(path), mtime)
    with _lock:
        if key in _cache:
            return _cache[key]
    author = _read_author(path)
    with _lock:
        if len(_cache) > 2048:
            _cache.clear()
        _cache[key] = author
    return author
