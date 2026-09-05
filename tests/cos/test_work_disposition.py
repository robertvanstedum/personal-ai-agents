"""Deciding: two steps, exact bytes, and no reading between the lines.

Approval pins one artifact's digest at the moment it is proposed and checks
it again at the moment it is confirmed. Encouragement is not a decision. A
decision closes the work item, and there is no path that quietly reopens it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from domains.cos.work import approval, records, store
from domains.cos.work.envelope import PROPOSED_STATES, new_operation_id


def code_of(response):
    assert response["ok"] is False, response
    return response["error"]["code"]


def paths_for(flow, work_id):
    return store.WorkPaths(directory=flow.work_dir(work_id))


def prepared(flow):
    work_id = flow.started()
    written = flow.write(work_id, "A draft to decide about.\n")
    return work_id, written["result"]["artifact_ref"], written["result"]["sha256"]


def test_two_step_approval_pins_exact_bytes(flow):
    """the digest is pinned when proposed and checked when confirmed"""
    work_id, artifact_ref, digest = prepared(flow)
    proposed = flow.propose(work_id, "approved_text", artifact_ref)
    assert proposed["result"]["artifact_sha256"] == digest
    assert proposed["receipt"]["sha256"] == digest
    assert proposed["receipt"]["state"] == "continuing"
    assert proposed["receipt"]["proposed_state"] == "approved_text"

    confirmed = flow.decide(work_id, proposed["result"]["pending_id"], "approved_text")
    assert confirmed["ok"] is True
    assert confirmed["receipt"]["state"] == "approved_text"
    assert confirmed["receipt"]["sha256"] == digest
    record = store.read_record(paths_for(flow, work_id))[0]
    assert record.approved_artifact_ref == artifact_ref


@pytest.mark.parametrize(
    "phrase", ["looks good", "great", "ship it", "send it", "lgtm", "sounds good", "yes"]
)
def test_ambiguous_phrases_change_nothing(phrase):
    """encouragement is not an instruction"""
    match = approval.match(phrase)
    assert match.confirms is False
    assert match.ambiguous is True
    assert match.state is None


@pytest.mark.parametrize(
    "phrase,state",
    [
        ("/approve", "approved_text"),
        ("approve", "approved_text"),
        ("Approved.", "approved_text"),
        ("use this text", "approved_text"),
        ("/close", "closed"),
        ("do not apply", "closed"),
        ("don't apply", "closed"),
        ("/unresolved", "unresolved"),
        ("park this", "unresolved"),
    ],
)
def test_closed_phrase_set_matches_deterministically(phrase, state):
    """each accepted phrase means exactly one decision"""
    assert approval.match(phrase).state == state


def test_phrases_are_matched_whole_not_by_substring():
    """a sentence that contains a phrase is not that phrase"""
    for text in (
        "I would not approve this yet",
        "please do not close it",
        "approve? not yet",
        "",
        "x" * 200,
    ):
        assert approval.match(text).state is None


def test_confirmation_sentence_carries_no_text(flow):
    """the prompt names the decision and the identifier, never the artifact"""
    work_id, artifact_ref, _digest = prepared(flow)
    proposed = flow.propose(work_id, "approved_text", artifact_ref)
    sentence = proposed["result"]["confirmation_sentence"]
    assert proposed["result"]["pending_id"] in sentence
    assert "A draft to decide about" not in sentence


def test_expired_pending_fails_closed(flow, work_service):
    """a decision past its deadline is no longer answerable"""
    work_id, artifact_ref, _digest = prepared(flow)
    proposed = flow.propose(work_id, "approved_text", artifact_ref)
    later = datetime.now(timezone.utc) + timedelta(seconds=2000)
    work_service._clock = lambda: later
    response = flow.decide(work_id, proposed["result"]["pending_id"], "approved_text")
    assert code_of(response) == "pending_expired"
    assert store.read_record(paths_for(flow, work_id))[0].disposition is None


def test_changed_target_fails_closed(flow):
    """text that changed between proposing and confirming is not approved"""
    work_id, artifact_ref, _digest = prepared(flow)
    proposed = flow.propose(work_id, "approved_text", artifact_ref)
    paths = paths_for(flow, work_id)
    (paths.artifacts / "0001-letter.md").write_text("Different text entirely.\n")
    os.chmod(paths.artifacts / "0001-letter.md", 0o600)
    response = flow.decide(work_id, proposed["result"]["pending_id"], "approved_text")
    assert code_of(response) == "pending_target_changed"
    assert store.read_record(paths)[0].disposition is None


def test_superseded_pending_id_is_unusable(flow):
    """replacing a pending makes the earlier identifier unanswerable"""
    work_id, artifact_ref, _digest = prepared(flow)
    first = flow.propose(work_id, "approved_text", artifact_ref)
    second = flow.propose(work_id, "closed")
    assert second["ok"] is True
    response = flow.decide(work_id, first["result"]["pending_id"], "approved_text")
    assert code_of(response) == "pending_expired"


def test_unknown_pending_id_is_pending_expired(flow):
    """absent, superseded and expired answer the same way"""
    work_id, _artifact_ref, _digest = prepared(flow)
    response = flow.decide(work_id, new_operation_id(), "closed")
    assert code_of(response) == "pending_expired"


def test_confirmed_state_must_match_the_proposal(flow):
    """confirming a different decision changes nothing"""
    work_id, artifact_ref, _digest = prepared(flow)
    proposed = flow.propose(work_id, "approved_text", artifact_ref)
    response = flow.decide(work_id, proposed["result"]["pending_id"], "closed")
    assert code_of(response) == "invalid_request"
    assert store.read_record(paths_for(flow, work_id))[0].disposition is None


def test_approval_state_and_disposition_agree(flow):
    """the projection then returns the artifact, and only that artifact"""
    work_id, artifact_ref, digest = prepared(flow)
    proposed = flow.propose(work_id, "approved_text", artifact_ref)
    flow.decide(work_id, proposed["result"]["pending_id"], "approved_text")
    items, issues = flow.service.accumulation.approved_artifacts(flow.subject)
    assert issues == ()
    assert [item.sha256 for item in items] == [digest]
    assert items[0].context_class == "agent_draft"


def test_close_without_artifact_is_legal(flow):
    """a decision not to apply is a first-class outcome, end to end"""
    work_id = flow.started()
    flow.write(work_id, "A draft nobody will send.\n")
    proposed = flow.propose(work_id, "closed")
    assert "artifact_ref" not in proposed["result"]
    assert "ref" not in proposed["receipt"]
    assert "sha256" not in proposed["receipt"]

    paths = paths_for(flow, work_id)
    parsed = records.parse_work_record(json.loads(paths.record.read_bytes()))
    assert parsed.pending_approval.artifact_ref is None
    assert parsed.pending_approval.artifact_sha256 is None

    reason = "We are not applying: the depot scope does not match."
    confirmed = flow.decide(work_id, proposed["result"]["pending_id"], "closed", reason=reason)
    assert confirmed["ok"] is True
    assert "ref" not in confirmed["receipt"]
    record = store.read_record(paths)[0]
    assert record.state == "closed"
    assert record.disposition.artifact_ref is None
    assert record.disposition.reason == reason
    assert records.load_work_record(paths.record) == record


def test_pending_close_refuses_an_artifact(flow):
    """a decision about the work item does not name a file"""
    work_id, artifact_ref, _digest = prepared(flow)
    grant = flow.mint("request_disposition", work_id=work_id, artifact_ref=artifact_ref)
    response = flow.call(
        "request_disposition",
        {"work_id": work_id, "proposed_state": "closed", "artifact_ref": artifact_ref},
        grant=grant,
    )
    assert code_of(response) == "invalid_request"

    hand_written = {
        "pending_id": "0a5d31c6-9e72-4a18-b3f5-27c69d08e4b1",
        "proposed_state": "closed",
        "artifact_ref": artifact_ref,
        "issued_at": "2026-09-05T00:00:00Z",
        "expires_at": "2026-09-05T00:10:00Z",
    }
    document = json.loads(paths_for(flow, work_id).record.read_bytes())
    document["pending_approval"] = hand_written
    with pytest.raises(records.RecordInvalid) as excinfo:
        records.parse_work_record(document)
    assert "may not name an artifact" in excinfo.value.message


def test_pending_approval_requires_ref_and_digest(flow):
    """an approval of text names the artifact and pins its digest"""
    work_id, artifact_ref, digest = prepared(flow)
    document = json.loads(paths_for(flow, work_id).record.read_bytes())
    base = {
        "pending_id": "0a5d31c6-9e72-4a18-b3f5-27c69d08e4b1",
        "proposed_state": "approved_text",
        "issued_at": "2026-09-05T00:00:00Z",
        "expires_at": "2026-09-05T00:10:00Z",
    }

    document["pending_approval"] = dict(base)
    with pytest.raises(records.RecordInvalid) as missing_ref:
        records.parse_work_record(document)
    assert "must name the artifact" in missing_ref.value.message

    document["pending_approval"] = {**base, "artifact_ref": artifact_ref}
    with pytest.raises(records.RecordInvalid) as missing_digest:
        records.parse_work_record(document)
    assert "must pin the artifact's digest" in missing_digest.value.message

    # the existing message still fires first for a ref that names nothing
    document["pending_approval"] = {**base, "artifact_ref": "art-9999"}
    with pytest.raises(records.RecordInvalid) as unknown:
        records.parse_work_record(document)
    assert "pending approval names an artifact" in unknown.value.message

    document["pending_approval"] = {
        **base,
        "artifact_ref": artifact_ref,
        "artifact_sha256": digest,
    }
    assert records.parse_work_record(document).pending_approval.artifact_sha256 == digest


def test_existing_w0a_record_tests_still_pass():
    """the merged record-invariant suite runs unmodified against the completion"""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/cos/test_work_record_invariants.py"],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("proposed_state", sorted(PROPOSED_STATES))
def test_request_disposition_artifact_rule(flow, proposed_state):
    """the artifact is required for approval and refused for the other two"""
    work_id, artifact_ref, _digest = prepared(flow)
    without = flow.propose(work_id, proposed_state)
    with_artifact = flow.propose(work_id, proposed_state, artifact_ref)
    if proposed_state == "approved_text":
        assert code_of(without) == "invalid_request"
        assert with_artifact["ok"] is True
    else:
        assert without["ok"] is True
        assert code_of(with_artifact) == "invalid_request"


def test_continuing_is_not_a_proposed_state(flow):
    """the initial active state is not a decision to propose"""
    assert "continuing" not in PROPOSED_STATES
    work_id, artifact_ref, _digest = prepared(flow)
    grant = flow.mint("request_disposition", work_id=work_id)
    before = paths_for(flow, work_id).record.read_bytes()
    response = flow.call(
        "request_disposition",
        {"work_id": work_id, "proposed_state": "continuing"},
        grant=grant,
    )
    assert code_of(response) == "invalid_request"
    assert paths_for(flow, work_id).record.read_bytes() == before


def test_writes_refused_while_disposition_present(flow):
    """a decided work item takes no more changes"""
    work_id, artifact_ref, digest = prepared(flow)
    proposed = flow.propose(work_id, "closed")
    flow.decide(work_id, proposed["result"]["pending_id"], "closed", reason="Not applying.")

    assert code_of(flow.write(work_id, "Another draft.\n")) == "invalid_request"
    assert code_of(flow.attach_inline(work_id, "More material.\n")) == "invalid_request"
    assert code_of(
        flow.edit_inline(work_id, "An edit.\n", artifact_ref, digest)
    ) == "invalid_request"
    assert code_of(flow.propose(work_id, "unresolved")) == "invalid_request"


def test_no_reopen_path_exists(flow):
    """no effect clears or replaces a decision, and opening still works"""
    work_id, _artifact_ref, _digest = prepared(flow)
    proposed = flow.propose(work_id, "closed")
    flow.decide(work_id, proposed["result"]["pending_id"], "closed", reason="Not applying.")
    paths = paths_for(flow, work_id)
    before = paths.record.read_bytes()

    for attempt in (
        lambda: flow.propose(work_id, "approved_text", "art-0001"),
        lambda: flow.propose(work_id, "unresolved"),
        lambda: flow.write(work_id, "Reopening by the back door.\n"),
    ):
        assert attempt()["ok"] is False
    assert paths.record.read_bytes() == before

    opened = flow.open_existing(work_id)
    assert opened["ok"] is True
    assert opened["result"]["disposition"]["state"] == "closed"
    assert paths.record.read_bytes() == before


def test_approval_sends_nothing(flow):
    """deciding performs no I/O beyond this record, under the socket guard"""
    work_id, artifact_ref, _digest = prepared(flow)
    proposed = flow.propose(work_id, "approved_text", artifact_ref)
    confirmed = flow.decide(work_id, proposed["result"]["pending_id"], "approved_text")
    assert confirmed["ok"] is True
    for payload in (proposed, confirmed):
        rendered = json.dumps(payload)
        for word in ("http", "send", "recipient", "email", "@"):
            assert word not in rendered.casefold()
