"""The delegation envelope a runner accepts (harness v0.3.1 §6.2), strictly.

A delegate gets a bounded subset of the parent's authority, never more: one repository at one
exact commit, one scope (``research`` reads; ``build`` edits a fresh checkout), a deadline and a
cost cap. There is no field for pushing, merging, deploying or credentials, and an envelope that
tries to add one is refused, not ignored.
"""
from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime

SCHEMA = "mc-delegation-envelope/1"
ACTORS = ("codex", "claude-code")
V2_ACTORS = ACTORS + ("fakework",)          # a version 2 host also runs the test worker (spec v0.5 §6.5, §9.3)
SCOPES = ("research", "build")
MAX_BRIEF_CHARS = 16_000
MAX_ARTIFACTS = 16
_FIELDS = {"schema", "request_id", "work_id", "generation", "actor", "principal_id", "environment",
           "scope", "repository", "ref", "brief", "input_artifacts", "deadline", "cost_cap_usd"}
_REF = re.compile(r"^[0-9a-f]{40}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class EnvelopeRefused(ValueError):
    """Fixed code only; never echoes the envelope's content."""


@dataclass(frozen=True)
class Envelope:
    request_id: str
    work_id: str
    generation: int
    actor: str
    principal_id: str
    environment: str
    scope: str
    repository: str
    ref: str
    brief: str
    input_artifacts: tuple
    deadline: datetime
    cost_cap_usd: float


def parse(doc: object, *, allowed_repositories: dict, max_cost_usd: float, now: datetime,
          actors: tuple = ACTORS) -> Envelope:
    """``allowed_repositories`` maps a repository name to the runner's local mirror; nothing else
    can be checked out. ``max_cost_usd`` is the runner's own ceiling for any one task. ``actors`` is version 1's
    two CLIs unless a version 2 host names ``V2_ACTORS``."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise EnvelopeRefused("envelope_schema_invalid")
    if set(doc) != _FIELDS:
        raise EnvelopeRefused("envelope_fields_invalid")
    try:
        uuid.UUID(str(doc["request_id"]))
    except ValueError:
        raise EnvelopeRefused("envelope_request_id_invalid") from None
    for key in ("work_id", "principal_id"):
        if not isinstance(doc[key], str) or not _ID.match(doc[key]):
            raise EnvelopeRefused(f"envelope_{key}_invalid")
    gen = doc["generation"]
    if isinstance(gen, bool) or not isinstance(gen, int) or gen < 1:
        raise EnvelopeRefused("envelope_generation_invalid")
    if doc["actor"] not in actors:
        raise EnvelopeRefused("envelope_actor_invalid")
    if doc["environment"] not in ("dev", "prod"):
        raise EnvelopeRefused("envelope_environment_invalid")
    if doc["scope"] not in SCOPES:
        raise EnvelopeRefused("envelope_scope_invalid")
    if doc["repository"] not in allowed_repositories:
        raise EnvelopeRefused("envelope_repository_not_allowed")
    if not isinstance(doc["ref"], str) or not _REF.match(doc["ref"]):
        raise EnvelopeRefused("envelope_ref_must_be_exact_commit")
    brief = doc["brief"]
    if not isinstance(brief, str) or not brief.strip() or len(brief) > MAX_BRIEF_CHARS:
        raise EnvelopeRefused("envelope_brief_invalid")
    arts = doc["input_artifacts"]
    if not isinstance(arts, list) or len(arts) > MAX_ARTIFACTS or not all(
            isinstance(a, dict) and set(a) == {"ref", "sha256"} and isinstance(a["ref"], str)
            and _ID.match(a["ref"]) and isinstance(a["sha256"], str) and _SHA.match(a["sha256"])
            for a in arts):
        raise EnvelopeRefused("envelope_artifacts_invalid")
    try:
        deadline = datetime.fromisoformat(str(doc["deadline"]).replace("Z", "+00:00"))
    except ValueError:
        raise EnvelopeRefused("envelope_deadline_invalid") from None
    if deadline.tzinfo is None or deadline <= now:
        raise EnvelopeRefused("envelope_deadline_invalid")
    cap = doc["cost_cap_usd"]
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not math.isfinite(cap) \
            or cap <= 0 or cap > max_cost_usd:
        raise EnvelopeRefused("envelope_cost_cap_invalid")
    return Envelope(str(doc["request_id"]), doc["work_id"], gen, doc["actor"], doc["principal_id"],
                    doc["environment"], doc["scope"], doc["repository"], doc["ref"], brief,
                    tuple((a["ref"], a["sha256"]) for a in arts), deadline, float(cap))
