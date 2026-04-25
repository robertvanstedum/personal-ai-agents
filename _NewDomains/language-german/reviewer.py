#!/usr/bin/env python3
"""
reviewer.py — Review a German practice session and generate learning outputs.

Four steps, in order:

  Step 1: READ & CALL CLAUDE
    Load session JSON. Format transcript into a prompt. Call claude-sonnet-4-6
    with a German tutor system prompt. Receive structured JSON feedback.

  Step 2: PARSE & SAVE REVIEWER OUTPUT
    3-stage parse: direct JSON → code block extract → fallback struct.
    Write reviewer_output back into session JSON. On fallback, store raw LLM
    text in reviewer_raw_output for debugging. Mark session reviewed.

  Step 3: GENERATE ANKI CSV AND LESSON PLAN
    Anki: deduplicate vocabulary against progress.json vocabulary_seen,
    append new cards to anki/YYYY-MM-DD_anki.csv.
    Lesson plan: pick next persona (not last 2 used), pick next uncovered
    scenario, carry forward top error type, write lessons/YYYY-MM-DD_lesson.json.

  Step 4: UPDATE progress.json
    Increment total_sessions, total_minutes, anki_cards_generated.
    Append to personas_practiced, scenarios_covered, vocabulary_seen.
    Merge new strengths. Set last_updated. Create file if first session.

Usage:
  python3 reviewer.py --session language/german/sessions/2026-04-19_001.json --base-dir language/german/
  python3 reviewer.py --latest --base-dir language/german/
"""
import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ERROR_TYPES = ["gender", "word_order", "missing_article", "verb_conjugation", "vocabulary", "register"]

SYSTEM_PROMPT = """\
You are a German language tutor reviewing a voice practice session between a student (Robert) and an AI persona.
Analyze the transcript and return a single JSON object — no markdown, no explanation, just the JSON.

Required schema:
{
  "overall_summary": "2-3 sentence summary of the session quality and main takeaway",
  "errors": [
    {
      "type": "gender|word_order|missing_article|verb_conjugation|vocabulary|register",
      "original": "what Robert said",
      "correction": "correct form",
      "explanation": "one sentence why",
      "context": "full sentence from transcript"
    }
  ],
  "error_type_counts": {
    "gender": 0,
    "word_order": 0,
    "missing_article": 0,
    "verb_conjugation": 0,
    "vocabulary": 0,
    "register": 0
  },
  "vocabulary_highlights": [
    { "german": "word or phrase", "english": "translation", "note": "used correctly / new this session / needs practice", "tags": ["optional", "anki", "tags"] }
  ],
  "strengths": ["specific things Robert did well"],
  "next_focus": "one concrete grammar or vocabulary focus for the next session"
}\
"""


# ── Step 1: Read & call Claude ────────────────────────────────────────────────

def _get_anthropic_client():
    try:
        import keyring
        api_key = keyring.get_password("anthropic", "api_key") or os.getenv("ANTHROPIC_API_KEY")
    except Exception:
        api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Anthropic API key not found in keychain or ANTHROPIC_API_KEY env var.")
    from anthropic import Anthropic
    return Anthropic(api_key=api_key)


def _build_user_prompt(session: dict) -> str:
    lines = [
        f"Persona: {session['persona']}",
        f"Scenario: {session['scenario']}",
        f"Date: {session['date']}",
        "",
        "Transcript:",
    ]
    for turn in session["raw_transcript"]:
        lines.append(f"{turn['speaker']}: {turn['text']}")
    return "\n".join(lines)


def _call_claude(session: dict, model: str) -> str:
    client = _get_anthropic_client()
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_prompt(session)}],
    )
    return response.content[0].text


# ── Step 2: Parse & save reviewer output ─────────────────────────────────────

def _empty_reviewer_output() -> dict:
    return {
        "overall_summary": "Review parse failed — see reviewer_raw_output in session file.",
        "errors": [],
        "error_type_counts": {t: 0 for t in ERROR_TYPES},
        "vocabulary_highlights": [],
        "strengths": [],
        "next_focus": "",
    }


