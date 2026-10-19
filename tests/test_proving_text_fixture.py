"""Frozen text fixtures for mc_repo_read (scripts/staging/mc_probe/proving_text_fixture.py). Synthetic
text only; no exam material. Git reads use the real RepositoryTools at a commit in this repository."""
import hashlib
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/staging/mc_probe"))
from proving_text_fixture import MAX_BYTES, FixtureTools  # noqa: E402
from proving_tools import RepositoryTools, ToolRefused  # noqa: E402

BASELINE = "b884af11"
TEXT = "# Frozen plan (synthetic)\n\nStep one.\nStep two.\nÜnïcödé line.\n"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture(scope="module")
def repo():
    return RepositoryTools(ROOT, {"baseline": BASELINE})


@pytest.fixture
def plan(tmp_path):
    f = tmp_path / "plan.md"
    f.write_bytes(TEXT.encode())
    return f


def pin(plan, **over):
    spec = {"file": str(plan), "sha256": sha(plan.read_bytes())}
    spec.update(over)
    return {"exam-x1": {"X1_PLAN_FROZEN.md": spec}}


# ── reads ─────────────────────────────────────────────────────────────────────

def test_fixture_read_has_the_git_read_shape_with_window_and_hash(repo, plan):
    tools = FixtureTools(repo, pin(plan))
    out = tools.read_file("exam-x1", "X1_PLAN_FROZEN.md", 2, 3)
    raw = TEXT.encode()
    assert out == {"revision": "exam-x1", "fixture": True, "path": "X1_PLAN_FROZEN.md",
                   "blob": hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest(), "sha256": sha(raw),
                   "start": 2, "total_lines": 5, "lines": ["", "Step one.", "Step two."]}
    assert tools.read_file("exam-x1", "X1_PLAN_FROZEN.md")["lines"][-1] == "Ünïcödé line."


def test_blob_id_matches_git_hash_object(repo, plan):
    import subprocess
    out = FixtureTools(repo, pin(plan)).read_file("exam-x1", "X1_PLAN_FROZEN.md")
    assert out["blob"] == subprocess.run(["git", "hash-object", str(plan)], capture_output=True, text=True).stdout.strip()


@pytest.mark.parametrize("start,count", [(0, 3), (True, 2), (1, 201), (1, 0), (1, -1), ("1", 2), (1, 2.0)])
def test_window_bounds_match_git_reads(repo, plan, start, count):
    with pytest.raises(ToolRefused, match="invalid_line_window"):
        FixtureTools(repo, pin(plan)).read_file("exam-x1", "X1_PLAN_FROZEN.md", start, count)


@pytest.mark.parametrize("path", ["x1_plan_frozen.md", "X1_PLAN_FROZEN.md ", "./X1_PLAN_FROZEN.md", "../plan.md",
                                  "/etc/passwd", None, 7, ["X1_PLAN_FROZEN.md"]])
def test_only_the_exact_pinned_path_label(repo, plan, path):
    with pytest.raises(ToolRefused, match="path_not_allowlisted"):
        FixtureTools(repo, pin(plan)).read_file("exam-x1", path)


# ── delegation is unchanged ───────────────────────────────────────────────────

def test_git_reads_diffs_and_refusals_are_delegated_unchanged(repo, plan):
    tools = FixtureTools(repo, pin(plan))
    path = "minimoi_portal/guild_ui/board_commands.py"
    assert tools.read_file("baseline", path, 1, 5) == repo.read_file("baseline", path, 1, 5)
    assert tools.diff("baseline", "baseline", path) == repo.diff("baseline", "baseline", path)
    for revision, p in [("baseline", "minimoi_portal/auth/users.json"), ("main", path), ("exam-x2", path)]:
        with pytest.raises(ToolRefused):
            tools.read_file(revision, p)


def test_run_suite_is_delegated(plan):
    class Fake:
        revisions = {}

        def run_suite(self, suite):
            return {"suite": suite}
    assert FixtureTools(Fake(), pin(plan)).run_suite("board_commands") == {"suite": "board_commands"}


@pytest.mark.parametrize("before,after", [("exam-x1", "baseline"), ("baseline", "exam-x1"), ("exam-x1", "exam-x1")])
def test_fixtures_cannot_be_diffed(repo, plan, before, after):
    with pytest.raises(ToolRefused, match="fixture_diff_not_supported"):
        FixtureTools(repo, pin(plan)).diff(before, after, "X1_PLAN_FROZEN.md")


# ── pinning is refused at construction ────────────────────────────────────────

def test_hash_mismatch_refused_at_construction(repo, plan):
    with pytest.raises(ToolRefused, match="fixture_hash_mismatch"):
        FixtureTools(repo, pin(plan, sha256="0" * 64))


@pytest.mark.parametrize("over", [{"sha256": "ABC"}, {"sha256": "0" * 63}, {"sha256": None}, {"file": "relative/plan.md"},
                                  {"file": None}, {"extra": 1}])
def test_bad_spec_refused(repo, plan, over):
    with pytest.raises(ToolRefused, match="fixture_spec_invalid"):
        FixtureTools(repo, pin(plan, **over))


