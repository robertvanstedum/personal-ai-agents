"""mc_repo_diff (RepositoryTools.diff): an absent side is empty, so added and deleted files diff in
full; a side that exists must still be an allowed, regular, small, UTF-8 blob. Real Git only."""
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/staging/mc_probe"))
from proving_text_fixture import FixtureTools  # noqa: E402
from proving_tools import RepositoryTools, ToolRefused  # noqa: E402


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, message):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q")
    (r / "core").mkdir()
    (r / "core/keep.py").write_text("keep = 1\n")
    return r


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def test_added_file_diffs_in_full_against_an_empty_side(repo):
    base = commit(repo, "base")
    (repo / "core/new.py").write_text("a = 1\nb = 2\n")
    head = commit(repo, "add")
    out = RepositoryTools(repo, {"p": base, "h": head}).diff("p", "h", "core/new.py")
    assert out["status"] == "added" and out["before_sha256"] is None and out["after_sha256"] == sha("a = 1\nb = 2\n")
    assert out["diff"].startswith("--- /dev/null\n+++ " + head + "/core/new.py\n")
    assert "+a = 1\n+b = 2\n" in out["diff"] and out["truncated"] is False


def test_deleted_file_diffs_in_full_against_an_empty_side(repo):
    (repo / "core/gone.py").write_text("x = 1\n")
    base = commit(repo, "base")
    (repo / "core/gone.py").unlink()
    head = commit(repo, "delete")
    out = RepositoryTools(repo, {"p": base, "h": head}).diff("p", "h", "core/gone.py")
    assert out["status"] == "deleted" and out["after_sha256"] is None and out["before_sha256"] == sha("x = 1\n")
    assert "+++ /dev/null" in out["diff"] and "-x = 1" in out["diff"]


def test_modified_file_keeps_its_previous_shape(repo):
    base = commit(repo, "base")
    (repo / "core/keep.py").write_text("keep = 2\n")
    head = commit(repo, "edit")
    out = RepositoryTools(repo, {"p": base, "h": head}).diff("p", "h", "core/keep.py")
    assert out["status"] == "modified" and out["before_sha256"] == sha("keep = 1\n") and out["after_sha256"] == sha("keep = 2\n")
    assert out["diff"].startswith("--- " + base + "/core/keep.py\n+++ " + head + "/core/keep.py\n")
    assert set(out) == {"before", "after", "path", "status", "before_sha256", "after_sha256", "diff", "truncated", "diff_sha256"}


def test_absent_at_both_revisions_is_refused(repo):
    base = commit(repo, "base")
    with pytest.raises(ToolRefused, match="path_absent_at_both_revisions"):
        RepositoryTools(repo, {"p": base}).diff("p", "p", "core/never.py")


# ── a side that exists must still pass every read check ───────────────────────

def test_symlink_side_is_refused_not_treated_as_absent(repo):
    base = commit(repo, "base")
    (repo / "core/link.py").symlink_to("keep.py")
    head = commit(repo, "link")
    with pytest.raises(ToolRefused, match="regular_source_blob_required"):
        RepositoryTools(repo, {"p": base, "h": head}).diff("p", "h", "core/link.py")


def test_folder_side_is_refused_not_treated_as_absent(repo):
    base = commit(repo, "base")
    (repo / "core/pkg.py").mkdir()
    (repo / "core/pkg.py/inner.py").write_text("x = 1\n")
    head = commit(repo, "folder")
    with pytest.raises(ToolRefused, match="regular_source_blob_required"):
        RepositoryTools(repo, {"p": base, "h": head}).diff("p", "h", "core/pkg.py")


def test_oversized_side_is_refused(repo):
    base = commit(repo, "base")
    (repo / "core/big.py").write_text("x" * 256_001)
    head = commit(repo, "big")
    with pytest.raises(ToolRefused, match="source_file_too_large"):
        RepositoryTools(repo, {"p": base, "h": head}).diff("p", "h", "core/big.py")


def test_non_utf8_side_is_refused(repo):
    base = commit(repo, "base")
    (repo / "core/latin.py").write_bytes("café = 1\n".encode("latin-1"))
    head = commit(repo, "latin")
    with pytest.raises(ToolRefused, match="source_not_utf8"):
        RepositoryTools(repo, {"p": base, "h": head}).diff("p", "h", "core/latin.py")


@pytest.mark.parametrize("path,error", [("core/data/private.py", "private_or_generated_path"), ("data/private.py", "path_not_allowlisted"),
                                        ("docs/notes.md", "path_not_allowlisted"),
                                        ("core/../etc.py", "path_not_allowlisted"),
                                        ("core/image.png", "source_text_only"),
                                        (None, "invalid_path")])
def test_path_policy_applies_even_when_absent(repo, path, error):
    base = commit(repo, "base")
    with pytest.raises(ToolRefused, match=f"^{error}$"):
        RepositoryTools(repo, {"p": base}).diff("p", "p", path)


@pytest.mark.parametrize("before,after", [("p^", "p"), ("p", "HEAD"), ("main", "p"), (None, "p"), (["p"], "p")])
def test_only_allowlisted_labels(repo, before, after):
    base = commit(repo, "base")
    with pytest.raises(ToolRefused, match="revision_not_allowlisted"):
        RepositoryTools(repo, {"p": base}).diff(before, after, "core/keep.py")


def test_large_added_file_diff_is_bounded(repo):
    base = commit(repo, "base")
    (repo / "core/long.py").write_text("".join(f"line_{i} = {i}\n" for i in range(9000)))
    head = commit(repo, "long")
    out = RepositoryTools(repo, {"p": base, "h": head}).diff("p", "h", "core/long.py")
    assert out["truncated"] is True and len(out["diff"].encode()) <= 64000 and out["status"] == "added"


# ── the nested-test-file regression and the fixture wrapper ─────────────

def test_added_nested_test_file_diffs_without_historical_checkout(repo):
    parent = commit(repo, "before parser test")
    path = "tests/guild/shop_floor/test_owner_board_multisentence.py"
    target = repo / path
    target.parent.mkdir(parents=True)
    target.write_text("def test_polite_board_request():\n    assert True\n")
    head = commit(repo, "add parser test")
    tools = RepositoryTools(repo, {"c": head, "c-parent": parent})
    out = tools.diff("c-parent", "c", path)
    assert out["status"] == "added" and out["before_sha256"] is None and out["diff"].startswith("--- /dev/null")
    assert "+def test_polite_board_request():" in out["diff"]


def test_fixture_wrapper_still_refuses_fixture_diffs_and_delegates_others(repo, tmp_path):
    base = commit(repo, "base")
    (repo / "core/new.py").write_text("a = 1\n")
    head = commit(repo, "add")
    doc = tmp_path / "plan.md"
    doc.write_text("plan\n")
    tools = FixtureTools(RepositoryTools(repo, {"p": base, "h": head}),
                         {"exam-x1": {"plan.md": {"file": str(doc), "sha256": sha("plan\n")}}})
    assert tools.diff("p", "h", "core/new.py")["status"] == "added"
    with pytest.raises(ToolRefused, match="fixture_diff_not_supported"):
        tools.diff("exam-x1", "h", "core/new.py")
