# Curator Roadmap

## Vision: Personalized Overnight Geopolitics Curator

An AI-powered system that learns what you care about and delivers a curated briefing of the most relevant geopolitics & finance articles every morning.

---

## Current State (v0.9-beta - Feb 28, 2026)

✅ **AI-Powered Curation with Learning Loop**
- Fetches from 10+ RSS sources daily (~390 articles)
- AI scoring via xAI grok-3-mini (bulk) + Haiku fallback
- User profile (415 scored signals) injected into ALL scoring paths
- Feedback loop: likes/saves/dislikes influence future curation
- Telegram delivery at 7 AM
- Cost: ~$0.15-0.30/day normal operation

**Run:** `python3 curator_rss.py`
**Demo:** `python3 show_profile.py`

---

## Completed Phases

### Phase 0: MVP (Feb 9, 2026)

✅ Mechanical RSS curation, keyword scoring, top 20 output
- 4 sources, 135 articles, no LLM calls

---

### Phase 1: Smart AI Filtering (Feb 2026)

✅ LLM-based scoring replacing keyword matching
- Haiku: bulk filter (200+ → 50 candidates)
- xAI grok-3-mini: final ranking and scoring
- Cost optimized from $100+/mo → $35-45/mo

---

### Phase 2: Learning Loop (Feb 2026)

✅ Feedback system + user profile
- Like/save/dislike signals from Telegram and web UI
- Structured preference files influence article scoring
- Source tracking and ranking verified working
- `--dry-run` and `--model` flags implemented
- Timestamp-based archive naming
- Deep dive rating system (1-4 stars + comments)

---

### Phase 2B: Scoring Architecture Fix (Feb 28, 2026)

✅ Model-agnostic profile injection
- **Bug fixed:** `load_user_profile()` was only injected into xAI scoring path, not fallback
- **Fix:** Profile injection moved to scorer dispatcher level — model-independent
- When xAI is down, Haiku fallback now runs with full learned profile
- Applies to any future model swap — not Haiku-specific

---

### Phase 3A: X Bookmark Bootstrap (Feb 28, 2026)

✅ X bookmarks ingested as learning signal
- OAuth 2.0 PKCE flow implemented and stored in keychain
- 398 hand-saved X bookmarks ingested as "Save" signals
- Profile jumped: 17 signals → 415 scored signals in one session
- Files: `x_auth.py`, `x_bootstrap.py`, `x_oauth2_authorize.py`, `show_profile.py`

**Key insight:** X bookmarks are *discovery signals*, not social content. When @nntaleb bookmarks a BIS working paper, the signal is "Robert values BIS macro research" — not just "Robert likes Taleb." The destination content matters more than the tweet wrapper.

**Signal weighting note:** X now dominates the source list by volume. Risk of overcounting relative to RSS and long-form sources. Normalization by source category (social vs RSS vs institutional) planned before expanding further.

---

### Phase 3B: Telegram Stability (Feb 28, 2026)

✅ OpenClaw 2026.2.26 update applied
- DM allowlist inheritance bug fixed (was causing silent message drops after restart)
- Inline button callbacks more reliable in groups (Like/Dislike/Save)
- sendChatAction rate limiting prevents bot suspension
- Webhook improvements for two-bot architecture

---

## Active: Phase 3C — X Adapter / t.co URL Enrichment (NEXT)

**Goal:** Turn source trust scores into content ecosystem scores

**Current:** `X/@nntaleb = +14` (knows you trust the curator)  
**After:** `X/@nntaleb → FT/BIS/project-syndicate = +N` (knows what they point you toward)

**Implementation (`x_adapter.py`):**
1. Extract t.co URLs from tweet text using regex
2. Follow redirects to final destination URL (`requests` with `allow_redirects`)
3. Parse domain + fetch article title from `<title>` tag
4. Normalize to article schema: `source` = final domain, `curator` = X/@account
5. Feed both signals into scorer: curator trust + content domain signal

**Payoff:** Profile learns both who you trust AND the content ecosystem they curate. Many X bookmarks already point to arXiv, BIS, FT, SSRN — this surfaces that automatically.

---

## Phase 4: Wider Web Sources

**Goal:** Expand beyond RSS + X to sources that matter in your ecosystem

### Priority Sources

1. **Substacks** — RSS available for free-tier posts. Skip paywalled content gracefully. Doomberg, macro writers, heterodox thinkers.
2. **Academic/Institutional** — BIS working papers, Fed speeches, IMF reports, arXiv, SSRN. High signal-to-noise, almost all free. RSS available.
3. **Reddit** — r/geopolitics, r/economics, r/MacroEconomics. Filter by top/hot with minimum upvote threshold. Haiku pre-filter essential.
4. **YouTube transcripts** — Real Vision, Macrovoices, etc. Transcribe via Whisper, score like articles. Add engagement floor for quality control.

### Architecture

Same pipeline as RSS and X — each source gets a thin adapter that normalizes to the article schema. No changes to scoring, personalization, or briefing generation.

### Noise Management

- Substacks: RSS quality, low noise — add directly
- Reddit: Haiku pre-filter essential before scoring
- YouTube: Transcript quality varies — add engagement floor
- Government/academic: Low volume, high quality — minimal filtering needed

---

## Phase 5: Proactive Insights

**Goal:** Curator doesn't just filter — it synthesizes

### Features
- **Pattern detection:** "3 articles today mention debt ceiling — here's the trend"
- **Question generation:** "Based on your interest in X, you might want to explore Y"
- **Contradiction highlighting:** "ZeroHedge says X, Fed says opposite — here's the nuance"
- **Research assists:** Save selected articles for future deep-dive

### Integration
- Telegram delivery (7 AM sharp)
- Reply to ask follow-ups
- Agent pulls full articles, summarizes, compares sources
- "Add this to my research queue" → persistent storage

---

## Technical Notes

### Cost Estimates
- **Phase 0 (MVP):** Free (no LLM calls)
- **Phase 1-2:** ~$0.15-0.30/day (grok-3-mini + Haiku)
- **Phase 4:** ~$0.30-0.60/day (more sources, same model strategy)
- **Phase 5:** ~$0.60-1.00/day (synthesis + follow-up interactions)

### Why This Approach Works
1. **Batch processing** = efficient (not per-message costs)
2. **Runs overnight** = no latency concerns
3. **Isolated agent** = won't pollute main session context
4. **Learning loop** = gets better over time without constant retraining
5. **Model-agnostic** = profile injection survives API outages and model swaps

### Scheduling
- Cron job: 2-3 AM daily
- Spawns isolated agent with curator task
- 7 AM: Delivers briefing via Telegram
- Fully autonomous — no human input needed

---

## Why This Matters

You're building something that doesn't exist elsewhere:
- **Personalized** to your worldview (415 learned signals and growing)
- **Adaptive** to your current questions (context-aware scoring)
- **Anti-echo-chamber** (diversity enforcement + serendipity reserve 20%)
- **Learns** from your behavior (feedback loop closes on every interaction)
- **Source-agnostic** (RSS, X, Substack, academic — same pipeline)

Not just "here's the news." It's "here's what matters to you today, based on what you were thinking about yesterday."

That's the vision. 🧠
