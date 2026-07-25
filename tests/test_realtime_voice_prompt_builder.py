"""
tests/test_realtime_voice_prompt_builder.py — persona/scene instruction
porting and clause-by-clause mapping.

Per _working/CLAUDE_CODE_BUILD_SPEC_voice_realtime_2026-07-24.md Section 8:
retain the useful semantics of the old vendor-app prompt scaffolding, but
replace its vendor-app-specific lifecycle instructions (spoken start/end
triggers, model-manufactured transcript blocks) with the realtime
session's own lifecycle (app Start/End actions, provider transcript
events).

The old-prompt text below is copied verbatim from the actual source as of
this build, so this test is a genuine mapping against reality, not a
paraphrase:
  - German: domains/german/german_domain.py:_UNIVERSAL_HEADER / _UNIVERSAL_FOOTER
  - Portuguese: domains/portuguese/html_server.py:_PT_UNIVERSAL_HEADER / _PT_UNIVERSAL_FOOTER
"""
import pytest

from core.realtime_voice.prompt_builder import (
    build_realtime_instructions,
    CONTINUATION_INSTRUCTION,
)

# Copied verbatim from domains/german/german_domain.py as of this build.
GERMAN_OLD_HEADER = """\
=== SESSION INSTRUCTIONS — READ BEFORE STARTING ===

You are playing a character in a German language practice session. These rules override everything else. Follow them exactly.

0. VOICE AND GENDER: Play the character exactly as described below — including gender. Never switch. Non-negotiable.

1. SCENARIO AND MEDIUM: Follow the scenario setup exactly. If it says "phone call", you answer the phone. If it says I walk in, greet me in person. Never change the setting mid-session.

2. NO NAME PREFIX: Do not announce your name before each turn.
   Wrong: "Klaus: Guten Abend!"
   Correct: "Guten Abend!"

3. LANGUAGE: Always respond in German. Never switch to English unless I say "English please."

4. CORRECTIONS: If I make a grammatical error, gently use the correct form naturally. Do not break character.

5. START TRIGGER: Do not begin until I say "Start today's session", "Start session", or "Let's start." Wait in silence — do not acknowledge or ask.

6. STAY IN CHARACTER: Do not comment on the exercise or your role. You are the character.

=== CHARACTER AND SCENARIO BELOW ==="""

GERMAN_OLD_FOOTER = """\
=== HOW TO END THIS SESSION ===

PREFERRED: Stop voice mode yourself first, then type "End session. Give me the transcript."
This prevents the transcript from being read aloud.

VOICE TRIGGER: If Robert says "end session" while in voice mode —
1. Stop speaking immediately. Do not say anything else.
2. Exit voice mode silently.
3. Output the transcript block below in text only. Do not read it aloud.

Output ONLY this block — nothing before or after, no commentary:

---SESSION---
Date: [today's date as YYYY-MM-DD]
Persona: [character name]
Scenario: [scenario_label]
Duration: [number only — e.g. 12]
Mode: voice

[Character name]: [their exact words]
Robert: [your exact words]
[continue alternating turns in order...]
---END---

Every turn in order, no skips. Use --- not em-dashes. Duration is a number only. Nothing before ---SESSION---. Nothing after ---END---."""


@pytest.fixture
def built():
    return build_realtime_instructions(
        locale="de-AT",
        persona_name="Frau Berger",
        persona_txt="You are Frau Berger, a warm bakery owner...",
        scene_text="Ordering bread and pastries.",
        learner_name="Robert",
    )


# ── Retained clauses: semantic content must survive the port ────────────────

def test_voice_and_gender_clause_retained(built):
    assert "gender" in built.lower()
    assert "never switch" in built.lower()


def test_scenario_medium_clause_retained(built):
    assert "scenario" in built.lower()
    assert "never change the setting" in built.lower() or "stay in the scenario" in built.lower()
    assert "not a script" in built.lower()
    assert "actual spoken request" in built.lower()
    assert "never act as if the learner said something you did not hear" in built.lower()


