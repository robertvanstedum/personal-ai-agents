# Claude.ai Design Session Handoff — 2026-07-04
*Paste this into a new Claude.ai chat to continue*

---

## What this session covered

Full day session — infrastructure fixes, security, Guild redesign spec, domain standardization spec, tips system, narrative sync spec, and significant cleanup. Context strong at end of day — summarized here for clean start.

---

## System state (end of 2026-07-04)

- **Prod (minimoi.ai / EC2):** Stable. Volume mounts fixed — guests.json, build_queue.json, docs/design/, docs/specs/ all host-mounted. sync_docs.sh working.
- **Dev (dev.minimoi.ai / Mac):** Ahead of prod on specs. German and Portuguese domain servers still need launchd plists (Phase 3 of #120).
- **Security:** Postgres credentials rotated, moved to SSM (/minimoi/production/postgres_password) + .env on EC2 host. No plaintext credentials in any git file. GitHub issue created and closed documenting the find and fix.
- **Tips system (#115):** Built and shipped to prod. tips.json at config/curator/ covering 15 slots across Curator, German, Portuguese. tips_slot_registry.md written as living reference doc. Slot keys need server-side verification (Phase 4 of #120).
- **Guest access:** guests.json volume-mounted and persisting. Guild dashboard guest card still shows +7D/Revoke buttons — should be read-only with "Manage guests →" link (small fix, not yet specced as ticket).

---

## Spec naming convention — NEW today

All spec files now follow numbered convention:
`spec_<number>_<name>_<date>.md`
Title inside: `# Spec #<number>: <Title>`

Claude Code task: rename all existing specs in docs/specs/ to numbered convention where build queue number is known.

---

## Specs produced today (5 total)

| # | File | Status | Notes |
|---|---|---|---|
| #116 | spec_116_narrative_sync_2026-07-04.md | Spec Ready | Replaces spec_landing_page_update_2026-07-03.md. Five surfaces: minimoi.ai landing, GitHub README, GitHub About, LinkedIn, resume. Design session required before Claude Code builds — scheduled tomorrow. |
| #117 | spec_117_guild_navigation_redesign_2026-07-04.md | Backlog | Grok reviewed. Full lifecycle, write API, live Roadmap. Build after #120. |
| #118 | spec_118_code_review_security_phase1_2026-07-04.md | Backlog | Code quality + security audit. Pre-external review gate. |
| #120 | spec_120_domain_standardization_2026-07-04.md | Spec Ready | Grok reviewed and approved. Phase 1 active. |
| — | design_github_docs_cleanup_2026-07-04.md | Design | Decisions pending before build-ready. |

---

## Claude Code tasks before closing today

- [ ] Rename spec files to numbered convention (spec_116_, spec_117_, spec_118_, spec_120_)
- [ ] Update H1 titles inside each file to match (#116, #117, etc.)
- [ ] Update build_queue.json spec_file references to new filenames
- [ ] Update #116 title in build_queue.json to "Narrative Synchronization — minimoi.ai / GitHub / LinkedIn / Resume"
- [ ] Commit all today's specs + design doc + tips files to docs/specs/ and docs/design/
- [ ] tips_slot_registry.md → docs/design/
- [ ] tips.json → config/curator/ (replace existing)
- [ ] investigation_german_portuguese_parity_2026-07-04.md → docs/design/ (superseded by #120 but keep for reference)
- [ ] Run sync_docs.sh 100.57.23.192
- [ ] Confirm all files visible in Guild Docs view on prod
- [ ] Mark spec_landing_page_update_2026-07-03.md as cancelled/superseded in build_queue.json

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

## #116 Narrative Sync — design session tomorrow

Five surfaces to synchronize in one coordinated update:
1. minimoi.ai landing page — visitor framing, four domain blurbs (Meu Português currently absent), Meu Português added
2. GitHub README — canonical narrative, multi-agent working model surfaced, tech stack current
3. GitHub About — one sentence, 160 chars (Robert updates manually)
4. LinkedIn — mini-moi section sync (Robert updates manually)
5. Resume — mini-moi bullets sync (Robert updates .docx manually)

Core narrative locked: *"LLMs have the world's knowledge. They don't have your intent, your goals, your history, your risk tolerance."* — do not change this line.

Design session plan: pull all five current texts side by side → agree canonical narrative → write copy in five formats → Grok review → Claude Code builds landing + README only.

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
| #116 | Narrative Sync — minimoi.ai / GitHub / LinkedIn / Resume | Spec Ready — design session tomorrow |
| #117 | Guild Navigation Redesign | Backlog |
| #118 | Code Review & Security Phase 1 | Backlog |
| #119 | Move DATABASE_URL to .env | Backlog |
| #120 | Domain Standardization | Spec Ready — Phase 1 active |
| #109 | Prometheus + Grafana | Backlog |
| #113 | Portuguese Backend Completion | In Build |

---

## Tomorrow's priority order

1. **#116 Narrative sync design session** — pull all five surfaces, write canonical copy, Grok review
2. **Memory/intent layer v3 + OpenClaw as CoS** — strategic design session
3. **Code review (#118) + domain standardization (#120) jointly** — Claude Code runs investigation in parallel with design

---

## Small fixes not yet specced (add to queue)

- Guild dashboard guest card: remove +7D/Revoke buttons, make read-only, add "Manage guests →" link
- `/admin/guests`: remove redundant expires stepper, keep free-form field, default 7 days, max 90
- `pipeline.items unavailable` error on Guild dashboard — needs investigation
- +7d extend confirm dialog (prevents accidental repeat clicks)

---

## Design threads open (schedule separately)

- **Memory/intent layer v3 revision** — four reshapes: exploratory archetype, archetypes-as-product, portable instances, seed personas
- **OpenClaw as CoS on AWS** — clone to AWS, scope to mini-moi only, Telegram integration. Phase 1 of memory/intent layer.
- **Multi-agent curation case study** — idea stage, 1-2 more review rounds needed

---

## Ways of Working — critical rules

- Dev first. Always. No ECR push without Robert's explicit approval.
- Hotfix process: Robert says "hotfix" explicitly → Claude Code opens GitHub issue labeled `hotfix` → deploys. No exceptions.
- Specs need: Intent + Definition of Done + Commit sections. Numbered filename convention.
- New domain template: `docs/architecture/DOMAIN_TEMPLATE.md` — read before starting any new domain.
- No credentials in git. Ever.

---

## Working model reminder

Robert decides/approves all pushes. Claude.ai designs/specs (no git). Claude Code builds/git/deploy. OpenClaw memory/files (no git). Grok reviews. Specs need Intent + Definition of Done + Commit sections. Filename conventions in docs/HANDOFF_PROCESS.md.

---

*Handoff · 2026-07-04 · Claude.ai → new Claude.ai session*
