"""Bounded, confined, provenance-preserving search and read."""

from __future__ import annotations

import socket
from pathlib import Path
from unittest import mock

import pytest

from domains.cos.work.confine import NotFound, PathDenied, sha256_file
from domains.cos.work.envelope import InvalidRequest
from domains.cos.work.retrieval import (
    MAX_EXCERPT_CHARS_CEILING,
    MAX_RESULTS_CEILING,
    Accumulation,
)
from domains.cos.work.roots import SourceRootUnavailable, load_root_configuration


def test_search_finds_term_in_configured_roots(career_accumulation: Accumulation):
    """a term present in a source root is found"""
    outcome = career_accumulation.search_sources("career", ["resumes"], "throughput")
    assert outcome.hits
    assert all(hit.root_ref == "resumes" for hit in outcome.hits)
    assert all("throughput" in hit.excerpt.casefold() for hit in outcome.hits)
    assert outcome.issues == ()


def test_search_results_carry_root_ref_path_hash_and_line_span(
    career_accumulation: Accumulation, career_sources: Path
):
    """every excerpt carries its provenance"""
    outcome = career_accumulation.search_sources("career", ["resumes"], "throughput")
    hit = outcome.hits[0]
    assert hit.subject == "career"
    assert hit.root_ref == "resumes"
    assert hit.relative_path == "current-resume.md"
    assert hit.sha256 == sha256_file(career_sources / "resumes" / "current-resume.md")
    assert hit.line_start >= 1
    assert hit.line_end == hit.line_start
    assert hit.excerpt


def test_search_provenance_class_is_robert_source_for_configured_roots(
    career_accumulation: Accumulation,
):
    """configured source roots yield robert_source"""
    outcome = career_accumulation.search_sources(
        "career", ["resumes", "other-responses", "base-letters"], "reconciliation"
    )
    assert outcome.hits
    assert {hit.context_class for hit in outcome.hits} == {"robert_source"}
    assert all(hit.disposition is None for hit in outcome.hits)


def test_search_is_deterministic_across_runs(career_accumulation: Accumulation):
    """repeated searches return an identical ordering"""
    def run():
        return [
            (hit.root_ref, hit.relative_path, hit.line_start)
            for hit in career_accumulation.search_sources(
                "career", None, "reconciliation throughput warehouse"
            ).hits
        ]

    first = run()
    assert first
    for _ in range(4):
        assert run() == first


def test_search_respects_max_results_ceiling(career_accumulation: Accumulation):
    """results are bounded"""
    outcome = career_accumulation.search_sources("career", None, "the", max_results=3)
    assert len(outcome.hits) == 3
    unbounded = career_accumulation.search_sources("career", None, "the")
    assert len(unbounded.hits) <= MAX_RESULTS_CEILING


def test_search_rejects_max_results_above_ceiling(career_accumulation: Accumulation):
    """asking for more than the ceiling is refused"""
    with pytest.raises(InvalidRequest) as excinfo:
        career_accumulation.search_sources("career", None, "the", max_results=25)
    assert excinfo.value.code == "invalid_request"
    with pytest.raises(InvalidRequest):
        career_accumulation.search_sources("career", None, "the", max_results=0)


def test_search_truncates_excerpt_to_limit(career_accumulation: Accumulation):
    """excerpts are bounded"""
    outcome = career_accumulation.search_sources(
        "career", None, "reconciliation", max_excerpt_chars=12
    )
    assert outcome.hits
    assert all(len(hit.excerpt) <= 12 for hit in outcome.hits)


def test_search_rejects_excerpt_limit_above_ceiling(career_accumulation: Accumulation):
    """asking for a longer excerpt than the ceiling is refused"""
    assert MAX_EXCERPT_CHARS_CEILING == 800
    with pytest.raises(InvalidRequest):
        career_accumulation.search_sources(
            "career", None, "reconciliation", max_excerpt_chars=4000
        )


def test_search_rejects_empty_query(career_accumulation: Accumulation):
    """an empty query is invalid_request"""
    for query in ["", "   ", "  -- "]:
        with pytest.raises(InvalidRequest):
            career_accumulation.search_sources("career", None, query)


