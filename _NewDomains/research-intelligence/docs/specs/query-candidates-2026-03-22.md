# Query Candidates — Spec
**Date:** March 21, 2026
**Scope:** Capture suggested queries from observe output → review → promote to config

---

## What Gets Created

- `data/feedback/query_candidates.json`
- `agent/candidates.py` ← extract, list, promote CLI

---

## Flow

```
observe runs → Sonnet writes "what's missing" section
→ observe.py parses suggested queries via Haiku (NOT Sonnet) → appends to query_candidates.json
→ Robert reviews: python agent/candidates.py list
→ Promotes: python agent/candidates.py promote --id [id]
→ Writes to config.json session_searches for that topic
```

---

## Schema

```json
[
  {
    "id": "a3f2c891",
    "topic": "empire-landpower",
    "query": "Belt and Road infrastructure landpower geopolitics",
    "source_observation": "data/observations/empire-landpower-observe-2026-03-22.md",
    "status": "candidate",
    "timestamp": "2026-03-22T03:05:21"
  }
]
```

`status` values: `candidate` → `promoted` → `retired`

---

## Two Changes to `observe.py`

**Change 1 — Query extraction prompt (Haiku, not Sonnet)**

Add a Haiku call after the main Sonnet synthesis — lightweight, just parses the "what's missing" section:

```
"Extract 3-5 search queries from the missing sources section.
Return JSON array of strings only. No explanation."
```

**Change 2 — Write to query_candidates.json**

After extraction, hash each query string for the ID, append to `query_candidates.json` with status `candidate`. Dedup on hash before writing.

---

## `candidates.py` CLI

```bash
python agent/candidates.py list              # show all candidates by topic
python agent/candidates.py list --topic X    # filter by topic
python agent/candidates.py promote --id X    # write to config session_searches
python agent/candidates.py retire --id X     # mark retired, don't delete
```

Promote writes directly to `agent/config.json` under `session_searches[topic]`. No manual config editing.

---

## What Does NOT Change

- Main observe synthesis — untouched
- Feedback storage — untouched
- Session runner — untouched

---

## Cost Note

Extraction call is **Haiku**, not Sonnet. Negligible cost. Add to per-call breakdown in observe output:

```
Sonnet · 459 in / 639 out · $0.0110
Haiku  · 180 in / 95 out  · $0.0003
Total  · $0.0113
```

---

*March 21, 2026 | Claude.ai + OpenClaw + Robert*
*Build targets: observe.py (changes 1+2) + new agent/candidates.py*
