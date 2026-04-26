#!/usr/bin/env python3
"""
get_german_session.py — Assemble and deliver today's German practice session package.

Usage:
  python get_german_session.py --base-dir language/german/
  python get_german_session.py --base-dir language/german/ --send
  python get_german_session.py --base-dir language/german/ --dropbox --send
  python get_german_session.py --base-dir language/german/ --drill 3 --dropbox --send
  python get_german_session.py --base-dir language/german/ --drill 3 --drill-session 2 --dropbox
"""
import argparse
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import keyring
    import requests
except ImportError:
    keyring = None
    requests = None


def _send_telegram(text: str) -> None:
    if keyring is None or requests is None:
        print("⚠️  keyring/requests not available — cannot send to Telegram.", file=sys.stderr)
        sys.exit(1)
    token = keyring.get_password("telegram", "polling_bot_token")
    chat_id = keyring.get_password("telegram", "chat_id")
    if not token or not chat_id:
        print("⚠️  Telegram credentials not found in Keychain (telegram/polling_bot_token, telegram/chat_id).", file=sys.stderr)
        sys.exit(1)
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=30,
    )
    if not resp.ok:
        print(f"⚠️  Telegram send failed: {resp.status_code} {resp.text[:200]}", file=sys.stderr)
        sys.exit(1)


def _get_anthropic_client():
    try:
        import anthropic
        import keyring as kr
        api_key = kr.get_password("anthropic", "api_key") or ""
        return anthropic.Anthropic(api_key=api_key)
    except Exception as e:
        raise RuntimeError(f"Cannot create Anthropic client: {e}")


def _enforce_length(prompt: str, limit: int, persona_name: str = "") -> str:
    if len(prompt) <= limit:
        return prompt
    original_len = len(prompt)
    try:
        client = _get_anthropic_client()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": (
                    f"The following German practice session prompt is {original_len} "
                    f"characters, exceeding the {limit} character delivery limit. "
                    f"Shorten it to under {limit} characters by:\n"
                    f"1. Condensing the vocabulary list — keep the 5 most important "
                    f"items, remove the rest\n"
                    f"2. Shortening the persona description — keep name, role, key "
                    f"traits; remove redundant detail\n"
                    f"DO NOT touch: the === SESSION INSTRUCTIONS === block, the "
                    f"=== HOW TO END === block, or the lesson metadata lines "
                    f"(persona, scenario, carry-forward, warm-up, prompt).\n"
                    f"Return only the revised prompt — no commentary.\n\n"
                    f"{prompt}"
                ),
            }],
        )
        revised = response.content[0].text.strip()
        print(f"⚠️  Prompt revised by Haiku: {original_len} → {len(revised)} chars", file=sys.stderr)
        if len(revised) > limit:
            _hard_fail_length(persona_name, limit)
        return revised
    except RuntimeError:
        raise
    except Exception as e:
        print(f"⚠️  Haiku revision failed ({e}) — sending full prompt", file=sys.stderr)
        return prompt


def _hard_fail_length(persona_name: str, limit: int) -> None:
    slug = f"config/prompts/{_slug(persona_name)}.txt" if persona_name else "config/prompts/[persona].txt"
    msg = f"⚠️ Session prompt too long after revision — trim {slug} and try again."
    print(f"❌ Prompt still over limit after all passes — not sent", file=sys.stderr)
    try:
        _send_telegram(msg)
    except Exception:
        pass
    sys.exit(1)


def _load_sync_config() -> dict:
    here = Path(__file__).parent
    cfg_path = here / "language/german/config/sync_config.json"
    if cfg_path.exists():
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    return {}


def _write_dropbox(sync_cfg: dict, filename: str, content: str) -> Path:
    base = Path(sync_cfg.get("base_path", "~/Dropbox/German_Sessions")).expanduser()
    prompts_dir = base / sync_cfg.get("prompts_dir", "prompts")
    prompts_dir.mkdir(parents=True, exist_ok=True)
    out = prompts_dir / filename
    out.write_text(content, encoding="utf-8")
    return out


def _slug(name: str) -> str:
    """'Frau Novak' → 'frau_novak', 'Dr. Huber' → 'dr_huber'"""
    name = re.sub(r'[^\w\s]', '', name)
    return '_'.join(name.lower().split())


def _find_prompt_file(prompts_dir: Path, persona_name: str) -> Path | None:
    slug = _slug(persona_name)
    exact = prompts_dir / f"{slug}.txt"
    if exact.exists():
        return exact
    matches = sorted(prompts_dir.glob(f"{slug}*.txt"))
    return matches[0] if matches else None


def _load_lesson(lessons_dir: Path, date_str: str) -> dict | None:
    today = lessons_dir / f"{date_str}_lesson.json"
    if today.exists():
        return json.loads(today.read_text(encoding='utf-8'))
    all_lessons = sorted(lessons_dir.glob('*_lesson.json'))
    if all_lessons:
        return json.loads(all_lessons[-1].read_text(encoding='utf-8'))
    return None


