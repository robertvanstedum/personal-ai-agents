"""Voice and Model, linked (#661): the owner picks a Voice; the Model is that provider's one strong route.

The owner's rules (16 Nov): the Voice list is OpenAI and Grok; the Model list shows the strong model of the chosen
Voice's provider, plan first where a plan exists, with the catalogue's declared one-step API failover behind it. Other
routes stay in the catalogue (depth pairs, fallbacks, other tasks); only the picker narrows.

Only the voice is stored (core.partner_settings). The route is derived here, so no cross-provider pair can be kept. A
host's own catalogue may name its own mapping under ``voice_routes`` ({agent: {voice: route_id}}).
"""
from __future__ import annotations

import re

from . import AVAILABLE, BILLING_WORDS, conversation_role, route_of

VOICE_ROUTES = {"openai": "plan-openai-strong", "xai": "api-xai-strong"}
VOICE_LABELS = {"openai": "OpenAI", "xai": "Grok"}
DEFAULT_ORDER = ("openai", "xai")                  # plans first: OpenAI's plan route when this host offers it
PARTNER_WORDS = {"mc": "MC", "cos": "CoS"}
_FAMILY = re.compile(r"(?:plan|api|minimoi)-(openai|anthropic|xai)-")


def family(route: dict | None) -> str | None:
    """The model family a route serves: openai, anthropic or xai; None for a gateway role (its chain decides)."""
    if not route:
        return None
    if route.get("provider") in ("openai", "anthropic"):
        return route["provider"]
    match = _FAMILY.match(str(route.get("model") or "")) or _FAMILY.match(str(route.get("route_id") or ""))
    return match.group(1) if match else None


def route_id_for(catalogue: dict, agent: str, voice: str) -> str | None:
    mapping = ((catalogue.get("voice_routes") or {}).get(agent) or {})
    return mapping.get(voice) or VOICE_ROUTES.get(voice)


def route_for(catalogue: dict, agent: str, voice: str | None) -> dict | None:
    """The voice's route when this host offers it to this agent as available, else None."""
    if voice not in VOICE_ROUTES:
        return None
    route = route_of(catalogue, route_id_for(catalogue, agent, voice))
    if route is None or (route.get("offered") or {}).get(agent) != AVAILABLE:
        return None
    return route


def voice_options(catalogue: dict, agent: str) -> list[dict]:
    out = []
    for voice in DEFAULT_ORDER:
        route = route_for(catalogue, agent, voice)
        out.append({"provider": voice, "label": VOICE_LABELS[voice], "available": route is not None,
                    "reason": None if route else f"{VOICE_LABELS[voice]} model not set up for {PARTNER_WORDS.get(agent, agent)} here"})
    return out


def effective_voice(catalogue: dict, agent: str, saved: str | None) -> str | None:
    """The saved voice when its route is offered here; else the first offered one, plan first; None when neither is."""
    if saved in VOICE_ROUTES and route_for(catalogue, agent, saved) is not None:
        return saved
    return next((v for v in DEFAULT_ORDER if route_for(catalogue, agent, v) is not None), None)


def model_view(route: dict | None) -> dict | None:
    if route is None:
        return None
    label = re.sub(r"\s*\((?:API|pay per use)\)\s*$", "", route["label"])
    return {"route_id": route["route_id"], "label": label, "billing_label": BILLING_WORDS[route["billing_mode"]],
            "text": f"{label} · {BILLING_WORDS[route['billing_mode']]}"}


def owner_conversation(conv: dict | None, agent: str, saved_voice: str | None, catalogue: dict) -> dict | None:
    """The conversation as a turn should read it: its selection becomes the owner's voice route (its last_turn and other
    evidence kept). Unchanged where the role is not selectable, or where no voice route is offered here."""
    role = catalogue["roles"].get(conversation_role(agent)) or {}
    voice = effective_voice(catalogue, agent, saved_voice)
    if not role.get("selectable") or voice is None:
        return conv
    route = route_for(catalogue, agent, voice)
    base = dict(conv or {})
    routes = dict(base.get("routes") or {})
    routes[agent] = {**(routes.get(agent) or {}), "route_id": route["route_id"]}
    base["routes"] = routes
    return base
