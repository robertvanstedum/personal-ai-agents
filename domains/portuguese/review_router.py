"""
review_router.py — provider-agnostic review and chat router for Portuguese Conversas.

Port of domains/german/providers/review_router.py. Same interfaces:
  run_review() → {feedback: {...}, model: str}
  run_chat_turn() → response string
"""
import json
import sys
from pathlib import Path

# Reach repo root for get_secret
_REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))
from get_secret import get_secret

_PERSONAS_DIR = Path(__file__).parent / "personas"

PORTUGUESE_REVIEW_PROMPT = """You are an experienced Brazilian Portuguese teacher reviewing a conversation transcript.

IMPORTANT: Analyze ONLY the student's lines (labeled "Robert" or "User"). Do NOT analyze the persona's Portuguese — only the student's.

Return ONLY a valid JSON object with this exact structure:
{
  "overall_summary": "1-2 sentence summary of the student's performance in English",
  "errors": [
    {"original": "student's exact text", "correction": "corrected form", "explanation": "brief explanation in English"}
  ],
  "strengths": ["strength 1 in English", "strength 2 in English"],
  "next_focus": "one thing for the student to practice next session",
  "topics": ["topic 1", "topic 2"],
  "vocabulary": []
}

Focus:
1. Important grammar or vocabulary errors in the student's lines (skip very minor issues)
2. What the student did well
3. One clear priority for next session

Be encouraging but honest. Write all feedback in English."""


class ProviderError(Exception):
    pass


def _provider_key(env_name: str, service: str) -> str:
    try:
        return get_secret(env_name, service, "api_key") or ""
    except Exception:
        return ""


def _parse_transcript_turns(transcript: str) -> list:
    """Split transcript into speaker turns. Accepts 'Speaker: text' format."""
    turns = []
    for line in transcript.splitlines():
        line = line.strip()
        if not line:
            continue
        if ": " in line:
            speaker, text = line.split(": ", 1)
            turns.append({"speaker": speaker.strip(), "text": text.strip()})
        else:
            turns.append({"speaker": "User", "text": line})
    return turns


def _build_user_prompt(transcript: str, persona: str, scene: str) -> str:
    import datetime
    turns = _parse_transcript_turns(transcript)
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"Persona (AI role-play partner, do NOT analyze): {persona}",
        f"Scene: {scene}",
        f"Date: {date_str}",
        "",
        "Transcript (analyze ONLY the student's lines — Robert or User):",
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


# ── Review (transcript analysis) ─────────────────────────────────────────────

def run_review(transcript: str, persona: str, scene: str, model: str) -> dict:
    if model == "grok":
        return _review_grok(transcript, persona, scene)
    elif model == "openai":
        return _review_openai(transcript, persona, scene)
    elif model == "claude":
        return _review_claude(transcript, persona, scene)
    else:
        raise ProviderError(f"Unknown model: {model}")


def _review_grok(transcript: str, persona: str, scene: str) -> dict:
    from openai import OpenAI
    api_key = _provider_key("XAI_API_KEY", "xai")
    if not api_key:
        raise ProviderError("xAI API key not found")
    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
    resp = client.chat.completions.create(
        model="grok-4.3",
        max_tokens=2000,
        temperature=0.3,
        messages=[
            {"role": "system", "content": PORTUGUESE_REVIEW_PROMPT},
            {"role": "user",   "content": _build_user_prompt(transcript, persona, scene)},
        ],
    )
    raw = resp.choices[0].message.content or ""
    try:
        return {"feedback": _parse_feedback(raw), "model": "grok"}
    except Exception as e:
        raise ProviderError(f"Grok parse error: {e}\nRaw: {raw[:200]}")


def _review_openai(transcript: str, persona: str, scene: str) -> dict:
    from openai import OpenAI
    api_key = _provider_key("OPENAI_API_KEY", "openai")
    if not api_key:
        raise ProviderError("OpenAI API key not found")
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=2000,
        temperature=0.3,
        messages=[
            {"role": "system", "content": PORTUGUESE_REVIEW_PROMPT},
            {"role": "user",   "content": _build_user_prompt(transcript, persona, scene)},
        ],
    )
    raw = resp.choices[0].message.content or ""
    try:
        return {"feedback": _parse_feedback(raw), "model": "openai"}
    except Exception as e:
        raise ProviderError(f"OpenAI parse error: {e}\nRaw: {raw[:200]}")


