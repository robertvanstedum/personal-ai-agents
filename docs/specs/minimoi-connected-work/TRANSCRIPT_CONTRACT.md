# Transcript artifact and PostgreSQL mapping — contract 1.0

Companion to [Records & Rooms revision 6](RECORDS_ROOMS_v6.md). Contract only: no exporter, migration or importer is installed by this document. Validate deployed PostgreSQL schemas and privileges before implementation.

## A. Reuse assessment

Repository definitions inspected:
- `domains/german/parse_transcript.py`: readable per-session JSON, `session_id`, `date`, `source`, `raw_transcript` and speaker/text entries. Reuse those concepts and snake_case naming, not language-specific persona/drill fields.
- `domains/guild/db/migrations/003_portuguese_sessions.sql`: domain-specific sessions, integer IDs and raw_transcript TEXT. Not a shared multi-speaker event store; leave unchanged.
- `domains/guild/db/migrations/002_auth_schema.sql`: auth.users and domain access. Reuse optional identity linkage after explicit mapping, not its credential fields.
- `domains/guild/db/schema_challenger.sql`: digests/summaries, not complete conversation. Do not repurpose it.
- `domains/guild/db/schema_phase5.sql`: design log summaries, not transcripts. It may link a session but cannot replace it.

This is source-level inspection, not a claim that all those tables exist in the deployed database. Proposed new schema: `records`. No live catalog or personal rows were inspected.

## B. JSON file contract

One UTF-8 JSON object per session snapshot, formatted with indentation for reading/diffing. This is ordinary JSON, not JSONL. Optional JSONL can be added as a separately versioned derivative later. Arrays hold typed records, not serialized JSON strings. No base64 documents, credentials, private key paths or raw SQL in the transcript artifact.

Required envelope fields:

| Field | Type and rule |
|---|---|
| schema_version | string, initially `minimoi.transcript/1.0`; reject unsupported major versions |
| source_instance_id | UUID identifying the historical source namespace; preserve on restore and authority transfer; deliberate independent fork gets a new origin |
| source_revision | nonnegative integer snapshot revision, advancing on export-relevant changes including metadata/notes/access policy; assigned by store, not wall clock |
| through_seq | nonnegative integer greatest included source sequence; gaps are permitted; not a message count |
| coverage | object describing accepted submissions only, omissions/gaps and full-owner versus other authorized scope |
| room | object with room_id UUID and title; metadata shared according to room policy |
| session | object with session_id UUID, title, purpose, state, opened_at and nullable closed_at |
| participants | array of identity snapshots used by included records; no access tokens |
| raw_transcript | ordered array of typed records below |
| notes | array of separately identified immutable note versions and their coverage |
| references | array of document/artifact/supporting-record links |

Timestamps use RFC 3339 UTC with `Z`. Unknown historical values are null and marked unknown in coverage, never guessed. IDs are strings and never reassigned on export. Arrays are deterministically ordered (transcript by seq then ID; other collections by stable ID). No field conveys approval merely by its presence.

Each participant: `participant_id` string (source-local stable actor ID), `display_name`, `kind` human/agent/unknown. A record can additionally preserve its display-name-at-submission; later name changes must not silently rewrite the historical quote. Human account linkage is an importer-side mapping, not credential data in the artifact.

Each transcript record:
- `record_id` UUID, `seq` integer, `kind` (message, lifecycle, correction, proposal, recorded_decision, assignment or other explicitly mapped audit kind).
- `speaker_id`, `submitted_by` participant IDs; `speaker_label` historical display name; `text` plain string preserving original wording.
- `source_created_at` nullable timestamp; `ingested_at` required timestamp. Legacy `created` maps to ingested_at only unless source-time provenance is known.
- `agent_id` and `model` nullable strings, separate from speaker/submitter. `source_application` nullable string.
- `material_class` nullable existing classification; `origin_assurance` unknown/declared/verified. Verified requires an evidence reference and verification method, not just a nonempty runtime ID.
- `reply_to_record_id`, `corrects_record_id` nullable IDs; `context_through_seq` nullable integer for agent snapshot coverage; optional `execution` object for retained request/run evidence.

