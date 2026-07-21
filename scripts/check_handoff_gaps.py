#!/usr/bin/env python3
"""
scripts/check_handoff_gaps.py — Handoff gap-check v0.1

Scans docs/ and recent _working/ plan files for references to
_working/*.md files and reports any that don't exist on disk.

Catches the failure mode where a spec or handoff is written in
Claude.ai's output and referenced in docs or plans but never
copied to _working/ — causing the next build to stall.

Usage:
    venv/bin/python3 scripts/check_handoff_gaps.py

Exit code: 0 if no gaps, 1 if gaps found.
"""

import re
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Files to scan for _working/ references
# GUILD.md and GUILD_BUILD_LOG.md moved to docs/archive/2026-07/ on 2026-07-21
# (superseded by ARCHITECTURE.md's Guild section) — pointing here so this
# check still runs against the same content rather than silently skipping it.
SCAN_DOCS = [
    "docs/archive/2026-07/GUILD.md",
    "docs/archive/2026-07/GUILD_BUILD_LOG.md",
]

# Also scan any plan_*.md or handoff_*.md already in _working/
# (plans often reference the specs they describe)
def _working_plans() -> list[Path]:
    w = ROOT / "_working"
    if not w.exists():
        return []
    return sorted(
        list(w.glob("plan_*.md")) +
        list(w.glob("handoff_*.md")) +
        list(w.glob("spec_*.md"))
    )

# Pattern: any _working/something.md reference
PATTERN = re.compile(r'`?_working/([\w\-\.]+\.md)`?')


def main():
    scan_targets: list[Path] = []

    for rel in SCAN_DOCS:
        p = ROOT / rel
        if p.exists():
            scan_targets.append(p)
        else:
            print(f"  (skipping {rel} — not found)")

    scan_targets.extend(_working_plans())

    referenced: dict[str, list[str]] = {}  # filename → list of source files
    for src in scan_targets:
        try:
            content = src.read_text(encoding="utf-8")
        except Exception:
            continue
        for match in PATTERN.findall(content):
            key = f"_working/{match}"
            referenced.setdefault(key, []).append(src.name)

    if not referenced:
        print("✅  No _working/ file references found in scanned docs.")
        sys.exit(0)

    missing = []
    present = []
    for ref in sorted(referenced):
        full = ROOT / ref
        if full.exists():
            present.append(ref)
        else:
            missing.append((ref, referenced[ref]))

    print(f"\n══ Handoff Gap-Check ══════════════════════════════════════\n")
    print(f"  Scanned {len(scan_targets)} file(s), found {len(referenced)} _working/ reference(s).\n")

    if present:
        print(f"  ✅  Present ({len(present)}):")
        for p in present:
            print(f"       {p}")

    if missing:
        print(f"\n  ❌  Missing ({len(missing)}) — these are referenced but don't exist:\n")
        for ref, sources in missing:
            print(f"       {ref}")
            print(f"         referenced in: {', '.join(sources)}")
        print(f"\n  Action: copy or commit the missing file(s) to _working/\n")
        sys.exit(1)
    else:
        print(f"\n  ✅  No gaps found — all referenced files are present.\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
