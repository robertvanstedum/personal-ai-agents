"""
Provider-agnostic review router for Gespräche transcript analysis and KI-Sitzung chat.
Add new providers here — no changes to the route handler needed.

Review functions return { feedback: {...}, model: str }.
Chat functions return the AI response string.
"""
import json
from pathlib import Path

from german_domain import _REVIEW_SYSTEM_PROMPT, _parse_transcript_turns
from core.get_secret import get_secret

# Persona prompt .txt files — same path as german_domain._PROMPTS_DIR
_PROMPTS_DIR = Path(__file__).parent.parent / "data" / "config" / "prompts"


class ProviderError(Exception):
    pass


def _provider_key(env_name: str, service: str) -> str:
    """Resolve an API key via get_secret: env → macOS keyring (Mac) → AWS SSM
    (container). Returns '' if unavailable so callers raise a clean ProviderError.
    The old keyring-only lookups failed in the mein-deutsch container, breaking
    the web KI-Sitzung chat and transcript analysis in production."""
    try:
        return get_secret(env_name, service, "api_key") or ""
    except Exception:
        return ""


def run_review(transcript: str, persona: str, scene: str, model: str) -> dict:
    """
    Routes transcript review to the correct provider.
    Raises ProviderError on failure — no silent fallback.
    """
    if model == "grok":
        return _review_grok(transcript, persona, scene)
    elif model == "openai":
        return _review_openai(transcript, persona, scene)
    elif model == "gemini":
        return _review_gemini(transcript, persona, scene)
    elif model == "claude":
        return _review_claude(transcript, persona, scene)
    else:
        raise ProviderError(f"Unknown model: {model}")


def _build_user_prompt(transcript: str, persona: str, scene: str) -> str:
    import datetime
    turns = _parse_transcript_turns(transcript)
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"Persona: {persona}",
        f"Scenario: {scene}",
        f"Date: {date_str}",
        "",
        "Transcript:",
    ] + [f"{t['speaker']}: {t['text']}" for t in turns]
    return "\n".join(lines)


def _parse_feedback(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    parsed = json.loads(text.strip())
    return {
        "overall_summary": parsed.get("overall_summary", ""),
        "errors":          parsed.get("errors", []),
        "strengths":       parsed.get("strengths", []),
        "next_focus":      parsed.get("next_focus", ""),
        "topics":          parsed.get("topics", []),
        "vocabulary":      parsed.get("vocabulary", []),
    }


def _review_grok(transcript: str, persona: str, scene: str) -> dict:
    from openai import OpenAI
    api_key = _provider_key("XAI_API_KEY", "xai")
    if not api_key:
        raise ProviderError("xAI API key not found in keyring")
    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
    resp = client.chat.completions.create(
        model="grok-4.3",
        max_tokens=2000,
        temperature=0.3,
        messages=[
            {"role": "system", "content": _REVIEW_SYSTEM_PROMPT},
            {"role": "user",   "content": _build_user_prompt(transcript, persona, scene)},
        ],
    )
    raw = resp.choices[0].message.content or ""
    try:
        return {"feedback": _parse_feedback(raw), "model": "grok"}
    except Exception as e:
        raise ProviderError(f"Grok response parse error: {e}\nRaw: {raw[:200]}")


def _review_openai(transcript: str, persona: str, scene: str) -> dict:
    from openai import OpenAI
    api_key = _provider_key("OPENAI_API_KEY", "openai")
    if not api_key:
        raise ProviderError("OpenAI API key not found in keyring")
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=2000,
        temperature=0.3,
        messages=[
            {"role": "system", "content": _REVIEW_SYSTEM_PROMPT},
            {"role": "user",   "content": _build_user_prompt(transcript, persona, scene)},
        ],
    )
    raw = resp.choices[0].message.content or ""
    try:
        return {"feedback": _parse_feedback(raw), "model": "openai"}
    except Exception as e:
        raise ProviderError(f"OpenAI response parse error: {e}\nRaw: {raw[:200]}")


def _review_gemini(transcript: str, persona: str, scene: str) -> dict:
    raise ProviderError("Gemini not configured — add gemini API key to keyring first")


