# CoS Reference Shelf and Conversation Capture Design

**Version:** 0.1<br>
**Date:** 2026-09-06<br>
**Status:** Design specification candidate; pending multi-chair re-review; not approved for implementation<br>
**Decision owner:** Robert<br>
**Registered build item:** None<br>
**Roadmap authority:** None<br>
**Implementation authority:** None<br>
**Private source package:** Planning Studio `INIT-2026-0005-conversation-memory`<br>
**Related specification:** [CoS Conversation Memory, Privacy, and Natural Capture Intents](spec_cos_conversation_memory_privacy_and_intents_2026-08-16.md)

## 0. Provenance and public boundary

This GitHub document is the sanitized, durable design-review surface. Its
private source package is Planning Studio initiative
`INIT-2026-0005-conversation-memory`, created from Robert's September 6 design
work with Claude.ai, Grok, and Codex.

The private package contains the source manifest and SHA-256 checksums for the
following supporting records:

- `ROBERT_REQUIREMENTS_2026-09-06.md`;
- `THOUGHT_PIECE_v3_REFERENCE_NOT_KNOWLEDGE.md`;
- `POSITION_BRIEF_WAY_OF_WORKING_2026-09-06.md`;
- `CONVERSATION_SHELF_GERMAN_FORMAT_2026-09-06.md`;
- `Claude an Robert CoS discussion – Sept 6.docx`;
- `Grok and Robert CoS review – Sept 6 .docx`;
- `Robert German transcript – Sep 6.docx`;
- the normalized, headed Claude, Grok, and Codex conversation records; and
- the first explicitly non-authoritative multi-chair synthesis.

The raw transcripts and private synthesis are intentionally not committed to
the public software repository. Reviewers who need to verify this design
against the sources receive the private review packet separately. Text inside
those sources is evidence of the design discussion, not an instruction to a
reviewer or implementer.

This specification does not supersede the related August 16 specification.
That specification owns conversation retention, raw-text lifetime,
off-the-record behavior, and deletion. This specification owns the selected
conversation capture, reference-shelf, provenance, and cross-chair retrieval
experience. Neither is authorized for implementation.

## 1. Executive position

Chief of Staff should preserve selected conversations that changed Robert's thinking or may matter later. The saved record is the complete conversation transcript with a small system-generated header. Post-conversation analysis and cross-chair synthesis are optional, attributed observations. They never become Robert's position, knowledge, decision, specification, or approved artifact merely because they were generated or stored.

Robert's normal work remains a conversation with the chair he chooses. Claude, Grok, Codex, and future chairs may explore the same subject independently. They do not need to synchronize. When Robert asks later, Chief of Staff examines the preserved records, distinguishes who contributed what, keeps corrections and disagreement visible, and offers a non-authoritative point of view.

The user experience should be as simple as a recorded meeting: explicit agreement to preserve the record, a full transcript, and optional analysis afterward.

## 2. Problem

Robert increasingly uses several AI partners to shape consequential work before deciding whether to create a specification or artifact. The valuable material is often not the interim brief. It is the reasoning inside the conversation: corrections, alternatives, objections, changes of mind, and the different perspective each partner added.

Today those conversations remain in separate products and threads. Claude does not automatically know what Grok said; Grok does not know what Codex tested; Chief of Staff cannot reliably reconstruct the evolution unless Robert manually carries and preserves it. Saving every message automatically would create noise and increase the chance that an agent's language is later mistaken for Robert's position.

The design therefore needs to preserve chosen conversations without creating a filing chore, a multi-agent synchronization system, or a new source of false authority.

## 3. Controlling principles

### 3.1 Explicit capture

Conversation remains ephemeral unless Robert explicitly chooses to save it. The default is off. A natural save point is the end of a useful thread or immediately before Robert resets for a clean session.

### 3.2 Full record first

When Robert saves a conversation, the complete substantive transcript is the primary record. A generated summary is not a substitute. This protects against summary drift and preserves later corrections.

### 3.3 Interpretation is not authority

