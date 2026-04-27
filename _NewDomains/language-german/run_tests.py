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
from pathlib import Path

PIPELINE_ROOT = Path(__file__).parent
GERMAN_BASE = PIPELINE_ROOT / "language" / "german"
FIXTURES = PIPELINE_ROOT / "test_fixtures"

sys.path.insert(0, str(PIPELINE_ROOT))

results = []


def report(n: int, label: str, passed: bool, detail: str = ""):
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
# Runner
# ---------------------------------------------------------------------------

TESTS = {1: test_1, 2: test_2, 3: test_3, 4: test_4, 5: test_5,
         6: test_6, 7: test_7, 8: test_8, 9: test_9}


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
    passed = sum(1 for _, _, ok in results if ok)
    total = len(results)
    print(f"{passed}/{total} tests passed.")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
