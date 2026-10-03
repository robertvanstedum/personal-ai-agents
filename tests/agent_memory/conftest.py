"""Fixtures for the agent-memory copier tests."""
from __future__ import annotations

import sys
from collections import namedtuple
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from agent_memory_helpers import NOW, Box  # noqa: E402


@pytest.fixture(autouse=True)
def roomy_disk(monkeypatch):
    """Hermetic: pretend the disk has plenty of room unless a test says otherwise."""
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr("core.agent_memory.headroom.shutil.disk_usage", lambda p: usage(10**12, 10**11, 9 * 10**11))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "agent-memory"


@pytest.fixture
def box() -> Box:
    return Box({"MEMORY.md": b"# memory\nalpha\n", "memory/2026-10-01.md": b"day one\n"})


@pytest.fixture
def later():
    return lambda hours=0, minutes=0: NOW + timedelta(hours=hours, minutes=minutes)
