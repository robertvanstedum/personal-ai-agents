# Curator Roadmap

## Vision: Personalized Overnight Geopolitics Curator

An AI-powered system that learns what you care about and delivers a curated briefing of the most relevant geopolitics & finance articles every morning.

---

## Current State (MVP - Feb 9, 2026)

✅ **Mechanical RSS Curation**
- Fetches from 4 sources: ZeroHedge, The Big Picture, Fed On The Economy, Treasury MSPD
- Keyword-based relevance scoring (gold, debt, sanctions, etc.)
- Recency scoring (newer = better)
- Source diversity enforcement (prevents confirmation bias)
- Outputs top 20 articles to text file

**Run:** `python3 curator_rss.py`

**Results:** 
- 135 articles fetched
- Top 20 balanced across sources (6/5/5/4 distribution)
- Works, but dumb (keywords only)

---

## Phase 1: Smart AI Filtering

**Goal:** Replace keyword matching with LLM-based intelligence

### Model Strategy
- **Haiku** (cheap) → Bulk filtering: 200+ articles → 50 candidates
- **Sonnet** (quality) → Quality assessment: 50 → top 20
  - Relevance score
  - Challenge-factor (diverse viewpoints)
  - Quality of analysis

### Why Not Opus?
- Sonnet is plenty smart for curation
- Save Opus for deep-dive analysis you manually request

### Implementation
1. Add Anthropic API integration
2. Batch articles to Haiku for initial filter
3. Pass candidates to Sonnet for final ranking
4. Cost estimate: ~$0.50-1.00 per run (vs $0 now)

---

## Phase 2: Context-Aware Learning Loop

**Goal:** Curator learns what's on your mind and answers your questions

### The Learning Loop

```
Day 1 Evening:
- You ask agent: "Why is gold rising despite rate hikes?"
- Captured in Neo4j: "Robert interested in gold price drivers, rate dynamics"

Day 2 Overnight (2-3am):
- Curator reads Neo4j context graph
- Sees your recent questions/interests
- Prioritizes articles that address them
- Finds: Fed rate commentary, gold market analysis, central bank buying

Day 2 Morning (7am):
- Telegram briefing arrives
- Top articles directly answer your question
- Includes contrarian takes (avoid echo chamber)
- You read 3 articles, skip 17

Day 2 Evening:
- Selection patterns stored in Postgres
- Feedback loop: what you valued vs. skipped
- Refines future curation

Day 3 Overnight:
- Smarter about your preferences
- Learns you care more about fiscal dynamics than market noise
```

### Data Flow

**Input:**
1. **Neo4j decision traces** → What you value, your worldview, recent questions
2. **Conversation history** → "What's on my mind today"
3. **Selection history (Postgres)** → What you actually read vs. skipped

**Processing:**
- LLM analyzes each article against your context
- Scores: relevance + quality + challenge-factor
- Diversity enforcement: no echo chamber

**Output:**
- Curated briefing (top 20)
- Summaries for each (1-2 sentences)
- Highlights articles that answer your questions
- Flags contrarian viewpoints

### Implementation
1. Add Neo4j query function (read recent traces)
2. Add conversation context capture
3. Modify scoring to include `user_context` parameter
4. Use Sonnet to assess article relevance to context
5. Store selection feedback (which articles opened/saved)
6. Iterate scoring weights based on feedback

---

## Phase 3: Proactive Insights

**Goal:** Curator doesn't just filter—it synthesizes

### Features
- **Pattern detection:** "3 articles today mention debt ceiling—here's the trend"
- **Question generation:** "Based on your interest in X, you might want to explore Y"
- **Contradiction highlighting:** "ZeroHedge says X, Fed says opposite—here's the nuance"
- **Research assists:** Save selected articles to Postgres for future deep-dive

### Integration
- Telegram delivery (7am sharp)
- You reply to ask follow-ups
- Agent can pull full articles, summarize, compare sources
- "Add this to my research queue" → Postgres

---

## Technical Notes

### Cost Estimates
- **Phase 0 (MVP):** Free (no LLM calls)
- **Phase 1:** ~$0.50-1/day (Haiku + Sonnet filtering)
- **Phase 2:** ~$1-2/day (adds context analysis + synthesis)
- **Phase 3:** ~$2-3/day (synthesis + follow-up interactions)

Still way cheaper than the Gmail cleanup incident ($22 in one session).

### Why This Approach Works
1. **Batch processing** = efficient (not per-message costs)
2. **Runs overnight** = no latency concerns
3. **Isolated agent** = won't pollute main session context
4. **Learning loop** = gets better over time without constant retraining

### Scheduling (Phase 1+)
- OpenClaw cron job: 2-3am daily
- Spawns isolated agent with curator task
- Agent runs, saves results
- 7am: Delivers briefing via Telegram
- No human input needed—fully autonomous

---

## Why This Matters

You're building something that doesn't exist elsewhere:
- **Personalized** to your worldview (Neo4j traces)
- **Adaptive** to your current questions (context-aware)
- **Anti-echo-chamber** (diversity enforcement)
- **Learns** from your behavior (feedback loop)

Not just "here's the news." It's "here's what matters to you today, based on what you were thinking about yesterday."

That's the vision. 🧠
