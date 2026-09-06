# Central Personal Repository Charter

**Version:** 0.1  
**Date:** 2026-09-02  
**Status:** Draft hypothesis for design review  
**Owner:** Robert  
**Implementation authority:** None

## 1. Purpose

The Central Personal Repository is a proposed durable home for the context that
makes one mini-moi instance belong to one person.

Its purpose is continuity: preserve enough original material, interpretation,
decisions, relationships, and personal state that future agents can understand
what developed and why without depending on one chat session, model vendor,
application process, or database instance.

## 2. Ownership invariant

The person owns the repository and the accumulated context within it.

For Robert's instance, Robert remains the owner even if:

- mini-moi stops being developed;
- CoS moves to another agent runtime;
- a model provider changes;
- a domain is removed;
- the system moves to another computer or hosting environment;
- the files are read without mini-moi.

Reusable software may be shared. Robert's content is not part of that reusable
distribution.

## 3. Proposed contents

The repository may eventually preserve:

- identity, principles, goals, and preferences;
- CoS memory and explicitly saved conversation captures;
- personal notes, questions, and early ideas;
- original source material and attachments;
- Curator research artifacts and evolving investigations;
- cross-domain threads and initiatives;
- Planning Studio proposals, alternatives, and reviews;
- decisions and their reasoning;
- reusable-pattern candidates and their evidence;
- domain-specific durable state or portable exports;
- provenance, version, relationship, sensitivity, and disposition metadata.

The repository should not become a dump of every generated token, log line, or
temporary file. Deliberate capture and retention classes are required.

## 4. Proposed logical structure

```text
personal-repository/
  identity/            durable self-description and principles
  context/             goals, preferences, and long-term context
  inbox/               newly saved captures awaiting classification
  library/             original sources and durable artifacts
  initiatives/         structured cross-domain investigations and designs
  planning-studio/     governance, templates, reviews, and promotion records
  decisions/           explicit decisions and rationale
  patterns/            extracted reuse candidates and validations
  domain-state/        durable domain exports or typed personal state
  registry/            machine-readable record and relationship indexes
  schemas/             record contracts and migrations
  archive/             closed, superseded, and discarded records
```

This is a logical model, not an approved physical implementation.

## 5. Record identity

Every durable record should eventually include:

- stable record ID;
- record type;
- title and description;
- origin and original creator;
- steward for the current lifecycle stage;
- one or more affected domains;
- created and updated timestamps;
- status and disposition;
- version and exact-content checksum where applicable;
- sensitivity and retention class;
- relationships to other records;
- canonical location;
- source location or retrieval status when the content cannot be stored.

Stable identity is required for PostgreSQL and graph projections later.

## 6. Provenance rule

For important material, preserve three separable layers:

1. **Source** — the original file, excerpt, message, or external material.
2. **Interpretation** — a human or agent summary, critique, or extraction.
3. **Decision** — Robert's explicit disposition and reasoning.

An interpretation may be corrected without modifying the source. A later
decision may change without pretending the earlier decision never occurred.

## 7. Personal memory and planning

Personal memory and structured planning are related but not identical.

- CoS memory answers what matters, what happened, and what may be relevant.
- Planning Studio answers what is under structured consideration and whether
  it is ready for a decision.
- A capture can remain personal context indefinitely without becoming an
  initiative.
- A Planning Studio initiative links back to the captures and sources that
  explain why it exists.

This prevents the planning repository from swallowing all personal memory and
prevents CoS memory from becoming an ungoverned roadmap.

## 8. Persistence layers

The proposed architecture favors a durable file representation for narrative,
sources, decisions, and manifests. It does not require every domain's live
transactional data to live in Git.

Potential layers are:

| Layer | Possible responsibility |
|---|---|
| Versioned files | Canonical source, narrative, provenance, and decisions |
| PostgreSQL | Structured query, status, filtering, access metadata, and full-text search |
| Graph database | Multi-domain and provenance relationships |
| Semantic index | Similarity retrieval and agent context selection |
| Domain databases | Operational state with explicit export and restore |
| Object storage | Large, binary, or restricted sources with checksums |

No projection should become impossible to rebuild or export. The exact source
of truth must be declared per record type rather than assumed globally.

## 9. Portability

Portability means more than copying application code. A portable instance must
separate:

```text
Reusable mini-moi distribution
  core + domain packs + generic baselines + migration tools

from

Person-owned instance
  identity + accumulated context + domain state + decisions + history
```

A second person starts from the reusable distribution and clean baselines, not
from Robert's repository. Their instance develops independently.

## 10. Durability target

"Never lost" is a design objective, not a literal guarantee. A credible target
requires:

- a private remote plus a local copy;
- a second backup failure domain;
- immutable source versions and checksums;
- periodic integrity and restoration tests;
- no silent deletion;
- explicit tombstones when content must be removed;
- documented recovery without dependence on one agent or UI.

## 11. Existing material

The current OpenClaw workspace is a precursor containing memory, personal
context, plans, generated output, operational state, and code. It must be
inventoried before any migration.

The future repository should not be created through an undifferentiated copy.
Items must be classified, checked for sensitivity and secrets, assigned
provenance, and migrated without destroying the original.

## 12. Exit and abandonment

This initiative is allowed to fail. If Robert does not use the repository or
mini-moi six months from now, the initiative should be archived honestly.
The preserved design and evidence remain available, but no release should be
manufactured to justify the work.

## 13. Questions still open

- Is one private Git repository sufficient, or should large and sensitive
  content use a companion encrypted store from the start?
- What exact CoS command or UI action means "save this"?
- Which CoS memories should be curated automatically, if any?
- How are access and redaction handled when an artifact spans domains?
- Which domain-state types must be exportable for portability?
- What evidence threshold justifies PostgreSQL or graph activation?
- What becomes part of a clean personal-instance template?

