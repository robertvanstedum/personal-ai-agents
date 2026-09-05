"""Root configuration and fail-closed validation."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from domains.cos.work import roots
from domains.cos.work.roots import (
    ENV_SOURCE_ROOTS,
    ENV_SOURCE_ROOTS_FILE,
    ENV_WORK_ROOT,
    RootUnavailable,
    SourceRootUnavailable,
    is_narrowing,
    load_root_configuration,
    narrow,
)


def private_dir(parent: Path, name: str) -> Path:
    path = parent / name
    path.mkdir(parents=True)
    os.chmod(path, 0o700)
    return path


def declare(mapping: dict, default_class: str = "robert_source") -> str:
    """Render a deployment declaration; every root carries a provenance class.

    A value may be a plain path — which takes ``default_class`` — or a
    ``(path, context_class)`` pair, so a test can declare any of the four
    classes without a second helper.
    """
    document: dict[str, dict[str, dict[str, str]]] = {}
    for subject, refs in mapping.items():
        entries: dict[str, dict[str, str]] = {}
        for ref, value in refs.items():
            path, context_class = value if isinstance(value, tuple) else (value, default_class)
            entries[ref] = {"path": str(path), "context_class": context_class}
        document[subject] = entries
    return json.dumps(document)


# -- the canonical write root ------------------------------------------------


def test_work_root_valid_outside_checkout_loads(workspace: Path, temp_git_repo):
    """a valid owner-private root outside the checkout loads"""
    checkout = temp_git_repo()
    root = private_dir(workspace, "work-area")
    config = load_root_configuration({ENV_WORK_ROOT: str(root)}, checkout_root=checkout)
    assert config.work_available is True
    assert config.require_work_root() == root
    assert config.work_root_issue is None


def test_work_root_missing_env_is_unavailable(workspace: Path, temp_git_repo):
    """no COS_WORK_ROOT set -> work_root_unavailable"""
    config = load_root_configuration({}, checkout_root=temp_git_repo())
    assert config.work_available is False
    assert config.work_root_issue is not None
    assert config.work_root_issue.code == "work_root_unavailable"
    with pytest.raises(RootUnavailable) as excinfo:
        config.require_work_root()
    assert excinfo.value.code == "work_root_unavailable"


def test_work_root_relative_path_is_unavailable(workspace: Path, temp_git_repo):
    """a relative path is refused"""
    config = load_root_configuration({ENV_WORK_ROOT: "relative/work"}, checkout_root=temp_git_repo())
    assert config.work_available is False
    assert "absolute" in config.work_root_issue.reason


def test_work_root_nonexistent_is_unavailable(workspace: Path, temp_git_repo):
    """a missing directory is refused"""
    config = load_root_configuration(
        {ENV_WORK_ROOT: str(workspace / "nowhere")}, checkout_root=temp_git_repo()
    )
    assert config.work_available is False
    assert "does not exist" in config.work_root_issue.reason


def test_work_root_file_not_directory_is_unavailable(workspace: Path, temp_git_repo):
    """a regular file is refused"""
    target = workspace / "work-area.txt"
    target.write_text("not a directory")
    os.chmod(target, 0o600)
    config = load_root_configuration({ENV_WORK_ROOT: str(target)}, checkout_root=temp_git_repo())
    assert config.work_available is False
    assert "not a directory" in config.work_root_issue.reason


def test_work_root_symlinked_component_is_unavailable(workspace: Path, temp_git_repo):
    """a symlink at any component is refused"""
    real = private_dir(workspace, "real-work-area")
    link = workspace / "linked-work-area"
    link.symlink_to(real, target_is_directory=True)
    config = load_root_configuration({ENV_WORK_ROOT: str(link)}, checkout_root=temp_git_repo())
    assert config.work_available is False
    assert "symbolic link" in config.work_root_issue.reason

    nested = private_dir(real, "inner")
    through_link = link / "inner"
    assert nested.is_dir()
    config = load_root_configuration(
        {ENV_WORK_ROOT: str(through_link)}, checkout_root=temp_git_repo()
    )
    assert config.work_available is False


@pytest.mark.parametrize("mode", [0o750, 0o755, 0o707])
def test_work_root_group_or_world_readable_is_unavailable(workspace: Path, temp_git_repo, mode):
    """0750 and 0755 roots are refused"""
    root = private_dir(workspace, f"open-{mode:o}")
    os.chmod(root, mode)
    config = load_root_configuration({ENV_WORK_ROOT: str(root)}, checkout_root=temp_git_repo())
    assert config.work_available is False
    assert "owner-private" in config.work_root_issue.reason


def test_work_root_inside_checkout_is_unavailable(workspace: Path, temp_git_repo):
    """a root equal to or under the checkout is refused"""
    checkout = temp_git_repo()
    inside = private_dir(checkout, "work-area")

    config = load_root_configuration({ENV_WORK_ROOT: str(inside)}, checkout_root=checkout)
    assert config.work_available is False
    assert "outside the repository checkout" in config.work_root_issue.reason

    config = load_root_configuration({ENV_WORK_ROOT: str(checkout)}, checkout_root=checkout)
    assert config.work_available is False


def test_work_root_failure_leaves_source_roots_usable(
    workspace: Path, career_sources: Path, temp_git_repo
):
    """Work is unavailable but reading configured sources still works"""
    config = load_root_configuration(
        {ENV_SOURCE_ROOTS: declare({"career": {"resumes": career_sources / "resumes"}})},
        checkout_root=temp_git_repo(),
    )
    assert config.work_available is False
    assert config.root_refs("career") == ("resumes",)
    assert config.resolve("career", "resumes").path == career_sources / "resumes"


# -- declaring source roots --------------------------------------------------


def test_source_root_declaration_parsed_from_inline_env(career_sources: Path, temp_git_repo):
    """COS_WORK_SOURCE_ROOTS JSON is read"""
    config = load_root_configuration(
        {
            ENV_SOURCE_ROOTS: declare(
                {
                    "career": {
                        "resumes": career_sources / "resumes",
                        "base-letters": career_sources / "base-letters",
                    }
                }
            )
        },
        checkout_root=temp_git_repo(),
    )
    assert config.source_root_issues == ()
    assert config.root_refs("career") == ("resumes", "base-letters")


def test_source_root_declaration_parsed_from_file_env(
    workspace: Path, career_sources: Path, temp_git_repo
):
    """COS_WORK_SOURCE_ROOTS_FILE is read"""
    declaration = workspace / "source-roots.json"
    declaration.write_text(declare({"career": {"resumes": career_sources / "resumes"}}))
    config = load_root_configuration(
        {ENV_SOURCE_ROOTS_FILE: str(declaration)}, checkout_root=temp_git_repo()
    )
    assert config.root_refs("career") == ("resumes",)


def test_source_root_inline_env_wins_over_file(
    workspace: Path, career_sources: Path, temp_git_repo
):
    """inline declaration takes precedence"""
    declaration = workspace / "source-roots.json"
    declaration.write_text(declare({"career": {"from-file": career_sources / "resumes"}}))
    config = load_root_configuration(
        {
            ENV_SOURCE_ROOTS: declare({"career": {"inline": career_sources / "base-letters"}}),
            ENV_SOURCE_ROOTS_FILE: str(declaration),
        },
        checkout_root=temp_git_repo(),
    )
    assert config.root_refs("career") == ("inline",)


def test_source_root_malformed_json_yields_issue_not_crash(temp_git_repo):
    """bad JSON drops every root and reports it"""
    config = load_root_configuration({ENV_SOURCE_ROOTS: "{not json"}, checkout_root=temp_git_repo())
    assert config.source_roots == {}
    assert any(issue.code == "source_root_unavailable" for issue in config.issues)


def test_source_root_relative_path_dropped_and_reported(temp_git_repo):
    """a relative source root is dropped fail-closed"""
    config = load_root_configuration(
        {ENV_SOURCE_ROOTS: declare({"career": {"resumes": "relative/resumes"}})},
        checkout_root=temp_git_repo(),
    )
    assert config.root_refs("career") == ()
    assert config.source_root_issues[0].ref == "resumes"
    assert "absolute" in config.source_root_issues[0].reason


def test_source_root_nonexistent_dropped_and_reported(workspace: Path, temp_git_repo):
    """a missing source root is dropped fail-closed"""
    config = load_root_configuration(
        {ENV_SOURCE_ROOTS: declare({"career": {"resumes": workspace / "nowhere"}})},
        checkout_root=temp_git_repo(),
    )
    assert config.root_refs("career") == ()
    assert "does not exist" in config.source_root_issues[0].reason


def test_source_root_symlink_component_dropped_and_reported(workspace: Path, temp_git_repo):
    """a symlinked source root is dropped fail-closed"""
    real = private_dir(workspace, "real-sources")
    link = workspace / "linked-sources"
    link.symlink_to(real, target_is_directory=True)
    config = load_root_configuration(
        {ENV_SOURCE_ROOTS: declare({"career": {"resumes": link}})}, checkout_root=temp_git_repo()
    )
    assert config.root_refs("career") == ()
    assert "symbolic link" in config.source_root_issues[0].reason


def test_source_root_group_readable_dropped_and_reported(workspace: Path, temp_git_repo):
    """a non-owner-private source root is dropped"""
    root = private_dir(workspace, "shared-sources")
    os.chmod(root, 0o755)
    config = load_root_configuration(
        {ENV_SOURCE_ROOTS: declare({"career": {"resumes": root}})}, checkout_root=temp_git_repo()
    )
    assert config.root_refs("career") == ()
    assert "owner-private" in config.source_root_issues[0].reason


# -- source roots inside the enclosing public repository ---------------------


def test_source_root_inside_checkout_accepted_when_ignored_and_untracked(temp_git_repo):
    """in-checkout root allowed only when ignored and untracked"""
    checkout = temp_git_repo()
    (checkout / ".gitignore").write_text("private-sources/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=checkout, check=True)
    subprocess.run(["git", "commit", "-qm", "ignore"], cwd=checkout, check=True)
    inside = private_dir(checkout, "private-sources")
    (inside / "note.md").write_text("synthetic note\n")
    os.chmod(inside / "note.md", 0o600)

    config = load_root_configuration(
        {ENV_SOURCE_ROOTS: declare({"career": {"in-place": inside}})}, checkout_root=checkout
    )
    assert config.source_root_issues == ()
    root = config.resolve("career", "in-place")
    assert root.inside_checkout is True
    assert root.path == inside


def test_source_root_inside_checkout_rejected_when_not_ignored(temp_git_repo):
    """in-checkout root refused when the repository does not ignore it"""
    checkout = temp_git_repo()
    inside = private_dir(checkout, "exposed-sources")
    config = load_root_configuration(
        {ENV_SOURCE_ROOTS: declare({"career": {"in-place": inside}})}, checkout_root=checkout
    )
    assert config.root_refs("career") == ()
    assert "ignored by the repository" in config.source_root_issues[0].reason


def test_source_root_inside_checkout_rejected_when_tracked_file_present(temp_git_repo):
    """in-checkout root refused when a tracked file lives inside"""
    checkout = temp_git_repo()
    inside = private_dir(checkout, "private-sources")
    tracked = inside / "tracked.md"
    tracked.write_text("tracked by the public repository\n")
    os.chmod(tracked, 0o600)
    subprocess.run(["git", "add", "-f", str(tracked)], cwd=checkout, check=True)
    subprocess.run(["git", "commit", "-qm", "track"], cwd=checkout, check=True)
    (checkout / ".gitignore").write_text("private-sources/\n")

    config = load_root_configuration(
        {ENV_SOURCE_ROOTS: declare({"career": {"in-place": inside}})}, checkout_root=checkout
    )
    assert config.root_refs("career") == ()
    assert "no tracked files" in config.source_root_issues[0].reason


def test_invalid_source_root_does_not_disable_valid_siblings(
    workspace: Path, career_sources: Path, temp_git_repo
):
    """one bad root is not a global outage"""
    config = load_root_configuration(
        {
            ENV_SOURCE_ROOTS: declare(
                {
                    "career": {
                        "resumes": career_sources / "resumes",
                        "missing": workspace / "nowhere",
                        "base-letters": career_sources / "base-letters",
                    }
                }
            )
        },
        checkout_root=temp_git_repo(),
    )
    assert config.root_refs("career") == ("resumes", "base-letters")
    assert [issue.ref for issue in config.source_root_issues] == ["missing"]


def test_nested_private_git_repo_inside_source_root_is_allowed(
    workspace: Path, career_sources: Path, temp_git_repo
):
    """a nested private repository is allowed and its metadata is not exposed"""
    nested = career_sources / "resumes"
    subprocess.run(["git", "init", "--quiet"], cwd=nested, check=True)
    assert (nested / ".git").is_dir()

    config = load_root_configuration(
        {ENV_SOURCE_ROOTS: declare({"career": {"resumes": nested}})},
        checkout_root=temp_git_repo(),
    )
    assert config.source_root_issues == ()

    from domains.cos.work.confine import iter_files

    walked = [item.relative_path for item in iter_files(nested)]
    assert walked
    assert not any(part.startswith(".git") for path in walked for part in path.split("/"))


def test_root_issue_records_carry_no_absolute_path(workspace: Path, temp_git_repo):
    """dropped-root reports leak no filesystem layout"""
    secret = private_dir(workspace, "secret-area")
    os.chmod(secret, 0o755)
    config = load_root_configuration(
        {
            ENV_WORK_ROOT: str(workspace / "nowhere"),
            ENV_SOURCE_ROOTS: declare({"career": {"resumes": secret}}),
        },
        checkout_root=temp_git_repo(),
    )
    assert config.issues
    for issue in config.issues:
        assert str(workspace) not in issue.reason
        assert "/" not in issue.reason


# -- narrowing ---------------------------------------------------------------


def test_model_cannot_widen_roots_narrowing_predicate():
    """the narrowing predicate accepts subsets and refuses additions"""
    configured = ("resumes", "other-responses", "base-letters")
    assert is_narrowing(configured, ["resumes"]) is True
    assert is_narrowing(configured, ["resumes", "base-letters"]) is True
    assert is_narrowing(configured, list(configured)) is True
    assert is_narrowing(configured, ["resumes", "/etc"]) is False
    assert is_narrowing(configured, ["everything"]) is False
    assert is_narrowing(configured, []) is False


def test_narrow_rejects_unknown_ref(career_env):
    """narrowing to an unconfigured reference fails closed"""
    config = load_root_configuration(career_env)
    with pytest.raises(SourceRootUnavailable) as excinfo:
        narrow(config, "career", ["resumes", "somebody-elses-folder"])
    assert excinfo.value.code == "source_root_unavailable"


def test_narrow_returns_subset_in_configured_order(career_env):
    """narrowing preserves configured order"""
    config = load_root_configuration(career_env)
    chosen = narrow(config, "career", ["base-letters", "resumes"])
    assert [root.ref for root in chosen] == ["resumes", "base-letters"]
    assert [root.ref for root in narrow(config, "career", None)] == [
        "resumes",
        "other-responses",
        "base-letters",
    ]


def test_resolve_unknown_root_ref_fails_closed(career_env):
    """resolving an unknown reference raises source_root_unavailable"""
    config = load_root_configuration(career_env)
    with pytest.raises(SourceRootUnavailable) as excinfo:
        config.resolve("career", "not-configured")
    assert excinfo.value.code == "source_root_unavailable"
    with pytest.raises(SourceRootUnavailable):
        config.resolve("no-such-subject", "resumes")


def test_find_checkout_root_locates_this_repository():
    """the checkout is discoverable from the module's own location"""
    found = roots.find_checkout_root()
    assert found is not None
    assert (found / "domains" / "cos" / "work" / "roots.py").is_file()
