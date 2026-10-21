"""The public repository must never carry private planning material.

Planning Studio lives in the private repo ``robertvanstedum/minimoi-planning``, nested at
``planning-studio/`` and ignored here. ``.gitignore`` only lowers the risk; this test is the
enforcement (it runs in CI on every push): no tracked file under ``planning-studio/`` and no
nested-repository (gitlink) entry for it.
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GUARDED = ("planning-studio",)


def _ls_files_stage() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "-s"], cwd=REPO, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return out.stdout.splitlines()


def test_no_planning_files_are_tracked_in_the_public_repo():
    tracked = [line.split("\t", 1)[1] for line in _ls_files_stage()]
    leaked = [p for p in tracked if p.split("/", 1)[0] in GUARDED]
    assert leaked == [], f"private planning files are tracked in the public repo: {leaked[:5]}"


def test_no_nested_repository_entry_for_planning():
    gitlinks = [line for line in _ls_files_stage() if line.startswith("160000 ")]
    assert not [g for g in gitlinks if g.split("\t", 1)[1].split("/", 1)[0] in GUARDED]


def test_planning_studio_is_ignored():
    out = subprocess.run(["git", "check-ignore", "-q", "planning-studio/x.md"], cwd=REPO)
    assert out.returncode == 0, "planning-studio/ must stay in .gitignore"
