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
HEADER_FIELDS = {"date", "persona", "scenario", "duration", "mode"}


def _next_session_id(date_str: str, sessions_dir: Path) -> str:
    existing = sorted(sessions_dir.glob(f"{date_str}_*.json"))
    if not existing:
        return f"{date_str}_001"
    last = existing[-1].stem
    n = int(last.split("_")[-1]) + 1
    return f"{date_str}_{n:03d}"


def _parse_header(lines: list) -> dict:
    fields = {}
    for line in lines:
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip().lower()
            if key in HEADER_FIELDS:
                fields[key] = val.strip()
    return fields


def _parse_turns(lines: list) -> list:
    turns = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            speaker, _, text = line.partition(":")
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


def parse_transcript(raw_text: str, sessions_dir: Path) -> Path:
    trigger = START_TRIGGER if START_TRIGGER in raw_text else (START_TRIGGER_ALT if START_TRIGGER_ALT in raw_text.upper() else None)
    if trigger:
        start = raw_text.upper().index(trigger.upper()) + len(trigger)
        end_pos = raw_text.upper().find(END_TRIGGER, start)
        if end_pos == -1:
            end_pos = raw_text.upper().find(END_TRIGGER_ALT, start)
        body = raw_text[start:end_pos].strip() if end_pos != -1 else raw_text[start:].strip()
    else:
        body = raw_text.strip()

    sections = body.split("\n\n", 1)
    header_lines = sections[0].strip().splitlines()
    turn_lines = sections[1].strip().splitlines() if len(sections) > 1 else []

    header = _parse_header(header_lines)
    if not header:
        # No header found — all lines are turns; fall back to domain.json defaults
        turn_lines = header_lines + turn_lines
        domain_cfg = _load_domain_defaults(sessions_dir)
        header = {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "persona": domain_cfg.get("active_persona", "Unknown"),
            "scenario": "unknown",
            "duration": "0",
        }

    date_str = header.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    persona = header.get("persona", "Unknown")
    scenario = header.get("scenario", "unknown")
    mode = header.get("mode", "voice").lower()
    if "duration" in header:
        m = re.search(r'\d+', header["duration"])
        duration = int(m.group()) if m else None
    else:
        duration = None

    session_id = _next_session_id(date_str, sessions_dir)

    turns = _parse_turns(turn_lines)

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

    parse_transcript(raw, sessions_dir)


if __name__ == "__main__":
    main()
