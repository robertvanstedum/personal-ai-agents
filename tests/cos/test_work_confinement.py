"""Path confinement and file gates."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from domains.cos.work.confine import (
    ALLOWED_EXTENSIONS,
    NotFound,
    PathRejected,
    TooLarge,
    UnsupportedFile,
    confine,
    iter_files,
    sha256_bytes,
    sha256_file,
)


@pytest.fixture
def root(workspace: Path) -> Path:
    """A small readable tree with a few traps in it."""
    base = workspace / "root"
    (base / "letters").mkdir(parents=True)
    (base / "letters" / "one.md").write_text("first letter\n")
    (base / "notes.txt").write_text("a note\n")
    (base / "picture.png").write_bytes(b"\x89PNG not really")
    outside = workspace / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("not yours\n")
    return base


def test_confine_accepts_plain_relative_path(root: Path):
    """an ordinary relative path resolves"""
    confined = confine(root, "letters/one.md")
    assert confined.relative_path == "letters/one.md"
    assert confined.absolute_path == root / "letters" / "one.md"
    assert confined.size == len("first letter\n")


def test_confine_rejects_parent_traversal(root: Path):
    """'..' is refused before any read"""
    for candidate in ["../outside/secret.md", "letters/../../outside/secret.md", ".."]:
        with pytest.raises(PathRejected) as excinfo:
            confine(root, candidate)
        assert excinfo.value.code == "path_rejected"


def test_confine_rejects_absolute_path(root: Path, workspace: Path):
    """a caller-supplied absolute path is refused"""
    with pytest.raises(PathRejected):
        confine(root, str(workspace / "outside" / "secret.md"))
    with pytest.raises(PathRejected):
        confine(root, "/etc/hosts")


def test_confine_rejects_nul_byte(root: Path):
    """a NUL byte is refused"""
    with pytest.raises(PathRejected):
        confine(root, "letters/one.md\x00.txt")


def test_confine_rejects_symlink_final_component(root: Path, workspace: Path):
    """a symlinked file is refused"""
    (root / "shortcut.md").symlink_to(workspace / "outside" / "secret.md")
    with pytest.raises(PathRejected) as excinfo:
        confine(root, "shortcut.md")
    assert "symbolic link" in excinfo.value.message


def test_confine_rejects_symlink_directory_component(root: Path, workspace: Path):
    """a symlinked directory component is refused"""
    (root / "elsewhere").symlink_to(workspace / "outside", target_is_directory=True)
    with pytest.raises(PathRejected):
        confine(root, "elsewhere/secret.md")


def test_confine_rejects_symlink_pointing_inside_root(root: Path):
    """a symlink is refused even when it points inside the root

    Resolution alone is not enough: a link that points inside the root today
    can be repointed between one call and the next.
    """
    (root / "alias.md").symlink_to(root / "letters" / "one.md")
    with pytest.raises(PathRejected) as excinfo:
        confine(root, "alias.md")
    assert "symbolic link" in excinfo.value.message


def test_confine_rejects_non_regular_file(root: Path):
    """a FIFO or device is refused"""
    fifo = root / "pipe.txt"
    os.mkfifo(fifo)
    with pytest.raises(PathRejected) as excinfo:
        confine(root, "pipe.txt")
    assert "regular files" in excinfo.value.message


def test_confine_rejects_unsupported_extension(root: Path):
    """anything but .md and .txt is refused"""
    assert ALLOWED_EXTENSIONS == frozenset({".md", ".txt"})
    with pytest.raises(UnsupportedFile) as excinfo:
        confine(root, "picture.png")
    assert excinfo.value.code == "unsupported_file"


def test_confine_rejects_oversized_file(root: Path):
    """a file above the cap is refused"""
    big = root / "big.md"
    big.write_text("x" * 4096)
    with pytest.raises(TooLarge) as excinfo:
        confine(root, "big.md", max_bytes=1024)
    assert excinfo.value.code == "too_large"
    assert confine(root, "big.md", max_bytes=8192).size == 4096


def test_confine_rejects_escape_after_resolution(root: Path, workspace: Path):
    """a path that escapes after resolution is refused"""
    escape = root / "letters" / "up"
    escape.symlink_to(workspace / "outside", target_is_directory=True)
    with pytest.raises(PathRejected):
        confine(root, "letters/up/secret.md")


def test_confine_rejects_missing_file_as_not_found(root: Path):
    """a missing file is not_found"""
    with pytest.raises(NotFound) as excinfo:
        confine(root, "letters/absent.md")
    assert excinfo.value.code == "not_found"
    assert excinfo.value.relative_path == "letters/absent.md"


def test_confine_error_messages_carry_no_absolute_path(root: Path, workspace: Path):
    """errors expose only the relative path"""
    (root / "shortcut.md").symlink_to(workspace / "outside" / "secret.md")
    failures = []
    for candidate in ["letters/absent.md", "shortcut.md", "picture.png", "../outside/secret.md"]:
        try:
            confine(root, candidate)
        except Exception as exc:  # noqa: BLE001 - the point is to inspect every one
            failures.append(exc)
    assert len(failures) == 4
    for exc in failures:
        rendered = str(exc.to_error())
        assert str(workspace) not in rendered
        assert str(root) not in rendered
        assert "/private" not in rendered


def test_iter_files_skips_git_metadata(root: Path):
    """version-control metadata is never walked"""
    (root / ".git").mkdir()
    (root / ".git" / "config.txt").write_text("private history\n")
    walked = [item.relative_path for item in iter_files(root)]
    assert walked
    assert not any(path.startswith(".git") for path in walked)


def test_iter_files_skips_symlinks_and_unsupported(root: Path, workspace: Path):
    """the readable surface excludes links and other types"""
    (root / "shortcut.md").symlink_to(workspace / "outside" / "secret.md")
    (root / "elsewhere").symlink_to(workspace / "outside", target_is_directory=True)
    os.mkfifo(root / "pipe.txt")
    (root / "huge.md").write_text("y" * 2048)

    walked = [item.relative_path for item in iter_files(root, max_bytes=1024)]
    assert walked == ["notes.txt", "letters/one.md"]


def test_iter_files_is_deterministic(root: Path):
    """two walks return the same order"""
    (root / "letters" / "two.md").write_text("second letter\n")
    (root / "letters" / "alpha.txt").write_text("alpha\n")
    first = [item.relative_path for item in iter_files(root)]
    second = [item.relative_path for item in iter_files(root)]
    assert first == second
    assert first == ["notes.txt", "letters/alpha.txt", "letters/one.md", "letters/two.md"]


def test_sha256_is_over_raw_bytes(root: Path):
    """hashing applies no normalisation"""
    unix = root / "unix.md"
    windows = root / "windows.md"
    unix.write_bytes(b"line\n")
    windows.write_bytes(b"line\r\n")
    assert sha256_file(unix) != sha256_file(windows)
    assert sha256_file(unix) == sha256_bytes(b"line\n")
    assert sha256_bytes(b"") == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
