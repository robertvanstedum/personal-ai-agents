---
id: 01M1J8V5KZXNS7MKSA0EFMWP2J
series: planning-studio-central-personal-repository-design
slug: planning-studio-central-personal-repository-design--0efmwp2j
type: note
kind: design
title: "Planning Studio and Central Personal Repository — revised design candidate"
revision: 2
source_version: 0.9
supersedes: 01M1HMMRC34T7KPAGA9RFKAJE5
created: 2026-09-02
authors: [robert, codex]
status: design_review
decision_owner: robert
roadmap_authority: none
implementation_authority: none
release_target: null
initial_evidence_cycle: "2-3 weeks, followed by continued operation"
domains: [cos, guild, curator, platform]
tags: [planning-studio, central-repository, file-first, provenance, automatic-versioning, thin-slice]
relations:
  - type: derived_from
    target: 01M1J8V5KYBG3R2EYDQ2C0A48D
---

# Planning Studio and Central Personal Repository

## Revised v0.9 design candidate — repository revision 2

This revision incorporates Grok's independent review and Robert's timing
decision. It retains the original thesis and reduces the first evidence cycle
to the minimum operational slice that can test it.

`v0.9` remains a human design label, not a mini-moi release. `revision: 2` is
the repository-assigned position in this design series. The earlier candidate
remains preserved and visible through `supersedes`.

## 1. Design thesis

mini-moi's durable value is the context that explains what Robert explored,
what evidence was gathered, what changed, what was decided, and why. That
context must remain person-owned, readable without an application, and
portable across agents, models, infrastructure, and future domain software.

The Central Personal Repository is the durable file substrate. Planning Studio
is the disciplined capability for preserving exploration, review, lineage, and
decisions within it. Databases, indexes, graphs, registries, and user interfaces
are optional projections, not the sole authority.

The direction advances through actual use. Architecture interest alone is not
evidence of value.

## 2. Goals

- Preserve exact sources, evolving notes, reviews, and decisions without
  flattening them into a reconstructed narrative.
- Make history understandable in a file browser, editor, Git client, or simple
  text reader.
- Remove identity, naming, checksum, revision, and supersession bookkeeping
  from Robert's capture action.
- Support work that crosses CoS, Curator, Guild, and domain boundaries without
  forcing exclusive folder ownership.
- Keep reusable mini-moi software and clean domain baselines separate from
  Robert's private context.
- Establish a controlled path from capture to retrieval and, only when
  appropriate, later delivery governance.

## 3. Non-goals for the first cycle

- committing mini-moi to a 2.0 release or assigning this work to a particular
  1.x release;
- building a Planning Studio application, generated registry product,
  PostgreSQL store, graph, semantic index, or user interface;
- migrating the existing OpenClaw workspace wholesale;
- retaining every conversation or agent output;
- granting CoS direct write access;
- automating promotion into Guild roadmaps; or
- defining shared multi-person state or commercial use.

## 4. Responsibility legend

The named roles describe responsibilities. They are not required intake fields
and should not become a filing tax.

| Role | Responsibility in this design |
|---|---|
| Robert | Chooses what persists and makes decisions or dispositions |
| CoS | Recommends capture and retrieves personal context; read-only initially |
| Curator | Investigates questions and contributes sources and synthesis |
| Planning Studio | Preserves structure, history, review, and decisions |
| Guild | Governs later roadmaps and controlled delivery when promotion is chosen |
| Domains | Contribute evidence and consume approved reusable capabilities |

`origin`, `steward`, and `domains` remain conceptually independent, but only
`origin` is required at initial capture. Robert is the default steward.
`domains` is an optional relevance hint during the first cycle, not a
permission model.

## 5. Explicit capture boundary

A record becomes durable when Robert asks to save it or deliberately places it
in the intake area. Agents may recommend capture, but they do not vacuum the
working tree, chats, or generated output.

The desired interaction is:

```text
Robert: save this / drops a file
        |
        v
single intake helper performs bookkeeping and validation
        |
        v
readable immutable source + small metadata + visible series history
```

If series membership is ambiguous, the helper asks one substantive question:
“Continue this existing series, or create a new series?” Robert does not choose
counters, IDs, checksums, filenames, or supersession links.

## 6. Minimum record model

The repository begins with three top-level record types:

| Type | Purpose | Preservation rule |
|---|---|---|
| `source` | Exact user-provided or external material plus provenance | Payload immutable after publication |
| `note` | Capture, briefing, proposal, review, or design | Freeze when reviewed or cited; material revision creates a new record |
| `decision` | Robert's dated disposition and reasoning | Immutable; later decision supersedes rather than rewrites |

Kinds may refine notes later. The first cycle does not require a complete
taxonomy. Minimum capture metadata is `id`, `slug`, `type`, `title`, `created`,
and `origin`; the helper supplies these wherever possible.

## 7. Source-preservation patterns

Exact bytes remain authoritative. The storage pattern depends on source type:

1. **Repository-authored Markdown.** The record may contain native frontmatter
   and content in one file. Once published, it becomes immutable.
