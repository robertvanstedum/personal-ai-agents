"""Which model serves which task, on which billing: the plan-route counterpart of the gateway's tiers and roles
(docs/runbooks/model_tiers.md).

The same pattern as the gateway: a task asks for a **role**, and each role takes one **route** in ``catalogue.json``,
so moving a task to another model is that one line. Tier (strong/mid/low, shown as A/B/C), provider and billing
(subscription or metered_api) stay separate. A role marked ``selectable`` also lets the owner choose per
conversation, from the next turn.

A route is offered to an agent only when ``offered[agent]`` says ``available``, which means it was proved on that
agent with real tools; anything else there is the reason it is not. Nothing falls back: a route that cannot serve stops
the turn. The browser only ever names a ``route_id``, never a model, endpoint, header or credential.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("minimoi.model_routes")
CATALOGUE_PATH = Path(__file__).with_name("catalogue.json")
CATALOGUE_VAR = "MINIMOI_MODEL_ROUTES_FILE"    # a host's own copy, so a one-line switch needs no image: read fresh each turn
AGENT_BY_BACKEND = {"openclaw": "mc", "cos": "cos"}
BILLING_WORDS = {"subscription": "Plan", "metered_api": "API"}
AVAILABLE = "available"
TASK_ROLES = {"mc-conversation": "mc", "mc-private": "mc", "mc-rooms": "mc",
              "cos-conversation": "cos"}    # every task the code asks for; a catalogue without one of them is broken


class RouteRefused(ValueError):
    """The route cannot be selected or used. ``code`` and ``message`` are safe to show; nothing was sent."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def routes_enabled() -> bool:
    """A host opts in by naming its catalogue, with its relay and runtime configured to match. Anywhere else there
    are no routes: nothing is selected, no header is sent, and the host's runtime default serves exactly as before."""
    return bool(os.environ.get(CATALOGUE_VAR))


def load_catalogue(path: Path | str | None = None) -> dict:
    """The catalogue, checked. Anything wrong with it is an OSError or a ValueError."""
    catalogue = json.loads(Path(path or os.environ.get(CATALOGUE_VAR) or CATALOGUE_PATH).read_text(encoding="utf-8"))
    try:
        ids = [r["route_id"] for r in catalogue["routes"]]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate route_id in the catalogue")
        for name, agent in TASK_ROLES.items():
            if catalogue["roles"].get(name, {}).get("agent") != agent:
                raise ValueError(f"the catalogue has no role {name} for {agent}")
        for name, role in catalogue["roles"].items():
            route = route_of(catalogue, role["route"])
            if route is None or route["offered"].get(role["agent"]) != AVAILABLE:
                raise ValueError(f"role {name} must read a route available to {role['agent']}")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("the route catalogue has the wrong shape") from exc
    return catalogue


def agent_for_backend(kind: str | None) -> str | None:
    return AGENT_BY_BACKEND.get(kind or "")


def route_of(catalogue: dict, route_id) -> dict | None:
    return next((r for r in catalogue["routes"] if r["route_id"] == route_id), None)


def conversation_role(agent: str) -> str:
    return f"{agent}-conversation"


def _public(catalogue: dict, route: dict, agent: str) -> dict:
    tier = catalogue["tiers"][route["tier"]]
    offered = route["offered"][agent]
    return {"route_id": route["route_id"], "tier": route["tier"], "tier_letter": tier["letter"], "tier_use": tier["use"],
            "provider": route["provider"], "model": route["model"], "label": route["label"],
            "reasoning_effort": route["reasoning_effort"], "billing_mode": route["billing_mode"],
            "billing_label": BILLING_WORDS[route["billing_mode"]], "available": offered == AVAILABLE,
            "reason": None if offered == AVAILABLE else offered}


def selection_of(conv: dict, agent: str) -> dict:
    """This conversation's stored choice for this agent; one agent's choice never reads another's."""
    return dict(((conv or {}).get("routes") or {}).get(agent) or {})


