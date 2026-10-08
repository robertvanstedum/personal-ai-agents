"""How each teammate is reached, stated honestly (v0.6 section 12; v0.7 Unit 6).

A route is a fact about the world, not a wish: it is either observed, available but unverified, or absent. Nothing here sends
anything and nothing falls back silently: a teammate with no safe unattended route is ``pending_pickup`` (it reads its own
pending list), and that is reported as such. ``verified`` says whether a person or a test actually saw the route work.
"""
from __future__ import annotations

from core.workshop_journal import config, schema

STATUSES = ("native_ui_bridge", "native_gateway", "headless_exec", "pending_pickup", "unverified", "unsupported")

# What was observed on 8 October 2026 (the evidence is a pointer to the dated handoff entries, not a claim of safety).
DEFAULT_ROUTES = {
    "claude-code": {"status": "pending_pickup", "verified": True, "unattended_safe": False,
                    "evidence": "Reads its own pending list. A native UI bridge from Codex into the running session was observed on "
                                "2026-10-08 and shows as agent-authored; no unattended send was tested."},
    "codex": {"status": "pending_pickup", "verified": True, "unattended_safe": False,
              "evidence": "Reads the journal and handoff on its own cadence. `codex exec` exists for a fresh non-interactive run "
                          "(a billed model call, not tested here)."},
    "grok-cli": {"status": "unverified", "verified": False, "unattended_safe": False,
                 "evidence": "Grok CLI 1.0.46 has a headless `-p` mode with allow rules; whether it can call the helper needs a model "
                             "turn that has not been run."},
    "grok-chat": {"status": "pending_pickup", "verified": True, "unattended_safe": False,
                  "evidence": "A chat has no native route; its handoffs arrive by drop-off and it reads what Robert pastes."},
    "claude-chat": {"status": "pending_pickup", "verified": True, "unattended_safe": False,
                    "evidence": "A chat has no native route; its handoffs arrive by drop-off and it reads what Robert pastes."},
    "mc": {"status": "native_gateway", "verified": True, "unattended_safe": False,
           "evidence": "Codex recorded gateway runs for MC on 2026-10-08; replies can be wrong and are not review status."},
    "journeyman": {"status": "native_gateway", "verified": True, "unattended_safe": False,
                   "evidence": "Posts to the handoff on its own; corrections are needed when its summaries add dependencies."},
    "robert": {"status": "pending_pickup", "verified": True, "unattended_safe": False,
               "evidence": "The owner reads the brief; nothing is sent in his name and silence approves nothing."},
}
KEYS = {"status", "verified", "unattended_safe", "evidence"}


class BadRoutes(ValueError):
    pass


def validate(table: dict) -> dict:
    """A routes table, or BadRoutes. Unknown actors, statuses and fields are refused, never ignored."""
    if not isinstance(table, dict):
        raise BadRoutes("not_an_object")
    out = {}
    for actor, row in table.items():
        if actor not in schema.DEFAULT_ACTORS:
            raise BadRoutes("unknown_actor")
        if not isinstance(row, dict) or set(row) - KEYS or "status" not in row:
            raise BadRoutes("bad_route_fields")
        if row["status"] not in STATUSES:
            raise BadRoutes("unknown_status")
        if not isinstance(row.get("verified", False), bool) or not isinstance(row.get("unattended_safe", False), bool):
            raise BadRoutes("bad_flag")
        evidence = row.get("evidence", "")
        if not isinstance(evidence, str) or len(evidence) > 500:
            raise BadRoutes("bad_evidence")
        out[actor] = {"status": row["status"], "verified": row.get("verified", False),
                      "unattended_safe": row.get("unattended_safe", False), "evidence": evidence}
    return out


def load(workshop_dir: str) -> dict:
    """The defaults, overridden by ``config.json`` -> ``routes`` when that file exists and is valid (invalid is refused)."""
    try:
        doc = config.read(workshop_dir)
    except config.BadConfig as exc:
        raise BadRoutes(str(exc)) from None
    table = dict(DEFAULT_ROUTES)
    table.update(validate(doc.get("routes") or {}))
    return table


def describe(table: dict, actor: str) -> dict:
    row = table.get(actor)
    if row is None:
        return {"actor": actor, "status": "unsupported", "verified": False, "unattended_safe": False,
                "evidence": "No route is declared for this actor.", "sends_automatically": False}
    return {"actor": actor, **row, "sends_automatically": row["status"] in ("native_ui_bridge", "native_gateway", "headless_exec")
            and row["unattended_safe"]}
