# Decision Record Practice — mini-moi
*Created: 2026-06-17 — Claude.ai*
*This document is the reference all agents read. Keep it short.*

---

## What a Decision Record is

A Decision Record (DR) captures the reasoning behind a significant design
decision — what was considered, what was rejected, and why. Specs say what
to build. Decision Records say how we decided to build it.

This reasoning is the primary training signal for the local LLM adaptation
planned in the Learning System Roadmap. A model trained on DRs learns how
to think about decisions in this project. A model trained only on specs
learns what was built.

---

## When to produce one

A DR is worth producing when:
- Multiple alternatives were genuinely considered
- The negative reasoning would be non-obvious from the spec alone
- A constraint shaped the decision that might change later (time, compute,
  operational risk, contract window)
- A generalizable principle emerged from a specific case

A DR is not needed for:
- Implementation decisions with one obvious path
- Pure spec refinements with no alternatives considered
- Decisions already covered by an existing locked DR

When in doubt, produce a short one. **200 words is better than nothing.**

---

## The habit

Design sessions produce DRs. The trigger is saying **"write decision record"**
to Claude.ai or Grok at the end of a session where a significant decision
was made. The LLM produces a structured DR from the conversation and flags
it captured.

DRs go to `docs/decision-records/drafts/` when produced in a session.
Claude reviews drafts against the checklist in `docs/DESIGN_SESSION_PROMPT.md`
before Claude Code commits them to `docs/decision-records/`.

---

## Claude's review role

Before committing a draft DR, Claude checks:
- Decision section is one clear paragraph — no marketing language
- Alternatives include the negative reasoning (why rejected/deferred),
  not just what the alternatives were
- Constraints that shaped the decision are named explicitly
- Flags from the session are included
- Frontmatter is present: `type`, `domain`, `status`, `lora-candidate`
- Impact/Follow-up section is present (can be sparse initially)

Claude does not require Robert's review per DR. Robert is not in the
per-DR loop unless there's a question.

---

## Robert's periodic role

Every few weeks: scan the `docs/decision-records/` index for DRs that
are `lora-candidate: yes` and tag them with any additional context worth
preserving. This is the curation step that makes LoRA training runs
high-signal rather than high-noise.

---

## Naming and location

```
docs/decision-records/dr_[topic]_[YYYY-MM-DD].md
docs/decision-records/drafts/dr_[topic]_[YYYY-MM-DD].md  ← before review
```

Spec reference (optional, one line in the spec):
> *Reasoning: see `dr_[name].md`*

---

## Format

Use the format in `docs/DESIGN_SESSION_PROMPT.md`. Required sections:
- Decision, Context, Alternatives considered, Constraints, Assumptions,
  Known failure modes, Principles, What this is not, Flags, Open questions,
  Impact/Follow-up

Frontmatter block immediately after the title:
```
---
type: decision-record
domain: german / curator / guild / platform
status: active
lora-candidate: yes / no
---
```

---

*Decision Record Practice · 2026-06-17 · Claude.ai*
*See also: `docs/DESIGN_SESSION_PROMPT.md`, `docs/LEARNING_SYSTEM_ROADMAP.md`*
