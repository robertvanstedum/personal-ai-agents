# Workstream 5 — Intelligence Layer

Design Spec: curator_intelligence.py

Date: 2026-03-14 | Author: Robert & Claude | Status: APPROVED — Phase A built March 15, 2026 (see BUILD_WS5_PHASE_A.md)

---

## Problem & Intent

The curator is a well-engineered scoring pipeline with a learned preference profile. The LLM is used reactively — score this, filter that. It does not proactively reason about what you *should* be seeing. WS5 closes that gap: a daily observation layer that monitors the pipeline's own output, flags anomalies, surfaces blind spots, and (in 1.1) acts on its findings.

**Philosophy:** Generate intelligence now. Actions come in 1.1. You decide what to do with each observation. The system's job is to notice things you would have missed.

---

## New Files

| File | Purpose |
|---|---|
| curator_intelligence.py | Main runner — reads pipeline output, generates observations, sends Telegram |
| run_intelligence_cron.sh | Shell wrapper (mirrors run_curator_cron.sh) |
| com.vanstedum.curator-intelligence.plist | launchd agent, fires 7:30 AM daily |
| intelligence/intelligence_YYYYMMDD.json | Daily observation output (one file per run) |
| intelligence/intelligence_state.json | Rolling 30-day baseline accumulator |

**Modified:** `curator_rss_v2.py` — add `curator_latest.json` write at end of main() (machine-readable scored pool; ~200KB/day)

---

## Prerequisite: curator_latest.json

`curator_output.txt` and HTML are fragile to parse. Intelligence needs structured access to the full scored pool.

One-liner addition to `curator_rss_v2.py` main():

```python
with open('curator_latest.json', 'w') as f:
    json.dump(results, f, indent=2, default=str)
```

Contains all scored entries (not just top 20) with fields: hash_id, title, source, link, summary, category, score, raw_score, final_score, method, **source_type** (rss/web_search/x_bookmark), **query_label**, trust_tier, matched_priorities, published.

---

## Five Observation Types

| # | Name | Model | Frequency | Est. cost |
|---|---|---|---|---|
| 1 | Topic Velocity | Haiku | Daily | ~$0.005 |
| 2 | Source Anomalies | Haiku (on flag only) | Daily | ~$0.005–0.015 |
| 3 | Discovery Candidates | Haiku | Daily (≤5/day) | ~$0.010–0.025 |
| 4 | Lateral Connections | Sonnet | Weekly (Thu) | ~$0.05–0.10/week |
| 5 | US Press Blind Spots | Haiku (on flag only) | Daily (opportunistic) | ~$0.005 |

### 1. Topic Velocity (Haiku, daily)

Count articles per category in today's full scored pool. Compare against 30-day rolling avg in `intelligence_state.json`. Flag: any category ±60% vs baseline; any active interest with 0 articles. One Haiku call to narrate findings.

Output:
```json
{"type":"topic_velocity","priority":"high",
 "observation":"Tariff retaliation +80% vs baseline; Iran interest at zero today.",
 "details":{"category_counts":{...},"zero_coverage_interests":["iran sanctions"]}}
```

### 2. Source Anomalies (Haiku on flag, daily)

For each trusted domain in `curator_sources.json`: count today's articles vs 30-day baseline. Flag if trusted source at 0 when baseline >1.5/day. Skip if baseline sparse (<7 days). Haiku only invoked when flag raised — pure arithmetic otherwise. **Exclude source_type='web_search' entries** (queries, not feeds — 0-count days are expected).

### 3. Discovery Candidates (Haiku, daily, ≤5/day)

Find probationary entries in `curator_sources.json` with no `haiku_evaluated` flag. Send domain + query_label to Haiku for credible/noise/unknown verdict. Write result back to the JSON entry:

```json
{"domain":"streetwisejournal.com","trust":"probationary","set_by":"auto",
 "haiku_evaluated":true,"haiku_verdict":"noise",
 "haiku_note":"Aggregator, no original reporting","haiku_evaluated_at":"2026-03-15"}
```

### 4. Lateral Connections (Sonnet, Thursday only)

Thursday guard in code. Collects: liked[]/saved[] titles from `curator_preferences.json` (last 30 days) + active priority labels + weekly topic summary. Single Sonnet prompt. Output: 2–3 suggested topics with example sources and one-line rationale. Sent as a **separate Telegram message**, not included in daily 5-line summary.

Example:
```
"You track Iran sanctions heavily. India is now Iran's largest crude buyer.
Suggested: S&P Global Commodity Insights, The Hindu Business Line."
```

### 5. US Press Blind Spots (Haiku on flag, daily)

Tag each article in scored pool as US or non-US (hardcoded sets). Find articles scoring >6.0 from non-US sources where no US source covered the same story (keyword similarity check, no LLM). If ≥2 qualifying articles found: invoke Haiku to summarize. Otherwise silent.

