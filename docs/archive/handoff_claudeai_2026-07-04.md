# Claude.ai Design Session Handoff — 2026-07-04
*Paste this into a new Claude.ai chat to continue*

---

## What this session covered

Full day session — infrastructure fixes, security, Guild redesign spec, domain standardization spec, tips system, and significant cleanup. Context strong at end of day — summarized here for clean start.

---

## System state (end of 2026-07-04)

- **Prod (minimoi.ai / EC2):** Stable. Volume mounts fixed — guests.json, build_queue.json, docs/design/, docs/specs/ all host-mounted. sync_docs.sh working.
- **Dev (dev.minimoi.ai / Mac):** Ahead of prod on specs. German and Portuguese domain servers still need launchd plists (Phase 3 of #120).
- **Security:** Postgres credentials rotated, moved to SSM (/minimoi/production/postgres_password) + .env on EC2 host. No plaintext credentials in any git file. GitHub issue created and closed documenting the find and fix.
- **Tips system (#115):** Built and shipped to prod. tips.json at config/curator/ covering 15 slots across Curator, German, Portuguese. tips_slot_registry.md written as living reference doc. Slot keys need server-side verification (Phase 4 of #120).
- **Guest access:** guests.json volume-mounted and persisting. Guild dashboard guest card still shows +7D/Revoke buttons — should be read-only with "Manage guests →" link (small fix, not yet specced as ticket).

---

## Specs produced today (4 total)

| # | File | Status | Notes |
|---|---|---|---|
| #117 | spec_guild_navigation_redesign_2026-07-04.md | Backlog | Grok reviewed. Full lifecycle, write API, live Roadmap. Build after #120. |
| #118 | spec_code_review_security_phase1_2026-07-04.md | Backlog | Code quality + security audit. Pre-external review gate. |
| #120 | spec_domain_standardization_2026-07-04.md | Spec Ready | Grok reviewed and approved. Start Phase 1 now. |
| — | design_github_docs_cleanup_2026-07-04.md | Design | Decisions pending before build-ready. |

All four need to be committed to docs/ and synced to EC2 if not already done.

---

## #120 Domain Standardization — active build

**Grok approved. Claude Code has been briefed. Phase 1 is active.**

Four clarifications folded in from Grok review:
1. Phase 1 endpoints must use `_request_user_id()` — in Phase 1 DoD
2. `DOMAIN_TEMPLATE.md` goes in `docs/architecture/` — create that directory
3. No automated template checks yet — backlog after first new domain built
4. Bot as own service is future scope — not this spec

**Phase sequence (do not reorder):**
- Phase 1: Bot refactor — decouple Telegram bot from German domain, expose HTTP endpoints. Gates everything. Closes GitHub #54.
- Phase 2: Per-user isolation — fix German persona DEFAULT_USER bug, fix Portuguese writing session "anonymous" fallback.
- Phase 3: launchd plists for German and Portuguese on Mac dev.
- Phase 4: Tips slot verification across both domains.
- Deliverable throughout: `docs/architecture/DOMAIN_TEMPLATE.md` — canonical reference for all future domains.

**Dev first. No ECR push without Robert's explicit approval.**

---

## Architecture principles — locked

1. JSON-first — JSON is source of truth, Postgres is rebuildable projection
2. Per-user data isolation — never hardcoded user ID
3. Domain server owns its data — no external process reads domain files directly
4. Model names never hardcoded in domain functions
5. No credentials in git — ever, public or private
6. Dev-first always — no ECR push without Robert's explicit approval

---

## Build queue state (end of day)

| # | Title | Status |
|---|---|---|
| #115 | Contextual Tips System | Done ✅ |
| #116 | Landing Page Update | Spec Ready — not started today |
| #117 | Guild Navigation Redesign | Backlog |
| #118 | Code Review & Security Phase 1 | Backlog |
| #119 | Move DATABASE_URL to .env | Backlog |
| #120 | Domain Standardization | Spec Ready — Phase 1 active |
| #109 | Prometheus + Grafana | Backlog |
| #113 | Portuguese Backend Completion | In Build |

---

## Small fixes not yet specced (add to queue)

- Guild dashboard guest card: remove +7D/Revoke buttons, make read-only, add "Manage guests →" link
- `/admin/guests`: remove redundant expires stepper, keep free-form field, default 7 days, max 90
- `pipeline.items unavailable` error on Guild dashboard — needs investigation
- +7d extend confirm dialog (prevents accidental repeat clicks)

---

## Design threads open (schedule separately)

- **Landing page copy session** — final blurbs for Curator, Mein Deutsch, Meu Português, Guild (#116 source material already in spec)
- **Memory/intent layer v3 revision** — four reshapes to fold in: exploratory archetype, archetypes-as-product, portable instances, seed personas
- **OpenClaw as CoS on AWS** — clone to AWS, scope to mini-moi only, Telegram integration. Phase 1 of memory/intent layer.
- **Multi-agent curation case study** — idea stage, needs 1-2 more review rounds

---

## Ways of Working — critical rules

- Dev first. Always. No ECR push without Robert's explicit approval.
- Hotfix process: Robert says "hotfix" explicitly → Claude Code opens GitHub issue labeled `hotfix` → deploys. No exceptions.
- Specs need: Intent + Definition of Done + Commit sections.
- New domain template: `docs/architecture/DOMAIN_TEMPLATE.md` — read before starting any new domain.
- No credentials in git. Ever.

---

## Working model reminder

Robert decides/approves all pushes. Claude.ai designs/specs (no git). Claude Code builds/git/deploy. OpenClaw memory/files (no git). Grok reviews. Specs need Intent + Definition of Done + Commit sections. Filename conventions in docs/HANDOFF_PROCESS.md.

---

*Handoff · 2026-07-04 · Claude.ai → new Claude.ai session*
