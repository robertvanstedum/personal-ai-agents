# Decision Record Practice — mini-moi
*Created: 2026-06-17 — Claude.ai*
*Location: docs/DECISION_RECORD_PRACTICE.md*
*Keep this short. The point is the habit, not the process.*

---

## Why we do this

Design sessions produce reasoning that specs don't capture. Specs say
what to build. Decision records say what was considered, what was
rejected, why, and what the failure modes are.

This reasoning is the primary training signal for the local LLM we're
building toward. A model trained on specs learns what was built. A model
trained on decision records learns how to think about what to build.
That's the difference between a model that can describe the codebase
and one that can reason about it.

**Decision Records are the highest-signal artifacts for future LoRA
adaptation of the local LLM.** They are produced now so the signal
exists when Phase 2 arrives — not reconstructed from memory.

We are in early stage. The infrastructure (UI, index, LoRA export) comes
later when there's an inventory worth using. What matters now is the
habit of capturing. A folder of well-structured markdown files in a
private repo is sufficient to start.

**Don't let this get in the way of other builds.** A session that produces
no DR because there was nothing worth capturing is correct behavior.
Quality over volume.

---

## The habit — one line

Session agent produces draft → Claude reviews →
Claude Code commits → Robert does periodic LoRA scan.

---

## When to produce a DR

**Produce one when:**
- A real decision was made with genuine alternatives considered
- The negative reasoning would be lost otherwise — what wasn't built and why
- A direction was cancelled (roadmap item removed, approach replaced)
- A generalizable principle emerged from a specific case

**Do not produce one for:**
- Minor wording or spec refinements
- Decisions with only one obvious path
- Routine build confirmations
- Anything already covered by an existing DR

If uncertain: produce a short one. A 200-word DR on a real decision is
better than nothing. A thorough DR on a trivial decision is noise.

---

## Where they live

```
docs/decision-records/drafts/    ← agent produces here
docs/decision-records/           ← Claude approves, Claude Code commits
```

Naming: `dr_[topic]_YYYY-MM-DD.md`

---

## Claude's review — what to check

Claude reviews drafts autonomously. Robert is not in the loop per DR.

Check:
1. Real decision with genuine alternatives? If trivial — delete.
2. Each rejection reason specific and honest? If vague — sharpen.
3. Principles section present and LoRA-useful? If missing — add.
4. Flags from session collected?
5. `dr_type` correct? (design / roadmap / cancelled)
6. Frontmatter complete?

One pass. Correct in place if needed, then move to `docs/decision-records/`
and hand to Claude Code. If the DR should not exist, delete it and note
it in the session summary.

---

## Required frontmatter

Every DR starts with this block:

```
---
type: decision-record
dr-type: design | roadmap | cancelled
domain: german | curator | guild | platform
status: active | superseded | cancelled
lora-candidate: yes | no
---
```

`lora-candidate` is set by the producing agent based on whether the
reasoning patterns are worth the local LLM internalizing. Robert may
adjust during periodic scans — that is his only required touch.

---

## Correction model

Committed DRs are not edited retroactively. If a DR is wrong or
misleading, mark it `status: superseded`, produce a short correcting DR
that references it, and commit both. The original stays — the correction
is additive. The record is permanent; corrections are new entries.

---

## Robert's role — periodic only

Robert does not review DRs before commit. His role:
- Periodic scan of `docs/decision-records/` to review LoRA candidates
- Adjust `lora-candidate` tags if needed
- Note any DRs that should be marked superseded

Frequency: whenever it feels useful, not on a schedule.
This is the one human judgment gate before training data is used.

---

## Full DR format

See `docs/DESIGN_SESSION_PROMPT.md` for the complete template with all
sections. The required sections are:

Decision / Context / Alternatives considered / Constraints /
Assumptions / Known failure modes / Principles /
What this is not / Flags / Open questions / Impact & follow-up

Short DRs that cover only the relevant sections are fine.
Not every section needs content for every DR.

---

*Decision Record Practice · 2026-06-17 · Claude.ai*
*This document is the reference. Keep it short. Update if the habit changes.*
