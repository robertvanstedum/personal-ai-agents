"""Bounded, read-only repository references for the owner Guild UI."""
from pathlib import Path, PurePosixPath
from functools import lru_cache
from itertools import islice
import subprocess
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
ROOTS = ("docs", "planning-studio/initiatives", "prototype-lab/projects")
CORE = ("ARCHITECTURE.md", "OPERATIONS.md", "ROADMAP.md")

def resolve(reference, root=None):
    root = Path(root or ROOT).resolve()
    if not isinstance(reference, str) or not reference or "\\" in reference:
        return None
    name = PurePosixPath(reference)
    if name.is_absolute() or any(p in ("", ".", "..") for p in reference.split("/")) or name.suffix.lower() != ".md":
        return None
    candidates = [root / reference]
    if len(name.parts) == 1 and reference not in CORE:
        candidates = [root / d / reference for d in ("docs/specs", "docs/design", "docs", "_working")]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            allowed = any(resolved.is_relative_to(root / d) for d in ROOTS)
            allowed |= resolved in [root / f for f in CORE]
            # Preserve exact legacy flat references, never enumerate private working files.
            allowed |= len(name.parts) == 1 and resolved.parent == root / "_working"
            if allowed and resolved.is_file() and resolved.stat().st_size <= 1_000_000:
                return resolved
        except (OSError, ValueError):
            continue
    return None

def catalog(root=None, directory="docs"):
    root = Path(root or ROOT).resolve()
    folder = root / directory
    if directory not in ROOTS and directory not in ("docs/retrospectives", "docs/design", "scripts/tools"):
        return [], "Unavailable source"
    if not folder.is_dir():
        return [], "Source directory is not available in this checkout"
    rows = []
    try:
        for path in sorted(folder.rglob("*.md")):
            ref = path.relative_to(root).as_posix()
            found = resolve(ref, root)
            if not found:
                continue
            from datetime import datetime, timezone
            rows.append({"reference": ref, "title": _title(found),
                         "updated": datetime.fromtimestamp(found.stat().st_mtime, timezone.utc).isoformat(),
                         "status": "Review status unknown"})
            if len(rows) >= 400:
                return rows, "Showing the first 400 documents; narrow the source to see more"
    except OSError:
        return [], "Source directory could not be read"
    return rows, None


def category(reference):
    """Group by repository location; never infer review or release state."""
    parts = reference.lower().split("/")
    if "archive" in parts or "archived" in parts: return "Archive"
    for key, label in (("specs", "Specs"), ("design", "Design"), ("retrospectives", "Retrospectives"),
                       ("decisions", "Decision Records"), ("releases", "Releases"),
                       ("process", "Process"), ("portfolio", "Portfolio"), ("journal", "Journal")):
        if key in parts: return label
    if reference.split("/")[-1] in CORE: return "Core"
    return "Unclassified"

def grouped(rows):
    groups = {}
    for row in rows:
        groups.setdefault(category(row["reference"]), []).append(row)
    return sorted(groups.items(), key=lambda kv: (kv[0] == "Unclassified", kv[0]))


def _title(path):
    try:
        with path.open(encoding="utf-8") as source:
            for line in islice(source, 60):
                if line.startswith("# "):
                    return line[2:].strip()
    except (OSError, UnicodeError):
        pass
    return path.stem.replace("_", " ")


def published_metadata(path, root):
    """Only link a published main version whose content matches this copy.

    Local release commits may not exist on GitHub. Images without Git simply
    offer the local reader/download. Cache by file stat; never fetch on a request.
    """
    try:
        stat = path.stat()
        return _published(str(root), path.relative_to(root).as_posix(), stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError):
        return None, None


@lru_cache(maxsize=512)
def _published(root, relative, mtime, size):
    try:
        def git(*args):
            return subprocess.run(["git", *args], cwd=root, capture_output=True,
                                  timeout=2, check=True).stdout
        commit = git("rev-parse", "refs/remotes/origin/main").decode().strip()
        published = git("show", f"{commit}:{relative}")
        if published != (Path(root) / relative).read_bytes():
            return None, None
        date = git("log", "-1", "--format=%cI", commit, "--", relative).decode().strip()
        return "https://github.com/robertvanstedum/personal-ai-agents/blob/" + commit + "/" + quote(relative), date or None
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return None, None
