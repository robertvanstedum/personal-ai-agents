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
- \`--dry-run\` and \`--model\` flags implemented
- Timestamp-based archive naming
- Deep dive rating system (1-4 stars + comments)

---

### Phase 2B: Scoring Architecture Fix (Feb 28, 2026)

✅ Model-agnostic profile injection
- **Bug fixed:** \`load_user_profile()\` was only injected into xAI scoring path, not fallback
- **Fix:** Profile injection moved to scorer dispatcher level — model-independent
- When xAI is down, Haiku fallback now runs with full learned profile
- Applies to any future model swap — not Haiku-specific

---

### Phase 3A: X Bookmark Bootstrap (Feb 28, 2026)

✅ X bookmarks ingested as learning signal
- OAuth 2.0 PKCE flow implemented and stored in keychain
- 398 hand-saved X bookmarks ingested as "Save" signals
- Profile jumped: 17 signals → 415 scored signals in one session
- Files: \`x_auth.py\`, \`x_bootstrap.py\`, \`x_oauth2_authorize.py\`, \`show_profile.py\`

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

## Active: Phase 3C — Domain-Scoped Content Signals (In Progress - Mar 1, 2026)

**Goal:** Extract content domain signals from X bookmarks, scoped by knowledge domain

**Architecture:**
- **Shared config:** \`curator_config.py\` — single source of truth for domain names
- **Archive import:** \`x_import_archive.py\` — one-time load from Twitter archive
- **Incremental updates:** \`x_adapter.py\` — weekly/monthly API pulls for new bookmarks
- **Nested storage:** \`domain_signals[domain][url]\` in \`curator_preferences.json\`

**Implementation (Phase 3C-alpha - Mar 1-4, 2026):**
1. Create \`curator_config.py\` with canonical domain names + \`ACTIVE_DOMAIN\`
2. Build \`x_import_archive.py\` to parse Twitter archive \`bookmarks.js\`
3. Map X folders → domains via \`KNOWN_FOLDERS\` (user-provided folder IDs)
4. Fetch tweet entities via Twitter API (batch 100), extract domains from \`expanded_url\`
5. Write to \`domain_signals["Finance and Geopolitics"][domain]\` (domain-scoped)
6. Update \`curator_rss_v2.py\` to read \`domain_signals[ACTIVE_DOMAIN]\` only
7. Test Tuesday (Mar 4) with real archive data

**Current State (Mar 1):**
- ✅ Config created, imports working
- ✅ Archive parser skeleton complete
- ✅ Curator reads domain-scoped signals
- ⏳ Awaiting Tuesday archive import for full test

**Payoff:** 
- Profile learns content ecosystem (ft.com, arxiv.org, bis.org) from trusted curators
- Clean separation for future multi-domain curation
- 398 historical bookmarks become domain-scoped training data

---

## Phase 3D: User-Driven Domain Tagging (Future)

**Goal:** Capture user feedback when articles fit multiple domains

**Use Case:**
ZeroHedge publishes geopolitics AND health/science articles. User saves an article in Finance briefing but tags it "Also: Health and Science." Future health articles from ZeroHedge get boosted in Health curator.

**Features:**
- Tag button in web UI: \`[🏷️ Tag Domain ▼] → Health | Tech | Language\`
- Telegram inline buttons: \`🏷️ Also: Health | Tech | Language\`
- Store multi-domain signals in feedback_history

**Feedback storage schema:**
\`\`\`json
"feedback_history": {
  "article_hash_xyz": {
    "action": "save",
    "primary_domain": "Finance and Geopolitics",
    "also_relevant_for": ["Health and Science"],
    "tagged_by": "user",
    "tag_source": "web_ui",
    "timestamp": "2026-03-01T15:26:00Z"
  }
}
\`\`\`

**Why \`tagged_by\` + \`tag_source\` fields matter:**
When you eventually build a domain classifier, you'll want to distinguish explicit user tags from inferred tags. Keeps training data clean.

**Implementation phases:**
- **Phase 3D-alpha:** UI button + storage (30 min) — build when touching web UI for multi-domain
- **Phase 3D-beta:** Domain affinity scoring (90 min) — \`source_domain_affinity\` in learned_patterns
- **Phase 5 integration:** Content-based routing when multi-domain launches

**Deferred until:** After Phase 3C archive import working, before launching domain 2

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
