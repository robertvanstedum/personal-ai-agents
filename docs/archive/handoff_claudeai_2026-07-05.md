# Claude.ai Design Session Handoff — 2026-07-05
*Paste this into a new Claude.ai chat to continue*

---

## What this session covered

Morning session — tips system updates, Portuguese backend confirmation, spec naming convention, narrative sync spec reframe. Picking up from yesterday's full day session (2026-07-04).

---

## System state (2026-07-05 morning)

- **Prod (minimoi.ai / EC2):** Stable. Volume mounts fixed — guests.json, build_queue.json, docs/design/, docs/specs/ all host-mounted. sync_docs.sh working.
- **Dev (dev.minimoi.ai / Mac):** Ahead of prod on specs. German and Portuguese domain servers still need launchd plists (Phase 3 of #120).
- **Security:** Postgres credentials rotated, in SSM (/minimoi/production/postgres_password) + .env on EC2 host. No plaintext credentials in any git file.
- **Tips system (#115):** Built and shipped to prod for Curator briefing. German and Portuguese tips written and in tips.json but NOT yet wired server-side — _load_tip() calls missing from domain servers and templates. This is Phase 4 of #120 — active task for Claude Code.
- **Guest access:** guests.json volume-mounted and persisting. Guild dashboard guest card still shows +7D/Revoke buttons — should be read-only with "Manage guests →" link (small fix, not yet built).

---

## Spec naming convention — established 2026-07-04

All spec files follow numbered convention:
`spec_<number>_<name>_<date>.md`
Title inside: `# Spec #<number>: <Title>`

Claude Code task: rename all existing specs in docs/specs/ to numbered convention where build queue number is known.

---

## Active build queue (current sprint)

| # | Title | Status | Notes |
|---|---|---|---|
| #113 | Portuguese Domain: Backend Completion | In Build | DoD not fully confirmed — Arquivo showing empty sessions. Claude Code must run DoD checklist on prod before marking done. Migrations 003-006 need EC2 confirmation. |
| #115 | Contextual Tips System | Done ✅ | Curator briefing tip live. German/Portuguese wiring pending (Phase 4 of #120). |
| #116 | Narrative Sync — minimoi.ai / GitHub / LinkedIn / Resume | Spec Ready | Design session required before Claude Code builds. Five surfaces: landing, GitHub README, GitHub About, LinkedIn, resume. Schedule today. |
| #120 | Domain Standardization | Spec Ready — Phase 1 active | Grok reviewed and approved. Phase sequence: bot refactor → per-user isolation → launchd plists → tips verification. Dev first, no ECR push without Robert's approval. |

---

## Backlog (not in current sprint)

| # | Title | Status |
|---|---|---|
| #117 | Guild Navigation Redesign | Backlog |
| #118 | Code Review & Security Phase 1 | Backlog |
| #119 | Move DATABASE_URL to .env | Backlog |
| #109 | Prometheus + Grafana | Backlog |
| — | GitHub & Docs Cleanup | Design |

---

## Claude Code tasks — immediate

### Tips system (Phase 4 of #120)
1. Replace `config/curator/tips.json` with updated version (produced 2026-07-05)
2. Replace `docs/design/tips_slot_registry_2026-07-04.md` with updated version
3. Wire `_load_tip()` calls into German domain server and templates for all 7 slots:
   - `german.landing`, `german.lesen`, `german.lesen.article`, `german.gesprache`, `german.schreiben`, `german.woerter`, `german.archiv`
4. Wire `_load_tip()` calls into Portuguese domain server and templates for all 7 slots:
   - `portuguese.landing`, `portuguese.leitura`, `portuguese.leitura.article`, `portuguese.conversas`, `portuguese.escrita`, `portuguese.palavras`, `portuguese.arquivo`
5. Test all tips rendering on dev before sync to EC2
6. Run sync_docs.sh 100.57.23.192 after confirming dev

### Portuguese backend (#113)
Run DoD checklist against prod:
- Confirm migrations 003-006 ran on EC2
- Confirm sessions saving to `portuguese.sessions` after a real Conversas session
- Confirm Arquivo Conversas shows saved sessions
- Confirm Escrita sessions saving and visible in Arquivo
- Confirm Palavras status promotion works end-to-end
- Report back before marking #113 done

### Spec file renaming
- Rename today's four specs to numbered convention:
  - `spec_116_narrative_sync_2026-07-04.md`
  - `spec_117_guild_navigation_redesign_2026-07-04.md`
  - `spec_118_code_review_security_phase1_2026-07-04.md`
  - `spec_120_domain_standardization_2026-07-04.md`
- Update H1 titles inside each file
- Update build_queue.json spec_file references
- Run sync_docs.sh after

### Small UX fixes (bundle into one commit)
- Guild dashboard guest card: remove +7D/Revoke buttons, read-only, add "Manage guests →" link
- `/admin/guests`: remove redundant expires stepper, default 7 days, max 90
- `pipeline.items unavailable` error on Guild dashboard — investigate and fix

---

## Updated tips.json — key changes from original

**german.gesprache and portuguese.conversas (revised — honest about voice latency):**
> For the best voice experience, copy the prompt and use it in Grok, Claude, Gemini, or ChatGPT on your phone — native apps have faster, more natural voice. In-app voice works but has more latency due to API calls.

**german.lesen and portuguese.leitura (revised):**
> Best on desktop — read, hover to translate, and save words in one flow. On mobile, tap and hold to translate.

**german.schreiben and portuguese.escrita (revised):**
> Best on desktop for the full correction experience. On mobile, dictate into the text field and submit — Corrigir works the same way.

Note: Grok listed first in voice tip intentionally. No explanation needed.

---

## #120 Domain Standardization — active

**Architecture principles locked:**
1. JSON-first — JSON is source of truth, Postgres is rebuildable projection
2. Per-user data isolation — never hardcoded user ID
3. Domain server owns its data — no external process reads domain files directly
4. Model names never hardcoded in domain functions
5. No credentials in git — ever, public or private
6. Dev-first always — no ECR push without Robert's explicit approval

**Phase sequence (do not reorder):**
- Phase 1: Bot refactor — decouple Telegram bot from German domain, expose HTTP endpoints. Gates everything. Closes GitHub #54.
- Phase 2: Per-user isolation — fix German persona DEFAULT_USER bug, fix Portuguese writing session "anonymous" fallback.
- Phase 3: launchd plists for German and Portuguese on Mac dev.
- Phase 4: Tips slot verification — active now.
- Deliverable: `docs/architecture/DOMAIN_TEMPLATE.md` — canonical reference for all future domains.

---

## #116 Narrative Sync — design session today

Five surfaces to synchronize:
1. minimoi.ai landing page — visitor framing, four domain blurbs, Meu Português currently absent
2. GitHub README — canonical narrative, multi-agent working model surfaced
3. GitHub About — one sentence, 160 chars (Robert updates manually)
4. LinkedIn — mini-moi section (Robert updates manually)
5. Resume — mini-moi bullets (Robert updates .docx manually)

Core narrative: *"LLMs have the world's knowledge. They don't have your intent, your goals, your history, your risk tolerance."* — do not change this line.

Design session plan: pull all five current texts → agree canonical narrative → write copy in five formats → Grok review → Claude Code builds landing + README only. LinkedIn, GitHub About, resume are Robert's manual updates.

---

## Today's priority order

1. **Tips wiring** — Claude Code wires _load_tip() across German and Portuguese (Phase 4 of #120)
2. **#113 DoD confirmation** — Claude Code runs checklist, marks done or flags gaps
3. **#116 Narrative sync design session** — Claude.ai session, pull all five surfaces
4. **Memory/intent layer v3 + OpenClaw as CoS** — strategic design session
5. **Code review (#118) + domain standardization (#120) jointly** — Claude Code investigation in parallel

---

## Design threads open (schedule separately)

- **Memory/intent layer v3 revision** — four reshapes: exploratory archetype, archetypes-as-product, portable instances, seed personas
- **OpenClaw as CoS on AWS** — clone to AWS, scope to mini-moi only, Telegram integration. Phase 1 of memory/intent layer.
- **Multi-agent curation case study** — idea stage, 1-2 more review rounds needed
- **German/Portuguese prompt-to-phone gap** — copy prompt button surfaces the workaround for now. Longer term: shared session link or email prompt. Not yet specced.

---

## Ways of Working — critical rules

- Dev first. Always. No ECR push without Robert's explicit approval.
- Hotfix: Robert says "hotfix" explicitly → Claude Code opens GitHub issue labeled `hotfix` → deploys. No exceptions.
- Specs need: Intent + Definition of Done + Commit sections. Numbered filename convention.
- New domain: read `docs/architecture/DOMAIN_TEMPLATE.md` first.
- No credentials in git. Ever.
- State the rule at every new Claude Code session open — it is not enough that it's in memory.

---

## Working model

Robert decides/approves all pushes. Claude.ai designs/specs (no git). Claude Code builds/git/deploy. OpenClaw memory/files (no git). Grok reviews specs before build. Filename conventions in docs/HANDOFF_PROCESS.md.

---

*Handoff · 2026-07-05 · Claude.ai → new Claude.ai session*
