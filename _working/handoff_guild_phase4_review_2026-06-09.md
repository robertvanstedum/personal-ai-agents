# Guild Phase 4 — Build Review
*mini-moi · personal-ai-agents · Guild domain*
*Session: 2026-06-09 · Author: Claude Code · Status: BUILT, RUNNING, COMMITTED*

---

## What Was Built

Guild Phase 4 adds four autonomous intelligence loops to the Chief of Staff agent.
CoS transitions from reactive (chat only) to proactive (loops fire on schedule and push findings).

### Files created or modified

| File | Change |
|---|---|
| `domains/guild/agents/loops/__init__.py` | New — makes loops a Python package |
| `domains/guild/agents/loops/cos_job_search.py` | New — Loop A (career scout) |
| `domains/guild/agents/loops/cos_german_watch.py` | New — Loop B (German domain watch) |
| `domains/guild/agents/loops/cos_curator_watch.py` | New — Loop C (Curator topic scout) |
| `domains/guild/agents/loops/cos_novelty_watch.py` | New — Loop D (mini-moi novelty watch) |
| `domains/guild/agents/chief_of_staff.py` | Modified — APScheduler wired in, `/loops` endpoint added |
| `domains/guild/db/schema_phase4.sql` | New — 3 tables (see below) |
| `requirements.txt` | Added `tavily-python` |

### Database schema added (`schema_phase4.sql`)

```sql
jobs.career_opportunities   -- job results written by Loop A
guild.cos_agenda            -- all loops write recommendations here for Robert to review
guild.agent_feedback        -- learning signal when Robert acts on a recommendation
```
*Apply when Docker is back up:*
`psql "postgresql://minimoi:simple123@localhost:5432/personal_agents" -f domains/guild/db/schema_phase4.sql`

### Scheduler (APScheduler, running under launchd)

| Loop | File | Cadence | What it does |
|---|---|---|---|
| A | `cos_job_search.py` | Daily 06:00 + 18:00 | Career focus scout — Tavily + RSS, 2-pass LLM scoring, Telegram alerts |
| B | `cos_german_watch.py` | Sunday 09:00 | Practice cadence check + tool search → reminder + `guild.cos_agenda` |
| C | `cos_curator_watch.py` | Sunday 10:00 | Emerging topic scout → `guild.cos_agenda` only (never auto-adds threads) |
| D | `cos_novelty_watch.py` | 1st + 15th 08:00 | Competitive scan → `guild.cos_agenda` + Telegram for threat/incorporate |

### Status endpoints

```
GET http://localhost:8769/health    — liveness
GET http://localhost:8769/status    — agent state, chat count, uptime
GET http://localhost:8769/loops     — last_run, last_result, error per loop
```

---

## Loop A Detail — Career Focus Scout

The career focus loop was the primary build and went through several calibration cycles
during the session. Full pipeline:

```
run_career_focus_scout()
  ├── _search_tavily()       — dual-pass: days=2 (fresh) + days=14 (recent)
  ├── _fetch_rss()           — Indeed RSS feeds (feedparser, fail-graceful)
  ├── _deduplicate()         — skip URLs already in DB or file store
  ├── _apply_date_filter()   — stamp age_tier, discard old (>14d)
  ├── _filter_pass()         — LLM filter: score 0-10, discard <5
  │                            also drops: news articles, aggregator pages, stale/closed listings
  ├── _evaluate_pass()       — LLM quality eval: fit_score, fit_narrative, opportunity_type
  ├── _warm_lead_check()     — fuzzy match vs network_companies.json (2× score boost)
  ├── _write_results()       — DB first (jobs.career_opportunities), JSON fallback
  └── _notify_telegram()     — age-tiered Telegram alerts via rvsopenbot
```

### Age tiering (how Tavily date limitation was solved)

Tavily does not return per-result published dates in basic search.
The `days=` parameter filters Tavily's index by crawl date, not original post date.

**Solution: dual-pass search**
- Pass 1 — `days=2`: all results tagged `fresh`
- Pass 2 — `days=14`: URLs not in pass 1 tagged `recent`
- RSS entries classified by feedparser `published_parsed` date

**Telegram notification thresholds by tier:**

| Tier | Window | Ping threshold | Notes |
|---|---|---|---|
| 🆕 fresh | ≤ 48h | score ≥ 7.0 | Higher sensitivity — act fast on new postings |
| 📅 recent | 3–14d | score ≥ 9.0 | High bar — only exceptional roles |
| Cap | — | 5 pings/run | Fresh-first sort, then by score |

### Calibration fixes made during session review

**1. Article discrimination in filter pass**
*Problem:* First run returned 32 results including Deutsche Telekom news articles
about their AI factory expansion — relevant topic but not job listings.
*Fix:* Filter pass prompt now explicitly instructs the LLM:
- News articles about company expansion → score ≤ 3
- Aggregator pages listing many unrelated jobs → score ≤ 4
- Actual job listings scored normally on fit
*Result:* Shortlist dropped from 32 to 10–12. Noise eliminated.

