"""Command-line entrypoint for repeatable public-tour capture."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .runner import CaptureRunner
from .scenario import ScenarioValidationError, load_scenario


PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[2]
SCENARIO_DIR = PACKAGE_DIR / "scenarios"
SCENARIO_SUITES = {
    "domain-baseline": (
        "curator-baseline",
        "german-baseline",
        "portuguese-baseline",
        "guild-baseline",
        "cos-baseline",
    ),
}


def scenario_path(name: str) -> Path:
    return SCENARIO_DIR / f"{name.replace('-', '_')}.json"


def scenario_names(name: str) -> tuple[str, ...]:
    """Expand a named suite, or return a single scenario unchanged."""
    return SCENARIO_SUITES.get(name, (name,))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture repeatable mini-moi tour scenes")
    parser.add_argument(
        "scenario",
        help=(
            "scenario name, for example portuguese-reading, or the "
            "domain-baseline five-domain suite"
        ),
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("MINIMOI_CAPTURE_BASE_URL", ""),
        help="dev/local portal origin (or MINIMOI_CAPTURE_BASE_URL)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPO_ROOT / "_working" / "tour-capture",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="only for scenarios without operator pauses",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate the scenario without opening a browser",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not args.base_url:
            if not args.dry_run:
                raise ScenarioValidationError(
                    "--base-url or MINIMOI_CAPTURE_BASE_URL is required"
                )

        for name in scenario_names(args.scenario):
            scenario = load_scenario(scenario_path(name))
            if args.dry_run:
                summary = scenario["_summary"]
                print(
                    f"valid: {scenario['id']} · {summary['screenshots']} screenshots · "
                    f"{summary['operator_pauses']} operator pauses"
                )
                continue
            review_path = CaptureRunner(
                scenario,
                args.base_url,
                args.output_root,
                headless=args.headless,
            ).run()
            print(f"capture complete: {review_path.resolve()}")
        return 0
    except (ScenarioValidationError, RuntimeError) as exc:
        print(f"capture error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
