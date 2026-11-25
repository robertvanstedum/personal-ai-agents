"""What the shared ⋯ menu shows for one partner (core/founders_ui/static/partner-menu.js), the same for MC and CoS.

Both hosts answer ``GET …/partner-settings`` with ``menu_view`` and ``POST`` through ``choose``; only the owner guard,
the write guard and where the conversation's last turn lives differ between them.
"""
from __future__ import annotations

from core.partner_settings import PartnerSettings

from . import routes_enabled
from .last_answer import line
from .picker import effective_voice, model_view, route_for, voice_options


class VoiceUnavailable(ValueError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def menu_view(catalogue: dict | None, agent: str, setting: dict, last_turn: dict | None = None) -> dict:
    """``catalogue`` None: this host runs without routes; the Voice choice is still the owner's, the Model line is empty."""
    if catalogue is None:
        voice = setting.get("voice") or "openai"
        return {"partner": agent, "revision": setting.get("revision", 0), "routes": False,
                "voice": {"selected": voice, "options": [{"provider": p, "label": l, "available": True, "reason": None}
                                                          for p, l in (("openai", "OpenAI"), ("xai", "Grok"))]},
                "model": None, "last_answer": ""}
    voice = effective_voice(catalogue, agent, setting.get("voice"))
    return {"partner": agent, "revision": setting.get("revision", 0), "routes": True,
            "voice": {"selected": voice, "options": voice_options(catalogue, agent)},
            "model": model_view(route_for(catalogue, agent, voice)),
            "last_answer": line(catalogue, last_turn)}


def choose(store: PartnerSettings, owner: str, catalogue: dict | None, agent: str, voice, revision) -> dict:
    """Save the owner's Voice. Refused (VoiceUnavailable) when its model is not offered to this partner here;
    a stale revision raises core.partner_settings.SettingsConflict."""
    if voice not in ("openai", "xai"):
        raise VoiceUnavailable("Choose OpenAI or Grok.")
    if catalogue is not None and route_for(catalogue, agent, voice) is None:
        option = next(o for o in voice_options(catalogue, agent) if o["provider"] == voice)
        raise VoiceUnavailable(option["reason"])
    return store.set_voice(owner, voice, revision)


def catalogue_or_none():
    """The host's catalogue where routes are on, else None. A broken catalogue raises (OSError/ValueError), as turns do."""
    if not routes_enabled():
        return None
    from . import load_catalogue
    return load_catalogue()


def bound_voice(store: PartnerSettings | None, owner, agent: str) -> str | None:
    """The voice a call for this owner is minted on: the menu's selected voice (the saved one, or the plan-first default
    where routes are on), so the call and its consults run on one provider. None: no store, or nothing to bind."""
    if store is None or not owner:
        return None
    saved = store.get(owner)["voice"]
    catalogue = catalogue_or_none()
    if catalogue is None:
        return saved
    from .picker import effective_voice
    return effective_voice(catalogue, agent, saved)