Do not parse a connector's JSON-in-text body heuristically. Future connector/exporter integration needs a versioned typed mapping that preserves exact original text and separates execution metadata. Legacy unrecognized bodies remain literal text with declared origin; no retroactive runtime attestation.

Each note: `note_id`, `version`, `kind`, `author_id`, `created_at`, `text`, source-through sequence and source record IDs, nullable supersedes ID. Notes are never substituted for raw_transcript.

Each reference: `reference_id`, `kind`, source record/note ID where applicable, target identifier/URI, target revision, nullable SHA-256 and label. References may point outside the store; do not automatically dereference or leak protected targets. Attachments remain separately access-controlled files.

### Small synthetic example

```json
{
  "schema_version": "minimoi.transcript/1.0",
  "source_instance_id": "11111111-1111-4111-8111-111111111111",
  "source_revision": 3,
  "through_seq": 8,
  "coverage": {"scope": "authorized_owner_full", "accepted_submissions_only": true, "gaps": []},
  "room": {"room_id": "22222222-2222-4222-8222-222222222222", "title": "Synthetic design room"},
  "session": {
    "session_id": "33333333-3333-4333-8333-333333333333",
    "title": "Synthetic conversation", "purpose": "Example only", "state": "closed",
    "opened_at": "2026-09-19T12:00:00Z", "closed_at": "2026-09-19T12:05:00Z"
  },
  "participants": [{"participant_id": "owner", "display_name": "Example owner", "kind": "human"}],
  "raw_transcript": [{
    "record_id": "44444444-4444-4444-8444-444444444444", "seq": 8, "kind": "message",
    "speaker_id": "owner", "submitted_by": "owner", "speaker_label": "Example owner",
    "text": "Preserve the discussion and its source links.", "source_created_at": null,
    "ingested_at": "2026-09-19T12:01:00Z", "agent_id": null, "model": null,
    "source_application": "records-ui", "material_class": "robert_source",
    "origin_assurance": "declared", "reply_to_record_id": null,
    "corrects_record_id": null, "context_through_seq": null
  }],
  "notes": [], "references": []
}
```

This abbreviated example illustrates fields; acceptance fixtures must also include actual lifecycle records and complete capture coverage. A JSON Schema and representative legacy/current fixtures must be implemented and independently validated before the exporter is accepted. The abbreviated example is not itself a complete acceptance fixture.

## C. Markdown and publication manifest

Each contribution has a greppable `timestamp — speaker [kind; record ID]:` header and a plain-text body preserving multiline content. Display source time when known, otherwise labelled ingestion time. Preserve JSON sequence order, IDs, attribution and text. Lifecycle events are visibly distinct; notes have a separate section and source coverage. Header metadata includes source/session IDs, snapshot time, through-sequence, source revision, coverage and in-progress/final state. Safely render untrusted markup. Markdown and JSON are views of the same snapshot, not competing authorities.


`manifest.json` contains bundle ID, schema/renderer versions, source instance/session IDs, source revision, through_seq, generated_at, session state, snapshot_at, publication_status (in_progress/final), scope and relative filenames with SHA-256 and byte counts. Do not self-hash the manifest. JSON and Markdown derive from the SAME consistent snapshot. An exporter verifies files before atomic publication; a consumer verifies the manifest before ingestion. Archive filename includes session ID, source revision and content digest to avoid silent replacement.

Generated timestamps live in the manifest, not each message. Re-exporting unchanged logical content should not churn message bytes. If renderer changes produce different bytes, preserve a new identified bundle. An old bundle remains valid for its snapshot, not necessarily current. A latest pointer is convenience, not source authority.

Automatic in-progress and final artifacts are full owner-authorized snapshots stored privately. In-progress state is not itself permission filtering. Permission-filtered exports must declare omissions/scope and cannot be imported as complete owner snapshots. A copied file is not a live authorization mechanism: central services must independently enforce approved access, and subsequent access changes must be propagated or access fail closed.

## D. Proposed relational layout

Use `records` schema; `source_instance_id UUID` namespaces origin-owned identities. Existing global sequence gaps are preserved. SQL migrations are a later reviewed implementation, not execution instructions here.

