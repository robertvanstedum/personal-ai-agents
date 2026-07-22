#!/usr/bin/env python3
"""
Soft guardrail for docs/specs/ — checks naming convention, byte-identical
duplicates, and declared supersession ("*Replaces: <file>*" header lines).

Informational only: prints GitHub Actions ::warning::/::notice:: annotations
and always exits 0. Does not block the test job, does not file GitHub
issues, does not touch any file. Run from repo root.
"""

import hashlib
import re
import sys
from pathlib import Path

SPECS_DIR = Path(__file__).parent.parent / "docs" / "specs"
NAME_RE = re.compile(r"^(spec|defect|build|design)_.+_\d{4}-\d{2}-\d{2}(?: copy)?\.md$")
REPLACES_RE = re.compile(r"\*?\*?Replaces:?\*?\*?\s*`?([\w.\-]+\.md)`?", re.IGNORECASE)


def main():
    if not SPECS_DIR.is_dir():
        print(f"::warning::docs/specs/ not found at {SPECS_DIR}")
        return 0

    files = sorted(SPECS_DIR.glob("*.md"))
    if not files:
        print("No files in docs/specs/ — nothing to check.")
        return 0

    problems = 0

    # 1. Naming convention
    for f in files:
        if not NAME_RE.match(f.name):
            print(f"::warning file={f}::Filename doesn't match the docs/specs/ "
                  f"convention (spec|defect|build|design)_<slug>_<YYYY-MM-DD>.md: {f.name}")
            problems += 1

    # 2. Byte-identical duplicates
    by_hash = {}
    for f in files:
        h = hashlib.sha256(f.read_bytes()).hexdigest()
        by_hash.setdefault(h, []).append(f.name)
    for h, names in by_hash.items():
        if len(names) > 1:
            print(f"::warning::Byte-identical duplicate content across files: {', '.join(names)}")
            problems += 1

    # 3. Declared supersession — informational, not a problem
    existing = {f.name for f in files}
    for f in files:
        text = f.read_text(errors="ignore")
        m = REPLACES_RE.search(text)
        if m:
            target = m.group(1)
            if target in existing:
                print(f"::notice file={f}::{f.name} declares it replaces {target} "
                      f"(still present in docs/specs/ — consider archiving it)")
            else:
                print(f"::notice file={f}::{f.name} declares it replaces {target} "
                      f"(not found in docs/specs/ — likely already archived, OK)")

    if problems:
        print(f"check_spec_format.py: {problems} issue(s) found (non-blocking).")
    else:
        print(f"check_spec_format.py: {len(files)} files checked, no naming/duplicate issues.")

    return 0  # always non-blocking


if __name__ == "__main__":
    sys.exit(main())
