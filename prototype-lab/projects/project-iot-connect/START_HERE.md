# Connect HQ Demo Program — Start Here

**Status:** Design package for review  
**Created:** 2026-09-02  
**Decision owner:** Robert  
**Repository safety:** Commit-safe, synthetic, vendor-neutral project material only

## Purpose

This is the shared project folder for turning the existing Connect HQ IoT
prototype into a repeatable standalone demonstration and then deriving a
complete fiber order-to-activate-to-bill demonstration from its approved
release.

The existing working prototype remains in its current location until the
standalone-beta implementation is reviewed. This directory is the durable,
tracked project home for plans, research, handoffs, contracts, test evidence,
and presentation material.

## Read in this order

1. [BRIEF.md](BRIEF.md) — goals, workstreams, sequence, and acceptance gates.
2. [DECISIONS.md](DECISIONS.md) — decisions already made and open decisions.
3. [handoffs/CLAUDE_CODE_STANDALONE_BETA_HANDOFF.md](handoffs/CLAUDE_CODE_STANDALONE_BETA_HANDOFF.md)
   — implementation brief for the baseline release.
4. [handoffs/CLAUDE_AI_FIBER_BACKOFFICE_RESEARCH_BRIEF.md](handoffs/CLAUDE_AI_FIBER_BACKOFFICE_RESEARCH_BRIEF.md)
   — sourced research assignment for fiber BSS/OSS.
5. [research/FIBER_BACKOFFICE_WORKING_MODEL.md](research/FIBER_BACKOFFICE_WORKING_MODEL.md)
   — initial domain model and utility-service-order crosswalk.
6. [design/CONNECT_HQ_FIBER_ROLE_BOUNDARY.md](design/CONNECT_HQ_FIBER_ROLE_BOUNDARY.md)
   — required pre-build definition of Connect HQ's changed role in the fiber demo.
7. [design/CLAUDE_AI_SPEC_DISPOSITION_2026-09-02.md](design/CLAUDE_AI_SPEC_DISPOSITION_2026-09-02.md)
   — review and controlled incorporation of Claude AI's standalone, fiber, integration, and access deliverables.
8. [build/OVERNIGHT_STANDALONE_BUILD_PLAN_2026-09-02.md](build/OVERNIGHT_STANDALONE_BUILD_PLAN_2026-09-02.md)
   — authorized isolated candidate scope for Claude Code and Codex verification.
9. [research/PAYMENTS_ADDENDUM_DISPOSITION.md](research/PAYMENTS_ADDENDUM_DISPOSITION.md)
   — Codex review and scoped incorporation of Claude's payment addendum.
10. [learning/ZUORA_FIBER_ORDER_TO_CASH_LEARNING_PLAN.md](learning/ZUORA_FIBER_ORDER_TO_CASH_LEARNING_PLAN.md)
   — P0 interview-week Zuora process, architecture, access, and fiber-learning assignment.
11. [learning/SALESFORCE_CATALOG_CPQ_LEARNING_PLAN.md](learning/SALESFORCE_CATALOG_CPQ_LEARNING_PLAN.md)
   — secondary hands-on Salesforce learning path and upstream CRM context.
12. [build/FIBER_SALESFORCE_POC_BUILD_PLAN.md](build/FIBER_SALESFORCE_POC_BUILD_PLAN.md)
   — future Salesforce–Connect HQ–synthetic Zuora/payment applied build, gated
   by the earlier work.
13. [sources/SOURCE_REGISTER.md](sources/SOURCE_REGISTER.md) — public evidence and
   private-source handling.

## Work ownership

- **Robert:** decisions, hands-on learning, design approval, reviewed-diff
  approval, and final demonstration narrative.
- **Claude AI:** sourced fiber back-office, Zuora, and Salesforce design and
  learning research, with Zuora as the near-term interview priority; no
  repository implementation.
- **Claude Code:** standalone-beta implementation after approval of its handoff.
- **Codex:** initial fiber back-office working model, synthesis, technical review,
  and later second-pass review of the standalone-beta diff.

Repository edits remain serialized in accordance with the repository's ways of
working. Research and review may happen in parallel when they do not edit the
working tree.

## Current gates

| Gate | Requirement | Status |
|---|---|---|
| G0 — Package review | Robert approves this project structure and briefs | Pending |
| G0B — Isolated candidate | Claude Code produces candidate; Codex reviews diff and evidence | Authorized / in progress |
| G0A — Source promotion | Prototype runs from this tracked project folder | Not started |
| G1 — Baseline build | Standalone IoT demo passes portability and reset tests | Not started |
| G2 — Baseline release | Reviewed diff is approved and GitHub release is tagged | Not started |
| G3 — Fiber design | Sourced fiber model plus Salesforce and Zuora mappings are approved | Not started |
| G3A — Connect HQ role | Fiber-specific ownership, projection, command, event, and evidence boundaries are approved | Not started |
| G4 — Fiber build | End-to-end fiber scenario and controls pass | Not started |
| G5 — Fiber release | Reviewed diff and demonstration package are approved | Not started |

## Private context

Employer-specific interview documents, personal notes, credentials, tokens,
and unsanitized run evidence belong in the ignored `private/` or runtime storage.
They must not be committed. Public vendor documentation may be cited as
technical evidence, but the product and demo identity remain vendor-neutral.

## Step 0 — Promote the working prototype

After G0 approval, move the current prototype out of the gitignored `_working`
area and make this directory its tracked home. Execute the move safely as:

1. inventory the current project and identify local-only/runtime material;
2. copy commit-safe source and artifacts into this directory;
3. exclude `.venv`, caches, secrets, local databases, and unsanitized evidence;
4. run the current application from this directory before changing behavior;
5. verify the existing UI, APIs, database, integration mocks, reset, and tests;
6. declare this directory authoritative only after that verification; and
7. retire or archive the old ignored copy only after Robert approves the result.

Containerization and refactoring begin after this promotion gate passes.
