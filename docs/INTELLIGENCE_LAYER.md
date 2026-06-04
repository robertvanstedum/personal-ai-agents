# Intelligence Layer
*mini-moi · June 2026*

---

## Part 1 — What We Figured Out

The question wasn't whether the database spine worked. It worked — Postgres and Neo4j both running, schema applied, data migrated from JSON, verification script clean. The question that surfaced when the traversal query returned zero was harder: *what is this actually going to do, and how will we know when it's doing it?*

With seven topics and an empty `sources.json`, the graph has nothing to traverse. Seven nodes floating with no edges between them. That's not a broken build — it's an honest finding about where the system is. The graph earns its place when the inner structure is richer than a human can hold in their head, when it starts surfacing connections between topics that weren't consciously drawn. That's roughly 50+ sources, 15+ topics, real overlapping tags. Before that threshold the graph is an investment in structure, not a source of insight. We knew that going in, but seeing the zero made it concrete.

What Neo4j is actually doing is worth being clear about, because it's not doing what Postgres does faster or differently. It stores relationships, not records. A Postgres query gives you a list — all sources tagged gold. A Neo4j traversal gives you a map — follow gold-geopolitics to a source tagged both gold and china, then follow that source to china-rise, and you've found a path between two topics that was never explicitly declared. That's what "two hops" means in practice. It only becomes useful when there are enough declared edges that the paths aren't already obvious.

The integration question — how Ollama and Neo4j work together — revealed a real problem before the solution was obvious: raw graph output isn't readable by a language model. You can't dump nodes and edges into a prompt and expect good answers. You identified this directly: it needs its own LLM just to create a prompt for another LLM call. That's exactly right, and it's the key implementation insight from current research on graph-enhanced language models. A cheap local model reads the subgraph and writes a plain-language brief of what the neighborhood means. That brief is what the reasoning model receives. The local model does the translation offline, for free, privately. The reasoning model gets interpreted context rather than raw structure.

The noise problem came up next, and the design response was already in how we work: one model flags candidates, a human decides. Same pattern as the Claude-to-Grok handoff — one pass surfaces issues, the other assesses, the person decides. Applied to the graph it means periodic review runs that flag weak connections, off-territory sources, tags that have drifted from their intended meaning. Nothing modified automatically. The graph stays clean through iteration, not through upfront perfection.

The routing problem — how the system knows when to use local context, when to reach for global knowledge, and when to blend both — is the hardest design question and the one that determines whether this becomes a real intelligence system or stays an AI tool with extra steps.

The framing that made it clear: three rings. The inner ring is what's been declared — leanings, tagged sources, research connections. Private, specific, mine. The middle ring is accumulated signal — months of daily briefing responses, what was saved and dismissed, where attention has been over time. The outer ring is the world's knowledge — trained models, current events, web search. Most AI tools live entirely in the outer ring. The intelligence that matters for real decisions lives in the inner two.

Routing between them is a lightweight classification step — local, global, or blend — made by a small fast local model before any expensive call happens. The blend path is where the two-stage pattern applies: local model prepares the inner-ring brief, reasoning model receives brief plus global context, synthesizes both. The local model does preparation for free; the reasoning call is shorter and better-informed because the work was already done.

The line between AI-augmented and a real intelligence system: when a BLEND query produces a materially different and better answer than a plain cloud model call on the same question — when the inner ring is rich enough to shift what the global model says. That's the target. Not a feature launch, not a date. A threshold you approach through use and recognize when you cross it.

One design discipline that should run through the system as it grows — not as a specification but as a habit of noticing. When a question comes up, the answer usually falls into one of three states:

You know it. The answer is in your history, your leanings, your research. An LLM call might actually get in the way. Surface it from the graph and decide.

You're curious. The local model might handle it. Try it and see. If it works, you've found a zero-cost path. If it falls short, you've learned the condition that trips it — and that's worth a one-line note: "tried local here, fell short on X, moved to global."

You know it needs the world. Current events, complex synthesis, anything that requires knowing what happened this week or reasoning the local model can't match. Go straight to the cloud model. Note why.

The trace isn't a field in a schema or a flag on a component. It's the habit of noticing which state you're in and writing one line when the answer surprises you. Over time that builds a real map — where the local tier is actually capable, where it reliably falls short, which questions belong to which tier. Grounded in use, not designed upfront. And durable across whatever the components turn out to be.

