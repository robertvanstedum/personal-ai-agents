#!/usr/bin/env python3
"""Regenerate docs/memory_capture_report_samples/ (synthetic report + matrix payloads for each Operate state).

    venv/bin/python scripts/memory/make_report_samples.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tests.memory_shelf import report_samples  # noqa: E402


def main() -> int:
    out = REPO / "docs" / "memory_capture_report_samples"
    out.mkdir(parents=True, exist_ok=True)
    for name, doc in report_samples.make_samples().items():
        (out / f"{name}.report.json").write_text(report_samples.render(doc))
        (out / f"{name}.matrix.json").write_text(report_samples.render(doc["operate"]))
    print(f"wrote {len(report_samples.SCENARIOS)} scenarios to {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
