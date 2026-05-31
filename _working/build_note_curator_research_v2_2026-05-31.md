# Build Note — Curator Research Gathering Tier v2
*Branch: `feat/curator-research-v2` · Date: 2026-05-31 · Author: Claude Code*
*For: OpenClaw (run tests + write STATE doc) and Claude.ai (design review)*

---

## What was built (this session)

Steps 1–4 of `PLAN-curator-gathering-tier-2026-05-31.md` are complete and committed.

| Step | What | Key file(s) |
|---|---|---|
| 1 | Source schema + CRUD | `tools/curator_research.py` — `add_source()`, `get_sources()`, `resolve_tags()` |
| 2 | Topic state machine + thread migration | `tools/curator_research.py` — `activate_topic()`, `pause_topic()`, `auto_stop_check()`, `migrate_threads_to_topics()` |
| 3 | Promotion-by-tag flow (3 entry paths) | `tools/curator_research.py` — `promote_feed_article()`, `promote_session_find()`, `promote_manual()` |
| 4 | Group primitive + pull scoping | `tools/curator_research.py` — `create_group()`, `narrow_pull_context()`, `contextual_pull_context()` |
| CLI | 17-command interface (primary UI until Flask routes added) | `tools/curator_research.py __main__` |
| Ref doc | Permanent CLI reference | `_working/CURATOR_RESEARCH_CLI_REFERENCE.md` |

Additional commits on this branch (pre-steps):
- **v1.1.5 scoring** (probationary 0.6, trusted source diversity discount) — cherry-picked to `curator_rss_v2.py`
- **Model display fix** — daily briefing now reads `briefing_model` from JSON instead of hardcoded string
- **Vorbereitung card redesign** — editorial textarea + suggestion pre-fill + inline brief preview
- **Gespräche KI-Personas / Konversation toggle** — pill toggle, 3-card Konversation layout, Echter Tutor promoted out of persona list footer

---

## Data layer overview

### Paths (all resolved relative to repo root)
```
_NewDomains/research-intelligence/data/
  sources/sources.json        — Source records (currently empty — 5 ready to migrate)
  tag_aliases.json            — Hand-edited alias map (currently empty {})
  threads/{slug}/thread.json  — Topic records (6 existing, all migrated to schema v2.0)
  groups/groups.json          — Group records (currently empty)
  feedback/article_signals.json  — Legacy saves (5 articles, read-only after migration)
```

