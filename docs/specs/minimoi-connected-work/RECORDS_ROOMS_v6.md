# Records & Rooms — specification revision 6

19 September 2026 · Detailed baseline under the [current direction](README.md).

## 1. Scope

Provide person-owned rooms, bounded recorded sessions, attributed durable contributions, source-linked notes and portable transcripts. Keep the native UI, support explicit agent requests and preserve evidence without introducing autonomous execution. Ordinary CoS chat stays outside capture unless an authorized recording session deliberately includes its submitted contributions.

This revision consolidates the prior Working Room and SPEC-005 design for this capability. It does not change Mini-moi Work's effect set, automatically ingest private history, replace general conversation memory or declare central production access implemented.

## 2. Entities and state

- Room: durable topic, stable ID and permission-safe metadata; no automatic running meeting. Room membership grants no sibling-session access by implication.
- Session: parent room, stable ID, purpose, participants, explicit recording acknowledgment. Active accepts authorized discussion; paused retains history but accepts no new discussion; closed is terminal. A later meeting has a new ID. Session closure is not a universal cancellation command for unrelated work.
- Record: append-only accepted contribution or mapped lifecycle/correction record, source identity and sequence, submitting actor, speaker, optional agent/model and source/ingestion times. Unknown historical values remain unknown. Corrections reference originals; they do not overwrite them.
- Note: separately versioned meeting note, decision summary, next steps, executive brief, build note or reasoning record; attributed author, source coverage and supersedes link. Agent-authored drafts are permitted and labelled; reconstructed intent is not original speech.
- Reference: source/record/note or exact artifact-version link, hash when available, access-controlled target. A reference does not prove its target was opened.

Capture includes only submitted, durably accepted records during acknowledged recording, not hidden reasoning, missing external intervals or unsubmitted drafts. Export coverage makes omissions explicit.

## 3. Authority and transactional writes

SQLite is the working authority for session records. All writes cross an authenticated application boundary; no shared file opened by competing agents. Short transactions serialize submissions and commit record/receipt together. Stable request IDs and fingerprints distinguish safe replay from conflicting reuse. Reconcile uncertain effects before retrying; never blindly rerun inference to resolve a write timeout.

Keep authenticated submitter, displayed speaker, logical agent, reported model and assurance separate. A relayed answer remains a relay with declared source. A display name or run-ID string alone is not verified runtime evidence. Preserve legacy IDs, receipts, references, access boundaries and immutable historical text through schema changes.

Source identity `(source_instance_id, record_id, revision)` is separate from current writer authority. Restore preserves identity; deliberate independent forks have new origins. Future authority transfer follows the directional fencing rule and is not an R1 migration operation.

## 4. Conversational concurrency

Server-side routes, not model judgments, allow an explicitly authorized `message` or `checkpoint` reply based on an earlier snapshot. Persist source IDs/through-sequence and identify changed context. Derived informational notes may describe an older explicit coverage range.

`decision`, `task`, `task_update`, approvals and any external-effect path remain strict on relevant context/version. New or unknown kinds default strict. Conversation routes have no execution authority even if message text demands an action. Recheck current access, capture state and request authority at write time for every route. Relaxed freshness never bypasses revocation, pause, closure or privacy policy.

Record kinds used by the API and exported vocabulary need an explicit versioned mapping; no heuristic inference from message text. Formal Work disposition is separately recorded; a missing disposition link is visible, not assumed.

## 5. Readable artifacts

Publish automatically on accepted export-relevant changes, with coalescing, and on close. Closure commits with durable export-needed state or an equivalently tested recovery mechanism. Rendering/hashing/uploading does not hold a long write transaction. Startup reconciliation repairs pending or absent/outdated output for open, paused and closed sessions. A failed export cannot lose already-committed discussion.

Use immutable versioned bundles containing `transcript.json`, `transcript.md`, `manifest.json`. Derive all from one consistent snapshot; validate checksums before same-filesystem atomic publication. A latest pointer can change atomically; older bundles remain identified. Never expose a half-bundle as complete or overwrite the last good version with failed output.

In-progress output is mandatory. Declare snapshot time, source revision, through-sequence, coverage and session state. Lifecycle incompleteness is distinct from permission-filtered coverage: an in-progress owner snapshot can include every accepted record through its checkpoint. No fixed 60-second availability guarantee; pending/failure/revision lag must remain visible and recovery must make progress. Quiet rooms produce no transcript.

Closed discussion is final; later authorized notes/references produce a new bundle revision without reopening it. Publication itself must not add a transcript event and cause an export loop. Hand-edited exported files are external derivatives, not new authoritative history.

Markdown uses a timestamp/speaker/kind/stable-record-ID header for each contribution, followed by exact text with multiline layout preserved. Sequence order matches JSON. Source time is used if known; ingestion fallback is labelled. Lifecycle records and notes are visibly distinct. Escape/render untrusted content safely; no scripts or commands execute from a transcript. The header declares IDs, coverage and freshness. JSON is schema-defined, database-loadable ordinary UTF-8 JSON, not opaque serialized text or base64 attachments; see [data contract](TRANSCRIPT_CONTRACT.md).

## 6. Access and central transfer

Artifacts are private by default, published only into authorized locations. Session isolation applies to read/search/export/receipt retrieval. Downloaded copies cannot be recalled reliably; state that limitation.

Migration categories do not authorize sync. Adopt sensitivity defaults `private_personal` for Career sources and `restricted` for employer/customer internal material pending review. These are not an exhaustive taxonomy. Unclassified material is denied. A synchronization approval record must name: approval ID/version, bounded source IDs/versions or explicit collection scope, sensitivity, purpose, destination, allowed principals/access, retention, revocation and recall limits, approver, approval date, validity if applicable and current state. Changes outside the approved scope require new authorization. Approval of an artifact is not permission to transfer it externally.

A source cannot self-authorize through an imported manifest. Check current policy before transfer and service access; historic membership is not permission. Deny or withhold access when approval updates cannot be validated. Restricted sources need explicit handling clearance; CoS's interest or domain ownership never suffices. Even reference labels/paths need authorized disclosure.

Central read is one-way consolidation with destination receipt and checkpoint. Local capture continues during central outage. Imported messages retain origin/IDs and cannot be edited centrally. Same immutable ID with different content is a conflict; repeats are no-ops, not duplicates. No deletion inferred from omissions. Central write participation requires a separately accepted route, not a writable projection.

## 7. Agent integration

Use the actual configured CoS agent through a reviewed connector, not a platform acknowledgment masquerading as an agent. Derive request authorization from trusted server authentication, never a body boolean. Bound the authorized snapshot, record omissions/coverage and isolate session/request model context from unrelated conversations.

Journal the attempt before inference and generated content before Records delivery. Preserve distinct platform request ID, optional runtime execution ID and authoritative Records receipt ID. On uncertain delivery, reconcile the existing request and exact persisted payload; do not make a new model call automatically. Failures and disconnected participants are displayed honestly.

Effective tool restrictions must be verified before private material is forwarded. Prompt instructions are not enforcement. Development synthetic tests use approved non-private sessions. Development evidence cannot establish production participation or laptop-off capability. The earlier exact connector pass remains evidence for that candidate only; permissive freshness and any HTTP wiring require new tests/review.

## 8. Acceptance boundary

Use [R1 and delivery gates](OPERATIONS_ACCEPTANCE.md). A readable local room is not the full unattended harness. Schema/contract tests with doubles do not prove live runtime participation. The independent runner works on a frozen build and Robert accepts or rejects the report. Public documentation is not production deployment, live migration, permission for private sync or blanket authorization to alter another agent's checkout.
