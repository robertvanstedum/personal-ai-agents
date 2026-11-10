"""Which model serves which task, on which billing: the plan-route counterpart of the gateway's tiers and roles
(docs/runbooks/model_tiers.md).

The same pattern as the gateway: a task asks for a **role**, and each role takes one **route** in ``catalogue.json``,
so moving a task to another model is that one line. Tier (strong/mid/low, shown as A/B/C), provider and billing
(subscription or metered_api) stay separate. A role marked ``selectable`` also lets the owner choose per
conversation, from the next turn.

A route is offered to an agent only when ``offered[agent]`` says ``available``, which means it was proved on that
agent with real tools; anything else there is the reason it is not. A route that cannot serve stops the turn, with one
exception Robert decided on 6 Nov 2026: a plan route may declare ``failover``, its own provider's API route. A turn moves
to it automatically, and says so, only on a declared plan-unavailable signal (``PLAN_SIGNALS``) before the turn did
anything. One level only: an API route never fails over, and nothing ever fails over onto Grok. The browser only ever
names a ``route_id``, never a model, endpoint, header or credential.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger("minimoi.model_routes")
CATALOGUE_PATH = Path(__file__).with_name("catalogue.json")
CATALOGUE_VAR = "MINIMOI_MODEL_ROUTES_FILE"    # a host's own copy, so a one-line switch needs no image: read fresh each turn
AGENT_BY_BACKEND = {"openclaw": "mc", "cos": "cos"}
BILLING_WORDS = {"subscription": "Plan", "metered_api": "API"}
AVAILABLE = "available"
TASK_ROLES = {"mc-conversation": "mc", "mc-private": "mc", "mc-rooms": "mc",
              "cos-conversation": "cos"}    # every task the code asks for; a catalogue without one of them is broken
# The error types OpenClaw's OpenAI-compatible answer uses (openai-compat-errors, 2026.9.6) for a plan that cannot serve.
# These alone may start a switch; every other type (api_error above all, also permission_error and invalid_request_error)
# is reported as it always was. docs/runbooks/model_routes_plan.md says how each was confirmed.
PLAN_SIGNALS = {"rate_limit_error": "rate_limit", "insufficient_quota": "plan_exhausted", "authentication_error": "signin_expired"}
SIGNAL_WORDS = {"rate_limit": "The plan hit its rate or usage limit", "plan_exhausted": "The plan's quota is used up",
                "signin_expired": "The plan's sign-in has expired"}
_API_MODEL = re.compile(r"minimoi-(openai|anthropic)-(?:strong|mid|low)")
_XAI_MODEL = re.compile(r"minimoi-xai-(?:strong|mid|low)")


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
        for route in catalogue["routes"]:
            _check_failover(catalogue, route)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("the route catalogue has the wrong shape") from exc
    return catalogue


def _check_failover(catalogue: dict, route: dict) -> None:
    """A failover target is the same provider's API route and goes no further. Refused here, so no turn can read it."""
    if "failover" not in route:
        return
    target = route_of(catalogue, route["failover"])
    family = _API_MODEL.fullmatch(target["model"]) if target else None
    if (target is None or target["billing_mode"] != "metered_api" or "failover" in target or target.get("runtime") != "openclaw"
            or not target.get("runtime_override", True) or family is None
            or not (_plan_to_own_api(route, family) or _xai_to_claude(route, family))):
        raise ValueError(f"route {route['route_id']} may fail over only to its own provider's API route, "
                         "or from a Grok API route to Claude's")


def _plan_to_own_api(route: dict, family) -> bool:
    return route["billing_mode"] == "subscription" and family.group(1) == route["provider"]


def _xai_to_claude(route: dict, family) -> bool:
    return (route["billing_mode"] == "metered_api" and _XAI_MODEL.fullmatch(route["model"]) is not None
            and family.group(1) == "anthropic")


def plan_signal(error) -> str | None:
    """The declared signal in an error envelope from the runtime, or None. Exact type only, never the words."""
    kind = error.get("type") if isinstance(error, dict) else None
    return PLAN_SIGNALS.get(kind) if isinstance(kind, str) else None


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
            "selection_revision": int(selection.get("revision") or 0), "failover": _failover_of(catalogue, route)}


