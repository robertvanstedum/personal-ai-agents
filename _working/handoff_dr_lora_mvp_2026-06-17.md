# Handoff — Decision Record MVP + LoRA Capability Initiative
*Created: 2026-06-17 — Claude.ai*
*Audience: Claude Code*
*Scope: Minimum viable build — introduce DR practice now, visible on GitHub*

---

## What this is

This handoff introduces the Decision Record practice as the first feature
in mini-moi's LoRA capability plan. The goal is to start capturing design
reasoning now — what was considered, what was rejected, and why — so that
when the local LLM training pipeline is ready, the signal is already there.

This is deliberately minimal. Two specs. Four documents to commit. One new
directory structure. No UI required yet. The practice starts with a folder
and a document.

---

## Why now

Design sessions produce reasoning that specs don't capture. Specs say what
to build. Decision records say what was considered, what was rejected, and
what the failure modes are. This reasoning is the primary training signal
for LoRA — the local LLM adaptation planned in the Learning System Roadmap.

Starting now means the inventory accumulates from the beginning. Starting
later means losing the early reasoning that shaped the whole system.

The GitHub visibility matters: this is a real capability initiative, not
a process experiment. It belongs on the roadmap as a named first step.

---

## Spec 1 — Commit the DR practice documents

### Files to commit to repo

**New file:** `docs/DECISION_RECORD_PRACTICE.md`
*Source:* `docs_DECISION_RECORD_PRACTICE_2026-06-17.md`
The reference document for the practice. Explains the why, the habit,
when to produce a DR, Claude's review role, Robert's periodic role.
Keep this short — it is the document all agents read.

**New file:** `docs/DESIGN_SESSION_PROMPT.md`
*Source:* `docs_DESIGN_SESSION_PROMPT_2026-06-16.md`
The prompt pasted at the start of any significant design conversation.
Produces Decision Records on command. Includes Claude review checklist.

**New file:** `docs/LEARNING_SYSTEM_ROADMAP.md`
*Source:* `docs_LEARNING_SYSTEM_ROADMAP_2026-06-16.md`
The phased plan for institutional memory — Phase 0 (now, habits),
Phase 1 (RAG), Phase 2 (LoRA), Phase 3 (local-first). Decision Records
are Phase 0, Step 0.2. This is the GitHub-visible roadmap document that
frames DRs as part of a larger initiative.

**New file:** `docs/GESPRACHE_ROADMAP.md`
*Source:* `docs_GESPRACHE_ROADMAP_2026-06-16.md`
Gespräche mobile vision and build queue. Referenced by the learning
system as the domain where LoRA signal accumulates first.

### New directory structure

```
docs/
  DECISION_RECORD_PRACTICE.md     ← new
  DESIGN_SESSION_PROMPT.md        ← new
  LEARNING_SYSTEM_ROADMAP.md      ← new
  GESPRACHE_ROADMAP.md            ← new
  decision-records/
    README.md                     ← new (see content below)
    drafts/
      .gitkeep                    ← new (empty, keeps folder in git)
    dr_gesprache_mobile_priority_2026-06-16.md   ← new (first DR)
    dr_guild_roadmap_database_cancelled_2026-06-17.md  ← new (first cancelled DR)
```

**`docs/decision-records/README.md` content:**
```markdown
# Decision Records

Decision Records capture the reasoning behind significant mini-moi
design decisions — alternatives considered, what was rejected and why,
constraints, assumptions, and the principles that emerged.

This is the training signal for the local LLM we are building toward.
Specs say what was built. Decision Records say how we decided to build it.

## How it works

- Session agents produce DR drafts to `drafts/`
- Claude reviews drafts against the checklist in `docs/DESIGN_SESSION_PROMPT.md`
- Claude Code commits approved DRs here
- Robert does periodic LoRA candidate tagging — no per-DR review required

See `docs/DECISION_RECORD_PRACTICE.md` for full guidance.
```

### Amend `docs/HANDOFF_PROCESS.md`

