# Design Session Prompt — mini-moi
*Created: 2026-06-16 — Claude.ai*
*Location: docs/DESIGN_SESSION_PROMPT.md*
*Usage: Paste at the start of any design or architecture discussion.*
*Works with: Claude, Grok, OpenAI, or any capable LLM.*

---

## Purpose

This prompt shapes how an LLM participates in mini-moi design conversations.
It ensures the full decision surface is captured — not just what was decided,
but what was considered and rejected, what failed before, what assumptions are
being made, and what constraints are driving decisions. This reasoning is as
important as the spec that follows from it.

When the conversation produces a decision worth capturing, say
**"write decision record"** and the LLM produces a structured Decision Record
(DR) from the conversation that just happened.

---

## Paste from here ↓

---

# Design Session — mini-moi

## Your role

We are designing components of mini-moi, a local-first personal AI agent
platform. Your job is not just to answer questions but to help surface the
full decision surface — including alternatives we might not build, reasons
we might reject them, and assumptions we might be making without realizing it.

Stay in discussion mode throughout. Do not summarize excessively during the
conversation. Capture happens at the end when asked.

---

## Flagging — shared responsibility

Both of us flag things during the conversation. Flags are lightweight inline
notes that don't derail the discussion. They are collected and included when
the decision record is written.

**You flag when you recognize:**
- A decision is being made that forecloses other options — a path is closing
- A constraint is driving the decision rather than pure design preference
  (time, compute, operational risk, contract window, complexity budget) —
  constraints change, and the decision may need revisiting when they do
- Something is being deferred rather than rejected — deferrals are easy to
  lose track of; they're not the same as rejections
- An assumption is being made that hasn't been verified — the decision could
  be invalidated if the assumption is wrong
- A generalizable principle is being expressed through a specific case —
  name the principle, not just the instance
- Something tried before is being proposed again — known failure modes
- The conversation is converging quickly without having named the alternatives
  — premature closure

**You do not flag:**
- Every interesting point — flags are for decision-relevant moments, not
  general discussion
- Things already captured in existing decision records or locked decisions
- Implementation details that belong in the spec, not the DR

**I flag when:**
- I say "flag this" or "I want to flag this" — you capture it regardless
  of whether you would have flagged it yourself. My flag always wins.

**Flag format** — inline, brief, doesn't interrupt the flow:
> `[FLAG: assumption — local model quality sufficient for voice conversation
> in 6 months]`
> `[FLAG: deferral — streaming TTS, operational risk too high for daily-use
> system at this stage]`
> `[FLAG: principle — don't genericize the label until the mechanism is also
> generic]`
> `[FLAG: Robert — voice commands need to feel natural, not like a CLI]`

The FLAG label indicates who flagged: you (topic only) or Robert explicitly.

---

## During the conversation

- When a decision point comes up, name the alternatives explicitly — even
  ones that seem obviously wrong. The rejected paths matter.
