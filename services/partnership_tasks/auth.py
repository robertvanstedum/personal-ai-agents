"""Seat bindings and grants for the task service (spec v1.1 A02, A03, P02, Q02).

A caller presents a token. The service keeps only the token's SHA-256 in a binding file it reads at start; the binding
fixes the seat, the principal it serves, the environment, the origin, which scopes it may write and read, and which
command kinds it may send. Nothing in a command or a forwarded header can change any of that: a token alone confers
exactly its binding, and only a binding whose origin is ``owner_session`` carries owner authority.

Grants are the existing standing authority an interpretation may cite (``existing_grant``). In B1 they are a
synthetic registry; the real grant store joins in B2/B4. Holding a grant reference never widens what a seat may do in
this service: grants answer "is this action authorised", bindings answer "who is calling".
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path

from services.partnership_tasks import contracts


@dataclass(frozen=True)
class Binding:
    binding_id: str
    seat: str
    principal: str
    environment: str
    origin: str
    scopes: frozenset
    read_scopes: frozenset
    operations: frozenset
    expires_at: str | None
    revoked_at: str | None

    @property
    def is_owner(self) -> bool:
        return self.origin == "owner_session"


def token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


RESERVED_SEATS = frozenset({"unresolved-lead"})   # markers, never a seat anyone can act as (B3B S2-1)


class Bindings:
    def __init__(self, records: list[dict]):
        self._by_hash: dict[str, Binding] = {}
        for rec in records:
            if rec.get("seat") in RESERVED_SEATS:
                raise ValueError(f"seat {rec['seat']} is reserved")
            problems = contracts.errors("evidence", "seat_binding", rec)
            if problems:
                raise ValueError(f"invalid seat binding {rec.get('binding_id')}: {problems}")
            if not set(rec["scopes"]) <= set(rec["read_scopes"]):
                raise ValueError(f"binding {rec['binding_id']} writes a scope it cannot read")
            if rec["token_sha256"] in self._by_hash:
                raise ValueError("two bindings share one token")
            self._by_hash[rec["token_sha256"]] = Binding(
                rec["binding_id"], rec["seat"], rec["principal"], rec["environment"], rec["origin"],
                frozenset(rec["scopes"]), frozenset(rec["read_scopes"]), frozenset(rec["operations"]),
                rec.get("expires_at"), rec.get("revoked_at"))

    @classmethod
    def load(cls, path: Path | str) -> "Bindings":
        return cls(json.loads(Path(path).read_text("utf-8")))

    def resolve(self, token: object, now: str) -> Binding | None:
        """The binding for this token, or None (unknown, expired or revoked). Constant-time on the hash."""
        if not isinstance(token, str) or not token:
            return None
        want = token_sha256(token)
        found = None
        for known, binding in self._by_hash.items():
            if hmac.compare_digest(known, want):
                found = binding
        if found is None or found.revoked_at is not None:
            return None
        if found.expires_at is not None and _instant(found.expires_at) <= _instant(now):
            return None
        return found


@dataclass(frozen=True)
class Grant:
    grant_id: str
    seats: frozenset
    scopes: frozenset
    expires_at: str | None


class Grants:
    """A synthetic standing-grant registry: grant id -> which seats may cite it, for which scopes, until when."""

    def __init__(self, records: list[dict] | None = None):
        self._grants = {g["grant_id"]: Grant(g["grant_id"], frozenset(g["seats"]), frozenset(g["scopes"]),
                                             g.get("expires_at")) for g in (records or [])}

    def covers(self, grant_id: str, seat: str, scope: str, now: str) -> bool:
        g = self._grants.get(grant_id)
        if g is None or seat not in g.seats or scope not in g.scopes:
            return False
        return g.expires_at is None or _instant(g.expires_at) > _instant(now)


def _instant(ts: str):
    from datetime import datetime
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))
