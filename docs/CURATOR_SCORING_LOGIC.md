# Curator Scoring Logic
**Version:** 1.1
**Last updated:** 2026-05-27
**Owner:** Robert Van Stedum
**Code file:** `curator_rss_v2.py`, `curator_sources.json`

---

## Pipeline Overview

Every run processes articles through eight sequential stages.
Each stage modifies the score or eligibility of candidates.
The final 20 articles are selected by the diversity-aware selection loop.

```
FETCH → MERGE X → DROP FILTER → SCORE (Grok) → TRUST MULT
   → AGE PENALTY → X FILTER → SELECTION LOOP → TOP 20
```

---

## Stage 1 — Feed Fetch

**What it does:** Fetches up to 50 entries per RSS source.
arXiv q-fin is capped at 15 to prevent academic flooding.

**Current sources (23):**

| Group | Sources |
|-------|---------|
| Geo/security | Geopolitical Futures, War on the Rocks, Foreign Affairs, Just Security, Crisis Group |
| Finance/macro | ZeroHedge, The Big Picture, CEPR VoxEU, NY Fed Liberty Street, Naked Capitalism, Adam Tooze Chartbook, Fed On The Economy, Treasury MSPD |
| International news | Al Jazeera, Deutsche Welle, Spiegel International, The Duran, ProPublica, Antiwar.com |
| German | FAZ, Die Welt |
| Academic | arXiv q-fin |
| Regional | O Globo |

*Chatham House deferred — returns 403 on automated fetch as of 2026-05-27.*

---

## Stage 2 — X Bookmark Merge

**What it does:** Loads X bookmark signals from `curator_signals.json`
and merges them into the candidate pool. Applies blacklist before merge.

**Blacklisted accounts (never enter the pool):**

| Account | Reason |
|---------|--------|
| `X/@ThomasSowell` | Aggregator — not Sowell's account |
| `X/@zerohedge` | Redundant with ZeroHedge RSS feed |
| `X/@realMaalouf` | Anonymous political commentary |
| `X/@WarrenVsCCP` | Anonymous political commentary |
| `X/@A1Anduril` | Anonymous defense commentary |
| `X/@Myrmikan` | Bi-monthly email PDF — wrong format for pipeline |

---

## Stage 3 — Source Trust Drop Filter

**What it does:** Removes all entries from `drop`-tier domains before scoring.
Saves Grok API tokens by not scoring low-quality content.

**Current drop-tier domains:** `investing.com` (removed from FEEDS in v1.1 — this stage now a safety net)

---

## Stage 4 — Grok Scoring

**Model:** `grok-4-1-fast-reasoning` via xAI API
**Temperature:** 1.0
**Output:** Raw score 0–10 per article + category label

**Score guidance given to Grok:**

| Score | Meaning |
|-------|---------|
| 9–10 | Critical developments, major policy shifts, must-read analysis |
| 7–8 | Important trends, significant geopolitical/financial analysis |
| 5–6 | Relevant but not urgent, decent background |
| 3–4 | Tangential interest, minor relevance |
| 0–2 | Skip (noise, spam, off-topic) |

**Categories assigned by Grok:**
`geo_major` · `geo_other` · `monetary` · `fiscal` · `technology` · `other`

---

## Stage 5 — Source Trust Multipliers

**What it does:** Multiplies each article's score by its source domain's trust tier.
Applied after Grok scoring. Defined in `curator_sources.json`.

**Multipliers:**

| Tier | Multiplier | Examples |
|------|-----------|---------|
| `trusted` | **1.5×** | War on the Rocks, Foreign Affairs, CEPR VoxEU, Crisis Group, Al Jazeera, DW, Spiegel International, Adam Tooze, NY Fed |
| `neutral` | 1.0× | ZeroHedge, The Duran, Naked Capitalism, Geopolitical Futures |
| `deprioritize` | 0.5× | (none currently active) |
| `probationary` | 0.7× | Auto-discovered sources pending evaluation |

*Note: Al Jazeera, DW, Spiegel were upgraded from probationary → trusted in v1.1.*

---

## Stage 6 — Age Penalty

**What it does:** Multiplies each article's score by an age-based decay factor.
Two tiers: SLOW sources tolerate older content, FAST sources decay sharply.
X bookmarks (no published date) receive no age penalty.

**SLOW sources** (think tanks, academic, institutional, newsletters):
Articles up to 90 days can still contribute meaningfully.

| Age | Multiplier |
|-----|-----------|
| 0–30 days | 1.00 |
| 31–60 days | 0.85 |
| 61–90 days | 0.65 |
| 90+ days | 0.40 |

**FAST sources** (news, blogs, X posts — everything not in SLOW list):
Content older than 30 days is near-dead.

| Age | Multiplier |
|-----|-----------|
| 0–3 days | 1.00 |
| 4–7 days | 0.85 |
| 8–14 days | 0.65 |
| 15–30 days | 0.40 |
| 30+ days | 0.05 |