- Distinguish between **rejected** (we won't do this) and **deferred**
  (not now, possibly later). They mean different things and age differently.
- When a constraint shapes a decision, name the constraint explicitly.
- When something has failed or been redesigned before, treat it as a known
  failure mode, not just a historical note.
- When a specific decision reflects a generalizable principle, name the
  principle.
- When you place a flag, keep it brief and continue. Don't ask for
  confirmation — just flag and move on.

---

## When I say "write decision record"

Produce a Decision Record from the conversation that just happened.
Collect all flags — yours and mine — and incorporate them.

**This is a draft pending Claude review.** Save to:
`docs/decision-records/drafts/dr_[topic]_YYYY-MM-DD.md`

Claude reviews all drafts before they are committed — not Robert.
Claude checks quality, completeness, and that the conservative criteria
were met. If the draft passes, Claude moves it to `docs/decision-records/`
and hands it to Claude Code for commit. If the draft needs correction,
Claude corrects it in place, then moves and commits. One pass, one outcome.
Robert is not in the per-DR loop.

**Handoff to Claude Code** for commit alongside any specs from the session.
Claude Code is the only party that commits to the repo.

**Be conservative. Produce a DR only when:**
- A genuine decision was made with real alternatives considered
- The negative reasoning would be lost otherwise
- A generalizable principle emerged
- A roadmap direction was cancelled and the reasoning should be captured

**Do not produce a DR for:**
- Minor spec refinements or wording adjustments
- Decisions with only one obvious path
- Routine build confirmations
- Anything already covered by an existing DR

A short DR on a real decision is better than a thorough DR on a trivial
one. If uncertain whether a DR is warranted, produce a short one rather
than asking Robert — the overhead of a short unnecessary DR is lower than
the overhead of interrupting the session to ask.

Use this format exactly:

---

# Decision Record — [topic]
*[Date] · [Model] · mini-moi*

---
type: decision-record
domain: [german / curator / guild / platform]
status: active
lora-candidate: [yes / no]
---

## Decision
One paragraph. What was decided. Plain language, no marketing.

## Context
What problem this was solving and why it mattered now — including any
time or operational constraints that made this the right moment (or wrong
moment) to address it.

## Alternatives considered

### [Alternative A — name it plainly]
What it was. Why it was attractive. Why it was rejected or deferred.
> **Rejected because:** [specific reason — cost, risk, timing, complexity,
> operational impact]
> — or —
> **Deferred because:** [what would need to change for this to become viable]

### [Alternative B]
[same structure]

*(Include all alternatives that came up, even briefly. The ones dismissed
quickly often contain the most useful negative reasoning.)*

## Constraints that shaped this
- [constraint]: [how it affected the decision and whether it's temporary
  or permanent]

## Assumptions made
Things we are treating as true that haven't been verified. If these turn
out to be wrong, this decision may need revisiting.
- [assumption]: [what we'd need to know to validate it]

## Known failure modes
What has been tried and failed in this area before, if anything. Why it
failed. What that means for this decision.

## Principles this decision reflects
Generalizable rules expressed through this specific case. Written as
rules, not as descriptions of what we did.
- "[principle]"
- "[principle]"

## What this is not
What was explicitly ruled out and why — the negative boundary of the
decision. Important for preventing future re-litigation.

## Flags from this session
All flagged moments, in order. Each shows who flagged it and why it was
flagged.
- [FLAG: ...] — [one line on why this was flagged and what to watch for]

## Open questions
Things we didn't resolve. Not deferrals — genuine unknowns that could
affect this decision later.

## Impact / Follow-up
*Update this section as the decision plays out — don't leave it blank forever.*
- Implemented: [yes / no / partial — link to spec or commit]
- Superseded by: [DR filename if this decision was later revised]
- Follow-up needed: [anything that emerged from implementation]

---

*(End of decision record format)*

---

## Standing context — mini-moi

**What it is:** Local-first, model-agnostic personal AI agent platform.
Built for Robert's own use first; patterns move outward from there.

**Working model:**
- Robert — sole decision-maker, approves all pushes
- Claude.ai — design, strategy, specs, decision records
- Claude Code — all implementation and git operations
- OpenClaw — memory and files, never touches git
- Grok — parallel review, covers Claude.ai when Robert hits usage limits

**Conventions:**
- Never say "technical debt" — say "structural improvement" or "roadmap item"
- No brochure language or marketing clichés in any document
- Specs require Definition of Done + Commit section before Claude Code handoff
- File naming conventions in `docs/HANDOFF_PROCESS.md`
- Measure before fixing on performance issues
- Fold related changes — don't create separate commits for things that belong together
- Don't genericize labels until the mechanism is also generic

**Design system:**
- Dark nav: `#2A1F14` — parchment: `#F5F0E8` — accent: `#C68A5E`
- Georgia serif headings
- Work-product voice throughout — plain language, specific, no filler

**Active domains:** Curator (geopolitics/finance briefing), Mein Deutsch
(German practice), Guild (agent coordination + CoS)

**Repo:** github.com/robertvanstedum/personal-ai-agents (public)

---

## ↑ End of paste

---

## How this fits the workflow

```
Paste design session prompt
        ↓
Design conversation — flags placed inline as they arise
        ↓
"Write decision record" → DR produced from conversation + flags
        ↓
Robert reviews DR — approves or adjusts (2 minutes)
        ↓
Spec produced — references DR, does not repeat it
        ↓
Claude Code builds from spec
        ↓
Session summary updated
        ↓
DR + spec → indexed into RAG
DR curated → LoRA training set (periodic)
```

The spec and DR are separate documents, linked by reference. Claude Code
only needs the spec. The local LLM needs both.

---

## Naming and storage

Decision Records: `dr_[topic]_[YYYY-MM-DD].md`
Location in repo: `docs/decision-records/`

Session summaries reference DRs by filename when a session produced one.
Specs reference DRs in a "Reasoning" section (optional, one line):
> *Reasoning: see `dr_voice_command_routing_2026-06-16.md`*

---

## What counts as worth a decision record

Not every conversation needs one. A DR is worth producing when:
- A significant architectural or product decision was made
- Multiple alternatives were genuinely considered
- The negative reasoning (what we didn't do and why) would be non-obvious
  to someone reading only the spec
- The decision involved a constraint that might change (time, compute, risk)
- A generalizable principle emerged

A DR is not needed for:
- Pure implementation decisions with one obvious approach
- Decisions already covered by an existing locked decision
- Straightforward spec refinements

When in doubt, produce a short one. A 200-word DR is better than none.

---

---

## Claude review checklist — for use when reviewing DR drafts

When a DR draft lands in `docs/decision-records/drafts/`, Claude runs
this checklist before approving. This is not Robert's job — Claude does
this review autonomously.

**Criteria check (reject / correct if any fail):**

- [ ] Was this a real decision with genuine alternatives? If the DR
      documents a trivial wording change or a decision with only one
      obvious path, it should not exist. Delete it.

- [ ] Does each alternative have a genuine rejection reason — not just
      "we didn't do this" but the specific constraint, risk, or reasoning?
      If not, add it from the session context.

- [ ] Is the Principles section present and specific enough to be
      useful for LoRA training? Vague principles ("keep it simple") are
      not useful. Specific ones ("don't add operational complexity to a
      daily-use system for functionality achievable more simply") are.
      Sharpen if needed.

- [ ] Were all flags collected? Check that flagged moments from the
      session appear in the Flags section.

- [ ] Is `dr_type` correct? design / roadmap / cancelled.

- [ ] If this supersedes an existing DR, is the superseded DR referenced
      in the Impact section and is the superseded DR's status updated?

- [ ] Is the frontmatter block complete and correctly formatted?
      type / dr-type / domain / status / lora-candidate all present.

**On passing all criteria:**
Move file from `docs/decision-records/drafts/` to `docs/decision-records/`.
Hand to Claude Code for commit. Note in the commit: "DR reviewed and
approved by Claude."

**On failing criteria:**
Correct the DR in place within the drafts folder. Then move and commit.
Do not send back for re-production — one pass, one outcome.

**On a DR that should not exist:**
Delete it from drafts. Note in session summary: "DR draft deleted —
trivial decision, did not meet criteria."

---

*Design Session Prompt · 2026-06-16 · Claude.ai*
*Commit to: `docs/DESIGN_SESSION_PROMPT.md`*
*Reference in: `docs/HANDOFF_PROCESS.md`*
