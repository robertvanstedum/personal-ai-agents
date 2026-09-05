"""Adopting an edit made outside the system, without rewriting authorship.

The earlier machine draft is never relabelled and never touched. A returned
edit becomes a *new* revision, co-authored, pointing back at the one it
replaces. Identical bytes are refused, because identical bytes are not
evidence that anyone contributed anything.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from domains.cos.work import records, store


def code_of(response):
    assert response["ok"] is False, response
    return response["error"]["code"]


def paths_for(flow, work_id):
    return store.WorkPaths(directory=flow.work_dir(work_id))


def drafted(flow):
    work_id = flow.started()
    written = flow.write(work_id, "A machine draft of the letter.\n")
    return work_id, written["result"]


def test_edit_creates_coauthored_revision(flow):
    """the edit is a new revision that names the one it supersedes"""
    work_id, draft = drafted(flow)
    edited = flow.edit_inline(
        work_id,
        "A machine draft of the letter, in my own words.\n",
        draft["artifact_ref"],
        draft["sha256"],
    )
    assert edited["ok"] is True
    result = edited["result"]
    assert result["revision"] == draft["revision"] + 1
    assert result["context_class"] == "coauthored_output"
    assert result["supersedes_ref"] == draft["artifact_ref"]
    assert edited["receipt"]["supersedes_ref"] == draft["artifact_ref"]

    record = store.read_record(paths_for(flow, work_id))[0]
    new = record.artifact(result["artifact_ref"])
    assert new.supersedes_ref == draft["artifact_ref"]
    assert new.sha256 == result["sha256"]


def test_agent_draft_is_untouched_and_unrelabelled(flow):
    """the earlier entry and its file are byte-identical afterwards"""
    work_id, draft = drafted(flow)
    paths = paths_for(flow, work_id)
    before_record = store.read_record(paths)[0]
    before_file = (paths.directory / draft["relative_path"]).read_bytes()

    flow.edit_inline(
        work_id, "Corrected by hand.\n", draft["artifact_ref"], draft["sha256"]
    )

    after = store.read_record(paths)[0]
    assert after.artifacts[: len(before_record.artifacts)] == before_record.artifacts
    assert (paths.directory / draft["relative_path"]).read_bytes() == before_file
    assert after.artifact(draft["artifact_ref"]).context_class == "agent_draft"


def test_identical_bytes_refused(flow):
    """the same text back is not evidence of an authorship contribution"""
    work_id, draft = drafted(flow)
    paths = paths_for(flow, work_id)
    before = store.read_record(paths)[0]
    response = flow.edit_inline(
        work_id, "A machine draft of the letter.\n", draft["artifact_ref"], draft["sha256"]
    )
    assert code_of(response) == "invalid_request"
    assert store.read_record(paths)[0] == before
    assert len(list(paths.artifacts.iterdir())) == 1


def test_edit_by_file_reference(flow, synthetic_roots):
    """the folder is the interface: a saved file, addressed by root and path"""
    work_id, draft = drafted(flow)
    edited_text = "A machine draft of the letter, rewritten where it mattered.\n"
    saved = synthetic_roots / "authored" / "returned-draft.md"
    saved.write_text(edited_text)
    os.chmod(saved, 0o600)

    response = flow.edit_file(
        work_id,
        "authored",
        "returned-draft.md",
        draft["artifact_ref"],
        draft["sha256"],
        hashlib.sha256(edited_text.encode("utf-8")).hexdigest(),
    )
    assert response["ok"] is True
    assert response["result"]["context_class"] == "coauthored_output"
    stored = (
        paths_for(flow, work_id).directory / response["result"]["relative_path"]
    ).read_text("utf-8")
    assert stored == edited_text


def test_stale_expected_sha256_refused(flow):
    """an edit against a revision that has moved writes nothing"""
    work_id, draft = drafted(flow)
    paths = paths_for(flow, work_id)
    before = store.read_record(paths)[0]
    response = flow.edit_inline(
        work_id, "Corrected by hand.\n", draft["artifact_ref"], "0" * 64
    )
    assert code_of(response) in ("stale_context", "grant_resource_mismatch")
    assert store.read_record(paths)[0] == before


def test_robert_edit_file_bytes_are_digest_bound(flow, synthetic_roots):
    """a file changed between mint and read is stale, not silently accepted"""
    work_id, draft = drafted(flow)
    edited_text = "The version I authorised.\n"
    saved = synthetic_roots / "authored" / "returned-draft.md"
    saved.write_text(edited_text)
    os.chmod(saved, 0o600)
    authorised = hashlib.sha256(edited_text.encode("utf-8")).hexdigest()

    grant = flow.mint(
        "use_robert_edit",
        work_id=work_id,
        supersedes_ref=draft["artifact_ref"],
        expected_sha256=draft["sha256"],
        root_refs=["authored"],
        relative_path="returned-draft.md",
        expected_input_sha256=authorised,
    )
    saved.write_text("Something substituted after the fact.\n")
    os.chmod(saved, 0o600)

    paths = paths_for(flow, work_id)
    before = store.read_record(paths)[0]
    response = flow.call(
        "use_robert_edit",
        {
            "work_id": work_id,
            "file_ref": {"root_ref": "authored", "relative_path": "returned-draft.md"},
            "supersedes_ref": draft["artifact_ref"],
            "expected_sha256": draft["sha256"],
        },
        grant=grant,
    )
    assert code_of(response) == "stale_context"
    assert store.read_record(paths)[0] == before


def test_robert_edit_inline_bytes_are_digest_bound(flow):
    """substituted inline bytes are refused before anything is written"""
    work_id, draft = drafted(flow)
    grant = flow.mint(
        "use_robert_edit",
        work_id=work_id,
        supersedes_ref=draft["artifact_ref"],
        expected_sha256=draft["sha256"],
        content_sha256=flow._sha("The version I authorised.\n"),
        content_bytes=len("The version I authorised.\n".encode()),
    )
    response = flow.call(
        "use_robert_edit",
        {
            "work_id": work_id,
            "content": "Something else entirely.\n",
            "supersedes_ref": draft["artifact_ref"],
            "expected_sha256": draft["sha256"],
        },
        grant=grant,
    )
    assert code_of(response) == "grant_resource_mismatch"
    assert len(store.read_record(paths_for(flow, work_id))[0].artifacts) == 1


def test_superseding_the_same_base_twice_is_permitted(flow):
    """two co-authored revisions may come from one base"""
    work_id, draft = drafted(flow)
    first = flow.edit_inline(
        work_id, "One way of putting it.\n", draft["artifact_ref"], draft["sha256"]
    )
    second = flow.edit_inline(
        work_id, "Another way of putting it.\n", draft["artifact_ref"], draft["sha256"]
    )
    assert first["ok"] and second["ok"]
    record = store.read_record(paths_for(flow, work_id))[0]
    assert [a.revision for a in record.artifacts] == [1, 2, 3]
    assert [a.supersedes_ref for a in record.artifacts] == [
        None,
        draft["artifact_ref"],
        draft["artifact_ref"],
    ]


def test_write_artifact_never_sets_supersedes_ref(flow):
    """only the edit effect records an authorship chain"""
    work_id, draft = drafted(flow)
    flow.write(work_id, "An unrelated later draft.\n")
    record = store.read_record(paths_for(flow, work_id))[0]
    assert all(
        entry.supersedes_ref is None
        for entry in record.artifacts
        if entry.context_class == "agent_draft"
    )
