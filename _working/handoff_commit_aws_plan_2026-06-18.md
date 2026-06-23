# Handoff — Commit AWS Migration Plan
*Created: 2026-06-18 — Claude.ai*
*Audience: Claude Code*
*Scope: One new doc, one roadmap update, one commit*

---

## Action 1 — Commit AWS migration plan

**Source:** `docs_AWS_MIGRATION_PLAN_2026-06-18.md`
**Destination:** `docs/AWS_MIGRATION_PLAN.md`

---

## Action 2 — Update _working/ROADMAP.md

In the Platform domain, Agreed targets table, add two rows:

```markdown
| **AWS Migration** | | | AWS_MIGRATION_PLAN.md |
| · Phase 0 · Containerization | Phase 0 | in_progress | |
| · Phase 1 · AWS Foundation | Phase 1 | target | |
| · Phase 2 · Cloud deployment | Phase 2 | target | |
| · Phase 3 · CI/CD pipeline | Phase 3 | target | |
| · Phase 4 · Data layer (RDS + S3) | Phase 4 | target | |
| · Phase 5 · GPU instance (local LLM) | Phase 5 | target | |
| · Phase 6 · Hardening | Phase 6 | target | |
```

Also update Mac Mini migration row to:
```markdown
| Mac Mini migration | — | deferred | Replaced by AWS Migration |
```

---

## Action 3 — Update README.md

Add under the Platform / Infrastructure section:
```markdown
- [AWS Migration Plan](docs/AWS_MIGRATION_PLAN.md) —
  containerization, cloud deployment, CI/CD, GPU instance for
  local LLM. Replaces Mac Mini purchase.
```

---

## Definition of Done

- `docs/AWS_MIGRATION_PLAN.md` exists and renders on GitHub
- ROADMAP.md shows AWS Migration with all 6 phases
- Mac Mini row marked deferred
- README links to the plan
- No other files changed

## Commit

`Add AWS migration plan — containerization through GPU instance.
Replaces Mac Mini. Phase 0 starts 2026-06-19.`

---

*Handoff · 2026-06-18 · Claude.ai*