*The full session exploration — the questions as they came up, the working-through, the insights in sequence — is in `docs/DESIGN_SESSION_INTELLIGENCE_LAYER_2026-06-03.md`.*

---

## Part 2 — How This Maps to Current AI Practice

The concepts we worked through have names in the field. For reference:

**Retrieval-Augmented Generation (RAG)** — before answering, retrieve relevant material from a knowledge base and inject it as context. mini-moi already does a version of this (profile injection, scoring against a learned preference model). The intelligence layer extends it to the full inner ring.

**GraphRAG** — graph-enhanced retrieval. Instead of retrieving the most similar documents, retrieve a connected subgraph — the neighborhood around the query. Enables multi-hop reasoning across connections that weren't explicitly declared. Microsoft Research published the canonical paper in 2024. The community summarization step (a model generates plain-language summaries of graph neighborhoods, stored and served at query time) is the key implementation pattern — the same one we arrived at by hitting the formatting problem directly.

**Agentic routing / adaptive RAG** — classifying queries by what kind of knowledge they need (local, global, or blend) before retrieval. Routes efficiently rather than calling everything on every query.

**Critic / evaluator agent** — a separate model pass that reviews data or outputs for quality, flagging issues for human review rather than acting autonomously. The same pattern as the Claude-to-Grok handoff, applied to graph hygiene.

**Local LLM (Ollama + Gemma3)** — a model running on the local machine. Sees private data. Costs nothing per call. Good for summarization, triage, routing, classification. Falls short on complex reasoning and current events. In this architecture: the translation and routing tier.

**Vector store** — a database for semantic similarity search. Not yet in mini-moi but the natural complement to the graph: graph for declared structure and traversal, vector store for semantic similarity. Both together is the complete local retrieval layer.

**LangGraph, LlamaIndex** — frameworks with building blocks for agentic retrieval pipelines. Not used here (the architecture is built lean in plain Python) but worth knowing as reference points.

---

## Part 3 — How It Gets Built

Preference is bones first: structure in place early, data grows into it. Every week without the structure is a week of signal that doesn't accumulate into the graph. The spine exists on the `guild` branch. The Source tab in the Investigate modal is live on `main`. The path from here to threshold is already open.

**The sequence, with triggers not timelines:**

*Now — land the bridge.*
Dual-write connects the domain modules to Postgres after every JSON write. JSON stays source of truth. Reconcile confirms zero drift. `guild` merges to `main`. Sources accumulate in real time from the next session forward.

*When 20+ sources are tagged — build the summarizer.*
`db/summarize.py`: query Neo4j neighborhood, call Ollama, store the plain-language brief. Regenerate when the graph changes. Can be built before the graph is dense — thin briefs until there's substance to summarize.

*When Q2 in `poc_verify.py` returns something non-obvious — build the router.*
If traversal surfaces a connection that isn't immediately obvious from scanning the topics list, the inner ring is rich enough to be worth routing to. Build the classification step and wire it into the Leaning teammate read first.

*When the router is live — wire the blend synthesis.*
BLEND path: local model produces the inner-ring brief, Sonnet receives brief plus global context, synthesizes. This is the step that crosses the line.

*When 50+ sources and the graph isn't auditable by eye — build the critic.*
`db/graph_review.py`: flags tag drift, dead nodes, weak connections. Produces a review document. Robert decides what gets cleaned. A/B test tagging approaches over time — which produces cleaner graph connections, which surfaces better traversal results.

*At threshold — the Chief of Staff brief.*
Overnight Curator results synthesized against current leanings and research state. The daily situational read that runs before the first deliberate session action of the day. This is the return on everything above.

**The check that marks the threshold:**
`poc_verify.py` Q2 returns topics connected through sources that weren't consciously linked. A BLEND query produces a different and better answer than a plain Sonnet call. The inner ring is no longer small enough to hold in your head.

**At each integration point — notice which state you're in.**
You know the answer, you're curious whether local handles it, or you know it needs the world. Try local when curious. Note when it falls short and why. The trace is one line, not a spec. It builds a real map over time — where the local tier is capable, where it isn't — grounded in use rather than designed upfront. The habit is durable even as the components change.

--- `guild` branch has the spine (Postgres + Neo4j, six research tables, migration, seed, verification). Bridge deferred pending source accumulation. Source tab live on `main`. For build details: `_working/BUILD_PLAN_2026-06-03_revised.md`. For current Curator state: `_working/CURATOR_STATE_2026-06-01.md`.*

*Part of mini-moi — personal-ai-agents.*