| Table | Keys and principal columns |
|---|---|
| records.sources | PK source_instance_id; label TEXT; source_kind TEXT; registered_at TIMESTAMPTZ; enabled BOOLEAN |
| records.rooms | PK (source_instance_id, room_id UUID); title TEXT; source_revision BIGINT |
| records.sessions | PK (source_instance_id, session_id UUID); room_id FK in same source; title/purpose TEXT; state TEXT; opened_at/closed_at TIMESTAMPTZ; source_revision BIGINT; through_seq BIGINT; optional owner_user_id INTEGER linked to auth.users after approved mapping |
| records.participants | PK (source_instance_id, participant_id TEXT); display_name/kind TEXT; optional auth_user_id INTEGER; declared agent identities do not become human accounts |
| records.session_participants | PK (source_instance_id, session_id, participant_id); references sessions/participants; provenance of association; historical participation, NOT an access grant |
| records.messages | PK (source_instance_id, record_id UUID); session_id FK; UNIQUE (source_instance_id, session_id, seq BIGINT); kind TEXT; speaker_id/submitted_by FKs; speaker_label/text TEXT; source_created_at nullable and ingested_at TIMESTAMPTZ; agent_id/model/source_application TEXT nullable; material_class/origin_assurance TEXT; reply_to/corrects UUID nullable; context_through_seq BIGINT nullable; execution JSONB nullable; content_sha256 TEXT |
| records.notes | PK (source_instance_id, note_id UUID, version INTEGER); session_id FK; author_id FK; kind/text TEXT; created_at TIMESTAMPTZ; source_through_seq BIGINT; source_record_ids JSONB; supersedes UUID nullable; content_sha256 TEXT |
| records.references | PK (source_instance_id, reference_id UUID); session_id FK; source_record_id/source_note_id nullable; kind/target/target_revision/label TEXT; target_sha256 TEXT nullable; content_sha256 TEXT |
| records.imports | PK bundle digest TEXT; source/session FKs; schema/renderer versions; source_revision/through_seq BIGINT; verified_at/imported_at TIMESTAMPTZ; status TEXT; row_counts JSONB; failure_code TEXT nullable |

Foreign keys use the same source namespace; validate same-session internal links. Preserve external references as typed references, not fake local FKs. Enforce nonnegative sequences/revisions and known state/assurance values through reviewed constraints. JSONB is for optional structured metadata, not an opaque whole transcript replacing queryable message rows.

Recommended query indexes: session/time, session/sequence (unique), source/speaker/time, and reference target/revision. Full-text/graph indexes are optional consumers, not capture prerequisites. Access is mediated by a least-privilege service using approved identity/domain/session mappings. Do not copy permissive grants from an unrelated historical migration or expose all records to every platform user.

## E. Import behavior

Verify supported schema, digest, authorized origin/scope, IDs/types/links and monotonic snapshot revision before transactional import. Register origins out of band; an artifact cannot grant its own trust/access. Reimport of identical bundle is a no-op with a receipt. Same immutable message/note/reference ID with different content is a conflict, not an overwrite. Later metadata revisions can update the current projection only after revision checks; preserve import history. Older snapshots never roll back newer state.

Do not infer deletions from missing records, partial exports or narrower access. Redaction/deletion requires a separate authorized lifecycle. Revoke or withhold central access when permission updates cannot be validated; stale exported membership is never sufficient authority.

Import acknowledgement/checkpoint advances only after the whole batch commits. Failed batches are retriable without duplicates. Source remains available while the center is down. Central edits to source-owned transcript rows are not allowed in this one-way design. Future production CoS writing is a distinct authorized origin/service workflow, not unplanned bidirectional replication.

Neo4j portability remains a required follow-up demonstration using a documented, tested standard-tool mapping with duplicate-safe rerun. Whether it is an initial-release gate requires the explicit scope disposition noted in the package entry point. No universal zero-transform claim or current import success is made.

Consolidation is not backup: it may omit keys, source bytes, operational journals and historical revisions. Restore requirements are specified separately.
