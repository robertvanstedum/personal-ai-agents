# Plan: Intelligence Layer — Phase B
**Mini-moi Personal AI Curator**
**Prepared:** March 15, 2026
**Sprint:** 1.0 — Workstream 5
**Status:** Ready for OpenClaw validation → Claude Code build
**Depends on:** Phase A complete ✅

---

## Context

Phase A delivered the intelligence foundation:
- `curator_intelligence.py` running at 7:30AM daily
- Topic velocity observation (momentum + gaps)
- Discovery candidates observation (probationary domain evaluation)
- Telegram delivery + `intelligence_YYYYMMDD.json` storage
- `curator_utils.py` refactored as shared helper module

Phase B adds three remaining observation types and completes Workstream 5.

---

## What Phase B Builds

Three new observations added to `curator_intelligence.py`:

### Observation 3: Source Anomalies (daily, Haiku)
**Purpose:** Detect when a trusted source is behaving differently than its norm — topic drift, quality change, sudden volume spike.

**Logic:**
1. Load `curator_sources.json` — filter to `trust == "trusted"` domains
2. Load `curator_history.json` — for each trusted domain, collect last 10 articles vs 30-day baseline articles from same domain
3. For each trusted domain with enough history (>5 articles in 30 days), one Haiku call:
```
Source: {domain}
Recent articles (last 10): {titles}
30-day baseline articles (sample): {baseline_titles}

Has this source changed in topic focus, tone, or quality recently?
Answer in one sentence. If no notable change, say "No anomaly detected."
```
4. Only surface anomalies in Telegram output — skip "no anomaly" results
5. Format: `⚠️ <b>Source drift:</b> {domain} — {haiku observation}`

**Quiet path:** If no anomalies detected across all trusted sources, omit this observation from Telegram entirely. Don't add noise.

---

### Observation 4: US Press Blind Spots (daily, Haiku)
**Purpose:** Surface stories with high coverage velocity in non-US sources that have low or zero coverage in US outlets. The gap is the signal.

**Non-US sources in RSS pool:**
`aljazeera.com`, `dw.com`, `spiegel.de`, `faz.net`, `welt.de`, `oglobo.globo.com`

**US sources in RSS pool:**
`zerohedge.com`, `propublica.org`, `stlouisfed.org`, `ritholtz.com`, `antiwar.com`, `warontherocks.com`, `justsecurity.org`

**Logic:**
1. Load today's full scored candidate pool (before top 20 selection) from history
2. Separate into non-US articles and US articles by domain
3. Extract topics/keywords from non-US articles using title text
4. Check coverage of same topics in US articles
5. One Haiku call with both sets:
```
Non-US source articles today:
{non_us_titles}

US source articles today:
{us_titles}

Identify 1-2 stories covered in non-US sources but absent or underrepresented
in US sources. Be specific — name the story, not the category.
If no significant gap exists, say "No blind spots today."
```
6. Format: `🌍 <b>Blind spot:</b> {haiku observation}`

**Quiet path:** If Haiku returns "No blind spots today", omit from Telegram.

---

### Observation 5: Lateral Connections (weekly, Sonnet)
**Purpose:** Reason across recent reading history and signal profile. Surface adjacent topics, second-order implications, and suggested sources you're not currently tracking.

**Cadence:** Weekly — fires every Sunday. Separate from daily observations. Delivered as a separate Telegram message.

**Logic:**
1. Load last 30 days of liked/saved articles from `curator_preferences.json`
2. Load current interest profile from `curator_preferences.json` (`learned_patterns`)
3. One Sonnet call:
```
Reading history (last 30 days — liked and saved articles):
{article_titles_and_sources}

Current interest profile summary:
{learned_patterns_summary}

You are a research advisor who knows this reader well.

Identify 2-3 topics, angles, or second-order implications the reader has NOT
been covering but would find genuinely valuable given their interests.

For each suggestion:
- Name the topic specifically
- Explain in one sentence why it connects to their existing interests
- Suggest 1-2 specific sources they could add (real, credible outlets)

Be specific and intellectually honest. Do not suggest topics already well-covered
in their history. Format using HTML for Telegram.
```
4. Format:
```
🧠 <b>Weekly Connections — {date}</b>

{sonnet output}
```
5. Store as `intelligence_weekly_YYYYMMDD.json` in OpenClaw workspace
6. Send as separate Telegram message — not combined with daily

