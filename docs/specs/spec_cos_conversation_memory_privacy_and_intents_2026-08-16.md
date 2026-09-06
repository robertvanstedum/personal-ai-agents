# COS Conversation Memory, Privacy, and Natural Capture Intents

**Registered:** Build queue #150
**Date:** 2026-08-16 · **Revised:** 2026-09-06
**Status:** In-process design; partially superseded (see below). Not authorized for implementation
**Owner / decision point:** Robert
**Initial consumer:** Chief of Staff / COS Agent A
**Related:** Specs #133, #148, and #149

## 0. Revision note — 2026-09-06

This specification predates the Work service, which was designed, independently
reviewed and merged on 4–5 September (PRs #200 and #201, `domains/cos/work/`).
Three of its sections are now answered by shipped code rather than open:

| Section | Disposition |
|---|---|
| §2 Governing storage rule — JSON first, database projection second | **Superseded.** What shipped is stricter: files only, no database, immutable source and artifact revisions, SHA-256 on every stored object. There is no projection to reconcile |
| §3 Platform-owned data boundaries | **Largely superseded** by the merged root configuration, path confinement and per-root provenance classes |
| §8 Verified operation rule | **Implemented.** The Work service executes the operation and returns a content-free receipt; nothing reports success without a committed terminal marker read back from disk |

**What this specification now covers** is the part the Work service does not:
what Chief of Staff *forgets*. Conversation retention, the raw-text window,
distilled-summary lifetime, off-the-record mode, and the deletion commands.
Those are destructive, confirmation-bound operations on the conversation itself,
and they are deliberately kept separate from the reference and artifact store.

**What has moved out.** §7's natural capture intents ("take a note to …",
"file this") belong with the Chief of Staff reference shelf, which is in design
separately (`_working/cos-strategy-2026-09-06/`). They are the same idea as
Robert's requirement to hand something over without choosing a directory for it.

**Effect on the blocking decisions in §11:** decision 5, whether agenda and
research share one JSON store, is largely settled by the merged file-first
design and the subject-scoped direction of the shelf work. The remaining
decisions — the retention window, summary lifetime, backup expectations and
runtime session cleanup — are unchanged and still Robert's.

## 1. Purpose

Give Robert a natural text-and-voice Chief of Staff conversation while keeping
retention, explicit notes, agenda items, research requests, privacy controls,
and deletion under MinimoI platform authority rather than model claims or an
agent runtime's private file format.

This is follow-up work after the COS Agent A beta. It does not gate the beta
release provided explicit single-turn notes remain receipt-backed and the
limitations are documented.

## 2. Governing storage rule

**Authoritative structured text files are written first. A database is a
rebuildable projection written second.** A PostgreSQL error must not prevent or
invalidate a successful authoritative file write.

Any exception requires an explicit design decision that names:

- why database-first storage is required;
- the authoritative source and recovery procedure;
- behavior when the database is unavailable;
- reconciliation and backup ownership.

An incidental implementation choice, an existing table, or convenience is not
a design decision. Existing COS database-first/file-fallback paths are gaps to
be corrected or explicitly decided.

## 3. Platform-owned data boundaries

Use per-user, provider-neutral JSON. OpenClaw session files are runtime working
state, not the MinimoI archive or analytics contract.

```text
data/cos/conversations/user_<id>/YYYY/MM/<conversation-id>.json
data/cos/memory/user_<id>.json
data/cos/agenda/user_<id>.json
data/cos/projection_outbox.jsonl
```

Each conversation records stable IDs, timestamps, channel (`text` or `voice`),
speaker, normalized text, retention class, and links to explicitly promoted
items. Raw audio is not stored. Operational logs must not contain full message
or transcript content.

After the JSON write succeeds, an idempotent projector may upsert PostgreSQL.
Reconciliation compares authoritative IDs and hashes to the projection.
Deletion writes a content-free tombstone so projections and downstream indexes
cannot resurrect removed content.

## 4. Conversation continuity

Typed and transcribed voice turns use the same COS conversation ID. Robert may
begin in text, enable voice, and leave voice open for a natural back-and-forth.
Switching transport must not create a new logical conversation.

COS may summarize today's plan from the active conversation and authorized COS
agenda/memory sources. It must distinguish retrieved plans from inference.

## 5. Retention classes

| Class | Intended behavior |
|---|---|
| `standard` | Raw conversation retained for a configured window, then distilled and purged |
| `keep` | Robert explicitly preserves the conversation |
| `off_record` | No platform conversation archive or derived summary |
| `explicit_item` | Note, decision, action, question, research, or revisit item retained until resolved or deleted |

