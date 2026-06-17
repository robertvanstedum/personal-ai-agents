# Decision Record — Decision Record Practice & LoRA Capability MVP
*2026-06-17 · Claude.ai · mini-moi*

---
type: decision-record
dr-type: design
domain: platform
status: active
lora-candidate: yes
---

## Decision

Introduce Decision Records as the first feature of the LoRA capability
plan. The MVP is a folder, a README, and four documents committed to the
repo. Agents produce DR drafts, Claude reviews them autonomously, Claude
Code commits. Robert is not in the per-DR loop — his only touch is
periodic LoRA candidate tagging. The practice starts now; infrastructure
(UI, indexing, export) follows when there is an inventory worth using.

---

## Context

Design sessions produce reasoning that specs don't capture — what was
considered, what was rejected, what failed, and why. This reasoning is
the primary training signal for LoRA adaptation of the local LLM
(Learning System Phase 2). Without it, the local LLM can learn what was
built but not how to think about what to build.

The question was how to start capturing this without adding overhead
that competes with the actual build work. The contract ends August 3
(~7 weeks), the build queue is active, and Robert is already at weekly
Claude usage limits. Any process that requires Robert's attention per DR
would not survive contact with reality.

---

## Alternatives considered

### Full database-backed Roadmap + Decisions UI (specced and cancelled same session)

**What it was:** A `guild.roadmap_items` table, full CRUD UI, status
flows, domain filters, seed data, the works. Specced as
`spec_guild_roadmap_view_2026-06-17.md`.

**Why it was attractive:** Clean UI, queryable, CoS could update
programmatically, everything visible in one place.

**Cancelled because:** Architectural mismatch. A database requires a
sync mechanism between the repo and the database — two sources of truth
that can drift. The repo already provides versioning, history, and a
source of truth without any sync mechanism. GitHub commit as the gate
is enforceable by architecture (items that aren't committed literally
cannot appear) rather than by policy. The existing markdown renderer
already works. Restructuring the markdown document achieves the same
visible result at a fraction of the build cost.

**Replaced by:** Repo-backed approach — structured `_working/ROADMAP.md`
with improved renderer. Roadmap and Decisions views are queued as
medium-priority, not MVP.

> [FLAG: principle — the repo is the source of truth; databases are
> rebuildable projections. Don't add a database when the repo already
> serves the need.]

---

### Robert reviews every DR before commit

**What it was:** All DR drafts go to `_working/drafts/`, Robert reviews
each one, approves or discards, then hands to Claude Code for commit.

**Why it was attractive:** Quality control, Robert maintains visibility
and judgment over what enters the training set.

**Rejected because:** Robert is already at weekly Claude limits and
spends significant time in design sessions. Adding a per-DR review loop
is double work — Robert is present for the design conversation that
produces the reasoning; reviewing a summary of what just happened adds
no new judgment. The failure mode is a well-designed process that never
gets used because it feels heavy. That is worse than a simpler habit
that actually runs.

> [FLAG: principle — don't add review overhead for artifacts that
> summarize conversations Robert was already part of. The design
> conversation is the input; the DR is the output.]

---

### Fully automatic — no review at all

**What it was:** Agents produce DRs directly to `docs/decision-records/`,
Claude Code commits without any review pass.

**Why it was attractive:** Zero friction, maximum automation, consistent
with the model-agnostic / agent-first philosophy.

**Rejected because:** Quality without any gate risks noise in the LoRA
training set. A DR that captures the conversation rather than the
reasoning, or that documents a trivial decision as if it were significant,
degrades the signal. LoRA trained on low-quality DRs learns the wrong
patterns. Some quality gate is needed — the question is who runs it
at what cost.

---

### Claude as reviewer (selected)

**What it is:** Agents produce drafts to `docs/decision-records/drafts/`.
Claude reviews against a 7-point checklist (real decision with genuine
alternatives, specific rejection reasons, LoRA-useful principles, flags
collected, correct dr_type, frontmatter complete, superseded DRs
referenced). Claude corrects in place if needed, then moves to
`docs/decision-records/` and hands to Claude Code for commit.

**Why this works:** Claude understands the format, the criteria, and
the project context. The review is a structured pass against specific
criteria — not subjective judgment. Robert is completely out of the
per-DR loop. One pass, one outcome. The quality gate exists without
adding to Robert's workload.

**Grok parallel review confirmed:** This approach is correctly scoped.
"Starts with folder + documents. No infrastructure yet. Excellent."
The conservative production criteria and Claude review together provide
sufficient quality control for the training signal without over-engineering.

---

### Post-commit hook + UI + LoRA export button (deferred)

**What it was:** Automated index rebuild on commit, Decisions view in
Guild with filters, LoRA export button when filtered to candidates.

**Why it was attractive:** Full pipeline, everything visible, one-click
export to training set.

**Deferred because:** There is no inventory yet. Building retrieval
infrastructure before having data to retrieve is the trap Grok explicitly
flagged: "It avoids the common trap of building fancy retrieval
infrastructure before you have good data." The UI and export are queued
as medium-priority. They ship when the inventory justifies them.

> [FLAG: principle — don't build retrieval infrastructure before you
> have data worth retrieving. Phase 0 generates the signal; Phase 1
> uses it.]

---

### DR production on every session

**What it was:** Every design session produces a DR regardless of
whether a significant decision was made.

**Rejected because:** Volume without signal. Sessions that produce spec
refinements, wording adjustments, or routine confirmations don't contain
reasoning worth capturing. LoRA trained on low-signal DRs learns noise.
Conservative criteria in the Design Session Prompt are the volume control:
produce a DR only when genuine alternatives were considered, negative
reasoning would otherwise be lost, or a principle emerged.

---

## Constraints that shaped this

