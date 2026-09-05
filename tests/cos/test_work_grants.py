"""Authority: single-use, effect-specific, resource-specific, and bounded.

There is no ambient authority in this package. Every test here asks the same
question from a different angle — can a turn do something the grant it holds
did not name — and the answer has to be no in every one of them, with nothing
written on the way to that answer.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from domains.cos.work import grants
from domains.cos.work.envelope import InvalidRequest, WorkError, new_operation_id

WORK_ID = "3f1c9a2e-7b64-4d5a-9c31-8ea20b45d701"
OTHER_WORK_ID = "b21e6f34-5a08-4c7d-8e19-6d4f0a92c583"
DIGEST = "b" * 64


def code_of(response):
    assert response["ok"] is False, response
    return response["error"]["code"]


def test_missing_grant_fails_closed(work_adapter, flow):
    """no grant is no authority, and nothing is written"""
    work_id = flow.started()
    before = flow.work_dir(work_id).stat().st_mtime_ns
    response = work_adapter.call(
        "write_artifact", {"work_id": work_id, "content": "text"}, grant_ref=None
    )
    assert code_of(response) == "grant_invalid"
    assert flow.work_dir(work_id).stat().st_mtime_ns == before


def test_expired_grant_fails_closed(flow, issuer):
    """an expired grant is refused on its own terms"""
    work_id = flow.started()
    past = datetime.now(timezone.utc) - timedelta(seconds=400)
    grant = issuer.mint(
        effect="write_artifact",
        subject="career",
        conversation_id="owner",
        work_id=work_id,
        content_sha256=DIGEST,
        content_bytes=4,
        ttl_seconds=1,
        now=past,
    )
    response = flow.call("write_artifact", {"work_id": work_id, "content": "text"}, grant=grant)
    assert code_of(response) == "grant_expired"


def test_replayed_grant_fails_closed(flow):
    """a grant is spent once, and a second use is not a second authority"""
    work_id = flow.started()
    content = "A first draft.\n"
    grant = flow.mint(
        "write_artifact",
        work_id=work_id,
        content_sha256=flow._sha(content),
        content_bytes=len(content.encode()),
    )
    first = flow.call("write_artifact", {"work_id": work_id, "content": content}, grant=grant)
    assert first["ok"] is True
    second = flow.call(
        "write_artifact",
        {"work_id": work_id, "content": content},
        grant=grant,
        operation_id=new_operation_id(),
    )
    assert code_of(second) == "grant_invalid"


def test_wrong_effect_fails_closed(flow):
    """a grant names one effect"""
    work_id = flow.started()
    grant = flow.mint("read_source", work_id=work_id, source_ref="src-0001")
    response = flow.call("write_artifact", {"work_id": work_id, "content": "text"}, grant=grant)
    assert code_of(response) == "grant_effect_mismatch"


def test_wrong_resource_fails_closed(flow):
    """every bound resource is compared against the resolved request"""
    work_id = flow.started()
    other = flow.started(label="A second item")

    grant = flow.mint(
        "write_artifact", work_id=other, content_sha256=DIGEST, content_bytes=4
    )
    assert code_of(
        flow.call("write_artifact", {"work_id": work_id, "content": "text"}, grant=grant)
    ) == "grant_resource_mismatch"

    grant = flow.mint(
        "attach_source",
        work_id=work_id,
        root_refs=["authored"],
        relative_path="answers.md",
    )
    assert code_of(
        flow.call(
            "attach_source",
            {
                "work_id": work_id,
                "file_ref": {"root_ref": "authored", "relative_path": "current-resume.md"},
            },
            grant=grant,
        )
    ) == "grant_resource_mismatch"

    grant = flow.mint("request_disposition", work_id=work_id, artifact_ref="art-0009")
    assert code_of(
        flow.call(
            "request_disposition",
            {"work_id": work_id, "proposed_state": "approved_text", "artifact_ref": "art-0001"},
            grant=grant,
        )
    ) == "grant_resource_mismatch"

    grant = flow.mint("record_disposition", work_id=work_id, pending_id=OTHER_WORK_ID)
    assert code_of(
        flow.call(
            "record_disposition",
            {"work_id": work_id, "pending_id": WORK_ID, "confirmed_state": "closed"},
            grant=grant,
        )
    ) == "grant_resource_mismatch"


def test_grant_cannot_widen_roots(flow, issuer):
    """a root outside the configured set is refused at mint and at call"""
    work_id = flow.started()
    with pytest.raises(WorkError) as excinfo:
        issuer.mint(
            effect="search_sources",
            subject="career",
            conversation_id="owner",
            work_id=work_id,
            root_refs=["somewhere-else"],
        )
    assert excinfo.value.code == "source_root_unavailable"

    grant = flow.mint("search_sources", work_id=work_id, root_refs=["postings"])
    response = flow.call(
        "search_sources",
        {"work_id": work_id, "query": "reconciliation", "root_refs": ["authored"]},
        grant=grant,
    )
    assert code_of(response) == "grant_resource_mismatch"


def test_grant_narrowing_is_honoured(flow):
    """search reaches only the granted subset"""
    work_id = flow.started()
    grant = flow.mint("search_sources", work_id=work_id, root_refs=["postings"])
    response = flow.call(
        "search_sources", {"work_id": work_id, "query": "reconciliation"}, grant=grant
    )
    assert response["ok"] is True
    assert {hit["root_ref"] for hit in response["result"]["hits"]} <= {"postings"}


def test_non_none_egress_refused(issuer):
    """egress is a one-value field, refused at mint"""
    with pytest.raises(WorkError) as excinfo:
        issuer.mint(
            effect="open_work",
            subject="career",
            allow_create=True,
            operation_id=new_operation_id(),
            egress="allowed",
        )
    assert excinfo.value.code == "egress_denied"


def test_create_grant_binds_the_operation(issuer, flow):
    """a create grant names the operation, and cannot reach an existing item"""
    with pytest.raises(InvalidRequest):
        issuer.mint(
            effect="open_work",
            subject="career",
            allow_create=True,
            operation_id=new_operation_id(),
            work_id=WORK_ID,
        )
    work_id = flow.started()
    operation_id = new_operation_id()
    grant = issuer.mint(
        effect="open_work",
        subject="career",
        allow_create=True,
        operation_id=operation_id,
    )
    response = flow.call(
        "open_work",
        {"subject": "career", "work_id": work_id},
        grant=grant,
        operation_id=operation_id,
    )
    assert code_of(response) == "grant_resource_mismatch"


def test_create_grant_has_no_request_fingerprint_field(issuer):
    """the create fingerprint lives on the reservation, not on the grant"""
    assert "request_sha256" not in grants.BINDING_FIELDS
    with pytest.raises(InvalidRequest):
        issuer.mint(
            effect="open_work",
            subject="career",
            allow_create=True,
            operation_id=new_operation_id(),
            request_sha256=DIGEST,
        )


def test_consumed_store_is_bounded(issuer):
    """a high mint rate across rolling windows does not grow the table"""
    start = datetime.now(timezone.utc)
    for index in range(10_000):
        moment = start + timedelta(seconds=index)
        grant = issuer.mint(
            effect="open_work",
            subject="career",
            allow_create=True,
            operation_id=new_operation_id(),
            ttl_seconds=2,
            now=moment,
        )
        issuer.consume(grant)
        assert issuer.entry_count <= grants.MAX_GRANT_ENTRIES


def test_grant_table_capacity_is_hard(issuer):
    """at capacity the next mint is refused rather than the table growing"""
    moment = datetime.now(timezone.utc)
    live = []
    while issuer.entry_count < grants.MAX_GRANT_ENTRIES:
        live.append(
            issuer.mint(
                effect="open_work",
                subject="career",
                allow_create=True,
                operation_id=new_operation_id(),
                ttl_seconds=grants.MAX_TTL_SECONDS,
                now=moment,
            )
        )
    spent = live[0]
    issuer.consume(spent)
    with pytest.raises(WorkError) as excinfo:
        issuer.mint(
            effect="open_work",
            subject="career",
            allow_create=True,
            operation_id=new_operation_id(),
            ttl_seconds=grants.MAX_TTL_SECONDS,
            now=moment,
        )
    assert excinfo.value.code == "grant_invalid"
    assert issuer.entry_count == grants.MAX_GRANT_ENTRIES
    # no live grant was evicted to make room
    survivor = live[1]
    assert issuer.peek(survivor.grant_ref) is not None
    # and a consumed reference is still refused as a replay
    with pytest.raises(WorkError) as replay:
        issuer.verify(
            spent.grant_ref, effect="open_work", subject="career", now=moment
        )
    assert replay.value.code == "grant_invalid"


def test_inline_content_is_digest_bound(flow):
    """authority is over specific bytes, not over text of a class"""
    work_id = flow.started()
    grant = flow.mint(
        "attach_source",
        work_id=work_id,
        source_class="external_source",
        content_sha256=DIGEST,
        content_bytes=4,
    )
    response = flow.call(
        "attach_source", {"work_id": work_id, "content": "different text"}, grant=grant
    )
    assert code_of(response) == "grant_resource_mismatch"
    assert list((flow.work_dir(work_id) / "sources").iterdir()) == []


def test_source_class_only_on_inline_attach_grants(issuer, flow):
    """a class may only be bound where a class is actually chosen"""
    work_id = flow.started()
    with pytest.raises(InvalidRequest):
        issuer.mint(
            effect="write_artifact",
            subject="career",
            conversation_id="owner",
            work_id=work_id,
            content_sha256=DIGEST,
            content_bytes=4,
            source_class="robert_source",
        )
    with pytest.raises(InvalidRequest):
        issuer.mint(
            effect="attach_source",
            subject="career",
            conversation_id="owner",
            work_id=work_id,
            root_refs=["authored"],
            relative_path="answers.md",
            source_class="robert_source",
        )


def test_external_public_grant_cannot_mint_robert_source(issuer, flow):
    """an external-public turn cannot mint personally authored evidence"""
    work_id = flow.started()
    with pytest.raises(InvalidRequest):
        issuer.mint(
            effect="attach_source",
            subject="career",
            conversation_id="owner",
            work_id=work_id,
            source_class="robert_source",
            content_sha256=DIGEST,
            content_bytes=4,
            data_class="external_public",
        )


def test_robert_edit_requires_robert_source_root(issuer, flow):
    """an edit is adopted only from a root that declares that authorship"""
    work_id = flow.started()
    for root_ref in ("postings", "drafts"):
        with pytest.raises(InvalidRequest):
            issuer.mint(
                effect="use_robert_edit",
                subject="career",
                conversation_id="owner",
                work_id=work_id,
                supersedes_ref="art-0001",
                expected_sha256=DIGEST,
                root_refs=[root_ref],
                relative_path="scratch.md",
                expected_input_sha256=DIGEST,
            )


def test_failed_effect_still_consumes_the_grant(flow, issuer):
    """a failure spends the authority; the operation stays retryable"""
    work_id = flow.started()
    grant = flow.mint(
        "request_disposition", work_id=work_id, artifact_ref="art-0404"
    )
    response = flow.call(
        "request_disposition",
        {"work_id": work_id, "proposed_state": "approved_text", "artifact_ref": "art-0404"},
        grant=grant,
    )
    assert response["ok"] is False
    assert issuer.peek(grant.grant_ref) is None
    assert issuer.is_consumed(grant.grant_ref) is True


def test_binding_authority_is_one_direct_open(flow, work_service):
    """a turn reaches the one work item its conversation names, and no other"""
    first = flow.started(label="First")
    second = flow.started(label="Second")
    # the conversation now names the second item
    grant = flow.mint(
        "write_artifact", work_id=first, content_sha256=DIGEST, content_bytes=4
    )
    response = flow.call("write_artifact", {"work_id": first, "content": "text"}, grant=grant)
    assert code_of(response) == "grant_resource_mismatch"

    # a conversation with no binding at all cannot reach into work either
    issuer = work_service.issuer
    content = "A draft.\n"
    stray = issuer.mint(
        effect="write_artifact",
        subject="career",
        conversation_id="no-such-conversation",
        work_id=second,
        content_sha256=flow._sha(content),
        content_bytes=len(content.encode()),
    )
    assert code_of(
        flow.call("write_artifact", {"work_id": second, "content": content}, grant=stray)
    ) == "grant_resource_mismatch"


def test_no_reverse_binding_scan(flow, monkeypatch):
    """no effect enumerates the conversations directory"""
    import os

    work_id = flow.started()
    subject_paths = flow.service.store.subject_paths("career")
    conversations = subject_paths.conversations
    real_scandir = os.scandir

    def watched(path=".", *args, **kwargs):
        assert os.path.realpath(str(path)) != os.path.realpath(str(conversations))
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", watched)
    response = flow.write(work_id, "A draft with no reverse lookup.\n")
    assert response["ok"] is True
