"""Flag a release whose authored notes did not change.

A hash of config/release_notes.json proves the notes belong to the commit.
It does not prove they describe that commit. This check is for a release cut.
It is not wired into deploy, so an ordinary commit on main is not refused
before the notes for that release are written.

    python3 scripts/ci/check_release_notes.py

Exit 0 when this commit changes the notes, or changes nothing else.
Exit 1 when it changes other files and leaves the notes as they were.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

NOTES = "config/release_notes.json"
ROOT = Path(__file__).resolve().parents[2]


def stale(changed_paths: list[str], head_notes: str, parent_notes: str | None) -> str | None:
    """A message when the notes are the parent's and something else changed."""
    if NOTES in changed_paths:
        return None
    if not changed_paths or parent_notes is None:
        return None
    if head_notes == parent_notes:
        return (f"{NOTES} is unchanged in a commit that changes other files. "
                "A hash match only proves these notes are in the commit.")
    return None


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=5)


def main() -> int:
    parent = _git("rev-parse", "--verify", "HEAD^1")
    diff = _git("diff", "--name-only", "HEAD^1", "HEAD", "--")
    if parent.returncode != 0 or diff.returncode != 0:
        print("release notes check skipped: no first parent", file=sys.stderr)
        return 0
    head = _git("show", f"HEAD:{NOTES}")
    previous = _git("show", f"HEAD^1:{NOTES}")
    message = stale(diff.stdout.splitlines(), head.stdout, previous.stdout if previous.returncode == 0 else None)
    if message:
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
