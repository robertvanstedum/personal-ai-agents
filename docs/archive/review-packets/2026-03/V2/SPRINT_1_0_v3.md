# Mini-moi Personal AI Curator
## Sprint to 1.0 — March 2026 (v3)

**Target:** Public GitHub launch, production-ready
**Updated:** March 16, 2026
**Status:** 1.0 feature complete. Documentation review and public launch in progress.

---

## Workstream Status

| Workstream | Status | Notes |
|------------|--------|-------|
| WS1 Source Scoring | ✅ Complete | Domain trust weights, drop/trusted/probationary tiers |
| WS2 Broader Sources + Priority Feed | ✅ Complete | Brave web search + institutional RSS feeds |
| WS5 AI Observations (A + B + C) | ✅ Complete | All 5 observation types + response capture web UI |
| WS3 Investigation Workspace | ➡️ Moved to 1.1 | Data layer only — no UI in 1.0 |
| WS4 Mac Mini Migration | ➡️ Moved to 1.1 | Development stays on MacBook through launch |
| Docs + README + ARCHITECTURE + Tag + Launch | 🔄 In progress | Strategy Agent (Claude.ai) drafting; Memory Agent validates |

---

## What 1.0 Delivers

**Core pipeline (Feb–Mar 2026):**
- Two-stage AI scoring with learned preference profile (Haiku pre-filter + reasoning model ranking)
- Three-source candidate pool: RSS (~400) + enriched X bookmarks (~332) + Brave web search (~50) = ~900 daily candidates
- Domain-level source trust scoring (curator_sources.json)
- Learning feedback loop: Like/Dislike/Save → updates local profile → influences tomorrow's run
- 415 bootstrapped signals from X bookmarks (cold start solved)
- Deep Dives, Signal Priorities, Reading Library

**AI Observations layer (WS5, March 15, 2026):**
- Topic velocity, discovery candidates, source anomalies, US press blind spots, lateral connections
- Daily Telegram message at 7:30 AM (30 min after briefing)
- Weekly Sonnet lateral connections (Sundays, separate message)
- Response capture: web UI (curator_intelligence.html) + intelligence_responses.json
- intelligence_responses.json is the seed of the personal memory system (RAG layer in 1.1)

---

## Documentation Tasks (Before Tag)

| Doc | Action | Status |
|-----|--------|--------|
| README.md | Evolve carefully — preserve hook lines, add WS5, fix model table, fix roadmap | 🔄 Draft in progress (README_v2.md) |
| ARCHITECTURE.md | Full replacement — Feb 2026 planning doc is completely stale | 🔄 Draft complete (ARCHITECTURE_v2.md) |
| SPRINT_1_0_v2.md | Update to v3 with final status | ✅ This file |
| CLAUDE.md | Update agent roles + pointer to WAYS_OF_WORKING.md | 📋 Pending |
| CHANGELOG.md | WS5 entries present — review for completeness | 📋 Pending review |
| docs/ folder | Confirm all PLAN and BUILD docs committed | 📋 Pending |

---

## 1.1 Scope (Post-Launch)

| Feature | Description |
|---------|-------------|
| Investigation workspace | Persistent research threads — topic, timeline, annotation, archive |
| Telegram reply capture | Haiku classifies replies to AI Observations → intelligence_responses.json |
| intelligence_responses.json → Sonnet | Lateral connections prompt reads stored responses |
| pending_action activation | Automatic action on flagged observations |
| RAG layer | pgvector + Neo4j — "What have I thought about this before?" |
| Mac Mini migration | Always-on production host |

---

## Notes

- **Model references in all docs** should use role descriptions, not model names — models are swappable and will continue to be evaluated
- **AI Observations** is the preferred name for what was called "Intelligence Layer" internally — clearer, less pretentious
- **Cost figures** should be framed as "actively managed" not pinned — will fluctuate as models and usage evolve
- **SPRINT_1_0.md** (original) is now historical — superseded by this file

---

*v3 updated: March 16, 2026*
*Prepared by: Strategy Agent (Claude.ai)*
*For: Memory Agent (OpenClaw) validation before Claude Code commit*
