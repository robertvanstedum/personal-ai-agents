# Handoff — Register Three Items
*Created: 2026-06-17 — Claude.ai*
*Audience: Claude Code + OpenClaw*
*Scope: Commit two docs, update roadmap, hand one task to OpenClaw*

---

## Item 1 — Commit Gespräche Forward Spec

**Source:** `docs_GESPRACHE_FORWARD_SPEC_2026-06-17.md`
**Destination:** `docs/GESPRACHE_FORWARD_SPEC.md`

Replaces `docs/GESPRACHE_ROADMAP.md` as the living forward spec for
Gespräche. Contains current build state, next tier items, open questions
from Grok review, known failure modes, learning system connection, and
a testing checklist for today's session.

Update `_working/ROADMAP.md` — German domain, Agreed targets table:
Change the Gespräche mobile row to reference this doc:

```markdown
| **Gespräche mobile** | | | GESPRACHE_FORWARD_SPEC.md |
```

---

## Item 2 — Commit CoS Page Roadmap Entry

**Source:** `docs_COS_PAGE_ROADMAP_2026-06-17.md`
**Destination:** `docs/COS_PAGE_ROADMAP.md`

Registers the CoS interaction page as an agreed roadmap target.
Voice-first on mobile, top of Guild nav, design session pending.

Update `_working/ROADMAP.md` — Guild domain, Agreed targets table.
Add as the first row (highest priority in Guild):

```markdown
| CoS interaction page | v1 | target | COS_PAGE_ROADMAP.md |
```

Update README.md — add under Learning System or Guild section:
```markdown
- [CoS Page Roadmap](docs/COS_PAGE_ROADMAP.md) — voice-first CoS
  interaction page, design session pending
```

---

## Item 3 — OpenClaw task: Local LLM interaction guide

**Source:** `task_openclaw_local_llm_guide_2026-06-17.md`
**Action:** Hand to OpenClaw, not a git commit

OpenClaw checks what's installed (Ollama, model list, any web UI),
produces a short "how to chat with your local LLM" note for Robert,
and surfaces it via CoS briefing or Telegram today.

If nothing is installed, provide the two-command setup.
Robert wants to use the local LLM for the first time today.

---

## Definition of Done

- `docs/GESPRACHE_FORWARD_SPEC.md` exists in repo
- `docs/COS_PAGE_ROADMAP.md` exists in repo
- `_working/ROADMAP.md` updated: Gespräche row references new doc,
  CoS page added as first Guild agreed target
- README.md updated with CoS page link
- OpenClaw task file handed off — OpenClaw produces guide today
- No other files changed

## Commit

`Register Gespräche forward spec and CoS page on roadmap.
Add docs/GESPRACHE_FORWARD_SPEC.md, docs/COS_PAGE_ROADMAP.md,
update ROADMAP.md and README.md.`

---

*Handoff · 2026-06-17 · Claude.ai*
