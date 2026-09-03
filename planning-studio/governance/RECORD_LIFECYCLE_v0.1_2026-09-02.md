# Planning Studio Record Lifecycle

**Version:** 0.1  
**Date:** 2026-09-02  
**Status:** Draft for design review

## Lifecycle

```text
ephemeral conversation
        |
        | explicit save or approved intake
        v
captured --> exploring --> design_review --> decision_pending
    |           |               |                  |
    |           |               |                  +--> promoted
    |           |               +---------------------> superseded
    |           +-------------------------------------> archived
    +-------------------------------------------------> discarded
```

`promoted` means that an explicit output was created in an authoritative
roadmap, specification, domain repository, or other named destination. It does
not mean implementation was approved.

## Status definitions

| Status | Meaning |
|---|---|
| `captured` | Preserved with provenance but not yet interpreted or structured |
| `exploring` | Actively gathering context, alternatives, or evidence |
| `design_review` | A bounded proposal is undergoing independent review |
| `decision_pending` | Review is complete and Robert must decide disposition |
| `promoted` | An explicit approved output exists elsewhere |
| `superseded` | A newer record replaces this one without erasing it |
| `archived` | Preserved but inactive; no negative judgment implied |
| `discarded` | Deliberately rejected with a recorded reason |

## Record types

| Type | Purpose |
|---|---|
| `capture` | A preserved thought, conversation excerpt, or note |
| `source` | Unchanged external or user-provided material |
| `research_artifact` | A briefing, synthesis, evidence set, or investigation output |
| `initiative` | A cross-domain question or possible direction under structured consideration |
| `proposal` | A bounded design offered for review |
| `review` | An independent critique of actual source material or a proposal |
| `decision` | Robert's dated disposition and reasoning |
| `pattern` | An extracted portion that may be reusable elsewhere |
| `promotion` | The trace from an approved decision to an authoritative destination |

## Version rules

- Stable record identity and version are different fields.
- A new version never overwrites a released or reviewed version.
- Source snapshots are immutable after intake.
- Corrections to a source snapshot create a new version and relation.
- Mutable lifecycle metadata may change, but changes must remain visible in
  Git history and include `updated_at`.
- `supersedes` is directional and names the exact prior record version.

## Disposition rules

Discarding is not deletion. A discarded record retains:

- its stable ID and title;
- the final version or source hash;
- the decision date;
- a concise reason;
- the decision owner;
- any replacement or related initiative.

Physical source deletion, when required for privacy, licensing, or security,
is a separate controlled action. Its tombstone and checksum remain unless that
retention is itself prohibited.

## Reuse states

Lifecycle and reuse are independent.

| Reuse state | Meaning |
|---|---|
| `not_assessed` | No reuse judgment has been made |
| `domain_specific` | Intentionally remains within its current context |
| `candidate` | Worth testing elsewhere but not yet proven |
| `validated` | Reused successfully in at least one materially different context |
| `rejected` | Assessed and unsuitable for general reuse |

An entire initiative does not become reusable merely because one component is.
Reusable portions should be extracted as their own pattern records with links
to the originating evidence.

