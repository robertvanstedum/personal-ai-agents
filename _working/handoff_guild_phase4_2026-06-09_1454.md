# Handoff — Guild Phase 4 (Domain Intelligence Loops)
*mini-moi · personal-ai-agents · Guild domain*

- **Authored:** 2026-06-09 14:54 CDT (19:54 UTC) — Claude.ai
- **Status:** READY TO BUILD — Tavily key stored, cos_context.json live, CoS running
- **Branch:** `main` directly
- **Reference:** `docs/GUILD_AGENTS_DESIGN.md` Section 5c

---

## Prerequisites confirmed

- [x] `cos_context.json` — career_focus config live with roles, geos, deadline Aug 1
- [x] Tavily API key — stored as `keyring.get_password("tavily", "api_key")`
- [x] CoS Flask service running on port 8769
- [x] `jobs.career_opportunities` table exists (Phase 1)
- [x] `guild.agent_feedback` table exists (Phase 1)
- [x] `domains/guild/agents/loops/` directory exists

---

## Build order

Loop A first — highest priority, Aug 1 deadline.
Loops B, C, D can build in parallel after A is tested.
Feedback tracking wires into all loops simultaneously.

---

## Loop A — Career Focus Scout (`cos_job_search.py`)

**Cadence:** twice daily (06:00 and 18:00 local)
**File:** `domains/guild/agents/loops/cos_job_search.py`

### Search mechanism

Two sources run in parallel:

**Source 1 — Tavily web search:**
```python
from tavily import TavilyClient
client = TavilyClient(api_key=keyring.get_password("tavily", "api_key"))

def search_tavily(query, max_results=10):
    return client.search(query, max_results=max_results, search_depth="basic")
```

**Search queries to run (from cos_context.json):**
Build queries dynamically from context:
- `"{role}" Chicago` for each role in `target_roles` — one query per role
- `"{role}" remote` for remote pass
- `"agentic AI" site:careers.telekom.com` for Deutsche Telekom direct
- `"telecom AI" {market}` for each market in `international_telecom.target_markets`

Keep total queries under 10 per run to manage cost. Prioritise by geo_priority order.

**Source 2 — RSS feeds:**
```python
import feedparser

RSS_SOURCES = {
    "indeed_tpm": "https://www.indeed.com/rss?q=technical+product+manager+AI&l=Chicago",
    "indeed_ai":  "https://www.indeed.com/rss?q=principal+engineer+agentic+AI&l=Chicago",
    "indeed_remote": "https://www.indeed.com/rss?q=principal+engineer+AI&remotejob=032b3046-06a3-4876-8dfd-474eb5e7ed11",
}

def fetch_rss(url):
    feed = feedparser.parse(url)
    return [{"title": e.title, "url": e.link, "company": e.get("source", {}).get("title",""),
             "date": e.get("published","")} for e in feed.entries[:20]]
```

### Scoring pipeline

**Step 1 — Haiku filter** (cheap, runs on all raw results):
```
Prompt: "Score this job opportunity 0-10 for fit with this candidate profile.
Return JSON only: {"score": N, "reason": "one line"}
Profile: {narrative from cos_context.json}
Opportunity: {title} at {company}, {geo}
Description: {snippet}"
```
Discard anything scoring below 5.

**Step 2 — Sonnet evaluation** (quality, runs on shortlist score ≥ 5):
```
Prompt: "Evaluate this opportunity for Robert van Stedum.
Return JSON: {"fit_score": N, "fit_narrative": "2-3 sentences",
              "opportunity_type": "employment|contract|advisory",
              "warm_lead": false, "cos_notes": "any flags"}
Context: {full career_focus section from cos_context.json}
Opportunity: {full details}"
```

**Step 3 — Warm lead check:**
If `domains/guild/data/network_companies.json` exists, fuzzy-match
`opportunity.company` against the list. If match found:
- Set `warm_lead = True`
- Set `warm_lead_contacts` to matching contact names
- Multiply fit_score by `career_focus.network.warm_lead_score_boost` (2.0)

