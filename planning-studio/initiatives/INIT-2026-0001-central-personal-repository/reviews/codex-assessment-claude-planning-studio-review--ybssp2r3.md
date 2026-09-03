---
id: 01M1HMMRC3BQP64F48YBSSP2R3
series: codex-assessment-claude-planning-studio-review
slug: codex-assessment-claude-planning-studio-review--ybssp2r3
type: note
kind: review
title: "Codex assessment of Claude's Planning Studio review"
version: 0.1
created: 2026-09-02
author: codex
status: submitted
decision_owner: robert
tags: [planning-studio, central-repository, design-review, cos, curator, provenance]
---

# Codex Assessment of Claude's Planning Studio Review

## Verdict

Concur with the central direction and use Claude's review as the basis for a
consolidated v0.9 design candidate. Do not implement the proposed structure or
tooling before that candidate receives another design review and Robert's
decision.

## Recommendations accepted

- Reduce intake to three top-level record types: `source`, `note`, and
  `decision`, with `kind` for later distinctions.
- Use Markdown with YAML frontmatter as the ordinary record format.
- Keep one canonical source location under `library/`.
- Use ULIDs rather than centrally allocated sequential IDs.
- Generate registries as projections rather than maintaining them manually.
- Drop `Guild` from the Studio name while retaining Guild governance at the
  promotion boundary.
- Remove empty speculative structure before first real use.
- Enforce retention, secret protection, and remote history policy rather than
  relying only on prose.
- Establish an evidence threshold for continued investment.
- Prefer a new private repository and migrate the existing OpenClaw workspace
  only through classification.

## Refinements added

### 1. Durable capture requires explicit placement or intent

The practical action is not merely that an agent produces a file. Agents
produce many transient files. A record becomes durable when Robert explicitly
asks to preserve it or places it into the Planning Studio intake. CoS may
recommend capture but should not infer retention for every conversation.

### 2. Domains should not collapse into free tags

Domains are multi-value and non-exclusive, but they retain semantic meaning for
stewardship, permissions, and retrieval. `domains` should remain a distinct
field. Flat `tags` provide unconstrained cross-pollination alongside it.

### 3. Graph relationships require typed edges

Wikilinks are useful for reading and navigation but do not state whether a
record supports, contradicts, supersedes, motivates, or is derived from
another. Canonical relationship metadata should use typed targets. Wikilinks
may be rendered or included for convenience.

### 4. Exact source preservation needs an asset boundary

Adding YAML frontmatter to an intake file changes its bytes, so it cannot also
be the byte-identical preserved original. The candidate design should separate:

- a Markdown source record containing metadata and relationships; and
- an immutable content-addressed asset containing the exact original bytes.

For Markdown input both remain readable. For PDF, DOCX, images, and other
formats this boundary is necessary anyway. The asset checksum is the integrity
identity; the record is the queryable description.

### 5. Multi-version identity needs a series model

A single human slug cannot identify several simultaneously retained immutable
versions without ambiguity. Each record version should receive a unique ULID
and unique link slug, while an optional stable `series` key groups related
versions. `version` remains in frontmatter; `supersedes` points to the exact
prior record ID.

### 6. Direct CoS writes remain deferred

Pre-commit hooks can be bypassed and do not by themselves make broad agent
writes safe. The first evidence cycle should use explicit file hand-in and a
validator. CoS write access should be designed after the manual path reveals
what needs automation.

### 7. Tool choices remain projections

Foam, markdown-oxide, Zettlr, Zotero, Obsidian, and other viewers may be useful,
but no editor should enter the core design contract. The repository must remain
usable without any of them.

## Additional integrity observation

Examples and fenced code blocks must be excluded from record scanning. The
Claude review uses an illustrative ULID inside Appendix A that is also its own
frontmatter ID. That is harmless as prose but would appear as a collision to a
naive whole-file pattern scanner. A future registry generator must parse only
document frontmatter, not example blocks.

## Recommended next gate

Review the consolidated v0.9 candidate for:

- minimum capture friction;
- source-versus-record integrity;
- unique identity across versions;
- typed cross-domain relationships;
- CoS authority boundaries;
- realistic evidence and abandonment criteria.

No implementation follows from this assessment.