**Robert's time:** At weekly Claude limits, active build queue, contract
ends in 7 weeks. Any process requiring per-DR attention from Robert
would not survive. This constraint drove the Claude-as-reviewer model
and the periodic-only LoRA tagging.

**Signal before infrastructure:** The value is in accumulating reasoning
now. Building infrastructure to browse an empty inventory adds no value.
The same discipline that held Neo4j and Postgres applies here — don't
build the structure until the foundation justifies it.

**Daily-use system:** mini-moi is used daily. Operational complexity that
could disrupt it requires strong justification. A folder of markdown files
adds zero operational risk.

**Mac Mini compute:** Full fine-tuning is impractical on current hardware.
LoRA (adapter-only training) is. This constrains the training approach
but not the outcome — LoRA is actually the better fit for a project that
changes frequently.

---

## Assumptions made

**Claude review quality is sufficient.** The 7-point checklist produces
consistent results across sessions. If review quality slips — DRs are
too permissive or too conservative — the checklist is tuned, not the
overall model.

**Conservative production criteria hold.** Agents using the Design
Session Prompt produce DRs only when criteria are genuinely met. If
volume creeps up without signal improving, revisit the criteria and
tighten them before adding review overhead.

**Local model quality improves sufficiently in 6 months** for LoRA
to produce meaningful adaptation. If base model capability doesn't
advance as expected, the DRs still have value as project documentation —
they are not wasted signal even if the LoRA timeline shifts.

---

## Known failure modes

**Process weight beats the habit.** The most likely failure mode is a
process that feels heavier than it is and quietly stops being used.
Mitigation: the Design Session Prompt explicitly says "don't let this
get in the way of other builds" and "quality over volume." If a session
produces no DR, that is correct behavior.

**DR captures conversation, not reasoning.** An agent that summarizes
the session rather than distilling the decision surface produces noise.
Mitigation: Claude review checklist specifically checks for genuine
rejection reasoning and LoRA-useful principles — not just "we discussed X."

**LoRA tagging never happens.** If Robert never does the periodic scan,
the training set never gets curated. Mitigation: the Decisions view (when
built) surfaces untagged DRs. The periodic scan is lightweight by design —
not a review, just a yes/no tag per row.

---

## Principles this decision reflects

- "Decision Records are the highest-signal artifacts for future LoRA
  adaptation of the local LLM. They are produced now so the signal
  exists when Phase 2 arrives — not reconstructed from memory."

- "The repo is the source of truth. Databases are rebuildable projections.
  Don't add a database when the repo already serves the need."

- "Don't build retrieval infrastructure before you have data worth
  retrieving. Phase 0 generates the signal; Phase 1 uses it."

- "Don't add review overhead for artifacts that summarize conversations
  Robert was already part of."

- "A well-designed process that never gets used is worse than a simple
  habit that actually runs."

- "Conservative production criteria are the volume control. Quality over
  volume. A session that produces no DR because there was nothing worth
  capturing is correct behavior."

- "The same discipline that held Neo4j and Postgres applies to
  infrastructure generally — don't build the structure until the
  foundation justifies it."

---

## What this is not

**Not a commitment to the full Decisions UI now.** The Guild Decisions
view, post-commit indexing, and LoRA export button are queued at medium
priority. They ship when the inventory justifies them — not before.

**Not a replacement for specs.** DRs capture reasoning; specs capture
what to build. Both are needed. Neither replaces the other. Claude Code
still only needs specs.

**Not a fixed process.** The Design Session Prompt and checklist are
provisional for the first month. If the habit forms but the format
produces low-signal DRs, the format changes. If the checklist is too
strict and valid DRs are being deleted, the criteria loosen. Tune the
prompt, not the overall model.

**Not a native app, a database, a sync mechanism, or retrieval
infrastructure.** Those were all considered and deferred or rejected.
A folder and four documents is the MVP.

---

## Flags from this session

- [FLAG: principle — repo as source of truth, databases as rebuildable
  projections] — emerged from the database roadmap cancellation. The
  most generalizable principle from this session.

- [FLAG: principle — don't build retrieval infrastructure before you
  have data worth retrieving] — Grok parallel review confirmed and
  named this explicitly as the trap to avoid.

- [FLAG: principle — don't add review overhead for artifacts that
  summarize conversations Robert was already part of] — the key
  reasoning behind rejecting the Robert-reviews-every-DR model.

- [FLAG: principle — a well-designed process that never gets used is
  worse than a simple habit that actually runs] — the framing that
  drove the MVP scope decision.

---

## Open questions

**When does the Decisions UI ship?** When there are enough DRs that
browsing a list is more useful than opening the folder directly.
Rough threshold: 15-20 committed DRs. Not a hard rule.

**Should Grok produce DRs directly or only in response to explicit
requests?** Currently the Design Session Prompt governs this — Grok
uses it when Robert pastes it. As the local LLM enters the design flow,
the same prompt applies. No special handling needed yet.

**When is the first LoRA training run?** Phase 2, after Phase 1 (RAG)
is stable. Prerequisite: sufficient accumulated DRs with LoRA candidate
tags. Current estimate: ~3 months from now if the habit holds.

---

## Impact / Follow-up

- Implemented: yes — MVP specced and handed to Claude Code
  (`handoff_dr_lora_mvp_2026-06-17.md`)
- Spec 1: commit DR practice documents and directory structure
- Spec 2: Dev Agent filing rules (builds first)
- Superseded by: n/a
- Follow-up needed: periodic LoRA tagging scan when inventory reaches
  ~15 DRs; Decisions UI spec when inventory justifies it

---

*Decision Record · 2026-06-17 · Claude.ai*
*File: `docs/decision-records/dr_decision_record_practice_mvp_2026-06-17.md`*
