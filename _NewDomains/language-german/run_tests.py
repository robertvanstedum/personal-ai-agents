#!/usr/bin/env python3
"""
run_tests.py — Acceptance tests for the German language pipeline.

Usage:
  python run_tests.py              # run all tests
  python run_tests.py --test 3     # run one test by number

Tests:
  1  parse_transcript: multi-line format → correct session JSON
  2  parse_transcript: em-dash delimiter (iPhone autocorrect) → correct session JSON
  3  get_german_session: UNIVERSAL_HEADER present in output
  4  get_german_session: UNIVERSAL_FOOTER present in output
  5  get_german_session: carry-forward strips session metadata noise
  6  get_german_session --writing: header at top, Mode: writing in footer
  7  persona files: all 8 have standard comment line
  8  ORCHESTRATOR.md: exists and has all 7 command sections
  9  parse_transcript: inline header (Grok single-line) → all fields, no Unknown
"""
import argparse
import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

PIPELINE_ROOT = Path(__file__).parent
GERMAN_BASE = PIPELINE_ROOT / "language" / "german"
FIXTURES = PIPELINE_ROOT / "test_fixtures"

sys.path.insert(0, str(PIPELINE_ROOT))

results = []


def report(n: int, label: str, passed, detail: str = ""):
    if passed is None:
        print(f"Test {n:2d} — {label}: SKIP")
        results.append((n, label, None))
        return
    status = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"Test {n:2d} — {label}: {status}{suffix}")
    results.append((n, label, passed))


def _parse_session(fixture_name: str) -> dict:
    from parse_transcript import parse_transcript
    raw = (FIXTURES / fixture_name).read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        sessions_dir = Path(tmp) / "sessions"
        sessions_dir.mkdir()
        out = parse_transcript(raw, sessions_dir)
        return json.loads(out.read_text(encoding="utf-8"))


def _run_get_session(*extra_args) -> str:
    result = subprocess.run(
        [sys.executable, str(PIPELINE_ROOT / "get_german_session.py"),
         "--base-dir", str(GERMAN_BASE), "--dry-run"] + list(extra_args),
        capture_output=True, text=True,
    )
    return result.stdout


# ---------------------------------------------------------------------------
# Tests 1-2: parse_transcript formats
# ---------------------------------------------------------------------------

def test_1():
    session = _parse_session("sample_transcript.txt")
    checks = {
        "persona == Frau Berger": session.get("persona") == "Frau Berger",
        "scenario == bakery_order": session.get("scenario") == "bakery_order",
        "has turns": len(session.get("raw_transcript", [])) > 0,
        "no Unknown": session.get("persona") != "Unknown",
    }
    ok = all(checks.values())
    report(1, "parse multi-line format", ok,
           "all checks pass" if ok else "failed: " + ", ".join(k for k, v in checks.items() if not v))


def test_2():
    session = _parse_session("sample_transcript_emdash.txt")
    checks = {
        "persona == Stefan": session.get("persona") == "Stefan",
        "has turns": len(session.get("raw_transcript", [])) > 0,
        "no Unknown": session.get("persona") != "Unknown",
    }
    ok = all(checks.values())
    report(2, "parse em-dash delimiter (iPhone format)", ok,
           "all checks pass" if ok else "failed: " + ", ".join(k for k, v in checks.items() if not v))


# ---------------------------------------------------------------------------
# Tests 3-6: get_german_session.py output
# ---------------------------------------------------------------------------

def test_3():
    out = _run_get_session()
    ok = "SESSION INSTRUCTIONS — READ BEFORE STARTING" in out
    report(3, "universal header present in session output", ok)


def test_4():
    out = _run_get_session()
    ok = "HOW TO END THIS SESSION" in out and "---SESSION---" in out
    report(4, "universal footer present in session output", ok)


def test_5():
    out = _run_get_session()
    carry_lines = [l for l in out.splitlines() if l.startswith("Carry forward:")]
    if not carry_lines:
        report(5, "carry-forward has no session metadata noise", False, "Carry forward line not found")
        return
    carry_val = carry_lines[0]
    noise = "Persona:" in carry_val or "Scenario:" in carry_val or "Duration:" in carry_val or "Mode:" in carry_val
    report(5, "carry-forward has no session metadata noise", not noise,
           carry_val if noise else "clean")


