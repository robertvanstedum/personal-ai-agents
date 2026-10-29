"""Chief of Staff's voice card and pin (domains/cos/voice; Spec 165 v0.3 §6.1, §6.2, §4.5), and the release check's
automated part that goes with it (§12.2): the persona the card restates matches the owner's role lock, so a persona
change flags her card. No provider, no Keychain."""
import hashlib
import json
from pathlib import Path

from core.realtime_voice.voice_card import load_card, load_pinned

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "domains/cos/voice"
LOCK = REPO / "docker/cos-agent-a/mc-role-lock.json"
PERSONA = REPO / "planning-studio/initiatives/INIT-2026-0007-interaction-vision/documents/MASTER_CRAFTSMAN_PERSONA.md"
# §6.2's draft card, as the spec gives it (final after the audition, A4).
DRAFT = ("Chief of Staff, Robert's familiar, candid partner, who keeps the work pointed at the right questions. Warm, sharp, "
         "direct, with dry humour when it fits; never a receptionist, concierge, servant or language examiner. English by "
         "default, with a light, natural, intelligible Austrian accent; never a caricature, never invented shared memories. "
         "German belongs naturally in his day: an occasional brief phrase; asked how to say something, one natural Austrian "
         "formulation, then pause. When he speaks German, follow in clear Austrian standard German with du, then return to "
         "English; never a lesson. No canned enthusiasm, flattery or filler. Pronounce MiniMoi, Chief of Staff, Master "
         "Craftsman, Claude Code, Codex and Grok the same way every time.")


def test_her_card_is_pinned_and_verified_with_the_fields_the_spec_gives_it():
    assert json.loads((ROOT / "pin.json").read_text()) == {"release": "0.1.0"}
    card = load_pinned(ROOT)
    assert card == load_card(ROOT, "0.1.0")
    assert card["card"] == DRAFT
    assert (card["language"], card["accent"], card["transcription"]) == ("en-US", "light Austrian", "bilingual")


def test_her_voices_are_cedar_on_openai_and_none_named_on_grok_until_the_audition():
    """D2 (a): OpenAI cedar, as CoS speaks today (core/realtime_voice/providers/openai_speech.py _VOICE, controlled_config's
    output voice). Her configuration names no xAI voice anywhere, so the card names none, and an xAI start is refused
    (domains/cos/direct_voice.py takes no provider default) until the audition chooses one."""
    raw = json.loads((ROOT / "releases/0.1.0/voice.json").read_text())
    assert raw["voices"] == {"openai": "cedar"}
    assert load_pinned(ROOT)["named"] == ["openai"]


def test_the_persona_her_card_restates_is_the_one_the_role_lock_approved():
    manifest = json.loads((ROOT / "releases/0.1.0/manifest.json").read_text())
    lock = json.loads(LOCK.read_text())
    persona = load_pinned(ROOT)["persona"]
    assert persona == manifest["persona"] and persona["source"] in lock["sources"]
    assert persona["sha256"] == lock["sources"][persona["source"]] and lock["approved_by"] == "Robert"
    assert hashlib.sha256(PERSONA.read_bytes()).hexdigest() == persona["sha256"]
    assert f"Version {persona['version']} " in PERSONA.read_text().splitlines()[2]


def test_her_card_restates_the_shared_persona_for_voice_and_adds_only_her_name_and_voice_wording():
    """D1 (a): the shared persona, restated; no manner of her own."""
    card = load_pinned(ROOT)["card"]
    for part in ("Austrian", "dry humour", "du", "never a caricature", "never invented shared memories",
                 "No canned enthusiasm, flattery or filler"):
        assert part in card, part
    assert "Master Craftsman, Robert's colleague" not in card               # not MC's card
