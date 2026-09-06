---
id: 01M1HMVNJBPVAWXZSGPKS773W2
series: artifact-retention-and-naming-practice
slug: artifact-retention-and-naming-practice--pks773w2
type: note
kind: design
title: "Artifact retention and naming practice"
version: 0.9
created: 2026-09-02
authors: [robert, codex]
origin: planning_studio_design_session
status: design_review
decision_owner: robert
roadmap_authority: none
implementation_authority: none
domains: [cos, guild, curator, platform]
tags: [retention, naming, provenance, source-history, planning-studio]
---

# Artifact Retention and Naming Practice

## Status

This is the v0.9 practice applied to the first Planning Studio artifacts for
review. It establishes documentary discipline only. It does not authorize a
registry generator, hook, database, CoS write path, migration, or runtime.

## 1. Sources remain human-visible

Successive sources are stored together under a human-readable series folder:

```text
library/sources/<series>/
  README.md
  <source-provided-name>-v0.1.md
  <source-provided-name>-v0.2.md
  <source-provided-name>-v0.3.md
```

The directory and its README show the sequence directly. A later source is
additive: it may supersede an earlier source for future work, but it never
overwrites or hides it.

## 2. Exact source bytes remain unchanged

A source file is copied or moved into its canonical series directory without
editing its contents. Its SHA-256 checksum is recorded and verified. Metadata,
interpretation, correction, or critique lives in a separate Markdown record.

Human-readable paths are canonical. Content hashes verify identity; they do
not replace filenames with opaque hash directories.

## 3. Record naming

Planning records use:

```text
<descriptive-slug>--<short-ulid>.md
```

The full ULID lives in frontmatter. The short suffix prevents collisions and
makes simultaneous versions unambiguous. `version` lives in frontmatter rather
than being repeated in the record filename.

Exact source files are the deliberate exception. They retain meaningful
source-provided names and visible version labels because preserving and reading
their lineage is more important than normalizing their filenames.

## 4. Series and supersession

- Every durable record version receives its own ULID and unique slug.
- `series` groups related versions.
- `version` states the author's version.
- `supersedes` points to the exact prior record ID.
- A series README provides the human-readable sequence for preserved sources.
- Git history supplements this lineage but is not the only way to see it.

## 5. Retention by record type

- `source`: immutable after intake; retain every received version.
- `note`: may be revised during intake; freeze the reviewed or cited version,
  then create a new version for material changes.
- `decision`: immutable; a later decision supersedes it without rewriting it.

Discarded or superseded work is archived or marked by a later disposition. It
is not silently deleted.

## 6. Canonical copy and references

There is one canonical source file in `library/sources/`. Initiatives link to
that source and its provenance record. Review packages may contain transport
copies, but they identify the canonical source and checksum and do not become
competing authorities.

## 7. Durability boundary

Robert's explicit request to save an artifact, or his deliberate placement of
it into Planning Studio, makes it a durable intake. Merely producing a file or
having a conversation does not.

The repository files become protected history only after Robert approves the
reviewed diff and they are committed to the protected remote. Until then they
are retained locally as a review candidate and must be reported as such.

## 8. Applied first examples

This practice is already represented by:

- both Terminal Pleistocene source files retained side by side;
- the Curator roadmap source retained unchanged;
- the exact initial Planning Studio v0.1 review package retained with its hash;
- Claude's review, Codex's assessment, the v0.9 candidate, and this origin
  capture retained as separate records rather than merged into one narrative.

The practice remains part of the second design review. Applying it to these
documents is not approval to implement automation around it.