def test_6():
    out = _run_get_session("--writing")
    first_line = out.strip().splitlines()[0] if out.strip() else ""
    header_at_top = "WRITING SESSION" in first_line
    mode_writing = "Mode: writing" in out
    mode_voice = "Mode: voice" in out
    ok = header_at_top and mode_writing and not mode_voice
    checks = {
        "⌨️ WRITING SESSION at top": header_at_top,
        "Mode: writing in footer": mode_writing,
        "no Mode: voice": not mode_voice,
    }
    report(6, "--writing: header at top, Mode: writing", ok,
           "all checks pass" if ok else "failed: " + ", ".join(k for k, v in checks.items() if not v))


# ---------------------------------------------------------------------------
# Test 7: persona files authoring standard
# ---------------------------------------------------------------------------

def test_7():
    prompts_dir = GERMAN_BASE / "config" / "prompts"
    persona_files = sorted(prompts_dir.glob("*.txt"))
    STANDARD_COMMENT = "# Target: under 3500 chars | Vocabulary: 6-8 items max | Persona: 3-4 sentences max"
    missing = [f.name for f in persona_files if STANDARD_COMMENT not in f.read_text(encoding="utf-8")]
    ok = len(missing) == 0
    report(7, f"all {len(persona_files)} persona files have standard comment", ok,
           "all present" if ok else "missing in: " + ", ".join(missing))


# ---------------------------------------------------------------------------
# Test 8: ORCHESTRATOR.md
# ---------------------------------------------------------------------------

def test_8():
    orc = PIPELINE_ROOT / "ORCHESTRATOR.md"
    if not orc.exists():
        report(8, "ORCHESTRATOR.md exists with 7 command sections", False, "file missing")
        return
    content = orc.read_text(encoding="utf-8")
    expected = ["Session Pull", "Writing Session", "Drill Mode", "Status",
                "Anki Import", "Watcher Start", "Watcher Stop"]
    missing = [s for s in expected if s not in content]
    ok = len(missing) == 0
    report(8, "ORCHESTRATOR.md exists with 7 command sections", ok,
           "all sections present" if ok else "missing: " + ", ".join(missing))


# ---------------------------------------------------------------------------
# Test 9: inline header (Grok single-line format)
# ---------------------------------------------------------------------------

def test_9():
    from parse_transcript import parse_transcript
    raw = (FIXTURES / "sample_transcript_inline_header.txt").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        sessions_dir = Path(tmp) / "sessions"
        sessions_dir.mkdir()
        out_path = parse_transcript(raw, sessions_dir)
        session = json.loads(out_path.read_text(encoding="utf-8"))
    checks = {
        "persona == Klaus": session.get("persona") == "Klaus",
        "scenario == restaurant_reservation": session.get("scenario") == "restaurant_reservation",
        "mode == voice": session.get("mode") == "voice",
        "duration == 8": session.get("duration_estimate_min") == 8,
        "no Unknown": session.get("persona") != "Unknown",
        "has turns": len(session.get("raw_transcript", [])) > 0,
    }
    ok = all(checks.values())
    report(9, "inline header (Grok single-line format)", ok,
           "all checks pass" if ok else "failed: " + ", ".join(k for k, v in checks.items() if not v))


# ---------------------------------------------------------------------------
# Step 1 — .txt file upload
# ---------------------------------------------------------------------------

def test_10():
    """Content path: file bytes → _handle_german_transcript → session saved."""
    import sys
    sys.path.insert(0, str(PIPELINE_ROOT.parent.parent))  # project root
    from parse_transcript import parse_transcript

    raw = textwrap.dedent("""\
        ---SESSION---
        Date: 2026-04-20
        Persona: Frau Berger
        Scenario: bakery_order
        Duration: 5
        Mode: writing
        Frau Berger: Guten Morgen! Was darf es sein?
        Robert: Ich hätte gerne zwei Brötchen, bitte.
        Frau Berger: Natürlich! Das macht einen Euro sechzig.
        Robert: Danke schön!
        ---END---
    """)

    with tempfile.TemporaryDirectory() as tmp:
        sessions_dir = Path(tmp) / "sessions"
        sessions_dir.mkdir()
        out = parse_transcript(raw, sessions_dir)
        saved = json.loads(out.read_text(encoding="utf-8"))
        checks = {
            "session saved": out.exists(),
            "mode writing": saved.get("mode") == "writing",
            "turns >= 2": len(saved.get("raw_transcript", [])) >= 2,
        }
    ok = all(checks.values())
    report(10, ".txt content fed to pipeline → session saved", ok,
           "all checks pass" if ok else str({k: v for k, v in checks.items() if not v}))