def _parse_llm_response(text: str) -> tuple[dict, bool]:
    """Return (parsed_dict, success). On failure returns fallback struct."""
    try:
        return json.loads(text.strip()), True
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1)), True
        except json.JSONDecodeError:
            pass
    print(f"⚠️  LLM response parse failed. Storing raw output for debugging.", file=sys.stderr)
    print(f"   Raw (first 300 chars): {text[:300]}", file=sys.stderr)
    return _empty_reviewer_output(), False


def _save_reviewer_output(session: dict, session_path: Path, reviewer_output: dict,
                           raw_text: str, parse_ok: bool):
    session["reviewer_output"] = reviewer_output
    if not parse_ok:
        session["reviewer_raw_output"] = raw_text
    session_path.write_text(json.dumps(session, indent=2, ensure_ascii=False))


# ── Step 3a: Generate Anki CSV ────────────────────────────────────────────────

def _generate_anki_csv(reviewer_output: dict, session: dict,
                        anki_dir: Path, progress: dict) -> int:
    vocab = reviewer_output.get("vocabulary_highlights", [])
    seen = set(progress.get("vocabulary_seen", []))
    new_cards = [v for v in vocab if v.get("german") and v["german"] not in seen]

    if not new_cards:
        print("   Anki: no new vocabulary this session.")
        return 0

    date_str = session["date"]
    scenario = session["scenario"]
    csv_path = anki_dir / f"{date_str}_anki.csv"
    write_header = not csv_path.exists()

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        if write_header:
            writer.writerow(["Front", "Back", "Tags"])
        for card in new_cards:
            back = f"{card['english']} — {scenario} session {date_str}"
            if card.get("note"):
                back += f" ({card['note']})"
            raw_tags = card.get("tags") or []
            tags = " ".join(raw_tags) if raw_tags else "german vienna"
            writer.writerow([card["german"], back, tags])

    # Update vocabulary_seen in progress (before _update_progress is called)
    progress["vocabulary_seen"].extend(c["german"] for c in new_cards)

    print(f"   Anki: {len(new_cards)} new card(s) → {csv_path.name}")
    return len(new_cards)


# ── Step 3b: Generate lesson plan ────────────────────────────────────────────

ANCHOR_PERSONA = "Frau Berger"


def _pick_next_persona(personas: list, progress: dict) -> dict:
    anchor = next((p for p in personas if p["name"] == ANCHOR_PERSONA), None)
    recent = progress.get("personas_practiced", [])[-2:]
    # Non-anchor candidates: avoid last 2 used
    non_anchor = [p for p in personas if p["name"] != ANCHOR_PERSONA and p["name"] not in recent]
    if not non_anchor:
        non_anchor = [p for p in personas if p["name"] != ANCHOR_PERSONA]
    # Prefer non-anchor personas not yet practiced at all
    not_yet = [p for p in non_anchor if p["name"] not in progress.get("personas_practiced", [])]
    pool = not_yet if not_yet else non_anchor
    # Anchor is always eligible — return it if it's the only option or pool is empty
    return pool[0] if pool else anchor


def _pick_next_scenario(persona: dict, progress: dict) -> str:
    covered = set(progress.get("scenarios_covered", []))
    for s in persona["scenarios"]:
        if s not in covered:
            return s
    return persona["scenarios"][0]  # all covered — cycle from start


def _top_error_type(progress: dict) -> str:
    counts = progress.get("cumulative_error_counts", {})
    if not any(counts.values()):
        return "gender"  # default for first session
    return max(counts, key=lambda k: counts[k])


def _carry_forward_errors(reviewer_output: dict, top_type: str, date_str: str) -> list:
    errors = [e for e in reviewer_output.get("errors", []) if e.get("type") == top_type]
    result = []
    for e in errors[:2]:
        result.append(f"{e['correction']} (was: {e['original']}) — session {date_str}")
    return result