**Cost:** One Sonnet call per week — ~$0.05–0.10. Acceptable.

---

## Files Touched

| # | File | Action |
|---|------|--------|
| 1 | `curator_intelligence.py` | Add 3 new observation functions |
| 2 | `curator_intelligence.py` | Add weekly cadence check in `main()` |
| 3 | `curator_intelligence.py` | Extend `format_telegram()` to include new observations |
| 4 | `curator_intelligence.py` | Extend `save_output()` to handle weekly JSON |

No new files. No new launchd plists — weekly logic runs inside the existing 7:30AM daily job with a day-of-week check.

---

## Implementation Notes

**Weekly cadence check:**
```python
import datetime
today = datetime.date.today()
is_sunday = today.weekday() == 6  # 0=Monday, 6=Sunday
if is_sunday:
    observations.append(observe_lateral_connections(today_str))
```

**Sonnet client:**
Use same keychain pattern as Haiku client in Phase A. Model: `claude-sonnet-4-20250514`

**Source anomaly minimum history:**
Only evaluate sources with >5 articles in the 30-day history window. Sources with thin history produce unreliable anomaly signals. Skip silently.

**Non-US / US domain classification:**
Hardcode the two lists as constants at top of file — same pattern as whitelist in `curator_priority_feed.py`. Do not attempt dynamic classification.

**Quiet paths are mandatory:**
All three observations must have quiet paths. If there's nothing meaningful to say, omit the observation from Telegram entirely. The daily message should never pad with "nothing to report" lines.

---

## Verification Steps

**After adding Observation 3 (source anomalies):**
```bash
python curator_intelligence.py --dry-run --date 2026-03-15
```
Expected: source anomaly observation present in output, or silently omitted if no anomalies. No errors.

**After adding Observation 4 (blind spots):**
```bash
python curator_intelligence.py --dry-run --date 2026-03-15
```
Expected: blind spot observation present or silently omitted. No errors.

**After adding Observation 5 (lateral connections):**
```bash
python curator_intelligence.py --dry-run --date 2026-03-16
```
Note: use a Sunday date to trigger weekly logic. 2026-03-15 is a Sunday ✅ — use today.
Expected: lateral connections observation present in dry-run output.

**Full integration test:**
```bash
python curator_intelligence.py --telegram --date 2026-03-15
```
Expected: Telegram message sent with all available observations. Weekly lateral connections sent as separate message. JSON files written to OpenClaw workspace.

---

## Open Questions for OpenClaw

1. **History schema** — confirm how today's full candidate pool is accessible. Phase A used `curator_history.json` keyed by hash_id with `appearance` dates. Confirm whether the full pre-top-20 pool is stored anywhere, or if blind spot detection must work from top 20 only.

2. **learned_patterns location** — confirm exact key path in `curator_preferences.json` for the learned patterns summary used in Lateral Connections prompt.

3. **Sonnet model string** — confirm current production model string for Sonnet. Phase A uses `claude-haiku-4-5` for Haiku — confirm Sonnet equivalent.

4. **Weekly JSON naming** — confirm no conflict with existing workspace files using `intelligence_weekly_` prefix.

---

## Success Criteria

- Source anomaly observation fires correctly, quiet path works when no anomalies
- Blind spot observation identifies at least one cross-source gap on a day with active international coverage
- Lateral connections fires on Sundays only, produces 2-3 specific topic suggestions with sources
- All quiet paths suppress empty observations from Telegram
- Weekly Sonnet message sends as separate Telegram message
- All JSON outputs written correctly to OpenClaw workspace
- No regressions to Phase A observations

---

## Post-Build

Claude Code writes build summary → "pass to OpenClaw" → OpenClaw saves to `docs/BUILD_WS5_PhaseB_YYYY-MM-DD.md` and appends CHANGELOG.

---

*Prepared by Claude.ai — March 15, 2026*
*For OpenClaw validation before Claude Code handoff*
*Convention: this file saved to `docs/PLAN_WS5_PhaseB_2026-03-15.md`*
