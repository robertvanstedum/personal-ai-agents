# Build Package: Priority Feed (Tier 2 — Web Search Layer)
**Mini-moi Personal AI Curator**
**Prepared:** March 13, 2026
**For:** Claude Code
**Confirmed by:** OpenClaw evaluation
**Build discipline:** One step at a time. Complete and confirm each step before proceeding to the next. Do not jump ahead.

---

## What We Are Building

An enhancement to the existing Signal Priorities feature. Each active priority will generate its own focused article list via web search — independent of the daily briefing, displayed in the web UI only, accumulating over the life of the priority.

Full design rationale in: `SPEC_Priority_Feed_v1.md`

---

## Confirmed Stack Context

| Item | Detail |
|------|--------|
| Priority store | `~/.openclaw/workspace/priorities.json` |
| Priority schema | `id`, `label`, `keywords`, `boost`, `created_at`, `expires_at`, `active`, `match_count` |
| Haiku pre-filter | `score_entries_haiku_prefilter()` in `curator_rss_v2.py` line 673 — use as-is |
| Search API | Brave Search API — OpenClaw has key, not yet in stack |
| launchd convention | `com.vanstedum.curator.plist` pattern |
| Flask priority routes | `/api/priority` (POST), `/api/priorities` (GET), UI at `/curator_priorities.html` |

---

## Build Steps

### Step 1 — Brave Search API: credential + test
**Status: BUILD NOW**

Add Brave Search API to the credential manager and verify it returns clean results before anything else is built around it.

Tasks:
- Add Brave Search API key to macOS keychain using existing credential manager pattern
- Write a minimal test function: query `"Tigray Ethiopia conflict"`, print top 5 results (title, URL, snippet)
- Confirm results are clean structured JSON, URLs are real articles, domain filtering is possible on the response
- Confirm free tier limit (2,000 queries/month) is sufficient for planned usage (~30-60 queries/day across 2-3 active priorities)

**Stop here. Confirm with Robert before proceeding to Step 2.**

---

### Step 2 — Extend priorities.json schema
**Status: DO NOT START — wait for Step 1 confirmation**

Extend the existing priority object schema to support the feed array.

New fields to add to each priority object:
```json
{
  "feed": [],
  "feed_last_updated": null
}
```

Tasks:
- Write a migration script that reads `priorities.json`, adds `feed: []` and `feed_last_updated: null` to any priority object missing these fields, writes back
- Run migration, verify file integrity
- No existing fields modified or removed

**Stop here. Confirm with Robert before proceeding to Step 3.**

---

### Step 3 — Build curator_priority_feed.py
**Status: DO NOT START — wait for Step 2 confirmation**

New script that runs the web search pipeline for all active priorities.

Logic:
1. Load `priorities.json`, filter to active priorities only
2. For each active priority:
   a. Construct search queries from priority keywords
   b. Call Brave Search API with domain whitelist filter
   c. Pass results to `score_entries_haiku_prefilter()` from `curator_rss_v2.py`
   d. Score surviving results with Grok (same scoring logic as daily briefing)
   e. Dedup against existing `feed[]` array by URL
   f. Append new results to `feed[]`, update `feed_last_updated`
3. Save updated `priorities.json`
4. Log run: timestamp, results per priority, estimated cost

Domain whitelist (initial):
```
reuters.com, ft.com, economist.com, foreignpolicy.com, foreignaffairs.com,
project-syndicate.org, politico.com, theguardian.com, nytimes.com,
bloomberg.com, warontherocks.com, justsecurity.org, crisisgroup.org, acleddata.com
```

Target: 5–10 new articles per active priority per run.

**Stop here. Confirm with Robert before proceeding to Step 4.**

---

### Step 4 — Flask: feed display + refresh endpoint
**Status: DO NOT START — wait for Step 3 confirmation**

Add two new Flask routes alongside existing priority routes:

- `GET /api/priority/<id>/feed` — returns the feed array for a specific priority
- `POST /api/priority/<id>/refresh` — triggers an immediate web search run for that priority only, same logic as Step 3 but single-priority scope

No changes to existing `/api/priority` or `/api/priorities` routes.

**Stop here. Confirm with Robert before proceeding to Step 5.**

---

### Step 5 — UI: feed list + refresh button on priority card
**Status: DO NOT START — wait for Step 4 confirmation**

Update `curator_priorities.html` to display the feed beneath each active priority card.

UI additions per priority card:
- Feed section showing accumulated articles, most recent first
- Each article: title (linked), source domain, date fetched, score
- Collapsed by default if article count > 10, expandable
- "Refresh" button — calls `POST /api/priority/<id>/refresh`, updates feed display on response
- "Last updated" timestamp

No changes to existing priority card controls (activate/expire/edit/delete).

**Stop here. Confirm with Robert before proceeding to Step 6.**

---

### Step 6 — launchd: register daily scheduler
**Status: DO NOT START — wait for Step 5 confirmation**

Register `curator_priority_feed.py` as a daily launchd job.

- Plist name: `com.vanstedum.curator-priority-feed.plist`
- Run time: TBD — suggest 14:00 daily, well separated from 7AM briefing
- Follow existing plist pattern from `com.vanstedum.curator.plist`
- Test: verify job fires, completes, logs correctly
- Verify no interference with existing 7AM briefing job

**Step 6 complete = feature complete. Final confirmation with Robert.**

---

## Confirmation Protocol

After each step:
- Claude Code states what was built and what was tested
- Claude Code explicitly states: **"Step N complete — ready for Robert's confirmation"**
- Robert confirms before next step begins
- If anything unexpected surfaces during a step, stop and flag before continuing

## Do Not

- Build multiple steps in one session without confirmation between them
- Modify existing files beyond what is specified for each step
- Change existing priority routes, daily briefing pipeline, or Haiku pre-filter function
- Add Telegram delivery — web UI only for this feature

---

*Full design context in SPEC_Priority_Feed_v1.md*
*OpenClaw evaluation confirmed March 13, 2026*

---

## Post-Step-6 Items (Tomorrow)

### Bug: Chicago Crime query quality
- 1 result returned, `chicagobusiness.com` not on whitelist — audit domain filter logic
- Keywords "loop", "economic impact" too indirect for news search — investigate query construction
- Add freshness/date filter to Brave API calls to exclude stale articles
- Chicago Crime priority expires Mar 14 — extend before retesting

### Decision: Feedback buttons on priority feed articles
**Save only — no Like/Dislike.**

Rationale: Priority feed is a focused research tool. Like/Dislike on priority articles would over-inflate weights on topics already explicitly tracked via the priority itself, distorting the daily briefing signal. Save to library only — same `/feedback` endpoint, `action=save`.

Implementation: Add 🔖 Save button to each `articleRowHtml()` row in `curator_priorities.html`. No new backend routes needed.