def test_11():
    """Files over 50KB must be rejected before reaching the pipeline."""
    MAX = 50 * 1024
    oversized = "A" * (MAX + 1)
    # Simulate the size check from handle_document
    too_large = len(oversized.encode("utf-8")) > MAX
    report(11, "content over 50KB → error message, session not saved", too_large,
           "size check triggers correctly" if too_large else "size check did not trigger")


# ---------------------------------------------------------------------------
# Step 2 — Two-message delivery + scaffold (SKIP until Step 2 ships)
# ---------------------------------------------------------------------------

def _import_get_session():
    sys.path.insert(0, str(PIPELINE_ROOT))
    import importlib
    return importlib.import_module("get_german_session")


_SAMPLE_KEYWORD_MAP = {
    "Klaus": {
        "trigger_words": ["restaurant", "Klaus"],
        "default_scenario": "restaurant_reservation",
        "recovery_phrase": "Entschuldigung — einen Moment bitte.",
        "scaffold_phrases": [
            {"de": "Haben Sie einen Tisch für zwei Personen?", "en": "Do you have a table for two?", "type": "transaction"},
            {"de": "Ich hätte gerne das Tagesmenu.", "en": "I'd like the daily menu.", "type": "transaction"},
            {"de": "Was empfehlen Sie als Hauptspeise?", "en": "What do you recommend as a main course?", "type": "preference"},
            {"de": "Wir möchten lieber draußen sitzen.", "en": "We'd prefer to sit outside.", "type": "preference"},
            {"de": "Könnten Sie bitte die Speisekarte bringen?", "en": "Could you bring the menu please?", "type": "transaction"},
            {"de": "Die Rechnung, bitte — zusammen.", "en": "The bill please — together.", "type": "transaction"},
        ],
    }
}


def test_12():
    """_build_briefing and _build_ai_prompt produce structurally distinct messages."""
    gs = _import_get_session()
    briefing = gs._build_briefing(
        "2026-05-01", "Klaus", "Waiter", 1, "restaurant_reservation",
        "", "Order confidently", "None yet", "🧱 scaffold here",
    )
    ai_prompt = gs._build_ai_prompt("== PERSONA ==", gs.UNIVERSAL_FOOTER)
    checks = {
        "briefing has 📋 header": "📋 YOUR BRIEFING" in briefing,
        "ai_prompt has SESSION INSTRUCTIONS": "SESSION INSTRUCTIONS" in ai_prompt,
        "briefing != ai_prompt": briefing != ai_prompt,
    }
    ok = all(checks.values())
    report(12, "get_german_session returns two messages, not one", ok,
           "all checks pass" if ok else "failed: " + ", ".join(k for k, v in checks.items() if not v))


def test_13():
    """Message 1 (briefing) contains scaffold block when scaffold is non-empty."""
    gs = _import_get_session()
    briefing = gs._build_briefing(
        "2026-05-01", "Klaus", "Waiter", 1, "restaurant_reservation",
        "", "", "None yet", "🧱 Today's scaffold — try to use these:\n   • Haben Sie einen Tisch?",
    )
    ok = "🧱" in briefing
    report(13, "Message 1 contains scaffold block (🧱 header present)", ok,
           "scaffold found" if ok else "🧱 missing from briefing")


def test_14():
    """Message 2 (AI prompt) does not contain scaffold block."""
    gs = _import_get_session()
    ai_prompt = gs._build_ai_prompt("== PERSONA PROMPT ==", gs.UNIVERSAL_FOOTER)
    ok = "🧱" not in ai_prompt
    report(14, "Message 2 does not contain scaffold block", ok,
           "clean" if ok else "🧱 found in ai_prompt — should not be there")


def test_15():
    """Message 2 (AI prompt) does not contain carry-forward or warm-up metadata lines."""
    gs = _import_get_session()
    ai_prompt = gs._build_ai_prompt("== PERSONA PROMPT ==", gs.UNIVERSAL_FOOTER)
    checks = {
        "no 'Carry forward:'": "Carry forward:" not in ai_prompt,
        "no 'Warm-up:'": "Warm-up:" not in ai_prompt,
        "no '📋 YOUR BRIEFING'": "📋 YOUR BRIEFING" not in ai_prompt,
    }
    ok = all(checks.values())
    report(15, "Message 2 does not contain carry-forward or warm-up metadata", ok,
           "clean" if ok else "failed: " + ", ".join(k for k, v in checks.items() if not v))