def _failover_of(catalogue: dict, route: dict) -> dict | None:
    target = route_of(catalogue, route["failover"]) if route.get("failover") else None
    if target is None:
        return None
    return {key: target[key] for key in ("route_id", "tier", "provider", "model", "label", "reasoning_effort", "billing_mode", "runtime")}


def switched_route(route: dict, signal: str) -> dict:
    """The route a switched turn was sent on: the target, on the same turn's role and revisions. It has no failover of
    its own, so a second switch cannot be read from it. The conversation's choice is not touched."""
    return {**route, **route["failover"], "runtime_override": True, "session_history": "shared", "failover": None,
            "switched_from": route["route_id"], "switch_signal": signal}


def billing_served(route: dict, served: dict | None) -> str:
    """The billing a turn's evidence shows. The gateway is always API, whatever route was chosen."""
    return "metered_api" if served and served.get("provider") == "minimoi-gateway" else route["billing_mode"]


PROVIDER_WORDS = {"openai": "OpenAI", "anthropic": "Claude", "xai": "xAI"}


def _provider_of(model_or_route: str) -> str | None:
    """The provider a route id (plan-openai-strong, api-xai-strong) or a gateway tier (minimoi-anthropic-mid) names."""
    match = re.match(r"(?:plan|api|minimoi)-(openai|anthropic|xai)-", str(model_or_route or ""))
    return PROVIDER_WORDS.get(match.group(1)) if match else None


def switch_words(route: dict, served: dict | None) -> str:
    """The plain sentence a switched turn's answer carries. It names the provider that could not serve and the one that
    answered, so the owner never has to guess which account ran out. The turn's evidence decides what it claims."""
    why = SIGNAL_WORDS.get(route.get("switch_signal"), "The plan could not serve")
    source = str(route.get("switched_from") or "")
    who = _provider_of(source)
    if source.startswith("api-xai-"):                                    # a Grok route's declared target, not a plan's
        why, again = why.replace("The plan", "xAI", 1), "xAI"
    elif who:
        why, again = why.replace("The plan", f"The {who} plan", 1), f"the {who} plan"
    else:
        again = "the plan"
    if served and served.get("provider") == "minimoi-gateway":
        answered = _provider_of(served.get("model"))
        ran = (f"so this turn ran on the {answered + ' ' if answered else ''}API: {served.get('model')}, "
               "billed per use against the capped key")
    else:
        ran = (f"so this turn was sent to the API route {route['model']}, billed per use against the capped key; "
               "the runtime's record of it was not read")
    return f"[{why}, {ran}. The next turn tries {again} again.]"


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
    switched = {key: route[key] for key in ("switched_from", "switch_signal") if route.get(key)}
    billing = billing_served(route, served)
    return {"route_id": route["route_id"], "role": route["role"], "tier": route["tier"], "provider": route["provider"],
            "model": route["model"], "reasoning_effort": route["reasoning_effort"], "billing_mode": billing,
            "billing_label": BILLING_WORDS[billing], "catalogue_revision": route["catalogue_revision"],
            "runtime": route.get("runtime"), "served": served or None, "served_matches": matches, **switched}


def relay_routes(catalogue: dict, agent: str = "mc") -> dict:
    """route_id -> provider/model for the relay and the runtime allowlist: every subscription route of the agent, proved
    or not, so a route can be proved before it is offered. An unproved route is never reachable from a conversation.
    A pay-per-use route is listed only once the host has offered it, or as a listed plan route's declared failover target;
    the relay knows no other paid route, and a target still needs the runtime's allowlist and a capped key."""
    routes = {r["route_id"]: f"{r['provider']}/{r['model']}" for r in catalogue["routes"]
              if agent in r["offered"] and r.get("runtime_override", True)
              and (r["billing_mode"] == "subscription" or r["offered"][agent] == AVAILABLE)}
    for route in catalogue["routes"]:
        target = route_of(catalogue, route.get("failover")) if route["route_id"] in routes else None
        if target is not None:
            routes[target["route_id"]] = f"{target['provider']}/{target['model']}"
    return routes


if __name__ == "__main__":
    print(json.dumps(relay_routes(load_catalogue()), separators=(",", ":"), sort_keys=True))