**SLOW source domains:**
`crisisgroup.org` · `chathamhouse.org` · `warontherocks.com` · `foreignaffairs.com` ·
`cepr.org` · `arxiv.org` · `stlouisfed.org` · `federalreserve.gov` · `treasurydirect.gov` ·
`newyorkfed.org` · `bis.org` · `adamtooze.substack.com` · `geopoliticalfutures.com` ·
`justsecurity.org` · `propublica.org`

---

## Stage 7 — X Post Score Filter

**What it does:** Removes X posts that score below the minimum threshold
after Grok scoring and all multipliers. Applied before the selection loop.

**Minimum score:** `3.0`

---

## Stage 8 — Selection Loop

**What it does:** Picks the top 20 articles using a diversity-aware
scoring formula. Runs in two phases:
- **Phase 1 (16 articles):** personalized — includes interest + priority boosts
- **Phase 2 (4 articles):** serendipity — base score + diversity only, no boosts

**Serendipity reserve:** 20% (4 of 20 articles)
Configurable via `~/.openclaw/workspace/curator_preferences.json`

### Final Score Formula

```
final_score = score
            − source_penalty
            − category_penalty
            + (interest_boost × age_multiplier)     ← v1.1: boost is age-scaled
            + (priorities_boost × age_multiplier)   ← v1.1: boost is age-scaled
```

### Source Diversity Penalty

Penalizes repeated picks from the same source within one briefing.
Quadratic — 2nd article from the same source is penalized 4× more than the 1st.

```
source_penalty = (source_count²) × 30 × diversity_weight
```
`diversity_weight = 0.3` (default)

Penalty per repeated pick from same source:
- 1st article: 0 penalty
- 2nd article: 2.7 penalty
- 3rd article: 10.8 penalty

### Category Diversity Penalty

Same logic, less aggressive. Allows some depth per topic.

```
category_penalty = (category_count²) × 15 × diversity_weight
```

Penalty per repeated pick from same category:
- 1st article: 0 penalty
- 2nd article: 1.35 penalty
- 3rd article: 5.4 penalty

### X Post Hard Cap

Maximum X bookmark posts per briefing: **4** (shared across Phase 1 + Phase 2).
Each `X/@handle` counts separately for source diversity, but the category cap
of 4 prevents X posts from collectively dominating regardless of score.

### Interest & Priority Boosts (Phase 1 only)

Boosts from past feedback (likes, saves) and active priorities.
**Both are scaled by the article's age_multiplier** (v1.1 change).
A 13d FAST article (age_mult=0.65) gets 65% of its normal boost.
Fresh articles get 100% boost. SLOW sources at <30d get 100%.

This prevents historical interest from resurfacing old content
above genuinely fresh articles with similar Grok scores.

---

## Version History

### v1.0 (original)
- Keyword-based mechanical scoring
- Source weights (Geopolitical Futures 1.4×, Big Picture 1.2×, etc.)
- Recency decay baked into mechanical score

### v1.0 + xAI (March 2026)
- Replaced mechanical scoring with Grok scoring
- Added user profile personalization to scoring prompt
- Added interest boost from feedback history
- Category + source diversity penalties added

### v1.1 (2026-05-27)
**Changes shipped in commit `e4220db`:**
- X post hard cap: 4 per briefing (was unlimited)
- X account blacklist: 6 accounts removed from candidate pool
- X post score floor: 3.0 minimum after all multipliers
- Trust tier upgrades: Al Jazeera, DW → trusted (was probationary / 0.7×)
- Spiegel International added to trust table as trusted
- 4 new RSS sources: Crisis Group, Adam Tooze Chartbook, NY Fed Liberty Street, Naked Capitalism
- Two-tier age penalty: SLOW vs FAST source decay curves
- 15 SLOW_SOURCE_DOMAINS defined
- Investing.com removed from FEEDS (was DROP tier)

**Fix shipped in commit `25fcb8a`:**
- Interest + priority boosts now scaled by age_multiplier
- Root cause: age penalty reduced base scores but historical boosts
  were not adjusted, letting old high-interest articles beat fresh content
- After fix: 13d ZeroHedge (boost=+10) final score 15.9 → 12.4
  vs fresh 1d article with lower Grok score: 16.0 (fresh wins)

---

## Tuning Reference

Quick-change parameters for future adjustments:

| Parameter | Location | Current Value | Purpose |
|-----------|----------|--------------|---------|
| `X_POST_CAP` | `curator_rss_v2.py` line ~148 | `4` | Max X posts per briefing |
| `X_POST_MIN_SCORE` | `curator_rss_v2.py` line ~152 | `3.0` | X post score floor |
| `diversity_weight` | `curate()` signature | `0.3` | Source/category penalty strength |
| `serendipity_reserve` | `curator_preferences.json` | `0.20` | % of briefing for non-personalized picks |
| `top_n` | `curate()` call | `20` | Total articles in briefing |
| Trust multiplier — trusted | `curator_rss_v2.py` | `1.5×` | Boost for explicit trusted sources |
| Trust multiplier — probationary | `curator_rss_v2.py` | `0.7×` | Penalty during calibration |
| FAST age at 14 days | `_compute_age_multiplier()` | `0.65` | Increase to decay faster |
| SLOW age at 60 days | `_compute_age_multiplier()` | `0.85` | Decrease to pressure think tank content |
