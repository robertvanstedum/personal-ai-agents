#!/usr/bin/env python3
"""
status.py — Terminal readout of current German practice state.

Reads: progress.json, most recent session JSON, most recent lesson JSON.
Degrades gracefully at every level — safe to run before any sessions exist.

Usage:
  python3 status.py --base-dir language/german/
"""
import argparse
import json
from pathlib import Path


def _load_json(path: Path):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    return None


def _latest_file(directory: Path, pattern: str = "*.json"):
    if not directory.exists():
        return None
    files = sorted(directory.glob(pattern))
    return files[-1] if files else None


def _top_error(progress: dict) -> str:
    counts = progress.get("cumulative_error_counts", {})
    if not any(counts.values()):
        return None
    top = max(counts, key=lambda k: counts[k])
    return f"{top.replace('_', ' ')} ({counts[top]} total)"


def _divider(width: int = 55):
    print("─" * width)


def main():
    parser = argparse.ArgumentParser(description="German practice status readout.")
    parser.add_argument("--base-dir", required=True, help="Path to language/german/ directory")
    args = parser.parse_args()

    base = Path(args.base_dir)
    progress_path = base / "progress.json"
    sessions_dir = base / "sessions"
    lessons_dir = base / "lessons"

    progress = _load_json(progress_path)
    latest_session_path = _latest_file(sessions_dir)
    latest_session = _load_json(latest_session_path) if latest_session_path else None
    latest_lesson_path = _latest_file(lessons_dir)
    latest_lesson = _load_json(latest_lesson_path) if latest_lesson_path else None

    print()
    print("── German Practice Status " + "─" * 30)

    # ── Summary line ──────────────────────────────────────────
    if progress:
        sessions = progress.get("total_sessions", 0)
        minutes = progress.get("total_minutes", 0)
        cards = progress.get("anki_cards_generated", 0)
        print(f"Sessions: {sessions} | Minutes: {minutes} | Anki cards: {cards}")
    else:
        print("No sessions yet — run reviewer.py after your first session.")

    # ── Last session ──────────────────────────────────────────
    print()
    if latest_session and latest_session.get("reviewer_output"):
        r = latest_session["reviewer_output"]
        date = latest_session.get("date", "?")
        persona = latest_session.get("persona", "?")
        scenario = latest_session.get("scenario", "?")
        print(f"Last session ({date} · {persona} — {scenario}):")
        summary = r.get("overall_summary", "")
        if summary:
            print(f"  {summary}")
        err_counts = r.get("error_type_counts", {})
        err_parts = [f"{k}×{v}" for k, v in err_counts.items() if v > 0]
        print(f"  Errors: {', '.join(err_parts) if err_parts else 'none'}")
        next_focus = r.get("next_focus", "")
        if next_focus:
            print(f"  Next focus: {next_focus}")
    elif latest_session:
        date = latest_session.get("date", "?")
        persona = latest_session.get("persona", "?")
        print(f"Last session ({date} · {persona}): not yet reviewed.")
    else:
        print("Last session: none yet.")

    # ── Next lesson ───────────────────────────────────────────
    print()
    if latest_lesson:
        ln = latest_lesson.get("lesson_number", "?")
        persona = latest_lesson.get("persona", "?")
        role = latest_lesson.get("persona_role", "")
        scenario = latest_lesson.get("scenario", "?")
        print(f"Next lesson (Lesson {ln}):")
        print(f"  Persona: {persona}{' — ' + role if role else ''}")
        print(f"  Scenario: {scenario.replace('_', ' ')}")
        warm_up = latest_lesson.get("warm_up", "")
        if warm_up:
            print(f"  Warm-up: {warm_up}")
        prompt = latest_lesson.get("speaking_prompt", "")
        if prompt:
            print(f"  Prompt: {prompt}")
        carry = latest_lesson.get("carry_forward_errors", [])
        if carry:
            print(f"  Carry forward: {carry[0]}")
    else:
        print("Next lesson: not yet generated.")

    # ── Top error pattern ─────────────────────────────────────
    print()
    if progress:
        top = _top_error(progress)
        if top:
            print(f"Top error pattern: {top}")

    _divider()
    print()


if __name__ == "__main__":
    main()
