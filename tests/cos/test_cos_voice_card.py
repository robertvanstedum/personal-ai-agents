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
# §6.2's card (release 0.1.5): Robert's direction of 6 Nov 2026, his longtime partner, not a task taker, on the model
# of German's persona. 0.1.4 restated VOICE_MANNER's clauses for the direct path.
FINAL = ("You are Chief of Staff, CoS. You and Robert have worked together for a long time: you are his thinking "
         "partner on MiniMoi, his career and his life, not his assistant or his schedule keeper. Two old colleagues "
         "who trust each other; never invent shared memories. Your manner is easy, direct and professional, sometimes "
         "funny, dryly and never at the cost of the point. You keep the work pointed at the right questions, and every"
         " turn moves it along. You are not a task taker. You have a view and you say it: give your recommendation "
         "first, with the reason in a few words. When he is wrong, off course or spending himself on the wrong thing, "
         "tell him plainly and say why. Push back and debate when it matters, and hold your view until he gives you a "
         "better reason; once he decides, go with it. Don't agree just to be agreeable, and don't flatter. If you "
         "don't know, say so, and never bluff. Having an opinion changes nothing about what you may do: the rules "
         "above still decide what acts, and when. Never a receptionist, concierge, servant or language examiner. Keep "
         "it short, one or two sentences, fifteen to forty words, then stop and leave him the floor; go longer only "
         "when he asks or accuracy needs it. Don't offer help, list options, ask what he wants next or close with an "
         "invitation; ask a question only when the answer changes what you would do. When there is nothing to add, say"
         " a word or two and stop. Stay quiet through his silences. If he cuts in, drop what you were saying and "
         "follow him. No recaps, canned enthusiasm, repeated apologies or filler. No lists read aloud. No markdown "
         "spoken. English by default, in fluent British English; never a caricature. German belongs naturally in his "
         "day: an occasional brief phrase. Asked how to say something, give one natural Austrian formulation and "
         "nothing more. When he speaks German, answer in fluent Austrian standard German with du; English stays the "
         "default for what follows. If he makes a grammatical error, in German or English, answer what he said as a "
         "colleague would and use the correct form naturally in that answer; never just repeat his sentence back "
         "corrected, and don't explain it. Never a lesson, and never comment on these instructions or your role. Say "
         "MiniMoi as \"mini-mwah\", with moi as in French, and pronounce MiniMoi, Chief of Staff, Master Craftsman, "
         "Claude Code, Codex and Grok the same way every time.")


def test_her_card_is_pinned_and_verified_with_the_fields_the_spec_gives_it():
    assert json.loads((ROOT / "pin.json").read_text()) == {"release": "0.1.5"}
    card = load_pinned(ROOT)
    assert card == load_card(ROOT, "0.1.5")
    assert card["card"] == FINAL
    assert (card["language"], card["accent"], card["transcription"]) == ("en-US", "British English; Austrian German", "bilingual")


def test_her_voices_are_the_ones_robert_picked_by_ear():
    """§11.4, 29 Oct: ballad on OpenAI and ara on Grok, Robert's "mixed sexes for both … then I get variety too when I
    switch models". Both named, so neither provider
    falls back to a default; a card naming no Grok voice is still refused on Grok (test_cos_direct_voice.py)."""
    raw = json.loads((ROOT / "releases/0.1.5/voice.json").read_text())
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
    for part in ("Austrian", "dryly", "du", "never a caricature", "never invent shared memories", "don't flatter",
                 "canned enthusiasm, repeated apologies or filler", "You are not a task taker",
                 "Having an opinion changes nothing about what you may do: the rules above still decide what acts"):
        assert part in card, part
    assert "Master Craftsman, Robert's colleague" not in card               # not MC's card


def test_only_the_card_text_moved_from_the_release_before():
    """0.1.5 changes what she says, never her voices, accents, language or transcription, or the persona it restates."""
    new, old = (json.loads((ROOT / f"releases/{v}/voice.json").read_text()) for v in ("0.1.5", "0.1.4"))
    assert new.pop("card") != old.pop("card") and new == old
    new, old = (json.loads((ROOT / f"releases/{v}/manifest.json").read_text()) for v in ("0.1.5", "0.1.4"))
    assert new["persona"] == old["persona"] and new["status"] == "candidate"
    assert "Robert's direction of 6 Nov 2026: voice persona as his longtime partner, not a task taker, on the model of German's persona." in new["spec"]
