#!/usr/bin/env python3
"""
run_tests.py — Acceptance tests for the German language pipeline.

Usage:
  python run_tests.py              # run all tests
  python run_tests.py --test 9     # run one test by number
"""
import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

PIPELINE_ROOT = Path(__file__).parent
GERMAN_BASE = PIPELINE_ROOT / "language" / "german"
FIXTURES = PIPELINE_ROOT / "test_fixtures"

sys.path.insert(0, str(PIPELINE_ROOT))

PASS = "PASS"
FAIL = "FAIL"
results = []


def report(n: int, label: str, passed: bool, detail: str = ""):
    status = PASS if passed else FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"Test {n:2d} — {label}: {status}{suffix}")
    results.append((n, label, passed))


# ---------------------------------------------------------------------------
# Test 9 — inline header parsing (Grok single-line format)
# ---------------------------------------------------------------------------

def test_9():
    from parse_transcript import parse_transcript

    fixture = FIXTURES / "sample_transcript_inline_header.txt"
    raw = fixture.read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        sessions_dir = Path(tmp) / "sessions"
        sessions_dir.mkdir()
        # Provide a minimal domain.json so fallback path doesn't fire
        cfg_dir = Path(tmp) / "config"
        cfg_dir.mkdir()
        (cfg_dir / "domain.json").write_text(
            json.dumps({"active_persona": "Unknown", "current_lesson_number": 1})
        )

        out_path = parse_transcript(raw, sessions_dir)
        session = json.loads(out_path.read_text(encoding="utf-8"))

    checks = {
        "persona == Klaus": session.get("persona") == "Klaus",
        "scenario == restaurant_reservation": session.get("scenario") == "restaurant_reservation",
        "mode == voice": session.get("mode") == "voice",
        "duration == 8": session.get("duration_estimate_min") == 8,
        "no Unknown persona": session.get("persona") != "Unknown",
        "has turns": len(session.get("raw_transcript", [])) > 0,
    }

    all_pass = all(checks.values())
    failed = [k for k, v in checks.items() if not v]
    detail = ("all checks pass" if all_pass
              else "failed: " + ", ".join(failed))
    report(9, "inline header (Grok single-line format)", all_pass, detail)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = {9: test_9}


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