Add this section (create the file if it doesn't exist):

```markdown
## Decision Records

For significant design decisions, produce a Decision Record alongside
the spec. See `docs/DECISION_RECORD_PRACTICE.md` for when and how.

DR naming: `dr_[topic]_[YYYY-MM-DD].md`
DR location: `docs/decision-records/` (drafts go to `docs/decision-records/drafts/`)
Spec reference: add one line — *Reasoning: see `dr_[name].md`*

Claude reviews drafts. Robert is not in the per-DR loop.
```

### Amend `README.md`

Add a **Learning System** section to the docs area. All four new docs
sit under this heading — keeping the narrative coherent as a named
initiative rather than scattered doc links:

```markdown
## Learning System

mini-moi builds institutional knowledge over time — capturing design
reasoning now so the local LLM training pipeline has signal when
Phase 2 (LoRA) arrives. Decision Records are the first feature of
this capability plan.

- [Learning System Roadmap](docs/LEARNING_SYSTEM_ROADMAP.md) —
  phased plan: Decision Records (Phase 0) → RAG (Phase 1) →
  LoRA training (Phase 2) → local-first operation (Phase 3)
- [Decision Record Practice](docs/DECISION_RECORD_PRACTICE.md) —
  the highest-signal artifacts for future LoRA adaptation.
  How design reasoning is captured, reviewed, and committed.
- [Design Session Prompt](docs/DESIGN_SESSION_PROMPT.md) —
  structured design conversations across Claude, Grok, and local LLM.
  Produces Decision Records on command.
- [Gespräche Roadmap](docs/GESPRACHE_ROADMAP.md) —
  mobile-first voice practice vision. The domain where LoRA signal
  accumulates first.
```

### Commit the three existing DRs

All three are pre-reviewed by Claude.ai — commit directly to
`docs/decision-records/`, not via the drafts folder.

`docs/decision-records/dr_gesprache_mobile_priority_2026-06-16.md`
*Source:* `dr_gesprache_mobile_priority_2026-06-16.md`
First decision record. Captures the reasoning behind Gespräche mobile
priority and the learning system foundation approach.

`docs/decision-records/dr_guild_roadmap_database_cancelled_2026-06-17.md`
*Source:* `dr_guild_roadmap_database_cancelled_2026-06-17.md`
First cancelled DR. Records that the database-backed roadmap view was
specced and replaced same session with a repo-backed approach.

`docs/decision-records/dr_decision_record_practice_mvp_2026-06-17.md`
*Source:* `dr_decision_record_practice_mvp_2026-06-17.md`
The DR for this design session itself. Captures what was considered and
discarded in designing the DR practice — database UI, Robert-reviews
model, fully automatic model, full pipeline — and why the MVP landed
where it did. Includes Grok parallel review findings. This is the
first DR produced using the process it describes.

### Definition of Done — Spec 1

- All four new `docs/` files exist and render correctly on GitHub
- `docs/decision-records/` directory exists with README.md
- `docs/decision-records/drafts/` directory exists with `.gitkeep`
- All three DRs committed and render correctly on GitHub
- `docs/HANDOFF_PROCESS.md` includes Decision Records section
- `README.md` links to all four new docs under a "Learning System" heading
- Verified: all internal cross-references resolve
- Verified: `docs/LEARNING_SYSTEM_ROADMAP.md` is visible and readable
  on GitHub as a top-level initiative document

### Commit — Spec 1

```
Introduce Decision Record practice — LoRA capability Phase 0.

Add docs/DECISION_RECORD_PRACTICE.md, docs/DESIGN_SESSION_PROMPT.md,
docs/LEARNING_SYSTEM_ROADMAP.md, docs/GESPRACHE_ROADMAP.md.
Create docs/decision-records/ with first two DRs committed.
Amend HANDOFF_PROCESS.md and README.md.

Decision Records are the first feature of the LoRA capability plan:
capturing design reasoning now so the local LLM training pipeline
has signal when Phase 2 arrives.
```

---

## Spec 2 — Dev Agent filing rules

*Source spec:* `spec_dev_agent_filing_rules_2026-06-17.md`

This is a separate commit — fixing the Dev Agent before the DR practice
goes live prevents junk rows immediately.

### What it does

Updates the Dev Agent's `_working/` filing logic to an allowlist:
only `spec_*.md` and `build_plan_*.md` are auto-filed. Everything else
is ignored. Introduces a `design` phase for specs missing DoD or Commit
sections.

### Key rules

```
spec_*.md with DoD + Commit present    → file as status: ready
spec_*.md missing either section       → file as status: design
build_plan_*.md                        → file as status: ready
everything else                        → do not auto-file
docs/decision-records/drafts/*         → never filed, never indexed
docs/decision-records/*.md             → never filed (not build items)
```

### Cleanup

Delete `guild.design_log` row id 48 (`session_summary_2026-06-16.md`,
no title, `incomplete`). Audit any other rows with empty titles or
non-spec filenames and delete junk rows.

### Definition of Done — Spec 2

- Dev Agent allowlist active: only `spec_*` and `build_plan_*` auto-filed
- `spec_*.md` without DoD files as `design`, with DoD files as `ready`
- Row 48 deleted, junk rows cleaned
- `docs/decision-records/drafts/` never appears in queue
- Verified: drop `session_summary_*.md` into `_working/` — does not file
- Verified: drop incomplete `spec_*.md` — files as `design`
- Verified: drop complete `spec_*.md` — files as `ready`

### Commit — Spec 2

```
Dev Agent: allowlist filing rules, design phase for incomplete specs,
junk row cleanup.
```

---

## Build order

**Spec 2 first** — fix the filing rules before the new documents land
in `_working/`. Prevents any of the new docs from accidentally filing
as queue items.

**Spec 1 second** — commit the DR practice documents and directory
structure. This is the visible GitHub initiative.

---

## What is NOT in this build

Deliberately excluded — do not build these now:

- Guild Decisions view UI (`spec_guild_roadmap_decisions_2026-06-17.md`)
  — queued for later, not MVP
- Guild Roadmap view maturity — same
- Post-commit hook for DR indexing — comes when there's an inventory
  worth indexing
- LoRA export button — Phase 2, not now
- Any automated DR production pipeline — the practice starts with agents
  producing DRs manually in sessions

The MVP is a folder, a README, and four documents. The practice starts
immediately. The infrastructure follows when the inventory justifies it.

---

## Source files for this handoff

All in current session outputs:

```
docs_DECISION_RECORD_PRACTICE_2026-06-17.md   → docs/DECISION_RECORD_PRACTICE.md
docs_DESIGN_SESSION_PROMPT_2026-06-16.md      → docs/DESIGN_SESSION_PROMPT.md
docs_LEARNING_SYSTEM_ROADMAP_2026-06-16.md    → docs/LEARNING_SYSTEM_ROADMAP.md
docs_GESPRACHE_ROADMAP_2026-06-16.md          → docs/GESPRACHE_ROADMAP.md
dr_gesprache_mobile_priority_2026-06-16.md           → docs/decision-records/
dr_guild_roadmap_database_cancelled_2026-06-17.md    → docs/decision-records/
dr_decision_record_practice_mvp_2026-06-17.md        → docs/decision-records/
spec_dev_agent_filing_rules_2026-06-17.md     → build spec (Spec 2)
```

---

*Handoff · 2026-06-17 · Claude.ai*
*Two specs. Build Spec 2 first, then Spec 1.*
