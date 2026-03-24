# POC Feedback Loop — Spec
**Date:** March 21, 2026
**Status:** Ready for morning session
**Scope:** Feedback capture → backend storage → AI observation layer

---

## What We're Building

Close the loop between research sessions and query generation. Robert reads findings, signals quality, system learns and adapts. AI observation runs on request, not automatically.

---

## Three Components

### Component 1 — Feedback Capture

**Where:** Telegram reply (primary) + simple HTML form (secondary, no UI investment)

**Signals:**
- `save` → mark article as high value
- `drop [domain]` → blacklist domain from future sessions
- `redirect [topic]` → replace a query thread next session
- `good` → positive signal on session overall
- `weak` → session quality low, adjust query set
- `note [text]` → freeform observation, stored as annotation

Telegram bot already exists — add a reply handler that parses these commands and writes to storage. HTML form is a single text field + submit, no styling needed. Swagger endpoint acceptable alternative.

---

### Component 2 — Backend Storage

Keep it simple — JSON for now, Pydantic for schema consistency before any DB migration.

**Three files:**
```
data/feedback/
  article_signals.json    # per-article save/drop/note signals
  session_signals.json    # per-session good/weak signals
  query_performance.json  # query string → score history → weight
```

**Article signal schema:**
```json
{
  "url": "...",
  "title": "...",
  "session_id": "005",
  "signal": "save",
  "note": "optional freeform",
  "timestamp": "2026-03-22T07:00:00"
}
```

**Query performance schema:**
```json
{
  "query": "Arrighi Long Twentieth Century...",
  "sessions_used": ["004", "005"],
  "avg_score": 3.8,
  "high_value_hits": 2,
  "weight": 1.4
}
```

Weight starts at 1.0, increments on saves, decrements on weak signals. Query generator reads weights when building next session's search list.

---

### Component 3 — AI Observation Layer

**Trigger:** Robert requests explicitly — Telegram command or HTML button

**Commands:**
- `observe [topic]` → synthesize findings across sessions on that thread
- `essay [topic]` → longer form, pulls all saved articles on thread
- `compare [topic1] [topic2]` → contrast two research threads
- `status` → session health summary, query weight table

**Model:** Sonnet — this is the one place it earns its cost, triggered not pushed

**What it reads:** saved articles + session findings for the requested thread only — bounded context, not full library scan

**Output:** Markdown file written to `data/observations/` + summary pushed to Telegram

---

## What Does NOT Change

- `research.py` — already updated per cost-architecture-fix spec
- Session runner and cron — already fixed (commit `d778fdf`)
- HTML reader — working, leave it
- Ollama triage — working at $0.00, leave it

---

## Build Sequence for Morning

1. Telegram reply handler + signal parser
2. Three JSON storage files + Pydantic schemas
3. Query weight reader integrated into `session_searches` generation
4. AI observation trigger — `observe` command → Sonnet synthesis → markdown output + Telegram summary

Stop before any UI investment beyond what's needed to test the flow.

---

## Open Questions to Revisit

- What fields are available in the scored results object for richer `top_find_sentence` synthesis (flagged from today)
- Essay cadence — periodic auto-generation vs pure on-request (leave as on-request for now)
- When to migrate from JSON to DB — after functional completeness, Pydantic schemas make this straightforward

---

*March 21, 2026 | Claude.ai + OpenClaw + Robert*
*Hand to OpenClaw at start of morning session*
