"""A person's voice card, loaded from that person's own release folder. Shared code holds no person: the host names the
folder (MC: services/mc_operations/personas/mc-chat; Spec 165 §4.5, §6.1).

<root>/releases/<v>/voice.json is listed in that release's manifest.json; the bytes read are the bytes hashed, and a card
whose hash is not the manifest's is refused (ValueError), as the projection refuses any other persona file
(scripts/mc_persona). <root>/pin.json, {"release": "<v>"}, names the adopted release, so no host names one in code:
changing a card is a new release folder plus a pin change, both data, both reviewed as an owner adoption (Spec 164 §3).

Schema 1, with two optional fields: ``language``, the session locale (one of config.ALLOWED_LOCALES; absent is en-US),
and ``accent``, a short label for the card's reader ("light Austrian"), informational: the card text is what the model
reads."""
import hashlib
import json
import re
from pathlib import Path

from core.realtime_voice.bootstrap import _DEFAULT_VOICE
from core.realtime_voice.config import ALLOWED_LOCALES

OPENAI_VOICES = frozenset({
    "alloy", "ash", "ballad", "cedar", "coral", "echo", "marin", "sage", "shimmer", "verse",
})
XAI_VOICES = frozenset({"eve", "ara", "leo", "rex", "sal"})
ALLOWED = {"openai": OPENAI_VOICES, "xai": XAI_VOICES}
DEFAULT_LANGUAGE = "en-US"
ACCENT_MAX = 80
_RELEASE = re.compile(r"[0-9]{1,4}(?:\.[0-9]{1,4}){1,3}")       # a release folder's name, never a path


def load_card(root, release):
    """The card of ``<root>/releases/<release>``, refused (ValueError) unless its hash is that release manifest's."""
    if not isinstance(release, str) or not _RELEASE.fullmatch(release):
        raise ValueError("voice card release is not a release name")
    folder = Path(root) / "releases" / release
    manifest = json.loads((folder / "manifest.json").read_text())
    files = manifest.get("files") if isinstance(manifest, dict) else None
    want = files.get("voice.json") if isinstance(files, dict) else None
    raw = (folder / "voice.json").read_bytes()
    if not isinstance(want, str) or hashlib.sha256(raw).hexdigest() != want:
        raise ValueError("voice card does not match its release manifest")
    payload = json.loads(raw.decode("utf-8"))
    # Every shape is checked before it is read, so a malformed card (valid JSON, wrong types) is refused as ValueError, as
    # every caller expects, and never stops a host from starting (review R1 P3-11, R2 N3).
    if not isinstance(payload, dict):
        raise ValueError("voice card is not an object")
    if payload.get("schema") != 1 or not isinstance(payload.get("card"), str) or not payload["card"].strip():
        raise ValueError("voice card is missing its text")
    language = payload.get("language", DEFAULT_LANGUAGE)
    if not isinstance(language, str) or language not in ALLOWED_LOCALES:
        raise ValueError("voice card language is not an allowed locale")
    accent = payload.get("accent")
    if accent is not None and (not isinstance(accent, str) or not accent.strip() or len(accent) > ACCENT_MAX):
        raise ValueError("voice card accent is not a short label")
    voices = payload.get("voices") or {}
    if not isinstance(voices, dict):
        raise ValueError("voice card voices are not an object")
    chosen = {}
    notices = []
    for provider, default in _DEFAULT_VOICE.items():
        wanted = voices.get(provider)
        if wanted in ALLOWED[provider]:
            chosen[provider] = wanted
        else:
            chosen[provider] = default
            notices.append(f"{provider} voice is unset, using {default}")
    return {"card": payload["card"].strip(), "voices": chosen, "notices": notices, "release": release,
            "language": language, "accent": accent.strip() if accent else None}


def load_pinned(root):
    """The card ``<root>/pin.json`` names: refused (OSError, ValueError) when the pin is missing or malformed, its release
    is missing, or the release's card is not its manifest's."""
    pin = json.loads((Path(root) / "pin.json").read_text())
    if not isinstance(pin, dict) or set(pin) != {"release"}:
        raise ValueError('voice pin is not {"release": "<v>"}')
    return load_card(root, pin["release"])
