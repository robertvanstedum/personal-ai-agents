# mini-moi Future Direction — Central Personal Repository

**Version:** 0.1  
**Date:** 2026-09-02  
**Status:** Exploratory direction  
**Release commitment:** None  
**Current mini-moi release:** 1.1

## Direction statement

mini-moi may evolve toward a portable, person-owned system whose durable center
is a private personal repository. CoS, Guild, Curator, and other domains would
read from and contribute to that repository through explicit responsibilities
and lifecycle rules.

Applications, models, databases, and infrastructure may change. The person's
accumulated context should remain understandable, recoverable, and owned by the
person.

## Why this direction is being explored

Current mini-moi information is distributed across application repositories,
an OpenClaw workspace, domain data, memory files, roadmaps, working folders,
databases, and conversations with several agents. Useful context can be
difficult to classify or reconstruct after a conversation ends.

The Open-subject Curator discussion exposed the problem clearly. A personal
curiosity produced a research briefing, suggested a Curator capability, raised
questions about CoS continuity, and became a possible Guild planning topic.
No single domain exclusively owns that chain.

## Central hypothesis

A durable personal repository could become the shared knowledge substrate for
mini-moi while leaving the domains distinct:

```text
Robert
  |
  v
CoS -- capture, recall, connect
  |
  v
Private personal repository
  ^              ^               ^
  |              |               |
Curator     Planning Studio    Other domains
research      maturation       evidence/state
                 |
                 v
          approved roadmap/spec
                 |
                 v
             Guild Build
```

The repository is not itself an agent. It persists across agents.

## Expected characteristics

- Private by default and owned by the person.
- Human-readable without a running mini-moi application.
- Fully scannable by authorized agents.
- Versioned with complete provenance.
- Able to preserve original material beside later interpretation.
- Able to represent one record across several domains and initiatives.
- Able to archive or discard from active work without erasing history.
- Portable across devices and hosting choices.
- Compatible with PostgreSQL, graph, semantic, and UI projections later.
- Separable from reusable mini-moi code and generic domain baselines.

## Evidence before architecture

This direction should advance only through actual use. The first useful test is
not a database build. It is whether Robert saves real material, later retrieves
it, and benefits from the continuity.

Possible evidence sequence:

1. Preserve a small number of real design conversations and sources manually.
2. Retrieve them during later decisions without reconstructing the background.
3. Let CoS read the repository and cite the relevant records.
4. Let CoS create bounded captures after explicit instruction.
5. Add structured indexes only when file scanning becomes limiting.
6. Test a clean second-person instance only after Robert's instance is useful.

## Version position

This direction is not named as mini-moi 2.0 scope. The term 2.0 is useful only
as a horizon for materially different behavior.

mini-moi should continue through 1.2, 1.3, 1.4, and later incremental releases
only when each increment is used and beneficial. A 2.0 designation must be
earned by sustained use, demonstrated portability, and a meaningful change in
the system's role.

## Future reference deck

If this direction eventually becomes a verified release, its release
definition should include a concise presentation deck covering:

1. the prior operating model;
2. the personal repository and ownership boundary;
3. CoS, Guild, Curator, and domain responsibilities;
4. capture, planning, decision, and build lifecycle;
5. portable software versus personal state;
6. evidence from a clean second-person initialization;
7. what the earned release provides in actual use.

The deck is an explanatory release artifact. It is not currently an investor,
sales, or commercial pitch.

## Non-goals

- Forecasting commercial use.
- Designing enterprise multi-tenancy or SaaS.
- Creating a private repository before the ownership model is reviewed.
- Moving all current mini-moi data into one store.
- Declaring files, PostgreSQL, or a graph database universally authoritative.
- Assigning implementation to a release before use demonstrates the need.

