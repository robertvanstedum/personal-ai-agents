"""Deterministic German lesson choices, independent of providers and persistence.

Ordering and fallback behavior are deliberate compatibility contracts. The
reviewer remains responsible for coordinating and writing the resulting lesson.
"""
ANCHOR_PERSONA = "Frau Berger"


def pick_next_persona(personas: list, progress: dict) -> dict:
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


def pick_next_scenario(persona: dict, progress: dict) -> str:
    covered = set(progress.get("scenarios_covered", []))
    for s in persona["scenarios"]:
        if s not in covered:
            return s
    return persona["scenarios"][0]  # all covered — cycle from start


def top_error_type(progress: dict) -> str:
    counts = progress.get("cumulative_error_counts", {})
    if not any(counts.values()):
        return "gender"  # default for first session
    return max(counts, key=lambda k: counts[k])


def carry_forward_errors(reviewer_output: dict, top_type: str, date_str: str) -> list:
    errors = [e for e in reviewer_output.get("errors", []) if e.get("type") == top_type]
    result = []
    for e in errors[:2]:
        result.append(f"{e['correction']} (was: {e['original']}) — session {date_str}")
    return result
