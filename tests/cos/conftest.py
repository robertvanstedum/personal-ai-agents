"""Shared fixtures for the accumulation-reference tests.

Everything here works on copies in a temporary directory. Committed fixture
trees cannot carry owner-private directory modes through Git, and the roots
under test must be owner-private, so each test copies what it needs and sets
the modes itself.

Temporary paths are canonicalised first: on macOS the system temporary
directory reaches through ``/var -> /private/var``, and a root with a
symbolic link at any component is refused by design.

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
def workspace(tmp_path: Path) -> Path:
    """A canonical, symlink-free temporary directory."""
    resolved = Path(os.path.realpath(tmp_path))
    make_private(resolved)
    return resolved


@pytest.fixture
def fixture_root() -> Path:
    """The committed, synthetic fixture tree (read only)."""
    return FIXTURE_ROOT


@pytest.fixture
def career_sources(workspace: Path) -> Path:
    """An owner-private copy of the synthetic Career source tree."""
    return copy_private(FIXTURE_ROOT / "sources" / "career", workspace / "career-sources")


@pytest.fixture
def decision_memo_sources(workspace: Path) -> Path:
    """An owner-private copy of the synthetic decision-memo source tree."""
    return copy_private(
        FIXTURE_ROOT / "sources" / "decision_memo", workspace / "decision-memo-sources"
    )


@pytest.fixture
def private_work_root(workspace: Path) -> Path:
    """An owner-private copy of the synthetic canonical work tree."""
    return copy_private(FIXTURE_ROOT / "work_root", workspace / "work-root")


def declaration(mapping: dict, default_class: str = "robert_source") -> str:
    """Render the deployment declaration for authorized read-only roots.

    Every root carries an explicit provenance class. A value may be given as a
    plain path — which takes ``default_class`` — or as a ``(path, class)``
    pair, so a test can declare a root of any of the four classes.
    """
    document: dict[str, dict[str, dict[str, str]]] = {}
    for subject, refs in mapping.items():
        entries: dict[str, dict[str, str]] = {}
        for ref, value in refs.items():
            if isinstance(value, tuple):
                path, context_class = value
            else:
                path, context_class = value, default_class
            entries[ref] = {"path": str(path), "context_class": context_class}
        document[subject] = entries
    return json.dumps(document)


@pytest.fixture
def declare_source_roots():
    """Build the deployment declaration for authorized read-only roots."""
    return declaration


@pytest.fixture
def career_env(private_work_root: Path, career_sources: Path, declare_source_roots):
    """A complete, valid environment for the synthetic Career subject."""
    from domains.cos.work.roots import ENV_SOURCE_ROOTS, ENV_WORK_ROOT

    return {
        ENV_WORK_ROOT: str(private_work_root),
        ENV_SOURCE_ROOTS: declare_source_roots(
            {
                "career": {
                    "resumes": career_sources / "resumes",
                    "other-responses": career_sources / "other-responses",
                    "base-letters": career_sources / "base-letters",
                }
            }
        ),
    }


@pytest.fixture
def career_accumulation(career_env):
    """The accumulation reference, configured for the Career fixtures."""
    from domains.cos.work.retrieval import Accumulation
    from domains.cos.work.roots import load_root_configuration

    configuration = load_root_configuration(career_env)
    assert configuration.issues == ()
    return Accumulation(configuration)


@pytest.fixture
def temp_git_repo(workspace: Path):
    """Create a throwaway Git checkout so in-checkout root rules are testable."""

    created: dict[str, Path] = {}

    def build(name: str = "checkout") -> Path:
        if name in created:
            return created[name]
        repo = workspace / name
        repo.mkdir()
        make_private(repo)
        subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "fixture@example.invalid"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Fixture"], cwd=repo, check=True)
        created[name] = repo
        return repo

    return build