**2. Staleness detection**
*Problem:* Pinterest "Principal Engineer, Agentic Engineering" appeared in fresh pass
(Tavily crawled it recently) but LinkedIn showed it was reposted 1 month ago and
"No longer accepting applications."
*Root cause:* LinkedIn reposts jobs and resets Tavily's crawl date. The `days=2`
window picks it up as fresh even though the actual posting is old.
*Fix:* Staleness rules added to filter pass prompt. Score forced to 0 if snippet contains:
- "no longer accepting", "position filled", "job closed", "expired"
- "reposted X months ago" / "reposted X weeks ago" (>2 weeks)
- Explicit posting date older than 14 days
- Any signal the role is closed
*Result:* Stale/closed listings dropped before the expensive eval pass.

**3. Warm lead contacts format fix**
*Problem:* `network_companies.json` stores contacts as plain strings.
Code assumed dicts with a `name` key — crashed on first run.
*Fix:* One-line isinstance check: `c if isinstance(c, str) else c.get("name", "")`

**4. sys.path fix for launchd**
*Problem:* Loop imports (`from domains.guild.agents.loops...`) failed under launchd
with "No module named 'domains'" because the script directory is
`domains/guild/agents/`, not the repo root.
*Fix:* `sys.path.insert(0, str(BASE_DIR))` added before loop imports in `main()`.

---

## Jobs Applied To

Robert applied to two roles during this session. Both were surfaced by Loop A.

### 1. Principal Engineer, Agentic AI — Comcast (Xfinity)
**URL:** https://jobs.comcast.com/job/philadelphia/principal-engineer-agentic-ai/45483/94521326256
**Loop A fit score:** 9.0 / 10
**Age tier at time of discovery:** fresh

**Why it scored 9.0:**
- Exact title match for Principal Engineer Agentic AI
- Comcast/Xfinity is a major US telecom — 30 years telecom background transfers directly
- Role is explicitly about building reusable agents, multi-agent orchestration, LLM-as-a-Judge
- Chicago metro presence increases hybrid/relocation flexibility
- T-Mobile contract end (Aug 3) aligns with hiring window

**Genuine gap identified:** No specific Google ADK or Gemini Enterprise background
(Comcast uses a Google-centric ecosystem).

**Application framing:** Lead with telecom + production agentic AI proof points.
Frame T-Mobile end date as clean availability Aug 1 2026. Mirror JD language:
"reusable agents", "agentic workflows", "LLM-as-a-Judge."

---

### 2. Principal Engineer, Enterprise Agentic AI Platform — NVIDIA IT
**Role:** JR2013809 · Enterprise AI & Automation team
**Status:** Closed March 2026 (applications no longer accepted)
**Loop A fit score:** 7.0 / 10 (scored manually during review, role was already closed)

**Why it scored 7.0:**
- Production agentic AI systems experience matches core mandate
- 30+ years distributed systems exceeds 15-year requirement
- "Builder profile" — writes code daily, not strategy-only — is a direct match
- Mentions Claude Code and Cursor as team tools

**Genuine gaps identified:**
- No explicit LangChain/LangGraph, Kubernetes, or NVIDIA GPU inference optimization in narrative
- Domain shift: NVIDIA wants enterprise IT domains (finance, HR, supply chain)
  vs Robert's telecom background
- Role closed before Aug 1 availability window

**Retained as benchmark:** NVIDIA $272K–$431K base + equity establishes a quality floor.
When NVIDIA posts similar roles (they will), Loop A should catch them fresh.

---

## Production State (end of session)

```
CoS service:   ✅ running port 8769 (launchd: com.user.cos)
Scheduler:     ✅ active — loop_a(6+18h) loop_b(Sun 9h) loop_c(Sun 10h) loop_d(1st+15th 8h)
First auto-run: Loop A at 18:00 today (2026-06-09)
DB:            ⚠️  Docker down — all loops use file fallback (data/guild/*.json)
Schema:        Pending — apply schema_phase4.sql when Docker is back up
Commits:       d9f3938, 2584b37, 8aaf87c (all on main)
```

---

## Open Items for OpenClaw / Next Session

1. **Schema apply** — when Docker is back up:
   `psql "postgresql://minimoi:simple123@localhost:5432/personal_agents" -f domains/guild/db/schema_phase4.sql`

2. **Loop A threshold calibration** — after 2–3 days of scheduled runs, review:
   - Are fresh ≥7.0 pings the right volume? (target: 0–3 per run)
   - Are any stale/closed listings still slipping through?
   - Consider tightening to ≥7.5 if too noisy, loosening to ≥6.5 if too quiet

3. **Loop B, C, D first runs** — Loop B and C fire Sunday 2026-06-15. Review `/loops`
   endpoint output after first scheduled fire and check `data/guild/cos_agenda.json`
   for quality of suggestions.

4. **CHANGELOG + roadmap update** — OpenClaw to update after Robert reviews this doc.

5. **Guild portal pages** — Robert noted: "I will start with Telegram but when we get
   HTML pages for Guild, we will build a chat interface there too." The `/chat` endpoint
   is already live at port 8769 — wiring it to a portal page is Phase 5 scope.

6. **Gespräche toggle** — German domain, deferred. Issue not filed.

---

*Guild Phase 4 · main · 2026-06-09 · Claude Code session review*
*Commits: d9f3938 → 2584b37 → 8aaf87c*
