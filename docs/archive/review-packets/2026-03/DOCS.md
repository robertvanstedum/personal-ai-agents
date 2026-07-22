# DOCS Registry
> Living inventory of all .md files across the repo and OpenClaw workspace.
> Agents: review this file, add any rows you find missing, populate from your own knowledge.

## Protocol
- When you read any .md file, find its row, increment Times Accessed, update Last Used
- Do not delete rows — mark Notes as "obsolete" or "historical" if no longer active
- New files get a row added immediately upon creation
- If you find a file not listed here, add it
- Concurrent writes are acceptable — this is observability, not a transaction log

## Agent Key
- **OpenClaw** — OpenClaw workspace only
- **ClaudeCode** — Claude Code reads this
- **Both** — either agent may read it
- **Other** — human reference, portfolio, never agent-read

---

## Repo Root (`~/Projects/personal-ai-agents/`)

| File | Domain | Agent | Times Accessed | Last Used | Notes |
|------|--------|-------|---------------|-----------|-------|
| CLAUDE.md | Agent orientation | ClaudeCode | 1 | 2026-03-08 | Auto-read every session. Points to PROJECT_STATE.md |
| OPERATIONS.md | How to run the system | Both | 1 | 2026-03-08 | Commands, health checks, cost rules, file locations |
| CHANGELOG.md | Change log | Both | 0 | — | Append-only. Also exists in OC workspace |
| CURATOR_ROADMAP.md | What's built / active / next | Both | 0 | — | Phase tracking. Most current roadmap doc |
| DEVELOPMENT.md | How we build | Both | 0 | — | Team roles, testing checklist, workflow |
| TELEGRAM_ARCHITECTURE.md | Two-bot setup | Both | 2 | 2026-03-08 | Validated 2026-03-05. Operationally critical. Companion to docs/FEATURE_TELEGRAM_ARCHITECTURE.md |
| DOCS.md | File registry | Both | 1 | 2026-03-08 | This file. Audit against all .md files in repo |
| BACKLOG.md | Small fixes and improvements | Both | 0 | — | Planned — not yet created. Consider consolidating FEATURE_DELETE_DEEP_DIVES.md and CURATOR_UX_BACKLOG.md here |
| README.md | Public project overview | Other | 1 | 2026-03-08 | Portfolio / GitHub facing. Protected |
| README_v2 March 3.md | Draft README | Other | 0 | — | Uncommitted draft at root. Review for merge or delete |
| ENGINEERING.md | Engineering philosophy | Both | 0 | — | OC workspace only (not in repo). Reference when onboarding or refocusing |
| CURATOR_PROMPTS.md | Scoring prompt text | Both | 0 | — | OC workspace only (not in repo). Reference when changing scoring logic |
| CURATOR_CALLBACKS.md | Callback handling detail | Both | 0 | — | OC workspace only (not in repo). Operational detail. Could fold into MEMORY.md |
| CURATOR_FEEDBACK_DESIGN.md | Phase 2B feedback spec | Other | 0 | — | Phase 2B built. Historical spec, still useful if extending feedback loop |
| PHASE_3C_PLAN.md | Phase 3C technical spec | Both | 0 | — | Phase 3C complete. Keep if revisiting X adapter |
| VOICE_NOTES.md | Freeform notes | Other | 0 | — | One entry (Cuba research, Feb 27). Human reference. Also in OC workspace |
| TOOLS.md | Tool inventory | Both | 0 | — | OC workspace only (not in repo). Mostly empty. Populate or archive |
| AGENTS.md | OpenClaw agent template | OpenClaw | 0 | — | OC workspace only (not in repo) |
| PROJECT_BRIEF.md | Project summary | Other | 0 | — | Portfolio / human reference |
| PROJECT_ROADMAP.md | Early system vision (Feb 7) | Other | 0 | — | Historical. Superseded by CURATOR_ROADMAP.md. Also in OC workspace |
| ARCHITECTURE.md | Early architecture thinking | Other | 0 | — | Historical. Pre-_NewDomains |
| PLATFORM_POC.md | Pre-_NewDomains platform thinking | Other | 0 | — | Historical. Superseded |
| PLATFORM_UNIFIED.md | Pre-_NewDomains platform thinking | Other | 0 | — | Historical. Superseded |
| BUILD_PLAN_v0.9.md | Feb 28 sprint plan | Other | 0 | — | v0.9-beta shipped. Historical |
| CURATOR_ENHANCEMENT_ANALYSIS.md | Enhancement planning | Other | 0 | — | Review: completed or stale? |
| TODO_MULTI_PROVIDER.md | Multi-provider planning | Other | 0 | — | Review: completed or stale? |
| CLAUDE_MEMORY_PROMPT.md | Old agent orientation | Other | 0 | — | Superseded by CLAUDE.md |
| CURATOR_README.md | Curator-specific readme | Other | 0 | — | Relationship to README.md unclear. Review |
| AI_TOOLS_EVALUATION.md | Tool selection rationale | Other | 0 | — | Decision made. Portfolio/interview reference |
| CREDENTIALS_SETUP.md | Initial credential setup | Other | 0 | — | Done. Only useful on fresh machine setup |
| PRODUCTION_SECURITY.md | Security setup | Other | 0 | — | Review: current or historical? |
| XAI_BUDGET_TRACKER.md | xAI cost tracking | Other | 0 | — | Superseded by OPERATIONS.md cost rules |
| COST_COMPARISON.md | Model cost comparison | Other | 0 | — | Historical. Decision made |
| HAIKU_IMPLEMENTATION_PLAN.md | Haiku fallback spec | Other | 0 | — | OC workspace only (not in repo). Built. Historical |
| CURATOR_DEDUP_IMPLEMENTATION.md | Dedup spec | Other | 0 | — | OC workspace only (not in repo). Built. Historical |
| CURATOR_REFACTOR.md | Refactor planning | Other | 0 | — | OC workspace only (not in repo). Review: completed? |
| FEATURE_PLAN_INTEREST_CAPTURE.md | Interest capture spec | Other | 0 | — | Built. Historical |
| INTEREST_CAPTURE_README.md | Interest capture docs | Other | 0 | — | Built. Historical |
| ROADMAP_X_INTEGRATION.md | X integration planning | Other | 0 | — | Built. Historical. Also in OC workspace |
| TESTING_CHECKLIST.md | Testing checklist | Other | 0 | — | Superseded by DEVELOPMENT.md |
| WEEKEND_PLAN.md | One-off sprint plan | Other | 0 | — | OC workspace only (not in repo). Done. Historical |
| WEEKEND_QUICKSTART.md | One-off quickstart | Other | 0 | — | OC workspace only (not in repo). Done. Historical |
| CRON-SETUP.md | Cron setup notes | Other | 0 | — | Superseded by launchd migration. Also in OC workspace |
| crontab-setup-notes.md | Cron notes | Other | 0 | — | OC workspace only (not in repo). Superseded by launchd migration |
| TELEGRAM_WEBHOOK_PLAN.md | Webhook approach | Other | 0 | — | Superseded by TELEGRAM_ARCHITECTURE.md |
| CLAUDE_PROJECT_SETUP.md | Initial Claude setup | Other | 0 | — | OC workspace only (not in repo). One-time setup. Done |
| BOOTSTRAP.md | Bootstrap instructions | Other | 0 | — | OC workspace only (not in repo). One-time setup. Done |
| FEATURE_DELETE_DEEP_DIVES.md | Delete feature spec | Other | 0 | — | Moved to BACKLOG.md |
| CURATOR_UX_BACKLOG.md | UX backlog | Other | 0 | — | OC workspace only (not in repo). Moved to BACKLOG.md |
| trusted-sources.md | Source list | Both | 0 | — | Review: still used by scoring? |
| reading-list-availability.md | Reading list research | Other | 0 | — | OC workspace only (not in repo). One-off research artifact |
| chrome-extension-setup.md | Chrome extension notes | Other | 0 | — | OC workspace only (not in repo). One-off. Review: still relevant? |
| feature-request-exec-cron.md | Feature request note | Other | 0 | — | OC workspace only (not in repo). One-off artifact |
| HEARTBEAT.md | System heartbeat | Both | 0 | — | OC workspace only (not in repo). Empty. Populate or remove |
| IDENTITY.md | Identity notes | Other | 0 | — | OC workspace only (not in repo). 3 lines. Consider folding into SOUL.md |