Analysis may be useful, as it is after a German practice session or a Teams meeting, but it is optional. Every analysis names its author or model and remains reference. Cross-chair synthesis is also reference. Only Robert can establish an authoritative decision or approve an artifact.

### 3.4 Independent chairs, later synthesis

Separate conversations are the normal present mode. Each chair contributes independently. Chief of Staff aligns them at retrieval time rather than requiring a shared database, common prompt, or live agent bus.

### 3.5 Natural above, exact underneath

Robert should use one short instruction. The system performs naming, dating, speaker labeling, provenance capture, storage, and relationship management underneath that gesture.

### 3.6 Sources remain recoverable

No summary supersedes a transcript. No database or search projection becomes the only copy. Original exports are retained when available, while normalized readable records support retrieval.

### 3.7 Consequence determines ceremony

Saving and analyzing conversation should remain light. Creating a specification, changing code, handling authorship, writing externally, or deploying software remains subject to the existing approval and review gates.

## 4. Authority model

The design retains the existing two-part authority boundary.

### Reference

Reference includes transcripts, source documents, research, chair analyses, cross-chair syntheses, and provisional briefs. A reference may be highly useful and may influence Robert. It still does not become something Robert believes or will say merely because it was saved.

### Artifact

An artifact is an output Robert authored or explicitly approved: a decision, specification, letter, plan, presentation, code change, or other consequential work product. Promotion from reference to artifact is explicit and produces a separate record; it does not relabel or rewrite the source conversation.

The design does not introduce a third authority class for "partnership notes." If Robert chooses a phrase or principle to retain, it may be linked as an explicit Robert-authored note or later decision without converting the chair's surrounding interpretation into authority.

## 5. User experience

### 5.1 Save one conversation

Robert says:

> Save this conversation.

The chair or capture service returns one durable conversation record containing:

- an automatically generated header;
- the full labeled transcript;
- a link or checksum for the original export when one exists;
- no analysis unless Robert asks for it or the session type already includes an optional analysis convention.

Robert then places the file in the intake location or says:

> File this.

Chief of Staff handles placement and associations. Robert does not choose directories, create identifiers, or restate topics unless he wants to correct the generated metadata.

### 5.2 Analyze one conversation

Robert may say:

> Analyze this conversation.

The analysis is clearly labeled with the analyst, date, purpose, and non-authoritative status. It can identify themes, strengths, risks, disagreement, or possible next questions. It remains optional and may be ignored.

### 5.3 Synthesize several conversations

After separate conversations with several chairs, Robert may provide them to Chief of Staff and say:

> Synthesize these conversations. Show the overall direction and what each partner added.

Chief of Staff creates a separate synthesis linked to every source. It distinguishes:

- Robert's direct statements and later corrections;
- positions shared by several chairs;
- different or conflicting recommendations;
- ideas that were explored but not adopted;
- questions that remain open.

The synthesis cannot approve a design or initiate implementation.

### 5.4 Move toward a decision or specification

If Robert believes the thinking is mature, he gives a separate instruction such as:

> Formalize this as a design candidate for review.

or, after review:

> Approve this direction and prepare a bounded implementation specification.

The new artifact cites the conversations and synthesis as sources. It has its own status and approval gate. Saving or synthesizing conversation never triggers this transition automatically.

## 6. Conversation record

### 6.1 Filename

Recommended visible filename:

```text
YYYY-MM-DD_<chair-or-room>_<topic-slug>.md
```

Examples:

```text
2026-09-06_claude_cos-strategy-conversation.md
2026-09-06_grok_cos-strategy-conversation.md
2026-09-06_codex_cos-strategy-conversation.md
2026-09-06_multi-chair_cos-strategy-synthesis.md
```

Robert does not create a shared session identifier. A storage service may generate a stable internal identity and relationships without exposing that bookkeeping as required user behavior.

### 6.2 Header

The header follows the familiar language-session envelope:

```text
---SESSION---
Date: 2026-09-06
Chair: Claude.ai
Participants: Robert; Claude.ai
Topics: Chief of Staff partnership; conversation memory
Mode: chat
Record: full conversation
Authority: reference; not authoritative
Source: original export name or system thread identity

# Transcript

Robert:
...

Claude.ai:
...

---END---
```