**Step 4 — Write to DB:**
```python
# Insert into jobs.career_opportunities
# status = 'suggested', created_by = 'cos_loop_a'
# model_used = whichever model ran the evaluation
```

**Step 5 — Telegram notification:**
If any opportunity scores ≥ 8.0 (or warm_lead + score ≥ 7.0):
Send to Rvsopenbot with format:
```
🎯 Career opportunity found:
{title} — {company}
{geo} · {opportunity_type}
Score: {fit_score}/10
{fit_narrative}
{"⭐ Warm lead — contact: " + warm_lead_contacts if warm_lead else ""}
View in portal → {url}
```

**Do not ping for every result.** Only ping above threshold. Batch lower-scoring items
for the CoS morning brief.

### Deduplication

Before inserting, check if `url` already exists in `jobs.career_opportunities`.
Skip duplicates. Same role at same company within 7 days = duplicate.

---

## Loop B — German Domain Watch (`cos_german_watch.py`)

**Cadence:** weekly (Sunday 09:00)
**File:** `domains/guild/agents/loops/cos_german_watch.py`

### Practice cadence check

```python
# Read last session date from German domain data
# Path: domains/german/data/sessions/ — find most recent session file
# If days_since_last_session > cos_context["german"]["remind_after_days"]:
#   Send Telegram reminder to Rvsopenbot
```

Reminder format:
```
🇩🇪 German practice reminder
{days_since} days since your last session.
Your target: {practice_target_sessions_per_week} sessions/week.
```

### Tool and community search

Run three Tavily searches:
1. `"German language learning AI tools 2026"` — emerging tools
2. `"German conversation groups Chicago"` — local community
3. `"online German language exchange 2026"` — international

**Sonnet evaluation of tools found:**
```
"Compare this language learning tool against Mein Deutsch.
Mein Deutsch features: [list key features from domains/german/].
Tool found: {tool name and description}
Return JSON: {"assessment": "threat|complement|incorporate|not_relevant",
              "key_difference": "one sentence", "recommendation": "one sentence"}"
```

Write findings to `guild.cos_agenda` (domain: "german", status: "open").
Surface anything rated "incorporate" as a Telegram recommendation.

---

## Loop C — Curator Domain Scout (`cos_curator_watch.py`)

**Cadence:** weekly (Sunday 10:00)
**File:** `domains/guild/agents/loops/cos_curator_watch.py`

### Emerging topic search

Read `cos_context["curator"]["scout_for"]` — run one Tavily search per phrase.

**Sonnet evaluation:**
```
"Is this an emerging topic worth tracking for someone who monitors:
{current Curator threads from Desk data}
Topic found: {title and description}
Return JSON: {"worth_tracking": true/false, "reason": "one sentence",
              "suggested_thread_name": "slug-style-name"}"
```

**Write to Desk "Suggested by CoS" queue:**
For each `worth_tracking = true`:
```python
# Insert into guild.cos_agenda:
# loop_name = "cos_curator_watch"
# domain = "curator"
# description = suggested_thread_name + ": " + reason
# status = "open" (Robert confirms to activate — never auto-add)
```

**Never auto-add a Curator thread.** Robert confirms in portal.

---

## Loop D — mini-moi Novelty Watch (`cos_novelty_watch.py`)

**Cadence:** bi-weekly (1st and 15th of month, 08:00)
**File:** `domains/guild/agents/loops/cos_novelty_watch.py`

### Competitive scan

For each term in `cos_context["mini_moi"]["watch_terms"]`:
Run Tavily search. Filter results to last 30 days where possible.

**Sonnet evaluation (run once on combined results, not per-result):**
```
"Robert van Stedum has built mini-moi: a personal, local-first, model-agnostic
AI agent platform with Curator (daily intelligence briefing), Mein Deutsch
(German coaching), Research Intelligence (threaded deep research), and Guild
(autonomous agents: Chief of Staff + Operations).

Here are recent results for competitive/related platforms:
{results}

For each relevant result, return JSON array:
[{"name": "...", "url": "...",
  "assessment": "threat|complement|incorporate|not_relevant",
  "key_difference": "one sentence",
  "recommendation": "one sentence"}]
Only include results that are genuinely relevant — not_relevant items can be omitted."
```

