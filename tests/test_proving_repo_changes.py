"""mc_repo_changes (RepositoryTools.changes): a pinned commit's subject, parents and changed paths with
status, nothing else. Real Git: commits in this repository, plus throwaway repositories built here."""
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/staging/mc_probe"))
from proving_text_fixture import FixtureTools  # noqa: E402
from proving_tool_queue import serve  # noqa: E402
from proving_tools import MAX_CHANGES, MAX_SUBJECT, RepositoryTools, ToolRefused  # noqa: E402

COMMIT, PARENT = "17899de1fe7b5d38d1975a749f502044e9d71798", "87941f68ec3910766b2714ae6a534e42622b86d6"


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
    return r


def write(repo, path, text="x = 1\n"):
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)


# ── a real commit in this repository ──────────────────────────────────────────

def test_lists_the_exact_changes_of_a_pinned_commit():
    out = RepositoryTools(ROOT, {"x3": "17899de1"}).changes("x3")
    assert out == {"revision": COMMIT, "subject": "Bound workflow-run polling pages to fit response limit",
                   "parents": [{"commit": PARENT, "labels": []}], "compared_with": PARENT, "total_files": 2,
                   "files": [{"status": "modified", "path": "core/github_awareness.py", "readable": True},
                             {"status": "modified", "path": "tests/test_github_awareness_followups.py", "readable": True}],
                   "truncated": False}


def test_parent_labels_tell_mc_which_diff_it_may_ask_for():
    tools = RepositoryTools(ROOT, {"x3": "17899de1", "x3-parent": PARENT[:8]})
    assert tools.changes("x3")["parents"] == [{"commit": PARENT, "labels": ["x3-parent"]}]
    # ...and that diff works with exactly those labels.
    assert tools.diff("x3-parent", "x3", "tests/test_github_awareness_followups.py")["diff"]


@pytest.mark.parametrize("revision", ["17899de1", "17899de1^", "HEAD", "main", "--all", "x3 ", None, 7, ["x3"]])
def test_only_allowlisted_labels(revision):
    with pytest.raises(ToolRefused, match="revision_not_allowlisted"):
        RepositoryTools(ROOT, {"x3": "17899de1"}).changes(revision)


def test_no_file_contents_in_the_answer():
    out = json.dumps(RepositoryTools(ROOT, {"x3": "17899de1"}).changes("x3"))
    assert "RUNS_PAGE" not in out and "per_page" not in out and "def " not in out


# ── statuses, readability and bounds, in throwaway repositories ───────────────

def test_added_modified_deleted_renamed_and_unreadable_paths(repo):
    write(repo, "core/a.py"); write(repo, "core/b.py"); write(repo, "core/gone.py", "y = 2\n" * 20)
    write(repo, "core/old_name.py", "z = 3\n" * 20)
    base = commit(repo, "base")
    write(repo, "core/a.py", "x = 2\n")                          # modified
    write(repo, "core/new.py")                                   # added
    (repo / "core/gone.py").unlink()                             # deleted
    (repo / "core/old_name.py").rename(repo / "core/new_name.py")  # renamed
    write(repo, "data/private.json", "{}")                       # outside the read allowlist
    write(repo, "tests/a b.py")                                  # a name the read tool refuses
    write(repo, "docs/notes.md")                                 # not an allowlisted root
    head = commit(repo, "change everything")
    out = RepositoryTools(repo, {"h": head, "b": base}).changes("h")
    files = {f["path"]: f for f in out["files"]}
    assert files["core/a.py"] == {"status": "modified", "path": "core/a.py", "readable": True}
    assert files["core/new.py"]["status"] == "added"
    assert files["core/gone.py"] == {"status": "deleted", "path": "core/gone.py", "readable": True}
    assert files["core/new_name.py"] == {"status": "renamed", "path": "core/new_name.py", "from": "core/old_name.py", "readable": True}
    assert files["data/private.json"]["readable"] is False and files["tests/a b.py"]["readable"] is False
    assert files["docs/notes.md"]["readable"] is False
    assert out["parents"] == [{"commit": base, "labels": ["b"]}] and out["total_files"] == 7


def test_root_commit_compares_with_the_empty_tree(repo):
    write(repo, "core/a.py")
    root = commit(repo, "root")
    out = RepositoryTools(repo, {"r": root}).changes("r")
    assert out["parents"] == [] and out["compared_with"] == "empty_tree"
    assert out["files"] == [{"status": "added", "path": "core/a.py", "readable": True}]


