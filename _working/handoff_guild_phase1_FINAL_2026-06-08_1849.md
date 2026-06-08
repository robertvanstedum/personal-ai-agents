# Handoff — Guild Phase 1 (Foundation) — FINAL
*mini-moi · personal-ai-agents · Guild domain*

- **Authored:** 2026-06-08 18:49 CDT (23:49 UTC) — Claude.ai
- **Supersedes:** handoff_guild_phase1_2026-06-08_1841.md
- **Status:** READY TO BUILD — all Phase 0 questions resolved, all additions incorporated
- **Branch:** `main` directly
- **Reference:** `docs/GUILD_AGENTS_DESIGN.md`

---

## Already done — do not repeat

- [x] Cherry-pick `db/` files from guild branch onto main
- [x] Docker containers running (Postgres + Neo4j)
- [x] `poc_verify.py` run — Phase 5 confirmed deferred
- [x] Directory scaffold: `domains/guild/config/`, `domains/guild/agents/loops/`
- [x] Memory files initialized: `data/guild/memory/cos_memory.md`, `ops_memory.md`

---

## Task 1 — Create `minimoi` Postgres user

```sql
CREATE USER minimoi WITH PASSWORD 'minimoi_local';
GRANT ALL PRIVILEGES ON DATABASE personal_agents TO minimoi;
\c personal_agents
GRANT ALL ON SCHEMA public TO minimoi;
```

**Test:** `python3 domains/guild/db/poc_verify.py` connects without auth error.

---

## Task 2 — Reorganize `db/` → `domains/guild/db/`

```bash
mkdir -p domains/guild/db
mv db/schema.sql      domains/guild/db/
mv db/migrate.py      domains/guild/db/
mv db/postgres.py     domains/guild/db/
mv db/neo4j_driver.py domains/guild/db/
mv db/poc_verify.py   domains/guild/db/
mv db/reconcile.py    domains/guild/db/
mv db/graph_seed.py   domains/guild/db/
```

Update any cross-file imports. **Test:** `python3 domains/guild/db/poc_verify.py` runs clean.

---

## Task 3 — Create PostgreSQL schemas

Run existing `domains/guild/db/schema.sql` first, then add these new tables:

```sql
CREATE SCHEMA IF NOT EXISTS guild;
CREATE SCHEMA IF NOT EXISTS jobs;

-- Agent infrastructure
CREATE TABLE IF NOT EXISTS guild.agent_state (
    id SERIAL PRIMARY KEY,
    agent_name VARCHAR(50) NOT NULL,
    current_state VARCHAR(50) DEFAULT 'idle',
    last_checkin TIMESTAMPTZ DEFAULT NOW(),
    current_agenda TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS guild.agent_log (
    id SERIAL PRIMARY KEY,
    agent_name VARCHAR(50) NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    loop_name VARCHAR(50),
    action TEXT NOT NULL,
    outcome TEXT,
    tier VARCHAR(10),
    metadata JSONB
);

CREATE TABLE IF NOT EXISTS guild.agent_feedback (
    id SERIAL PRIMARY KEY,
    agent_name VARCHAR(50) NOT NULL,
    recommendation_id INTEGER,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    signal_type VARCHAR(20),
    domain VARCHAR(50),
    item_type VARCHAR(50),
    metadata JSONB
);

CREATE TABLE IF NOT EXISTS guild.cos_agenda (
    id SERIAL PRIMARY KEY,
    loop_name VARCHAR(50) NOT NULL,
    domain VARCHAR(50),
    status VARCHAR(20) DEFAULT 'open',
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    next_check_at TIMESTAMPTZ,
    metadata JSONB
);

CREATE TABLE IF NOT EXISTS guild.ops_escalation_queue (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    tier INTEGER NOT NULL,
    description TEXT NOT NULL,
    action_taken TEXT,
    suggested_options TEXT,
    status VARCHAR(20) DEFAULT 'pending',
    resolved_at TIMESTAMPTZ,
    resolved_by VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS guild.ops_maintenance_log (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    tier INTEGER NOT NULL,
    action VARCHAR(100) NOT NULL,
    outcome TEXT,
    auto_resolved BOOLEAN DEFAULT FALSE
);

-- Career Focus domain (note: key is career_focus, NOT job_search)
CREATE TABLE IF NOT EXISTS jobs.career_opportunities (
    id SERIAL PRIMARY KEY,
    source VARCHAR(100),
    source_type VARCHAR(50),
    title VARCHAR(200) NOT NULL,
    company VARCHAR(100),
    geo VARCHAR(100),
    opportunity_type VARCHAR(50),
    warm_lead BOOLEAN DEFAULT FALSE,
    warm_lead_contacts TEXT,
    date_found TIMESTAMPTZ DEFAULT NOW(),
    fit_score DECIMAL(3,1),
    fit_narrative TEXT,
    status VARCHAR(30) DEFAULT 'suggested',
    cos_notes TEXT,
    robert_notes TEXT,
    model_used VARCHAR(50),
    url TEXT,
    date_updated TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS jobs.applications (
    id SERIAL PRIMARY KEY,
    opportunity_id INTEGER REFERENCES jobs.career_opportunities(id),
    date_applied TIMESTAMPTZ,
    contact_id INTEGER,
    cover_letter_path TEXT,
    status VARCHAR(50),
    last_activity TIMESTAMPTZ,
    next_action TEXT,
    next_action_date TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS jobs.contacts (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100),
    company VARCHAR(100),
    role VARCHAR(100),
    relationship VARCHAR(50),
    first_contact TIMESTAMPTZ,
    last_contact TIMESTAMPTZ,
    notes TEXT,
    source VARCHAR(100)
);

CREATE TABLE IF NOT EXISTS jobs.follow_ups (
    id SERIAL PRIMARY KEY,
    entity_type VARCHAR(50),
    entity_id INTEGER,
    due_date TIMESTAMPTZ,
    description TEXT,
    status VARCHAR(20) DEFAULT 'open',
    created_by VARCHAR(50) DEFAULT 'cos'
);
```

