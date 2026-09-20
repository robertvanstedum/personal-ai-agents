"""Transcript 1.0 renderer/validator; no database, publication or network IO.

Input is a typed, authorized snapshot, not the legacy room export. The caller
must obtain source identity/revision from the store and enforce authorization.
This module neither invents those values nor upgrades declared provenance.
"""
from copy import deepcopy
from datetime import datetime
import html
import json
import re


VERSION = "minimoi.transcript/1.0"
RENDERER = "minimoi.transcript.markdown/1.0"


def _object(properties, required=None):
    return dict(type="object", properties=properties,
                required=list(properties) if required is None else required,
                additionalProperties=False)


TEXT = {"type": "string"}
ACTOR_ID = {"type": "string", "minLength": 1, "maxLength": 60,
            "pattern": r"^[a-z][a-z0-9_-]*$(?![\s\S])"}
NULL_TEXT = {"type": ["string", "null"]}
ID = {"type": "string", "format": "uuid"}
NULL_ID = {"type": ["string", "null"], "format": "uuid"}
NUMBER = {"type": "integer", "minimum": 0}
TIME = {"type": "string", "format": "date-time", "pattern": "Z$"}
NULL_TIME = {"type": ["string", "null"], "format": "date-time", "pattern": "Z$"}
KINDS = ["message", "lifecycle", "correction", "proposal", "recorded_decision",
         "assignment", "checkpoint", "task_update", "membership", "moderator",
         "document_filed", "note_filed", "artifact_linked"]

RECORD = _object(dict(
    record_id=ID, seq=NUMBER, kind={"enum": KINDS}, speaker_id=ACTOR_ID,
    submitted_by=ACTOR_ID, speaker_label=TEXT, text=TEXT,
    source_created_at=NULL_TIME, ingested_at=TIME,
    agent_id=NULL_TEXT, model=NULL_TEXT, source_application=NULL_TEXT,
    material_class=NULL_TEXT, origin_assurance={"enum": ["unknown", "declared", "verified"]},
    reply_to_record_id=NULL_ID, corrects_record_id=NULL_ID,
    context_through_seq={"type": ["integer", "null"], "minimum": 0},
    execution=_object(dict(coordination_request_id=ID, openclaw_run_id=TEXT), required=[]),
    imported_source=_object(dict(declared_speaker=TEXT,coverage=TEXT,source_ordinal=NUMBER,
        source_record_ids={"type":"array","items":ID},material_type={"enum":["transcript","handoff"]}),
        required=["coverage","material_type"]),
    evidence_reference=TEXT, verification_method=TEXT),
    required=["record_id", "seq", "kind", "speaker_id", "submitted_by", "speaker_label", "text",
              "source_created_at", "ingested_at", "agent_id", "model", "source_application",
              "material_class", "origin_assurance", "reply_to_record_id", "corrects_record_id",
              "context_through_seq"])

SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", **_object(dict(
    schema_version={"const": VERSION}, source_instance_id=ID, source_revision=NUMBER,
    through_seq=NUMBER,
    coverage=_object(dict(scope={"const": "authorized_owner_full"},
        accepted_submissions_only={"const": True},
        gaps={"type": "array", "items": _object(dict(after_seq=NUMBER, before_seq=NUMBER))},
        unknown_fields={"type": "array", "items": TEXT},
        omissions={"type": "array", "items": TEXT})),
    room=_object(dict(room_id=ID, title=TEXT)),
    session=_object(dict(session_id=ID, title=TEXT, purpose=TEXT,
        state={"enum": ["active", "paused", "closed"]}, opened_at=NULL_TIME, closed_at=NULL_TIME)),
    participants={"type": "array", "items": _object(dict(participant_id=ACTOR_ID,
        display_name=TEXT, kind={"enum": ["human", "agent", "unknown"]}))},
    raw_transcript={"type": "array", "items": RECORD},
    notes={"type": "array", "items": _object(dict(note_id=ID,
        version={"type": "integer", "minimum": 1}, kind=TEXT, author_id=ACTOR_ID,
        created_at=TIME, text=TEXT, source_through_seq=NUMBER,
        source_record_ids={"type": "array", "items": ID, "uniqueItems": True},
        supersedes=NULL_ID))},
    references={"type": "array", "items": _object(dict(reference_id=ID, kind=TEXT,
        source_record_id=NULL_ID, source_note_id=NULL_ID, target=TEXT,
        target_revision=NULL_TEXT, sha256={"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
        label=TEXT))}
))}


