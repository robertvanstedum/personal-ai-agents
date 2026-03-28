# PROJECT_STATE.md
**Last updated:** 2026-03-07  
**Location:** `_NewDomains/PROJECT_STATE.md`  
**All parties read this first before starting any work**  
**Update this file at the end of every working session**

---

## ⚠️ PROTECTED FILES — DO NOT MODIFY WITHOUT EXPLICIT INSTRUCTION

These existing files are polished, public-facing, or critical.  
No agent touches them without Robert explicitly saying so.

```
README.md         ← public portfolio piece, carefully written
CHANGELOG.md      ← append only, never rewrite or restructure
WHITEBOARD.md     ← ideas only, see whiteboard rule below
OPERATIONS.md     ← operational runbook, edit only when instructed
CLAUDE.md         ← agent orientation, edit only when instructed
docs/*            ← all existing docs files, do not overwrite
```

**If you are unsure whether a file is protected — stop and ask Robert.**

---

## ⚠️ WHITEBOARD RULE

Nothing in `WHITEBOARD.md` gets built without an explicit entry  
in the "Approved to Build" section of this file.  
Claude Code and OpenClaw: if you see an idea in WHITEBOARD.md  
that is not listed below, do not build it. Ask Robert first.

---

## Public GitHub Policy

Public repo shows:
- Geopolitics domain (active, polished)
- "Additional domains in development" — one line, no detail

Nothing goes public until:
- Working prototype exists
- Code is clean and documented
- Robert explicitly approves

Never publish:
- `_NewDomains/` design docs
- Half-built domain code
- Personal data of any kind
- Business plans or financial structure

---

## Current Program Phase
**Phase:** Multi-domain platform foundation  
**Active domain:** Geopolitics (v0.9-beta)  
**In design:** Language Learning, Finance

---

## Approved to Build Right Now

### Geopolitics Domain
- [ ] LLM folder classification — tag 335 null X signals via Haiku (~$0.02)
- [ ] Fix launchd scheduling — cron missed March 4, needs diagnosis
- [ ] Fix `show_profile.py` — doesn't display Phase 3C fields
- [ ] Second A/B test — 20-tweet bootstrap vs 398-tweet full profile
- [ ] Commit README v2 (already drafted)
- [ ] Expand RSS sources — Foreign Affairs, War on the Rocks, VoxEU/CEPR, Chatham House, Adam Tooze Chartbook, Naked Capitalism, NY Fed Liberty Street

### Telegram
- [ ] TELEGRAM_CONTEXT.md — implement Option B minimal context for OpenClaw commands
- [ ] Session auto-reset after each Telegram command

### Project Structure (new files only)
- [x] Create `_NewDomains/` folder with all files in this package
- [ ] Update `.gitignore` with finance and language learning private data paths
- [ ] Add pointer to `_NewDomains/PROJECT_STATE.md` in CLAUDE.md

---

## NOT Approved to Build

- Finance domain tools (design only, no code)
- Language learning tools (design only, no code)
- PostgreSQL migration (deferred)
- Rails migration (deferred until RVSAssociates)
- Core/ shared library (deferred until 2 domains active)
- Mac Mini migration (deferred)
- Any reorganization of existing docs or folders

---

## Active Blockers

| Blocker | Impact | Owner |
|---------|--------|-------|
| Grok transcript export unknown | Language learning build blocked | Robert to check |
| Itaú OFX/CSV export format | Finance parser blocked | Robert to export sample |
| launchd cron not firing | Missing daily briefings | Claude Code |
| grok-4.1 model name unverified | Model upgrade blocked | Verify vs xAI API |

---

## Domain Status

### Geopolitics (public)
- **Version:** v0.9-beta (tagged)
- **Last run:** March 5, 2026
- **Signal base:** 398 X bookmarks + RSS feeds
- **Phase 3C complete:** t.co enrichment, 1,190 topics, 23 domains
- **Next phase:** 3D image analysis (gated)
- **Scoring model:** grok-3-mini (upgrade planned, verify model name first)

