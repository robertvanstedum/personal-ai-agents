# The Thinking Record — Formal Specification

**Project:** Mini-moi / Research Intelligence Agent  
**Document type:** Foundational architecture spec  
**Date:** March 22, 2026  
**Status:** Ready for implementation review  
**Author:** Robert van Stedum + Claude.ai  
**Save to:** `_NewDomains/research-intelligence/docs/specs/thinking-record-2026-03-22.md`

---

## For New Readers — What This Document Is About

Mini-moi is a personal AI agent system built around a simple premise: the most valuable intelligence is not what the world knows, but what *you* think about what the world knows — and how that thinking evolves over time.

Most research tools solve the retrieval problem. They find sources, rank them, surface them. That problem is largely solved. What no tool solves is the *thinking* problem: capturing why you opened a question, how your understanding shifted as evidence came in, where you were wrong, where you were right but at the wrong scale, and what would cause you to revisit a conclusion years later.

This specification defines the **Thinking Record** — the layer of Mini-moi that turns a search and triage pipeline into a structured intellectual diary. It sits above the session/query layer and below the UI layer. It is the connective tissue between what the system finds and what you actually think.

The Thinking Record is designed with one long-term goal: **future data mining of your thoughts against content against new world events**. Everything stored here is structured for eventual migration to a graph database, where the relationships between your beliefs, the sources that shaped them, and the events that tested them become queryable over years.

This is not a feature. It is the point of the project.

---

## The Problem It Solves

The research agent as built through March 2026 has a gap. It knows:

- What to search (queries)
- What scored well (triage)
- What you saved (article signals)
- What patterns Sonnet found (observations)

It does not know:

- Why you opened this research thread
- What you believed before you saw the evidence
- How your thinking shifted as sessions ran
- Whether you concluded anything
- Whether you were right

Without this layer, the system is a sophisticated search tool. With it, the system becomes a record of how an informed person thinks through hard questions under uncertainty — and gets better at it over time.

---

## Core Concepts

### The Thread

A Thread is a named research question with a lifecycle. It is not the same as a topic. A topic (`gold-geopolitics`) can have multiple threads over time — one opened in 2026, another in 2028 after a different crisis. Each thread is a discrete intellectual episode with its own motivation, evidence base, and conclusion.

**Thread = Topic + Time + Your Thinking**

### The Motivation

The immutable record of why you opened the thread and what you believed before seeing evidence. Written once at thread creation. Never edited. This is your prior — explicit, datestamped, preserved.

Why immutable? Because intellectual honesty requires that you cannot revise what you believed before you saw the evidence. The calibration value of this system depends on the prior being genuine. If you can edit it after the fact, it becomes useless for self-assessment.

### The Annotation

A timestamped note you add at any point during the thread's life. One of four types:

| Type | Meaning | Behavior |
|------|---------|----------|
| `direction_shift` | I want the search to focus differently now | Injects into next session triage context and observe prompt silently |
| `reaction` | What I think about what I found | Logged, searchable, included in observe synthesis |
| `observation` | Something I noticed, not a direction change | Logged, searchable, available to observe on request |
| `wrap_up` | How I'm closing this thread | Triggers thread close, preserved as conclusion record |

Annotations can reference a session, an article, or nothing (freeform). The reference is optional but makes future data mining richer.

### The Influence Mechanism

When a `direction_shift` annotation exists, it influences the next research session and observe run silently. The influence is:

- **Logged** — every session records which annotations influenced it (`influenced_by: ["ann-003"]`)
- **Discoverable** — you can always ask "what shaped session gold-003?" and get a clear answer
- **Silent** — it does not appear in the Telegram message or session output unless you explicitly request it

This keeps the operational flow clean while maintaining full auditability. The system acts on your direction without narrating it constantly.

### The Lifecycle

```
ACTIVE → CLOSED (auto after 30 days inactivity, or manual wrap_up)
ACTIVE → RETIRED (manual, empty path — hidden from UI, preserved on disk)
CLOSED → [new thread, links back] (no reopen — start fresh with informed priors)
```

Retirement hides a thread from the dashboard and library without deleting it. It remains on disk, searchable, data-minable. "This went nowhere" is valid data.

Reopening is intentionally not supported. Instead, open a new thread that links to the old one in its motivation. This forces you to state explicitly what you learned from the previous thread and what you are now testing differently. The link chain becomes a longitudinal record of how your thinking about a question evolves across years.

---