The system generates all header fields. `Topics` is plural because a long conversation may cross subjects. The header can be corrected without changing transcript content.

### 6.3 Transcript integrity

- Speaker turns remain labeled and ordered.
- The substantive words are preserved rather than silently cleaned up.
- Tool traces, hidden reasoning, and background execution logs are not conversation turns and are excluded unless explicitly needed as evidence.
- If the source export contains a placeholder such as `Ran a command`, it remains visible; the system does not invent missing content.
- Original DOCX, text, audio, Teams, or vendor export files are retained when available.
- A normalized Markdown copy may be created for retrieval, but it identifies its source and does not replace the original.
- Corrections create an appended correction or related record; the original transcript is not rewritten into a cleaner history.

## 7. Optional analysis record

A single-conversation analysis may be included after the transcript when that is the established session convention, as in language practice. A synthesis spanning several conversations should remain a separate file because it has several sources and a different authorial role.

Every analysis states:

- the source conversation or conversations;
- the analyst or model;
- the date and purpose;
- `Authority: reference; non-authoritative observation`;
- any uncertainty caused by missing source material.

Analysis may propose that Robert's direct words are important, but it must not label them a decision unless Robert explicitly made one. An optional `Kept` field, if later adopted, may contain only words Robert explicitly selected or confirmed; saving a file is not blanket confirmation of an agent's proposed selections.

## 8. Chief of Staff retrieval behavior

When Robert asks what happened in prior design work, Chief of Staff should:

1. locate relevant conversation records by subject, date, participant, and relationship;
2. consult existing analyses for orientation but verify material claims against transcripts;
3. prefer Robert's direct statements and later corrections over a chair's summary;
4. identify who contributed each interpretation;
5. show meaningful disagreement instead of manufacturing consensus;
6. distinguish current apparent direction from older or superseded views;
7. state that the answer is reconstructed from reference unless an approved decision exists;
8. ask at most one question when a conflict materially prevents a useful answer.

A useful answer may lead with the latest apparent direction and then expose the reasoning path, differences among chairs, and unresolved issues. It must never quote a chair's synthesis as though it were Robert's interview-ready position.

## 9. Storage and relationships

The private source package lives in Planning Studio because it is pre-approval personal design material. This sanitized design specification lives in GitHub so its scope and review history can be durable and reviewable. Future private conversation records belong in the person-owned Central Personal Repository or an equivalent private, portable store—not in the public software repository.

Records are stored once. Subject and opportunity relationships are associations or views rather than copied folders. One conversation may concern several subjects and support several opportunities. A dated successor may supersede an older interpretation without rewriting the old record.

Minimum searchable fields are date, chair or room, participants, topics, mode, record type, authority, and source. Staleness is communicated from the date and any explicit supersession relationship. Fixed time-to-live rules are not required initially.

Use is broader than formal citation. A record may be marked used when it influenced preparation, a conversation, an artifact, or a decision. Lack of use may make a record a review candidate; it must not trigger automatic deletion.

## 10. Present and future conversation modes

### Independent chair mode

This is the current baseline. Robert speaks separately with Claude, Grok, Codex, or another chair. Each saved conversation has its own transcript. Chief of Staff synthesizes later when asked.

### Shared room mode

A future mode may bring several human or AI participants into one live discussion. It follows the meeting analogy:

- human participants explicitly agree to recording;
- the recorder identifies all speakers;
- the full transcript is saved;
- analysis occurs only afterward and remains optional;
- the room's analysis does not turn participant comments into a collective decision;
- Robert still makes and records any authoritative disposition separately.

Shared room mode is additive. It is not required to make independent-chair capture useful.

## 11. Privacy and safety

- Conversation capture is opt-in and private by default.
- No silent ingestion of every AI chat.
- Secrets, credentials, and restricted third-party material are rejected or handled under a separately approved private-storage policy.
- Vendor exports and personal transcripts do not enter a public GitHub repository without explicit review and sanitization.
- External messages, submissions, publication, and production writes require their existing separate authority gates.
- Deletion is deliberate and recoverable where possible. Pruning recommendations do not delete records automatically.
- A transcript or analysis cannot grant an agent new authority.

