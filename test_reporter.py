"""
test_reporter.py — Shared build-phase test reporter.

Used by all domain test suites. Provides consistent failure visibility,
dated report files, and cross-suite build stats.

Usage in a test file:
    from test_reporter import TestReporter
    runner = TestReporter(suite="german_domain", group="Group A")
    runner.report("D01", "normalize: strips punctuation", passed,
                  expected="ich bin", got=result)
    runner.finish()   # prints summary, writes report, sys.exit(0|1)

Stats across all suites:
    python3 test_reporter.py --stats [--suite german_domain]

Ways of working:
    - Every build phase that touches a domain module ships with a test file
      that uses this reporter.
    - Reports are written to _working/test_<suite>_<timestamp>.md.
      All runs are kept — they are gitignored but readable locally.
    - At the end of a build phase, run --stats for a defect count summary.
    - Failure output shows expected/got explicitly — no editorial commentary.
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

_REPO_ROOT = Path(__file__).parent
_WORKING_DIR = _REPO_ROOT / "_working"


class TestReporter:
    def __init__(self, suite: str, group: str = ""):
        self.suite = suite
        self.group = group
        self._results: list[dict] = []
        self._run_ts = datetime.now().strftime("%Y%m%d_%H%M")

    def report(
        self,
        test_num: str,
        name: str,
        passed: bool,
        detail: str = "",
        *,
        expected: str = "",
        got: str = "",
    ) -> None:
        status = "PASS" if passed else "FAIL"
        suffix = f": {detail}" if detail else ""
        print(f"Test {test_num} — {name}: {status}{suffix}")
        self._results.append({
            "num": test_num,
            "name": name,
            "status": status,
            "detail": detail,
            "expected": expected,
            "got": got,
        })

    def _print_failure_summary(self) -> None:
        failures = [r for r in self._results if r["status"] == "FAIL"]
        if not failures:
            return
        print("\n" + "─" * 60)
        print(f"FAILURES ({len(failures)}):")
        for r in failures:
            print(f"\n  ❌ {r['num']} — {r['name']}")
            if r.get("expected"):
                print(f"     expected : {r['expected']}")
                print(f"     got      : {r['got']}")
            elif r.get("detail"):
                print(f"     detail   : {r['detail']}")
        print("─" * 60)

    def _write_report(self) -> Path:
        out_path = _WORKING_DIR / f"test_{self.suite}_{self._run_ts}.md"
        passed = sum(1 for r in self._results if r["status"] == "PASS")
        failed = sum(1 for r in self._results if r["status"] == "FAIL")
        total  = len(self._results)
        group_label = f" — {self.group}" if self.group else ""

        lines = [
            f"# {self.suite}{group_label} — Test Results",
            f"**Run:** {self._run_ts.replace('_', ' ')}  ",
            f"**Suite:** {self.suite}{group_label}  ",
            f"**Result:** {passed}/{total} passed" + (f" — **{failed} FAILED**" if failed else ""),
            "",
        ]

        failures = [r for r in self._results if r["status"] == "FAIL"]
        if failures:
            lines += ["## Failures", ""]
            for r in failures:
                lines.append(f"### ❌ {r['num']} — {r['name']}")
                if r.get("expected"):
                    lines.append(f"- **Expected:** `{r['expected']}`")
                    lines.append(f"- **Got:** `{r['got']}`")
                if r.get("detail"):
                    lines.append(f"- **Detail:** {r['detail']}")
                lines.append("")
            lines += ["---", ""]

        lines += [
            "## Full Results",
            "",
            "| # | Test | Status | Detail |",
            "|---|------|--------|--------|",
        ]
        for r in self._results:
            icon = "✅" if r["status"] == "PASS" else "❌"
            detail = r["detail"].replace("|", "\\|")
            lines.append(f"| {r['num']} | {r['name']} | {icon} {r['status']} | {detail} |")

        out_path.write_text("\n".join(lines) + "\n")
        return out_path

    def finish(self) -> None:
        """Print summary, write report, exit with 0 (all pass) or 1 (any fail)."""
        passed = sum(1 for r in self._results if r["status"] == "PASS")
        total  = len(self._results)
        self._print_failure_summary()
        print(f"\n{passed}/{total} tests passed.")
        out_path = self._write_report()
        print(f"Report: {out_path}")
        sys.exit(0 if passed == total else 1)


def print_stats(suite_filter: str = "") -> None:
    """Print defect summary across all saved reports, optionally filtered by suite name."""
    pattern = f"test_{suite_filter}*.md" if suite_filter else "test_*.md"
    reports = sorted(_WORKING_DIR.glob(pattern))
    if not reports:
        label = f"suite '{suite_filter}'" if suite_filter else "any suite"
        print(f"No reports found for {label} in {_WORKING_DIR}/")
        return

    suites_seen: dict[str, list] = {}
    for p in reports:
        # Extract suite name: test_<suite>_YYYYMMDD_HHMM.md
        stem = p.stem  # e.g. test_german_domain_20260519_0653
        parts = stem.split("_")
        # Last two parts are date + time; everything between 'test' and those is the suite
        suite = "_".join(parts[1:-2]) if len(parts) >= 4 else "_".join(parts[1:])
        suites_seen.setdefault(suite, []).append(p)

    total_runs = len(reports)
    total_failures = 0

    print(f"\nBuild phase stats — {total_runs} run(s) across {len(suites_seen)} suite(s):\n")
    for suite, suite_reports in suites_seen.items():
        print(f"  {suite}:")
        suite_failures = 0
        for p in suite_reports:
            text = p.read_text()
            ts = p.stem.split("_")[-2] + " " + p.stem.split("_")[-1]
            result_line = next((l for l in text.splitlines() if "**Result:**" in l), "")
            result_str = result_line.replace("**Result:**", "").replace("**", "").strip()
            fail_count = text.count("❌")
            suite_failures += fail_count
            print(f"    {ts}  {result_str}")
        total_failures += suite_failures
        if suite_failures:
            print(f"    → {suite_failures} failure instance(s)")
        print()

    print(f"  Total failure instances across all runs: {total_failures}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build-phase test report stats")
    parser.add_argument("--stats", action="store_true", help="Show stats across all saved reports")
    parser.add_argument("--suite", type=str, default="", help="Filter stats by suite name")
    args = parser.parse_args()

    if args.stats:
        print_stats(suite_filter=args.suite)
    else:
        parser.print_help()