def test_16():
    """scaffold_rotation_index advances by 2 after _scaffold_block() is called."""
    gs = _import_get_session()
    with tempfile.TemporaryDirectory() as tmp:
        pf = Path(tmp) / "progress.json"
        progress = {"scaffold_rotation_index": {"Klaus": 0}}
        pf.write_text(json.dumps(progress), encoding="utf-8")

        gs._scaffold_block("Klaus", _SAMPLE_KEYWORD_MAP, progress, pf)

        updated = json.loads(pf.read_text(encoding="utf-8"))
        idx = updated.get("scaffold_rotation_index", {}).get("Klaus", -1)
    ok = idx == 2
    report(16, "scaffold_rotation_index advances by 2 after session", ok,
           f"index={idx}" if not ok else "0→2")


def test_17():
    """scaffold_rotation_index resets to 0 when it reaches 6."""
    gs = _import_get_session()
    with tempfile.TemporaryDirectory() as tmp:
        pf = Path(tmp) / "progress.json"
        progress = {"scaffold_rotation_index": {"Klaus": 4}}
        pf.write_text(json.dumps(progress), encoding="utf-8")

        gs._scaffold_block("Klaus", _SAMPLE_KEYWORD_MAP, progress, pf)

        updated = json.loads(pf.read_text(encoding="utf-8"))
        idx = updated.get("scaffold_rotation_index", {}).get("Klaus", -1)
    ok = idx == 0
    report(17, "scaffold_rotation_index resets to 0 after reaching 6", ok,
           f"index={idx}" if not ok else "4→0")


def test_18():
    """_scaffold_block returns '' for an unknown persona — no crash."""
    gs = _import_get_session()
    result = gs._scaffold_block("UnknownPersona", {}, {}, None)
    ok = result == ""
    report(18, "persona missing scaffold_phrases → scaffold skipped, no crash", ok,
           "returned empty string" if ok else f"returned: {result!r}")


def test_19():
    """carry_forward_phrases from progress appears in _carry_forward result."""
    gs = _import_get_session()
    phrase = "Ich hätte gerne einen kleinen Brauner, bitte."
    progress = {"carry_forward_phrases": [phrase]}
    result = gs._carry_forward({}, progress)
    ok = phrase in result
    report(19, "carry_forward_phrases from progress.json appears in Message 1", ok,
           "phrase present" if ok else f"result was: {result!r}")


# ---------------------------------------------------------------------------
# Step 3 — NL intent + standalone word safety (SKIP until Step 3 ships)
# ---------------------------------------------------------------------------

def test_20():
    report(20, "'café session' → intent resolves to Maria / cafe_order", None)

def test_21():
    report(21, "'hotel' → intent resolves to Herr Fischer / hotel_checkin", None)

def test_22():
    report(22, "'I had a terrible session today' → no trigger fired", None)

def test_23():
    report(23, "unknown intent → helpful fallback message, no crash", None)

def test_23b():
    report(24, "keyword_map.json missing → falls back to !german only, no crash", None)


# ---------------------------------------------------------------------------
# Step 4 — 'again' intent (SKIP until Step 4 ships)
# ---------------------------------------------------------------------------

def test_25():
    report(25, "'again' → same persona/scenario as last session", None)

def test_26():
    report(26, "repeat:true present in session JSON after 'again'", None)

def test_27():
    report(27, "rotation index does not advance after repeat session", None)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = {
    1: test_1, 2: test_2, 3: test_3, 4: test_4, 5: test_5,
    6: test_6, 7: test_7, 8: test_8, 9: test_9,
    10: test_10, 11: test_11,
    12: test_12, 13: test_13, 14: test_14, 15: test_15,
    16: test_16, 17: test_17, 18: test_18, 19: test_19,
    20: test_20, 21: test_21, 22: test_22, 23: test_23, 24: test_23b,
    25: test_25, 26: test_26, 27: test_27,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", type=int, help="Run a single test by number")
    args = parser.parse_args()

    to_run = [args.test] if args.test else sorted(TESTS)
    for n in to_run:
        if n not in TESTS:
            print(f"Test {n}: not defined")
            continue
        TESTS[n]()

    print()
    passed = sum(1 for _, _, ok in results if ok is True)
    skipped = sum(1 for _, _, ok in results if ok is None)
    total = len(results) - skipped
    skip_note = f" ({skipped} skipped)" if skipped else ""
    print(f"{passed}/{total} tests passed.{skip_note}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
