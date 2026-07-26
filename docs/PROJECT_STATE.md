# PROJECT_STATE.md
**Last updated:** April 18, 2026
**Updated by:** Claude Code

> **Read this first.** One page. All parties — Robert, OpenClaw, Claude Code, Claude.ai — read this at the start of every session before touching anything else.

---

## Current Status

**Domain:** Geopolitics Curator
**Version:** v1.1.5
**State:** ✅ Production — stable and running daily

The geopolitics curator is in production. Daily briefings run at 7 AM via launchd, delivered to Telegram and web UI. All v1.0 workstreams complete.

---

## What's Running

| Component | Status | Notes |
|-----------|--------|-------|
| Daily briefing (launchd) | ✅ Healthy | Hourly poll, time-gated 06:00–18:00, idempotent |
| grok-4-1 scoring | ✅ Active | `--model=grok-4-1 --temperature=0.7` in cron |
| X incremental pull | ✅ Active | OAuth re-authorized Mar 17 · 427 signals |
| Telegram delivery | ✅ Active | Two-bot setup (rvsopenbot + minimoi) |
| AI Observations (WS5) | ✅ Complete | Topic velocity, source anomalies, lateral connections |
| Feedback loop | ✅ Active | Like/dislike/save → profile → scoring |
| Web UI | ✅ Active | Briefing, Library, AI Observations, Priorities |

---

## Open Bugs (unblocking next session)

| # | Title | Owner |
|---|-------|-------|
| #8 | Telegram save callback silent on NetworkError at startup | Claude Code |

---

## Next Domain: Language Learning

**Status:** Design phase — not started
**Confirmed order:** Geopolitics infra upgrades first, then language learning

**Pending before starting:**
- Stale doc cleanup (root + docs/ — ~25 files to archive/delete)
- Bug fixes #8 and #9
- Infrastructure upgrades from ROADMAP.md near-term list (evaluate scope)

**Language learning design:** See `_NewDomains/DOMAIN_SPEC_language_learning.md` (gitignored)

---

## Approved to Build

Nothing currently approved. See ROADMAP.md for candidates.

> **Rule:** Nothing gets built without an explicit "approved to build: [item]" entry here.
> Claude Code reads issues as briefs. OpenClaw validates. Robert decides.

---

## Pending OpenClaw Tasks

- [ ] Stale doc cleanup — root folder + docs/ (~25 old working docs to archive or delete)
- [ ] `_NewDomains/PROJECT_STATE.md` — stale (Mar 7, v0.9-beta). Archive it or replace with a pointer to the root-level version.

---

## Recent History

| Date | What happened |
|------|---------------|
| Mar 20 | Job-search module scaffold + ACC-001 seed. Issues #2 (View Dive button) and #9 (date stat styling) fixed and closed. DECISIONS.md created (DEC-001). WAYS_OF_WORKING.md updated. |
| Mar 18 | README v3, ROADMAP rewrite, ARCHITECTURE Agent Layer, CHANGELOG v1.0.2, PROJECT_STATE created |
| Mar 17 | Telegram timeout/retry, date headers, grok-4-1 in cron, time gate, X OAuth re-auth, ARCHITECTURE.md, OPERATIONS.md, CHANGELOG v1.0.1 |
| Mar 15 | WS5 complete (AI Observations A+B+C), WAYS_OF_WORKING.md, 27 commits |
| Mar 12 | Phase 3C.7: incremental X bookmark pull |

---

## Agent Roles (quick ref)

| Role | Tool | Does |
|------|------|------|
| Strategy | Claude.ai | Plans, specs, architecture decisions |
| Memory | OpenClaw | Validates, documents, files issues, updates state |
| Implementation | Claude Code | Builds from issues/specs — reads GitHub issues as briefs |

**One agent active on the repo at a time. Robert is the decision point between them.**
