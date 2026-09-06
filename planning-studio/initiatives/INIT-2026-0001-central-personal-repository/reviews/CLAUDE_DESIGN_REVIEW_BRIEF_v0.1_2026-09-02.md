# Claude Design Review Brief — Central Personal Repository

**Version:** 0.1  
**Date:** 2026-09-02  
**Review type:** Independent design critique  
**Decision owner:** Robert

## Context

Robert and Codex developed an early direction for a durable central personal
repository and a Guild Planning Studio. The work remains exploratory. mini-moi
is currently at release 1.1; no 2.0 scope or build has been approved.

Commercial use is explicitly outside this review.

## Read in this order

1. `planning-studio/README.md`
2. `planning-studio/governance/GUILD_PLANNING_STUDIO_CHARTER_v0.1_2026-09-02.md`
3. `planning-studio/governance/RECORD_LIFECYCLE_v0.1_2026-09-02.md`
4. `planning-studio/initiatives/INIT-2026-0001-central-personal-repository/README.md`
5. Every file under that initiative's `documents/` directory
6. `planning-studio/initiatives/INIT-2026-0002-curator-open-subject/README.md`
7. Its `initiative.json`, source manifest, and three unchanged source files
8. The schemas and registries at the repository root

## Review request

Assess the actual structure and documents rather than this summary.

Please identify:

1. unclear or conflicting boundaries among CoS, Guild Planning Studio, Guild
   Build, Curator, and Robert;
2. places where the structure prematurely commits to an architecture;
3. records or metadata required for provenance, privacy, durability, or future
   migration that are missing;
4. places where source, interpretation, decision, and promotion can still be
   confused;
5. whether the initiative-first structure represents cross-domain work
   cleanly;
6. risks in using Git as the durable document layer;
7. what should remain manual until real use proves automation worthwhile;
8. the smallest useful experiment that would test the central hypothesis;
9. anything that improperly implies mini-moi 2.0 is committed;
10. anything commercial that should be removed.

## Requested output

Return:

- overall verdict;
- highest-risk design issues in priority order;
- proposed revisions to structure or terminology;
- missing decisions;
- smallest evidence-producing next step;
- explicit recommendation: continue exploring, revise, pause, or discard.

Do not produce an implementation plan or assume approval.

