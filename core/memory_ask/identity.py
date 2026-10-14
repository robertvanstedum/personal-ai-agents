"""Who is asking: the host-bound agent registry for the Ask service (Codex lane, 5 Oct 2026).

An agent (Master Craftsman, the Chief of Staff, later others) never *says* who it is. The service derives the caller from a
per-agent secret token held by the agent's runtime and kept out of the model's reach, looks it up here, and builds the
``policy.Caller`` from the registered grant. Nothing in a request body, header or query string can change the identity, the
tier, the scopes or the cap. The owner's terminal (``moi ask``) is the one fixed identity ``robert/cli`` and is not a token.

Registry file: ``<shelf root>/../memory-ask/agents.json`` (folder 0700, file 0600; outside the repo and outside the shelf). It holds
only the SHA-256 of each token (the tokens are random 256-bit values, so a plain hash is enough), never the token. A registry that
is readable by group or others, unparseable or malformed is refused (the service fails closed). Changes take effect on the very
next request; there is no cache to expire.

A grant: ``entry_point`` (a short token, default ``tool``), ``tiers`` (``curated`` always; ``all`` only if the owner allowed the raw
record), ``scopes`` (record scopes the agent may see: ``robert`` and/or ``mandate:<id>``; default ``robert``), ``max_k``, a
per-minute request cap and ``revoked_at``. Revoking keeps the entry (so the audit trail and the name stay) and stops the token.

Limits, stated plainly: this binds identity at the service boundary. It assumes the agent runtime cannot run code on the host with
the shelf and the index (the agents run in containers and reach only the service). An agent that can execute arbitrary code on the
host is outside what this can protect; keep the shelf and the registry out of its filesystem.
"""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.memory_index import policy
from core.memory_shelf import fsio

SCHEMA = "minimoi.ask-agents.v1"
NAME = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
RESERVED = frozenset({"robert", "owner", "canary", "system", "admin", "root", "none", "unknown"})
SCOPE = re.compile(r"^(robert|mandate:[A-Za-z0-9_.-]{1,64})$")
TIERS = ("curated", "all")
DEFAULT_MAX_K = 5
DEFAULT_RATE_PER_MIN = 30
TOKEN_BYTES = 32


