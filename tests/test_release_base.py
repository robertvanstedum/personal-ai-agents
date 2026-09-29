"""scripts/ci/release_base.sh: the diff base for release classification must
survive a rebase and force-push (#256: "fatal: bad object <before-sha>")."""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts/ci/release_base.sh"
ZERO = "0" * 40


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def _commit(cwd, name):
    (cwd / name).write_text(name)
    _git(cwd, "add", name)
    _git(cwd, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", name)
    return _git(cwd, "rev-parse", "HEAD")


def _base(cwd, before, head="HEAD", base_ref=""):
    r = subprocess.run(["bash", str(SCRIPT), before, head, base_ref], cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr


def _clone(tmp_path):
    """origin with main (a, b) and a PR branch (c on b), then a clone like CI's."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _commit(origin, "a")
    b = _commit(origin, "b")
    _git(origin, "checkout", "-q", "-b", "pr")
    c = _commit(origin, "c")
    _git(origin, "checkout", "-q", "main")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    _git(clone, "checkout", "-q", "pr")
    return origin, clone, b, c


def test_a_reachable_before_is_used(tmp_path):
    _origin, clone, b, _c = _clone(tmp_path)
    assert _base(clone, b)[:2] == (0, b)


def test_an_unreachable_before_after_a_force_push_falls_back_to_the_merge_base(tmp_path):
    _origin, clone, b, _c = _clone(tmp_path)
    gone = "1234567890abcdef1234567890abcdef12345678"          # a pre-rebase head this clone never had
    code, out, err = _base(clone, gone, "HEAD", "main")
    assert (code, out) == (0, b) and "not in this clone" in err and "merge-base with origin/main" in err


def test_an_empty_or_zero_before_falls_back_too(tmp_path):
    _origin, clone, b, _c = _clone(tmp_path)
    assert _base(clone, "")[:2] == (0, b)
    assert _base(clone, ZERO, "HEAD", "main")[:2] == (0, b)


def test_a_push_to_main_with_an_empty_before_uses_head_parent(tmp_path):
    _origin, clone, _b, _c = _clone(tmp_path)
    _git(clone, "checkout", "-q", "main")
    a = _git(clone, "rev-parse", "HEAD^")
    code, out, err = _base(clone, "")
    assert (code, out) == (0, a) and "HEAD^" in err


def test_a_push_to_main_with_a_missing_before_is_a_full_release(tmp_path):
    """Main's history was rewritten: HEAD^ could hide changed services (PR #260 review)."""
    _origin, clone, _b, _c = _clone(tmp_path)
    _git(clone, "checkout", "-q", "main")
    code, out, err = _base(clone, "1234567890abcdef1234567890abcdef12345678")
    assert (code, out) == (3, "") and "full release" in err


def test_the_workflow_uses_the_script_and_never_diffs_the_raw_before():
    text = (REPO / ".github/workflows/deploy.yml").read_text()
    assert 'BEFORE=$(bash scripts/ci/release_base.sh "${{ github.event.before }}" "${{ github.sha }}" "${{ github.base_ref }}") || rc=$?' in text
    assert 'if [ "$rc" = 3 ]; then\n            echo "release_class=full"' in text
    assert '[ "$rc" = 0 ] || exit "$rc"' in text
    assert 'BEFORE="${{ github.event.before }}"' not in text
    assert 'git diff --name-only "$BEFORE" "${{ github.sha }}"' in text


def test_the_script_itself_redeploys_nothing():
    import sys
    sys.path.insert(0, str(REPO / "scripts/ci"))
    from classify_release import classify
    assert classify(["scripts/ci/release_base.sh"]) == ("documents", ())