Write findings to `guild.cos_agenda`. Send Telegram for anything rated "threat" or "incorporate."

Telegram format for significant finds:
```
🔍 mini-moi novelty watch:
Found: {name}
Assessment: {assessment}
{key_difference}
Recommendation: {recommendation}
{url}
```

---

## Feedback tracking (wire into ALL loops)

Every recommendation written to `jobs.career_opportunities` or `guild.cos_agenda`
gets a corresponding row in `guild.agent_feedback` when Robert acts on it.

**In the portal** — when Robert clicks through from a recommendation, confirms, defers,
or dismisses, write to `guild.agent_feedback`:
```python
{
  "agent_name": "cos",
  "recommendation_id": id,
  "signal_type": "confirmed" | "dismissed" | "deferred" | "click_through",
  "domain": "career_focus" | "german" | "curator" | "mini_moi",
  "item_type": "job_opportunity" | "tool_found" | "topic_suggested" | "competitive_find"
}
```

This is the learning signal. Future phases use it to calibrate loop scoring thresholds.

---

## Wire loops into `chief_of_staff.py`

Add a scheduler to the CoS Flask service. Use APScheduler if installed,
otherwise threading.Timer pattern matching Operations:

```python
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()

# Loop A — twice daily
scheduler.add_job(run_career_focus_scout, 'cron', hour='6,18', id='loop_a')

# Loop B — weekly Sunday 09:00
scheduler.add_job(run_german_watch, 'cron', day_of_week='sun', hour=9, id='loop_b')

# Loop C — weekly Sunday 10:00
scheduler.add_job(run_curator_scout, 'cron', day_of_week='sun', hour=10, id='loop_c')

# Loop D — bi-weekly 1st and 15th 08:00
scheduler.add_job(run_novelty_watch, 'cron', day='1,15', hour=8, id='loop_d')

scheduler.start()
```

Add to `requirements.txt` if not already there: `apscheduler`, `tavily-python`, `feedparser`

---

## Definition of done

- [ ] `cos_job_search.py` exists, runs without error, writes ≥1 result to `jobs.career_opportunities`
- [ ] Loop A Telegram ping fires for any result scoring ≥ 8.0
- [ ] `cos_german_watch.py` exists, runs, checks practice cadence
- [ ] `cos_curator_watch.py` exists, runs, writes suggestions to `guild.cos_agenda`
- [ ] `cos_novelty_watch.py` exists, runs, Sonnet evaluation returns valid JSON
- [ ] All four loops wired into `chief_of_staff.py` scheduler
- [ ] `guild.agent_feedback` receives an entry when a recommendation is acted on in portal
- [ ] `python3 tools/health_check.py` passes — existing services unaffected
- [ ] Robert visual sign-off on first Loop A Telegram ping
- [ ] Committed and pushed to main

---

## Run Loop A once manually to test before scheduling

Before wiring the scheduler, run Loop A once manually to confirm results:

```bash
python3 -c "
from domains.guild.agents.loops.cos_job_search import run_career_focus_scout
run_career_focus_scout()
print('Done — check jobs.career_opportunities table')
"
```

Then check the DB:
```sql
SELECT title, company, geo, fit_score, warm_lead, status
FROM jobs.career_opportunities
ORDER BY fit_score DESC LIMIT 10;
```

Robert reviews the first batch before the scheduler goes live.
**Robert signs off on first batch quality before scheduler is activated.**

---

## Commit

```
git add domains/guild/agents/loops/ domains/guild/agents/chief_of_staff.py requirements.txt
git commit -m "phase4: CoS intelligence loops — career_focus scout, German watch, Curator scout, novelty watch"
git push origin main
```

---

*Guild Phase 4 · main · 2026-06-09 14:54 CDT*