---

## docs/ (`~/Projects/personal-ai-agents/docs/`)

| File | Domain | Agent | Times Accessed | Last Used | Notes |
|------|--------|-------|---------------|-----------|-------|
| NEXT_PHASE_PLAN_grok41.md | Grok 4.1 upgrade plan | Both | 1 | 2026-03-08 | Saved for implementation. Verify model name before building |
| FEATURE_TELEGRAM_ARCHITECTURE.md | Telegram two-bot design | Both | 0 | — | Validated design doc. Companion to TELEGRAM_ARCHITECTURE.md at root |
| CASE_STUDY_GROK41_MODEL_TUNING.md | Grok 4.1 tuning case study | Other | 0 | — | Written 2026-03-06. Portfolio/reference |
| CASE-STUDY-DEEP-DIVE-FEATURE.md | Deep dive feature case study | Other | 0 | — | Historical. Feature shipped |
| FEATURE_DEEP_DIVE_RATINGS.md | Deep dive ratings spec | Other | 0 | — | Review: built or backlog? |
| WORKSPACE-SETUP.md | Workspace setup guide | Other | 0 | — | One-time setup reference |
| portfolio/phase3c-enrichment-results.md | Phase 3C enrichment results | Other | 0 | — | Portfolio data artifact |
| portfolio/phase3c-results.md | Phase 3C results summary | Other | 0 | — | Portfolio data artifact |
| test-reports/2026-03-03-phase3c-ab-test.md | Phase 3C A/B test report | Other | 0 | — | Historical test report |
| test-reports/2026-03-06-grok41-ab-test.md | Grok 4.1 A/B test report | Other | 0 | — | Recent. Reference for model tuning decisions |
| test-reports/GROK41_MIGRATION_SUMMARY.md | Grok 4.1 migration summary | Other | 0 | — | Uncommitted. Migration complete |
| test-reports/REPORT_SCHEMA.md | Test report schema | Other | 0 | — | Schema reference for future test reports |

