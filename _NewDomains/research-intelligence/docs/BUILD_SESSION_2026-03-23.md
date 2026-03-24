# Build Session — 2026-03-23
**Branch:** poc/research-source-quality (research-intelligence repo)
**Author:** Robert + Claude Code
**Status:** Complete — regression passing

---

## What Was Built

### Thinking Record — threads.py (primary build)
The Thinking Record layer exists in code. Spec: `docs/specs/thinking-record-2026-03-22.md`.

**`agent/threads.py`** — full library + CLI
- `ThreadRecord` + `Annotation` Pydantic v2 schemas
- `load_thread` / `save_thread` / `load_annotations` / `save_annotations`
- `check_direction_shifts(topic)` — returns latest unprocessed direction_shift or None
- `get_active_direction(topic)` — alias for observe.py use
- `mark_influenced(topic, annotation_id, session_id)` — audit trail after session consumes shift
- CLI: `create`, `annotate`, `wrap-up`, `retire`, `status`, `list`
- Session count populated from `topics/{topic}/` directory (authoritative source)

**Thread data files created:**
- `data/threads/gold-geopolitics/` — full motivation + prior_belief (Robert's text)
- `data/threads/empire-landpower/` — placeholder, motivation TBW
- `data/threads/china-rise/` — placeholder, motivation TBW

**Direction shift injection:**
- `research.py` — checks `check_direction_shifts(topic)` at session start, injects note into every `build_triage_prompt()` call, marks shift consumed at session end
- `observe.py` — checks `get_active_direction(topic)` before Sonnet call, prepends focus to synthesis prompt
- Dual import path fix: `from agent.threads` (package) with fallback `from threads` (direct script run)

**5 API routes in `research_routes.py`:**
- `GET  /api/research/thread/<topic>` — thread + recent annotations
- `POST /api/research/thread/<topic>/annotate` — write annotation
- `POST /api/research/thread/create` — create thread
- `POST /api/research/thread/<topic>/retire` — retire thread
- `POST /api/research/thread/<topic>/wrap-up` — close with conclusion

---

### Bugs Closed

**BUG-005** — Session findings "Research Question" hardcoded "Kotkin / Empire & Land Power"
- Fix: replaced with dynamic `topic` variable + `len(triage_targets)` count

**BUG-006** — Translation firing on English sources (Ollama misclassifying `anglophone_origin`)
- Fix: language guard added — skip translation if `language == "english"` regardless of `anglophone_origin`
- Follow-on: removed `anglophone_origin` entirely from triage prompt schema, response parsing, candidate dicts, and sources-candidates table. Language field is now the sole translation gate.

---

### Regression Results — gold-005
All checks passed:
- Direction shift `ann-001` picked up, injected into triage, consumed at session end ✓
- Translation skipped — no non-English source scored ≥ 4 ✓
- Citation chase skipped — nothing scored ≥ 4 ✓
- Cost $0.00 — pure Ollama ✓
- Telegram arriving on RVSopenbot, full titles, no truncation ✓

Note: `ann-001` firing on gold-005 (not gold-004) was expected — ann-001 predated the
direction shift wiring and was never marked consumed. Both ann-001 and ann-002 are now
consumed. Thread is clean.

---

## Commits on poc/research-source-quality (research-intelligence repo)

| Hash | Description |
|------|-------------|
| `3582865` | fix: BUG-005 — dynamic topic label in session findings |
| `9b1501b` | feat(threads): threads.py library + CLI + retroactive thread data files |
| `c481501` | feat(threads): inject direction shifts into observe.py synthesis prompt |
| `5297688` | feat(threads): inject direction shifts into research.py triage context |
| `bd91cde` | fix: remove truncation on Telegram thread titles and explanation field |
| `47315ba` | fix: route research session Telegram to RVSopenbot via research_chat_id |
| `b4bd4af` | feat(research): cost-arch fix + institution searches + citation graph chase |
| `26acee9` | fix: BUG-006 — skip translation when language is English |
| `3fa33ca` | fix: remove anglophone_origin — unreliable from small triage model |
| `2cea5d8` | fix: dual import path for threads.py in research.py and observe.py |

## Commits on poc/research-source-quality (personal-ai-agents repo)

| Hash | Description |
|------|-------------|
| `205d55e` | feat(threads): add 5 thread API routes to research_routes.py |

---

## Open Items for Tomorrow

1. **New gold queries** — futures mechanics, dollar index correlation, proximate event focus.
   Current queries are pulling the same Bretton Woods pool repeatedly. Need:
   - gold futures positioning during geopolitical shock events
   - dollar index vs gold price correlation breakdown
   - historical gold price drops during war escalation — proximate triggers
   - central bank gold selling episodes and mechanics

2. **Run gold-006** with new queries, save strong candidates, run observe

3. **Dashboard UI pass** — visual polish deferred from yesterday:
   - Nav style to match observe.html inline button style
   - Topic card left border color per topic
   - Budget bar contrast
   - Remove dropdown arrow from Run Session

4. **empire-landpower and china-rise motivations** — Robert writes when ready,
   then update thread.json files via CLI:
   `python agent/threads.py create --topic empire-landpower` (will prompt for input)

5. **session-log.md → session-log.json migration** — logged as future backlog item

---

## Architecture State

The research pipeline now has three active layers:

```
Thread (motivation + prior + direction shifts)
    ↓ injects into
Session (research.py — search + triage + cost)
    ↓ saves to
Signal store (article_signals.json)
    ↓ synthesised by
Observe (observe.py — Sonnet synthesis, direction-steered)
    ↓ extracts to
Query candidates → promoted into config → next session
```

Direction shifts flow through research.py and observe.py automatically.
Annotation trail is auditable — `influenced_sessions` on each shift.

*Session closed: 2026-03-23*
*Next session: 2026-03-24*