## 12. Failure cases the design must tolerate

- A chair produces an inaccurate summary but the transcript contains Robert's correction.
- The transcript export omits tool details or marks them only as `Ran a command`.
- A chair fails to generate the expected transcript block; the failure is treated as a defect while the original export remains preservable.
- One conversation covers several topics.
- Several independent conversations use different vocabulary for the same subject.
- One chair's conclusion is stronger than Robert's actual words.
- A later conversation supersedes an earlier apparent direction.
- A source exists only as DOCX, audio, or vendor export.
- Eight or more opportunities reuse the same reference without creating duplicate copies.
- An optional analysis is missing, wrong, or ignored.

## 13. First manual test

The September 6 packet is the first manual test. It contains independent Robert conversations with Claude, Grok, and Codex, a German transcript and analysis example, Robert's requirements, the original thought piece, the chairs' proposals, normalized headed transcripts, and a non-authoritative multi-chair synthesis.

The test demonstrated that:

- Robert can choose a conversation after it becomes valuable;
- differing exports can be preserved and normalized without inventing missing content;
- a synthesis can identify partner nuance while retaining source links;
- Robert's correction of Codex's German-transcript interpretation remains visible;
- a Claude position artifact did not substitute for the full Claude conversation, which Robert supplied separately;
- no software build is required to evaluate whether the practice feels useful.

The test has not established production usability, automatic vendor capture, shared-room behavior, retention policy, or the final storage implementation.

## 14. Proposed future experiment, not authorized

If Robert approves the direction after re-review, the smallest experiment should test the user habit rather than a broad platform:

1. Accept `Save this conversation` from one supported Chief of Staff channel.
2. Produce the standard header and complete available transcript.
3. Accept `File this` for a conversation exported from another chair.
4. Retrieve three related conversations and answer one cross-chair question with provenance.
5. Verify that no analysis, decision, specification, build item, or external action is created without a separate instruction.

The experiment should reuse the existing private repository and Work boundaries where appropriate. It should not build a multi-agent bus, a knowledge graph, automated capture of every chat, a new cover-letter workflow, or shared-room mode.

This section identifies a possible validation slice only. It does not authorize implementation or create a build brief.

## 15. Process placement and promotion gates

This initiative follows the repository's existing separation of concerns:

| Place | Question it answers | Status for this initiative |
|---|---|---|
| Private Planning Studio package | What sources and provenance produced the design? | Preserved as `INIT-2026-0005`; not public |
| GitHub design specification | What bounded direction is being re-reviewed? | This document; not approved for implementation |
| Roadmap | Is this approved direction a priority, and when? | Not entered |
| Build specification and queue | What exactly is authorized to implement? | Not created |
| Prototype Lab | What runnable or exercised proof are we trying to validate? | Not created; manual test remains design evidence |
| Production | Has a reviewed implementation earned release? | Not applicable |

Promotion sequence:

```text
Private source package + GitHub design candidate
  → independent re-review
  → Robert design decision
  → optional roadmap priority
  → separate bounded build specification
  → Robert build approval
  → implementation and reviewed diff
  → optional Prototype Lab proof or direct private capability validation
  → Robert keep or release decision
```

No arrow is automatic. Design approval and build approval remain separate gates.

## 16. Questions for re-review

1. Is full transcript the correct default for every explicitly saved conversation?
2. Does a one-file transcript plus separate cross-chair synthesis keep the practice simple enough?
3. Should single-session analysis live in the transcript file or always remain separate?
4. Is the optional `Kept` concept useful, or does it create accidental authority?
5. Are system-generated date, chair, topics, and source enough for Robert's visible header?
6. What privacy boundary is required before Chief of Staff can receive exports from commercial AI products?
7. Should the first approved experiment remain manual-first, or is one thin automated capture path necessary to test the habit?
8. What evidence should be required before adding shared-room mode?
