"""Building on text that was already approved, without laundering it.

A later work item must be able to cite text Robert approved, pinned by hash.
It does that through one qualified reference resolved entirely by the
accumulation reference — never by recapturing the artifact as a source, which
would turn a machine draft into personally authored evidence, and never by a
second membership rule of W0b's own.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from domains.cos.work import records, service, store
from domains.cos.work.envelope import InvalidRequest


def code_of(response):
    assert response["ok"] is False, response
    return response["error"]["code"]


def paths_for(flow, work_id):
    return store.WorkPaths(directory=flow.work_dir(work_id))


def approved_item(flow, *, label, text, by_edit=False):
    """Create a work item, approve one artifact, and return its reference."""
    work_id = flow.started(label=label)
    written = flow.write(work_id, text)
    ref = written["result"]["artifact_ref"]
    digest = written["result"]["sha256"]
    relative = written["result"]["relative_path"]
    if by_edit:
        edited = flow.edit_inline(work_id, text + "In my own words.\n", ref, digest)
        ref = edited["result"]["artifact_ref"]
        digest = edited["result"]["sha256"]
        relative = edited["result"]["relative_path"]
    proposed = flow.propose(work_id, "approved_text", ref)
    flow.decide(work_id, proposed["result"]["pending_id"], "approved_text")
    qualified = f"approved:{flow.subject}/{flow.work_dir(work_id).name}/{relative}"
    return work_id, qualified, digest


def test_approved_based_on_resolves_through_w0a(flow, monkeypatch):
    """the reference is resolved only through the accumulation reference"""
    _source, qualified, digest = approved_item(
        flow, label="An approved item", text="Approved text.\n"
    )
    citing = flow.started(label="A later item")

    calls = []
    real = flow.service.accumulation.read_source

    def watched(subject, root_ref, relative_path):
        calls.append((subject, root_ref, relative_path))
        return real(subject, root_ref, relative_path)

    monkeypatch.setattr(flow.service.accumulation, "read_source", watched)
    response = flow.write(
        citing, "A draft that cites it.\n", based_on=[{"ref": qualified, "sha256": digest}]
    )
    assert response["ok"] is True
    assert calls
    assert all(root_ref == f"approved:{flow.subject}" for _s, root_ref, _p in calls)


def test_membership_decision_is_w0a_only(flow, monkeypatch):
    """with the accumulation read refused, nothing resolves"""
    _source, qualified, digest = approved_item(
        flow, label="An approved item", text="Approved text.\n"
    )
    citing = flow.started(label="A later item")

    def refuse(subject, root_ref, relative_path):
        raise records.StaleContext(relative_path)

    monkeypatch.setattr(flow.service.accumulation, "read_source", refuse)
    response = flow.write(
        citing, "A draft that cites it.\n", based_on=[{"ref": qualified, "sha256": digest}]
    )
    assert code_of(response) == "stale_context"


def test_approved_based_on_cites_draft_and_coauthored(flow):
    """a third item cites an approved draft and an approved edit together"""
    _one, draft_ref, draft_digest = approved_item(
        flow, label="An approved draft", text="An approved machine draft.\n"
    )
    _two, edit_ref, edit_digest = approved_item(
        flow, label="An approved edit", text="A draft that was rewritten.\n", by_edit=True
    )
    citing = flow.started(label="A third item")
    response = flow.write(
        citing,
        "A draft built on both.\n",
        based_on=[
            {"ref": draft_ref, "sha256": draft_digest},
            {"ref": edit_ref, "sha256": edit_digest},
        ],
    )
    assert response["ok"] is True
    record = records.load_work_record(paths_for(flow, citing).record)
    cited = {entry.ref for entry in record.artifacts[0].based_on}
    assert cited == {draft_ref, edit_ref}


def test_approved_based_on_stale_hash_writes_nothing(flow):
    """a hash that no longer matches the approved snapshot refuses"""
    _source, qualified, _digest = approved_item(
        flow, label="An approved item", text="Approved text.\n"
    )
    citing = flow.started(label="A later item")
    paths = paths_for(flow, citing)
    response = flow.write(
        citing, "A draft.\n", based_on=[{"ref": qualified, "sha256": "0" * 64}]
    )
    assert code_of(response) == "stale_context"
    assert list(paths.artifacts.iterdir()) == []
    assert list(paths.pending.iterdir()) == []


def test_approved_based_on_unapproved_path_writes_nothing(flow):
    """a real but unapproved artifact answers exactly like one that is absent"""
    unapproved = flow.started(label="An item nobody approved")
    written = flow.write(unapproved, "An unapproved draft.\n")
    qualified = (
        f"approved:{flow.subject}/{flow.work_dir(unapproved).name}/"
        f"{written['result']['relative_path']}"
    )
    citing = flow.started(label="A later item")
    real = flow.write(
        citing, "A draft.\n", based_on=[{"ref": qualified, "sha256": written["result"]["sha256"]}]
    )
    absent = flow.write(
        citing,
        "A draft.\n",
        based_on=[
            {
                "ref": f"approved:{flow.subject}/nothing--here/artifacts/0001-letter.md",
                "sha256": written["result"]["sha256"],
            }
        ],
    )
    assert code_of(real) == code_of(absent) == "not_found"
    assert list(paths_for(flow, citing).artifacts.iterdir()) == []


def test_approved_based_on_other_subject_refused(flow):
    """a reference to another subject is refused before anything is read"""
    citing = flow.started(label="A later item")
    response = flow.write(
        citing,
        "A draft.\n",
        based_on=[
            {"ref": "approved:decision-memo/a--b/artifacts/0001-letter.md", "sha256": "0" * 64}
        ],
    )
    assert code_of(response) == "invalid_request"


@pytest.mark.parametrize(
    "reference",
    [
        "approved:career/" + "x" * 300,
        "approved:career/work--id/../escape.md",
        "approved:career//etc/passwd",
        "approved:career/work--id/sources/0001-posting.txt",
        "approved:career/work--id",
        "approved:Career/work--id/artifacts/0001-letter.md",
        "approved:career/work--id/artifacts",
        "not-approved:career/work--id/artifacts/0001-letter.md",
    ],
)
def test_approved_based_on_grammar_bounded(reference):
    """the wrapper is bounded, and only the artifact subtree is admitted"""
    with pytest.raises(Exception) as excinfo:
        service.parse_approved_ref(reference, "career")
    assert excinfo.value.__class__.__name__ in ("InvalidRequest", "PathDenied")


def test_approved_based_on_accepts_a_nested_artifact_path():
    """nesting under artifacts is accepted, because the record schema accepts it"""
    parsed = service.parse_approved_ref(
        "approved:career/work--id/artifacts/revised/0002-letter.md", "career"
    )
    assert parsed == "work--id/artifacts/revised/0002-letter.md"


def test_approved_based_on_never_enters_sources(flow):
    """citing changes neither the cited record nor this one's sources"""
    source_id, qualified, digest = approved_item(
        flow, label="An approved item", text="Approved text.\n"
    )
    source_record_before = paths_for(flow, source_id).record.read_bytes()
    citing = flow.started(label="A later item")

    response = flow.write(
        citing, "A draft that cites it.\n", based_on=[{"ref": qualified, "sha256": digest}]
    )
    assert response["ok"] is True
    record = store.read_record(paths_for(flow, citing))[0]
    assert record.sources == ()
    assert record.artifacts[0].context_class == "agent_draft"
    assert paths_for(flow, source_id).record.read_bytes() == source_record_before


def test_attach_source_from_approved_still_refused(flow):
    """approved material is read and cited, never recaptured"""
    _source, qualified, _digest = approved_item(
        flow, label="An approved item", text="Approved text.\n"
    )
    citing = flow.started(label="A later item")
    relative = qualified.split("/", 1)[1]
    grant = flow.mint(
        "attach_source",
        work_id=citing,
        root_refs=[f"approved:{flow.subject}"],
        relative_path=relative,
    )
    response = flow.call(
        "attach_source",
        {
            "work_id": citing,
            "file_ref": {"root_ref": f"approved:{flow.subject}", "relative_path": relative},
        },
        grant=grant,
    )
    assert code_of(response) == "invalid_request"
    assert store.read_record(paths_for(flow, citing))[0].sources == ()


def test_local_based_on_must_resolve_in_this_record(flow):
    """a local handle that names nothing here is refused"""
    citing = flow.started(label="A later item")
    response = flow.write(
        citing, "A draft.\n", based_on=[{"ref": "src-0009", "sha256": "0" * 64}]
    )
    assert code_of(response) == "invalid_request"
