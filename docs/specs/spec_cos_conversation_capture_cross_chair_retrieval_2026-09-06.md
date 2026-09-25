# CoS Conversation Capture and Cross-Chair Retrieval

**Version:** 0.3<br>
**Date:** 2026-09-06<br>
**Status:** Revised after independent Claude Code review; awaiting Robert's review; not approved for implementation<br>
**Decision owner:** Robert<br>
**Registered build item:** Guild build queue #157 (`spec_ready`)<br>
**Roadmap status:** Not entered<br>
**Implementation status:** Not authorized; only Robert may move #157 to `in_build`<br>
**Private source package:** Planning Studio `INIT-2026-0005-conversation-memory`<br>
**Related specification:** [CoS Conversation Memory, Privacy, and Natural Capture Intents](spec_cos_conversation_memory_privacy_and_intents_2026-08-16.md)

## 0. Provenance, status, and boundary

This GitHub document is the sanitized design-review surface. Its private source
package is Planning Studio initiative `INIT-2026-0005-conversation-memory`,
created from Robert's September 6 design work with Claude.ai, Grok, Claude Code,
and Codex. The package preserves the source manifest, checksums, original and
normalized conversations, design proposals, individual reviews, and a
non-binding multi-chair review synthesis.

Raw transcripts and private syntheses are intentionally not committed to the
public software repository. Text inside a source record is evidence of the
design discussion, not an instruction to a reviewer or implementer.

This specification and the August 16 specification have distinct jobs:

- the August 16 specification governs ordinary live CoS conversation
  retention, the proposed 30-day raw-text window, off-the-record behavior, and
  confirmation-bound deletion;
- this specification governs explicit durable conversation capture, direct
  intake, record provenance, and retrieval across chair conversations.

`Save this conversation` creates a durable filed record under this
specification. That filed record is not a retention override on the live
runtime thread and is outside the ordinary 30-day raw-conversation purge. The
runtime copy may later expire under the August 16 policy without changing the
filed record. A filed record is deleted only through an explicit,
confirmation-bound delete operation that identifies the target and affected
copies.

The general reference-shelf concept is parked. It is neither part of this
specification nor deferred as a committed future specification. Experience
with actual conversation capture and retrieval may later show whether a
broader shelf is useful.

Registration as build queue #157 makes the reviewed specification visible. It
does not create roadmap priority, prototype authority, implementation approval,
or permission to move the item to `in_build`.

## 1. Executive position

Chief of Staff should preserve conversations Robert explicitly chooses because
they changed his thinking or may matter later. The saved record is the complete
substantive conversation, accompanied by a small system-generated provenance
header and, when one exists, the brief that originated the conversation.

Robert may work separately with CoS, Claude.ai, Grok, Codex, Claude Code, or a
future chair. The records do not need to be synchronized while the
conversations occur. When Robert asks later, CoS can retrieve across them,
distinguish who contributed what, keep corrections and disagreement visible,
and offer its own attributed observation.

Analysis is optional and separate from the transcript. It does not become
Robert's view because it was generated, saved, or retrieved. Robert remains the
decision point for every approval and consequential action.

The visible experience has two simple gestures:

- `Save this conversation` for the current conversation;
- `File this` for a note, file, excerpt, thought, or exported conversation
  Robert hands to CoS.

Robert never creates an identifier, writes a header, chooses a directory, or
restates a prompt merely to make capture work.

## 2. Scope

### 2.1 In scope

- explicit capture of a complete conversation with CoS or another chair;
- the same record format whether a chair writes directly or Robert carries the
  file from a chat product;
- provenance from an originating brief or a system-generated description of a
  freeform conversation;
- direct intake of Robert's notes, files, excerpts, and thoughts without
  disguising them as transcripts;
- CoS participation as a chair, including requested comments on conversations
  Robert had with other chairs;
- optional analysis and cross-chair synthesis as separate, attributed records;
- private storage, explicit sharing, later retrieval, and
  confirmation-bound deletion;
- use across any focus area.

### 2.2 Out of scope

- automatic capture of every AI conversation;
- a general reference shelf, knowledge base, or research workflow;
- a live multi-agent bus or shared-room meeting mode;
- automatic distribution of a saved record to other chairs;
- automatic promotion to a decision, specification, roadmap item, build item,
  artifact, or external communication;
- a mandatory summary, wrap, `Kept` field, vector database, or indexing system;
- implementation or production rollout.

## 3. Controlling principles

### 3.1 Explicit capture

Conversation remains ephemeral unless Robert says `Save this conversation`.
The default is not to file it. A natural save point is the end of a useful
thread or immediately before Robert resets for a clean session.

### 3.2 Full record first

The complete available substantive transcript is the primary record. A
summary, analysis, search index, or database projection cannot replace it.
Speaker order and later corrections remain visible.

