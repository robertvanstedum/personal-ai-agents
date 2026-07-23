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

# Make repo-root modules (utils/, get_secret) importable when run as a script.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    import keyring
    import requests
except ImportError:
    keyring = None
    requests = None


def _send_telegram(text: str) -> None:
    if requests is None:
        print("⚠️  requests not available — cannot send to Telegram.", file=sys.stderr)
        sys.exit(1)
    token = ""
    chat_id = ""
    # Preferred: role-aware system-bot token (SSM on EC2, keyring on Mac).
    # This is the same resolution the polling bot uses, so sends land on
    # minimoi_system_bot in production and minimoi_system_test_bot on standby.
    try:
        from utils.telegram import get_system_token, get_chat_id
        token = get_system_token()
        chat_id = get_chat_id()
    except Exception:
        pass
    # Legacy fallback: macOS Keychain (old minimoi_cmd_bot delivery path).
    if (not token or not chat_id) and keyring is not None:
        token = token or keyring.get_password("telegram", "polling_bot_token")
        chat_id = chat_id or keyring.get_password("telegram", "chat_id")
    if not token or not chat_id:
        print("⚠️  Telegram credentials not found (utils.telegram or Keychain).", file=sys.stderr)
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
        from core.get_secret import get_secret
        api_key = get_secret("ANTHROPIC_API_KEY", "anthropic", "api_key") or ""
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
    # Explicit carry-forward phrases added manually take priority
    phrases = progress.get('carry_forward_phrases', [])
    if phrases:
        raw = phrases[-1]
        return _strip_session_metadata(raw)

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


def _load_keyword_map(base: Path) -> dict:
    """Load keyword_map.json — returns empty dict if missing (graceful fallback)."""
    path = base / 'config' / 'keyword_map.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return {}


_DU_PATTERNS = re.compile(
    r'\b(du\b|dich\b|dir\b|dein[emrs]?\b|hast\b|bist\b|kannst\b|machst\b|gehst\b|weißt\b|willst\b)',
    re.IGNORECASE,
)


def _validate_register_consistency(keyword_map: dict) -> list[str]:
    """Return a list of violation strings for any formal_sie persona with du-form scaffold phrases."""
    violations = []
    for name, data in keyword_map.items():
        if data.get('register') != 'formal_sie':
            continue
        for phrase in data.get('scaffold_phrases', []):
            de = phrase.get('de', '')
            if _DU_PATTERNS.search(de):
                violations.append(f"{name}: informal phrase detected — {de!r}")
    return violations


def _scaffold_block(persona_name: str, keyword_map: dict, progress: dict,
                    progress_file: Path) -> str:
    """
    Select 2 scaffold phrases + fixed recovery phrase for the session briefing.
    Rotation index advances by 2 per session, resets at 6. Per-persona tracking.
    Returns empty string if scaffold data is missing — never crashes.
    """
    violations = _validate_register_consistency(keyword_map)
    for v in violations:
        print(f"⚠️  Register violation: {v}", file=__import__('sys').stderr)

    persona_data = keyword_map.get(persona_name, {})
    phrases = persona_data.get('scaffold_phrases', [])
    recovery = persona_data.get('recovery_phrase', None)

    if not phrases or not recovery:
        return ""

    rotation = progress.get('scaffold_rotation_index', {})
    idx = rotation.get(persona_name, 0) % 6

    phrase1 = phrases[idx % len(phrases)]
    phrase2 = phrases[(idx + 1) % len(phrases)]

    # Advance and persist rotation index
    rotation[persona_name] = (idx + 2) % 6
    progress['scaffold_rotation_index'] = rotation
    if progress_file and progress_file.exists():
        progress_file.write_text(
            json.dumps(progress, indent=2, ensure_ascii=False), encoding='utf-8'
        )

    delivered = [phrase1['de'], phrase2['de'], recovery]
    if progress_file and progress_file.exists():
        progress['last_scaffold_delivered'] = delivered
        progress_file.write_text(
            json.dumps(progress, indent=2, ensure_ascii=False), encoding='utf-8'
        )

    lines = [
        "🧱 Today's scaffold — try to use these:",
        f"   • {phrase1['de']}",
        f"   • {phrase2['de']}",
        f"   • 🆘 If you freeze: {recovery}",
    ]
    return '\n'.join(lines)


