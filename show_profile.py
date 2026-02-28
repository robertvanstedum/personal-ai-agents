#!/usr/bin/env python3
"""
show_profile.py — What has the curator learned about you?

Reads curator_preferences.json and prints a human-readable summary
of all learned signals: preferred sources, themes, content styles,
and patterns to avoid.

Usage:
    python show_profile.py
    python show_profile.py --json     # raw learned_patterns as JSON
    python show_profile.py --verbose  # include full feedback history
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

PREFS_PATH = Path.home() / '.openclaw' / 'workspace' / 'curator_preferences.json'


def load_prefs():
    try:
        return json.loads(PREFS_PATH.read_text())
    except FileNotFoundError:
        print(f"No preferences file found at {PREFS_PATH}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Preferences file is corrupted: {e}")
        sys.exit(1)


def format_bar(value, max_val, width=20):
    """Simple ASCII bar chart."""
    filled = int(abs(value) / max_val * width) if max_val > 0 else 0
    bar = '█' * filled + '░' * (width - filled)
    return bar


def days_ago(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return delta.days
    except Exception:
        return None


def main():
    raw_json = '--json' in sys.argv
    verbose = '--verbose' in sys.argv

    prefs = load_prefs()
    lp = prefs.get('learned_patterns', {})
    history = prefs.get('feedback_history', {})
    settings = prefs.get('curation_settings', {})

    if raw_json:
        print(json.dumps(lp, indent=2))
        return

    sample_size = lp.get('sample_size', 0)
    last_updated = lp.get('last_updated', '')
    days = days_ago(last_updated) if last_updated else None
    staleness = f"{days}d ago" if days is not None else "unknown"

    # Count feedback events
    total_liked = sum(len(v.get('liked', [])) for v in history.values())
    total_disliked = sum(len(v.get('disliked', [])) for v in history.values())
    total_saved = sum(len(v.get('saved', [])) for v in history.values())
    total_events = total_liked + total_disliked + total_saved

    print()
    print("=" * 56)
    print("  CURATOR LEARNED PROFILE")
    print("=" * 56)
    print(f"  Interactions : {sample_size} scored signals from {total_events} feedback events")
    print(f"  Last updated : {last_updated[:10] if last_updated else 'never'} ({staleness})")
    print(f"  Feedback     : {total_liked} liked  |  {total_disliked} disliked  |  {total_saved} saved")
    print("=" * 56)

    # --- Sources ---
    sources = lp.get('preferred_sources', {})
    if sources:
        print()
        print("  SOURCES")
        print("  -------")
        sorted_sources = sorted(sources.items(), key=lambda x: -x[1])
        max_val = max(abs(v) for v in sources.values()) or 1
        for name, score in sorted_sources:
            bar = format_bar(score, max_val, width=16)
            sign = '+' if score > 0 else ''
            indicator = '  ' if score > 0 else '  '
            print(f"  {bar}  {sign}{score:+d}  {name}")

    # --- Themes ---
    themes = lp.get('preferred_themes', {})
    if themes:
        print()
        print("  THEMES")
        print("  ------")
        sorted_themes = sorted(themes.items(), key=lambda x: -x[1])
        max_val = max(abs(v) for v in themes.values()) or 1
        for name, score in sorted_themes:
            bar = format_bar(score, max_val, width=16)
            print(f"  {bar}  {score:+d}  {name}")

    # --- Content style ---
    CO_TAG_EXCLUDE = {'descriptive'}
    content = {k: v for k, v in lp.get('preferred_content_types', {}).items()
               if k not in CO_TAG_EXCLUDE}
    if content:
        print()
        print("  CONTENT STYLE")
        print("  -------------")
        sorted_content = sorted(content.items(), key=lambda x: -x[1])
        max_val = max(abs(v) for v in content.values()) or 1
        for name, score in sorted_content:
            bar = format_bar(score, max_val, width=16)
            print(f"  {bar}  {score:+d}  {name}")

    # --- Avoid patterns ---
    avoid = lp.get('avoid_patterns', {})
    if avoid:
        print()
        print("  AVOID PATTERNS")
        print("  --------------")
        sorted_avoid = sorted(avoid.items(), key=lambda x: -x[1])
        for name, count in sorted_avoid:
            print(f"  {'▪' * min(count, 10)}  ({count}x)  {name}")

    # --- Serendipity setting ---
    serendipity = settings.get('serendipity_reserve', 0.20)
    print()
    print("  SETTINGS")
    print("  --------")
    print(f"  Serendipity reserve : {int(serendipity * 100)}%  (articles from outside learned patterns)")

    # --- Decay status ---
    if days is not None and days > 30:
        print()
        print(f"  NOTE: Data is {days}d old — decay gate active (weaker signals filtered)")

    # --- Verbose: feedback history ---
    if verbose and history:
        print()
        print("  FEEDBACK HISTORY")
        print("  ----------------")
        for date in sorted(history.keys()):
            day = history[date]
            liked = day.get('liked', [])
            disliked = day.get('disliked', [])
            saved = day.get('saved', [])
            print(f"\n  {date}  ({len(liked)} liked, {len(disliked)} disliked, {len(saved)} saved)")
            for item in liked:
                print(f"    + [{item.get('source','?')}] {item.get('title','')[:60]}")
            for item in saved:
                print(f"    * [{item.get('source','?')}] {item.get('title','')[:60]}")
            for item in disliked:
                print(f"    - [{item.get('source','?')}] {item.get('title','')[:60]}")

    print()
    print("=" * 56)
    print()


if __name__ == '__main__':
    main()