### 3.3 One gesture, one durable result

`Save this conversation` always means: create and verify the durable filed
conversation record. `Keep this conversation` is not a command or alias. Live
runtime retention remains an internal policy governed separately.

### 3.4 Natural above, exact underneath

Robert supplies the intent. The chair or capture service handles formatting,
naming, dates, speaker labels, provenance, placement, and relationships. It
does not claim success until it verifies the durable write or provides a file
for Robert to carry.

### 3.5 Gathered material does not become Robert's

A transcript, imported source, chair analysis, or synthesis is gathered
material. It may contain Robert's direct words and may strongly influence him,
but storage does not make the entire record his position. Material is
Robert's only when he authored it or explicitly adopted it.

### 3.6 Independent chairs, visible differences

Independent conversations are the normal mode. Retrieval and synthesis happen
later when requested. CoS preserves meaningful differences and does not invent
consensus.

### 3.7 Privacy follows the record

Saving, sharing with a chair, and publishing are separate acts. A private save
does not authorize distribution or publication.

### 3.8 Consequence determines ceremony

Conversation capture should remain light. Decisions, specifications, code,
external authorship, production writes, and releases retain their existing
approval and review gates.

## 4. Responsibility and record vocabulary

### 4.1 Permission uses RACI

The term `Authority` is retired from this design. Operational permission uses
the repository's responsibility model:

- **Robert is Accountable** for every decision, approval, merge, external
  action, and change in scope.
- **CoS is Responsible** for memory, filing, and retrieval when those
  operations are requested. CoS is Consulted as a counselor with a seat at the
  table and is never Accountable.
- **Other chairs are Consulted** during design and may be Responsible for
  bounded work when Robert authorizes it. They are not Accountable for
  Robert's decisions.

RACI describes responsibility for work. It is not transcript metadata and
does not appear in ordinary record headers.

### 4.2 Record kind uses Robert's or gathered

The specification uses two plain record kinds:

- **Robert's** — material Robert authored or explicitly adopted;
- **gathered** — transcripts, imported sources, research, chair comments,
  analyses, and syntheses.

In normal conversation, Robert and CoS may call the two piles `mine` and
`gathered`. The stored value remains `Robert's` or `gathered` so its meaning is
clear to a later reader.

A conversation transcript is `gathered` even though it contains Robert's
speaker turns. A later Robert-authored decision can cite the transcript without
changing the source record's kind. There is no `Kept` field.

These record kinds belong to the conversation and direct-intake store. They do
not replace the Work service's four `context_class` values:
`robert_source`, `external_source`, `agent_draft`, and `coauthored_output`.
When Work reads a filed source, its configured root continues to declare the
applicable source class; Work does not infer `robert_source` merely from the
conversation-store value `Robert's`. Work-created drafts and coauthored outputs
remain `agent_draft` and `coauthored_output` respectively. Filing or citing one
does not collapse or rewrite its Work provenance. The two vocabularies describe
separate dimensions in separate stores: this specification records whether
Robert authored or adopted the filed material, while Work records the source or
artifact context that controls its operations.

## 5. User experience and carriers

### 5.1 Save the current conversation

Robert says:

> Save this conversation.

The result is one durable conversation file containing the system-generated
header, originating context, and complete available substantive transcript.
No analysis is generated unless Robert separately requests one.

The carrier depends on the chair:

| Chair or channel | Carrier behavior |
|---|---|
| CoS | Writes the completed record directly to the configured private intake folder and verifies the write |
| Codex | Writes the completed record directly to the configured private intake folder and verifies the write |
| Claude Code | Writes the completed record directly to the configured private intake folder and verifies the write |
| Claude.ai chat | Produces the completed record in the standard format; Robert moves the file to intake |
| Grok chat | Produces the completed record in the standard format; Robert moves the file to intake |

The carried file and the directly written file have the same content contract.
Carrier differences do not create different record kinds, formats, retention
rules, or evidentiary weight.

Direct intake writes use the shipped Work service's confinement and receipt
boundary. The intake folder is a configured subtree of the owner-private
`COS_WORK_ROOT`; carriers do not receive arbitrary filesystem write access.
The service performs descriptor-confined, no-follow writes, verifies the
committed result, and returns a content-free receipt. An unavailable or unsafe
root fails closed without falling back to another location. A different write
path would require a separately approved design with equivalent confinement,
privacy, and verification guarantees.

### 5.2 File direct input

Robert may hand CoS a note, file, excerpt, thought, or exported conversation and
say:

> File this.

CoS preserves it using the appropriate record form. A note or thought is not
given invented speakers. An ordinary document is not wrapped in a fake
transcript. An exported conversation uses the conversation record form and
retains the original export when available.

