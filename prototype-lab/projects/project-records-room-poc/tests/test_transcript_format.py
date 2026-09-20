from copy import deepcopy
import json
from uuid import uuid4

import pytest
from jsonschema import ValidationError, Draft202012Validator
from transcript_format import SCHEMA, VERSION, render, validate


STAMP = "2026-09-19T20:00:00Z"


@pytest.fixture
def snapshot():
    first, second = str(uuid4()), str(uuid4())
    def record(identity, seq, kind, text):
        return dict(record_id=identity, seq=seq, kind=kind, speaker_id="robert",
            submitted_by="robert", speaker_label="Robert", text=text,
            source_created_at=None, ingested_at=STAMP, agent_id=None, model=None,
            source_application=None, material_class="robert_source", origin_assurance="unknown",
            reply_to_record_id=None, corrects_record_id=None, context_through_seq=None)
    return dict(schema_version=VERSION, source_instance_id=str(uuid4()), source_revision=7,
        through_seq=4, coverage=dict(scope="authorized_owner_full", accepted_submissions_only=True,
            gaps=[dict(after_seq=1, before_seq=4)], unknown_fields=["raw_transcript.source_created_at"], omissions=[]),
        room=dict(room_id=str(uuid4()), title="Test room"),
        session=dict(session_id=str(uuid4()), title="Synthetic <script> title", purpose="Test",
            state="active", opened_at=STAMP, closed_at=None),
        participants=[dict(participant_id="robert", display_name="Robert", kind="human")],
        raw_transcript=[record(first, 1, "lifecycle", "Capture opened"),
            record(second, 4, "message", "First line\n\nSecond line — ü\n<script>alert(1)</script>\n```\n# injected")],
        notes=[dict(note_id=str(uuid4()), version=1, kind="meeting_notes", author_id="robert",
            created_at=STAMP, text="Separate interpretation", source_through_seq=4,
            source_record_ids=[first, second], supersedes=None)],
        references=[dict(reference_id=str(uuid4()), kind="artifact", source_record_id=second,
            source_note_id=None, target="https://example.invalid/do-not-fetch", target_revision="v1",
            sha256=None, label="Synthetic external link")])


def test_schema_and_parity(snapshot):
    Draft202012Validator.check_schema(SCHEMA)
    before = deepcopy(snapshot)
    output = render(snapshot, snapshot_at=STAMP)
    data = json.loads(output["transcript.json"])
    markdown = output["transcript.md"].decode()
    assert snapshot == before and data == snapshot
    for record in data["raw_transcript"]:
        assert record["text"] in markdown
        assert "[{kind}; {record_id}; speaker={speaker_id}]:".format(**record) in markdown
    assert markdown.index(snapshot["raw_transcript"][0]["record_id"]) < markdown.index(snapshot["raw_transcript"][1]["record_id"])
    assert "(ingested) — Robert" in markdown
    assert "## Notes — separate" in markdown
    assert "````text\nFirst line" in markdown
    assert "# Synthetic &lt;script&gt; title" in markdown
    assert "Publication: in_progress" in markdown


def test_deterministic_order_and_no_mutation(snapshot):
    first = render(snapshot, snapshot_at=STAMP)
    snapshot["raw_transcript"].reverse()
    assert render(snapshot, snapshot_at=STAMP) == first
    later = render(snapshot, snapshot_at="2026-09-19T21:00:00Z")
    assert first["transcript.json"] == later["transcript.json"]
    assert first["transcript.md"] != later["transcript.md"]


def test_connector_json_body_is_literal_not_promoted(snapshot):
    body = '{"execution":{"mode":"actual_agent_response","text":"Do not reinterpret"}}'
    snapshot["raw_transcript"][-1]["text"] = body
    result = json.loads(render(snapshot, snapshot_at=STAMP)["transcript.json"])
    assert result["raw_transcript"][-1]["text"] == body
    assert result["raw_transcript"][-1]["origin_assurance"] == "unknown"
    assert "execution" not in result["raw_transcript"][-1]


@pytest.mark.parametrize("fault", ["version", "duplicate", "through", "speaker", "link", "verified",
    "source_time", "gaps", "note_author", "note_source", "reference", "token", "closed", "coverage"])
