# Feature: Deep Dive Rating & Feedback System

## Status: Planned

## Added: February 21, 2026

## Overview

A rating system for deep dive analyses that creates a feedback loop to improve future deep dive quality and article selection over time.

## Strategic Purpose

This feature demonstrates closed-loop AI system design — where user feedback continuously improves AI output quality without manual prompt rewriting. A core pattern for personal AI systems.

## Rating Model

- **No rating** — acceptable, no action taken
- **1 star** — poor quality, negative signal
- **2 stars** — mediocre
- **3 stars** — good
- **4 stars** — excellent, reference quality

Optional short comment at any rating level.

## Data Storage

Ratings stored in `interests/ratings.json`:

```json
{
  "hash_id": "a19c5",
  "stars": 4,
  "your_comment": "good contrarian angle, specific mechanisms",
  "ai_analysis": {
    "strengths": ["specific policy data", "contrarian view"],
    "weaknesses": [],
    "themes": ["geopolitics", "trade policy"]
  },
  "rated_at": "2026-02-21"
}
```

## Feedback Loop (Two-Sided)

**Positive signals (3-4 stars)** → what to do more of  
**Negative signals (1-2 stars)** → what to avoid

Both feed into future deep dive prompts as explicit guidance.

## Implementation Phases

### Phase 1: Rating UI (immediate)
- Star widget on each deep dive archive row
- Optional comment field
- POST rating to curator_server.py
- Save to ratings.json

### Phase 2: Backend Analysis (after Phase 1)
- On any rated dive, trigger small AI call (Haiku)
- Extract themes, strengths, weaknesses automatically
- Append to ratings.json entry
- Cost: ~$0.01 per rated dive

### Phase 3: Prompt Injection (after Phase 2)
- curator_feedback.py reads ratings.json before generating
- Injects two-sided guidance into deep dive prompt
- curator_rss_v2.py boosts score for topics/sources with high-rated history

## Why This Matters

Most AI systems treat each interaction independently. This creates a personal taste model that compounds over time — the system gets measurably better the more you use it.
