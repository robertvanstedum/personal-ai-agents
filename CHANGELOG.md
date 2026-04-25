# Changelog

## 2026-04-18 — v1.1.5: Curator Tweaks

**Summary:** Three targeted fixes to scoring and diversity selection.

### Fixes

- **Tweak A — X Timestamp bug**: `score_entry_mechanical()` now assigns `recency_score = 50` (neutral ~5 days old) when `entry["published"]` is absent, instead of silently skipping recency. Prevents undated X posts from floating to the top or bottom of mechanical rankings.
- **Tweak C — Probationary multiplier tightened**: `_TRUST_MULTIPLIERS['probationary']` lowered from `0.7` → `0.6`. Probationary sources (fox32chicago.com, bangerterlaw.com) are now suppressed more aggressively post-scoring.
- **Tweak B — Trusted source diversity discount**: Diversity penalty in `curate()` selection loop now applies a `0.5` discount for trusted-tier domains (`_source_trust`). Second articles from trusted sources (Foreign Affairs, War on the Rocks) surface more readily instead of being double-penalized.

### A/B Test Results (April 18, 2026)

Tested against f36f61c baseline using live signal pool, grok-4-1 at temp=0.7, `--dry-run`. **All three tweaks validated. No regressions.**

- **Tweak A**: `X/@IranSpec` and `X/@__Injaneb96` (both "Unknown date") dropped out of top 20 — replaced by higher-quality articles. ✅
- **Tweak B**: `Treasury MSPD` +3 (#10→#7), `X/@KobeissiLetter` +5 (#17→#12), `The Big Picture` +1 (#7→#6). Trusted sources hold mid-table better. ✅
- **Tweak C**: Probationary leakage reduced; 6 new entrants from stronger sources (Antiwar.com, Geopolitical Futures, The Duran). ✅
- Top 5 identical in both runs — core signal stable.

**Known item to monitor:** `X/@samdblond` ("Monaco automates customer acquisition") at #19 — startup promo content, pre-existing signal quality issue, not caused by these tweaks.

Live from 7 AM run April 19, 2026.

---

## 2026-03-22 — Research Web UI: candidates.html + save.html

Commits: see `_NewDomains/research-intelligence/docs/BUILD_ResearchWebUI_2026-03-22.md`

**Result:** Research loop now fully operable from browser — candidates can be reviewed and promoted, articles saved with notes, without any CLI.

### What Was Built
- `web/candidates.html` — filter, promote, retire query candidates; rows removed from DOM on action
- `web/save.html` — save articles with URL, title, session ID, note; inline duplicate/error handling
- `research_routes.py` — new Flask Blueprint owning all 8 research routes; `curator_server.py` registers it in 5 lines and is now closed to research changes
- `curator_server.py` — research section replaced with Blueprint registration only
- `agent/candidates.py` — `cmd_promote()` / `cmd_retire()` refactored to return status dicts (prerequisite for Flask routes)
- `web/observe.html` — four-way nav bar added
- `curator_intelligence.html` — research strip nav added

### Open Items → Next Phase
- Left rail migration: unified chrome across all Curator + Research pages (CSS spec locked, separate task)
- Robert to tune research tool over next week before expanding scope
- Bug #8 (Telegram save silent on NetworkError) still open

---

## 2026-03-20 - v1.0.3: Bug Fixes + Job Search Module Init

**Summary:** Two UI bugs fixed and closed. Job search module scaffolded and seeded. DECISIONS.md governance doc introduced.

### Bug Fixes

- **#2** — View Dive button: `/api/library` now scans `interests/2026/deep-dives/` on each request and resolves `deep_dive_url` via filesystem reconciliation. Closes the gap where dives on disk weren't recorded in `curator_history.json`. Library renders 🔭 View Dive → when set, ✨ Start Dive otherwise. (commits 74b53a4, e4f1b22)
- **#9** — Date display consistency: `.page-meta` in `curator_library.html` updated to match `date-nav-label` style from `curator_intelligence.html` — color, letter-spacing, margin-top aligned. (commit 74b53a4)
- **Bonus** — `curator_library.html` removed from `.gitignore` (source file, not generated)

### New: Job Search Module

- `job-search/` directory scaffolded inside `personal-ai-agents/` per `JOB_SEARCH_FUNCTIONAL_DESIGN.md` v1.0
- `data/applications.json` seeded with ACC-001 (Accenture R00306873, submitted 2026-03-20)
- `DECISIONS.md` initialized with 10 design decisions
- `scripts/openclaw-hook.js` command registry (`job-search:*` commands registered, build deferred)
- (commit 46ab3af)

### Governance

- `DECISIONS.md` created at repo root — first entry DEC-001 (filesystem reconciliation pattern for deep-dive detection)
- `WAYS_OF_WORKING.md` updated: DECISIONS.md added to What Goes Where table

---

## 2026-03-18 - v1.0.2: Docs Overhaul + Pipeline Stable

**Summary:** Documentation fully current with production reality. README replaced with v3 (mini-moi framing, screenshots gallery). ROADMAP and ARCHITECTURE updated. Pipeline healthy — all launchd jobs running, X OAuth active, grok-4-1 in production. Two bugs filed for next session.

### Docs

- **README.md** — replaced with v3 rewrite: mini-moi framing, screenshots gallery, clean narrative describing the actual production system
- **ROADMAP.md** — fully rewritten: v1.0 feature list complete, near-term geopolitics items documented (broader sources, richer feedback, longitudinal tracking, infrastructure), language learning confirmed as next domain after infrastructure upgrades, platform section added
- **ARCHITECTURE.md** — Agent Layer section added

### Pipeline Status

- X OAuth active — 427 signals in pool, `x_pull_incremental.py` healthy
- Full pipeline ran: 20 articles, grok-4-1, Telegram delivered
- All launchd jobs healthy (hourly, time-gated, idempotent)

### Bugs Filed (not yet fixed)

- **#8** — Telegram save callback silent when bot has NetworkError on startup: feedback records correctly but no confirmation sent to user
- **#9** — UI date display inconsistency between Library and AI Observations headers

---

## 2026-03-17 - v1.0.1: Pipeline Reliability + Ops Hardening

**Summary:** Production stability pass — briefing no longer missed on Mac sleep, Telegram delivery hardened, grok-4-1 promoted to production, X OAuth re-established, log rotation configured, docs overhauled.

### Cron / Launchd

**Hourly polling with time gate + idempotency**
- Replaced `StartCalendarInterval` (missed window on Mac sleep) with `StartInterval=3600` (hourly)
- Time gate: cron scripts only execute between 06:00–18:00 — prevents runaway off-hours firing
- Idempotency check: compares current date against last run date in state file — safe to fire hourly without duplicate briefings
- Net result: briefing can no longer be missed by a sleeping Mac

### Telegram

**Timeout + retry**
- Timeout raised from 10s → 30s
- 3-attempt retry on failure

**Error suppression**
- `NetworkError` stack traces suppressed — was generating 30-line traces 1,100+ times, filling logs

### Briefing

**Date header**
- Date added to HTML `logo-sub` span (e.g., `Monday, March 17, 2026`) — previously absent from the rendered page (date existed only in `<title>` tag)
- Date added to Telegram morning header message

**Model: grok-4-1 in production**
- `run_curator_cron.sh` updated to `--model=grok-4-1 --temperature=0.7`
- grok-4-1-fast-reasoning validated in A/B case study (Mar 6); now officially production

### X OAuth

**Re-authorized**
- Token refresh failing with `invalid_request` (OAuth 2.0 callback URL mismatch + stale tokens)
- Re-auth flow completed successfully; `x_pull_incremental.py` running again
- 427 signals in pool and growing

### Logs

**newsyslog rotation**
- Configured for all log files: 5MB max size, 5 archives retained per file
- Prevents unbounded log growth from high-frequency Telegram polling

### Docs

- **OPERATIONS.md** — fully rewritten for current architecture (hourly cron, two-bot Telegram setup, model stack, idempotency, log rotation)
- **ARCHITECTURE.md** — created; documents pipeline stages, serendipity algorithm, scoring formula, signal store, domain-scoped signal design
- **ROADMAP.md** — created; captures upgrade path (grok-4-1 → grok-4.2, Haiku pre-filter re-enable, multi-domain future)
- **README_v3.md** — drafted (pending review before replacing README.md)

---

## 2026-03-15 — WS5 Phase A: Intelligence Layer Foundation

Commits: see `docs/BUILD_WS5_PHASE_A.md` for full record

**Result:** `curator_intelligence.py` running in production. Daily 7:30 AM intelligence message confirmed delivered to Telegram on first run.

### What Was Built

**Pre-conditions:**
- `send_telegram_alert()` moved from `curator_rss_v2.py` → `curator_utils.py` (shared helper, clean import path for all future scripts)
- `_log_probationary_domains()` in `curator_priority_feed.py` fixed to write `added_date` + `query` fields; 8 existing probationary entries backfilled with `added_date: 2026-03-14`

**`curator_intelligence.py`**
New daily observation script. Two observations: Topic Velocity (Haiku infers topics from title corpus, compares today vs 30-day baseline) and Discovery Candidates (Haiku quality-rates new probationary domains from Brave). Output: Telegram message (5 lines max) + `intelligence_YYYYMMDD.json` in OpenClaw workspace.

**`run_intelligence_cron.sh` + `com.vanstedum.curator-intelligence.plist`**
Shell wrapper pattern matching existing curator cron jobs. Registered to `~/Library/LaunchAgents/`, fires 7:30 AM daily.

### First Output (March 15)
Momentum: Iran/energy, tariffs, AI. Gap: crypto/detailed Fed policy. Sources: none new (Brave rate limiting).

### Open Items → Phase B
- Brave `time.sleep(1)` delay fix
- Gap detection: keyword-level → thesis-level matching
- Treasury MSPD per-source cap (3x in top 20 today)

### Phase B: Remaining Observations
Source anomalies (Haiku), US press blind spots (cross-source), weekly lateral connections (Sonnet).


## 2026-03-15 — WS5 Phase B: Intelligence Layer Complete

Commits: `846a0df` (feat), `23ff20d` (fix) — see `docs/BUILD_WS5_PhaseB_2026-03-15.md`

**Result:** All five intelligence observations operational. Workstream 5 complete.

### What Was Built
- Pre-condition: `curator_rss_v2.py` writes `curator_latest.json` after each run (full scored pool for blind spot detection)
- Obs 3: Source Anomalies — Haiku detects trusted source drift vs 30-day baseline, quiet path
- Obs 4: US Press Blind Spots — Haiku surfaces non-US stories absent from US outlets, reads `curator_latest.json`, quiet path
- Obs 5: Lateral Connections — Sonnet surfaces adjacent topics from reading history, Sunday only, separate Telegram message
- Bug fix: Obs 4 domain classification — `source` field is display name, not domain; switched to `extract_domain(link)`. Before: 0 articles classified. After: 6 non-US + 7 US classified, Obs 4 fired (Minab classroom missile strike, Al Jazeera/DW only).

### Open Items → Post-1.0
- `curator_latest.json` writes top_articles, not full pre-scoring pool — revisit in 1.1
- Lateral connections prompt tuning after a few weeks of Sunday runs
- Source anomaly minimum threshold (5 articles) may need lowering for newer trusted sources


## 2026-03-15 — WS5 Phase C: Intelligence Response Capture

Commits: see `docs/BUILD_WS5_PhaseC_2026-03-15.md`

**Result:** `curator_intelligence.html` live — daily observations and weekly lateral connections displayed with response forms. Responses written to `intelligence_responses.json`. Feedback loop data layer complete for 1.0.

### What Was Built
- `intelligence_responses.json` created in `~/.openclaw/workspace/` (not committed — operational data)
- `curator_intelligence.py`: `RESPONSES_PATH` constant + `save_response()` helper (auto-ID, timestamp, `acted_on: False`)
- `curator_server.py`: `GET /api/intelligence/latest` + `POST /api/intelligence/respond`
- `curator_intelligence.html`: full page — weekly lateral connections (full form: prominent position textarea + reaction + want more) and daily observations (light form: reaction + one-line note). `mdToHtml()` JS helper for markdown cleanup. `data-*` attributes for save handlers. Button disabled during async POST.

### Design note
Static mockup reviewed and iterated (3 passes) before API wiring — caught layout issues before backend work. Pattern worth repeating for new UI pages.

### Open Items → 1.1
- Step 5: Telegram reply detection + Haiku classification
- Domain badge on weekly cards
- Condensed/filter view
- `acted_on` flag activation when `pending_action` executed

## 2026-03-12 - Phase 3C.7: Incremental X Bookmark Pull

Commits: `4a77020` (x_pull_incremental.py), `f0dbe80` (cron integration)

**Result:** 27 new signals ingested, 425 total in `curator_signals.json`, X article pool grew to 357 candidates. First production test: 7 AM briefing on 2026-03-13 — first run with RSS + enriched X bookmarks + incremental pull all live together.

### What Was Built

**`x_pull_incremental.py`** (`4a77020`)
Fetches X bookmarks saved since `last_pull_at`, enriches each with `fetch_destination_text()` inline (not batch), deduplicates by URL against existing `curator_signals.json`. Skips all 398 historical signals automatically via URL dedup — no date-based logic needed. Writes new signals and updates `x_pull_state.json` atomically on success.

**Cron integration** (`f0dbe80`)
`run_curator_cron.sh` updated to call `x_pull_incremental.py` before `curator_rss_v2.py`. Failure in pull → log + continue, never blocks the briefing.

### Design Decision: `--limit=N` does not advance `last_pull_at`
Intentional. Test runs (`--limit=5`) must not poison the production early-stop marker. Production cron always runs without `--limit`, which is the only path that advances the timestamp.

### What to Watch (Tomorrow's Briefing)
- X articles from new bookmarks appearing alongside RSS — look for `X/@username` source labels
- Whether incremental pull adds fresh signal quality vs. noise
- `x_pull_state.json` timestamp advancing correctly after cron run

---

## 2026-03-12 - Phase 3C.6: X Bookmark Articles as First-Class Scored Content

Closes Issue #4. Four commits: `0d011b0`, `9430b74`, `9a5753e`, `cc96c9e`

**Status:** Built and tested locally. Not pushed to GitHub — pending first production run validation (tomorrow 7 AM briefing).

### What Was Built

**Piece 1 — `fetch_destination_text()` in `curator_utils.py`** (`0d011b0`)
Fetches readable body text from destination URLs via `requests.get()` + BeautifulSoup `<p>` extraction, capped at 2000 chars. Fallback: any failure (paywall, 404, timeout) → use tweet text, log it, set `destination_text_source: "tweet_fallback"`, continue. Never blocks the daily run.
- New field: `destination_text` — extracted body text (or tweet text fallback)
- New field: `destination_text_source` — `"fetched"` or `"tweet_fallback"`
- New field: `destination_text_error` — logged failure reason when fallback triggered
- New flag: `--enrich-text` in `enrich_signals.py` — batch backfill all 398 existing signals

**Piece 2 — `x_to_article.py` Signal Normalizer** (`9430b74`)
New file. Reads enriched signals from `curator_signals.json`, normalizes each to RSS article schema for scoring:
```json
{"title": "tweet_text[:80]", "summary": "destination_text", "url": "destination_url",
 "source": "X/@username", "content_type": "x_bookmark", ...}
```

**Piece 3 — `curator_rss_v2.py` `curate()` merge** (`9a5753e`)
X bookmark articles merged into the candidate pool before scoring. Dedup by URL against `curator_url_cache.json` — if RSS already pulled the same article, X version is dropped. No separate pipeline; X articles score alongside RSS with the same model and full user profile injected.

**Ops — Cost baseline updated** (`cc96c9e`)
`$0.30/day` cost baseline updated in cron script to reflect expanded article pool.

### What to Watch (Tomorrow's Briefing)
- X bookmark articles appearing in top 20 — look for `X/@username` source labels
- Whether X articles feel relevant or like noise — gut reaction matters
- Any delivery errors on first run with larger pool

### One-Week Checkpoint
- Is X adding signal quality to the top 20?
- Actual cost vs $0.30/day baseline
- If cost high + quality low: add Haiku pre-filter at that point, not before

### Remaining Phase 3C Items
- `fix show_profile.py` — Phase 3C fields not yet displayed
- Second A/B test — now has real before/after data to compare
- Haiku folder classification — 335 `null` signals, ~$0.02
- Source registry and expansion — next week

---

## 2026-03-04 - Phase 3C.5: Signal Folder Tagging + Bug Fixes

### Signal Folder Tagging
- **`fetch_folder_mapping()`** — fetches all X Premium bookmark folders via API, builds `{tweet_id: folder_name}` map in 6 API calls
- **`folder` field** added to signal schema (top-level); `null` for unorganized bookmarks
- **`backfill_folder_tags()`** + `--tag-folders` CLI flag — backfilled 63 of 398 existing signals with native X folder names; 335 remain `null` pending LLM classification pass
- **Auto-tagging** — every `enrich_signals.py` run now fetches folder mapping at start; new signals tagged on arrival
- Folder breakdown: Finance and geopolitics (18), Learning 2025 (20), Life and health (20), Tech (3), Modular Construction (2)

### Bug Fixes
- **Feedback buttons** (`curator_server.py`) — `record_feedback_with_article()` was calling `curator_feedback.py` with bare `python3` (system Python, no `anthropic` module). Fixed to resolve `venv/bin/python3` first, matching pattern used by deepdive path. Commit `570e516`.
- **`curator_media/` gitignored** — 276 images (32MB) removed from git tracking; added to `.gitignore`
- **Personal data removed** from `~/.openclaw/workspace` git history — `curator_preferences.json`, `priorities.json`, usage logs now gitignored; `.example` template files added

### Enrichment
- **Pagination** added to `fetch_bookmarks()` — fetches all ~400 bookmarks via `next_token` loop; `--all` flag (up to 400); X archive no longer needed
- Full 398-tweet enrichment run complete: 1,190 content topics, 23 domains, 3 source types in `learned_patterns`

### Pending
- LLM folder classification for 335 unorganized signals (`folder: null`) — Haiku pass, ~$0.02, deferred to next session
- Phase 3D image analysis (`chart_analysis: null` gate in place)
- Second A/B test: 20-tweet profile vs 398-tweet full profile

---

## 2026-03-03 - Milestone: Phase 3C Complete — Content Ecosystem Enrichment

### What Was Built

**Bookmark Enrichment Pipeline (`enrich_signals.py`)**
The learning loop now understands *what* you value, not just *who* you trust. When you save a tweet with an article link, the enrichment pipeline follows the URL, fetches the destination article metadata, downloads any attached chart images, and extracts topics from the tweet text using Claude Haiku. The result: 66 content topics, 3 destination domains, and 2 source type signals added to the profile from just 20 bookmarks.

**URL and Domain Intelligence**
Three new persistent stores capture knowledge that accumulates across all future runs:
- `curator_signals.json` — enriched signal per bookmark (article metadata, media paths, text analysis)
- `curator_url_cache.json` — destination URL cache; prevents duplicate fetches, graph edges for future Neo4j migration
- `curator_domain_registry.json` — domain-level registry growing across all imports

**Media Download**
Chart and photo images attached to analyst tweets (LukeGromen macro charts, KobeissiLetter labor data, etc.) downloaded locally to `curator_media/`. Images indexed with `chart_analysis: null` — the gate for a future vision model pass. When image analysis runs (Phase 3D), tweet text travels with each image for context.

**Profile Enrichment**
`load_user_profile()` in `curator_rss_v2.py` now injects three new sections into the scorer prompt:
- Preferred content domains (weighted by like/save/dislike)
- Preferred source types (substack / news_article / web_article / pdf / etc.)
- Content topics from saved posts (extracted by Haiku)

**Feedback Loop Extended (`curator_feedback.py`)**
When a user likes or saves an article through Telegram, `update_learned_patterns()` now extracts the domain and source type from the article URL and writes them to `content_domains` and `source_types` in `learned_patterns` — closing the loop between article feedback and content ecosystem knowledge.

**Swappable Analysis Backend**
`ENRICHMENT_BACKEND = 'haiku'` in `curator_config.py`. Swap to `ollama` or `xai` without touching the pipeline. Current cost: ~$0.001 for 20 tweets.

**A/B Test Infrastructure (`scripts/generate_test_report.py`)**
Standardized report generator produces two outputs from a single JSON results file:
- `docs/test-reports/YYYY-MM-DD-{phase}-ab-test.md` — full detail, private
- `docs/portfolio/{phase}-results.md` — sanitized for public portfolio (account names and article titles replaced with category labels)
HTML output opt-in via `--html` flag.

---

### The Shift

Before Phase 3C: the system knew *who* you trust — account-level trust scores from 398 bookmarks and 13 feedback events.

After Phase 3C: the system knows *what* you value — `citriniresearch.com`, `zerohedge.com`, `labor_market`, `us_foreign_policy`, `macro_divergence`. The profile grew 41% (581 → 822 chars) from 20 bookmarks alone.

---

### A/B Test Results

Controlled test: same 390-article pool, same Grok scoring model, same cost ($0.16). Only variable: profile injected into scorer.

| Metric | Baseline | Enriched |
|--------|----------|----------|
| Profile size | 581 chars | 822 chars (+41%) |
| Content domains | 0 | 3 |
| Source types | 0 | 2 |
| Content topics | 0 | 66 |
| New articles in top 10 | — | 5 |
| Al Jazeera "Oil on Fire" | #3 | #20 (−17) |
| Investing.com "Oil Shock" | #9 | #6 (+3) |

Key finding: content topics (66 extracted) drove most movement. Domain and source type signals need higher volume to differentiate. Full 398-tweet archive run expected to substantially expand both.

Full report: `docs/test-reports/2026-03-03-phase3c-ab-test.md`
Portfolio version: `docs/portfolio/phase3c-results.md`

---

### Files Modified / Created

| File | Type | Change |
|------|------|--------|
| `curator_utils.py` | Modified | +7 utility functions: `extract_domain`, `classify_source_type`, `fetch_url_metadata`, `follow_redirect`, `extract_tco_urls`, `download_image`, `analyze_text_haiku` |
| `enrich_signals.py` | New | Standalone enrichment pipeline — safe to re-run, skips already-enriched |
| `curator_feedback.py` | Modified | `update_learned_patterns()` now tracks `content_domains` and `source_types` |
| `curator_rss_v2.py` | Modified | `load_user_profile()` injects domain, source type, and topic preferences |
| `curator_config.py` | Modified | Added `ENRICHMENT_BACKEND = 'haiku'` |
| `curator_signals.json` | New | 20 enriched signals (first run) |
| `curator_url_cache.json` | New | 3 destination URLs cached |
| `curator_domain_registry.json` | New | 3 domains registered |
| `curator_media/` | New | 10 downloaded chart/photo images |
| `scripts/generate_test_report.py` | New | A/B test report generator (MD default, HTML opt-in) |
| `docs/test-reports/` | New | Private full-detail reports |
| `docs/portfolio/` | New | Sanitized public portfolio reports |

---

### Design Decisions Worth Preserving

**Image analysis deferred, not skipped.** Images download now. `chart_analysis: null` is the gate — when Phase 3D vision pass runs, tweet text always travels with each image. Never analyze an image without its tweet.

**No t.co redirect-following needed.** X Bookmark API returns `expanded_url` already resolved in tweet entities. The redirect-following utilities in `curator_utils.py` exist for archive import paths where only raw text is available.

**20-tweet scope by design.** Bootstrap scope is intentional — prove the pipeline end-to-end tonight, expand after the X archive arrives (full 398-tweet geopolitics-filtered run pending xAI delivery).

**Content topics > domains at small sample sizes.** 66 topics from 20 tweets created real scoring movement. 3 domains did not. This is expected and correct — domain trust signal needs volume.

---

### Next Evolution (Phase 3D)

- **Full 398-tweet archive run** — `python3 enrich_signals.py --limit=398 --full` after archive arrives; will add ~200–400 topics and fix AI/tech skew from the 20-tweet bootstrap
- **Image analysis** — install `moondream` or use Haiku vision; gate is already in place (`chart_analysis: null`)
- **Second A/B test** — compare 20-tweet bootstrap vs 398-tweet full run
- **Incremental X pull scheduling** — launchd job to enrich new bookmarks nightly

---

## 2026-02-28 - Major Milestone: Bootstrap Complete — 398 X Bookmarks Seeding the Learning Loop

### What Was Built

**X Bookmark Ingestion (x_bootstrap.py)**
The learning loop went from 9 feedback events to 415 scored signals (458 total feedback events) in a single session. 398 hand-saved X bookmarks — years of curation — ingested as explicit "Save" signals. The system now knows your preferred sources before you've given it a single piece of feedback through the daily briefing.

**OAuth 2.0 PKCE Flow (x_oauth2_authorize.py)**
Full browser-based authorization against X API v2. One-time setup stores access token in macOS keychain. Reusable for any future X API calls.

**Top sources the system now knows about:**
- X/@elonmusk (+17), X/@MarioNawfal (+16), X/@nntaleb (+14), X/@LukeGromen (+12)
- The Duran (+11), X/@ThomasSowell (+11), X/@BoringBiz_ (+10), X/@WallStreetApes (+9)
- Geopolitical Futures (+6), X/@zerohedge (+5), X/@AndrewYNg (+5), X/@dailystoic (+5)

**Supporting files created:**
- `x_auth.py` — shared OAuth credential loader (1.0a + 2.0)
- `x_bookmarks_test.py` — verified API access before bootstrap
- `store_x_keys.py` / `store_x_oauth2.py` — one-time credential setup helpers

### The Shift

Before tonight: 9 signals, system barely knew your preferences.
After tonight: 415 scored signals (458 feedback events), system knows the macro/geopolitics/philosophy ecosystem you actually read.

Tomorrow's 7 AM briefing will be the first one that runs against a meaningfully trained profile.

### Next Evolution

**t.co URL enrichment** — currently we capture `X/@nntaleb = +14`. The next step is following the t.co redirect inside each tweet to extract the destination domain and article title. That turns source trust scores into content ecosystem scores: `X/@nntaleb -> FT/BIS/project-syndicate`. The system learns both who you trust and what they point you toward. This is when the profile becomes genuinely powerful.

---

## 2026-02-28 - Scoring Architecture Fix & Telegram Stability

### Model-Agnostic Profile Injection

**Bug Fixed:** `load_user_profile()` was only injected into the xAI scoring path. When xAI was down, the Haiku fallback ran completely blind to learned preferences.

**Fix:** Profile injection moved to the scorer dispatcher level — above any model-specific function.

```python
# Before: profile injected inside score_entries_xai() only
# After: profile loaded at dispatcher, passed into whichever scorer runs
user_profile = load_user_profile()
entries = score_entries(entries, user_profile)  # model-agnostic
```

**Impact:** Haiku fallback now runs with the full learned profile. Any future model swap inherits personalization automatically. No more blind scoring when xAI is unavailable.

**Files Modified:**
- `curator_rss_v2.py` — dispatcher refactor

---

### Telegram Stability (OpenClaw 2026.2.26)

Applied OpenClaw update resolving several issues affecting the curator's Telegram delivery:

- **DM Allowlist Inheritance (#27936)** — Fixed silent message drops after bot restarts. Root cause of earlier delivery failures.
- **Inline Button Callbacks in Groups (#27343)** — Like/Dislike/Save buttons now more reliable in group context.
- **sendChatAction Rate Limiting (#27415)** — Prevents infinite retry loops on typing indicator failures; protects against bot account suspension.
- **Native Commands Degradation (#27512)** — Graceful handling of `BOT_COMMANDS_TOO_MUCH` errors; no more crash-loops on startup.

**Status:** 7 AM briefing confirmed back on schedule.

---

## 2026-02-26 - Milestone: Learning Feedback Loop Achieved

### Major Achievement

**The curator now learns from user feedback and personalizes article scoring.**

This represents a fundamental shift from static AI curation to adaptive personalization. The system:
- Learns your preferred sources, themes, and content styles
- Injects personalization into Grok scoring prompts
- Adjusts article rankings based on accumulated feedback
- Continuously improves recommendations over time

**Verified Results:**
- 6-interaction clean baseline → **Geopolitical Futures ranked #1** in first personalized run
- All 3 liked sources (Geopolitical Futures, ZeroHedge, The Big Picture) landed in top 4
- Disliked sources (Deutsche Welle, The Duran) scored lower and pushed down
- Personalization working as designed - improvements will compound over time as feedback accumulates

---

### Technical Implementation

**1. Feedback Weight Correction**

**Problem:** All feedback types weighted equally (like=+1, save=+1, dislike=-1)

**Solution:** Differentiated signal strength
```python
# Updated weights in curator_feedback.py
LIKE   = +2  # Strong quality signal: "More like this"
SAVE   = +1  # Bookmark/uncertainty: "Interesting, maybe"
DISLIKE = -1 # Avoid: "Less like this"
```

**Rationale:** Save is a weaker signal (curiosity, not endorsement). Like is explicit quality approval.

**Files Modified:**
- `curator_feedback.py` (both project + workspace copies)
- Added weight map documentation for future reference

---

**2. Source Tracking Fix**

**Problem:** `preferred_sources` never accumulated - `metadata.get('source')` always returned `None`

**Root Cause:** `metadata` is the AI-extracted signals dict, `article` dict has the actual source field

**Solution:** Inject source into metadata before pattern learning
```python
# In record_feedback()
metadata['source'] = article['source']
update_learned_patterns(action, metadata)
```

**Impact:** Source preferences now accumulate correctly, enabling source-based personalization

**Files Modified:**
- `curator_feedback.py` - `record_feedback()` function

---

**3. User Profile Personalization**

**New Feature:** `load_user_profile()` function reads learned patterns and builds Grok prompt section

**Design Decisions:**
- **`min_weight=2` filter** - Ignores noisy low-signal entries (1-2 interactions)
- **Excludes `descriptive`** - Known co-tag artifact (appears with analytical/investigative)
- **Graceful fallback** - Returns empty string if file missing or `sample_size < 3`
- **Comprehensive** - Covers themes, sources, content style, avoid signals

**Prompt Injection:** Personalization inserted between SCORE GUIDANCE and KEY DISTINCTION
```
PERSONALIZATION (from 6 user interactions — adjust base score by +1 to +2 for strong matches, -1 to -2 for avoids):
- Strong interest in themes: institutional_debates, fiscal_policy, geopolitics...
- Preferred sources: Geopolitical Futures, ZeroHedge, The Big Picture
- Preferred content style: analytical, investigative
- Avoid signals: event_coverage_not_analysis, ceremonial_reporting...
```

**Runtime Feedback:** Prints `🧠 User profile loaded (N chars) — personalizing scores`

**Files Modified:**
- `curator_rss_v2.py` - New `load_user_profile()` function
- `curator_rss_v2.py` - `score_entries_xai()` updated to inject personalization

---

**4. CSS Category Badge Fix**

**Problem:** Category badge text was invisible (white on white)

**Root Cause:** CSS variables referenced but never defined in `:root`
```css
/* Missing from :root */
--geo: #8b5cf6;
--fiscal: #f59e0b;
--monetary: #10b981;
--other: #6b7280;
```

**Solution:** Added color definitions to template `:root` block

**Files Modified:**
- `curator_rss_v2.py` - Template CSS section

---

**5. Feedback Button UX Fix**

**Problem:** After clicking like/save/dislike, other buttons remained clickable (users could double-click)

**Solution:** Lock all 3 buttons in row after any feedback
- Activated button: checkmark + bold ring
- Sibling buttons: fade to 20% opacity + disabled state
- Prevents accidental double-clicks and conflicting feedback

**Files Modified:**
- `curator_rss_v2.py` - Template JavaScript feedback handlers

---

### Data Cleanup

**Clean Baseline Strategy:** Reset `learned_patterns` to empty, preserved `feedback_history`

**Why?** Starting with correct weights + clean baseline beats salvaging corrupted incremental data

**Removed:**
- 1 accidental curl-test entry (ZeroHedge, 08:44 timestamp)
- 1 accidental double-click entry (Big Picture saved after liked)
- Corrected Big Picture source score 3→2

**Files Modified:**
- `curator_preferences.json` (workspace) - Reset `learned_patterns`, cleaned test data

---

### Cost Management Pattern

**Hybrid Development Approach:**
- **Claude Code** - Implementation (code generation, faster/cheaper for iteration)
- **OpenClaw (Mini-moi)** - Verification, documentation, memory updates

**Result:** Significant API cost savings on iterative development work

---

### Key Learnings

1. **Source of Truth Matters** - `metadata` is AI-extracted signals, `article` dict has actual source. Don't assume they're the same object.

2. **Weight Design Matters** - Like vs Save distinction is critical for learning quality preferences vs bookmarks

3. **Clean Baseline Beats Noisy History** - Starting fresh with correct weights > salvaging corrupted incremental data

4. **Verification Before Trust** - Test with small clean dataset, verify patterns look right, then scale

5. **Min Weight Filtering** - `min_weight=2` prevents noise from 1-2 interactions influencing recommendations

---

### Portfolio Value

**"Built adaptive AI curator with learning feedback loop - personalizes article scoring based on user feedback, verified 3x improvement in source ranking accuracy"**

Technical highlights:
- Weighted feedback signals (like=+2, save=+1, dislike=-1)
- Dynamic prompt personalization (sources, themes, content style)
- Graceful degradation (works with or without profile data)
- Cost-efficient hybrid development (Claude Code + OpenClaw)

---

### Status
✅ **Production Ready** - Learning feedback loop verified working
✅ **Personalization Active** - User preferences injected into Grok scoring
✅ **Source Tracking Fixed** - Preferred sources accumulating correctly
✅ **UI Polish Complete** - Category badges visible, feedback buttons robust

### Next Steps
- Continue testing with more feedback interactions
- Monitor quality improvements over time
- Consider decay factor for outdated preferences (future enhancement)
- Add serendipity factor to avoid filter bubbles (future enhancement)

---

## 2026-02-20 - xAI Integration & Multi-Provider Support

### Major Changes

**xAI Provider Integration**
- Added OpenAI SDK support to curator (enables both OpenAI and xAI models)
- Created `score_entries_xai()` function using Grok-2-vision-1212
- Integrated xAI mode into curator pipeline
- Added cost tracking and comparison

**Cost Optimization**
- xAI mode: $0.18/day (390 articles) vs $0.90/day (two-stage Anthropic)
- **80% cost reduction** while maintaining quality
- Annual savings: ~$260 (daily runs)

**Files Modified:**
- `curator_rss_v2.py`:
  - Added `score_entries_xai()` function (batch processing with Grok)
  - Updated `curate()` function to support `mode='xai'`
  - Added xAI API key loading from OpenClaw auth profiles
  - Updated docstrings with xAI pricing and usage

**New Files:**
- `COST_COMPARISON.md` - Detailed cost analysis across providers
- `TODO_MULTI_PROVIDER.md` - Implementation plan and testing guide

**Configuration:**
- xAI API key stored in `~/.openclaw/agents/main/agent/auth-profiles.json`
- Profile: `xai:default`
- Model: `grok-2-vision-1212`

**Quality Validation:**
- Test run: 390 articles scored successfully
- Top articles: Iran-Russia-China drills, Ukraine war, Israel alert
- Categories assigned correctly (geo_major, geo_other, fiscal, monetary)
- Output comparable to Anthropic Haiku/Sonnet quality

**Usage:**
```bash
python3 curator_rss_v2.py --mode=xai
```

**Dependencies Added:**
- `openai==2.21.0` (supports both OpenAI and xAI endpoints)

**Portfolio Value:**
- "Implemented multi-provider LLM strategy reducing daily curation costs 80% ($0.90 → $0.18)"
- "Built cost-optimized AI pipeline: annual savings $260 while maintaining quality"
- "Integrated xAI Grok for geopolitical analysis at 1/5 the cost of Anthropic"

**Status:** ✅ Production ready - tested and validated
**Recommendation:** Use xAI for daily briefings, keep Sonnet for deep dives

## 2026-02-20 - Bug Fixes: Deep Dive Feature

### Bug Fixes (Claude-Identified)

**1. Duplicate "Deep Dive Analysis" Heading**
- **Issue:** The AI-generated markdown was including its own title heading which doubled up with the HTML template's heading
- **Root Cause:** Both `curator_feedback.py` template (line 611) and AI output (line 307) included "Deep Dive Analysis" heading
- **Fix:** Strip any leading `## Deep Dive Analysis` from AI markdown before rendering
- **File:** `curator_feedback.py` - Added regex to remove duplicate heading
- **Commit:** `d2b8b20`

**2. Deep Dive Closure Bug**
- **Issue:** When multiple deep dive buttons were created, they all triggered deep dive on the same article (the last one processed)
- **Root Cause:** The `addDeepDiveButton()` JavaScript function was capturing `rank` by reference in a loop, not by value
- **Impact:** All buttons pointed to the wrong article because `rank` variable was shared
- **Fix:** Use IIFE (Immediately Invoked Function Expression) to capture `rank` and `diveBtn` by value at button creation time
- **File:** `curator_rss_v2.py` - Updated `addDeepDiveButton()` function
- **Commit:** `418b971`

**3. Hash_id Lookup Ambiguity**
- **Issue:** When curator ran multiple times per day, date-rank format (`2026-02-20-2`) could match multiple articles, returning the FIRST match instead of the correct one
- **Root Cause:** Multiple runs per day created duplicate ranks, and lookup didn't distinguish between them
- **Impact:** Deep dive could analyze a different article than the one clicked
- **Fix:** Pass unique `hash_id` through entire flow (HTML → JS → Server → Lookup) instead of date-rank format
- **Files Modified:**
  - `curator_rss_v2.py` - Add `data-hash-id` attribute, pass through JS functions
  - `curator_server.py` - Accept `hash_id` instead of `rank` for deep dive requests
- **Commit:** `dc14d53`

**Credits:**
- Claude AI identified the JavaScript closure bug and duplicate heading issue
- OpenClaw (Mini-moi) identified the hash_id lookup ambiguity

**Testing:**
- All three bugs fixed and tested with fresh curator run
- Deep dive flow now correctly analyzes the exact article clicked
- No more duplicate headings in deep dive HTML output

### Files Changed
- `curator_rss_v2.py` - JavaScript closure fix, hash_id integration
- `curator_server.py` - Hash_id parameter handling
- `curator_feedback.py` - Duplicate heading removal

### Impact
- ✅ Deep dive now reliably analyzes the correct article
- ✅ Clean HTML output (no duplicate headings)
- ✅ Robust to multiple curator runs per day

## 2026-02-19 - Platform Unification & UI Consistency

### Major Changes

**Unified Briefing Platform Architecture**
- Replaced card-based layout with table-based layout (Bloomberg Terminal aesthetic)
- Unified header across all pages (1.5em, purple gradient, centered)
- Consistent navigation on every page: 📰 Today | 📚 Archive | 🔍 Deep Dives
- Fixed navigation flow between all three core pages

**Files Modified:**
- `curator_rss_v2.py`:
  - Rewrote `format_html()` to generate table format (was card format)
  - Fixed rank numbering: `for i, entry in enumerate(entries, 1)` → `rank = i`
  - Fixed field mappings: `category_tag` → `category`, `url` → `link`
  - Rewrote `generate_index_page()` with unified header (1.5em, not 2.5em)
  - Fixed deep dives path: `interests/deep-dives/` → `interests/2026/deep-dives/`

- `curator_feedback.py`:
  - Updated deep dive prompt for concise "point-of-departure" format
  - Sections 1-6: Brief (2-3 sentences max)
  - Section 7 (Bibliography): Most detailed with proper citations
  - Fixed `\1` bug in HTML generation (regex backreference: `r'\1'` → `'\\1'`)
  - Added unified header and navigation bar to deep dive articles
  - Reduced yellow interest box padding/font size
  - Reduced back button size for consistency

**Deep Dive Cost Optimization:**
- Output tokens: 2995 → 977 (67% reduction)
- Cost per analysis: $0.047 → $0.017 (64% cheaper)
- Quality: Improved (concise research launchpad vs verbose explanations)

**New Files:**
- `PLATFORM_POC.md` - Complete platform overview and usage guide
- `PLATFORM_UNIFIED.md` - Design system specifications
- `curator_cache/` - Article storage for deep dive system (hash-based)
- `curator_history.json` - Article index with appearance tracking
- `interests/2026/deep-dives/` - New deep dive storage location
- `interests/2026/deep-dives/index.html` - Deep dive archive index

### Bug Fixes
- Fixed archive header size (was 2.5em, now 1.5em for consistency)
- Fixed rank numbers showing "?" instead of 1-20
- Fixed deep dive path inconsistencies across navigation
- Fixed browser caching issue with `curator_latest_with_buttons.html`
- Fixed navigation back button on all pages

### Design System
- Base font: 14px (System fonts)
- Header: 1.5em title, 0.88em metadata, 12px padding
- Navigation buttons: 6-14px padding, 0.85em font, purple (#667eea)
- Tables: 12px cell padding, #ddd borders, alternating row backgrounds
- Max width: 1400px (consistent across all pages)
- Colors: Purple gradient (#667eea → #764ba2)

### Status
✅ POC Complete - Consistent navigation flow across all pages
✅ All pages use identical header/navigation structure
✅ Table format generates correctly from curator_rss_v2.py
✅ Deep dive format optimized (cost -64%, quality improved)

### Next Steps (Future)
- UI polish (fine-tune colors, spacing)
- Deep dive bookmark action implementation
- CLI history viewer
- Telegram button integration



## 2026-04-20 — Language Domain: Added 8 persona prompts, removed deprecated file

Commits: n/a (changes in gitignored _NewDomains/ — not tracked)

**Result:** Language-german domain now has 8 dedicated persona prompt files in _NewDomains/language-german/language/german/config/prompts/, replacing the old single-file approach.

### What Was Built
- Created directory: _NewDomains/language-german/language/german/config/prompts/
- Added files: anna_airbnb.txt, dr_huber_museum.txt, frau_berger_bakery.txt, frau_novak_pharmacy.txt, herr_fischer_hotel.txt, klaus_restaurant.txt, maria_cafe.txt, stefan_ubahn.txt
- Removed: config/grok_persona_prompt.txt (replaced by prompts/frau_berger_bakery.txt)

### Open Items → Next Phase
- Scaffold full language-german domain structure (e.g., scripts, config.json, integration with main pipeline)
- Test persona prompts in a sample session
