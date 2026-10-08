"""Shared fixtures for the Workshop journal tests (Unit 1). Synthetic data only; every journal lives in a temp folder."""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from core.workshop_journal.journal import Journal  # noqa: E402

WORKSHOP = "workshop-neubau"
REQUEST = {"actor": "claude-code", "kind": "request", "item": "spec:workshop-backend", "topic": "workshop-backend",
           "recipients": ["codex"], "stage": "review", "text": "Review the pinned implementation contract.",
           "payload": {"action": "review", "expected_result": "Numbered findings, or no findings with limits."}}


def envelope(**over) -> dict:
    base = dict(REQUEST)
    base.update(over)
    return base


def progress(text: str = "working", **over) -> dict:
    return envelope(kind="progress", recipients=[], stage="build", item="queue:146", text=text,
                    payload={"action": "implementing unit 1"}, **over)


@pytest.fixture
def root(tmp_path) -> str:
    path = tmp_path / "ws"
    path.mkdir(mode=0o700)
    os.chmod(path, 0o700)
    return str(path)


@pytest.fixture
def journal(root) -> Journal:
    return Journal(root, WORKSHOP, lock_timeout=0.3)


def run_child(code: str, *args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1", **(env_extra or {})}
    return subprocess.run([sys.executable, "-c", code, *args], capture_output=True, text=True, env=env, timeout=120)


def new_id() -> str:
    return str(uuid.uuid4())