**Test:** `\dt guild.*` returns 6 tables. `\dt jobs.*` returns 4 tables.

---

## Task 4 — Create `domains/guild/data/` and LinkedIn placeholder

```bash
mkdir -p domains/guild/data
touch domains/guild/data/.gitkeep
```

Add to `.gitignore`:
```
domains/guild/data/linkedin_connections.csv
```

The file `domains/guild/data/network_companies.json` will be generated from the CSV in
Phase 3 and IS committed (it contains only company names and contact counts, no PII).

**Note for Robert:** Export LinkedIn connections now (Settings → Data Privacy → Get a copy
of your data → Connections). Drop the CSV at `domains/guild/data/linkedin_connections.csv`
when ready. It stays out of git — only the processed JSON is committed.

---

## Task 5 — Create `cos_context.json`

Write to `domains/guild/config/cos_context.json`:

```json
{
  "career_focus": {
    "active": true,
    "deadline": "2026-08-01",
    "mode": ["employment", "contract"],
    "urgency_note": "T-Mobile contract ends Aug 3, 2026. Need placement by Aug 1.",

    "search_sources": {
      "primary": ["web_search_api", "rss_feeds"],
      "web_search_api": "tavily",
      "rss_targets": ["indeed", "linkedin", "glassdoor"],
      "direct_company_pages": [
        "https://careers.telekom.com",
        "https://jobs.ericsson.com"
      ]
    },

    "sector_focus": {
      "primary": "agentic AI — any industry",
      "secondary": "telecom + AI — international, strongest resume match",
      "telecom_ai_score_boost": 1.5,
      "note": "30yr telecom background makes telecom AI roles internationally equivalent to Chicago primary. Float these to top."
    },

    "geo_priority": [
      {
        "geo": "Chicago, IL",
        "priority": 1,
        "mode": "on-site or hybrid",
        "scope": "all TPM roles + all IT/technology leadership + agentic AI"
      },
      {
        "geo": "Remote",
        "priority": 2,
        "mode": "remote",
        "scope": "same role types as Chicago"
      },
      {
        "geo": "Contract",
        "priority": 3,
        "mode": "any location",
        "scope": "any matching role"
      },
      {
        "geo": "International Telecom AI",
        "priority": 4,
        "mode": "on-site or hybrid",
        "markets": ["Germany", "UAE", "Saudi Arabia", "UK", "Netherlands", "Singapore"],
        "target_companies": [
          "Deutsche Telekom",
          "Ericsson",
          "Nokia",
          "Vodafone",
          "STC",
          "Etisalat",
          "du",
          "Zain",
          "Orange",
          "Singtel",
          "StarHub"
        ],
        "role_scope": "Principal, Director, VP, or equivalent — telecom + AI only",
        "language_edge": "German preferred for Deutsche Telekom and DACH region"
      }
    ],

    "target_roles": [
      "Technical Product Manager",
      "Principal Product Manager",
      "Principal Engineer Agentic AI",
      "AI Platform Lead",
      "Solutions Architect",
      "IT Director",
      "System Integrator",
      "VP Engineering",
      "Director AI",
      "Head of AI"
    ],

    "role_keywords": [
      "agentic AI", "LLM orchestration", "AI platform",
      "technical product manager", "TPM", "principal engineer",
      "system integrator", "IT director", "network AI",
      "AI RAN", "telecom AI", "AI transformation"
    ],

    "network": {
      "linkedin_companies_source": "domains/guild/data/network_companies.json",
      "warm_lead_score_boost": 2.0,
      "note": "If opportunity company matches a company in network_companies.json, flag as warm lead and boost score"
    },

    "german_speaking_companies": true,
    "middle_east_open": true,
    "narrative": "Technology leader and hands-on builder. 30 years telecom, satellite, and IoT. Production agentic AI systems. Deep cross-cultural experience across Brazil, UAE, and Latin America. Strong judgment on AI solution design and delivery.",
    "avoid": [],
    "cos_self_improve": "CoS should surface new search sources or APIs as recommendations when better signal is found. Robert decides whether to activate."
  },

  "german": {
    "current_level": "B1-B2",
    "practice_target_sessions_per_week": 3,
    "remind_after_days": 4,
    "watch_for": "emerging language learning tools, AI tutors, conversation groups, new language learning APIs",
    "local_search": "Chicago",
    "online_search": true,
    "user": "robert"
  },

  "curator": {
    "scout_for": "emerging AI governance, telecom AI, MENA geopolitics, USD hegemony, agentic AI platforms",
    "competitive_watch": true
  },

  "mini_moi": {
    "novelty_watch": true,
    "watch_terms": [
      "personal AI agent platform",
      "local-first AI assistant",
      "model-agnostic AI",
      "personal knowledge graph AI",
      "AI chief of staff personal",
      "personal AI briefing system",
      "autonomous AI agent personal use"
    ]
  }
}
```

