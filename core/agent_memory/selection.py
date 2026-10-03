"""Which files may be copied (agent-memory v0.4 §3.3, §3.5).

Defaults copy only ``MEMORY.md`` and ``memory/**/*.md``. Instruction and
persona files (``SOUL.md``, ``AGENTS.md``, skills ...) are copied only when a
source lists them in its ``include``. ``never_copy`` is applied FIRST and is
counted by reason code without naming files. Every refusal is a fixed code;
nothing here keeps or logs a file name.
"""
from __future__ import annotations

import fnmatch
import re
from collections import Counter
from dataclasses import dataclass, field

MAX_BYTES = 512 * 1024
DEFAULT_PATTERNS: tuple[str, ...] = ("MEMORY.md", "memory/**/*.md")
PERSONA_NAMES = frozenset({"soul.md", "agents.md", "identity.md", "claude.md",
                           "user.md", "tools.md", "heartbeat.md", "bootstrap.md"})
PERSONA_DIRS = frozenset({"skills"})

# Skip reason codes (fixed vocabulary; counted, never paired with a name).
NEVER_COPY = "never_copy"
UNSAFE_PATH = "unsafe_path"
HIDDEN = "hidden"
SYMLINK = "symlink"
SPECIAL = "special"
SESSIONS = "sessions"
DENIED_NAME = "denied_name"
DENIED_EXT = "denied_ext"
NOT_MARKDOWN = "not_markdown"
PERSONA_NOT_INCLUDED = "persona_not_included"
NOT_SELECTED = "not_selected"
TOO_LARGE = "too_large"
NOT_UTF8 = "not_utf8"
DUPLICATE_PATH = "duplicate_path"

_DENIED_EXT = re.compile(r"\.(key|pem|json|db|jsonl)$|\.sqlite")


def glob_match(pattern: str, path: str) -> bool:
    """Match a relative path to a glob where ``*`` stays in one folder and ``**`` crosses folders."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.fullmatch("".join(out), path) is not None


@dataclass(frozen=True)
class Rules:
    """One source's selection rules."""
    patterns: tuple[str, ...] = DEFAULT_PATTERNS
    include: tuple[str, ...] = ()
    never_copy: tuple[str, ...] = ()


@dataclass
class Selection:
    files: dict[str, bytes] = field(default_factory=dict)
    skipped: Counter = field(default_factory=Counter)


def normalize_path(raw: str) -> str | None:
    """Relative, ``/``-separated path, or ``None`` if unsafe (absolute, ``..``, NUL, backslash)."""
    if not raw or "\x00" in raw or "\\" in raw or raw.startswith("/"):
        return None
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def _never_copied(path: str, entries: tuple[str, ...]) -> bool:
    parts = path.split("/")
    base = parts[-1]
    for entry in entries:
        e = entry.strip().strip("/")
        if not e:
            continue
        if path == e or path.startswith(e + "/") or ("/" not in e and e in parts):
            return True
        if fnmatch.fnmatchcase(path, e) or fnmatch.fnmatchcase(base, e):
            return True
    return False


def is_persona(path: str) -> bool:
    parts = path.lower().split("/")
    return parts[-1] in PERSONA_NAMES or bool(PERSONA_DIRS & set(parts[:-1]))


def classify_path(raw: str, rules: Rules) -> tuple[str | None, str | None]:
    """(normalized path, None) when the name may be copied, else (path or None, reason code)."""
    path = normalize_path(raw)
    if path is None:
        return None, UNSAFE_PATH
    if _never_copied(path, rules.never_copy):
        return path, NEVER_COPY
    parts = path.split("/")
    name = parts[-1].lower()
    if any(p.startswith(".") for p in parts):
        return path, HIDDEN
    if "sessions" in (p.lower() for p in parts[:-1]):
        return path, SESSIONS
    if name.startswith("auth") or "credential" in name or "token" in name:
        return path, DENIED_NAME
    if _DENIED_EXT.search(name):
        return path, DENIED_EXT
    if not name.endswith(".md"):
        return path, NOT_MARKDOWN
    explicit = any(glob_match(p, path) for p in rules.include)
    if is_persona(path):
        return path, (None if explicit else PERSONA_NOT_INCLUDED)
    if explicit or any(glob_match(p, path) for p in rules.patterns):
        return path, None
    return path, NOT_SELECTED


def path_reason(raw: str, rules: Rules) -> str | None:
    """The refusal code for a name alone (what a source uses to skip reading a file)."""
    return classify_path(raw, rules)[1]


def content_reason(data: bytes) -> str | None:
    if len(data) > MAX_BYTES:
        return TOO_LARGE
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return NOT_UTF8
    return None


def select(files: dict[str, bytes], rules: Rules) -> Selection:
    """Keep the copyable files (keyed by normalized path); count every refusal by code."""
    result = Selection()
    for raw in sorted(files):
        path, reason = classify_path(raw, rules)
        if reason is None:
            reason = content_reason(files[raw])
        if reason is None and path in result.files:
            reason = DUPLICATE_PATH
        if reason is not None:
            result.skipped[reason] += 1
        else:
            result.files[path] = files[raw]
    return result
