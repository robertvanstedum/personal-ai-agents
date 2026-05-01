#!/usr/bin/env python3
"""
parse_transcript.py — Convert ---SESSION---/---END--- transcript text to session JSON.

Standalone entry point. Works independently of OpenClaw.
Freeform fallback: if no ---SESSION--- delimiter is present, treats the entire
input as raw turns and defaults header fields from domain.json.

Usage:
  python3 parse_transcript.py --input path/to/transcript.txt --base-dir language/german/
  python3 parse_transcript.py --stdin --base-dir language/german/
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

START_TRIGGER = "---SESSION---"
START_TRIGGER_ALT = "\u2014SESSION\u2014"  # iPhone auto-corrects --- to em-dash
END_TRIGGER = "---END---"
END_TRIGGER_ALT = "\u2014END\u2014"
HEADER_FIELDS = {"date", "persona", "scenario", "duration", "mode", "drill", "drill-session"}

# Format 3: ##YYYY-MM-DD|persona|scenario|duration|mode
_KEY_LINE_RE = re.compile(
    r'^##(\d{4}-\d{2}-\d{2})\|([^|]+)\|([^|]+)\|(\d+)\|(\w+)$',
    re.MULTILINE,
)


def _next_session_id(date_str: str, sessions_dir: Path) -> str:
    existing = sorted(sessions_dir.glob(f"{date_str}_*.json"))
    if not existing:
        return f"{date_str}_001"
    last = existing[-1].stem
    n = int(last.split("_")[-1]) + 1
    return f"{date_str}_{n:03d}"


def _parse_header(lines: list) -> dict:
    # Expand lines that contain multiple fields inline (e.g. Grok single-line format:
    # "Date: 2026-04-26 Persona: Klaus Scenario: ..." all on one line)
    field_re = re.compile(
        r'\b(' + '|'.join(re.escape(f) for f in HEADER_FIELDS) + r')\s*:',
        re.IGNORECASE,
    )
    expanded = []
    for line in lines:
        matches = list(field_re.finditer(line))
        if len(matches) <= 1:
            expanded.append(line)
        else:
            for i, m in enumerate(matches):
                end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
                expanded.append(line[m.start():end].strip())

    fields = {}
    for line in expanded:
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip().lower()
            if key in HEADER_FIELDS:
                fields[key] = val.strip()
    return fields


# Matches "Speaker Name:" at turn boundaries within a single-line dump.
# Speaker names are 1-3 words, each word starts with a capital letter,
# optionally prefixed with a title abbreviation ending in a period (e.g. Dr.).
_INLINE_SPEAKER_RE = re.compile(
    r'(?:^|(?<=[\s.?!]))'
    r'(?:(?:Dr|Frau|Herr|Prof)\.\s+)?'
    r'(?:[A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)?)'
    r':'
)


def _split_inline_turns(line: str) -> list:
    """Split a single-line conversation dump on speaker-name boundaries."""
    matches = list(_INLINE_SPEAKER_RE.finditer(line))
    if len(matches) < 2:
        return [line]
    segments = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
        segments.append(line[m.start():end].strip())
    return segments


def _parse_turns(lines: list) -> list:
    turns = []
    for line in lines:
        # Normalize em-dash and en-dash to hyphen; collapse Unicode whitespace
        line = line.replace('\u2014', '-').replace('\u2013', '-')
        line = ' '.join(line.split())
        if not line:
            continue
        # Detect single-line dump (multiple speaker prefixes on one line)
        segments = _split_inline_turns(line)
        for seg in segments:
            if ":" in seg:
                speaker, _, text = seg.partition(":")
                speaker = speaker.strip()
                text = text.strip()
                if speaker and text:
                    turns.append({"speaker": speaker, "text": text})
    return turns


def _load_domain_defaults(sessions_dir: Path) -> dict:
    cfg = sessions_dir.parent / 'config/domain.json'
    if cfg.exists():
        return json.loads(cfg.read_text())
    return {}


def _load_lesson_defaults(sessions_dir: Path) -> dict:
    """Infer persona/scenario from most recent lesson file; fall back to domain.json."""
    domain = _load_domain_defaults(sessions_dir)
    defaults = {
        "persona": domain.get("active_persona", "Unknown"),
        "scenario": "unknown",
    }
    lessons_dir = sessions_dir.parent / "lessons"
    if lessons_dir.exists():
        lesson_files = sorted(lessons_dir.glob("*_lesson.json"))
        if lesson_files:
            lesson = json.loads(lesson_files[-1].read_text(encoding="utf-8"))
            defaults["persona"] = lesson.get("persona", defaults["persona"])
            defaults["scenario"] = lesson.get("scenario", defaults["scenario"])
    return defaults


def parse_transcript(raw_text: str, sessions_dir: Path) -> Path:
    # --- Format 3: ##YYYY-MM-DD|persona|scenario|duration|mode key line ---
    key_match = _KEY_LINE_RE.search(raw_text)
    if key_match:
        date_str, persona, scenario, dur_str, mode = key_match.groups()
        persona = persona.strip()
        scenario = scenario.strip()
        mode = mode.strip().lower()
        duration = int(dur_str)
        # Turn lines are everything except the key line
        turn_lines = [l for l in raw_text.splitlines() if not l.startswith("##")]
        turns = _parse_turns(turn_lines)
        if len(turns) < 2:
            raise ValueError(
                "⚠️ Couldn't parse a clear conversation (fewer than 2 turns found). "
                "Paste again or upload the file directly — session not saved."
            )
        session_id = _next_session_id(date_str, sessions_dir)
        session = {
            "session_id": session_id, "date": date_str, "persona": persona,
            "scenario": scenario, "duration_estimate_min": duration, "mode": mode,
            "source": "manual", "raw_transcript": turns,
            "reviewer_output": None, "anki_generated": False,
            "next_lesson_generated": False, "drill_mode": False,
            "drill_session": 0, "drill_total": 0,
        }
        out_path = sessions_dir / f"{session_id}.json"
        out_path.write_text(json.dumps(session, indent=2, ensure_ascii=False))
        print(f"✅ Session saved: {out_path}")
        print(f"   Persona: {persona} | Scenario: {scenario} | Turns: {len(turns)}")
        return out_path

    # --- Formats 1 & 2: ---SESSION--- / —SESSION— delimited ---
    trigger = START_TRIGGER if START_TRIGGER in raw_text else (START_TRIGGER_ALT if START_TRIGGER_ALT in raw_text else None)
    if trigger:
        start = raw_text.upper().index(trigger.upper()) + len(trigger)
        end_pos = raw_text.upper().find(END_TRIGGER, start)
        if end_pos == -1:
            end_pos = raw_text.upper().find(END_TRIGGER_ALT, start)
        body = raw_text[start:end_pos].strip() if end_pos != -1 else raw_text[start:].strip()
    else:
        body = raw_text.strip()

    # Fix missing newline when a header value runs directly into the conversation body,
    # e.g. "Mode: voiceDr. Huber: Guten Tag!" → "Mode: voice\nDr. Huber: Guten Tag!"
    body = re.sub(r'(?i)(Mode:\s*(?:voice|writing))([A-ZÄÖÜ])', r'\1\n\2', body)
    body = re.sub(r'(?i)(Duration:\s*\d+)([A-ZÄÖÜ])', r'\1\n\2', body)

    sections = body.split("\n\n", 1)
    if len(sections) == 2:
        header_lines = sections[0].strip().splitlines()
        turn_lines = sections[1].strip().splitlines()
    else:
        # No blank line separator (common in iPhone/Grok formatting) —
        # split by detecting known header fields vs. speaker turns
        header_lines = []
        turn_lines = []
        past_header = False
        for line in body.splitlines():
            if not line.strip():
                continue
            if not past_header and ":" in line:
                key = line.partition(":")[0].strip().lower()
                if key in HEADER_FIELDS:
                    header_lines.append(line)
                    continue
            past_header = True
            turn_lines.append(line)

    header = _parse_header(header_lines)
    if not header:
        # No header found — fuzzy path: infer metadata from latest lesson file
        turn_lines = header_lines + turn_lines
        lesson_defaults = _load_lesson_defaults(sessions_dir)
        header = {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "persona": lesson_defaults["persona"],
            "scenario": lesson_defaults["scenario"],
        }

    date_str = header.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    persona = header.get("persona", "Unknown")
    scenario = header.get("scenario", "unknown")
    mode = header.get("mode", "voice").lower()

    # Mode detection from content — fuzzy override when header didn't set it
    if mode == "voice" and "mode: writing" in raw_text.lower():
        mode = "writing"

    if "duration" in header:
        m = re.search(r'\d+', header["duration"])
        duration = int(m.group()) if m else None
    else:
        duration = None

    # Drill mode fields
    drill_mode = header.get("drill", "").lower() == "true"
    drill_session = 0
    drill_total = 0
    raw_ds = header.get("drill-session", "")  # e.g. "2 of 3"
    if raw_ds:
        parts = raw_ds.split("of")
        if len(parts) == 2:
            try:
                drill_session = int(parts[0].strip())
                drill_total   = int(parts[1].strip())
            except ValueError:
                pass

    session_id = _next_session_id(date_str, sessions_dir)

    turns = _parse_turns(turn_lines)

    # Estimate duration from turn count if not provided (≈ 0.5 min per turn)
    if duration is None:
        duration = max(1, len(turns) // 2)

    if len(turns) < 2:
        raise ValueError(
            "⚠️ Couldn't parse a clear conversation (fewer than 2 turns found). "
            "Paste again or upload the file directly — session not saved."
        )

    session = {
        "session_id": session_id,
        "date": date_str,
        "persona": persona,
        "scenario": scenario,
        "duration_estimate_min": duration,
        "mode": mode,
        "source": "manual",
        "raw_transcript": turns,
        "reviewer_output": None,
        "anki_generated": False,
        "next_lesson_generated": False,
        "drill_mode": drill_mode,
        "drill_session": drill_session,
        "drill_total": drill_total,
    }

    out_path = sessions_dir / f"{session_id}.json"
    out_path.write_text(json.dumps(session, indent=2, ensure_ascii=False))
    print(f"✅ Session saved: {out_path}")
    print(f"   Persona: {persona} | Scenario: {scenario} | Turns: {len(turns)}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Parse a German session transcript to JSON.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="Path to raw transcript text file")
    src.add_argument("--stdin", action="store_true", help="Read from stdin")
    parser.add_argument("--base-dir", required=True, help="Path to language/german/ directory")
    args = parser.parse_args()

    base = Path(args.base_dir)
    sessions_dir = base / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    if args.stdin:
        raw = sys.stdin.read()
    else:
        raw = Path(args.input).read_text(encoding="utf-8")

    try:
        parse_transcript(raw, sessions_dir)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