def validate(snapshot):
    """Validate shape and local linkage; no remote schema or target fetches."""
    from jsonschema import Draft202012Validator, FormatChecker
    Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(snapshot)
    records = snapshot["raw_transcript"]
    ids = [r["record_id"] for r in records]
    seqs = [r["seq"] for r in records]
    participants = [p["participant_id"] for p in snapshot["participants"]]
    notes = snapshot["notes"]
    note_ids = [n["note_id"] for n in notes]
    refs = [r["reference_id"] for r in snapshot["references"]]
    for values in (ids, seqs, participants, note_ids, refs):
        if len(values) != len(set(values)):
            raise ValueError("Duplicate transcript identity or sequence")
    if snapshot["through_seq"] != max(seqs, default=0):
        raise ValueError("through_seq must equal greatest included sequence")
    by_id = {r["record_id"]: r for r in records}
    ordered_seqs = sorted(seqs)
    expected_gaps = [dict(after_seq=a, before_seq=b) for a, b in zip(ordered_seqs, ordered_seqs[1:]) if b > a + 1]
    if snapshot["coverage"]["gaps"] != expected_gaps:
        raise ValueError("Sequence gaps must be declared exactly; they need not mean lost records")
    for record in records:
        if record["speaker_id"] not in participants or record["submitted_by"] not in participants:
            raise ValueError("Unknown transcript participant")
        for field in ("reply_to_record_id", "corrects_record_id"):
            target = record[field]
            if target is not None and (target not in by_id or by_id[target]["seq"] >= record["seq"]):
                raise ValueError("Record link must identify an earlier included record")
        if record["kind"] == "correction" and record["corrects_record_id"] is None:
            raise ValueError("Correction requires its original record")
        if record["source_created_at"] is None and "raw_transcript.source_created_at" not in snapshot["coverage"]["unknown_fields"]:
            raise ValueError("Unknown source timestamps must be declared")
        if record["origin_assurance"] == "verified" and not all(
                record.get(k, "").strip() for k in ("evidence_reference", "verification_method")):
            raise ValueError("Verified origin requires evidence and verification method")
        if record["context_through_seq"] is not None and record["context_through_seq"] >= record["seq"]:
            raise ValueError("Agent coverage must precede contribution")
    for note in notes:
        if note["author_id"] not in participants or note["source_through_seq"] > snapshot["through_seq"]:
            raise ValueError("Invalid note author or coverage")
        if any(i not in by_id or by_id[i]["seq"] > note["source_through_seq"] for i in note["source_record_ids"]):
            raise ValueError("Invalid note source record")
        if note["supersedes"] is not None and (note["supersedes"] not in note_ids or note["supersedes"] == note["note_id"]):
            raise ValueError("Invalid superseded note")
        if note["supersedes"] is not None:
            previous = next(n for n in notes if n["note_id"] == note["supersedes"])
            if previous["version"] + 1 != note["version"] or previous["kind"] != note["kind"]:
                raise ValueError("Note chain must advance one version within its kind")
    for ref in snapshot["references"]:
        if ref["source_record_id"] is not None and ref["source_record_id"] not in ids:
            raise ValueError("Reference source record is outside snapshot")
        if ref["source_note_id"] is not None and ref["source_note_id"] not in note_ids:
            raise ValueError("Reference source note is outside snapshot")
    for field in ("opened_at", "closed_at"):
        if snapshot["session"][field] is None and "session." + field not in snapshot["coverage"]["unknown_fields"]:
            if field == "opened_at" or snapshot["session"]["state"] == "closed":
                raise ValueError("Unknown historical timestamp must be declared")
    if snapshot["session"]["state"] != "closed" and snapshot["session"]["closed_at"] is not None:
        raise ValueError("Open session cannot claim a closing timestamp")


def _label(value):
    # Inline metadata cannot inject new transcript headers or HTML.
    value = html.escape(value, quote=False).replace("\n", " ").replace("\r", " ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value)


def _literal(value):
    # Dynamic fences keep HTML/links/injected fences inert, with exact body bytes.
    fence = "`" * max(3, 1 + max((len(s) for s in re.findall(r"`+", value)), default=0))
    return fence + "text\n" + value + "\n" + fence


def render(snapshot, *, snapshot_at):
    """Return deterministic JSON and safe Markdown from ONE copied snapshot.

    snapshot_at describes the captured read, supplied by the capture layer.
    Generated-at and publication hashes belong to the later bundle manifest.
    """
    data = deepcopy(snapshot)
    validate(data)
    if not snapshot_at.endswith("Z"):
        raise ValueError("snapshot_at requires UTC Z timestamp")
    datetime.fromisoformat(snapshot_at.replace("Z", "+00:00"))
    data["raw_transcript"].sort(key=lambda r: (r["seq"], r["record_id"]))
    for field, key in (("participants", "participant_id"), ("notes", "note_id"), ("references", "reference_id")):
        data[field].sort(key=lambda r: r[key])
    status = "final" if data["session"]["state"] == "closed" else "in_progress"
    lines = ["# " + _label(data["session"]["title"]), "",
        f"Source: {data['source_instance_id']} · Session: {data['session']['session_id']}",
        f"Snapshot: {snapshot_at} · Revision: {data['source_revision']} · Through sequence: {data['through_seq']}",
        f"Publication: {status} · Session state: {data['session']['state']}",
        "Coverage: " + _literal(json.dumps(data["coverage"], ensure_ascii=False, sort_keys=True)),
        "", "## Transcript", ""]
    for record in data["raw_transcript"]:
        stamp = record["source_created_at"] or record["ingested_at"] + " (ingested)"
        lines.extend([f"{stamp} — {_label(record['speaker_label'])} [{record['kind']}; {record['record_id']}; speaker={record['speaker_id']}]:",
                      _literal(record["text"]), ""])
        if record.get("imported_source"):
            source=record["imported_source"]
            lines.extend(["Imported source (declared, not authenticated speaker): " + _literal(json.dumps(source,ensure_ascii=False,sort_keys=True)), ""])
    lines.extend(["## Notes — separate from transcript", ""])
    for note in data["notes"]:
        lines.extend([f"{note['created_at']} — {_label(note['author_id'])} [{_label(note['kind'])}; {note['note_id']}; author={note['author_id']}]:",
                      f"Version: {note['version']} · Source through: {note['source_through_seq']}",
                      "Source records: " + ", ".join(note["source_record_ids"]),
                      _literal(note["text"]), ""])
    lines.extend(["## References — not automatically followed", ""])
    for ref in data["references"]:
        lines.extend([_literal(json.dumps(ref, ensure_ascii=False, sort_keys=True)), ""])
    return {"transcript.json": (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
            "transcript.md": ("\n".join(lines) + "\n").encode()}
