#!/usr/bin/env python3
"""
get_german_session.py — Assemble and print today's German practice session package.

Usage:
  python get_german_session.py --base-dir language/german/
  python get_german_session.py --base-dir language/german/ --date 2026-04-25
  python get_german_session.py --base-dir language/german/ --dry-run
  python get_german_session.py --base-dir language/german/ --send
"""
import argparse
import json
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


def _carry_forward(lesson: dict, progress: dict) -> str:
    if lesson.get('carry_forward_errors'):
        return lesson['carry_forward_errors'][0]

    counts = progress.get('cumulative_error_counts', {})
    if not counts:
        return "None yet — first session!"

    top_error = max(counts, key=counts.get)
    count = counts[top_error]

    vocab = progress.get('vocabulary_seen', [])
    if top_error == 'vocabulary' and vocab:
        return f"vocabulary ({count} total, e.g. {vocab[-1]})"

    return f"{top_error} ({count} total)"


def main():
    parser = argparse.ArgumentParser(description="Assemble today's German practice session package.")
    parser.add_argument('--base-dir', default='language/german/', help='Path to language/german/ directory')
    parser.add_argument('--date', help='Override date (YYYY-MM-DD, for testing)')
    parser.add_argument('--dry-run', action='store_true', help='Print output without sending anywhere')
    parser.add_argument('--send', action='store_true', help='Send session package to Telegram after printing')
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

    persona_role = next((p['role'] for p in personas if p['name'] == persona_name), 'Unknown')

    prompt_file = _find_prompt_file(prompts_dir, persona_name)
    if prompt_file is None:
        slug = _slug(persona_name)
        print(f"Persona prompt file not found: {prompts_dir}/{slug}*.txt", file=sys.stderr)
        sys.exit(1)

    persona_prompt = prompt_file.read_text(encoding='utf-8').strip()
    carry = _carry_forward(lesson or {}, progress)

    lines = [
        f"📚 Today's German Session — Lesson {lesson_number}",
        f"Persona: {persona_name} — {persona_role}",
        f"Scenario: {scenario}",
        f"Carry forward: {carry}",
    ]
    if warm_up:
        lines.append(f"Warm-up: {warm_up}")
    if speaking_prompt:
        lines.append(f"Prompt: {speaking_prompt}")

    lines += [
        "",
        "─── PASTE INTO GROK OR CLAUDE ───",
        "",
        persona_prompt,
        "",
        "─── HOW TO END THE SESSION ───",
        "",
        'When finished say: "End session. Give me a clean transcript."',
        "",
        "Format:",
        "---SESSION---",
        f"Date: {date_str}",
        f"Persona: {persona_name}",
        f"Scenario: {scenario}",
        "Duration: [number only, e.g. 12]",
        "Mode: voice",
        "",
        f"{persona_name}: [turn text]",
        "Robert: [turn text]",
        "[continue alternating turns...]",
        "---END---",
        "",
        "Send transcript to @minimoi_cmd_bot to process.",
    ]

    output = '\n'.join(lines)
    print(output)

    if args.send and not args.dry_run:
        _send_telegram(output)
        print("✅ Sent to Telegram.", file=sys.stderr)


if __name__ == '__main__':
    main()