Proposed initial standard window: **30 days of raw text**. Before raw purge, a
scheduled job may create a nonverbatim pattern summary containing only durable
value. Compression is a storage optimization, not a privacy control. Privacy
requires purging raw content.

The 30-day window, summary lifetime, and backup-expiration disclosure require
Robert's acceptance before implementation. Configuration owns these values;
they are not hardcoded in domain logic.

## 6. Privacy and deletion commands

The platform, not the model, executes these operations and returns a receipt.

- **“Off the record.”** Start a non-retained segment and acknowledge the mode.
- **“Back on the record.”** End the private segment and begin a retained one.
- **“Keep this conversation.”** Apply a retention override.
- **“Delete this conversation.”** Identify scope, request confirmation, then
  delete authoritative content, projection rows, derived summaries, and indexes.
- **“Delete my last conversation.”** Name the target date/title before confirmation.
- **“Don't save that.”** Clarify whether Robert means the last explicit item or
  the current conversation when the target is ambiguous.

Deletion is destructive and therefore confirmation-bound. COS must disclose
that expired backups and provider-side retention follow their own documented
windows. The build must discover and use a supported OpenClaw session deletion
or rotation boundary; directly editing OpenClaw's private JSONL/SQLite files is
prohibited.

An `off_record` claim is not allowed until tests prove that MinimoI platform
storage, Agent A runtime persistence, projections, analytics, and backups obey
the declared contract. Provider processing or retention must be described
separately and must never be represented as MinimoI-controlled deletion.

## 7. Natural capture intents

Command phrases, record types, and topical tags are separate concepts. The
starter vocabulary is deliberately small and explicit.

| Natural phrase | Platform record type | Result |
|---|---|---|
| “Save this…”, “Note this…”, “Remember this…” | `note` | Durable reference |
| “Add this to today's agenda…” | `action` | Pending agenda item |
| “Record this decision…” | `decision` | Durable decision |
| “Add a research item…” | `research` | Research queue item; no automatic external action |
| “Open question…”, “I'm unsure about…” | `question` | Unresolved question after clarification if needed |
| “Come back to this…”, “I'd like your opinion later…” | `revisit` | Follow-up item |

“Take a note” supports a deterministic two-turn interaction: COS asks what to
record, and the next turn is saved by the platform with the same correlation
ID. Voice remains open. “Take a note to …” may be accepted as a one-turn
command when the content boundary is unambiguous.

Ambiguous language does not silently mutate state. For example, “Can you
research it?” may mean research now or create a future item; COS asks which.

Topical tags such as `cos`, `voice`, `production`, or `today` may be suggested
automatically. Robert can override them with “Tag this as …”. Tags never grant
authority or substitute for an explicit record type.

## 8. Verified operation rule

COS may say saved, recorded, deleted, retained, off the record, or queued only
when the response includes a platform operation receipt. Agent text is not
evidence that an operation occurred. Retries use stable operation IDs and are
idempotent.

## 9. Delivery phases

1. Replace COS Markdown/database-first writes with per-user authoritative JSON
   stores while preserving the existing UI and importing current notes.
2. Add a best-effort PostgreSQL projector, outbox, deletion tombstones, and
   reconciliation report.
3. Add provider-neutral conversation capture for typed and transcribed voice
   turns with configurable retention.
4. Add natural note, agenda, decision, question, research, and revisit intents.
5. Add keep/off-record/delete controls after supported Agent A session cleanup
   is proven.
6. Add scheduled distillation/purge and transparent backup-expiration reporting.

Each phase requires a reviewed diff and dev acceptance before production.

## 10. Acceptance criteria

- text-to-voice switching preserves one conversation and voice stays open;
- “Take a note” completes naturally without stopping voice;
- every mutation has a durable, correlated platform receipt;
- JSON remains readable and authoritative during PostgreSQL outage;
- projection reconciliation reports zero unexplained drift;
- ordinary raw conversations expire according to configuration;
- explicit items survive raw-conversation purge;
- deletion cannot be undone by a later projection rebuild;
- off-record behavior is tested across platform, runtime, analytics, and backups;
- no code reads OpenClaw private persistence as the application contract;
- operational logs contain metadata only, never full conversation text or audio.

## 11. Decisions required before implementation

1. Accept or change the proposed 30-day raw-conversation window.
2. Set the lifetime and review surface for distilled pattern summaries.
3. Define backup deletion expectations and disclosure language.
4. Confirm supported OpenClaw session deletion/rotation behavior.
5. Decide whether agenda actions and research items share one JSON store or
   remain separate authoritative stores with linked IDs.
