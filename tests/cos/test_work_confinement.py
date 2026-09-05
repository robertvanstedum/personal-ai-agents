"""Path confinement and file gates.

Checkpoint A: the full test list, not yet implemented.
"""
import pytest

def test_confine_accepts_plain_relative_path():
    """an ordinary relative path resolves"""
    pytest.skip("checkpoint A")

def test_confine_rejects_parent_traversal():
    """'..' is refused before any read"""
    pytest.skip("checkpoint A")

def test_confine_rejects_absolute_path():
    """a caller-supplied absolute path is refused"""
    pytest.skip("checkpoint A")

def test_confine_rejects_nul_byte():
    """a NUL byte is refused"""
    pytest.skip("checkpoint A")

def test_confine_rejects_symlink_final_component():
    """a symlinked file is refused"""
    pytest.skip("checkpoint A")

def test_confine_rejects_symlink_directory_component():
    """a symlinked directory component is refused"""
    pytest.skip("checkpoint A")

def test_confine_rejects_symlink_pointing_inside_root():
    """a symlink is refused even when it points inside the root"""
    pytest.skip("checkpoint A")

def test_confine_rejects_non_regular_file():
    """a FIFO or device is refused"""
    pytest.skip("checkpoint A")

def test_confine_rejects_unsupported_extension():
    """anything but .md and .txt is refused"""
    pytest.skip("checkpoint A")

def test_confine_rejects_oversized_file():
    """a file above the cap is refused"""
    pytest.skip("checkpoint A")

def test_confine_rejects_escape_after_resolution():
    """a path that escapes after resolution is refused"""
    pytest.skip("checkpoint A")

def test_confine_rejects_missing_file_as_not_found():
    """a missing file is not_found"""
    pytest.skip("checkpoint A")

def test_confine_error_messages_carry_no_absolute_path():
    """errors expose only the relative path"""
    pytest.skip("checkpoint A")

def test_iter_files_skips_git_metadata():
    """version-control metadata is never walked"""
    pytest.skip("checkpoint A")

def test_iter_files_skips_symlinks_and_unsupported():
    """the readable surface excludes links and other types"""
    pytest.skip("checkpoint A")

def test_iter_files_is_deterministic():
    """two walks return the same order"""
    pytest.skip("checkpoint A")

def test_sha256_is_over_raw_bytes():
    """hashing applies no normalisation"""
    pytest.skip("checkpoint A")

