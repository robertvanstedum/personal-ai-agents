"""A person's voice card, loaded from a persona release. Shared code holds no person.

voice.json is listed in the release manifest; the bytes read are the bytes hashed, and a card whose hash is not the
manifest's is refused (ValueError), as the projection refuses any other persona file (scripts/mc_persona)."""
import hashlib
import json
from pathlib import Path

from core.realtime_voice.bootstrap import _DEFAULT_VOICE

OPENAI_VOICES = frozenset({
    "alloy", "ash", "ballad", "cedar", "coral", "echo", "marin", "sage", "shimmer", "verse",
})
XAI_VOICES = frozenset({"eve", "ara", "leo", "rex", "sal"})
ALLOWED = {"openai": OPENAI_VOICES, "xai": XAI_VOICES}
RELEASES = Path(__file__).resolve().parents[2] / "services/mc_operations/personas/mc-chat/releases"   # not the working directory


def load_card(release):
    folder = RELEASES / release
    manifest = json.loads((folder / "manifest.json").read_text())
    want = (manifest.get("files") or {}).get("voice.json") if isinstance(manifest, dict) else None
    raw = (folder / "voice.json").read_bytes()
    if not isinstance(want, str) or hashlib.sha256(raw).hexdigest() != want:
        raise ValueError("voice card does not match its release manifest")
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("schema") != 1 or not isinstance(payload.get("card"), str) or not payload["card"].strip():
        raise ValueError("voice card is missing its text")
    voices = payload.get("voices") or {}
    chosen = {}
    notices = []
    for provider, default in _DEFAULT_VOICE.items():
        wanted = voices.get(provider)
        if wanted in ALLOWED[provider]:
            chosen[provider] = wanted
        else:
            chosen[provider] = default
            notices.append(f"{provider} voice is unset, using {default}")
    return {"card": payload["card"].strip(), "voices": chosen, "notices": notices, "release": release}
