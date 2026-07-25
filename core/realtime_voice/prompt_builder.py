"""
core/realtime_voice/prompt_builder.py — shared realtime-session instruction
builder, replacing German's and Portuguese's per-domain
UNIVERSAL_HEADER/UNIVERSAL_FOOTER vendor-app prompt scaffolding.

Per _working/CLAUDE_CODE_BUILD_SPEC_voice_realtime_2026-07-24.md Section 8.

Clause mapping from the old prompts (domains/german/german_domain.py
:_UNIVERSAL_HEADER/_UNIVERSAL_FOOTER, domains/portuguese/html_server.py
:_PT_UNIVERSAL_HEADER/_PT_UNIVERSAL_FOOTER — both had identical structure,
localized text):

  Rule 0 VOICE AND GENDER      -> retained
  Rule 1 SCENARIO AND MEDIUM   -> retained
  Rule 2 NO NAME PREFIX        -> retained
  Rule 3 LANGUAGE              -> retained (per-locale)
  Rule 4 CORRECTIONS           -> retained
  Rule 5 START TRIGGER         -> REPLACED: the realtime session's own
                                   Start action / provider connection event
                                   is the start signal. No spoken trigger
                                   phrase, no "wait in silence" instruction.
  Rule 6 STAY IN CHARACTER     -> retained
  FOOTER (end trigger + manufactured transcript block) -> REPLACED: the
                                   application's End action ends the
                                   session; the transcript comes from
                                   provider transcript events, never from
                                   asking the model to output a formatted
                                   text block.

See tests/test_realtime_voice_prompt_builder.py for the mapping test that
enforces this against the actual old prompt text.
"""

_LOCALE_LANGUAGE = {
    "de-AT": "German",
    "pt-BR": "Brazilian Portuguese",
}

# Fixed, scene-neutral -- used for idle re-engagement (Section 9). Not
# built per-persona/per-scene; the same instruction works for any session.
CONTINUATION_INSTRUCTION = "Continue naturally in character."


def build_realtime_instructions(
    *,
    locale: str,
    persona_name: str,
    persona_txt: str,
    scene_text: str,
    learner_name: str,
) -> str:
    """Build the realtime session's instructions field.

    Replaces the old assemble_session_prompt()'s
    UNIVERSAL_HEADER + persona.txt + scene + UNIVERSAL_FOOTER assembly with
    the same retained rule semantics, minus the vendor-app lifecycle
    instructions (see module docstring for the exact mapping).
    """
    if locale not in _LOCALE_LANGUAGE:
        raise ValueError(
            f"Unknown locale: {locale!r}. Allowed: {sorted(_LOCALE_LANGUAGE)}"
        )
    language = _LOCALE_LANGUAGE[locale]

    rules = "\n\n".join([
        "0. VOICE AND GENDER: Play the character exactly as described below "
        "— including gender. Never switch. Non-negotiable.",

        "1. SCENARIO AND MEDIUM: Follow the scenario setup exactly and stay "
        "in the scenario. Never change the setting mid-session. The scenario "
        "is context and a practice goal, not a script. The learner's actual "
        "spoken request, destination, choices, and facts are authoritative "
        "and override examples in the scenario. Never act as if the learner "
        "said something you did not hear; ask a short clarifying question "
        "when needed.",

        "2. NO NAME PREFIX: Do not announce your name before each turn — "
        "speak directly in character, without a \"Name:\" prefix.",

        f"3. LANGUAGE: Always respond in {language}. Never switch to "
        f"English unless the learner explicitly asks for it.",

        "4. CORRECTIONS: If the learner makes a grammatical error, gently "
        "use the correct form naturally in your reply. Do not break "
        "character to explain it.",

        "5. STAY IN CHARACTER: Do not comment on the exercise or your "
        "role. You are the character, for the entire session.",
    ])

    role_anchor = (
        f"ROLES: You are {persona_name}. The learner you are speaking with "
        f"is {learner_name}. Stay in character as {persona_name} for the "
        f"entire session."
    )

    parts = [
        "=== SESSION INSTRUCTIONS ===\n\nThese rules override everything "
        "else. Follow them exactly.\n\n" + rules,
        role_anchor,
        persona_txt,
    ]
    if scene_text:
        parts.append(f"=== SCENARIO FOR THIS SESSION ===\n\n{scene_text}")

    return "\n\n".join(parts)
