"""Every configured root declares its own provenance, and keeps it.

The authorship boundary is the point of this gate. A root that holds an
employer's posting, an earlier agent draft, or a co-authored output must never
have its contents presented later as Robert's own writing, so there is no
``robert_source`` default to fall back to: a root that does not say what it
holds is refused.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from domains.cos.work.envelope import CONTEXT_CLASSES
from domains.cos.work.retrieval import Accumulation
from domains.cos.work.roots import (
    ENV_SOURCE_ROOTS,
    ENV_SOURCE_ROOTS_FILE,
    ENV_WORK_ROOT,
    load_root_configuration,
)


def declare(mapping: dict, default_class: str = "robert_source") -> str:
    document: dict[str, dict[str, dict[str, str]]] = {}
    for subject, refs in mapping.items():
        entries: dict[str, dict[str, str]] = {}
        for ref, value in refs.items():
            path, context_class = value if isinstance(value, tuple) else (value, default_class)
            entries[ref] = {"path": str(path), "context_class": context_class}
        document[subject] = entries
    return json.dumps(document)


@pytest.fixture
def four_class_env(workspace: Path, private_work_root: Path) -> dict[str, str]:
    """One root of each of the four provenance classes, all readable."""
    mapping: dict[str, tuple[Path, str]] = {}
    for index, context_class in enumerate(sorted(CONTEXT_CLASSES)):
        directory = workspace / f"root-{index}"
        directory.mkdir()
        os.chmod(directory, 0o700)
        note = directory / "note.md"
        note.write_text(f"A shared marker word: reconciliation, in {context_class}.\n")
        os.chmod(note, 0o600)
        mapping[context_class.replace("_", "-")] = (directory, context_class)
    return {
        ENV_WORK_ROOT: str(private_work_root),
        ENV_SOURCE_ROOTS: declare({"career": mapping}),
    }


def test_all_four_provenance_classes_are_accepted(four_class_env):
    """a root may declare any of the four stored classes"""
    assert CONTEXT_CLASSES == frozenset(
        {"robert_source", "external_source", "agent_draft", "coauthored_output"}
    )
    config = load_root_configuration(four_class_env)
    assert config.source_root_issues == ()
    declared = {ref: config.resolve("career", ref).context_class for ref in config.root_refs("career")}
    assert set(declared.values()) == CONTEXT_CLASSES


def test_search_and_read_carry_the_declared_class(four_class_env):
    """every result carries the class its root declared"""
    configuration = load_root_configuration(four_class_env)
    accumulation = Accumulation(configuration)
    configured = list(configuration.root_refs("career"))
    outcome = accumulation.search_sources("career", configured, "reconciliation")
    assert outcome.hits
    seen = {hit.root_ref: hit.context_class for hit in outcome.hits}
    for ref, context_class in seen.items():
        assert context_class == ref.replace("-", "_")
    assert set(seen.values()) == CONTEXT_CLASSES

    for ref in seen:
        read = accumulation.read_source("career", ref, "note.md")
        assert read.context_class == ref.replace("-", "_")


@pytest.mark.parametrize(
    "context_class", ["external_source", "agent_draft", "coauthored_output"]
)
def test_non_robert_root_is_never_relabelled_as_robert(four_class_env, context_class):
    """someone else's writing, or the system's own draft, stays labelled as such"""
    accumulation = Accumulation(load_root_configuration(four_class_env))
    ref = context_class.replace("_", "-")

    outcome = accumulation.search_sources("career", [ref], "reconciliation")
    assert outcome.hits
    assert {hit.context_class for hit in outcome.hits} == {context_class}
    assert "robert_source" not in {hit.context_class for hit in outcome.hits}

    read = accumulation.read_source("career", ref, "note.md")
    assert read.context_class == context_class
    assert read.context_class != "robert_source"


def test_root_without_a_declared_class_is_refused(workspace: Path, career_sources: Path):
    """an absent provenance class drops the root and reports it"""
    document = {"career": {"resumes": {"path": str(career_sources / "resumes")}}}
    config = load_root_configuration({ENV_SOURCE_ROOTS: json.dumps(document)})
    assert config.root_refs("career") == ()
    assert [issue.code for issue in config.source_root_issues] == ["source_root_unavailable"]
    assert "provenance class" in config.source_root_issues[0].reason


def test_root_with_an_unknown_class_is_refused(career_sources: Path):
    """an unrecognised provenance class drops the root and reports it"""
    for bad in ["robert", "ROBERT_SOURCE", "trusted", "", "  "]:
        config = load_root_configuration(
            {
                ENV_SOURCE_ROOTS: declare(
                    {"career": {"resumes": (career_sources / "resumes", bad)}}
                )
            }
        )
        assert config.root_refs("career") == ()
        assert config.source_root_issues
        assert "provenance class" in config.source_root_issues[0].reason


def test_bare_path_declaration_is_refused(career_sources: Path):
    """the old path-only shape carries no provenance, so it is not accepted"""
    document = {"career": {"resumes": str(career_sources / "resumes")}}
    config = load_root_configuration({ENV_SOURCE_ROOTS: json.dumps(document)})
    assert config.root_refs("career") == ()
    assert [issue.code for issue in config.source_root_issues] == ["source_root_unavailable"]
    assert "object" in config.source_root_issues[0].reason


def test_root_declaration_carries_no_per_file_metadata(career_sources: Path):
    """a root declares a path and a class, and nothing else"""
    document = {
        "career": {
            "resumes": {
                "path": str(career_sources / "resumes"),
                "context_class": "robert_source",
                "files": {"current-resume.md": "robert_source"},
            }
        }
    }
    config = load_root_configuration({ENV_SOURCE_ROOTS: json.dumps(document)})
    assert config.root_refs("career") == ()
    assert "not part of it" in config.source_root_issues[0].reason


def test_file_variant_accepts_the_same_declaration(
    workspace: Path, career_sources: Path
):
    """COS_WORK_SOURCE_ROOTS_FILE reads the same per-root shape"""
    declaration = workspace / "source-roots.json"
    declaration.write_text(
        declare({"career": {"postings": (career_sources / "base-letters", "external_source")}})
    )
    config = load_root_configuration({ENV_SOURCE_ROOTS_FILE: str(declaration)})
    assert config.source_root_issues == ()
    assert config.resolve("career", "postings").context_class == "external_source"


def test_one_undeclared_root_does_not_disable_its_siblings(
    career_sources: Path, workspace: Path
):
    """a missing class is a per-root outage, not a global one"""
    document = {
        "career": {
            "resumes": {
                "path": str(career_sources / "resumes"),
                "context_class": "robert_source",
            },
            "postings": {"path": str(career_sources / "base-letters")},
        }
    }
    config = load_root_configuration({ENV_SOURCE_ROOTS: json.dumps(document)})
    assert config.root_refs("career") == ("resumes",)
    assert config.resolve("career", "resumes").context_class == "robert_source"
    assert [issue.ref for issue in config.source_root_issues] == ["postings"]