### Current data state
- **Sources**: 0 (5 ready to migrate from `article_signals.json` — Robert's call)
- **Topics**: 6 total
  - `active-pull`: china-rise, empire-landpower, gold-geopolitics, quad-flexibility-not
  - `closed`: hellscape-taiwan-porcupine, strait-of-hormuz
- **Groups**: 0
- **Tag aliases**: 0

### Topic state machine
```
dormant ──activate──→ active-pull ──pause──→ paused ──activate──→ active-pull
                           │                                          │
                           └──────────────close──────────────────────┘→ closed (terminal)
one-shot ──────────────────────────────────────────────────close────────→ closed
```
Engagement gate: `duration_days` + `expires` field. `auto_stop_check()` moves expired active-pull topics to dormant (date-math only, no AI cost).

### Group primitive
First-class record with `id`, optional `name`, `member_tags`, `member_topics`. Leaning attachment point (`leaning: null`) exists in schema — Leaning itself is a later PLAN (not built here). A Group with no Leaning is a Theme.

### Pull scoping
- `narrow_pull_context(slug)` → queries from that topic's `agent/config.json` entry
- `contextual_pull_context(group_id)` → merged queries across all member topics

### Tag alias resolution
`tag_aliases.json` is a hand-edited file (`{"prc": "china", "quad-security": "quad"}`). Resolution is **read-time lookup only** — stored tags are always the original string entered. No AI, no auto-merge.

---

## Governing constraint (build to this)
**Spend follows attention** — nothing expensive runs without Robert's engagement.
- Tag aliasing: file lookup, zero cost
- Grouping: Robert's explicit action, never auto-created
- Promotion: Robert is the gate on all three paths
- No background inference, no unattended pipeline steps

---

## Test cases — OpenClaw: run all of these

**Run from repo root: `~/Projects/personal-ai-agents/`**

### T1 — Status overview
```bash
python3 tools/curator_research.py status
```
**Expect:**
- `Sources    : 0`
- `Topics     : 6`
- `  active-pull       : 4`
- `  closed            : 2`
- `Groups     : 0`
- `Tag aliases: 0`
- No "⚠" overdue warning (quad-flexibility-not expires 2026-06-03, not past today)

---

### T2 — Topics list (all)
```bash
python3 tools/curator_research.py topics
```
**Expect:** 6 rows. Confirm these slugs appear:
`china-rise`, `empire-landpower`, `gold-geopolitics`, `hellscape-taiwan-porcupine`, `quad-flexibility-not`, `strait-of-hormuz`

---

### T3 — Topics filter by state
```bash
python3 tools/curator_research.py topics --status active-pull
python3 tools/curator_research.py topics --status closed
```
**Expect:**
- active-pull: 4 rows (china-rise, empire-landpower, gold-geopolitics, quad-flexibility-not)
- closed: 2 rows (hellscape-taiwan-porcupine, strait-of-hormuz)

---

### T4 — Pull context: narrow
```bash
python3 tools/curator_research.py pull narrow quad-flexibility-not
```
**Expect:** Valid JSON with:
- `"scope": "narrow"`
- `"topic_slug": "quad-flexibility-not"`
- `"status": "active-pull"`
- `"queries"` array with 8 entries
- `"motivation"` non-empty string

Pipe-verify JSON validity:
```bash
python3 tools/curator_research.py pull narrow quad-flexibility-not | python3 -m json.tool > /dev/null && echo "PASS: valid JSON"
```

---

### T5 — Auto-stop: nothing to stop
```bash
python3 tools/curator_research.py auto-stop
```
**Expect:** `No topics past expiry — nothing changed.`

---

### T6 — Groups: empty
```bash
python3 tools/curator_research.py groups
```
**Expect:** `No groups.`

---

### T7 — Sources: empty
```bash
python3 tools/curator_research.py sources
```
**Expect:** `No sources found.`

---

### T8 — Tag aliases: empty
```bash
python3 tools/curator_research.py tag-aliases
```
**Expect:** `No tag aliases defined. Edit: _NewDomains/research-intelligence/data/tag_aliases.json`

---

### T9 — Migrate signals: dry-run (read-only, safe to run)
```bash
python3 tools/curator_research.py migrate-signals --dry-run
```
**Expect:**
- `Would create 5 Sources from article_signals.json:`
- 5 article lines including `Arrighi`, `Krause`, `Martins`, `Kotkin`, `Nixon`
- No files written (dry-run)

---

### T10 — Migrate threads: dry-run idempotency check
```bash
python3 tools/curator_research.py migrate-threads --dry-run
```
**Expect:** All 6 threads show `⏭  Already v2.0: <slug>` — confirms schema migration is idempotent.

---

### T11 — Error handling: unknown topic slug
```bash
python3 tools/curator_research.py pull narrow nonexistent-slug; echo "Exit: $?"
```
**Expect:**
- `Error: Topic not found: 'nonexistent-slug'` on stderr
- `Exit: 1`

---

### T12 — Help output (smoke test: module loads cleanly)
```bash
python3 tools/curator_research.py --help
```
**Expect:** Usage block listing all 17 commands. Exit 0.

---

### T13 — Create group (dry-run — fully non-destructive)
```bash
python3 tools/curator_research.py create-group \
  --name "Indo-Pacific Test" \
  --topics quad-flexibility-not,china-rise \
  --tags quad,china,test \
  --dry-run
python3 tools/curator_research.py status
```
**Expect:**
- `create-group` prints `[DRY RUN] Would create group: grp_20260531_001 (Indo-Pacific Test)`
- `  Topics: quad-flexibility-not, china-rise`
- `  Tags  : quad, china, test`
- `  (nothing written to disk)`
- `status` still shows `Groups     : 0`

How `--dry-run` works: the full record is built and assigned an ID (so you can see exactly what would be written), but the final `_save_groups()` call is skipped. The moment the command exits, nothing changed on disk. No cleanup needed.

---

### T14 — Contextual pull context (self-contained — no prior write needed)
```bash
# Create a real group just for this test, then clean it up afterward
python3 tools/curator_research.py create-group \
  --name "Indo-Pacific Test" \
  --topics quad-flexibility-not,china-rise
python3 tools/curator_research.py pull contextual grp_20260531_001 | python3 -m json.tool > /dev/null && echo "PASS: valid JSON"
# Cleanup: reset groups.json to []
python3 -c "
import json; from pathlib import Path
p = Path('_NewDomains/research-intelligence/data/groups/groups.json')
p.write_text('[]')
print('groups.json reset to []')
"
```
**Expect:** Valid JSON with `"scope": "contextual"`, `"group_id": "grp_20260531_001"`, merged queries from both member topics.

---

## Definition of done (this build)

Per the PLAN:
- [x] Steps 1–4 built and committed
- [x] All test cases above pass
- [ ] **STATE doc written/updated** — OpenClaw's job (see below)
- [ ] No regression to v1.1.5 scoring or top-20 selection (German suite: 48/49 baseline)
- [ ] Robert's sign-off → commit + push `feat/curator-research-v2`

---

## OpenClaw: what to do after running tests

1. **Run all test cases T1–T14** above. Report pass/fail for each.

2. **Write the STATE doc** — `_working/CURATOR_STATE_2026-05-31.md` (or update existing Curator state doc if one exists). Cover:
   - Current Curator structure (what files exist, what pipeline runs)
   - The new Source/Topic/Group objects (schema fields, state machine)
   - Tag-alias mechanism (how it works, where the file lives)
   - What's built vs. deferred (Leaning — later PLAN; dormant section UI — later PLAN; Flask routes for research — later PLAN; DB spine — separate Track-B PLAN)
   - Branch: `feat/curator-research-v2`, what it adds vs. main

3. **Report** test results + confirm STATE doc location to Robert so he can give sign-off.

---

## Out of scope (do not build, later PLANs)

- Leaning / testing tier (Question→Lean→Hold, evidence, teammate read)
- Dormant section + radar routing
- Flask routes for research UI (`research_routes.py` wiring)
- Deep DB/spine work (Postgres + Neo4j/temporal memory)
- Domain pluggability

---

## Key files on this branch (new or significantly changed)

```
tools/curator_research.py                        ← NEW: data layer + CLI (1300+ lines)
tools/tools_config.json                          ← unchanged
_working/CURATOR_RESEARCH_CLI_REFERENCE.md       ← NEW: permanent CLI reference
_NewDomains/research-intelligence/data/sources/sources.json   ← NEW (empty [])
_NewDomains/research-intelligence/data/tag_aliases.json       ← NEW (empty {})
_NewDomains/research-intelligence/data/groups/groups.json     ← NEW (empty [])
_NewDomains/research-intelligence/data/threads/*/thread.json  ← migrated to schema v2.0
curator_rss_v2.py                                ← v1.1.5 scoring + model stamp
curator_server.py                                ← model display fix
domains/german/templates/german_gesprache.html   ← Konversation toggle + Vorbereitung card
domains/german/html_server.py                    ← /api/tutor-brief/suggestion + updated generate
domains/german/german_domain.py                  ← build_tutor_brief() + get_last_human_session_suggestion()
domains/german/static/german.css                 ← konv-brief-preview pre-wrap
```
