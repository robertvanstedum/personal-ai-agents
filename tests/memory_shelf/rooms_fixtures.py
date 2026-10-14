"""Synthetic Rooms export bundles. Nothing here is real: ids are made up, texts are invented."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

from core.memory_shelf.config import SourceCfg

INSTANCE = "11111111-1111-4111-8111-111111111111"
OTHER_INSTANCE = "22222222-2222-4222-8222-222222222222"
SESSION = "aaaaaaaa-0000-4000-8000-000000000001"
SESSION_2 = "aaaaaaaa-0000-4000-8000-000000000002"
OWNER = "robert"


def uid(n: int, group: str = "0") -> str:
    return str(UUID(int=int(group, 16) * (1 << 64) + n))


def person(pid: str, kind: str, label: str | None = None) -> dict:
    return {"participant_id": pid, "display_name": label or pid.title(), "kind": kind}


def record(n: int, speaker: str, text: str, *, kind: str = "message", label: str | None = None, reply=None,
           corrects=None, created: str | None = None, submitted_by: str | None = None, **extra) -> dict:
    row = {"record_id": uid(n, "a"), "seq": n, "kind": kind, "speaker_id": speaker, "submitted_by": submitted_by or speaker,
           "speaker_label": label or speaker, "text": text, "source_created_at": created,
           "ingested_at": f"2026-10-0{1 + min(n, 8)}T10:00:00Z", "agent_id": None, "model": None, "source_application": None,
           "material_class": None, "origin_assurance": "declared", "reply_to_record_id": reply, "corrects_record_id": corrects,
           "context_through_seq": None}
    row.update(extra)
    return row


def note(n: int, author: str, text: str, *, through: int, kind: str = "summary") -> dict:
    return {"note_id": uid(n, "b"), "version": 1, "kind": kind, "author_id": author, "created_at": "2026-10-04T09:00:00Z",
            "text": text, "source_through_seq": through, "source_record_ids": [], "supersedes": None}


def reference(n: int, target: str, label: str, *, record_n: int | None = None) -> dict:
    return {"reference_id": uid(n, "c"), "kind": "artifact", "source_record_id": uid(record_n, "a") if record_n else None,
            "source_note_id": None, "target": target, "target_revision": None, "sha256": None, "label": label}


def default_records() -> list[dict]:
    return [record(1, OWNER, "Kick-off: what is the plan for the migration?"),
            record(2, "agent-a", "I propose we start with the read path.", reply=uid(1, "a")),
            record(3, "agent-b", "Agreed, with a rollback checkpoint.", reply=uid(2, "a"), label="Agent B (reviewer)"),
            record(4, OWNER, "Decision: read path first.", kind="recorded_decision")]


V10, V11 = "minimoi.transcript/1.0", "minimoi.transcript/1.1"


def execution(n: int = 1, **over) -> dict:
    """Rooms R1 typed execution evidence, as the exporter writes it for a committed agent turn."""
    ev = {"turn_id": uid(n, "e"), "claim_id": uid(n, "f"), "attempt": 1, "coordinating_installation": "install-test-1",
          "caller_correlation": "a" * 32, "upstream_execution_id": None, "usage_evidence_status": "none"}
    ev.update(over)
    return ev


def agent_turn(n: int, speaker: str, text: str, *, reply=None, ev: dict | None = None, **extra) -> dict:
    """A committed agent record the way the 1.1 exporter maps it: declared origin, context coverage, execution evidence."""
    base = dict(execution=ev if ev is not None else execution(n), context_through_seq=n - 1, model=None,
                agent_id="mc-agent", source_application="rooms_worker")
    base.update(extra)
    return record(n, speaker, text, reply=reply, **base)


def document(*, instance=INSTANCE, session=SESSION, revision=1, state="closed", records=None, participants=None,
             notes=None, references=None, coverage=None, title="Migration planning", schema=V10) -> dict:
    records = default_records() if records is None else records
    participants = participants if participants is not None else [person(OWNER, "human"), person("agent-a", "agent"),
                                                                  person("agent-b", "agent")]
    through = max((r["seq"] for r in records), default=0)
    return {"schema_version": schema, "source_instance_id": instance, "source_revision": revision,
            "through_seq": through,
            "coverage": coverage or {"scope": "authorized_owner_full", "accepted_submissions_only": True, "gaps": [],
                                     "unknown_fields": ["raw_transcript.source_created_at"], "omissions": []},
            "room": {"room_id": uid(1, "d"), "title": "Room"},
            "session": {"session_id": session, "title": title, "purpose": "planning", "state": state,
                        "opened_at": "2026-10-01T09:00:00Z", "closed_at": "2026-10-04T09:00:00Z" if state == "closed" else None},
            "participants": participants, "raw_transcript": records, "notes": notes or [], "references": references or []}


def write_bundle(root: Path, doc: dict | None = None, *, md: bytes = b"# rendered view\n", tamper=None, name=None,
                 manifest_extra: dict | None = None) -> Path:
    """Write one bundle directory exactly as the exporter does; ``tamper(manifest, files)`` may break it afterwards."""
    doc = doc or document()
    files = {"transcript.json": (json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
             "transcript.md": md}
    material = b"".join(n.encode() + b"\0" + files[n] for n in sorted(files))
    identity = f"{doc['session']['session_id']}-r{doc['source_revision']}-{hashlib.sha256(material).hexdigest()}"
    manifest = {"bundle_id": identity, "schema_version": doc["schema_version"], "renderer_version": "minimoi.transcript.markdown/1.0",
                "source_instance_id": doc["source_instance_id"], "session_id": doc["session"]["session_id"],
                "source_revision": doc["source_revision"], "through_seq": doc["through_seq"],
                "generated_at": "2026-10-04T09:30:00Z", "snapshot_at": "2026-10-04T09:29:00Z",
                "session_state": doc["session"]["state"],
                "publication_status": "final" if doc["session"]["state"] == "closed" else "in_progress",
                "scope": "authorized_owner_full",
                "files": {n: {"bytes": len(c), "sha256": hashlib.sha256(c).hexdigest()} for n, c in files.items()}}
    manifest.update(manifest_extra or {})
    if tamper:
        tamper(manifest, files)
    folder = Path(root) / (name or identity)
    folder.mkdir(parents=True, mode=0o700)
    for fname, content in files.items():
        (folder / fname).write_bytes(content)
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return folder


def cfg(root: Path, *, owners=(OWNER,), instance=INSTANCE, name="rooms", never_copy=()) -> SourceCfg:
    return SourceCfg(name, "rooms", Path(root), tuple(never_copy), tuple(owners), instance)