def _generate_lesson_plan(reviewer_output: dict, session: dict, domain_cfg: dict,
                           personas: list, progress: dict, lessons_dir: Path):
    lesson_number = domain_cfg.get("current_lesson_number", 1)
    next_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    next_persona = _pick_next_persona(personas, progress)
    next_scenario = _pick_next_scenario(next_persona, progress)
    top_error = _top_error_type(progress)
    carry = _carry_forward_errors(reviewer_output, top_error, session["date"])

    vocab_targets = [
        v["german"] for v in reviewer_output.get("vocabulary_highlights", [])[:5]
    ]

    # Use scenario-specific prompt from personas.json if available
    base_prompt = next_persona.get("speaking_prompts", {}).get(
        next_scenario,
        f"You walk into a scene with {next_persona['name']} ({next_persona['role']}). "
        f"Scenario: {next_scenario.replace('_', ' ')}. Speak naturally."
    )

    # Anchor persona gets carry-forward and vocab layered on top
    if next_persona["name"] == ANCHOR_PERSONA and (carry or vocab_targets):
        extra = []
        if carry:
            extra.append(f"Focus on fixing: {carry[0]}.")
        if vocab_targets:
            extra.append(f"Try to use: {', '.join(vocab_targets[:3])}.")
        speaking_prompt = base_prompt + " " + " ".join(extra)
    else:
        speaking_prompt = base_prompt

    lesson = {
        "lesson_date": next_date,
        "lesson_number": lesson_number + 1,
        "persona": next_persona["name"],
        "persona_role": next_persona["role"],  # from personas.json, never from reviewer_output
        "scenario": next_scenario,
        "warm_up": f"Review top error from last session: {top_error.replace('_', ' ')}.",
        "focus": reviewer_output.get("next_focus", ""),
        "speaking_prompt": speaking_prompt,
        "vocabulary_targets": vocab_targets,
        "carry_forward_errors": carry,
    }

    out_path = lessons_dir / f"{next_date}_lesson.json"
    out_path.write_text(json.dumps(lesson, indent=2, ensure_ascii=False))
    print(f"   Lesson plan: Lesson {lesson_number + 1} with {next_persona['name']} → {out_path.name}")

    # Increment lesson counter in domain config
    domain_cfg["current_lesson_number"] = lesson_number + 1


# ── Step 4: Update progress.json ─────────────────────────────────────────────

def _load_progress(progress_path: Path) -> dict:
    if progress_path.exists():
        return json.loads(progress_path.read_text())
    return {
        "total_sessions": 0,
        "voice_sessions": 0,
        "writing_sessions": 0,
        "total_minutes": 0,
        "personas_practiced": [],
        "scenarios_covered": [],
        "cumulative_error_counts": {t: 0 for t in ERROR_TYPES},
        "anki_cards_generated": 0,
        "vocabulary_seen": [],
        "strengths_noted": [],
        "last_updated": None,
    }


def _update_progress(progress: dict, session: dict, reviewer_output: dict,
                      new_vocab_count: int) -> dict:
    progress["total_sessions"] += 1
    mode = session.get("mode", "voice")
    if mode == "writing":
        progress["writing_sessions"] = progress.get("writing_sessions", 0) + 1
    else:
        progress["voice_sessions"] = progress.get("voice_sessions", 0) + 1
    progress["total_minutes"] += session.get("duration_estimate_min") or 0
    progress["personas_practiced"].append(session["persona"])
    progress["scenarios_covered"].append(session["scenario"])
    for err_type, count in reviewer_output.get("error_type_counts", {}).items():
        progress["cumulative_error_counts"][err_type] = (
            progress["cumulative_error_counts"].get(err_type, 0) + count
        )
    progress["anki_cards_generated"] += new_vocab_count
    for s in reviewer_output.get("strengths", []):
        if s not in progress["strengths_noted"]:
            progress["strengths_noted"].append(s)
    progress["last_updated"] = datetime.now(timezone.utc).isoformat()
    return progress


# ── Main ──────────────────────────────────────────────────────────────────────

