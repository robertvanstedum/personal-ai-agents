"""Command-line entrypoint for repeatable public-tour capture."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess

from .runner import CaptureRunner
from .scenario import ScenarioValidationError, load_scenario


PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[2]
SCENARIO_DIR = PACKAGE_DIR / "scenarios"


def default_output_root() -> Path:
    """Use the primary checkout's shared _working directory from worktrees."""
    configured = os.environ.get("MINIMOI_CAPTURE_OUTPUT_ROOT")
    if configured:
        return Path(configured).expanduser()
    try:
        common_dir = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if common_dir:
            return Path(common_dir).resolve().parent / "_working" / "tour-capture"
    except (OSError, subprocess.CalledProcessError):
        pass
    return REPO_ROOT / "_working" / "tour-capture"


def scenario_path(name: str) -> Path:
    return SCENARIO_DIR / f"{name.replace('-', '_')}.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture repeatable mini-moi tour scenes")
    parser.add_argument("scenario", help="scenario name, for example curator-desktop")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("MINIMOI_CAPTURE_BASE_URL", ""),
        help="dev/local portal origin (or MINIMOI_CAPTURE_BASE_URL)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=default_output_root(),
        help=(
            "capture destination (defaults to the primary checkout's "
            "_working/tour-capture, or MINIMOI_CAPTURE_OUTPUT_ROOT)"
        ),
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
    parser.add_argument(
        "--clean-web",
        action="store_true",
        help=(
            "for external-source captures, block common ad requests and hide "
            "cookie/ad overlays; mini-moi pages are not visually altered"
        ),
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

        scenario = load_scenario(scenario_path(args.scenario))
        if args.dry_run:
            summary = scenario["_summary"]
            print(
                f"valid: {scenario['id']} · {summary['screenshots']} screenshots · "
                f"{summary['operator_pauses']} operator pauses"
            )
            return 0
        review_path = CaptureRunner(
            scenario,
            args.base_url,
            args.output_root,
            headless=args.headless,
            clean_web=args.clean_web,
        ).run()
        print(f"capture complete: {review_path.resolve()}")
        return 0
    except (ScenarioValidationError, RuntimeError) as exc:
        print(f"capture error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
