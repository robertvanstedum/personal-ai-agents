# Spec — Commit Learning System Foundation Documents
*Created: 2026-06-16 — Claude.ai*
*Status: Ready for `_working/`*

---

## Context

mini-moi is beginning a structured approach to institutional knowledge —
capturing design reasoning, not just build decisions, and establishing the
foundation for a local LLM that learns the project over time. This spec
commits the foundational documents and makes the minimal workflow changes
needed to start Phase 0.

Full vision: `docs/LEARNING_SYSTEM_ROADMAP.md` (committed in this spec).

---

## Changes

### 1. New files to commit

**`docs/LEARNING_SYSTEM_ROADMAP.md`**
Content: as produced in this session (attached).
The GitHub-visible roadmap for the learning system. Explains the
institutional memory model, the local LLM role, and the phased plan.

**`docs/DESIGN_SESSION_PROMPT.md`**
Content: as produced in this session (attached).
The prompt pasted at the start of any design conversation — Claude,
Grok, OpenAI, or local LLM. Produces Decision Records on command.

**`docs/decision-records/`**
Create this directory with a `.gitkeep` so it exists in the repo.
First real DRs will be added as they're produced going forward.
Add a one-line `README.md` inside:
> Decision Records capture the reasoning behind significant design
> decisions — alternatives considered, constraints, assumptions, and
> principles. See `docs/DESIGN_SESSION_PROMPT.md` for format and usage.

### 2. Amend `docs/HANDOFF_PROCESS.md`

Add a section after the existing spec conventions:

```markdown
## Decision Records

For any significant architectural or product decision, produce a Decision
Record alongside the spec. Use the design session prompt in
`docs/DESIGN_SESSION_PROMPT.md` to structure the conversation and trigger
the DR output.

A DR is worth producing when:
- Multiple alternatives were genuinely considered
- The negative reasoning (what we didn't do and why) would be non-obvious
  from the spec alone
- A constraint shaped the decision that might change later
- A generalizable principle emerged

DR naming: `dr_[topic]_[YYYY-MM-DD].md`
DR location: `docs/decision-records/`
Spec reference: add one line to spec — *Reasoning: see `dr_[name].md`*

When in doubt, produce a short one. 200 words is better than nothing.
```

### 3. Amend `README.md`

Add `docs/LEARNING_SYSTEM_ROADMAP.md` to the docs section of the README
so it's visible as a top-level project document on GitHub.
One line: `- [Learning System Roadmap](docs/LEARNING_SYSTEM_ROADMAP.md)
— how mini-moi builds institutional knowledge over time`

---

## Definition of Done

- `docs/LEARNING_SYSTEM_ROADMAP.md` exists in repo and renders correctly
  on GitHub
- `docs/DESIGN_SESSION_PROMPT.md` exists in repo
- `docs/decision-records/` directory exists with README.md inside
- `docs/HANDOFF_PROCESS.md` includes the Decision Records section
- `README.md` links to the learning system roadmap
- No other files changed in this commit

## Commit

`Add learning system foundation: roadmap, design session prompt,
decision-records directory, HANDOFF_PROCESS amendment.`

---

*Spec · 2026-06-16 · Claude.ai*