def _persona_answers_block(persona_name: str, keyword_map: dict) -> str:
    """Return formatted Q→A cheat-sheet for personas that have carry_forward_phrases."""
    pairs = keyword_map.get(persona_name, {}).get('carry_forward_phrases', [])
    if not pairs:
        return ""
    lines = ["💬 Ready answers:"]
    for pair in pairs:
        lines.append(f"   {pair['q']} → {pair['a']}")
    return '\n'.join(lines)


UNIVERSAL_HEADER = """\
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

=== CHARACTER AND SCENARIO BELOW ===""".strip()

UNIVERSAL_FOOTER = """\
=== HOW TO END THIS SESSION ===

Switch to TEXT MODE, then type "End session. Give me the transcript." Do NOT end while in voice mode.

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

Every turn in order, no skips. Use --- not em-dashes. Duration is a number only. Nothing before ---SESSION---. Nothing after ---END---.""".strip()


def _dropbox_filename(date_str: str, lesson_number: int, persona_name: str,
                      scenario: str, drill_session: int = 0, drill_total: int = 0) -> str:
    ts = datetime.now().strftime("%H%M")
    slug_p = _slug(persona_name)
    slug_s = scenario.replace(' ', '_').lower()
    base = f"{date_str}_{ts}_lesson{lesson_number}_{slug_p}_{slug_s}"
    if drill_total > 0:
        return f"{base}_drill{drill_session}.txt"
    return f"{base}.txt"


def _build_briefing(date_str: str, persona_name: str, persona_role: str,
                    lesson_number: int, scenario: str, warm_up: str,
                    speaking_prompt: str, carry: str, scaffold: str,
                    drill_session: int = 0, drill_total: int = 0,
                    writing_mode: bool = False, persona_answers: str = "",
                    register: str = "") -> str:
    """Message 1 — YOUR BRIEFING. Read this; do not paste into Grok."""
    drill_mode = drill_total > 0

    lines = ["📋 YOUR BRIEFING — read this, do not paste into Grok", ""]

    if writing_mode:
        lines += ["⌨️ WRITING SESSION — Type at your own pace.", ""]

    register_label = f" [use {register}]" if register else ""
    lines += [
        f"📚 Lesson {lesson_number} — {persona_name} / {scenario.replace('_', ' ').title()}{register_label}",
        f"Carry forward: {carry}",
    ]
    if drill_mode:
        lines.append(f"Drill: {drill_session} of {drill_total}")
    if warm_up:
        lines.append(f"Warm-up: {warm_up}")
    if speaking_prompt:
        lines.append(f"Goal: {speaking_prompt}")

    if scaffold:
        lines += ["", scaffold]
    if persona_answers:
        lines += ["", persona_answers]

    return '\n'.join(lines)


def _build_ai_prompt(persona_prompt: str, footer: str) -> str:
    """Message 2 — AI prompt. Paste this into Grok or Claude."""
    return '\n'.join([UNIVERSAL_HEADER, "", persona_prompt, "", footer])