2. **Externally supplied text or Markdown.** Preserve the exact payload as a
   source file and place provenance in a small sibling record or series
   orientation file. Do not inject frontmatter into the payload.
3. **Binary sources.** Preserve DOCX, PDF, ZIP, images, and other binaries
   unchanged with a sibling metadata record and SHA-256 checksum.

This avoids both byte mutation and a second explanatory essay. A human-visible
series orientation lists revisions, checksums, and supersession. It is a
rebuildable navigation aid rather than a competing source of truth.

## 8. Identity and automatic repository revisioning

Every published record receives:

- a globally unique, time-sortable ULID;
- a descriptive slug with a collision-resistant short suffix;
- a stable `series` identifier when it continues evolving work;
- a monotonically increasing integer `revision` within that series;
- `supersedes` pointing to the exact previous record, when applicable; and
- a checksum for preserved source payloads.

Repository revision is distinct from semantic or source versioning. A supplied
label such as `v0.2`, `v0.9`, or `Mini-moi-2.0` is retained as
`source_version` or provenance. It does not assign a product release.

The intake mechanism, not Robert, performs this bookkeeping. Timestamps alone
do not determine a series revision.

## 9. Single-writer transaction

File-first storage is the durable and readable interface, but it is not safe
for uncoordinated concurrent mutation. The first bounded specification must
define one intake writer per repository at a time:

1. acquire a repository lock with owner identity and timeout behavior;
2. resolve the series and read its current revision under the lock;
3. mint the ULID and candidate revision;
4. write payload and metadata into a staging directory on the same filesystem;
5. validate required metadata, duplicate IDs, slugs, checksum, and target
   paths;
6. atomically publish the staged record or directory;
7. rebuild the series orientation from published records; and
8. release the lock.

If orientation generation fails after publication, the immutable record
remains valid and orientation can be rebuilt. A stale or conflicting series
revision aborts; the helper does not silently choose another number after an
unlocked read. Exceptional removal uses a tombstone, never a silent unlink.

Multi-agent writes remain prohibited until this transaction is specified,
implemented, and tested.

## 10. Relationships, domains, and decisions

The first cycle permits only relationships demonstrated by real records:

- `supersedes`;
- `reviews`; and
- `derived_from`.

Additional types may be introduced when an actual record needs them. There is
no separate relationship store or graph in the first cycle.

`domains` remains an optional multi-value relevance hint. It does not control
permissions or determine ownership during the experiment.

Decisions are canonically co-located with the series or initiative they
address. A future global decision index may be generated as a projection; it
is not a second canonical store.

## 11. Retrieval and CoS boundary

The first retrieval test uses ordinary files, series orientation, and explicit
relationships. CoS may read the repository and answer a substantive question
using the preserved source and later decision. It should cite or open those
records rather than rely on a chat paraphrase.

CoS write access is deferred. It may recommend “save this,” but the designated
single intake helper is the only writer during the initial cycle.

## 12. Initial two-to-three-week evidence cycle

The first cycle is a delivery and evidence period of two to three weeks,
followed by continued operation if the slice is useful and safe.

The initial evidence target is:

- five or more deliberate captures across at least two concerns, with Terminal
  Pleistocene and Connect HQ/Nightjar as natural independent series;
- two or more later retrievals that materially inform a decision, action, or
  investigation;
- one case where visible adjacent revisions clarify what changed; and
- one documented recovery of a preserved series.

The evidence is qualitative as well as numeric. Capture must feel materially
easier than ad hoc filing, retrieval must return underlying evidence rather
than only an orientation file, and integrity failures must be observable.

After the initial cycle, continue operating the capability and review actual
capture frequency, retrieval usefulness, misclassification, integrity events,
and restoration results. A longer observation period measures durability; it
does not delay the initial operational milestone.

## 13. Private-repository and durability boundary

Before migrating personal material beyond existing design artifacts, require:

- a private repository separate from reusable mini-moi software;
- protected remote history with force-push disabled;
- secret scanning on intake;
- a sensitivity field such as `personal`, `design`, or `restricted`, with
  restricted material remaining local until an approved storage policy exists;
- local backup plus independent versioned object storage; and
- one successful restore test of a complete series.

The repository name and final hosting location may be chosen later. The
separation between private context and reusable software is not deferred.

## 14. Known input gap

The preserved Claude chat references two artifacts that are not present as
files in Planning Studio:

- `decision-authorize-registry-generator-for-first-experiment--jy330nyb.md`;
- Claude's second Planning Studio design review.

Their contents must not be reconstructed from mentions. If the original files
cannot be recovered, their absence should be recorded. This revised design
does not depend on the missing registry-generator decision because a generated
registry is outside the first thin slice.

## 15. Review and implementation gate

This revision is ready for final design review of the thin-slice boundary.
Robert then decides whether to authorize a bounded specification.

The implementation specification must be limited to explicit intake,
automatic bookkeeping, single-writer transactional publication, visible
series history, retrieval, integrity checks, and recovery appropriate to the
two-to-three-week cycle. Any registry product, database, graph, semantic index,
UI, CoS write path, migration, or Guild automation requires later evidence and
a separate decision.

Approval of this design is not approval of an unreviewed implementation diff.