## Data Model

### File Structure

```
data/threads/
  gold-geopolitics/
    thread.json          ← identity, status, lifecycle, links, motivation
    annotations.json     ← all notes in chronological order
  empire-landpower/
    thread.json
    annotations.json
  china-rise/
    thread.json
    annotations.json
```

### `thread.json` — full schema

```json
{
  "id": "gold-geopolitics-2026",
  "topic": "gold-geopolitics",
  "version": 1,
  "status": "active",
  "opened": "2026-03-22T18:00:00",
  "closed": null,
  "retired": false,
  "auto_close_days": 30,
  "last_session_date": "2026-03-22",
  "session_count": 1,
  "links_to": [],
  "links_from": [],
  "motivation": "Gold is dropping during Iran escalation which is counterintuitive. I want to understand whether this is a breakdown of the safe haven thesis or a mechanical/positioning issue — dollar strength overwhelming safe haven demand, or futures positioning. What does history say about gold during hegemonic stress periods?",
  "prior_belief": "Gold should rise during geopolitical stress. Safe haven demand should dominate.",
  "wrap_up": null,
  "retired_reason": null,
  "created_by": "robert",
  "schema_version": "1.0"
}
```

### `annotations.json` — full schema

```json
[
  {
    "id": "ann-001",
    "thread_id": "gold-geopolitics-2026",
    "timestamp": "2026-03-22T19:30:00",
    "type": "direction_shift",
    "level": "thread",
    "ref_session": null,
    "ref_article": null,
    "note": "Less interested in long-term gold cycles. Focus on proximate events around price drops — what triggered each major drop historically and whether there is a geopolitical pattern. The Iran timing is the specific puzzle.",
    "affects_next_session": true,
    "influenced_sessions": []
  },
  {
    "id": "ann-002",
    "thread_id": "gold-geopolitics-2026",
    "timestamp": "2026-03-22T20:15:00",
    "type": "reaction",
    "level": "article",
    "ref_session": "gold-001",
    "ref_article": "https://bis.org/...",
    "note": "BIS paper confirms dollar dominance as the suppressor mechanism but does not explain the Iran timing specifically. Need sources on futures market positioning during geopolitical shocks.",
    "affects_next_session": false,
    "influenced_sessions": []
  }
]
```

### Graph DB Mapping — Future Migration

This schema maps directly to Neo4j or similar without redesign:

```
(Thread {id, topic, status, motivation, prior_belief})
  -[:HAS_SESSION]→ (Session {id, date, cost, top_find})
  -[:HAS_ANNOTATION]→ (Annotation {id, type, note, timestamp})
  -[:LINKS_TO]→ (Thread)

(Session)
  -[:FOUND]→ (Article {url, title, score})
  -[:INFLUENCED_BY]→ (Annotation)

(Article)
  -[:CITED_BY]→ (Article)  ← OpenAlex citation chain, already collected
  -[:SAVED_BY]→ (SaveSignal {note, timestamp})

(Annotation)
  -[:REFERENCES]→ (Article)
  -[:REFERENCES]→ (Session)
```

Every relationship is already in the JSON data. Migration is a loader script, not a redesign. The citation chain from OpenAlex is already graph-shaped data stored flat — it migrates directly to `(Article)-[:CITED_BY]→(Article)` edges.

This is why the graph database finally makes sense for this project: the data is inherently relational. Your annotation references a session which found an article which cites another article which influenced your direction shift which shaped the next session. That's a graph. Querying it in flat JSON is possible but awkward. In Neo4j it's one traversal.

---

## Integration Points

### 1. Thread Creation — Where Motivation Is Captured

**Trigger:** New topic added to config, or explicitly via UI  
**UI:** Research Home page — before first session runs, prompt for motivation and prior belief. Simple textarea, required fields. Cannot run first session without completing them.  
**Alternative:** CLI — `python agent/threads.py create --topic gold-geopolitics`  
**Note:** Existing topics (empire-landpower, china-rise, gold-geopolitics) need retroactive thread.json files. Robert writes motivations from memory for session continuity.

### 2. Annotation Entry — Three Surfaces

**Surface A — Research Home (thread level)**  
Freeform textarea always visible. Type selector: direction_shift / reaction / observation. Submit writes to `annotations.json` immediately.

**Surface B — Session view (session level)**  
After reviewing session findings, annotation field pre-tagged with session ref. "What do you think about what this session found?"