class RegistryError(RuntimeError):
    """The registry cannot be trusted. ``code``: ``registry_missing`` | ``registry_insecure`` | ``registry_invalid``."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class TierNotPermitted(PermissionError):
    pass


@dataclass(frozen=True)
class Grant:
    name: str
    entry_point: str
    tiers: tuple[str, ...]
    scopes: tuple[str, ...]
    max_k: int
    rate_per_min: int
    revoked: bool
    created_at: str | None

    def caller(self, include_raw: bool) -> policy.Caller:
        """The ``Caller`` for one request. ``include_raw`` needs the ``all`` tier; scopes come only from the grant."""
        if include_raw and "all" not in self.tiers:
            raise TierNotPermitted(self.name)
        return policy.Caller(tier="all" if include_raw else "curated", scopes=self.scopes, agent=self.name,
                             entry_point=self.entry_point)


def registry_path(shelf_root) -> Path:
    return Path(shelf_root).parent / "memory-ask" / "agents.json"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _grant(name: str, row: dict) -> tuple[Grant, str]:
    if not isinstance(name, str) or not NAME.match(name) or name in RESERVED or not isinstance(row, dict):
        raise RegistryError("registry_invalid")
    digest = row.get("token_sha256")
    entry = row.get("entry_point", "tool")
    tiers, scopes = tuple(row.get("tiers") or ()), tuple(row.get("scopes") or ())
    max_k, rate = row.get("max_k", DEFAULT_MAX_K), row.get("rate_per_min", DEFAULT_RATE_PER_MIN)
    if not (isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise RegistryError("registry_invalid")
    if not (isinstance(entry, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", entry)):
        raise RegistryError("registry_invalid")
    if not tiers or "curated" not in tiers or any(t not in TIERS for t in tiers):
        raise RegistryError("registry_invalid")
    if not scopes or any(not (isinstance(s, str) and SCOPE.match(s)) for s in scopes):
        raise RegistryError("registry_invalid")
    for number in (max_k, rate):
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 1000:
            raise RegistryError("registry_invalid")
    revoked = row.get("revoked_at")
    if revoked is not None and not isinstance(revoked, str):
        raise RegistryError("registry_invalid")
    return Grant(name, entry, tiers, scopes, max_k, rate, revoked is not None, row.get("created_at")), digest


def load(path: Path) -> dict[str, tuple[Grant, str]]:
    """``{name: (grant, token_sha256)}`` or ``RegistryError``. Refuses a file readable by group or others."""
    path = Path(path)
    try:
        info = path.stat()
    except FileNotFoundError:
        raise RegistryError("registry_missing") from None
    except OSError:
        raise RegistryError("registry_invalid") from None
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise RegistryError("registry_insecure")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RegistryError("registry_invalid") from None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or not isinstance(doc.get("agents"), dict):
        raise RegistryError("registry_invalid")
    out = {name: _grant(name, row) for name, row in doc["agents"].items()}
    digests = [d for _, d in out.values()]
    if len(set(digests)) != len(digests):
        raise RegistryError("registry_invalid")                      # one token must never name two agents
    return out


class RegistryFile:
    """The registry as the service reads it: re-read whenever the file changes (revocation is immediate), fail closed on any error."""

    def __init__(self, path):
        self.path, self._key, self._value = Path(path), None, None

    def current(self) -> dict[str, tuple[Grant, str]]:
        try:
            info = self.path.stat()
        except FileNotFoundError:
            self._key = self._value = None
            raise RegistryError("registry_missing") from None
        except OSError:
            self._key = self._value = None
            raise RegistryError("registry_invalid") from None
        key = (info.st_mtime_ns, info.st_size, info.st_ino, info.st_mode)
        if key != self._key or self._value is None:
            self._value = None
            self._value = load(self.path)                            # raises: the cache stays empty, so the next call retries
            self._key = key
        return self._value


def authenticate(registry: dict[str, tuple[Grant, str]], token: str | None) -> tuple[Grant | None, str]:
    """``(grant, "ok")`` for a live token, else ``(None, "unknown" | "revoked")``. Every entry is compared in constant time; the
    caller must answer ``unknown`` and ``revoked`` identically (no oracle for which names exist)."""
    if not isinstance(token, str) or not 16 <= len(token) <= 256:
        return None, "unknown"
    digest = token_hash(token)
    found: Grant | None = None
    for grant, stored in registry.values():
        if hmac.compare_digest(stored, digest):
            found = grant
    if found is None:
        return None, "unknown"
    return (None, "revoked") if found.revoked else (found, "ok")


# ── the owner's provisioning (called only from the TTY-gated ``moi ask-agents``) ─────────────────────────────────────

def _read_raw(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema": SCHEMA, "agents": {}}
    except (OSError, ValueError):
        raise RegistryError("registry_invalid") from None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or not isinstance(doc.get("agents"), dict):
        raise RegistryError("registry_invalid")
    return doc


def _write_raw(path: Path, doc: dict) -> None:
    try:
        fsio.write_atomic(path, (json.dumps(doc, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    except fsio.PublicationUncertain:
        raise RegistryError("registry_durability_unconfirmed") from None


class _Locked:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.fd = os.open(self.path.parent / ".agents.lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        os.close(self.fd)


def add_agent(path: Path, name: str, *, tiers=("curated",), scopes=("robert",), entry_point="tool", max_k=DEFAULT_MAX_K,
              rate_per_min=DEFAULT_RATE_PER_MIN) -> str:
    """Register a new agent and return its token **once**. Only the hash is stored. Refuses an existing name (use ``rotate``)."""
    path = Path(path)
    row = {"token_sha256": "0" * 64, "entry_point": entry_point, "tiers": list(tiers), "scopes": list(scopes),
           "max_k": max_k, "rate_per_min": rate_per_min}
    _grant(name, row)                                                # validates the name and the grant before anything is written
    token = secrets.token_urlsafe(TOKEN_BYTES)
    with _Locked(path):
        doc = _read_raw(path)
        if name in doc["agents"]:
            raise RegistryError("agent_exists")
        row.update(token_sha256=token_hash(token), created_at=_now(), revoked_at=None)
        doc["agents"][name] = row
        _write_raw(path, doc)
    return token


def rotate_agent(path: Path, name: str) -> str:
    """A new token for an existing agent; the old one stops working at once. Clears a revocation (an explicit owner act)."""
    path = Path(path)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    with _Locked(path):
        doc = _read_raw(path)
        if name not in doc["agents"]:
            raise RegistryError("agent_unknown")
        doc["agents"][name].update(token_sha256=token_hash(token), revoked_at=None, rotated_at=_now())
        _write_raw(path, doc)
    return token


def revoke_agent(path: Path, name: str) -> None:
    path = Path(path)
    with _Locked(path):
        doc = _read_raw(path)
        if name not in doc["agents"]:
            raise RegistryError("agent_unknown")
        doc["agents"][name]["revoked_at"] = doc["agents"][name].get("revoked_at") or _now()
        _write_raw(path, doc)


def public_view(path: Path) -> list[dict]:
    """Names and grants for the owner's list: never a token or a hash."""
    doc = _read_raw(Path(path))
    return [{"agent": n, "entry_point": r.get("entry_point", "tool"), "tiers": r.get("tiers"), "scopes": r.get("scopes"),
             "max_k": r.get("max_k"), "rate_per_min": r.get("rate_per_min"), "created_at": r.get("created_at"),
             "revoked_at": r.get("revoked_at")} for n, r in sorted(doc["agents"].items())]


__all__ = ["Grant", "RegistryError", "TierNotPermitted", "RegistryFile", "authenticate", "load", "registry_path", "add_agent",
           "rotate_agent", "revoke_agent", "public_view", "token_hash", "SCHEMA", "RESERVED"]
