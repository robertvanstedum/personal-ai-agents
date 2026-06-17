# Handoff Process — mini-moi
*Living document — add conventions here as they are established.*
*Created: 2026-06-16*

---

## Decision Records

For any significant architectural or product decision, produce a Decision
Record alongside the spec. Use `docs/DESIGN_SESSION_PROMPT.md` to
structure the conversation and trigger the DR output.

A DR is worth producing when:
- Multiple alternatives were genuinely considered
- The negative reasoning would be non-obvious from the spec alone
- A constraint shaped the decision that might change later
- A generalizable principle emerged

DR naming: `dr_[topic]_[YYYY-MM-DD].md`
DR location: `docs/decision-records/`
Spec reference: add one line — *Reasoning: see `dr_[name].md`*

When in doubt, produce a short one. 200 words is better than nothing.
