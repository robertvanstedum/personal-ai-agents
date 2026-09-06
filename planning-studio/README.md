# Planning Studio — Design Repository

**Repository scaffold version:** 0.1; consolidated design candidate 0.9  
**Established:** 2026-09-02  
**Status:** Second design review; not an approved roadmap or build specification  
**Decision owner:** Robert  
**Current mini-moi release:** 1.1

## Purpose

This directory is the first durable home for early mini-moi ideas that have
become important enough to preserve but are not yet approved roadmap work.
It is deliberately outside `_working/`.

Planning Studio is governed by Guild at the promotion boundary but is not named
or owned as a Guild subdomain. It separates four things that were previously
easy to blur:

1. an early thought or conversation;
2. a durable capture with its original background;
3. a structured initiative under design review;
4. an approved roadmap item or build specification.

The first two initiatives are:

- [Central Personal Repository](initiatives/INIT-2026-0001-central-personal-repository/README.md)
- [Open-subject Curator](initiatives/INIT-2026-0002-curator-open-subject/README.md)

The Curator example is intentionally cross-domain. It began as Robert's
curiosity, produced Curator research artifacts, raised questions about CoS
continuity, and may affect Guild design. Domain association is therefore
many-to-many metadata, not exclusive folder ownership.

## Authority

Nothing here authorizes implementation.

- `exploring` and `design_review` records are proposals.
- Robert alone promotes an initiative into a domain roadmap.
- An approved roadmap still requires a separate build specification and
  reviewed implementation diff.
- The current mini-moi version remains 1.1. References to 2.0 are design
  horizons, not release commitments.

## Intended long-term home

This scaffold lives in `personal-ai-agents` so the structure and initial
documents can be reviewed. The current repository is public-facing and is not
the final home for Robert's private accumulated context.

The design hypothesis is that a future private, person-owned repository will
contain CoS context, durable captures, research artifacts, cross-domain
initiatives, Planning Studio records, decisions, and portable domain state.
Reusable code, schemas, templates, and generic domain baselines remain in the
mini-moi software repository.

No private repository has been created or published by this scaffold.

## Structure

```text
planning-studio/
  README.md
  governance/       repository rules and lifecycle
  registry/         machine-readable indexes
  schemas/          validation contracts
  templates/        new-record templates
  inbox/            proposed captures awaiting classification
  library/          canonical sources, visible version history, provenance records
  initiatives/      cross-domain design work
  patterns/         possible reusable portions
  archive/          preserved closed or superseded records
```

Initiatives contain their metadata, documents, reviews, and decision records.
Canonical sources live once in `library/sources/`, grouped by human-readable
series so successive source files remain visible together. A source remains
unchanged after intake. Its provenance record, interpretation, or summary is a
separate artifact.

## Current review sequence

1. Review the [consolidated v0.9 candidate](initiatives/INIT-2026-0001-central-personal-repository/documents/planning-studio-central-personal-repository-design--9rfkaje5.md).
2. Review the [artifact retention and naming practice](governance/artifact-retention-and-naming-practice--pks773w2.md).
3. Read [Claude's first review](initiatives/INIT-2026-0001-central-personal-repository/reviews/claude-design-review-planning-studio--zz7807nr.md).
4. Read [Codex's assessment and refinements](initiatives/INIT-2026-0001-central-personal-repository/reviews/codex-assessment-claude-planning-studio-review--ybssp2r3.md).
5. Inspect the Curator initiative and its visible source history.
6. Use the [second-review brief](initiatives/INIT-2026-0001-central-personal-repository/reviews/planning-studio-second-design-review--9tdg8ckv.md).

The earlier v0.1 charters and schemas remain available as design history. They
are not the implementation template while v0.9 is under review.

## Repository rule

No agent memory supersedes the material on disk. No summary supersedes its
source. No database projection supersedes the durable repository record.
