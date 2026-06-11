# Curator Research State — 2026-05-31

**Branch:** `feat/curator-research-v2`  
**Last verified:** 2026-05-31 (OpenClaw test run)  
**Data location:** `_NewDomains/research-intelligence/data/`

---

## Current Data State (verified live)

| Object     | Count | Details |
|------------|-------|---------|
| Sources    | 0     | Ready for migration from `article_signals.json` (5 candidates) |
| Topics     | 6     | 4× `active-pull`, 2× `closed` |
| Groups     | 0     | (test groups created & cleaned during verification) |
| Tag aliases| 0     | File exists, empty map `{}` |

**Active-pull topics:**
- `china-rise`, `empire-landpower`, `gold-geopolitics`, `quad-flexibility-not` (expires 2026-06-03)

**Closed topics:**
- `hellscape-taiwan-porcupine`, `strait-of-hormuz`

---

## Schema & State Machine (new in this build)

**Topic states:**
```
dormant ──activate──→ active-pull ──pause──→ paused ──activate──→ active-pull
                           │                                          │
                           └──────────────close──────────────────────┘→ closed (terminal)
one-shot ──────────────────────────────────────────────────close────────→ closed
```

**Key new primitives:**
- `sources/sources.json` — Source records (promotion-by-tag entry points)
- `groups/groups.json` — First-class Group records (`id`, `name`, `member_tags`, `member_topics`)
- `tag_aliases.json` — Hand-edited alias map (read-time only, zero cost)
- All 6 threads migrated to `thread.json` schema v2.0

**Pull scoping:**
- `narrow_pull_context(slug)` — single-topic query context
- `contextual_pull_context(group_id)` — merged queries across group members

---

## What's Built vs. Deferred

**Built (this branch):**
- Full data layer + 17-command CLI (`tools/curator_research.py`)
- Source/Topic/Group CRUD + state machine
- Three promotion paths (feed, session, manual)
- Tag alias resolution (file-based)
- Dry-run migration tools (idempotent)

**Deferred (later PLANs):**
- Leaning / testing tier (Question→Lean→Hold)
- Dormant/radar section + newsworthiness routing
- Flask routes for research UI
- Deep DB spine (Postgres + Neo4j)
- Domain pluggability

---

## Test Results (all 14 cases)

| Test | Description | Result |
|------|-------------|--------|
| T1   | Status overview | ✅ PASS |
| T2   | Topics list (all) | ✅ PASS |
| T3   | Topics by state | ✅ PASS |
| T4   | Narrow pull context (JSON valid) | ✅ PASS |
| T5   | Auto-stop (nothing due) | ✅ PASS |
| T6   | Groups (empty) | ✅ PASS |
| T7   | Sources (empty) | ✅ PASS |
| T8   | Tag aliases (empty) | ✅ PASS |
| T9   | Migrate signals dry-run | ✅ PASS |
| T10  | Migrate threads dry-run (idempotent) | ✅ PASS |
| T11  | Error handling (unknown slug) | ✅ PASS (exit 1) |
| T12  | Help / module load | ✅ PASS |
| T13  | Create group + verify + cleanup | ✅ PASS |
| T14  | Contextual pull (JSON valid) | ✅ PASS |

**All tests passed cleanly.** No regressions observed.

---

## Files Changed on Branch

- `tools/curator_research.py` (new, 1300+ lines)
- `_working/CURATOR_RESEARCH_CLI_REFERENCE.md` (new)
- Data files under `_NewDomains/research-intelligence/data/` (new empty structures + migrated threads)
- `curator_rss_v2.py` + `curator_server.py` (v1.1.5 scoring + model display fix — cherry-picked)

---

**Ready for Robert sign-off.** All tests green, state documented, branch stable.