@pytest.mark.parametrize("label", ["baseline", "Exam-X1", "b884af11", "deadbeef", "", "x" * 41, "exam x1", 7])
def test_bad_or_colliding_revision_labels_refused(repo, plan, label):
    spec = pin(plan)["exam-x1"]
    with pytest.raises(ToolRefused, match="fixture_revision_label"):
        FixtureTools(repo, {label: spec})


@pytest.mark.parametrize("label", ["../X1.md", "/X1.md", "a//b.md", "a/../b.md", "X1 PLAN.md", ""])
def test_bad_path_labels_refused(repo, plan, label):
    spec = pin(plan)["exam-x1"]["X1_PLAN_FROZEN.md"]
    with pytest.raises(ToolRefused, match="fixture_path_label_invalid"):
        FixtureTools(repo, {"exam-x1": {label: spec}})


def test_symlinked_fixture_refused(repo, plan, tmp_path):
    link = tmp_path / "link.md"
    link.symlink_to(plan)
    with pytest.raises(ToolRefused, match="fixture_path_must_not_traverse_symlinks"):
        FixtureTools(repo, pin(plan, file=str(link)))


def test_symlinked_parent_directory_refused(repo, plan, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ToolRefused, match="fixture_path_must_not_traverse_symlinks"):
        FixtureTools(repo, pin(plan, file=str(alias / "plan.md")))


def test_missing_directory_fifo_and_oversize_refused(repo, tmp_path):
    missing = tmp_path / "missing.md"
    with pytest.raises(ToolRefused, match="fixture_unavailable"):
        FixtureTools(repo, {"exam-x1": {"a.md": {"file": str(missing), "sha256": "0" * 64}}})
    with pytest.raises(ToolRefused, match="fixture_not_a_regular_file"):
        FixtureTools(repo, {"exam-x1": {"a.md": {"file": str(tmp_path), "sha256": "0" * 64}}})
    fifo = tmp_path / "pipe.md"
    os.mkfifo(fifo)
    with pytest.raises(ToolRefused, match="fixture_not_a_regular_file"):     # and does not block
        FixtureTools(repo, {"exam-x1": {"a.md": {"file": str(fifo), "sha256": "0" * 64}}})
    big = tmp_path / "big.md"
    big.write_bytes(b"x" * (MAX_BYTES + 1))
    with pytest.raises(ToolRefused, match="fixture_too_large"):
        FixtureTools(repo, {"exam-x1": {"a.md": {"file": str(big), "sha256": sha(big.read_bytes())}}})


def test_exactly_max_bytes_is_allowed(repo, tmp_path):
    f = tmp_path / "max.md"
    f.write_bytes(b"x" * MAX_BYTES)
    out = FixtureTools(repo, {"exam-x1": {"a.md": {"file": str(f), "sha256": sha(f.read_bytes())}}}).read_file("exam-x1", "a.md")
    assert out["total_lines"] == 1


def test_non_utf8_refused(repo, tmp_path):
    f = tmp_path / "latin1.md"
    f.write_bytes("café".encode("latin-1"))
    with pytest.raises(ToolRefused, match="fixture_not_utf8"):
        FixtureTools(repo, {"exam-x1": {"a.md": {"file": str(f), "sha256": sha(f.read_bytes())}}})


# ── a file changed after pinning is refused, never served ────────────────────

def test_changed_after_pinning_is_refused_on_read(repo, plan):
    tools = FixtureTools(repo, pin(plan))
    plan.write_text(TEXT + "Injected step.\n")
    with pytest.raises(ToolRefused, match="fixture_hash_mismatch"):
        tools.read_file("exam-x1", "X1_PLAN_FROZEN.md")


def test_replaced_by_symlink_after_pinning_is_refused_on_read(repo, plan, tmp_path):
    tools = FixtureTools(repo, pin(plan))
    copy = tmp_path / "copy.md"
    copy.write_bytes(plan.read_bytes())
    plan.unlink()
    plan.symlink_to(copy)                     # same bytes, but now a symlink
    with pytest.raises(ToolRefused, match="fixture_unavailable"):
        tools.read_file("exam-x1", "X1_PLAN_FROZEN.md")


def test_works_behind_the_tool_queue(repo, plan, tmp_path):
    """The queue dispatches mc_repo_read to read_file on whatever tools object it is given."""
    import json
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    try:
        from proving_tool_queue import serve
    except ImportError:
        pytest.skip("proving_tool_queue.py is not on this branch")
    rid = "00000000-0000-4000-8000-000000000001"
    (queue_dir / f"{rid}.request").write_text(json.dumps({"id": rid, "method": "mc_repo_read", "args": {
        "revision": "exam-x1", "path": "X1_PLAN_FROZEN.md", "start": 1, "count": 2}}))
    serve(FixtureTools(repo, pin(plan)), queue_dir, max_requests=1, max_seconds=2)
    answer = json.loads((queue_dir / f"{rid}.response").read_text())
    assert answer["ok"] and answer["result"]["lines"] == ["# Frozen plan (synthetic)", ""]
