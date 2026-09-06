---
id: 01M1HMMRC34T7KPAGA9RFKAJE5
series: planning-studio-central-personal-repository-design
slug: planning-studio-central-personal-repository-design--9rfkaje5
type: note
kind: design
title: "Planning Studio and Central Personal Repository — consolidated design candidate"
version: 0.9
created: 2026-09-02
authors: [robert, codex]
review_inputs: [claude-design-review-planning-studio]
status: design_review
decision_owner: robert
roadmap_authority: none
implementation_authority: none
release_target: null
tags: [planning-studio, central-repository, cos, curator, guild, portability, provenance]
---

# Planning Studio and Central Personal Repository

## Consolidated Design Candidate v0.9

This document consolidates the initial Planning Studio package, Claude's
independent review, Robert's concurrence with that review, and Codex's boundary
and provenance refinements.

It is intentionally numbered v0.9: mature enough for a second design review,
not approved for implementation. mini-moi remains at release 1.1. No later
release number is assigned by this document.

## 1. Design position

mini-moi may evolve toward a portable, person-owned system whose durable center
is a private personal repository. The repository preserves the context that
makes one mini-moi instance belong to one person while applications, agents,
models, and infrastructure may change.

Planning Studio is a capability within that personal repository. CoS is a
primary intake and retrieval interface. Curator and other domains contribute
typed artifacts and evidence. Guild governs promotion into roadmaps and build
work. The domains remain distinct.

This direction advances only if Robert uses it and receives real benefit.

## 2. Authority and scope

### Authority

- Robert owns the personal repository and all promotion decisions.
- CoS may capture, retrieve, connect, and propose within defined bounds.
- Planning Studio preserves structured exploration and prepares decisions.
- Curator investigates and produces research artifacts.
- Guild governs roadmap promotion and later build discipline.
- No agent may turn a durable capture into approved work implicitly.

### Current status

- mini-moi current release: 1.1.
- This design: `design_review`.
- Roadmap authority: none.
- Implementation authority: none.
- Release target: none.

### Outside scope

- a committed mini-moi 2.0 release;
- assignment of work to 1.2, 1.3, or another release;
- application or database implementation;
- broad migration of the OpenClaw workspace;
- automatic retention of every conversation;
- shared multi-person state;
- a required Planning Studio UI.

## 3. Responsibilities

| Capability | Primary question | Responsibility |
|---|---|---|
| Robert | What should persist or become authoritative? | Ownership, capture intent, decisions, priorities |
| CoS | What matters, what connects, and what should carry forward? | Personal intake, continuity, recall, cross-domain connection, judgment |
| Curator | What should be investigated and what does the evidence show? | Sources, research, briefings, synthesis, evolving questions |
| Planning Studio | What has an idea become and is it ready for a decision? | Durable structure, alternatives, reviews, disposition, promotion preparation |
| Guild | How does approved direction enter controlled delivery? | Roadmaps, specifications, review gates, build, verification, operation |
| Other domains | What domain evidence or durable state matters? | Typed artifacts, feedback, and exportable state where needed |

`origin`, `steward`, and `domains` are independent. A record may originate in a
CoS conversation, be stewarded by Planning Studio, and affect Curator, Guild,
and another domain simultaneously.

## 4. Durable capture boundary

The durable-capture action is explicit:

> A file becomes a Planning Studio intake when Robert asks that it be saved or
> deliberately places it in the intake area.

Producing a file elsewhere does not automatically make it durable. CoS and
other agents may recommend capture, but they must not infer that every
conversation or generated artifact belongs in the repository.

The first capture path remains manual and agent-independent:

```text
conversation or investigation
          |
          | Robert: save this / file handed into intake
          v
       inbox/
          |
          | validate, preserve source, add minimum metadata
          v
   durable repository record
```

CoS write automation is deferred until this path has been used enough to show
what should be automated.

## 5. Minimum record model

Planning Studio begins with three top-level record types:

| Type | Meaning | Preservation rule |
|---|---|---|
| `source` | Metadata record for unchanged user-provided or external material | Source file immutable after intake |
| `note` | Captures, briefings, proposals, reviews, designs, and patterns | Working until review; reviewed versions preserved |
| `decision` | Robert's dated disposition and reasoning | Immutable; later decisions supersede rather than rewrite |

`kind` distinguishes note purposes without expanding the top-level taxonomy.
Examples include `capture`, `briefing`, `proposal`, `review`, `design`, and
`pattern`.

### Minimum intake fields

Only these fields are required at the moment of capture:

