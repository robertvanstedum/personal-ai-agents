# Research Expansion Spec — 2026-03-22

## New Topic Query Sets

### china-rise
Extends the Arrighi/hegemony transition work already in empire-landpower. Focus on China as the specific case — not generic \"China rising\" news but the scholarly debate about whether this transition follows historical patterns or breaks them.

```json
\"china-rise\": [
  \"China hegemonic transition Arrighi accumulation cycles\",
  \"BRI Belt Road infrastructure capital export hegemony\",
  \"yuan internationalization dollar system challenge\",
  \"Chinese IR theory tianxia tributary system Yan Xuetong\",
  \"Xi Jinping political economy state capitalism accumulation\",
  \"China rare earth supply chain leverage geopolitics\",
  \"AIIB Asian Infrastructure Investment Bank Bretton Woods\",
  \"China Russia strategic partnership multipolarity IR theory\"
]
```

**Institution searches to add:**
```json
\"china-rise\": [
  \"China hegemony transition site:carnegieendowment.org\",
  \"BRI infrastructure finance site:chathamhouse.org\",
  \"Chinese IR theory site:csstoday.net\"
]
```

### gold-geopolitics
The conundrum: gold dropping during Iran war escalation. The research angle is the longer historical view — gold's role in hegemonic transitions, the dollar-gold relationship, and whether the traditional safe haven thesis is breaking down or being overwhelmed by other forces.

```json
\"gold-geopolitics\": [
  \"gold dollar system Bretton Woods collapse history\",
  \"gold safe haven thesis breakdown dollar strength\",
  \"Arrighi gold financialization hegemonic transition\",
  \"gold price war geopolitics historical correlation\",
  \"central bank gold reserves de-dollarization BRICS\",
  \"gold futures positioning mechanics safe haven paradox\",
  \"Nixon shock gold window closure long term consequences\",
  \"gold miners historical returns war inflation cycles\"
]
```

**Institution searches to add:**
```json
\"gold-geopolitics\": [
  \"gold dollar hegemony site:bis.org\",
  \"central bank gold reserves site:imf.org\",
  \"gold geopolitics site:chathamhouse.org\"
]
```

---

## Research Dashboard Spec

**File:** `web/dashboard.html`  
**Route:** `GET /research/dashboard` (add to `research_routes.py`)  
**Purpose:** Single-page overview of all active research topics. Replaces the need to check each topic separately. Entry point for the research section.

### Layout
```
[dashboard] [observe] [candidates] [save] [library] ← nav bar, dashboard added

Research Intelligence
Last run: empire-landpower · today · $0.012

┌─────────────────────────────────────────────────────────────┐
│ empire-landpower 8 sessions · 3 saved · 5 candidates│
│ Last run: today · Top find: Arrighi systemic cycles... │
│ [Run Session] [Observe] [Candidates] │
├─────────────────────────────────────────────────────────────┤
│ china-rise 0 sessions · 0 saved · 0 candidates│
│ Last run: never │
│ [Run Session] [Observe] [Candidates] │
├─────────────────────────────────────────────────────────────┤
│ gold-geopolitics 0 sessions · 0 saved · 0 candidates│
│ Last run: never │
│ [Run Session] [Observe] [Candidates] │
└─────────────────────────────────────────────────────────────┘

Pilot budget: $0.08 of $20.00 used ████░░░░░░░░░░░░░░░░ 0.4%
```

### What it reads — all existing data, no new backend

| Data point | Source |
|---|---|
| Session count | Count `topics/{topic}/` session files |
| Saved count | Filter `data/feedback/article_signals.json` by topic sessions |
| Candidates count | Filter `query_candidates.json` by topic + status=candidate |
| Last run | Most recent session file timestamp |
| Top find | Last session's top-scored source title |
| Budget used | `run.py` ledger or sum of session costs |

**One new API endpoint:**
```
GET /api/research/dashboard 
→ returns array of topic summaries, reads existing JSON files
```

### Behavior
- **Auto-refreshes** every 60 seconds if a session is running
- **[Run Session]** — links to Telegram command instructions or triggers via API (defer the trigger for now, just show the command)
- **[Observe]** — links to `observe.html?topic=X` pre-filled
- **[Candidates]** — links to `candidates.html?topic=X` pre-filled
- **Budget bar** — visual progress against $20 pilot budget, color shifts yellow at $15, red at $18

### Files touched

| File | Change |
|---|---|
| `research_routes.py` | 2 new routes: `GET /research/dashboard` + `GET /api/research/dashboard` |
| `web/dashboard.html` | New |
| `web/observe.html` | Add dashboard to nav bar |
| `web/candidates.html` | Add dashboard to nav bar |
| `web/save.html` | Add dashboard to nav bar |
| `agent/config.json` | Add `china-rise` and `gold-geopolitics` to `session_searches` and `institution_searches` |

### Commit messages
```
feat(research): add china-rise + gold-geopolitics topics to config
feat(research): dashboard page — topic overview, session counts, budget progress
```

Pass the config changes to Claude Code first (simple, no UI) — run a session on china-rise to seed real data, then build the dashboard against live content rather than empty state. The dashboard will be more useful to verify when it has something to show.
