# Guild Planning Studio Charter

**Version:** 0.1  
**Date:** 2026-09-02  
**Status:** Draft for design review  
**Decision owner:** Robert

## 1. Mission

Guild Planning Studio is mini-moi's durable pre-roadmap design area. It
preserves ideas, investigations, original sources, alternatives, reviews, and
decisions once Robert determines that they should survive beyond a single
conversation.

Planning Studio is governed by Guild but is not the Guild build queue. It is a
place for thought to mature before implementation is authorized.

## 2. Core principles

### Durable after deliberate capture

Conversation may remain ephemeral. Once Robert or an approved intake process
saves an item to Planning Studio, the item receives a stable identity,
provenance, version, lifecycle state, and retention record.

### Originals and interpretations remain distinct

An original source is preserved unchanged. Summaries, critiques, extracted
patterns, and decisions are separate records linked to that source. An agent
must never silently replace source content with its interpretation.

### Cross-domain by design

An initiative may involve several domains. `origin`, `steward`, `domains`, and
`relationships` are independent fields. Physical placement must not imply that
one domain exclusively owns a cross-domain idea.

### Human-readable and machine-indexable

The durable record uses ordinary files and explicit metadata so Robert can
read it without a special application and agents can scan it completely.
PostgreSQL, graph storage, search indexes, and user interfaces may project the
record later but do not become the only copy.

### Decisions are explicit

Planning Studio may contain competing or contradictory proposals. A direction
becomes authoritative only through a dated decision from Robert and an
explicit promotion record.

### Retention without clutter

Superseded, rejected, or abandoned material moves out of active views but is
not silently erased. A disposition record explains what happened and links to
any replacement.

### Personal ownership

Robert owns his accumulated context and may carry it to another implementation
or stop using mini-moi without losing the readable record. Reusable mini-moi
software must be separable from Robert's personal contents.

## 3. CoS as intake and reader

CoS is not merely a reader of Planning Studio. It is a primary intake path for
personal ideas, notes, questions, and cross-domain observations.

CoS may eventually:

- create a durable capture when Robert explicitly asks;
- preserve the relevant conversation excerpt and attachments;
- add a clearly labeled summary;
- link new material to an existing thread or initiative;
- retrieve relevant prior records;
- propose that a capture be promoted into structured planning.

CoS may not independently approve a roadmap, rewrite historical sources,
delete a record, or initiate implementation.

The intended permission contract remains: propose freely, preserve within
defined bounds, promote or execute only with approval.

## 4. Guild's role

Guild governs the planning lifecycle once an idea becomes structured:

- ensure required metadata and provenance exist;
- organize design reviews and alternatives;
- record open questions and dependencies;
- prepare a decision package;
- record Robert's disposition;
- promote approved direction to the correct domain roadmap;
- keep build authorization separate from design approval.

Guild Planning Studio and Guild Build remain distinct capabilities.

## 5. Curator and other domains

Curator produces investigations, evidence, research briefings, and synthesis.
Other domains produce their own typed artifacts and operational evidence.

A domain artifact may support a Planning Studio initiative without moving out
of its domain's conceptual custody. The durable repository records the link.
The same source may inform several initiatives without being duplicated and
silently diverging.

## 6. Source-of-truth model

For Planning Studio records, the versioned file repository is canonical.

Future projections may include:

- PostgreSQL for structured metadata, filtering, status, and full-text search;
- a graph database for relationships such as `SUPPORTS`, `SUPERSEDES`,
  `DERIVED_FROM`, `CONFLICTS_WITH`, and `REUSED_IN`;
- semantic indexes for retrieval;
- Guild and CoS interfaces.

Every projection must be reproducible from durable records or have a defined
export-and-restore contract. A UI or database must not become the only place a
decision exists.

## 7. Privacy and portability

The intended personal repository is private. It may contain material that is
appropriate for Robert and his agents but not for a public code repository.

- Secrets and credentials never enter Git.
- Client or third-party confidential material requires its own explicit
  handling rules.
- Large or restricted sources may live in private object storage, but the
  repository retains a manifest, checksum, provenance, and retrieval status.
- Backups must exist in more than one failure domain before the repository can
  claim strong durability.

## 8. Relationship to mini-moi versions

The current mini-moi release is 1.1. This charter does not assign work to 1.2,
1.3, or any later release.

The phrase "mini-moi 2.0" is a design horizon only. Incremental 1.x releases
should be earned through real use and measurable personal benefit. A 2.0
designation is appropriate only after the accumulated system is materially
different, actively used, and demonstrably valuable to Robert.

## 9. Non-goals

This charter does not:

- create or approve a mini-moi roadmap;
- authorize software implementation;
- define a commercial offering;
- design a SaaS or multi-tenant product;
- require PostgreSQL, a graph database, or a Planning Studio UI;
- declare that mini-moi 2.0 will be built;
- migrate the existing OpenClaw workspace;
- publish personal data.

## 10. Review questions

1. Is the boundary between CoS continuity and Guild planning clear enough?
2. What exact user action makes a conversation durable?
3. Can CoS write directly to an inbox, or must another service validate every
   capture first?
4. Which records are immutable, and which receive mutable metadata?
5. What belongs in Git versus an encrypted data store?
6. How should an artifact participate in several initiatives without copies?
7. What minimum backup and integrity controls justify "never lost"?

