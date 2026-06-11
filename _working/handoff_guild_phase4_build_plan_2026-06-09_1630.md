# Guild Phase 4 Build Plan — CoS Intelligence Loops
*mini-moi · personal-ai-agents · Guild domain*
*Authored: 2026-06-09 16:30 CDT — Claude Code*
*Status: READY TO BUILD — awaiting Robert go-ahead after claude.ai review*
*Source spec: `_working/handoff_guild_phase4_2026-06-09_1454.md`*

---

## Robert's pre-answered decisions

**1. Dependencies (apscheduler, tavily-python)**
> "If we need to install these, let me know how or you can do it."
→ Claude Code will install them as Step 0. No action needed from Robert.

**2. LLM models**
> "I'm ok with xai 4-1 fast reasoning for both, cost is low for now."
→ Both filter pass AND quality eval use `grok-4-1-fast-reasoning`. No Haiku/Sonnet.
→ Revisit if cost becomes noticeable after first week of runs.

**3. Missing DB schema**
> "Ok to move missing schema now."
→ Claude Code creates `domains/guild/db/schema_phase4.sql` and applies it when Docker is back up.
→ All loops built with JSON file fallback — run regardless of Docker state.

---

## Pre-flight findings

| Item | State | Action |
|---|---|---|
| `apscheduler` | In requirements.txt but NOT installed in venv | `pip install apscheduler` (Step 0) |
| `tavily-python` | NOT in requirements.txt, NOT installed | `pip install tavily-python` + add to requirements.txt (Step 0) |
| `feedparser` | ✅ Installed | None |
| `domains/guild/agents/loops/` | ✅ Exists, empty | None |
| `jobs.career_opportunities` | Missing from schema.sql | schema_phase4.sql (Step 1) |
| `guild.agent_feedback` | Missing from schema.sql | schema_phase4.sql (Step 1) |
| `guild.cos_agenda` | Missing from schema.sql | schema_phase4.sql (Step 1) |
| Docker / DB | Currently down | File fallback throughout |
| `network_companies.json` | Does not exist | Warm lead check handles missing file gracefully |

---

## Build order

### Step 0 — Install dependencies
```bash
venv/bin/pip install apscheduler tavily-python
# add tavily-python to requirements.txt
```

### Step 1 — DB schema
New file: `domains/guild/db/schema_phase4.sql`

Three new tables:

**`jobs.career_opportunities`** — job opportunities found by Loop A
```sql
CREATE TABLE IF NOT EXISTS jobs.career_opportunities (
    id                   SERIAL PRIMARY KEY,
    title                TEXT NOT NULL,
    company              TEXT,
    geo                  TEXT,
    url                  TEXT UNIQUE,
    opportunity_type     TEXT,       -- employment / contract / advisory
    fit_score            NUMERIC(4,2),
    fit_narrative        TEXT,
    warm_lead            BOOLEAN DEFAULT FALSE,
    warm_lead_contacts   TEXT,
    cos_notes            TEXT,
    source               TEXT,       -- tavily / rss / direct
    model_used           TEXT,
    status               TEXT DEFAULT 'suggested',  -- suggested / reviewed / applied / dismissed
    created_by           TEXT DEFAULT 'cos_loop_a',
    created_at           TIMESTAMPTZ DEFAULT now()
);
```

**`guild.cos_agenda`** — all loops write recommendations here
```sql
CREATE TABLE IF NOT EXISTS guild.cos_agenda (
    id           SERIAL PRIMARY KEY,
    domain       TEXT,       -- career_focus / german / curator / mini_moi / operations
    description  TEXT,
    confidence   NUMERIC(3,2),
    loop_name    TEXT,
    status       TEXT DEFAULT 'pending',  -- pending / confirmed / dismissed / deferred
    created_at   TIMESTAMPTZ DEFAULT now()
);
```

**`guild.agent_feedback`** — learning signal when Robert acts on recommendations
```sql
CREATE TABLE IF NOT EXISTS guild.agent_feedback (
    id                SERIAL PRIMARY KEY,
    agent_name        TEXT DEFAULT 'cos',
    recommendation_id INT,
    signal_type       TEXT,   -- confirmed / dismissed / deferred / click_through
    domain            TEXT,
    item_type         TEXT,   -- job_opportunity / tool_found / topic_suggested / competitive_find
    created_at        TIMESTAMPTZ DEFAULT now()
);
```

### Step 2 — Loop A: `cos_job_search.py`

