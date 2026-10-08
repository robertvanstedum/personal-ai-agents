"""Model profiles: configured, never assumed (v0.6 section 13; v0.7 Unit 7; acceptance row O02).

A profile names a provider and a model for one purpose (``routine`` checks, ``judgment`` calls). It comes only from the workshop's
``config.json``. A missing profile is a hard error: there is no default model in this code and no fallback to an environment
variable, a previous run or "whatever is available". The profile a run requested is what the event records (``model`` =
``{provider, requested}``); what actually ran is recorded afterwards on a separate outcome event.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.workshop_journal import strictjson

PURPOSES = ("routine", "judgment")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}$")


class ProfileMissing(RuntimeError):
    """No profile is configured for this purpose. The caller must stop; nothing is guessed."""


class BadProfiles(ValueError):
    pass


def validate(table) -> dict:
    if not isinstance(table, dict):
        raise BadProfiles("not_an_object")
    out = {}
    for purpose, row in table.items():
        if purpose not in PURPOSES:
            raise BadProfiles("unknown_purpose")
        if not isinstance(row, dict) or set(row) != {"provider", "model"}:
            raise BadProfiles("profile_needs_exactly_provider_and_model")
        if not all(isinstance(row[k], str) and _NAME.fullmatch(row[k]) for k in ("provider", "model")):
            raise BadProfiles("bad_profile_value")
        out[purpose] = {"provider": row["provider"], "model": row["model"]}
    return out


def load(workshop_dir: str) -> dict:
    """The configured profiles (possibly none). Only ``config.json`` is read: no environment, no defaults."""
    path = Path(workshop_dir) / "config.json"
    if not path.is_file():
        return {}
    try:
        doc = strictjson.loads(path.read_bytes())
    except strictjson.StrictJSONError:
        raise BadProfiles("config_unreadable") from None
    if not isinstance(doc, dict) or set(doc) - {"v", "routes", "model_profiles"}:
        raise BadProfiles("unknown_config_field")
    return validate(doc.get("model_profiles") or {})


def require(profiles: dict, purpose: str) -> dict:
    """The profile for a purpose, or ProfileMissing. ``model`` in the form an event records."""
    if purpose not in PURPOSES:
        raise ProfileMissing("unknown_purpose")
    row = profiles.get(purpose)
    if row is None:
        raise ProfileMissing(f"no_{purpose}_profile_configured")
    return {"provider": row["provider"], "requested": row["model"]}