def view(catalogue: dict, agent: str, conv: dict) -> dict:
    """What the conversation options show: the routes, the choice, and the last evidence, kept apart."""
    selection = selection_of(conv, agent)
    role = catalogue["roles"][conversation_role(agent)]
    selected = route_of(catalogue, selection.get("route_id") or role["route"])
    return {"agent": agent, "catalogue_revision": catalogue["revision"], "revision": int(selection.get("revision") or 0),
            "selected_route_id": selected["route_id"] if selected else None,
            "selected": _public(catalogue, selected, agent) if selected and agent in selected["offered"] else None,
            "routes": [_public(catalogue, r, agent) for r in catalogue["routes"] if agent in r["offered"]],
            "selectable": bool(role.get("selectable")), "last_turn": selection.get("last_turn")}


def check_selectable(catalogue: dict, agent: str, route_id) -> dict:
    route = route_of(catalogue, route_id) if isinstance(route_id, str) else None
    if route is None or agent not in route["offered"]:
        raise RouteRefused("route_unknown", "That route is not offered for this conversation.")
    if route["offered"][agent] != AVAILABLE:
        raise RouteRefused("route_unavailable", route["offered"][agent])
    return route


def snapshot(catalogue: dict, role_name: str, conv: dict | None = None) -> dict:
    """The route of one admitted turn, fixed here. The turn keeps it; a later change applies to the next turn. A route
    no longer available refuses the turn rather than substituting another."""
    role = catalogue["roles"][role_name]
    agent = role["agent"]
    selection = selection_of(conv, agent) if role.get("selectable") else {}
    route = route_of(catalogue, selection.get("route_id") or role["route"])
    offered = route["offered"].get(agent) if route else None
    if offered != AVAILABLE:
        why = f": {offered}" if offered else ""
        raise RouteRefused("route_unavailable", f"This model route is not available{why}. Nothing was sent. "
                                                "Choose another under Options.")
    return {"route_id": route["route_id"], "agent": agent, "role": role_name, "tier": route["tier"],
            "provider": route["provider"], "model": route["model"], "reasoning_effort": route["reasoning_effort"],
            "billing_mode": route["billing_mode"], "runtime": route["runtime"],
            "runtime_override": route.get("runtime_override", True),
            "session_history": route.get("session_history", "shared"), "catalogue_revision": catalogue["revision"],
            "selection_revision": int(selection.get("revision") or 0)}


def role_snapshot_for(kind: str | None, role_name: str) -> dict | None:
    """A non-conversation task's route. None only where this role has no routes (routes not enabled on this host, or
    a backend this task does not belong to). A catalogue that cannot be read, or that lacks a task, refuses, as a
    conversation does: never a silent default."""
    if not routes_enabled() or agent_for_backend(kind) != TASK_ROLES.get(role_name):
        return None
    try:
        catalogue = load_catalogue()
    except (OSError, ValueError) as exc:
        log.warning("model routes: the catalogue could not be read for %s (%s)", role_name, type(exc).__name__)
        raise RouteRefused("route_unavailable", "The model routes could not be read. Nothing was sent.") from exc
    return snapshot(catalogue, role_name)


def served_receipt(route: dict | None, served: dict | None) -> dict | None:
    """Selected and evidenced are separate: ``served`` is only what the runtime's own record showed, or None."""
    if route is None:
        return None
    matches = None
    if served:
        # A CLI runtime records itself as the provider ("claude-cli" for Anthropic's plan route).
        matches = served.get("provider") in (route["provider"], route.get("runtime")) and served.get("model") == route["model"]
    return {"route_id": route["route_id"], "role": route["role"], "tier": route["tier"], "provider": route["provider"],
            "model": route["model"], "reasoning_effort": route["reasoning_effort"], "billing_mode": route["billing_mode"],
            "catalogue_revision": route["catalogue_revision"], "runtime": route.get("runtime"), "served": served or None, "served_matches": matches}


def relay_routes(catalogue: dict, agent: str = "mc") -> dict:
    """route_id -> provider/model for the relay and the runtime allowlist: every subscription route of the agent, proved
    or not, so a route can be proved before it is offered. An unproved route is never reachable from a conversation."""
    return {r["route_id"]: f"{r['provider']}/{r['model']}" for r in catalogue["routes"]
            if agent in r["offered"] and r.get("runtime_override", True) and r["billing_mode"] == "subscription"}


if __name__ == "__main__":
    print(json.dumps(relay_routes(load_catalogue()), separators=(",", ":"), sort_keys=True))