def _find_latest_unreviewed(sessions_dir: Path) -> Path:
    sessions = sorted(sessions_dir.glob("*.json"), reverse=True)
    for p in sessions:
        data = json.loads(p.read_text())
        if data.get("reviewer_output") is None:
            return p
    raise FileNotFoundError("No unreviewed sessions found in sessions/")


def main():
    parser = argparse.ArgumentParser(description="Review a German practice session.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--session", help="Path to session JSON file")
    src.add_argument("--latest", action="store_true", help="Process most recent unreviewed session")
    parser.add_argument("--base-dir", required=True, help="Path to language/german/ directory")
    args = parser.parse_args()

    base = Path(args.base_dir)
    sessions_dir = base / "sessions"
    anki_dir = base / "anki"
    lessons_dir = base / "lessons"
    progress_path = base / "progress.json"
    domain_cfg_path = base / "config" / "domain.json"
    personas_path = base / "config" / "personas.json"

    for d in (anki_dir, lessons_dir):
        d.mkdir(parents=True, exist_ok=True)

    session_path = (
        _find_latest_unreviewed(sessions_dir)
        if args.latest
        else Path(args.session)
    )
    session = json.loads(session_path.read_text())
    domain_cfg = json.loads(domain_cfg_path.read_text())
    personas = json.loads(personas_path.read_text())
    progress = _load_progress(progress_path)

    model = domain_cfg.get("reviewer_model", "claude-sonnet-4-6")
    print(f"\n── Reviewing: {session_path.name} ──────────────────────────")
    print(f"   Persona: {session['persona']} | Scenario: {session['scenario']}")
    print(f"   Model: {model}")

    # Step 1: Call Claude
    print("\n[1/4] Calling Claude...")
    raw_text = _call_claude(session, model)

    # Step 2: Parse & save reviewer output
    print("[2/4] Parsing response...")
    reviewer_output, parse_ok = _parse_llm_response(raw_text)
    session["anki_generated"] = False
    session["next_lesson_generated"] = False
    _save_reviewer_output(session, session_path, reviewer_output, raw_text, parse_ok)
    err_summary = ", ".join(
        f"{k}×{v}" for k, v in reviewer_output["error_type_counts"].items() if v > 0
    ) or "none"
    print(f"   Errors: {err_summary}")
    print(f"   Vocabulary highlights: {len(reviewer_output.get('vocabulary_highlights', []))}")

    # Step 3: Anki CSV + lesson plan
    drill_mode    = session.get("drill_mode", False)
    drill_session = session.get("drill_session", 0)
    drill_total   = session.get("drill_total", 0)
    is_final_drill = drill_mode and drill_total > 0 and drill_session >= drill_total

    if drill_mode and not is_final_drill:
        print(f"[3/4] Drill session {drill_session}/{drill_total} — generating Anki CSV, skipping lesson rotation...")
        new_vocab_count = _generate_anki_csv(reviewer_output, session, anki_dir, progress)
        print(f"⏭️  Drill session {drill_session}/{drill_total} — skipping lesson rotation")
    else:
        print("[3/4] Generating Anki CSV and lesson plan...")
        new_vocab_count = _generate_anki_csv(reviewer_output, session, anki_dir, progress)
        _generate_lesson_plan(reviewer_output, session, domain_cfg, personas, progress, lessons_dir)

    # Mark session complete and save domain config (lesson counter incremented)
    session["anki_generated"] = True
    session["next_lesson_generated"] = not drill_mode or is_final_drill
    session_path.write_text(json.dumps(session, indent=2, ensure_ascii=False))
    domain_cfg_path.write_text(json.dumps(domain_cfg, indent=2, ensure_ascii=False))

    # Step 4: Update progress.json
    print("[4/4] Updating progress.json...")
    progress = _update_progress(progress, session, reviewer_output, new_vocab_count)
    progress_path.write_text(json.dumps(progress, indent=2, ensure_ascii=False))

    print(f"\n✅ Done. Sessions: {progress['total_sessions']} | "
          f"Anki cards: {progress['anki_cards_generated']} | "
          f"Top error: {_top_error_type(progress)}")


if __name__ == "__main__":
    main()