---

## Task 6 — Create `ops_maintenance_rules.json`

Write to `domains/guild/config/ops_maintenance_rules.json`:

```json
{
  "tier_1": {
    "description": "Act silently, log it",
    "actions": [
      {"trigger": "disk_usage_pct > 75", "action": "archive_screenshots_older_than_days",
       "params": {"days": 30, "exclude": "current/"}},
      {"trigger": "log_file_age_days > 60", "action": "compress_and_archive"},
      {"trigger": "launchd_service_failed", "action": "restart_once_verify",
       "params": {"wait_seconds": 120}},
      {"trigger": "cron_missed_within_day", "action": "rerun_once"}
    ]
  },
  "tier_2": {
    "description": "Act and notify CoS",
    "actions": [
      {"trigger": "daily_briefing_missed_scheduled_time", "action": "rerun_and_notify_cos"},
      {"trigger": "disk_usage_pct > 85", "action": "emergency_cleanup_and_notify_cos"}
    ]
  },
  "tier_3": {
    "description": "Escalate to CoS with options — CoS decides if Robert hears",
    "triggers": [
      "service_restarted_3_times_in_1_hour",
      "disk_growth_unexpected_source",
      "cron_failing_multiple_cycles"
    ]
  },
  "tier_4": {
    "description": "Escalate CoS + Robert immediately",
    "triggers": [
      "service_down_minutes > 15",
      "disk_usage_pct > 95",
      "data_integrity_anomaly"
    ],
    "notify_robert_directly": true
  },
  "maintenance_schedule": {
    "daily": "health_check_log_summary",
    "weekly_day": "sunday",
    "weekly_tasks": [
      "disk_audit", "log_file_review",
      "launchd_status_report", "memory_compaction_ops"
    ],
    "monthly_day": 1,
    "monthly_tasks": [
      "log_rotation", "screenshot_baseline_cleanup_check",
      "db_vacuum", "memory_archive_ops"
    ]
  },
  "service_down_threshold_minutes": 15
}
```

---

## Task 7 — Career Focus portal page

In the existing Jobs portal tab, build a basic pipeline view.

- **Tab label:** stays "Jobs" in the nav — backend key is `career_focus`
- **Pipeline columns:** Suggested / Reviewing / Applied / Interview / Offer / Archived / Rejected
- **Filters:** opportunity_type (employment / contract / advisory) · geo (Chicago / Remote / Contract / International)
- **Card fields:** title, company, geo, type badge, warm lead flag (if applicable), fit score, date found
- **Manual entry only for now** — CoS populates in Phase 3
- No warm lead logic yet — just the `warm_lead` boolean field visible on cards

---

## Task 8 — Agent status panel on Desk page

Add below the research threads list on the Desk page:

```
AGENTS
────────────────────────────────────────────────────────
Chief of Staff     not yet running     —
Operations         not yet running     —
```

Static placeholder. Live status wires in Phase 2 (Operations) and Phase 3 (CoS).

---

## Task 9 — launchd plist templates

Create (do NOT load yet — launchctl load happens in Phase 2/3 when services exist):

- `~/Library/LaunchAgents/com.user.operations.plist` — port 8768
- `~/Library/LaunchAgents/com.user.cos.plist` — port 8769

Match the structure of the closest existing plist (Curator Flask server or German server).
Log paths consistent with existing services.

---

## Definition of done

- [ ] `minimoi` Postgres user: `poc_verify.py` connects without auth error
- [ ] `db/` reorganized to `domains/guild/db/`; imports updated
- [ ] `\dt guild.*` returns 6 tables; `\dt jobs.*` returns 4 tables
- [ ] `domains/guild/data/` exists; `linkedin_connections.csv` in `.gitignore`
- [ ] `cos_context.json` written with full content above
- [ ] `ops_maintenance_rules.json` written with full content above
- [ ] Career Focus pipeline page visible in portal Jobs tab (empty state fine)
- [ ] Agent status panel visible on Desk ("not yet running")
- [ ] launchd plist templates created, NOT loaded
- [ ] `python3 tools/health_check.py` passes — existing services unaffected
- [ ] Committed and pushed to main

---

## Commit

```
git add domains/guild/ data/guild/ [portal templates] [launchd plists] .gitignore
git commit -m "phase1: Guild foundation — schemas, career_focus config, LinkedIn data dir, portal scaffolds"
git push origin main
```

---

*Guild Phase 1 FINAL · main · 2026-06-08 18:49 CDT*