def _strip_session_metadata(text: str) -> str:
    """Remove trailing '— session YYYY-MM-DD ...' from carry-forward strings."""
    return re.sub(r'\s*—\s*session\s+\d{4}-\d{2}-\d{2}.*$', '', text).strip()


def _carry_forward(lesson: dict, progress: dict) -> str:
    if lesson.get('carry_forward_errors'):
        return _strip_session_metadata(lesson['carry_forward_errors'][0])

    counts = progress.get('cumulative_error_counts', {})
    if not counts:
        return "None yet — first session!"

    top_error = max(counts, key=counts.get)
    count = counts[top_error]

    vocab = progress.get('vocabulary_seen', [])
    if top_error == 'vocabulary' and vocab:
        return f"vocabulary ({count} total, e.g. {vocab[-1]})"

    return f"{top_error} ({count} total)"


UNIVERSAL_HEADER = """\
=== SESSION INSTRUCTIONS — READ BEFORE STARTING ===

You are playing a character in a German language practice session.
These rules override everything else. Follow them exactly.

0. VOICE AND GENDER: You are playing the character exactly as described
   below — including gender. If the character is male, use a male voice
   and male mannerisms throughout. Never switch gender. Never use a female
   voice for a male character. This is non-negotiable.

1. SCENARIO AND MEDIUM: Follow the scenario setup exactly, including the
   communication medium. If it says "phone call" or "making a reservation
   by phone", you answer the phone — I am not standing in front of you.
   If it says I walk in, greet me as I arrive in person. Never change the
   setting or medium mid-session.

2. NO NAME PREFIX: Do not announce your name before each turn. Speak
   directly and naturally as the character would in real life.
   Wrong: "Klaus: Guten Abend!"
   Correct: "Guten Abend!"

3. LANGUAGE: Always respond in German. Never switch to English unless I
   explicitly say "English please."

4. CORRECTIONS: If I make a grammatical error, gently use the correct
   form naturally in your response. Do not break character to explain.

5. START TRIGGER: Do not begin the scenario until I say exactly:
   "Start today's session."

6. STAY IN CHARACTER: Do not comment on the exercise, the instructions,
   or your role. You are the character. Respond only as the character
   would.

=== CHARACTER AND SCENARIO BELOW ===""".strip()

UNIVERSAL_FOOTER = """\
=== HOW TO END THIS SESSION ===

When the session is over, say: "End session."

At that moment, switch to text and output ONLY the following block —
nothing before it, nothing after it, no commentary:

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

RULES FOR THE TRANSCRIPT:
- Include every turn in order. Do not skip or summarize.
- Use --- (three hyphens) not em-dashes (—) for ---SESSION--- and ---END---.
- Duration is a number only. No "minutes" or other text.
- Nothing before ---SESSION---. Nothing after ---END---.""".strip()


def _dropbox_filename(date_str: str, lesson_number: int, persona_name: str,
                      scenario: str, drill_session: int = 0, drill_total: int = 0) -> str:
    ts = datetime.now().strftime("%H%M")
    slug_p = _slug(persona_name)
    slug_s = scenario.replace(' ', '_').lower()
    base = f"{date_str}_{ts}_lesson{lesson_number}_{slug_p}_{slug_s}"
    if drill_total > 0:
        return f"{base}_drill{drill_session}.txt"
    return f"{base}.txt"