### Language Learning (private)
- **Version:** Not started — design phase only
- **Blocker:** Grok transcript export method unknown
- **Target:** German B1 in ~12 months
- **Go public:** When conversation capture + feedback loop working

### Finance (private)
- **Version:** Not started — design phase only
- **Scope:** Itaú + Airbnb → monthly BRL income statement + USD
- **Tax scope:** US 1040 (self-file) + Brazilian CPF
- **Go public:** Never

### Commercial — RVSAssociates Platform (separate repo)
- **Version:** Not started — design phase only
- **Stack:** Rails 8 + Hotwire + Stripe Connect + Kamal + PostgreSQL
- **Tenants:** Vera's jewelry (Phase 1), Cleaning by Vera (Phase 2), RVS Associates, future clients
- **Key decision pending:** Vera's jewelry site name and domain
- **Key decision pending:** VPS provisioned (Hetzner ~$6/month)
- **Go public:** Yes — separate repo, commercial portfolio piece

---

## Cost Monitor

| Service | Cost | Target |
|---------|------|--------|
| OpenClaw fresh session | ~$0.003-0.01/msg | Keep under 50k tokens |
| OpenClaw bloated session | ~$0.52/msg | Reset after each major task |
| grok-3-mini scoring | ~$0.30/day | Updated 2026-03-12: X pool merged (722 articles vs 390). Evaluate Haiku pre-filter after 1 week. |
| Haiku pre-filter | ~$0.02/run | Stable |

**Rule:** Reset OpenClaw session after each major task.

---

## Resume / Portfolio
- README v2 drafted, ready to commit
- Project to be added to resume this week
- Public narrative: geopolitics AI curator, multi-domain platform in progress
- New domains noted as "in development" — not merged into existing docs

---

## Key File Locations

| File | Purpose |
|------|---------|
| `_NewDomains/PROJECT_STATE.md` | This file — read first |
| `_NewDomains/ARCHITECTURE.md` | Platform vision and principles |
| `_NewDomains/DOMAIN_SPEC_language_learning.md` | Language learning design |
| `_NewDomains/DOMAIN_SPEC_commercial.md` | Commercial platform design |
| `_NewDomains/DOMAIN_SPEC_finance.md` | Finance domain design |
| `OPERATIONS.md` | Daily ops and cost management |
| `docs/FEATURE_TELEGRAM_ARCHITECTURE.md` | Two-bot Telegram design |
| `docs/NEXT_PHASE_PLAN_grok41.md` | Model upgrade plan |

---

## Tonight's Build Queue — 2026-03-24

### AI Observations page — navigation + ordering fix

**Problem:**
- Weekly Connections (last run 03-15, 9 days stale) renders above Today's Observations
- Date nav `← prev / today →` is ambiguous — navigates daily only but Weekly doesn't move with it
- Weekly staleness not communicated clearly

**Approved fix:**

1. Reorder sections — Today's Observations first, Weekly Connections below
2. Weekly section: add "Last updated: Mar 15" label, collapse by default if > 7 days old
3. Date nav: move inline with "Daily Observations" section header, remove standalone buttons
   - Pattern: `Daily Observations — Mar 24  ←  →`

**Files to touch:**
- `curator_intelligence.html` — reorder HTML sections, update date nav placement
- `curator_intelligence.py` — check if weekly staleness date is passed in API response (may need `weekly.date` field surfaced)

**Not in scope tonight:**
- Weekly intelligence regeneration schedule (OpenClaw cron question)
- Reaction/save UI on weekly items

**Also fixed today (already committed):**
- `run_intelligence_cron.sh` — was checking `date` field, briefing uses `briefing_date`. Intelligence hadn't run since 03-15. Fixed and verified running.
- `curator_latest.html` — nav emoji + order inconsistency fixed

