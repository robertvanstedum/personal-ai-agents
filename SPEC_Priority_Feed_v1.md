# Feature Spec: Priority Feed (Tier 2 — Web Search Layer)
**Mini-moi Personal AI Curator**
**Prepared:** March 13, 2026
**Status:** Ready for OpenClaw evaluation
**Sprint:** 1.0 — Week 1

---

## Context

This spec describes an enhancement to the existing Signal Priorities feature and introduces Tier 2 (web search) as a distinct source layer in the curator architecture.

The curator now has three tiers:
- **Tier 1 — Daily Briefing:** RSS pool → scored → top 20 → Telegram 7AM (existing)
- **Tier 2 — Priority Feed:** Web search on active priority topics → topic-focused article list → web UI only (this spec)
- **Tier 3 — Deep Dive:** Scholarly + historical repositories → structured bibliography (separate spec, Week 2)

---

## Problem Statement

Signal Priorities currently do two things: inject a scoring boost into the daily briefing for matching articles, and provide an expiry-based active/inactive lifecycle. What they do not do is generate their own focused feed.

A user watching the Tigray conflict wants more than a boost to the daily 20. They want a dedicated thread of coverage on that topic — available on demand, accumulating over the life of the priority, sourced from beyond the RSS subscription pool.

---

## Design

### Existing behavior — preserve unchanged

Active priorities inject a scoring boost into the daily briefing pipeline. Articles matching priority keywords score higher and compete more strongly for the 20 daily slots. This remains exactly as-is.

### New behavior — Priority Feed

Each active priority generates its own article list, independent of the daily briefing:

- **Source:** Web search on priority keywords — not the RSS pool
- **Display:** Priority page (existing web UI), as an expandable article list beneath each active priority card
- **Delivery:** Web UI only — no Telegram push. User goes there deliberately.
- **Schedule:** Runs once daily on its own schedule, separate from the 7AM briefing run
- **Accumulation:** Results accumulate over the life of the priority. A 7-day priority builds a 7-day thread of web coverage on that topic.
- **Dedup:** Within the priority's own list — same URL never appears twice across daily runs
- **Refresh:** Manual refresh button on each priority card triggers an on-demand pull using the same web search logic
- **Expiry:** When a priority expires or is deleted, the feed stops updating. Accumulated history remains visible until the priority is cleared.

---

## Web Search Design

### Query construction
Priority keywords are used directly as search terms. Example: Tigray priority with keywords `[Tigray, Ethiopia, TPLF]` generates searches on those terms — individually or combined, TBD during implementation based on result quality.

### Domain whitelist
Web search is constrained to a trusted domain list. Initial seed (expand over time):

```
reuters.com
ft.com
economist.com
foreignpolicy.com
foreignaffairs.com
project-syndicate.org
politico.com
theguardian.com
nytimes.com
bloomberg.com
warontherocks.com
justsecurity.org
crisisgroup.org
acleddata.com
```

No SEO aggregators, no content farms. If a result URL is not in the whitelist, it is discarded before scoring.

### Pre-filtering
Haiku pre-filters web search results before they enter scoring:
- Removes results that are paywalled landing pages with no readable content
- Removes results that are clearly off-topic despite keyword match
- Keeps results that are substantive articles directly relevant to the priority topic

### Scoring
Surviving results are scored by Grok against the user profile — same scoring logic as the daily briefing. Score is stored with each article in the priority feed.

### Result count
Target: 5–10 new articles per daily run per active priority. Bounded to control cost. If fewer than 5 relevant results survive filtering, that is acceptable — quality over count.

---

## Data Model

### New fields on priority object (in existing priority store)

```json
{
  "id": "tigray-001",
  "topic": "Tigray Conflict",
  "keywords": ["Tigray", "Ethiopia", "TPLF"],
  "boost": 2.0,
  "status": "active",
  "created_at": "2026-02-25",
  "expires_at": "2026-03-04",
  "feed": [
    {
      "url": "https://example.com/article",
      "title": "Article title",
      "source": "reuters.com",
      "fetched_at": "2026-03-13T14:00:00Z",
      "score": 7.4,
      "summary": "One sentence Haiku summary"
    }
  ],
  "feed_last_updated": "2026-03-13T14:00:00Z"
}
```

Feed array grows daily. Dedup by URL before appending.

---

## UI Changes (Priority Page)

### Current priority card
Shows: topic name, boost multiplier, status badge, keyword tags, match count, created/expires dates, +3d/+7d/Activate/Edit/Delete controls.

### Enhanced priority card
Adds beneath existing controls:
- **Feed section** — list of accumulated articles, most recent first
- Each article shows: title (linked), source domain, date fetched, score
- **Refresh button** — triggers on-demand web search pull, appends new results
- **Last updated** timestamp

Feed section is collapsed by default if article count > 10, expandable.

---

## Scheduler

New daily job: `curator_priority_feed.py`
- Runs once daily, time TBD during implementation (suggest early afternoon, separate from 7AM briefing)
- Iterates active priorities only
- For each active priority: runs web search → Haiku pre-filter → Grok scoring → appends to feed → dedup → saves
- Registered as a separate launchd plist on MacBook (and later Mac Mini)
- Logs run time, result count per priority, cost

---

## Cost Model

Per active priority per day:
- Web search API calls: bounded query set, low cost
- Haiku pre-filter: bulk filtering, ~$0.01–0.02 per priority per day
- Grok scoring: 5–10 articles, ~$0.02–0.05 per priority per day

Estimated total: ~$0.03–0.07 per active priority per day. With 2–3 active priorities typical: $0.06–0.21/day added cost. Acceptable within budget.

---

## Out of Scope (this spec)

- Telegram delivery of priority feed results — not planned, web UI only
- RSS pool as source for priority feed — daily briefing boost covers this
- Priority feed cross-referencing with Deep Dive — future
- Export of accumulated priority feed as bibliography — future (Investigation Workspace 1.1)
- Notification when priority feed updates — future

---

## Open Questions for OpenClaw

1. **Priority store location** — where do priorities currently live? JSON file, in-memory, or database? Confirm schema before extending.
2. **Web search API** — which search API is currently available in the stack? Confirm before designing query construction.
3. **Haiku pre-filter** — is a reusable pre-filter function already in the codebase from X bookmark processing, or does this need to be built fresh?
4. **launchd plist naming convention** — confirm existing naming pattern before adding new plist.
5. **Flask routes** — confirm existing priority page routes before adding feed display logic.

---

## Success Criteria

- Active priority generates a feed of 5–10 scored articles per day from web search
- Feed accumulates correctly over the priority's active window with no duplicates
- Refresh button triggers immediate pull and updates the UI
- Daily run completes without affecting 7AM briefing schedule or cost
- Domain whitelist correctly filters out non-whitelisted sources
- All results pass Haiku pre-filter before scoring

---

*For evaluation by OpenClaw against CLAUDE.md, PROJECT_STATE.md, and local implementation context.*
*Implementation by Claude Code after OpenClaw confirms schema and integration points.*