---

## intelligence_state.json — Rolling 30-Day Baseline

```json
{
    "last_updated": "2026-03-14",
    "topic_baseline": {
        "geo_major":  {"days":14,"total":168,"daily_avg":12.0,"last_7d":[14,11,13,12,15,10,11]},
        "monetary":   {"days":14,"total":72, "daily_avg":5.1, "last_7d":[6,5,4,5,6,5,4]},
        "geo_other":  {"days":14,"total":84, "daily_avg":6.0, "last_7d":[7,6,6,6,5,7,6]}
    },
    "source_baseline": {
        "reuters.com":        {"days":14,"total_articles":42,"daily_avg":3.0},
        "warontherocks.com":  {"days":14,"total_articles":32,"daily_avg":2.3}
    }
}
```

Update: append today's count to last_7d (drop oldest), increment days/total, recalculate daily_avg. Cap at 30 days. Stored at `~/.openclaw/workspace/intelligence_state.json` (private, not in repo).

---

## intelligence_YYYYMMDD.json — Daily Output Schema

```json
{
    "date": "2026-03-15",
    "run_type": "daily",
    "observations": [
        {"type":"topic_velocity|source_anomaly|discovery_candidate|lateral_connection|blind_spot",
         "priority":"high|medium|low",
         "observation":"Plain English, 1-2 sentences",
         "details":{},
         "model":"haiku|sonnet|none",
         "cost_usd":0.012,
         "timestamp":"2026-03-15T07:30:00Z"}
    ],
    "telegram_sent":true,"total_cost_usd":0.034,
    "run_duration_s":52,"pool_size":882
}
```

---

## Telegram Delivery

**Daily message** (5 lines max, 7:30 AM — 30 min after morning briefing):
```
🧠 Intelligence | Mar 15

📈 Tariff retaliation +80% vs baseline; Iran interest at zero today
⚠️ War on the Rocks quiet (0 articles, norm 2.3/day)
🔍 3 domains evaluated: 0 credible, 3 noise
👁 Blind spot: Bamako summit in non-US press, absent from US wire
```

**Weekly message** (Thursday, separate):
```
🔗 Lateral Connections | Week of Mar 10

→ India crude workarounds (Iran sanctions adjacency) — S&P Global, Hindu Business Line
→ ECB balance sheet normalization — ties to your monetary policy tracking
→ Türkiye–Gulf creditor negotiations — ACLED + FT coverage gap
```

*Uses existing `send_telegram_alert()` from `curator_utils.py` (moved from curator_rss_v2.py as pre-condition). Chat ID from TELEGRAM_CHAT_ID env var.*

---

## launchd — 7:30 AM Daily

```
com.vanstedum.curator-intelligence.plist
StartCalendarInterval: Hour=7, Minute=30
Logs: logs/intelligence_launchd.log + logs/intelligence_launchd_error.log
Shell wrapper: run_intelligence_cron.sh  (mirrors run_curator_cron.sh pattern)
```

---

## Build Sequence

Phase A (complete — March 15):
1. ~~Move send_telegram_alert → curator_utils.py~~
2. ~~Fix _log_probationary_domains() — add added_date + query fields, backfill 8 entries~~
3. ~~Scaffold curator_intelligence.py — Topic Velocity + Discovery Candidates~~
4. ~~launchd plist + run_intelligence_cron.sh~~

Phase B (next):
5. Add curator_latest.json write to curator_rss_v2.py main()
6. Implement intelligence_state.json rolling baseline updater
7. Source anomaly detection (arithmetic + Haiku on flag)
8. US press blind spot detection (keyword similarity + Haiku on flag)
9. Weekly lateral connections (Sonnet, Thursday guard)

---

## Open Questions — Answered Before Phase A

**Q1 — curator_latest.json:** Deferred to Phase B. Phase A reads curator_history.json instead; topic inference via Haiku from titles at runtime.

**Q2 — Source anomaly threshold:** A first (0 articles when baseline >1.5/day). B in 1.1.

**Q3 — Discovery candidate cap:** 5/day to control cost. ✅

**Q4 — Lateral connections history:** Option A — liked[]/saved[] from curator_preferences.json. ✅

**Q5 — Telegram destination:** Same chat as morning briefing. ✅

**Q6 — intelligence_state.json location:** ~/.openclaw/workspace/ — private, not in repo. ✅

**Q7 — Blind spot trigger:** Daily, silent unless ≥2 qualifying articles found. ✅

---

*Generated 2026-03-14 | personal-ai-agents / Workstream 5*
*Converted from PDF to markdown 2026-03-15*
*Phase A build record: docs/BUILD_WS5_PHASE_A.md*