def _build_package(date_str: str, persona_name: str, persona_role: str,
                   lesson_number: int, scenario: str, warm_up: str,
                   speaking_prompt: str, persona_prompt: str, carry: str,
                   drill_session: int = 0, drill_total: int = 0,
                   writing_mode: bool = False) -> str:
    drill_mode = drill_total > 0

    lines = []
    if writing_mode:
        lines.append("⌨️ WRITING SESSION — Add Mode: writing to transcript header.")
        lines.append("")

    lines += [
        f"📚 Today's German Session — Lesson {lesson_number}",
        f"Persona: {persona_name} — {persona_role}",
        f"Scenario: {scenario}",
        f"Carry forward: {carry}",
    ]
    if drill_mode:
        lines.append(f"Drill: {drill_session} of {drill_total}")
    if warm_up:
        lines.append(f"Warm-up: {warm_up}")
    if speaking_prompt:
        lines.append(f"Prompt: {speaking_prompt}")

    footer = UNIVERSAL_FOOTER
    if writing_mode:
        footer = footer.replace("Mode: voice", "Mode: writing")
    if drill_mode:
        footer = footer.replace(
            "Mode: voice",
            f"Mode: voice\nDrill: true\nDrill-Session: {drill_session} of {drill_total}",
        )

    lines += [
        "",
        UNIVERSAL_HEADER,
        "",
        persona_prompt,
        "",
        footer,
    ]
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description="Assemble today's German practice session package.")
    parser.add_argument('--base-dir', default='language/german/', help='Path to language/german/ directory')
    parser.add_argument('--date', help='Override date (YYYY-MM-DD, for testing)')
    parser.add_argument('--dry-run', action='store_true', help='Print output without writing or sending')
    parser.add_argument('--send', action='store_true', help='Send session package to Telegram')
    parser.add_argument('--dropbox', action='store_true', help='Write prompt file(s) to Dropbox prompts dir')
    parser.add_argument('--drill', type=int, metavar='N', help='Generate N drill session prompts (one per session)')
    parser.add_argument('--drill-session', type=int, metavar='K', help='Generate prompt for drill session K only (requires --drill N)')
    parser.add_argument('--writing', action='store_true', help='Writing session mode — sets Mode: writing in transcript footer')
    args = parser.parse_args()

    base = Path(args.base_dir)
    config_dir = base / 'config'
    prompts_dir = config_dir / 'prompts'
    lessons_dir = base / 'lessons'

    date_str = args.date or datetime.now(timezone.utc).strftime('%Y-%m-%d')

    domain = {}
    domain_file = config_dir / 'domain.json'
    if domain_file.exists():
        domain = json.loads(domain_file.read_text(encoding='utf-8'))

    progress = {}
    progress_file = base / 'progress.json'
    if progress_file.exists():
        progress = json.loads(progress_file.read_text(encoding='utf-8'))

    personas = []
    personas_file = config_dir / 'personas.json'
    if personas_file.exists():
        personas = json.loads(personas_file.read_text(encoding='utf-8'))

    lesson = _load_lesson(lessons_dir, date_str)

    if lesson is None:
        persona_name = domain.get('active_persona', 'Frau Berger')
        lesson_number = domain.get('current_lesson_number', 1)
        scenario = 'bakery_order'
        warm_up = ''
        speaking_prompt = ''
    else:
        persona_name = lesson.get('persona', domain.get('active_persona', 'Frau Berger'))
        lesson_number = lesson.get('lesson_number', domain.get('current_lesson_number', 1))
        scenario = lesson.get('scenario', 'bakery_order')
        warm_up = lesson.get('warm_up', '')
        speaking_prompt = lesson.get('speaking_prompt', '')

    persona_obj = next((p for p in personas if p['name'] == persona_name), {})
    persona_role = persona_obj.get('role', 'Unknown')
    warm_up_variants = persona_obj.get('warm_up_variants', [])

    prompt_file = _find_prompt_file(prompts_dir, persona_name)
    if prompt_file is None:
        slug = _slug(persona_name)
        print(f"Persona prompt file not found: {prompts_dir}/{slug}*.txt", file=sys.stderr)
        sys.exit(1)

    persona_prompt = prompt_file.read_text(encoding='utf-8').strip()
    carry = _carry_forward(lesson or {}, progress)

    sync_cfg = _load_sync_config()
    max_chars = sync_cfg.get("max_prompt_chars", 4000)

    drill_total = args.drill or 0

    if drill_total > 0:
        # Determine which session numbers to generate
        if args.drill_session:
            sessions_to_generate = [args.drill_session]
        else:
            sessions_to_generate = list(range(1, drill_total + 1))

        first_output = None
        for k in sessions_to_generate:
            wu = random.choice(warm_up_variants) if warm_up_variants else warm_up
            output = _build_package(
                date_str, persona_name, persona_role, lesson_number, scenario,
                wu, speaking_prompt, persona_prompt, carry,
                drill_session=k, drill_total=drill_total,
                writing_mode=args.writing,
            )
            if not args.dry_run:
                output = _enforce_length(output, max_chars, persona_name)
            print(output)
            print()

            if not args.dry_run and args.dropbox:
                fname = _dropbox_filename(date_str, lesson_number, persona_name, scenario, k, drill_total)
                dest = _write_dropbox(sync_cfg, fname, output)
                print(f"✅ Drill {k}/{drill_total} written to Dropbox: {dest.name}", file=sys.stderr)

            if first_output is None:
                first_output = output

        # Send only the first drill session to Telegram
        if args.send and not args.dry_run and first_output:
            _send_telegram(first_output)
            print(f"✅ Drill 1/{drill_total} sent to Telegram.", file=sys.stderr)

    else:
        output = _build_package(
            date_str, persona_name, persona_role, lesson_number, scenario,
            warm_up, speaking_prompt, persona_prompt, carry,
            writing_mode=args.writing,
        )
        if not args.dry_run:
            output = _enforce_length(output, max_chars)
        print(output)

        if not args.dry_run and args.dropbox:
            fname = _dropbox_filename(date_str, lesson_number, persona_name, scenario)
            dest = _write_dropbox(sync_cfg, fname, output)
            print(f"✅ Written to Dropbox: {dest.name}", file=sys.stderr)

        if args.send and not args.dry_run:
            _send_telegram(output)
            print("✅ Sent to Telegram.", file=sys.stderr)


if __name__ == '__main__':
    main()
