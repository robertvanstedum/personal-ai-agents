"""Shared synthetic fixtures for the agent-memory copier tests (no real files, ever)."""
from __future__ import annotations

import io
import json
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.agent_memory.config import SourceConfig
from core.agent_memory.sources import DirectorySource, TarSource

NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)


def make_cfg(name: str = "cos-agent-a", **kw) -> SourceConfig:
    base = dict(name=name, agent=name, runtime="openclaw", runtime_version="test",
                source_kind="docker_workspace")
    base.update(kw)
    return SourceConfig(**base)


def make_tar(files: dict[str, bytes], top: str = "workspace") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(top)
        info.type = tarfile.DIRTYPE
        tar.addfile(info)
        for name, data in files.items():
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def write_tree(base: Path, files: dict[str, bytes]) -> None:
    for name, data in files.items():
        target = base / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def write_mac_manifest(base: Path, count: int | None = None, *, complete: bool = True,
                       copied_at: datetime | None = None) -> None:
    if count is None:
        count = sum(1 for p in base.rglob("*") if p.is_file() and p.name != "_copy_manifest.json")
    (base / "_copy_manifest.json").write_text(json.dumps({
        "complete": complete, "file_count": count,
        "copied_at": (copied_at or NOW).strftime("%Y-%m-%dT%H:%M:%SZ")}))


class Box:
    """A mutable synthetic workspace behind a TarSource, so tests can edit it between runs."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = dict(files)
        self.source = TarSource(lambda: make_tar(self.files))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "agent-memory"


@pytest.fixture
def box() -> Box:
    return Box({"MEMORY.md": b"# memory\nalpha\n", "memory/2026-10-01.md": b"day one\n"})


@pytest.fixture
def later():
    return lambda hours=0, minutes=0: NOW + timedelta(hours=hours, minutes=minutes)


def tree(path: Path) -> dict[str, bytes]:
    """Every file under ``path`` as {relative name: bytes}, for scanning a data root."""
    return {str(p.relative_to(path)): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}