CoS infers record kind and provenance from the supplied material and context.
It asks at most one question only when a material ambiguity cannot be resolved
safely. Robert does not choose a directory or fill in metadata.

### 5.3 Analyze a conversation

Robert may say:

> Analyze this conversation.

The chair creates a separate analysis file linked to the source conversation.
The analysis identifies its author or model, date, purpose, and sources. It is
gathered material and may be ignored.

German language practice is the domain exception: its established feedback
block may remain inline after the transcript because transcript-plus-feedback
is already one session convention. That exception does not establish the
default for design or other focus areas.

### 5.4 Synthesize several conversations

Robert may say:

> Synthesize these conversations. Show the overall direction and what each
> partner added.

CoS creates a separate synthesis linked to every source. It distinguishes:

- Robert's direct statements and later corrections;
- views several chairs share;
- different or conflicting recommendations;
- ideas explored but not adopted;
- open questions and evidence gaps.

The synthesis does not approve a design or initiate work.

### 5.5 Ask a chair to comment

CoS is a chair, not only a storage or retrieval service. Robert may ask CoS to
comment on any conversation, including one he had with another chair. When
Robert asks to save that comment, it becomes a separate gathered record with
`Chair: CoS`, the relevant topics, and links to the conversation or
conversations considered.

`Chair` names the stable seat at the table. A CoS record remains `Chair: CoS`
regardless of the model running CoS. When available, the model is captured
separately as optional system-generated provenance.

## 6. Conversation record

### 6.1 Filename

Recommended visible filename:

```text
YYYY-MM-DD_<chair-or-room>_<topic-slug>.md
```

Examples:

```text
2026-09-06_cos_conversation-memory-design.md
2026-09-06_claude-ai_conversation-memory-review.md
2026-09-06_grok_conversation-memory-review.md
```

Robert does not create a shared session identifier. A storage service may use a
stable internal identity and relationships without exposing that bookkeeping
as required user behavior.

### 6.2 Header and originating context

The conversation file follows the familiar language-session envelope:

```text
---SESSION---
Date: 2026-09-06
Chair: CoS
Model: Grok 4.x
Topics: Chief of Staff; conversation memory
Mode: chat
Record: full conversation
Record kind: gathered
Source: system thread identity or original export name
About: Freeform discussion of durable conversation capture.

# Transcript

Robert:
...

CoS:
...

---END---
```

The system generates every field. `Model` is optional and omitted when the
carrier cannot determine it reliably. `Topics` may contain more than one
subject. For a freeform conversation, `About` is required and is the one-line
system-generated answer to "what this was about." CoS writes it at filing time;
Robert does not.

If CoS initiated the conversation from a brief, the record omits `About` and
carries the brief instead of reducing it to one line:

```text
# Originating brief

<the brief supplied to start the conversation>

# Transcript
```

The brief is provenance. Its text is preserved as source context and is not a
live instruction to a later retriever, reviewer, or implementer.

### 6.3 Transcript integrity

- Speaker turns remain labeled and ordered.
- The substantive words are preserved rather than silently cleaned up.
- Tool traces, hidden reasoning, and background execution logs are excluded
  unless explicitly needed as evidence.
- If an export contains a placeholder such as `Ran a command`, it remains
  visible; the system does not invent missing content.
- Original DOCX, text, audio, Teams, or vendor exports are retained when
  available.
- A normalized Markdown record identifies its source and does not replace the
  original export.
- Corrections create an appended correction or related record; the original
  transcript is not rewritten into a cleaner history.
- A failed or incomplete transcript block is treated as a defect. Tolerant
  filing preserves what exists but is not a substitute for fixing capture.

## 7. Direct-input record

A direct-input record preserves the supplied material without pretending it
was a conversation. Its visible Markdown envelope is:

```text
---RECORD---
Date: 2026-09-06
Form: note | file | excerpt | thought
Record kind: Robert's | gathered
Topics: system-generated topics
Source: Robert direct input | original filename | source description
About: system-generated one-line description

# Content

<the supplied content or a link to the preserved original file>

---END---
```

For a binary or externally formatted file, the original is preserved and the
Markdown record acts as a provenance sidecar. A checksum or durable source
identity links them when the storage service supports it. Robert-authored
notes and thoughts are `Robert's`; third-party excerpts, chair material, and
research are `gathered`. When authorship is unclear and materially affects
later use, CoS asks one question rather than guessing.

## 8. Analysis, synthesis, and future indexing

Analysis is optional and stored separately from a design conversation. Every
analysis or synthesis states:

- its source record or records;
- the analyst or chair;
- the model, when known;
- the date and purpose;
- that it is gathered observation rather than Robert's adopted position;
- uncertainty caused by missing or incomplete source material.