**File:** `domains/guild/agents/loops/cos_job_search.py`
**Cadence:** twice daily — 06:00 and 18:00 local
**Model:** `grok-4-1-fast-reasoning` for both filter and eval passes

Pipeline:
```
run_career_focus_scout()
  ├── _search_tavily()         — ≤10 queries from cos_context.json, geo_priority order
  ├── _fetch_rss()             — Indeed RSS (try/except per feed, failures non-fatal)
  ├── _deduplicate()           — skip URLs already in DB or file store
  ├── _filter_pass()           — LLM score each result 0-10, discard < 5
  ├── _evaluate_pass()         — full eval on shortlist (fit_score, narrative, opportunity_type)
  ├── _warm_lead_check()       — fuzzy match network_companies.json (skip if file missing)
  ├── _write_results()         — DB first (jobs.career_opportunities), JSON fallback
  └── _notify_telegram()       — rvsopenbot ping if score ≥ 8.0 or warm_lead + score ≥ 7.0
```

Queries built dynamically from cos_context.json:
- `"{role}" Chicago` — one per target_role (top 5 by priority)
- `"{role}" remote` — remote pass
- `"agentic AI" site:careers.telekom.com` — Deutsche Telekom direct
- `"telecom AI" {market}` — top 2 international markets

**Telegram ping format (above threshold only):**
```
🎯 Career opportunity found:
{title} — {company}
{geo} · {opportunity_type}
Score: {fit_score}/10
{fit_narrative}
[⭐ Warm lead — {contacts}]
{url}
```

### Step 3 — Loops B, C, D

Build after Loop A manual test passes and Robert reviews first batch.

| Loop | File | Cadence | What it does |
|---|---|---|---|
| B | `cos_german_watch.py` | Sun 09:00 | Practice cadence check + Tavily tool search → Telegram reminder + cos_agenda |
| C | `cos_curator_watch.py` | Sun 10:00 | Tavily per `scout_for` term → cos_agenda only (no auto-add to Curator) |
| D | `cos_novelty_watch.py` | 1st+15th 08:00 | Tavily per `watch_terms` → cos_agenda + Telegram for threat/incorporate |

### Step 4 — Wire into `chief_of_staff.py`

Add APScheduler at startup + `/loops` status endpoint:
```python
from apscheduler.schedulers.background import BackgroundScheduler
scheduler = BackgroundScheduler()
scheduler.add_job(run_career_focus_scout, 'cron', hour='6,18',           id='loop_a')
scheduler.add_job(run_german_watch,       'cron', day_of_week='sun', hour=9,  id='loop_b')
scheduler.add_job(run_curator_scout,      'cron', day_of_week='sun', hour=10, id='loop_c')
scheduler.add_job(run_novelty_watch,      'cron', day='1,15',        hour=8,  id='loop_d')
scheduler.start()
```

`/loops` endpoint — shows last run time, result count, and status per loop.

---

## Files to create/modify

| File | Change |
|---|---|
| `requirements.txt` | Add `tavily-python` |
| `domains/guild/db/schema_phase4.sql` | New — 3 tables |
| `domains/guild/agents/loops/__init__.py` | New (empty) |
| `domains/guild/agents/loops/cos_job_search.py` | New — Loop A |
| `domains/guild/agents/loops/cos_german_watch.py` | New — Loop B |
| `domains/guild/agents/loops/cos_curator_watch.py` | New — Loop C |
| `domains/guild/agents/loops/cos_novelty_watch.py` | New — Loop D |
| `domains/guild/agents/chief_of_staff.py` | Add scheduler + `/loops` endpoint |

---

## Gate before scheduler goes live

1. Run Loop A once manually — confirm no errors
2. Check `data/guild/career_opportunities.json` (file fallback) — ≥1 result written
3. Robert reviews first batch of job results for quality
4. Robert signs off → scheduler activated in chief_of_staff.py
5. Restart CoS under launchd

**Scheduler does not go live until Robert explicitly approves the first batch.**

---

## Commit

```
git add domains/guild/agents/loops/ domains/guild/db/schema_phase4.sql \
        domains/guild/agents/chief_of_staff.py requirements.txt
git commit -m "phase4: CoS intelligence loops — career scout, German watch, Curator scout, novelty watch"
```

---

*Guild Phase 4 · main · Claude Code plan · 2026-06-09 16:30 CDT*
*Decisions pre-answered by Robert. Ready to build on go-ahead after claude.ai review.*
