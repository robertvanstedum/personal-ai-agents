# CoS, Guild, and Curator Boundary Hypothesis

**Version:** 0.1  
**Date:** 2026-09-02  
**Status:** Exploratory; intended to challenge current domain boundaries

## Problem

Current architecture describes CoS and Guild as distinct, but CoS is still
implemented physically within the Guild area. Curator research and personal
memory also cross current file and application boundaries.

The Open-subject Curator example shows why ownership cannot be assigned only by
folder:

- the origin was Robert's personal curiosity;
- CoS may need to remember and reconnect it;
- Curator produced the substantive research artifact;
- Guild Planning Studio may assess a resulting platform capability;
- any implementation would later enter Guild Build.

## Proposed distinction

| Capability | Primary question | Responsibility |
|---|---|---|
| CoS | What matters to this person, and what context should carry forward? | Intake, continuity, retrieval, cross-domain connection, judgment |
| Curator | What should be investigated, and what does the evidence show? | Research, sources, briefings, synthesis, evolving questions |
| Guild Planning Studio | What has this idea become, and is it ready for a decision? | Structure, alternatives, review, disposition, promotion preparation |
| Guild Build | How is approved work delivered and operated correctly? | Specification, implementation, verification, release, operations |
| Robert | What becomes authoritative? | Ownership, approval, rejection, and priority |

## Origin, steward, and domain are different

Every significant record may declare:

- `origin`: where it first appeared;
- `steward`: who is responsible for its current lifecycle stage;
- `domains`: all domains affected by or contributing to it;
- `relationships`: how it connects to other records.

Example:

```json
{
  "origin": "cos_conversation",
  "steward": "guild_planning_studio",
  "domains": ["curator", "cos", "guild"],
  "status": "exploring"
}
```

This avoids treating a cross-domain initiative as owned exclusively by the
folder where the first file was saved.

## CoS capture boundary

CoS should be able to preserve material without turning it into planned work.

Proposed actions:

| Robert's intent | Durable result |
|---|---|
| "Remember this" | Curated context or memory record |
| "Save this idea" | Capture in the personal inbox |
| "Add this to the Curator thread" | Link to an existing investigation |
| "This deserves design work" | Proposed Planning Studio intake |
| "Promote this direction" | Decision and explicit roadmap candidate |

The wording and automation are not yet decided. Explicit intent is more
important than exact command syntax.

## Shared repository, distinct authorities

Using one private personal repository does not merge the domains. It provides
a shared substrate with typed access and lifecycle boundaries.

- CoS does not become the build coordinator.
- Guild does not own Robert's identity or all personal memory.
- Curator does not own every personal curiosity.
- An initiative does not become a roadmap merely by being durable.

## Implementation implications to investigate later

- CoS may deserve a sibling `domains/cos` implementation boundary rather than
  remaining physically under Guild.
- A repository service may validate CoS writes rather than giving an agent
  unrestricted Git access.
- Shared record schemas should belong to the platform or private-repository
  contract, not to one domain.
- Permissions may vary by record type even inside a personal instance.

These are design implications, not authorized refactors.

## Review tests

1. Can a personal note remain useful without becoming planning?
2. Can Curator produce an artifact without owning the initiative it informs?
3. Can Planning Studio preserve uncertainty without creating build pressure?
4. Can Guild receive an approved direction without needing every private
   source in its build context?
5. Can another agent replace CoS without losing the accumulated record?

