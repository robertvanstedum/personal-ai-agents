---

## Deep Dive → Research Inbox Flow
*Captured: March 26, 2026*

### What Changed

Deep Dive was a static report. It is now a launching pad.

The Daily briefing side stays static — quick read, no interaction needed. On the Research side, Deep Dive bibliography items become actionable: clickable sources, promotable to Research candidates, feedable into new or existing research threads.

### Source Link Strategy

| Source type | Link target |
|---|---|
| URL present | Direct link, open in new tab |
| Academic paper, no URL | Google Scholar search on title |
| DOI present | doi.org direct link |
| Book | Google Books primary, Amazon fallback |
| Subscription / no link possible | "Library" tag, no fake link |

### Research Inbox

Deep Dive bibliography items can be marked "Add to Research" with one click. They land in an **unassigned inbox** in the Research → Queries tab — no topic required at point of capture. Topic assignment happens later on the Research side, or never. Not everything needs to become a thread.

```
Deep Dive bibliography item
    → "Add to Research" button (one click, no modal)
    → candidates.json { topic: null, status: "unassigned" }
    → Research → Queries tab → unassigned bucket at top
    → assign to existing topic, create new topic, or leave
```

### Design Principle: Loose by Default

The inbox is intentionally loose. Premature taxonomy is worse than no taxonomy — the graph layer (v2.0) will find clusters better from natural language than from forced categories. Let things accumulate. Some will never get researched. Others will grow and link together over time. That's fine.

---

## v2.0 Intelligence Layer — Graph + Local Reasoning
*Captured: March 26, 2026*

### The Big Picture

Everything written today — sessions, observations, threads, candidates, notes, deep dives — is latent graph data. The JSON files are nodes and edges waiting to be connected. Neo4j + a local LLM (Ollama) make that graph queryable and generative without burning cloud API credits on background reasoning.

The v2.0 layer doesn't replace the current pipeline. It runs alongside it, reading what the pipeline writes, and surfacing what you can't see by looking at one session at a time.

```
Current pipeline (v1.x):
  Sources → curator_rss_v2.py → curator_latest.json
  Research sessions → .md files → observe.py → synthesis
  User signals → like/save/dislike → Grok scoring

v2.0 layer (reads everything, writes suggestions):
  All of the above
    → Neo4j graph (concepts, authors, citations, threads, signals)
    → Ollama local LLM (persistent background reasoning)
    → Surfaces: latent connections, suggested paths, blind spots
```

### What the Graph Knows

Every piece of data already being written maps naturally to a graph node or edge:

| Data | Node | Edge |
|---|---|---|
| Research session | Session | BELONGS_TO topic, CITES source |
| Saved source | Source | CITED_BY session, AUTHORED_BY person |
| Observation synthesis | Observation | SYNTHESIZES sessions, IDENTIFIES gap |
| Thread note / direction shift | Annotation | ATTACHED_TO source or session |
| Deep Dive | DeepDive | SPAWNED_FROM article, REFERENCES source |
| Briefing like/save/dislike | Signal | FROM_USER, ON_ARTICLE |
| Research candidate | Candidate | PROMOTED_FROM DeepDive or session |

### What the Local LLM Does

Ollama runs scheduled background jobs — not on every user action, not burning Sonnet credits. Three initial jobs:

**1. Cluster detection (weekly)**
Scans all sessions and observations across topics. Finds concepts appearing in multiple threads that haven't been explicitly connected. Example output: "empire-landpower and china-rise both engage Arrighi's accumulation cycles across 8 sessions — no observation has synthesized across both threads."

**2. Gap identification (after each observe run)**
Reads the latest observation's "missing piece" section (which Sonnet already writes) and checks whether any existing session, saved source, or candidate already addresses it. Surfaces matches the user hasn't noticed.

**3. Path suggestion (on demand or weekly)**
Given the current state of a thread, suggests 2-3 directions the research hasn't gone. Not a search query generator — a reasoning step. "You've covered the macro pattern (Arrighi), the structural critique (Martins), the 1971 pivot. You haven't touched the operational mechanics. Dollar plumbing is the missing layer."

### Why Ollama, Not Sonnet

- Background jobs should not cost money per run
- Persistent reasoning across sessions requires a model that can hold context cheaply
- Privacy: the graph contains your full intellectual history — keep it local
- Sonnet stays reserved for: deep dives, observe synthesis, ask-layer responses — tasks where quality matters and cost per run is justified

### Migration Path — No Big Bang

The graph layer activates incrementally. Nothing breaks, nothing migrates forcibly.

**Phase 1 — Parallel write (no queries yet)**
Neo4j ingestion pipeline reads existing JSON files and builds the graph. Runs nightly alongside the existing pipeline. No UI changes. Validate that the graph is being populated correctly.

**Phase 2 — Read-only surfaces**
New UI panel: "Connections" — shows what the graph found. Latent links, cross-topic overlaps, citation clusters. Read-only. No actions yet.

**Phase 3 — Suggestions feed into workflow**
Graph suggestions appear as candidates in the Research inbox. User promotes or retires them like any other candidate. The loop closes.

**Phase 4 — Ollama background jobs active**
Scheduled cluster detection, gap identification, path suggestion. Results surface in the Connections panel and as annotations on existing threads.

### Schema Decisions to Defer

Do not pre-optimize the Neo4j schema now. Let 3-6 months of JSON accumulate first. The natural language content of sessions and observations will reveal the right node types and relationship labels better than upfront design. Pydantic schema consistency (already flagged) should be enforced before ingestion begins — that's the only prerequisite.

### Tonight's Build Session Scope

The v2.0 layer is not tonight. Tonight's session is the prerequisite work that makes v2.0 possible without a painful migration later:

**1. Deep Dive → Research Inbox (primary build)**
- Parse bibliography items in deep dive output into structured data (title, url, type)
- Add "Add to Research" button per bibliography item in Deep Dives tab
- Write to `candidates.json` with `{ topic: null, status: "unassigned", source: "deep_dive", deep_dive_id: "..." }`
- Research → Queries tab: add unassigned bucket at top, assign-to-topic dropdown

**2. Source link rendering in Deep Dive (secondary build)**
- Detect URL presence in bibliography items
- Apply link strategy from table above
- Google Scholar fallback for papers without URLs
- "Library" tag for subscription sources

**3. Inbox housekeeping on Queries page**
- Unassigned section at top, separated from topic-scoped candidates
- Assign action: dropdown of existing topics + "New topic..." option
- Retire action available from inbox (same as existing candidates)

**Explicitly out of scope tonight:**
- Neo4j setup
- Ollama integration
- Graph schema design
- Cross-topic synthesis UI
- Any changes to observe.py or research.py

### Handoff Note for OpenClaw

Spec the Deep Dive bibliography parser first — the structure of bibliography items in the deep dive `.md` output determines everything downstream. Confirm field availability (title, url, authors, type) before building the "Add to Research" action or the inbox UI. If the bibliography is unstructured text, a light parsing pass in Python is needed before the button can write clean records to `candidates.json`.

---
