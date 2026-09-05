"""Validation and reading happen on the same descriptor, not the same name.

Checking a path and then opening it by name later leaves a window: between the
check and the open, a directory component can be replaced by a symbolic link
pointing anywhere. These tests prove the window is closed — the read walks from
the root's own descriptor with no-follow semantics and re-checks what it
actually opened — and that no layer above can reintroduce the problem by
holding on to an absolute path.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from domains.cos.work.confine import (
    ConfinedFile,
    PathDenied,
    TooLarge,
    UnsupportedMedia,
    confine,
    read_bytes,
    read_text,
    sha256_bytes,
    sha256_confined,
)

PACKAGE = Path(__file__).resolve().parents[2] / "domains" / "cos" / "work"


@pytest.fixture
def root(workspace: Path) -> Path:
    base = workspace / "root"
    (base / "letters").mkdir(parents=True)
    (base / "letters" / "one.md").write_text("first letter\n")
    outside = workspace / "outside"
    outside.mkdir()
    (outside / "one.md").write_text("not yours\n")
    (outside / "secret.md").write_text("not yours either\n")
    return base


def test_confined_file_exposes_no_absolute_path(root: Path):
    """the handle a caller holds is a name under a root, not a path to open"""
    confined = confine(root, "letters/one.md")
    assert confined.relative_path == "letters/one.md"
    assert confined.root == root
    assert not hasattr(confined, "absolute_path")
    assert "absolute_path" not in {field for field in ConfinedFile.__dataclass_fields__}


def test_directory_swapped_for_a_symlink_after_validation_fails_the_read(
    root: Path, workspace: Path
):
    """the classic check-then-open race is refused at read time"""
    confined = confine(root, "letters/one.md")
    assert read_text(confined) == "first letter\n"

    # Between the check above and the read below, the validated directory
    # component becomes a symbolic link pointing outside the root.
    (root / "letters").rename(root / "letters-real")
    (root / "letters").symlink_to(workspace / "outside", target_is_directory=True)

    # A symbolic link opened with O_NOFOLLOW|O_DIRECTORY reports ELOOP on some
    # platforms and ENOTDIR on others; both are refusals, and neither returns
    # the file the link now points at.
    with pytest.raises(PathDenied) as excinfo:
        read_text(confined)
    assert excinfo.value.code == "path_denied"
    assert excinfo.value.relative_path == "letters/one.md"
    assert "not yours" not in excinfo.value.message


def test_file_swapped_for_a_symlink_after_validation_fails_the_read(
    root: Path, workspace: Path
):
    """a swapped final component is refused too"""
    confined = confine(root, "letters/one.md")
    (root / "letters" / "one.md").unlink()
    (root / "letters" / "one.md").symlink_to(workspace / "outside" / "secret.md")

    with pytest.raises(PathDenied):
        read_bytes(confined)
    with pytest.raises(PathDenied):
        sha256_confined(confined)


def test_root_itself_swapped_for_a_symlink_fails_the_read(root: Path, workspace: Path):
    """even the root is opened with no-follow semantics"""
    confined = confine(root, "letters/one.md")
    parent = root.parent
    root.rename(parent / "root-real")
    (parent / "root").symlink_to(workspace / "outside", target_is_directory=True)
    with pytest.raises(PathDenied):
        read_text(confined)


def test_file_replaced_by_a_fifo_after_validation_fails_the_read(root: Path):
    """a non-regular file substituted later is refused by fstat, not by name"""
    confined = confine(root, "letters/one.md")
    (root / "letters" / "one.md").unlink()
    os.mkfifo(root / "letters" / "one.md")
    with pytest.raises(PathDenied) as excinfo:
        read_bytes(confined)
    assert "regular files" in excinfo.value.message


def test_file_grown_past_the_cap_after_validation_fails_the_read(root: Path):
    """the size gate is applied to the descriptor the read opens"""
    confined = confine(root, "letters/one.md", max_bytes=64)
    (root / "letters" / "one.md").write_text("x" * 4096)
    with pytest.raises(TooLarge):
        read_text(confined, max_bytes=64)


def test_read_bytes_and_hash_agree_with_the_content(root: Path):
    """the ordinary path still works, and hashes what it read"""
    confined = confine(root, "letters/one.md")
    raw = read_bytes(confined)
    assert raw == b"first letter\n"
    assert sha256_confined(confined) == sha256_bytes(raw)
    assert read_text(confined) == "first letter\n"


def test_non_utf8_bytes_are_unsupported_media(root: Path):
    """a file that is not text is refused with the contract's name"""
    (root / "letters" / "binary.md").write_bytes(b"\xff\xfe\x00\x01")
    confined = confine(root, "letters/binary.md")
    with pytest.raises(UnsupportedMedia) as excinfo:
        read_text(confined)
    assert excinfo.value.code == "unsupported_media"


def test_retrieval_layer_never_opens_a_path_of_its_own():
    """the layer above confinement has no way to open a file by name"""
    source = (PACKAGE / "retrieval.py").read_text("utf-8")
    assert "absolute_path" not in source
    offences = [
        f"retrieval.py:{number}: {line.strip()}"
        for number, line in enumerate(source.splitlines(), start=1)
        if re.search(r"(?<![\w.])open\s*\(", line) or "os.open" in line
    ]
    assert offences == []


def test_only_the_confinement_module_opens_descriptors():
    """the whole package reaches the filesystem through two places

    Reading is confinement's alone: nothing else in the package opens a file
    to look at its contents. The write side has to open descriptors of its
    own — a directory to fsync it, a temp to write and link — and it does
    that in exactly one module, whose every open is checked below to be a
    directory open or a creation. There is still no second way to read a
    file by name.
    """
    offenders = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        source = path.read_text("utf-8")
        hits = [
            number
            for number, line in enumerate(source.splitlines(), start=1)
            if re.search(r"(?<![\w.])open\s*\(", line) or "os.open" in line
        ]
        if hits:
            offenders[path.name] = hits
    assert set(offenders) <= {"confine.py", "store.py"}


def test_the_write_side_only_opens_directories_and_creations():
    """the write side never opens an existing file to read it"""
    source = (PACKAGE / "store.py").read_text("utf-8")
    lines = source.splitlines()
    calls = []
    for number, line in enumerate(lines):
        if "os.open(" not in line:
            continue
        calls.append("\n".join(lines[number : number + 8]))
    assert calls
    for call in calls:
        assert "_DIR_FLAGS" in call or "O_CREAT" in call, call
