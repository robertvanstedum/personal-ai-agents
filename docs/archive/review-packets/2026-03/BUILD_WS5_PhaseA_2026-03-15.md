# Build Record: Intelligence Layer — Phase A
**Mini-moi Personal AI Curator**
**Built:** March 15, 2026
**Sprint:** 1.0 — Workstream 5
**Status:** Complete and confirmed working

---

## What Was Planned

Phase A established the foundation of `curator_intelligence.py` — a new daily observation component that runs after the main briefing and produces a structured intelligence summary.

Two observations targeted for Phase A:

**1. Topic velocity**
Compare today's scored articles against a 30-day baseline. Identify topics gaining momentum and topics absent from today's briefing that appear in the interest profile.

**2. Discovery candidates**
Surface unknown domains from the probationary tier in `curator_sources.json` added today. Flag for manual review with a Haiku quality assessment.

---

## Pre-conditions Completed

Two refactors executed before building `curator_intelligence.py`:

**Pre-1: `send_telegram_alert` moved to `curator_utils.py`**
Function previously lived in `curator_rss_v2.py` (line 386). Moved to `curator_utils.py` (shared utility module) so `curator_intelligence.py` could import it cleanly without pulling in the entire briefing pipeline. `curator_rss_v2.py` updated to import from `curator_utils`.

**Pre-2: `added_date` and `query` fields added to probationary entries**
`_log_probationary_domains()` in `curator_priority_feed.py` previously wrote probationary entries without `added_date` or `query` fields, making it impossible to filter "new today" entries. Fixed to write both fields on new entries. One-time backfill applied to 8 existing probationary entries (all added 2026-03-14).

---

## What Was Built

### New file: `curator_intelligence.py`

Daily intelligence observation script. Reads from:
- Today's scored history (top 20 articles)
- 30-day rolling history for baseline comparison
- `curator_sources.json` probationary tier for discovery candidates
- User interest profile for gap detection

Produces:
- Telegram message (brief — 5 lines max)
- `intelligence_YYYYMMDD.json` stored in OpenClaw workspace

### New file: `run_intelligence_cron.sh`
Shell wrapper following the same pattern as `run_curator_cron.sh` and `run_priority_feed_cron.sh` — activates venv, runs script, logs result.

### New file: `com.vanstedum.curator-intelligence.plist`
Registered to `~/Library/LaunchAgents/`. Fires at 7:30AM daily — after the 7AM briefing run, before the 2PM priority feed run. Logs to `logs/intelligence_launchd.log` and `logs/intelligence_launchd_error.log`.

---

## Confirmed Working Output (March 15, 2026)

```
🧠 Intelligence — Mar 15

📈 Momentum: Iran military tensions (Trump ultimatums, troop deployments),
 tariff rulings, AI capability advances
⚠️ Gap: Crypto/Bitcoin markets, detailed Fed monetary policy
🔍 Sources: No new sources discovered today.
```

**Notes on output:**
- Momentum call accurate — Iran/energy dominated today's top 20
- Gap detection working — crypto and detailed Fed policy genuinely absent from today's briefing
- Discovery candidates correctly returning "none" — Brave rate limiting reduced new domain surfacing today. Will improve once `time.sleep(1)` delay fix is confirmed in production
- Format is tight and readable

---

## Design Decisions Made During Build

**Gap detection scope:** The system flags gaps against the interest profile. Crypto/Bitcoin surfaced as a gap. However, the actual interest is specific — crypto/gold correlation and crypto as speculative vs uncertainty-hedge asset, not generic crypto coverage. This is a prompt tuning opportunity for Phase B or post-1.0 — gap detection should eventually match against thesis-level interests, not just topic keywords.

**Telegram delivery:** Intelligence message sent as a separate Telegram message, not appended to the daily briefing. Keeps the morning read clean. Intelligence is a separate register — advisory, not news.

**Storage format:** `intelligence_YYYYMMDD.json` in OpenClaw workspace alongside other workspace files. Not in repo — operational data, not source code.

**launchd timing:** 7:30AM — 30 minutes after briefing fires, well before priority feed at 2PM. Gives the briefing time to complete before intelligence reads from it.

---

## Cost

- Haiku calls for gap analysis and discovery assessment: ~$0.01–0.02/day (confirmed actual; design estimate was $0.02–0.05)
- No Sonnet or Grok calls in Phase A — observation only, no reasoning layer yet
- Phase A adds negligible cost to daily operation

---

## Open Items Carried to Phase B

- `time.sleep(1)` delay between Brave queries — rate limiting fix, small change
- Gap detection prompt refinement — thesis-level matching vs keyword matching (post-1.0 or Phase B)
- Treasury MSPD appearing 3x in top 20 — per-source cap of 2 worth considering (separate from intelligence layer, flag for WS review)

---

## Phase B Scope (Next)

Three remaining observation types:

**3. Source anomalies (Haiku)**
Review trusted sources' last 10 articles against historical profile. Flag drift.

**4. US press blind spots (cross-source)**
Stories with high velocity in non-US sources (Al Jazeera, DW, Spiegel, O Globo) with low or zero US outlet coverage. The gap is the signal.

**5. Weekly lateral connections (Sonnet)**
Reason across recent reading history and signal profile. Surface adjacent topics, second-order implications, suggested sources. Weekly cadence.

Delivery: same Telegram message and JSON storage as Phase A. Intelligence message grows as observations are added.

---

*Phase A confirmed: March 15, 2026*
*Authored by: Claude Code + OpenClaw (validation)*