---

## _NewDomains/ (gitignored — local only)

| File | Domain | Agent | Times Accessed | Last Used | Notes |
|------|--------|-------|---------------|-----------|-------|
| PROJECT_STATE.md | Current state snapshot | Both | 1 | 2026-03-08 | First file read after CLAUDE.md. Dashboard not master doc |
| ARCHITECTURE.md | Platform vision and principles | Both | 1 | 2026-03-08 | Domain independence design. Living doc |
| README.md | Agent rules for this folder | Both | 1 | 2026-03-08 | Hard rules for Claude Code and OpenClaw |
| DOMAIN_SPEC_finance.md | Finance domain design | Both | 1 | 2026-03-08 | Design phase only. Private indefinitely |
| DOMAIN_SPEC_language_learning.md | Language learning design | Both | 1 | 2026-03-08 | Design phase only. Blocked on Grok transcript export |
| DOMAIN_SPEC_commercial.md | Commercial platform design | Both | 1 | 2026-03-08 | Design phase only. Rails 8 + Stripe Connect. Separate repo when built |
| INSTALL.md | Install instructions | Other | 1 | 2026-03-08 | One-time setup instructions for this package |

---

## interests/ (gitignored — generated content)

| File | Domain | Agent | Times Accessed | Last Used | Notes |
|------|--------|-------|---------------|-----------|-------|
| README.md | Interests folder overview | Other | 0 | — | Gitignored. Human reference only |
| TEMPLATE.md | Interest capture template | Other | 0 | — | Gitignored. Template for new entries |
| 2026-02-13-thoughts.md | Freeform thoughts | Other | 0 | — | Gitignored. Personal notes |
| 2026-02-16-flagged.md | Flagged articles | Other | 0 | — | Gitignored. Generated content |
| deep-dives/*.md | Deep dive articles | Generated | 3+ | 2026-03-08 | 3 recent deep dives from Feb 16 curations |
| 2026/deep-dives/*.md | Deep dive articles | Generated | 10+ | 2026-03-08 | 15+ deep dives from Mar briefings. Auto-generated from curation scoring |

---

## OpenClaw Workspace (`~/.openclaw/workspace/`)

| File | Domain | Agent | Times Accessed | Last Used | Notes |
|------|--------|-------|---------------|-----------|-------|
| SOUL.md | Identity / values | OpenClaw | 3+ | 2026-03-07 | Core identity. Read every session |
| USER.md | User profile | OpenClaw | 3+ | 2026-03-07 | Robert's context and preferences. Read every session |
| MEMORY.md | Operational memory | OpenClaw | 5+ | 2026-03-08 | Running memory across sessions. Updated daily |
| WHITEBOARD.md | Brainstorming | OpenClaw | 3 | 2026-03-07 | Exploratory thinking. Now ideas-only with governance rule |
| PROJECT_STATE.md | Build authorization | OpenClaw | 2 | 2026-03-07 | Master orientation. Read first (in workspace, separate from repo) |
| AGENTS.md | OpenClaw agent template | OpenClaw | 2+ | 2026-03-07 | Workspace setup guide for this instance |
| HEARTBEAT.md | System heartbeat | OpenClaw | 1 | 2026-03-08 | Empty. Can populate with daily checks or remove |
| IDENTITY.md | Identity notes | OpenClaw | 1 | 2026-03-07 | 3 lines. Consider folding into SOUL.md or keeping separate |
| TOOLS.md | Tool inventory | OpenClaw | 1 | 2026-03-07 | Mostly empty. Populate with camera names, SSH hosts, etc. |
| finance/ | Finance domain folder | OpenClaw | 1 | 2026-03-07 | New private domain (gitignored in repo). .gitignore + README created. Design phase |
| memory/ | Daily memory logs | OpenClaw | 3+ | 2026-03-08 | Session continuity. Contains 2026-03-01.md through 2026-03-07.md |
| CHANGELOG.md | Change log | OpenClaw | 1 | 2026-03-07 | Session-by-session record. Also in repo root. Consider which is authoritative |
| TONIGHT_SESSION_CHECKLIST.md | Session checklist | OpenClaw | 0 | — | One-off checklist. Historical. Can archive |
| PHASE_3C_SPEC.md | Phase 3C spec | OpenClaw | 0 | — | Phase 3C complete. Historical. Archive if not needed |
| VOICE_NOTES_SPEC.md | Voice notes spec | OpenClaw | 0 | — | Feature spec. Review: built? Historical artifact |
| WEEKEND_PLAN_BETA.md | Weekend plan beta | OpenClaw | 0 | — | Historical sprint plan. Can archive |

---

## Future Domains (for awareness — not current focus)

| Domain | Status | Notes |
|--------|--------|-------|
| RVSAssociates | Placeholder only | Commercial platform. No active build yet |
| Vera jewelry site | Pending name/domain decision | Phase 1 commercial. Blocked on naming |
| Language learning (German) | Future | Grok transcript export method TBD |
| Finance parser (Itaú) | Future | Needs OFX/CSV export sample from Robert |

---

*Schema version 1.2 — created 2026-03-08, updated 2026-03-08 (evening)*

---

## Summary of Changes (Mar 8 Evening Update)

**Added rows:**
- TELEGRAM_ARCHITECTURE.md companion note (repo root)
- Deep-dives tracking (interests/deep-dives and interests/2026/deep-dives)
- OpenClaw workspace files with accurate access counts

**Corrected:**
- TELEGRAM_ARCHITECTURE.md: updated access counts, added companion reference
- Project_STATE.md: now tracked separately in workspace (different from repo)
- finance/ domain: new folder in workspace, gitignored in repo
- memory/ folder: daily session logs tracked

**Status notes:**
- Many historical/obsolete files marked for archival review
- Two CHANGELOG.md files (workspace + repo) — clarify which is primary
- Some workspace files marked empty or minimal — consider consolidation
- Deep-dives auto-generated from curation, no manual tracking needed (for info only)
