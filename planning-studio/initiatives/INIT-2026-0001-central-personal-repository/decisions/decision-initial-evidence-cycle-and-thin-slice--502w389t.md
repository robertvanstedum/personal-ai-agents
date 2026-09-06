---
id: 01M1J8V5M0FGFP8FY4502W389T
series: decision-initial-evidence-cycle-and-thin-slice
slug: decision-initial-evidence-cycle-and-thin-slice--502w389t
type: decision
title: "Use a two-to-three-week thin-slice evidence cycle, then operate it"
revision: 1
created: 2026-09-02
decided_by: robert
recorded_by: codex
status: recorded
roadmap_authority: none
implementation_authority: none
domains: [cos, guild, curator, platform]
tags: [planning-studio, evidence-cycle, thin-slice, operations, design-scope]
relations:
  - type: derived_from
    target: 01M1J8V5KYBG3R2EYDQ2C0A48D
---

# Decision — Initial evidence cycle and thin slice

Planning Studio will use an initial two-to-three-week evidence and delivery
cycle. If the slice proves useful and safe, it will remain in operation after
that period. The period is not a temporary demonstration and is not a
six-month waiting gate.

## Thin-slice boundary

The first bounded specification should cover only:

1. an explicit drop or “save this” capture action;
2. automatic ULID, slug, checksum, filename, series revision, and supersession
   bookkeeping so Robert does not manage them;
3. a single-writer transaction over durable files;
4. immutable exact source payloads with small, readable metadata;
5. visible integer revisions within a series;
6. separate source, note, and decision records;
7. only `supersedes`, `reviews`, and `derived_from` relationships initially;
8. optional domain hints rather than required classification or permissions;
9. decisions co-located with the series or initiative they address; and
10. retrieval from the preserved sources and decisions before CoS receives
    write access.

## Initial evidence

During the two-to-three-week cycle, seek:

- at least five deliberate captures across at least two concerns;
- at least two later retrievals that materially inform a decision, action, or
  investigation;
- at least one case where visible source revisions resolve what changed; and
- a successful restore of one preserved series from an independent backup or
  documented recovery copy before personal migration expands.

These are learning measures, not a release claim. After the initial cycle, the
capability should continue operating and be reviewed periodically using actual
capture friction, retrieval quality, integrity failures, and restoration
results.

## Explicit deferrals

The first slice does not require a generated JSON registry, PostgreSQL, a graph
store, semantic indexing, a Planning Studio UI, hooks, broad OpenClaw
migration, a complete typed-edge vocabulary, domain-based permissions, CoS
write access, or automated Guild promotion.

## Authority

This decision authorizes the revised design direction and its time horizon. It
does not by itself authorize implementation. A bounded specification and
reviewed implementation diff remain required before the intake helper or
operational controls are built.