def test_search_is_confined_to_requested_roots(career_accumulation: Accumulation):
    """narrowing to one root excludes the others"""
    everywhere = career_accumulation.search_sources("career", None, "reconciliation")
    assert {hit.root_ref for hit in everywhere.hits} > {"other-responses"}

    narrowed = career_accumulation.search_sources(
        "career", ["other-responses"], "reconciliation"
    )
    assert narrowed.hits
    assert {hit.root_ref for hit in narrowed.hits} == {"other-responses"}


def test_search_cannot_address_unconfigured_root(career_accumulation: Accumulation):
    """an unconfigured root reference fails closed"""
    with pytest.raises(SourceRootUnavailable) as excinfo:
        career_accumulation.search_sources("career", ["somebody-elses-folder"], "anything")
    assert excinfo.value.code == "source_root_unavailable"
    with pytest.raises(InvalidRequest):
        career_accumulation.search_sources("career", "resumes", "anything")
    with pytest.raises(InvalidRequest):
        career_accumulation.search_sources("career", [], "anything")


def test_search_skips_unsupported_and_oversized_files(
    career_accumulation: Accumulation, career_sources: Path
):
    """the search surface obeys the same file gates"""
    (career_sources / "resumes" / "scan.png").write_bytes(b"reconciliation in a picture")
    (career_sources / "resumes" / "archive.pdf").write_bytes(b"reconciliation in a document")
    outcome = career_accumulation.search_sources("career", ["resumes"], "reconciliation")
    assert {hit.relative_path for hit in outcome.hits} == {"current-resume.md"}


def test_read_source_returns_content_hash_and_size(
    career_accumulation: Accumulation, career_sources: Path
):
    """read returns bytes, hash and size"""
    outcome = career_accumulation.read_source("career", "resumes", "current-resume.md")
    target = career_sources / "resumes" / "current-resume.md"
    assert outcome.content == target.read_text()
    assert outcome.sha256 == sha256_file(target)
    assert outcome.bytes == target.stat().st_size
    assert outcome.relative_path == "current-resume.md"
    assert outcome.context_class == "robert_source"
    assert outcome.disposition is None


def test_read_source_rejects_traversal(career_accumulation: Accumulation):
    """read is confined"""
    with pytest.raises(PathDenied):
        career_accumulation.read_source("career", "resumes", "../other-responses/answer-01.md")
    with pytest.raises(PathDenied):
        career_accumulation.read_source("career", "resumes", "/etc/hosts")
    with pytest.raises(NotFound):
        career_accumulation.read_source("career", "resumes", "absent.md")


def test_read_source_unknown_root_fails_closed(career_accumulation: Accumulation):
    """read of an unconfigured root fails closed"""
    with pytest.raises(SourceRootUnavailable):
        career_accumulation.read_source("career", "not-configured", "current-resume.md")
    with pytest.raises(InvalidRequest):
        career_accumulation.read_source("career", "", "current-resume.md")


def test_retrieval_performs_no_network_io(career_accumulation: Accumulation):
    """no operation opens a socket"""

    def refuse(*args, **kwargs):
        raise AssertionError("the accumulation reference must never open a socket")

    with mock.patch.object(socket, "socket", refuse), mock.patch.object(
        socket, "create_connection", refuse
    ):
        career_accumulation.search_sources("career", None, "reconciliation")
        career_accumulation.read_source("career", "resumes", "current-resume.md")
        career_accumulation.approved_artifacts("career")


def test_search_hits_never_contain_absolute_paths(
    career_accumulation: Accumulation, workspace: Path
):
    """results expose relative paths only"""
    outcome = career_accumulation.search_sources("career", None, "reconciliation throughput")
    assert outcome.hits
    for hit in outcome.hits:
        assert not hit.relative_path.startswith("/")
        assert str(workspace) not in hit.relative_path
        assert str(workspace) not in hit.excerpt


def test_search_unknown_subject_returns_nothing(career_env):
    """a subject with no configured roots yields no results and no leak"""
    accumulation = Accumulation(load_root_configuration(career_env))
    outcome = accumulation.search_sources("no-such-subject", None, "reconciliation")
    assert outcome.hits == ()
    with pytest.raises(InvalidRequest):
        accumulation.search_sources("", None, "reconciliation")