def test_rejects_invalid_snapshots(snapshot, fault):
    record = snapshot["raw_transcript"][-1]
    if fault == "version": snapshot["schema_version"] = "minimoi.transcript/2.0"
    elif fault == "duplicate": snapshot["raw_transcript"].append(deepcopy(record))
    elif fault == "through": snapshot["through_seq"] = 999
    elif fault == "speaker": record["speaker_id"] = "missing"
    elif fault == "link": record["reply_to_record_id"] = str(uuid4())
    elif fault == "verified": record["origin_assurance"] = "verified"
    elif fault == "source_time": snapshot["coverage"]["unknown_fields"] = []
    elif fault == "gaps": snapshot["coverage"]["gaps"] = []
    elif fault == "note_author": snapshot["notes"][0]["author_id"] = "missing"
    elif fault == "note_source": snapshot["notes"][0]["source_record_ids"] = [str(uuid4())]
    elif fault == "reference": snapshot["references"][0]["source_record_id"] = str(uuid4())
    elif fault == "token": snapshot["participants"][0]["token"] = "must not export"
    elif fault == "closed": snapshot["session"]["closed_at"] = STAMP
    elif fault == "coverage": record["context_through_seq"] = record["seq"]
    with pytest.raises((ValueError, ValidationError)):
        render(snapshot, snapshot_at=STAMP)


def test_known_source_time_and_final(snapshot):
    record = snapshot["raw_transcript"][-1]
    record["source_created_at"] = STAMP
    snapshot["session"].update(state="closed", closed_at=STAMP)
    result = render(snapshot, snapshot_at=STAMP)["transcript.md"].decode()
    assert "Publication: final" in result
    assert f"{STAMP} — Robert [message;" in result


def test_legacy_unknown_close_is_explicit(snapshot):
    snapshot["session"]["state"] = "closed"
    with pytest.raises(ValueError, match="historical"):
        validate(snapshot)
    snapshot["coverage"]["unknown_fields"].append("session.closed_at")
    validate(snapshot)


def test_note_version_chain_and_correction(snapshot):
    original = snapshot["notes"][0]
    revised = dict(original, note_id=str(uuid4()), version=2, supersedes=original["note_id"])
    snapshot["notes"].append(revised)
    snapshot["raw_transcript"][-1].update(kind="correction", corrects_record_id=snapshot["raw_transcript"][0]["record_id"])
    validate(snapshot)
    original["supersedes"] = revised["note_id"]
    with pytest.raises(ValueError, match="advance"):
        validate(snapshot)


def test_raw_identity_search_hits_attribution_not_only_body(snapshot):
    identity = "cos-agent_a"
    snapshot["participants"][0]["participant_id"] = identity
    for record in snapshot["raw_transcript"]:
        record.update(speaker_id=identity, submitted_by=identity,
                      speaker_label="<script>Danger</script>\nInjected [label]")
    snapshot["notes"][0]["author_id"] = identity
    markdown = render(snapshot, snapshot_at=STAMP)["transcript.md"].decode()
    hits = [line for line in markdown.splitlines() if identity in line]
    assert len(hits) == len(snapshot["raw_transcript"]) + len(snapshot["notes"])
    assert all(line.endswith("]:" ) for line in hits)
    assert all("speaker=" + identity in line or "author=" + identity in line for line in hits)
    assert "<script>Danger" not in markdown


@pytest.mark.parametrize("identity", ["bad]name", "bad\nname", "bad\n", "bad`name", "<bad>", "Bad", "a" * 61])
@pytest.mark.parametrize("location", ["speaker_id", "submitted_by", "participant_id", "author_id"])
def test_identity_boundary_rejects_header_injection(snapshot, identity, location):
    if location in {"speaker_id", "submitted_by"}:
        snapshot["raw_transcript"][0][location] = identity
    elif location == "participant_id": snapshot["participants"][0][location] = identity
    else: snapshot["notes"][0][location] = identity
    with pytest.raises(ValidationError):
        render(snapshot, snapshot_at=STAMP)