- `id`;
- `slug`;
- `type`;
- `title`;
- `created`;
- `origin`.

`kind`, `domains`, `tags`, `steward`, relationships, status, and version may be
added during classification. Intake should not fail because Robert does not
yet know how to classify an idea.

## 6. Identity and versioning

### Unique record identity

Every durable record receives a ULID. Any authorized agent or tool can mint one
without a central allocator.

### Human and series identity

- `slug` is a unique human-readable link target for one physical record.
- `series` is an optional stable key grouping versions of the same evolving
  work.
- `version` appears in frontmatter only.
- an immutable new version receives a new ULID and unique slug;
- `supersedes` names the exact prior ULID.

Candidate filename convention:

```text
<human-series>--<short-ulid>.md
```

Example:

```yaml
id: 01M1HMVNJBNKWD9GWC9HN6RRK9
series: terminal-pleistocene-briefing
slug: terminal-pleistocene-briefing--9hn6rrk9
version: 0.3
supersedes: 01M1HKM1BQRDJ5DJC2ZZ7807NR
```

The exact short-ID length remains subject to review. The design requirement is
unambiguous simultaneous retention of all cited source versions without
encoding the version in several manually synchronized locations.

Exact intake source files are the deliberate naming exception. They retain a
recognizable source-provided filename and visible version label because their
history must be understandable directly from the source directory. Their
separate provenance records follow the normal slug-plus-ULID convention.

### Working notes

A newly captured note may be amended while still in intake, with Git history
retaining changes. Once a note is submitted for review or cited by another
record, its reviewed version is frozen. A material revision creates a new
record version in the same series.

## 7. Source preservation and visible history

One canonical source exists under `library/`. Initiatives link to it; they do
not keep their own copies.

Adding frontmatter to an original source changes its bytes. A source therefore
uses a human-visible file plus a separate provenance record:

```text
library/
  sources/
    <human-series>/
      README.md
      <source-name>-v0.1.md
      <source-name>-v0.2.md
  records/
    <source-version-record>--<short-ulid>.md
```

Successive source versions remain beside one another. The series README shows
their order, checksums, and supersession relationship. A reviewer can follow
v0.1 to v0.2 to v0.3 from the source files themselves without a database,
generated registry, opaque hash path, or Git diff.

The Markdown provenance record contains the source path, SHA-256 checksum,
original location, version, and relationships. The source file retains its
exact intake bytes. Content hashes verify identity; they do not hide the source
behind a content-addressed directory. Summaries and critiques are separate
`note` records.

This model handles Markdown, PDFs, DOCX files, images, exported conversations,
and later formats consistently while keeping human navigation primary.

## 8. Relationships and domains

### Typed relationships

Canonical relationships are typed frontmatter entries:

```yaml
relations:
  - type: supports
    target: 01M1HMMRC34T7KPAGA9RFKAJE5
  - type: supersedes
    target: 01M1HKM1BQRDJ5DJC2ZZ7807NR
```

Initial relationship vocabulary may include:

- `supports`;
- `complicates`;
- `contradicts`;
- `derived_from`;
- `motivates`;
- `reviews`;
- `supersedes`;
- `promoted_to`.

Wikilinks remain useful for human navigation and optional viewers. They are not
the sole graph contract because an untyped link does not explain the
relationship.

### Domains and tags

`domains` is a multi-value, non-exclusive field used for domain relevance,
stewardship, permissions, and retrieval.

`tags` is an open vocabulary used for cross-pollination, subject matter, and
emerging patterns.

Domains may also appear as tags for navigation, but the structured `domains`
field remains distinct.

## 9. Lifecycle

The user-facing lifecycle remains small:

```text
captured -> exploring -> design_review -> decision_pending -> promoted
    |           |              |                 |
    +-----------+--------------+-----------------+-> archived/discarded
```

`superseded` is primarily a relationship and derived status rather than a
manual filing operation.

Lifecycle changes must remain visible in Git history. A discarded record is
not deleted; a decision or tombstone records the disposition.

## 10. Candidate repository shape

```text
personal-repository/
  README.md
  identity/        personal principles and durable self-description
  context/         goals, preferences, and curated long-term context
  inbox/           explicit new captures awaiting classification
  library/
    sources/       human-visible immutable source files, grouped by series
    records/       source provenance and relationship records
  initiatives/     structured notes and reviews; sources linked by ID
  decisions/       canonical Robert decisions
  governance/      charter, lifecycle, and record contracts
  registry/        generated projections only
  schemas/         minimum frontmatter validation contracts
  archive/         tombstones and inactive records
```

