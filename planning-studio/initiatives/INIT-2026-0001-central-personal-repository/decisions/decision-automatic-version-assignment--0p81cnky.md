---
id: 01M1J73KAVC3EJMPFV0P81CNKY
series: decision-automatic-version-assignment
slug: decision-automatic-version-assignment--0p81cnky
type: decision
title: "Assign record identity and revision automatically"
revision: 1
created: 2026-09-02
decided_by: robert
recorded_by: codex
status: recorded
roadmap_authority: none
implementation_authority: none
domains: [cos, guild, curator, platform]
tags: [planning-studio, automatic-versioning, naming, provenance, source-history]
relations:
  - type: derived_from
    target: 01M1J73KASXG79Q5K31MF77XDP
  - type: complements
    target: 01M1J18VFF908D4A9EQDBGSN88
  - type: complements
    target: 01M1J6M79H78ZR99BFGZH5BSCH
---

# Decision — Assign Identity and Revision Automatically

Robert should not have to type `v0.1`, determine the next revision, mint an
identifier, construct a filename, or manually connect a new version to the one
it supersedes.

The future intake/versioning mechanism must automatically:

1. mint a globally unique ULID;
2. resolve or create the artifact's stable `series`;
3. assign the next monotonically increasing `revision` within that series;
4. set `supersedes` to the exact prior record when one exists;
5. create the descriptive slug and collision-resistant filename;
6. calculate and record the source checksum; and
7. update any generated orientation or registry projections.

## Version meaning

The automatic value is a repository revision, not an inferred semantic release
number. If a supplied source already calls itself `v0.2`, that label is
preserved as `source_version`; the repository still assigns its own revision.
A release or milestone label such as `v0.9` remains a separate design decision
and is never guessed by the intake mechanism.

## Visible source history

Automatic assignment must preserve the existing readability requirement. The
source files remain visibly ordered in their series. If an incoming source has
no usable version in its filename, the intake mechanism adds a readable,
system-assigned revision suffix without changing the file's bytes.

## Human responsibility

Robert may need to confirm that a source continues an existing series when the
relationship is ambiguous. He does not manage counters or identifiers. The
mechanism proposes the relationship and validation prevents collisions or
silent overwrite.

## Authority boundary

This decision establishes a design requirement. It does not authorize building
the intake tool, registry generator, hook, database, CoS write path, or other
automation. Those require a bounded specification and reviewed implementation
diff after the design review.