def test_merge_commit_compares_with_its_first_parent_only(repo):
    write(repo, "core/a.py")
    commit(repo, "root")
    git(repo, "checkout", "-q", "-b", "side")
    write(repo, "core/side.py")
    side = commit(repo, "side")
    git(repo, "checkout", "-q", "-")
    write(repo, "core/main.py")
    first = commit(repo, "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    merge = git(repo, "rev-parse", "HEAD")
    out = RepositoryTools(repo, {"m": merge}).changes("m")
    assert [p["commit"] for p in out["parents"]] == [first, side] and out["compared_with"] == first
    assert [f["path"] for f in out["files"]] == ["core/side.py"]


def test_many_changes_are_capped_and_counted(repo):
    write(repo, "core/keep.py")
    commit(repo, "root")
    for i in range(MAX_CHANGES + 5):
        write(repo, f"core/f{i:03d}.py")
    head = commit(repo, "many")
    out = RepositoryTools(repo, {"h": head}).changes("h")
    assert len(out["files"]) == MAX_CHANGES and out["total_files"] == MAX_CHANGES + 5 and out["truncated"] is True


def test_long_subject_is_capped_and_body_is_not_returned(repo):
    write(repo, "core/a.py")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "S" * (MAX_SUBJECT + 50), "-m", "BODY-SHOULD-NOT-APPEAR")
    head = git(repo, "rev-parse", "HEAD")
    out = RepositoryTools(repo, {"h": head}).changes("h")
    assert out["subject"] == "S" * MAX_SUBJECT and "BODY-SHOULD-NOT-APPEAR" not in json.dumps(out)


def test_newline_in_a_path_is_kept_exact_and_unreadable(repo):
    write(repo, "core/a.py")
    commit(repo, "root")
    write(repo, "core/odd\nname.py")
    head = commit(repo, "odd")
    (entry,) = RepositoryTools(repo, {"h": head}).changes("h")["files"]
    assert entry == {"status": "added", "path": "core/odd\nname.py", "readable": False}


# ── read_file keeps its exact refusals after the allowlist moved into a helper ─

@pytest.mark.parametrize("path,error", [("../../etc/passwd", "path_not_allowlisted"), ("/etc/passwd", "path_not_allowlisted"),
                                        ("domains/cos/data/private.json", "private_or_generated_path"),
                                        ("minimoi_portal/auth/users.json", "private_or_generated_path"),
                                        (".git/config", "path_not_allowlisted"), ("core/x;echo.py", "invalid_path"),
                                        ("core/image.png", "source_text_only"), (None, "invalid_path")])
def test_read_refusals_unchanged(path, error):
    with pytest.raises(ToolRefused, match=f"^{error}$"):
        RepositoryTools(ROOT, {"baseline": "b884af11"}).read_file("baseline", path)


# ── through the queue, the fixture wrapper and the plugin ─────────────────────

def test_through_the_real_queue(tmp_path):
    q = tmp_path / "q"
    q.mkdir()
    ids = ["00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"]
    (q / f"{ids[0]}.request").write_text(json.dumps({"id": ids[0], "method": "mc_repo_changes", "args": {"revision": "x3"}}))
    (q / f"{ids[1]}.request").write_text(json.dumps({"id": ids[1], "method": "mc_repo_changes",
                                                     "args": {"revision": "x3", "path": "core"}}))
    serve(RepositoryTools(ROOT, {"x3": "17899de1"}), q, max_requests=2, max_seconds=3)
    first, second = (json.loads((q / f"{i}.response").read_text()) for i in ids)
    assert first["ok"] and first["result"]["total_files"] == 2
    assert second == {"ok": False, "error": "invalid_arguments"}


def test_fixture_wrapper_delegates_and_refuses_fixture_labels(tmp_path):
    import hashlib
    doc = tmp_path / "plan.md"
    doc.write_text("plan\n")
    tools = FixtureTools(RepositoryTools(ROOT, {"x3": "17899de1"}),
                         {"exam-x1": {"plan.md": {"file": str(doc), "sha256": hashlib.sha256(doc.read_bytes()).hexdigest()}}})
    assert tools.changes("x3")["total_files"] == 2
    with pytest.raises(ToolRefused, match="fixture_has_no_changes"):
        tools.changes("exam-x1")


def test_plugin_declares_exactly_the_four_tools():
    plugin = ROOT / "docker/cos-agent-a/plugins/mc-proving-tools"
    declared = json.loads((plugin / "openclaw.plugin.json").read_text())["contracts"]["tools"]
    registered = re.findall(r"\[\s*'(mc_[a-z_]+)'", (plugin / "index.mjs").read_text())
    assert declared == registered == ["mc_repo_read", "mc_repo_diff", "mc_run_tests", "mc_repo_changes"]
    source = (plugin / "index.mjs").read_text()
    assert "['mc_repo_changes'" in source and "{revision:str},['revision']" in source
