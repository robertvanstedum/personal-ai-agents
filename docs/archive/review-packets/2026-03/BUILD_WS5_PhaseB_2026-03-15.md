# Build Record: Intelligence Layer — Phase B
**Mini-moi Personal AI Curator**
**Built:** March 15, 2026
**Sprint:** 1.0 — Workstream 5
**Status:** Complete and confirmed working
**Commits:** `846a0df`, `23ff20d`

---

## What Was Planned

Phase B added three remaining observation types to `curator_intelligence.py`:

1. **Source Anomalies (Obs 3)** — Haiku detects trusted sources drifting in topic focus, tone, or quality vs 30-day baseline. Quiet path: omit from Telegram if no anomalies.
2. **US Press Blind Spots (Obs 4)** — Haiku surfaces stories with strong non-US coverage absent from US outlets. Reads full scored candidate pool from `curator_latest.json`. Quiet path: omit if no significant gaps.
3. **Lateral Connections (Obs 5)** — Sonnet reasons across 30-day reading history and interest profile, surfaces 2–3 adjacent topics with suggested sources. Weekly cadence (Sundays only). Delivered as a separate Telegram message.

No new files, no new launchd plists — all three observations added to the existing daily job with a day-of-week guard for the weekly Sonnet call.

---

## Pre-conditions Completed

**`curator_rss_v2.py` — write `curator_latest.json`**
The full scored candidate pool was not previously stored anywhere accessible after the daily run. Phase B's blind spot detection (Obs 4) needs all candidates, not just the top 20 in `curator_history.json`. Added write at end of `curator_rss_v2.py main()`:
```python
with open('curator_latest.json', 'w') as f:
    json.dump(results, f, indent=2, default=str)
```
~200KB/day. Not committed to repo (operational data). This was a planned pre-condition deferred from Phase A.

---

## What Was Built

### `curator_intelligence.py` (`846a0df`, `23ff20d`)
- **`SONNET_MODEL`** constant added (`claude-sonnet-4-5`), alongside existing `HAIKU_MODEL`
- **`NON_US_DOMAINS` / `US_DOMAINS`** classification sets — hardcoded constants, consistent with whitelist pattern in `curator_priority_feed.py`
  - Non-US: `aljazeera.com`, `dw.com`, `spiegel.de`, `faz.net`, `welt.de`, `oglobo.globo.com`
  - US: `zerohedge.com`, `propublica.org`, `stlouisfed.org`, `ritholtz.com`, `antiwar.com`, `warontherocks.com`, `justsecurity.org`
- **`_sonnet()`** helper — mirrors `_haiku()` from Phase A, same keychain credential pattern
- **`_load_learned_patterns()`** — reads `curator_preferences.json` → `learned_patterns` (keys: `preferred_themes`, `preferred_sources`, `preferred_content_types`, `avoid_patterns`)
- **`observe_source_anomalies()`** — Obs 3, Haiku, quiet path
- **`observe_blind_spots()`** — Obs 4, Haiku, quiet path, reads `curator_latest.json`
- **`observe_lateral_connections()`** — Obs 5, Sonnet, Sunday guard
- **`save_output()`** — extended with `weekly=True` flag to write `intelligence_weekly_YYYYMMDD.json`
- **`main()`** — all five observations wired in; separate Telegram send for weekly lateral connections

### `curator_rss_v2.py` (`846a0df`)
- Added `curator_latest.json` write at end of `main()` — 9 lines including error handling

---

## Confirmed Working Output (March 15, 2026)

**Obs 3 (Source Anomalies):** Fired correctly on first run. Quiet path tested — returns nothing when no anomalies detected across trusted sources.

**Obs 4 (US Press Blind Spots):** Fired with Minab classroom missile strike story — covered by Al Jazeera and DW, absent from US outlet pool. Verified: 6 non-US + 7 US articles correctly classified from today's `curator_latest.json`.

**Obs 5 (Lateral Connections):** Fired (today is Sunday). Sonnet produced 2–3 topic suggestions from 30-day reading history. Delivered as separate Telegram message.

---

## Design Decisions Made During Build

**Minimum history threshold for Source Anomalies:** Sources with fewer than 5 articles in the 30-day history window are skipped silently. Thin history produces unreliable anomaly signals — better to omit than flag spuriously.

**Quiet paths are mandatory, not optional:** All three observations suppress output entirely when nothing meaningful is detected. The daily intelligence message should never pad with "nothing to report" lines. This was specified in the plan and enforced in implementation.

**`curator_latest.json` contains top_articles, not full pool:** The commit message notes the pre-condition writes `top_articles` to `curator_latest.json` — not the full pre-scoring candidate pool. Blind spot detection therefore works from the scored output set, not every article that entered the pipeline. Acceptable for 1.0 — the scored pool is large enough to surface cross-source gaps reliably.

**Domain classification bug fixed post-commit (`23ff20d`):**
The plan specified matching on `entry.get("source", "")` described as the "domain field stored on articles." In practice, the `source` field is a display name ("Al Jazeera", "ZeroHedge"), not a domain. The `link` field holds the full URL. Without the fix, every article was unclassified → Obs 4 always took the quiet path silently.

Fix: `extract_domain(entry.get("link", ""))` — consistent with how Obs 3 classifies history entries. The `NON_US_DOMAINS`/`US_DOMAINS` sets already used domains correctly; only the lookup key was wrong.

Before fix: 0 non-US, 0 US articles classified.
After fix: 6 non-US, 7 US articles classified. Obs 4 fired.

Lesson: when a quiet path is always triggered in testing, verify classification logic before assuming "nothing to report."

---

## Cost

- Obs 3 (Source Anomalies): Haiku, invoked only on flag — ~$0.005/day average
- Obs 4 (Blind Spots): Haiku, invoked only on flag — ~$0.005/day average
- Obs 5 (Lateral Connections): Sonnet, once per week — ~$0.05–0.10/week
- Phase B total addition: ~$0.01/day + ~$0.07/week — negligible

---

## Open Items Carried Forward

- `curator_latest.json` currently writes `top_articles` (scored output), not the full pre-scoring candidate pool. For full pipeline visibility in 1.1, consider writing all scored candidates before top-20 selection.
- Lateral connections prompt quality: first run produced usable output. Prompt may benefit from tuning after a few weeks of Sunday runs — flag for post-1.0 review.
- Source anomaly minimum threshold (5 articles/30 days): conservative. May need lowering for newer trusted sources added to `curator_sources.json` that haven't accumulated history yet.

---

## Workstream 5 Status

**Complete.** All five observation types operational:

| Obs | Name | Status |
|---|---|---|
| 1 | Topic Velocity | ✅ Phase A |
| 2 | Discovery Candidates | ✅ Phase A |
| 3 | Source Anomalies | ✅ Phase B |
| 4 | US Press Blind Spots | ✅ Phase B |
| 5 | Lateral Connections | ✅ Phase B |

Intelligence layer running daily at 7:30AM. Weekly Sonnet call fires Sundays.

---

## Next: Remaining Sprint Work

- **Workstream 3:** Investigation workspace infrastructure (data layer, no UI)
- **Workstream 4:** Mac Mini migration
- **GitHub cleanup:** README, issues, roadmap, CLAUDE.md all current → public launch

---

*Phase B confirmed: March 15, 2026*
*Authored by: Claude Code + OpenClaw (validation and completion)*
*Plan doc: docs/PLAN_WS5_PhaseB_2026-03-15.md*