Not included until real use requires them:

- `patterns/` as a dedicated directory;
- hand-maintained templates;
- per-initiative source copies;
- manually maintained registries;
- empty directories representing hypothetical functions.

The current v0.1 scaffold is retained as design history until v0.9 is approved.
It is not the implementation template.

## 11. Projections and tools

Markdown records and preserved assets form the durable substrate. Tools remain
replaceable projections or viewers.

Potential projections include:

- generated JSON registry;
- PostgreSQL metadata and full-text search;
- graph relationships;
- semantic retrieval index;
- Planning Studio and CoS interfaces.

A projection must be reproducible from durable records or have a documented
export-and-restore contract. No editor or viewer is required by the substrate.

The open-source tool survey in Claude's review is retained as research. No tool
selection is made by this design.

## 12. Integrity and durability

Before real use, a later approved implementation should enforce:

- frontmatter validation that ignores examples and fenced code blocks;
- duplicate ULID and slug detection;
- source-asset checksum verification;
- secret scanning;
- generated-registry drift detection;
- protected remote history with force-push disabled;
- append-only disposition practice;
- backup to an independent versioned store;
- periodic restore testing.

Time Machine provides a local backup domain. S3 with object versioning can
provide an independent remote backup domain. iCloud is useful synchronization
but should not be counted as an independent deletion-resistant backup.

These are design requirements, not implemented controls.

## 13. Existing OpenClaw workspace

The existing workspace is a legacy source, not the foundation for a wholesale
copy. It mixes personal memory, code, runtime material, generated outputs, and
uncommitted state.

The current candidate is to create a new private repository and migrate by
classification. The existing workspace remains untouched until an inventory,
sensitivity review, destination mapping, and verification are approved.

The exact repository name and remote location remain open.

## 14. Evidence-first experiment

The smallest proposed evidence cycle is one real round trip:

1. Place an external review of Terminal Pleistocene v0.2 into `inbox/`.
2. Preserve the review source unchanged in its visible series and create a
   source or review record with minimum metadata.
3. Record Robert's disposition as a decision.
4. If warranted, preserve v0.3 beside v0.1 and v0.2 as a new immutable source
   version linked to v0.2.
5. Generate, rather than manually edit, the registry.
6. Later ask CoS a relevant question and observe whether it retrieves the right
   background without Robert reconstructing it.

This experiment is proposed, not authorized by the design candidate.

## 15. Use and abandonment evidence

The repository should not continue because its architecture is interesting.
It should continue because it provides personal value.

Proposed six-month threshold for review:

- at least five deliberate durable captures; and
- at least two later retrievals that materially inform a decision, action, or
  investigation.

Robert must confirm or replace these numbers during the next design review. If
the agreed threshold is not met, archive or reduce the initiative without
manufacturing a release.

## 16. Version direction and future deck

mini-moi continues through incremental 1.x releases only as real use earns
them. This candidate reserves no release number.

A 2.0 label is appropriate only if accumulated, validated changes create a
materially different system that Robert actually uses and benefits from.

If that happens, a reference presentation is part of the eventual release
definition. It should explain:

1. the prior operating model;
2. the personal repository and ownership boundary;
3. CoS, Planning Studio, Curator, Guild, and domain roles;
4. capture through decision and delivery;
5. reusable software versus personal state;
6. evidence from real use and an independent personal instance;
7. what the earned release actually provides.

The deck documents the proven system. It does not create the release.

## 17. Open decisions for the second review

1. Confirm the explicit durable-capture action.
2. Confirm the visible source-series plus provenance-record model for exact
   source preservation.
3. Confirm ULID-per-version plus stable `series` identity.
4. Confirm typed relationships alongside optional wikilinks.
5. Confirm `domains` remains distinct from free tags.
6. Decide the six-month capture and retrieval threshold.
7. Decide whether reviewed note versions always become new files or only when
   externally cited.
8. Decide whether decisions live only in top-level `decisions/` or may be
   co-located and projected there.
9. Decide the future private repository name and backup policy.
10. Decide whether the Pleistocene round trip is the correct first experiment.

## 18. Review and implementation gate

This v0.9 candidate must receive another independent design review. Robert then
chooses whether to revise, approve a smaller experiment, pause, archive, or
discard it.

Even approval of this design does not authorize implementation. A separate,
bounded specification and reviewed diff are required before any software,
repository migration, automated CoS write path, database projection, hook, or
remote policy is built.
