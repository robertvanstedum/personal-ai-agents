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
