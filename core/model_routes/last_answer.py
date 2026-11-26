"""The one quiet line under the menu: "Last answer: <model> (fallback)", only when another model answered (#661 item 4,
#658). Read from the conversation's own last turn receipt (core.model_routes.served_receipt), never from the selection.

A fallback is:
- a MiniMoi switch (plan to its API, or a declared API failover): ``switched_from`` on the receipt;
- a gateway role fallback: the gateway receipt's ``fallback_position`` above 0, copied into ``served``;
- the runtime's record naming a model of another family than the route sent.
A depth pin (the same family's -light or -deep route) is not a fallback. Anything else says nothing.
"""
from __future__ import annotations

import re

from .picker import family

PROVIDER_WORDS = {"openai": "OpenAI", "anthropic": "Claude", "xai": "Grok"}
_ALIAS = re.compile(r"minimoi-(openai|anthropic|xai)-")
_PROVIDER_PREFIX = re.compile(r"^(openai|anthropic|xai)/")


def _model_family(model: str | None, provider: str | None = None) -> str | None:
    text = str(model or "")
    match = _ALIAS.search(text) or _PROVIDER_PREFIX.match(text)
    if match:
        return match.group(1)
    if provider in PROVIDER_WORDS:
        return provider
    lowered = text.lower()
    for word, fam in (("claude", "anthropic"), ("grok", "xai"), ("gpt", "openai")):
        if word in lowered:
            return fam
    return None


def label_for(catalogue: dict, model: str | None, provider: str | None = None) -> str:
    """A served model in plain words: the catalogue label of the route with that model, else the provider's word."""
    text = str(model or "")
    bare = text.split("/", 1)[1] if _PROVIDER_PREFIX.match(text) else text
    for route in catalogue.get("routes") or ():
        if route.get("model") in (text, bare):
            return route["label"]
    fam = _model_family(text, provider)
    return PROVIDER_WORDS.get(fam, "another model")


def line(catalogue: dict, last_turn: dict | None) -> str:
    if not isinstance(last_turn, dict):
        return ""
    served = last_turn.get("served") if isinstance(last_turn.get("served"), dict) else None
    if last_turn.get("switched_from"):
        model = served.get("model") if served else last_turn.get("model")
        return f"Last answer: {label_for(catalogue, model, served.get('provider') if served else None)} (fallback)"
    if served:
        position = served.get("fallback_position")
        if isinstance(position, int) and not isinstance(position, bool) and position > 0:
            return f"Last answer: {label_for(catalogue, served.get('model'), served.get('provider'))} (fallback)"
        if last_turn.get("served_matches") is False:
            sent = family({"provider": last_turn.get("provider"), "model": last_turn.get("model"),
                           "route_id": last_turn.get("route_id")})
            got = _model_family(served.get("model"), served.get("provider"))
            if got and sent and got != sent:
                return f"Last answer: {label_for(catalogue, served.get('model'), served.get('provider'))} (fallback)"
    return ""