**Surface C — Observe view (synthesis level)**  
After reading synthesis, annotation field. "Does this change your direction?" If yes, type auto-sets to direction_shift.

**All three write to the same `annotations.json`.** No separate storage per surface.

### 3. Direction Shift → Session Influence

When `research.py` runs, it checks `annotations.json` for unprocessed `direction_shift` entries (`influenced_sessions: []`). If found:

```python
# In research.py session start
direction_shifts = [a for a in annotations 
                   if a["type"] == "direction_shift" 
                   and a["affects_next_session"] 
                   and session_id not in a["influenced_sessions"]]

if direction_shifts:
    # Inject latest shift into triage context
    shift_context = direction_shifts[-1]["note"]
    # Add to triage prompt: "Current research focus: {shift_context}"
    # Log: mark this session as influenced_by annotation id
```

After session completes, update `influenced_sessions` in the annotation to include this session ID. Audit trail complete.

### 4. Direction Shift → Observe Influence

Same pattern. `observe.py` checks for direction shifts and prepends them to the Sonnet synthesis prompt:

```
"Researcher's current focus: {latest direction_shift note}
Synthesize the saved articles with this focus in mind, not just general patterns."
```

This makes observe output directly responsive to your steering without changing the underlying sources.

### 5. Wrap-up → Thread Close

When you submit a `wrap_up` annotation:

- `thread.json` status → `closed`, `closed` date set
- `wrap_up` field in thread.json populated with the note
- Thread moves to closed section in dashboard
- Auto-close timer disabled

### 6. Auto-Close

`run.py` or a separate maintenance script checks thread.json files daily. If `last_session_date` is more than `auto_close_days` ago and status is `active`, it fires a one-time prompt via Telegram:

```
[Mini-moi] Thread 'gold-geopolitics' has been inactive 30 days.
Add a closing note or it will close empty in 7 days.
Reply: /wrap gold-geopolitics [your note]
       /close gold-geopolitics (close empty)
       /extend gold-geopolitics (reset timer)
```

If no response in 7 days, closes empty. Wrap-up field stays null — that's valid data. "I lost interest" is a legitimate conclusion.

### 7. Deep Dive as Thread Opener

When Deep Dive is eventually built, it becomes the primary thread creation surface:

```
Daily briefing article → you flag for Deep Dive
  → Deep Dive form pre-fills motivation field:
    "From briefing: {article title}"
    "What triggered this: [your input]"
    "Prior belief: [your input]"
  → Creates thread.json
  → Seeds session_searches from article content
  → First research.py session runs with your motivation as context
```

This is the bridge between the daily briefing and the research agent. The Deep Dive is not a one-shot brief — it's a thread opener. The motivation captures why today's article triggered a research question.

---

## Build Plan

### New File: `agent/threads.py`

CLI and library for thread management. Importable by research.py, observe.py, research_routes.py.

```
python agent/threads.py create --topic gold-geopolitics
python agent/threads.py annotate --topic gold-geopolitics --type direction_shift --note "..."
python agent/threads.py annotate --topic gold-geopolitics --type reaction --ref-session gold-001 --note "..."
python agent/threads.py wrap-up --topic gold-geopolitics --note "..."
python agent/threads.py retire --topic gold-geopolitics --reason "..."
python agent/threads.py status --topic gold-geopolitics
python agent/threads.py list
```

Pydantic schemas for ThreadRecord and Annotation. Same pattern as feedback.py.

### Changes to `research.py`

- On session start: call `threads.check_direction_shifts(topic)` — returns latest unprocessed shift note or None
- If shift exists: inject into triage prompt context
- On session end: call `threads.mark_influenced(annotation_id, session_id)`

### Changes to `observe.py`

- Before Sonnet call: call `threads.get_active_direction(topic)` — returns latest direction_shift note
- If exists: prepend to synthesis prompt as "Researcher's current focus:"

### New Routes in `research_routes.py`

```
GET  /api/research/thread/{topic}           → return thread.json + recent annotations
POST /api/research/thread/{topic}/annotate  → write annotation
POST /api/research/thread/create            → create new thread
POST /api/research/thread/{topic}/retire    → retire thread
POST /api/research/thread/{topic}/wrap-up   → close with conclusion
```

### UI Changes

- **Research Home / Dashboard** — add motivation display + annotation input per topic card
- **Observe page** — add annotation field below synthesis output
- **Session view** (future) — annotation field per session

### Retroactive Thread Creation

