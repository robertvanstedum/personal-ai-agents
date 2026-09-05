"""Shared fixtures for the accumulation-reference tests.

Everything here works on copies in a temporary directory. Committed fixture
trees cannot carry owner-private directory modes through Git, and the roots
under test must be owner-private, so each test copies what it needs and sets
the modes itself.

All fixture content is synthetic: invented people, employers and postings.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "work"


def make_private(path: Path) -> Path:
    """Make ``path`` and everything under it owner-private."""
    os.chmod(path, 0o700)
    for child in path.rglob("*"):
        if child.is_symlink():
            continue
        os.chmod(child, 0o700 if child.is_dir() else 0o600)
    return path


def copy_private(source: Path, destination: Path) -> Path:
    """Copy a fixture tree and make the copy owner-private."""
    shutil.copytree(source, destination, symlinks=False)
    return make_private(destination)


@pytest.fixture
def fixture_root() -> Path:
    """The committed, synthetic fixture tree (read only)."""
    return FIXTURE_ROOT


@pytest.fixture
def career_sources(tmp_path: Path) -> Path:
    """An owner-private copy of the synthetic Career source tree."""
    return copy_private(FIXTURE_ROOT / "sources" / "career", tmp_path / "career-sources")


@pytest.fixture
def decision_memo_sources(tmp_path: Path) -> Path:
    """An owner-private copy of the synthetic decision-memo source tree."""
    return copy_private(
        FIXTURE_ROOT / "sources" / "decision_memo", tmp_path / "decision-memo-sources"
    )


@pytest.fixture
def private_work_root(tmp_path: Path) -> Path:
    """An owner-private copy of the synthetic canonical work tree."""
    return copy_private(FIXTURE_ROOT / "work_root", tmp_path / "work-root")


@pytest.fixture
def source_roots_env():
    """Build the deployment declaration for authorized read-only roots."""

    def build(mapping: dict[str, dict[str, Path | str]]) -> str:
        return json.dumps(
            {
                subject: {ref: str(path) for ref, path in refs.items()}
                for subject, refs in mapping.items()
            }
        )

    return build


@pytest.fixture
def temp_git_repo(tmp_path: Path):
    """Create a throwaway Git checkout so in-checkout root rules are testable."""

    def build(name: str = "checkout") -> Path:
        repo = tmp_path / name
        repo.mkdir()
        make_private(repo)
        subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "fixture@example.invalid"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Fixture"], cwd=repo, check=True)
        return repo

    return build
