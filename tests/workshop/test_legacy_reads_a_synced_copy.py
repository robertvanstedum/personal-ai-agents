"""The old Workshop screen reads a synced copy, which has no lock file and is mounted read only.

The portal's mount of ``~/minimoi-staging/data/workshops`` holds only what ``workshop.py sync`` copies: ``events.jsonl`` and
``state.json``. The compatibility layer used to open every journal for writing, which refuses a folder with no lock file, so the
screen would have reported "missing" for a good copy. It now reads such a copy as a replica and still reads a live journal
through its lock."""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from core.workshop_journal.journal import Journal  # noqa: E402
from minimoi_portal.workshop.record import Workshop, load_state  # noqa: E402


def _live(root: Path, count: int = 3) -> Workshop:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    ws = Workshop(str(root), "mac")
    for n in range(count):
        ws.append({"workshop": "mac", "actor": "claude-code", "kind": "progress", "item": "queue:146", "text": f"step {n}"})
    ws.write_state()
    return ws


def _synced_copy(live_dir: Path, copy_root: Path) -> None:
    """What ``workshop.py sync`` leaves behind, with the modes of the portal's mount: events and state only, no lock file."""
    target = copy_root / "mac"
    target.mkdir(mode=0o700, parents=True)
    os.chmod(copy_root, 0o755)
    for name in ("events.jsonl", "state.json"):
        shutil.copy(live_dir / name, target / name)
        os.chmod(target / name, 0o600)


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(path.relative_to(root).as_posix().encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def test_a_synced_copy_with_no_lock_file_is_read_as_a_replica_and_nothing_is_written(tmp_path):
    ws = _live(tmp_path / "live")
    copy = tmp_path / "synced"
    _synced_copy(Path(ws.dir), copy)
    assert not (copy / "mac" / ".lock").exists()
    assert Journal(str(copy), "mac").read(deep=False).status == "unsupported_writer"          # why the old code said "missing"
    before = _tree_hash(copy)
    state, status = load_state(str(copy), "mac")
    assert state is not None and status in ("ok", "stale")
    assert len(Workshop(str(copy), "mac").events()) == 3
    assert _tree_hash(copy) == before and not (copy / "mac" / ".lock").exists()               # a read wrote nothing, made no lock


def test_the_synced_copy_gives_the_same_events_and_state_as_the_live_journal(tmp_path):
    ws = _live(tmp_path / "live", count=5)
    copy = tmp_path / "synced"
    _synced_copy(Path(ws.dir), copy)
    live_state, _ = load_state(str(tmp_path / "live"), "mac")
    copy_state, _ = load_state(str(copy), "mac")
    assert [e["event_id"] for e in Workshop(str(copy), "mac").events()] == [e["event_id"] for e in ws.events()]
    for key in ("events", "needs_you", "in_progress", "items", "next", "last_event"):
        assert copy_state.get(key) == live_state.get(key), key


def test_a_live_journal_is_still_read_through_its_lock(tmp_path):
    ws = _live(tmp_path / "live")
    assert (Path(ws.dir) / ".lock").exists()
    assert ws.reader() is ws.journal
    lockless = Workshop(str(tmp_path / "live"), "mac")
    os.unlink(Path(lockless.dir) / ".lock")
    assert lockless.reader() is not lockless.journal and lockless.reader().replica


def test_a_damaged_synced_copy_is_still_reported_not_hidden(tmp_path):
    ws = _live(tmp_path / "live")
    copy = tmp_path / "synced"
    _synced_copy(Path(ws.dir), copy)
    with open(copy / "mac" / "events.jsonl", "ab") as f:
        f.write(b"this line is not json\n")
    state, status = load_state(str(copy), "mac")
    assert state is None and status == "unreadable"
