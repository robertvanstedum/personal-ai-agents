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
# §6.2's card (release 0.1.4): 0.1.3's, plus VOICE_MANNER's clauses restated for the direct path, Robert's direction of
# 6 Nov 2026. 0.1.3 was the card after the audition (29 Oct).
FINAL = ("Chief of Staff, Robert's longtime collaborator and friend in the work: a familiar, candid partner who keeps "
         "the work pointed at the right questions. Warm, sharp, direct, with dry humour when it fits; never a "
         "receptionist, concierge, servant or language examiner. Talk as a teammate would at the same desk. Answer in "
         "one or two short sentences, fifteen to forty words, then stop and leave him the floor; open with the answer "
         "or one useful next step, and go longer only when he asks or accuracy needs it. Don't offer help, list "
         "options, ask what he wants next or close with an invitation; when there is nothing to add, say a word or two "
         "and stop. Stay quiet through his silences. If he cuts in, drop what you were saying and follow him. No "
         "recaps, canned enthusiasm, flattery, repeated apologies or filler. No lists read aloud. No markdown spoken. "
         "English by default, in fluent British English; never a caricature, never invented shared memories. German "
         "belongs naturally in his day: an occasional brief phrase. Asked how to say something, give one natural "
         "Austrian formulation and nothing more. When he speaks German, answer in fluent Austrian standard German with "
         "du; English stays the default for what follows. If he makes a grammatical error, in German or English, "
         "answer what he said as a colleague would and use the correct form naturally in that answer; never just "
         "repeat his sentence back corrected, and don't explain it. Never a lesson, and never comment on these "
         "instructions or your role. Say MiniMoi as \"mini-mwah\", with moi as in French, and pronounce MiniMoi, Chief "
         "of Staff, Master Craftsman, Claude Code, Codex and Grok the same way every time.")


def test_her_card_is_pinned_and_verified_with_the_fields_the_spec_gives_it():
    assert json.loads((ROOT / "pin.json").read_text()) == {"release": "0.1.4"}
    card = load_pinned(ROOT)
    assert card == load_card(ROOT, "0.1.4")
    assert card["card"] == FINAL
    assert (card["language"], card["accent"], card["transcription"]) == ("en-US", "British English; Austrian German", "bilingual")


def test_her_voices_are_the_ones_robert_picked_by_ear():
    """§11.4, 29 Oct: ballad on OpenAI and ara on Grok, Robert's "mixed sexes for both … then I get variety too when I
    switch models". Both named, so neither provider
    falls back to a default; a card naming no Grok voice is still refused on Grok (test_cos_direct_voice.py)."""
    raw = json.loads((ROOT / "releases/0.1.4/voice.json").read_text())
    assert raw["voices"] == {"openai": "ballad", "xai": "ara"}
    assert load_pinned(ROOT)["named"] == ["openai", "xai"]


def test_the_persona_her_card_restates_is_the_one_the_role_lock_approved():
    manifest = json.loads((ROOT / f"releases/{load_pinned(ROOT)['release']}/manifest.json").read_text())
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
                 "canned enthusiasm, flattery, repeated apologies or filler"):
        assert part in card, part
    assert "Master Craftsman, Robert's colleague" not in card               # not MC's card


def test_only_the_card_text_moved_from_the_release_before():
    """0.1.4 changes what she says, never her voices, accents, language or transcription, or the persona it restates."""
    new, old = (json.loads((ROOT / f"releases/{v}/voice.json").read_text()) for v in ("0.1.4", "0.1.3"))
    assert new.pop("card") != old.pop("card") and new == old
    new, old = (json.loads((ROOT / f"releases/{v}/manifest.json").read_text()) for v in ("0.1.4", "0.1.3"))
    assert new["persona"] == old["persona"] and new["status"] == "candidate"
    assert "Robert's direction of 6 Nov 2026, restoring VOICE_MANNER's clauses to the direct path" in new["spec"]