Before first build: Robert writes motivations for existing three topics. Claude Code creates the thread.json files with those motivations. Existing sessions get linked retroactively via `last_session_date` and `session_count` from existing data.

---

## Options to Consider

**Option A — threads.py as pure library, no CLI**  
Build only the importable functions, no CLI. Annotation entry is UI-only. Simpler build, loses the terminal access that's useful during development and for OpenClaw to call directly.  
*Recommendation: build CLI first, UI second. CLI is faster to test and OpenClaw can use it.*

**Option B — Single annotations.json vs per-type files**  
Split into `direction_shifts.json`, `reactions.json` etc. Simpler per-type queries, more files to manage.  
*Recommendation: single file, filter by type. Consistent with existing feedback.py pattern.*

**Option C — Store motivation in config.json rather than thread.json**  
Keeps all research configuration in one place.  
*Recommendation: separate thread.json. Config is operational (queries, budgets). Thread is intellectual (motivation, thinking). They should not be in the same file — different lifecycle, different purpose, different future DB destination.*

**Option D — Defer graph DB migration path, build flat only**  
Don't design for graph migration now, just make it work.  
*Recommendation: reject. The schema cost of being graph-ready is near zero — just use the field names and relationship structure above. The migration payoff is high. This decision was already made for the main project and holds here.*

---

## What This Enables Over Time

**Year 1:** You have 10-15 closed threads across 4-5 topics. Each has a motivation, a session history, annotations tracking how your thinking shifted, and a wrap-up conclusion. Some are right. Some are wrong. Some are "right but wrong scale."

**Year 2:** A new geopolitical event touches a topic you researched in Year 1. You open a new thread, link to the old one. The motivation field forces you to state what you learned last time. You're not starting from zero — you're starting from a documented prior with evidence.

**Year 3:** You have enough threads to query patterns. Which topic areas produce the most direction shifts (hardest to call)? Where are your priors consistently wrong? Which sources tend to change your mind? Which session query patterns produce the highest-scored candidates?

In Neo4j:
```cypher
MATCH (a:Annotation {type: "direction_shift"})-[:REFERENCES]->(s:Session)
WHERE s.topic = "gold-geopolitics"
RETURN a.note, s.date, s.top_find
ORDER BY s.date
```

That query tells you every time you changed direction on gold, what session triggered it, and what the top find was that day. That's your intellectual history on a topic made queryable.

**The portfolio value:** You're not showing a search tool. You're showing a system that captures how expert thinking evolves against evidence over time. That's a different category of work.

---

## Implementation Sequence

1. **Robert writes motivations** for empire-landpower, china-rise, gold-geopolitics (15 minutes, freeform)
2. **Claude Code** builds `agent/threads.py` — Pydantic schemas + CLI + read/write functions
3. **Claude Code** creates retroactive `thread.json` files for three existing topics using Robert's motivations
4. **Claude Code** updates `research.py` — direction shift injection at session start
5. **Claude Code** updates `observe.py` — direction shift injection at synthesis prompt
6. **OpenClaw** updates spec files and MEMORY.md
7. **Robert** runs one full session on gold-geopolitics with a direction shift annotation to validate end-to-end
8. **Claude Code** builds annotation input into dashboard UI (Surface A)
9. **Claude Code** builds annotation input into observe UI (Surface C)
10. **Defer** — session-level annotation UI (Surface B) until session view is designed

---

## Files Created / Modified

| File | Change |
|------|--------|
| `agent/threads.py` | New — thread management library + CLI |
| `data/threads/empire-landpower/thread.json` | New |
| `data/threads/empire-landpower/annotations.json` | New (empty) |
| `data/threads/china-rise/thread.json` | New |
| `data/threads/china-rise/annotations.json` | New (empty) |
| `data/threads/gold-geopolitics/thread.json` | New |
| `data/threads/gold-geopolitics/annotations.json` | New (empty) |
| `agent/research.py` | Add direction shift check at session start |
| `agent/observe.py` | Add direction shift injection to synthesis prompt |
| `research_routes.py` | 5 new thread API routes |
| `web/dashboard.html` | Add motivation display + annotation input |
| `web/observe.html` | Add annotation field below synthesis |

---

*This document is the foundational architecture spec for the Thinking Record layer.*  
*All future UI design, session flow design, and Deep Dive integration should reference this document.*  
*Graph DB migration design should reference the data model section.*  
*Full history and session context: Claude.ai session March 22, 2026.*