def _review_claude(transcript: str, persona: str, scene: str) -> dict:
    import anthropic
    api_key = _provider_key("ANTHROPIC_API_KEY", "anthropic")
    if not api_key:
        raise ProviderError("Anthropic API key not found")
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        system=PORTUGUESE_REVIEW_PROMPT,
        messages=[{"role": "user", "content": _build_user_prompt(transcript, persona, scene)}],
    )
    raw = resp.content[0].text.strip()
    try:
        return {"feedback": _parse_feedback(raw), "model": "claude"}
    except Exception as e:
        raise ProviderError(f"Claude parse error: {e}\nRaw: {raw[:200]}")


# ── Chat (KI-Sessão turn-by-turn) ────────────────────────────────────────────

def _load_persona_txt(slug: str, persona_data: dict) -> str:
    matches = list(_PERSONAS_DIR.glob(f"{slug}*.txt"))
    if matches:
        return matches[0].read_text(encoding="utf-8").strip()
    return persona_data.get("description", f"Você é {persona_data.get('name', slug)}.")


def build_chat_system_prompt(persona_slug: str, scene: str, personas: list) -> str:
    persona = next((p for p in personas if _name_to_slug(p["name"]) == persona_slug), None)
    if not persona:
        raise ProviderError(f"Persona not found: {persona_slug}")

    persona_name = persona.get("name", "")
    persona_txt = _load_persona_txt(persona_slug, persona)
    scene_text = persona.get("speaking_prompts", {}).get(scene, "")

    parts = [
        f"Você é {persona_name} em uma sessão de prática de português com o usuário. "
        f"Mantenha sempre o papel de {persona_name}. "
        f"Responda exclusivamente em português brasileiro. "
        f"Se o usuário cometer erros gramaticais, use naturalmente a forma correta sem sair do papel. "
        f"Não escreva prefixos de nome antes das suas respostas.",
        persona_txt,
    ]
    if scene_text:
        parts.append(f"Cenário desta sessão:\n{scene_text}")
    return "\n\n".join(parts)


def _name_to_slug(name: str) -> str:
    return name.lower().replace(" ", "_").replace("-", "_")


def _build_chat_messages(history: list) -> list:
    if not history:
        return [{"role": "user", "content": "Por favor, inicie a conversa agora."}]
    return list(history)


def run_chat_turn(
    history: list, persona: str, scene: str, user_turn: str, model: str, personas: list
) -> str:
    system_prompt = build_chat_system_prompt(persona, scene, personas)
    if model == "grok":
        return _chat_grok(system_prompt, history)
    elif model == "openai":
        return _chat_openai(system_prompt, history)
    elif model == "claude":
        return _chat_claude(system_prompt, history)
    else:
        raise ProviderError(f"Unknown model for chat: {model}")


def _chat_grok(system_prompt: str, history: list) -> str:
    from openai import OpenAI
    api_key = _provider_key("XAI_API_KEY", "xai")
    if not api_key:
        raise ProviderError("xAI API key not found")
    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
    resp = client.chat.completions.create(
        model="grok-3-mini",
        max_tokens=200,
        temperature=0.7,
        messages=[{"role": "system", "content": system_prompt}] + _build_chat_messages(history),
    )
    return resp.choices[0].message.content or ""


def _chat_openai(system_prompt: str, history: list) -> str:
    from openai import OpenAI
    api_key = _provider_key("OPENAI_API_KEY", "openai")
    if not api_key:
        raise ProviderError("OpenAI API key not found")
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=200,
        temperature=0.7,
        messages=[{"role": "system", "content": system_prompt}] + _build_chat_messages(history),
    )
    return resp.choices[0].message.content or ""


def _chat_claude(system_prompt: str, history: list) -> str:
    import anthropic
    api_key = _provider_key("ANTHROPIC_API_KEY", "anthropic")
    if not api_key:
        raise ProviderError("Anthropic API key not found")
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        system=system_prompt,
        messages=_build_chat_messages(history),
    )
    return resp.content[0].text.strip()