def test_live_conversation_priority_follows_scenario(built):
    scenario_at = built.index("=== SCENARIO FOR THIS SESSION ===")
    priority_at = built.index("=== LIVE CONVERSATION PRIORITY ===")
    assert priority_at > scenario_at
    assert "Do not assume the learner chose" in built
    assert "First listen to the learner's actual words" in built


def test_no_name_prefix_clause_retained(built):
    assert "do not announce your name" in built.lower() or "no name prefix" in built.lower()


def test_language_clause_retained(built):
    assert "german" in built.lower()
    assert "english" in built.lower()  # the "never switch to English unless asked" carve-out


def test_corrections_clause_retained(built):
    assert "correct" in built.lower()
    assert "do not break character" in built.lower()


def test_stay_in_character_clause_retained(built):
    assert "stay in character" in built.lower() or "you are the character" in built.lower()


# ── Replaced clauses: vendor-app lifecycle instructions must NOT survive ────

def test_spoken_start_trigger_phrase_removed(built):
    assert "start today's session" not in built.lower()
    assert "wait in silence" not in built.lower()


def test_spoken_end_trigger_and_transcript_block_removed(built):
    assert "end session" not in built.lower() or "voice trigger" not in built.lower()
    assert "---session---" not in built.lower()
    assert "---end---" not in built.lower()
    assert "give me the transcript" not in built.lower()


def test_no_reference_to_robert_by_name_leaks_into_shared_instructions(built):
    # The old prompt hardcodes "Robert" as the learner name (single-user
    # legacy assumption). The new shared builder must take learner_name as
    # a parameter, not hardcode it, so the module is genuinely shared and
    # not still implicitly Robert-only.
    assert "Robert" in built  # present because we passed learner_name="Robert"
    built_other_learner = build_realtime_instructions(
        locale="de-AT",
        persona_name="Frau Berger",
        persona_txt="You are Frau Berger...",
        scene_text="Ordering bread.",
        learner_name="Isabella",
    )
    assert "Isabella" in built_other_learner
    assert "Robert" not in built_other_learner


def test_application_name_is_not_used_as_stranger_familiarity(built):
    assert "use it only for internal context and transcript attribution" in built
    assert "Do not address the learner by name unless" in built


def test_stefan_realtime_prompt_is_concise_and_stranger_appropriate():
    from pathlib import Path

    prompt = Path(
        "domains/german/data/config/prompts/stefan_ubahn.txt"
    ).read_text(encoding="utf-8")
    assert "slightly measured pace" in prompt
    assert "Never call the tourist by name unless" in prompt
    assert "one or two essential direction steps at a time" in prompt
    assert "Do not volunteer alternate routes" in prompt


def test_german_review_prompt_rejects_likely_speech_recognition_errors():
    from pathlib import Path

    source = Path("domains/german/german_domain.py").read_text(encoding="utf-8")
    assert "speech-recognition artifacts" in source
    assert '"Uh, oh, vier" or "Ufer"' in source
    assert 'omit the item from "errors"' in source
    assert "statement-versus-question intent" in source
    assert "verb-first word order may already be correct for a question" in source


# ── Continuation instruction (Section 9) ─────────────────────────────────────

def test_continuation_instruction_is_fixed_and_scene_neutral():
    assert CONTINUATION_INSTRUCTION == "Continue naturally in character."


# ── Locale coverage: same clause structure, correct language ────────────────

def test_portuguese_locale_produces_portuguese_language_clause():
    built_pt = build_realtime_instructions(
        locale="pt-BR",
        persona_name="Maria",
        persona_txt="You are Maria...",
        scene_text="No mercado.",
        learner_name="Robert",
    )
    assert "portuguese" in built_pt.lower()
    assert "start today's session" not in built_pt.lower()
    assert "---session---" not in built_pt.lower()


def test_unknown_locale_rejected():
    with pytest.raises(ValueError):
        build_realtime_instructions(
            locale="fr-FR",
            persona_name="X",
            persona_txt="",
            scene_text="",
            learner_name="Robert",
        )