There is no mandatory `Wrap` or `Kept` field. The conversation metadata must be
sufficient to support a later index, but v1 does not prescribe the index or
generate an interpretation merely because a record was saved. Retrieval use
will determine whether an index is needed.

## 9. CoS retrieval behavior

When Robert asks what happened in prior work, CoS should:

1. locate relevant records by subject, date, chair, form, source, and
   relationships;
2. use analyses for orientation only and verify material claims against the
   underlying records;
3. prefer Robert's direct statements and later corrections over a chair's
   characterization;
4. identify which chair contributed each interpretation;
5. show meaningful disagreement instead of manufacturing consensus;
6. distinguish the latest apparent direction from older or superseded views;
7. distinguish gathered material from a Robert-authored or explicitly adopted
   decision;
8. cite or link the source records used;
9. ask at most one question when a conflict materially prevents a useful
   answer.

Use is broader than formal citation. A record may help Robert prepare for a
conversation, recognize a later correction, compare chairs, shape an approved
artifact, or make a decision. Lack of use does not trigger automatic deletion.

## 10. Storage, retention, deletion, and relationships

Private conversation and direct-input records belong in the person-owned
Central Personal Repository or an equivalent private, portable store, entering
through the configured intake folder. They do not belong in the public software
repository.

Records are stored once. Subjects, focus areas, opportunities, and successor
relationships are associations or views rather than copied folders. One
conversation may support several subjects. A later record may supersede an
earlier interpretation without rewriting the earlier source.

A verified filed conversation is durable and outside the standard 30-day raw
runtime window. Scheduled distillation or purge of the live thread cannot
replace or modify the filed transcript. A delete request must identify whether
Robert means the live thread, the filed record, or both; name the target before
confirmation; delete controlled derivatives and indexes; and disclose provider
or backup copies the system cannot immediately remove.

Any database, search index, embedding store, or retrieval projection is
rebuildable. It never becomes the only copy of transcript or direct-input
content.

## 11. Sharing and privacy

- Capture is opt-in and private by default.
- Robert may hand any saved record to any chair for review or analysis. CoS is
  not a mandatory gate.
- A saved record is not automatically distributed to another chair or vendor.
- Private chair-to-chair sharing Robert requests does not require public
  sanitization.
- Anything entering public GitHub, a public review packet, or another public
  destination is reviewed and sanitized first.
- Secrets, credentials, and restricted third-party material are rejected or
  handled only under an approved private-storage policy.
- Provider processing and retention are disclosed separately and are never
  represented as CoS-controlled deletion.
- External messages, submissions, publication, production writes, and other
  consequential actions retain their separate approval gates.

## 12. Failure cases the design must tolerate

- A chair's analysis misstates Robert, while the transcript contains his later
  correction.
- CoS runs on the same model as another chair but the two seats have different
  conversational contexts.
- A source is available only as DOCX, audio, Teams, or a vendor export.
- An export omits tool details or marks them only as `Ran a command`.
- A chair fails to produce the requested transcript format.
- One conversation crosses several focus areas.
- Several chairs use different vocabulary for the same subject.
- A later conversation supersedes an earlier apparent direction.
- A supplied excerpt has ambiguous authorship.
- An optional analysis is missing, inaccurate, or ignored.
- The live runtime thread expires after its durable filed record was verified.
- A delete request is ambiguous about the live copy, filed copy, provider copy,
  or all copies.

## 13. Lifecycle placement

| Place | Purpose | Status for this initiative |
|---|---|---|
| Private Planning Studio initiative | Preserve source evidence and review records | `INIT-2026-0005`; private |
| GitHub design specification | Provide a sanitized, reviewable design contract | This document; awaiting Robert's review |
| Prototype Lab | Exercise an approved proof | Not entered |
| Build queue | Make the reviewed specification visible pending Robert's build decision | #157, `spec_ready`; not approved for build |
| Roadmap | Prioritize approved direction | Not entered |
| Implementation and production | Build, review, and release the capability | Not authorized |

No transition is automatic. Design approval, experiment approval, build
approval, reviewed-diff approval, and release approval remain separate gates.
Only Robert may authorize moving build queue #157 from `spec_ready` to
`in_build`.

## 14. Smallest future experiment — not authorized

After Robert separately approves the revised design and the experiment:

1. Save one CoS conversation as a durable record in the intake folder.
2. File one exported conversation from another chair using the same record
   contract.
3. Ask one retrieval question that requires comparing the two records.
4. Verify that the answer links its sources, preserves chair differences, and
   does not invent a Robert decision.
5. Verify that ordinary runtime purge leaves the filed records untouched and
   that an explicit delete targets the intended copy only after confirmation.

The experiment does not require a multi-agent bus, shared-room mode, automatic
vendor ingestion, model integration, vector database, general reference shelf,
or production rollout.