def _review_claude(transcript: str, persona: str, scene: str) -> dict:
    import anthropic
    api_key = _provider_key("ANTHROPIC_API_KEY", "anthropic")
    if not api_key:
        raise ProviderError("Anthropic API key not found in keyring")
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=_REVIEW_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_prompt(transcript, persona, scene)}],
    )
    raw = resp.content[0].text.strip()
    try:
        return {"feedback": _parse_feedback(raw), "model": "claude"}
    except Exception as e:
        raise ProviderError(f"Claude response parse error: {e}\nRaw: {raw[:200]}")


# ── KI-Sitzung: turn-by-turn chat ────────────────────────────────────────────


def build_chat_system_prompt(persona_slug: str, scene: str) -> str:
    """Builds a chat-appropriate system prompt for in-page KI-Sitzung.

    Uses the persona .txt file (same source as assemble_session_prompt) but
    omits the Grok Voice header/footer — those contain voice-mode instructions
    that would interfere with in-page text chat (e.g. "wait for start trigger").
    """
    from german_domain import get_personas, persona_to_slug
    personas = get_personas()
    persona = next(
        (p for p in personas if persona_to_slug(p["name"]) == persona_slug), None
    )
    if not persona:
        raise ProviderError(f"Persona not found: {persona_slug}")

    persona_name = persona.get("name", "")
    slug = persona_name.lower().replace(" ", "_")
    matches = list(_PROMPTS_DIR.glob(f"{slug}*.txt"))
    if matches:
        persona_txt = matches[0].read_text(encoding="utf-8").strip()
    else:
        persona_txt = persona.get("description", f"Du bist {persona_name}.")

    scene_text = persona.get("speaking_prompts", {}).get(scene, "")

    parts = [
        f"Du spielst {persona_name} in einer Deutsch-Übungssitzung mit Robert. "
        f"Bleib die gesamte Sitzung in der Rolle von {persona_name}. "
        f"Antworte ausschließlich auf Deutsch. "
        f"Wenn Robert einen Grammatikfehler macht, verwende natürlich die korrekte Form — "
        f"verlasse nicht die Rolle. Schreibe keine Namenspräfixe vor deinen Antworten.",
        persona_txt,
    ]
    if scene_text:
        parts.append(f"Szenario für diese Sitzung:\n{scene_text}")
    return "\n\n".join(parts)


def run_chat_turn(
    history: list, persona: str, scene: str, user_turn: str, model: str
) -> str:
    """Routes a single conversation turn to the correct provider.

    Opening turn: history=[], user_turn='' → AI sends persona greeting.
    Subsequent turns: history has previous turns, user_turn already pushed to history.
    Returns the AI response string.
    """
    system_prompt = build_chat_system_prompt(persona, scene)
    if model == "grok":
        return _chat_grok(system_prompt, history, user_turn)
    elif model == "openai":
        return _chat_openai(system_prompt, history, user_turn)
    elif model == "claude":
        return _chat_claude_chat(system_prompt, history, user_turn)
    else:
        raise ProviderError(f"Unknown model for chat: {model}")


def _build_chat_messages(history: list) -> list:
    if not history:
        return [{"role": "user", "content": "Bitte starte das Gespräch jetzt."}]
    return list(history)


def _chat_grok(system_prompt: str, history: list, user_turn: str) -> str:
    from openai import OpenAI
    api_key = _provider_key("XAI_API_KEY", "xai")
    if not api_key:
        raise ProviderError("xAI API key not found in keyring")
    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
    resp = client.chat.completions.create(
        model="grok-4.3",
        max_tokens=200,
        temperature=0.7,
        messages=[{"role": "system", "content": system_prompt}] + _build_chat_messages(history),
    )
    return resp.choices[0].message.content or ""


def _chat_openai(system_prompt: str, history: list, user_turn: str) -> str:
    from openai import OpenAI
    api_key = _provider_key("OPENAI_API_KEY", "openai")
    if not api_key:
        raise ProviderError("OpenAI API key not found in keyring")
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=200,
        temperature=0.7,
        messages=[{"role": "system", "content": system_prompt}] + _build_chat_messages(history),
    )
    return resp.choices[0].message.content or ""


def _chat_claude_chat(system_prompt: str, history: list, user_turn: str) -> str:
    import anthropic
    api_key = _provider_key("ANTHROPIC_API_KEY", "anthropic")
    if not api_key:
        raise ProviderError("Anthropic API key not found in keyring")
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=system_prompt,
        messages=_build_chat_messages(history),
    )
    return resp.content[0].text.strip()