def _build_package(date_str: str, persona_name: str, persona_role: str,
                   lesson_number: int, scenario: str, warm_up: str,
                   speaking_prompt: str, persona_prompt: str, carry: str,
                   drill_session: int = 0, drill_total: int = 0,
                   writing_mode: bool = False,
                   scaffold: str = "") -> str:
    """Single-message fallback (dry-run, Dropbox file). Combines briefing + AI prompt."""
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
    if scaffold:
        lines += ["", scaffold]

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
    parser.add_argument('--persona', help='Override persona name (e.g. "Maria")')
    parser.add_argument('--scenario', help='Override scenario (e.g. "cafe_order")')
    parser.add_argument('--repeat', action='store_true', help='Repeat last session — suppresses scaffold rotation advancement')
    parser.add_argument('--skip', action='store_true', help='Skip current lesson — advances rotation to next persona/scenario without generating a session prompt')
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

    if args.persona:
        persona_name = args.persona
        scenario = args.scenario or scenario
        warm_up = ''
        speaking_prompt = ''
        # When an explicit persona is requested and today's lesson file doesn't exist, write it
        # so that subsequent fallback calls find the right persona instead of the alphabetically-last file.
        if not args.dry_run and not (lessons_dir / f"{date_str}_lesson.json").exists():
            persona_obj_tmp = next((p for p in personas if p['name'] == persona_name), {})
            lesson_number_tmp = domain.get('current_lesson_number', 1)
            explicit_lesson = {
                "lesson_date": date_str,
                "lesson_number": lesson_number_tmp,
                "persona": persona_name,
                "persona_role": persona_obj_tmp.get('role', ''),
                "scenario": scenario,
                "warm_up": '',
                "focus": '',
                "speaking_prompt": '',
                "vocabulary_targets": [],
                "carry_forward_errors": [],
            }
            (lessons_dir / f"{date_str}_lesson.json").write_text(
                json.dumps(explicit_lesson, indent=2, ensure_ascii=False), encoding='utf-8'
            )

    if args.skip:
        # Import reviewer helpers here to avoid circular deps at module level
        import sys as _sys
        _reviewer_dir = Path(__file__).parent
        if str(_reviewer_dir) not in _sys.path:
            _sys.path.insert(0, str(_reviewer_dir))
        from reviewer import _pick_next_persona, _pick_next_scenario, _generate_lesson_plan, _empty_reviewer_output

        current_persona_name = persona_name
        current_scenario = scenario

        # Write a minimal skipped session JSON so the session log stays coherent
        sessions_dir = base / 'sessions'
        sessions_dir.mkdir(exist_ok=True)
        existing = sorted(sessions_dir.glob(f"{date_str}_*.json"))
        n = int(existing[-1].stem.split("_")[-1]) + 1 if existing else 1
        session_id = f"{date_str}_{n:03d}"
        skipped_session = {
            "date": date_str,
            "session_id": session_id,
            "persona": current_persona_name,
            "scenario": current_scenario,
            "mode": "skipped",
            "skipped": True,
            "duration_estimate_min": 0,
            "raw_transcript": [],
        }
        (sessions_dir / f"{session_id}.json").write_text(
            json.dumps(skipped_session, indent=2, ensure_ascii=False), encoding='utf-8'
        )

        # Advance rotation: record current persona/scenario in progress so _pick_next skips them
        progress.setdefault("personas_practiced", []).append(current_persona_name)
        progress.setdefault("scenarios_covered", []).append(current_scenario)
        progress_file.write_text(json.dumps(progress, indent=2, ensure_ascii=False), encoding='utf-8')

        # Generate next lesson plan (writes {today}_lesson.json, increments lesson counter)
        _generate_lesson_plan(_empty_reviewer_output(), skipped_session, domain, personas, progress, lessons_dir)
        domain_file.write_text(json.dumps(domain, indent=2, ensure_ascii=False), encoding='utf-8')

        # Read back what was just written to report to user
        next_lesson_path = lessons_dir / f"{date_str}_lesson.json"
        next_lesson = json.loads(next_lesson_path.read_text(encoding='utf-8'))
        next_persona = next_lesson.get('persona', '?')
        next_scenario_str = next_lesson.get('scenario', '').replace('_', ' ')
        print(f"⏭ Skipped {current_persona_name}. Next up: {next_persona} — {next_scenario_str}. Say 'next session' to start.")
        sys.exit(0)

    persona_obj = next((p for p in personas if p['name'] == persona_name), {})
    persona_role = persona_obj.get('role', 'Unknown')
    persona_register = persona_obj.get('register', '')
    warm_up_variants = persona_obj.get('warm_up_variants', [])

    prompt_file = _find_prompt_file(prompts_dir, persona_name)
    if prompt_file is None:
        slug = _slug(persona_name)
        print(f"Persona prompt file not found: {prompts_dir}/{slug}*.txt", file=sys.stderr)
        sys.exit(1)

    persona_prompt = prompt_file.read_text(encoding='utf-8').strip()
    carry = _carry_forward(lesson or {}, progress)

    keyword_map = _load_keyword_map(base)
    # Repeat sessions: scaffold shown but rotation index not advanced
    pf_for_scaffold = None if (args.dry_run or args.repeat) else progress_file
    scaffold = _scaffold_block(persona_name, keyword_map, progress, pf_for_scaffold)
    persona_answers = _persona_answers_block(persona_name, keyword_map)

    if args.repeat and not args.dry_run and progress_file.exists():
        progress['last_repeat'] = True
        progress_file.write_text(
            json.dumps(progress, indent=2, ensure_ascii=False), encoding='utf-8'
        )

    footer = UNIVERSAL_FOOTER
    if args.writing:
        footer = footer.replace("Mode: voice", "Mode: writing")

    sync_cfg = _load_sync_config()
    max_chars = sync_cfg.get("max_prompt_chars", 4000)
    max_assembled_chars = sync_cfg.get("max_assembled_chars", 7000)

    # Tier 1: enforce content budget against persona .txt only — Haiku trims here if needed
    if not args.dry_run and len(persona_prompt) > max_chars:
        persona_prompt = _enforce_length(persona_prompt, max_chars, persona_name)

    drill_total = args.drill or 0

    if drill_total > 0:
        if args.drill_session:
            sessions_to_generate = [args.drill_session]
        else:
            sessions_to_generate = list(range(1, drill_total + 1))

        first_briefing = None
        first_ai_prompt = None
        for k in sessions_to_generate:
            wu = random.choice(warm_up_variants) if warm_up_variants else warm_up
            drill_footer = footer.replace(
                "Mode: voice" if not args.writing else "Mode: writing",
                f"{'Mode: writing' if args.writing else 'Mode: voice'}\nDrill: true\nDrill-Session: {k} of {drill_total}",
            )
            briefing = _build_briefing(
                date_str, persona_name, persona_role, lesson_number, scenario,
                wu, speaking_prompt, carry, scaffold,
                drill_session=k, drill_total=drill_total, writing_mode=args.writing,
                persona_answers=persona_answers, register=persona_register,
            )
            ai_prompt = _build_ai_prompt(persona_prompt, drill_footer)
            output = _build_package(
                date_str, persona_name, persona_role, lesson_number, scenario,
                wu, speaking_prompt, persona_prompt, carry,
                drill_session=k, drill_total=drill_total,
                writing_mode=args.writing, scaffold=scaffold,
            )
            # Tier 2: hard ceiling on assembled output — no Haiku, just fail fast
            if not args.dry_run and len(output) > max_assembled_chars:
                _hard_fail_length(persona_name, max_assembled_chars)
            print(output)
            print()

            if not args.dry_run and args.dropbox:
                fname = _dropbox_filename(date_str, lesson_number, persona_name, scenario, k, drill_total)
                dest = _write_dropbox(sync_cfg, fname, output)
                print(f"✅ Drill {k}/{drill_total} written to Dropbox: {dest.name}", file=sys.stderr)

            if first_briefing is None:
                first_briefing = briefing
                first_ai_prompt = ai_prompt

        if args.send and not args.dry_run and first_briefing:
            import time
            _send_telegram(first_briefing)
            time.sleep(1)
            _send_telegram(first_ai_prompt)
            print(f"✅ Drill 1/{drill_total} sent to Telegram (2 messages).", file=sys.stderr)

    else:
        briefing = _build_briefing(
            date_str, persona_name, persona_role, lesson_number, scenario,
            warm_up, speaking_prompt, carry, scaffold, writing_mode=args.writing,
            persona_answers=persona_answers, register=persona_register,
        )
        ai_prompt = _build_ai_prompt(persona_prompt, footer)
        output = _build_package(
            date_str, persona_name, persona_role, lesson_number, scenario,
            warm_up, speaking_prompt, persona_prompt, carry,
            writing_mode=args.writing, scaffold=scaffold,
        )
        # Tier 2: hard ceiling on assembled output — no Haiku, just fail fast
        if not args.dry_run and len(output) > max_assembled_chars:
            _hard_fail_length(persona_name, max_assembled_chars)
        print(output)

        if not args.dry_run and args.dropbox:
            fname = _dropbox_filename(date_str, lesson_number, persona_name, scenario)
            dest = _write_dropbox(sync_cfg, fname, output)
            print(f"✅ Written to Dropbox: {dest.name}", file=sys.stderr)

        if args.send and not args.dry_run:
            import time
            _send_telegram(briefing)
            time.sleep(1)
            _send_telegram(ai_prompt)
            print("✅ Sent to Telegram (2 messages).", file=sys.stderr)


if __name__ == '__main__':
    main()
