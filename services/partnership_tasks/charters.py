"""Helper charters (helper seats spec v0.4 §3.1, §3.3, §4). A charter is the owner-approved contract for one helper seat: a
versioned file, loaded like seat bindings, refused at start when it doesn't match its approval. It is not a practice card:
cards never grant authority, and a charter does, narrowly.

What the label ``none`` or ``internal`` means is enforced here, not trusted: a charter whose tools could write, send or run code
is refused at load. Standard library only, plus the canonical digest."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.canonical_json import digest

EFFECTS = ("none", "internal")
# What a helper may be given. Reads only. Anything else, including an unknown name, refuses the charter.
READ_ONLY_TOOLS = frozenset({"repo.read", "build_log.read", "github.read", "records.read", "recall.read", "actions.read", "tasks.read"})
RETURNS = ("in_turn", "background")
TYPE_FIELDS = {"purpose", "inputs", "outputs", "evidence", "effects", "model", "per_task_budget_usd", "max_revisions", "escalation", "return", "tools"}
SEAT_FIELDS = {"seat", "version", "leads", "scopes", "readers", "daily_budget_usd", "types"}
MAX_REVISIONS = 3


class CharterRefused(ValueError):
    pass


@dataclass(frozen=True)
class Charter:
    seat: str
    version: int
    digest: str
    leads: frozenset
    scopes: frozenset
    types: dict
    daily_budget_usd: float

    @property
    def grant_id(self) -> str:
        return f"charter:{self.seat}:{self.version}"


def _check(doc: dict, name: str) -> None:
    if not isinstance(doc, dict) or set(doc) != SEAT_FIELDS:
        raise CharterRefused(f"{name}: wrong fields")
    if not doc["types"] or not doc["leads"] or not doc["scopes"] or not isinstance(doc["version"], int):
        raise CharterRefused(f"{name}: incomplete")
    for type_id, t in doc["types"].items():
        if not isinstance(t, dict) or set(t) != TYPE_FIELDS:
            raise CharterRefused(f"{name}/{type_id}: wrong fields")
        if t["effects"] not in EFFECTS:
            raise CharterRefused(f"{name}/{type_id}: effects must be none or internal; anything else goes through the harness")
        unknown = set(t["tools"]) - READ_ONLY_TOOLS
        if unknown:
            raise CharterRefused(f"{name}/{type_id}: tools that are not read-only: {sorted(unknown)}")
        if t["return"] not in RETURNS or not 0 <= t["max_revisions"] <= MAX_REVISIONS:
            raise CharterRefused(f"{name}/{type_id}: bad return or revision limit")


DEFAULT_DIR, DEFAULT_APPROVALS = "/app/config/partnership/charters", "/run/secrets/partnership/charter-approvals.json"


def load_if_approved(environ):
    """The approved charters, or None until Robert's approvals are on the host."""
    approvals = environ.get("PARTNERSHIP_CHARTER_APPROVALS") or DEFAULT_APPROVALS
    if not Path(approvals).is_file():
        return None
    return Charters.load(environ.get("PARTNERSHIP_CHARTERS_DIR") or DEFAULT_DIR, approvals)


class Charters:
    def __init__(self, charters=()):
        # The newest version per seat starts new work; every approved version stays readable, so a task chartered under
        # an older version still finds its charter after a newer one is approved (Dev, 11 Nov: raising the helper
        # limits to v2 otherwise dropped returns already in flight under v1).
        self._all = {(c.seat, c.version): c for c in charters}
        self._by_seat = {}
        for c in sorted(charters, key=lambda c: c.version):
            self._by_seat[c.seat] = c

    @classmethod
    def load(cls, root, approvals_file=None) -> "Charters":
        """Every <seat>/v<N>.json under ``root``, each matching an approval. The approvals file is a host record, like a policy's
        authority file, not part of the image. A mismatch refuses the whole start: a changed charter with an old approval must not run."""
        root = Path(root)
        approvals = json.loads(Path(approvals_file or root / "approvals.json").read_text("utf-8"))
        out = []
        for path in sorted(root.glob("*/v*.json")):
            doc = json.loads(path.read_text("utf-8"))
            name = f"{doc.get('seat')}@{doc.get('version')}"
            _check(doc, name)
            want = digest(doc)
            approval = approvals.get(name)
            if not approval or approval.get("digest") != want or not approval.get("approved_by") or not approval.get("ref"):
                raise CharterRefused(f"{name}: no approval for this exact charter")
            out.append(Charter(doc["seat"], doc["version"], want, frozenset(doc["leads"]), frozenset(doc["scopes"]), doc["types"], doc["daily_budget_usd"]))
        return cls(out)

    def get(self, seat, version=None):
        if version is None:
            return self._by_seat.get(seat)
        return self._all.get((seat, version))

    def covers(self, grant_id: str, lead: str, scope: str) -> bool:
        parts = str(grant_id).split(":")
        if len(parts) != 3 or parts[0] != "charter" or not parts[2].isdigit():
            return False
        c = self.get(parts[1], int(parts[2]))
        return c is not None and lead in c.leads and scope in c.scopes

    def chartered(self, ref: dict | None):
        """The charter and type a task's ``charter_ref`` names, or None."""
        if not isinstance(ref, dict):
            return None
        c = self.get(ref.get("seat"), ref.get("version"))
        if c is None or c.digest != ref.get("digest") or ref.get("type_id") not in c.types:
            return None
        return c, c.types[ref["type_id"]]
