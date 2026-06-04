# Design Session — Intelligence Layer
*mini-moi · June 3, 2026*
*mini-moi-private/guild/design-sessions/*

---

Tonight's conversation started with the POC result and ended somewhere more interesting than we expected.

---

## Where it started

The database spine came in on the `guild` branch. Both databases running, schema applied, data migrated, `poc_verify.py` ran clean. Postgres returned active topics and source counts. Neo4j's traversal query — find what's within two hops of `gold-geopolitics` — returned zero.

Not a bug. `sources.json` was empty. Seven topic nodes floating with no edges between them. Nothing to traverse.

The honest finding from the session was that the POC proved the spine works and simultaneously proved it has nothing to work with yet. That's the right outcome for a first build — you learned something real. But it surfaced a harder question than "does it run": what is this actually going to do, and how will we know when it starts doing it?

With seven topics you can see the whole structure in your head. The graph earns its place when you can't anymore. The rough threshold — not a number but a condition: sources with overlapping tags that create paths between topics you didn't consciously connect. You need enough declared edges that the traversal finds something you didn't already know. That's roughly 50+ sources, 15+ topics, a few active groups. Before that the graph is an investment in structure, not a source of insight.

The fastest path to getting there isn't waiting for research sessions to accumulate organically. It's the Investigate modal Source tab — one action per article, tags attached, directly from the daily reading. That got built earlier today, which means starting tomorrow every article can feed the inner ring with one deliberate gesture.

---

## What Neo4j actually is

You asked what Neo4j contributes to AI, and separately how to integrate it with Ollama. Worth capturing how that thinking actually went because it clarifies what the graph is for.

Neo4j isn't doing what Postgres does, faster or differently. It stores a different kind of thing: relationships, not records. A relational database is good at answering "give me all sources tagged gold" — you get a list. A graph database is good at answering "what connects gold-geopolitics to china-rise through paths I didn't draw explicitly" — you get a map.

The difference matters when you start accumulating sources with overlapping tags. If you tag a source about China's gold reserves with both `gold` and `china`, Neo4j can traverse from `gold-geopolitics` through that source to `china-rise` and tell you those topics are connected. You never said they were. The graph surfaced it from the declared edges. That's what "two hops" means in practice — follow the first edge (topic to source), then the second edge (source to another topic), return what's at the end.

This only becomes interesting when there are enough sources that the connections aren't obvious. With seven topics you'd see those connections yourself. At fifty topics and a few hundred sources, you genuinely can't hold the structure in your head, and the graph starts finding things that aren't visible without it.

The relevance to AI: a standard AI call works from global knowledge (what the model was trained on) plus whatever context you paste in. A graph-enhanced call works from your specific declared structure — your leanings, your research history, your tagging decisions over months. Those are different kinds of intelligence. The graph is what makes the local context queryable in a meaningful way, rather than just a pile of JSON files.

---

## The formatting problem — you identified it before I'd articulated it

When we got to the question of how Ollama and Neo4j work together in practice — how you actually give an LLM useful context from a graph — you cut to the core of it faster than the formal explanation:

> "It seems like it needs its own LLM just to create and then produce a prompt for another LLM call."

That's exactly right. Raw graph output is not readable by a language model in any useful way. Dumping nodes and edges into a prompt produces poor answers. What works is a translation layer: a cheaper local model reads the raw neighborhood and writes a plain-language brief of what it means. "Gold-geopolitics connects to china-rise through three sources about reserve diversification" is something a model can reason over. `{"nodes": [{"id": "gold-geopolitics"...}]}` is not.

This is actually the core insight from the current research on graph-enhanced language models — the community summarization step, where summaries of graph neighborhoods are generated offline and served at query time rather than generating them fresh on every call. The expensive work happens once, gets stored, gets served cheap. We arrived at it from a different direction: by hitting the actual problem and reasoning backward to what would work.

So the practical integration is two stages. Stage one is Ollama: read the subgraph, write the brief, store it. Free, private, runs on your machine, slow is fine because it runs offline. Stage two is Sonnet (or whatever the reasoning model is): receive the interpreted brief, reason over it, answer the question. This is also where the "local model recognizes it needs help" behavior matters — you can design explicitly for the local model to return "NEEDS_GLOBAL: [reason]" rather than forcing it to answer and confabulating. Small models are more honest when you give them a clean exit than when you force them to pretend they know.

---

## The noise problem and the critic pattern

You raised this one clearly: a graph populated over time from real use will accumulate noise. Tags applied carelessly, sources promoted that didn't really belong, connections that seemed meaningful in the moment but aren't. That's not a failure to prevent — it's a reality to design for.

Your instinct was exactly right: handle it the way we handle everything else — one model surfaces candidates, a better model or a human decides. The same pattern as the Claude-to-Grok handoff. One pass flags what looks off-territory or weakly connected. The flagged items go to a review document. Nothing is modified automatically. Robert decides what stays.

This is graph hygiene as a designed feature, not a future cleanup task. And it creates a natural mechanism for A/B calibration over time — run two tagging approaches for a few months, review which produces cleaner graph connections, adjust accordingly. The system gets better through use rather than through upfront perfection.

Worth noting: build the critic pass only when the graph is dense enough that you can't audit it yourself. Right now with seven topics you'd see every problem at a glance. The critic earns its place around the same threshold as the graph itself — 50+ sources, more than one active group.

---

## The routing problem — the hardest question

This is where the conversation went somewhere genuinely interesting. You asked how to combine a capable global model (Sonnet, Claude, the outer ring) with the local graph and the local LLM, in a way that knows when to reach out to global knowledge and when to use local context — and how to blend both when you need both.

The framing that clarified it: three rings.

The inner ring is what you've declared — your leanings, your tagged sources, your research connections. Private, specific to you, entirely yours. Nobody else's inner ring looks like yours.

The middle ring is your accumulated signal — months of daily briefing responses, what you've saved and dismissed, where your attention has been over time. Reflects your history without being your explicit beliefs.

The outer ring is the world's knowledge — trained models, current events, web search. Available to everyone.

Most AI tools live entirely in the outer ring. They're powerful there. The intelligence that matters for real decisions — what knows your particular situation — lives in the inner two rings. That's what mini-moi is building toward.

The routing problem is actually not that hard once you name it. Some questions are pure inner ring ("what's my current leaning on Hungry-and-Poland"). Some are pure outer ring ("what happened in Warsaw last week"). Some are genuinely both ("given what happened in Warsaw, does it shift my leaning"). The system needs to know which bucket a question falls into before it decides what to call.

A lightweight classification step — a small, fast local model call that returns LOCAL, GLOBAL, or BLEND — handles this reliably and cheaply. One token, near-zero cost, fast. The model doesn't reason about the answer; it just classifies the query type. What's harder is the BLEND path: how do you combine the inner ring's context with the global model's reasoning without the context being noise?

The answer connects back to the two-stage summarization: the local model prepares the inner-ring brief offline, the global model receives a clean interpreted brief plus global context, synthesizes both. The local model does the preparation for free; the expensive call is shorter and more focused because the work was already done.

The "local model recognizes it needs help" design point is worth holding: you build the explicit escape hatch ("NEEDS_GLOBAL: reason") rather than patching confabulation out after the fact. Small models are honest about uncertainty when you make honesty easy. They confabulate when forced to answer.

---

## "This is a design discussion and a learning exercise, not a build"

You said this explicitly partway through, and it was the right call to make explicit. The conversation had been covering real architecture — integration patterns, routing logic, two-stage pipelines — but in a way that wasn't building toward a sprint, it was building toward understanding.

That distinction shaped the rest of the session. The goal was to think through what the system becomes and how the pieces actually work, so that when we do build it the decisions are already made. The architecture is right. The data has to grow into it. The bones go in early; the intelligence layer grows as the inner ring gets richer.

The honest state: the spine is proven, the daily reading path to graph population is built, the system is usable every day in its current state. What we worked through tonight is the direction — what each component does, why the combination matters, where the line is between AI-augmented and a real intelligence system.

The line, specifically: when the inner ring is rich enough that a BLEND query produces a materially different and better answer than a plain Sonnet call — when the local context genuinely shifts what the global model says — the system is doing something a tool can't do. That's the target.

---

## What the system becomes

Worth stating plainly what we're building toward, since we worked through all the pieces:

A morning session that opens with a situational brief — overnight news synthesized against current leanings and research state, grounded in what I've declared about the world over months, not just what a model was trained on.

A leaning review that knows what evidence I've already seen, asks what new information changes, and surfaces the connections between topics I didn't consciously draw.

A research session where "find more on this" means the system knows what I've already read on the topic, what I've tagged as relevant, what my current stance is, and uses all of that to make the next session genuinely additive rather than starting from scratch.

Not AI I use. AI that knows what I'm working on.

The pieces that get us there: bridge first (accumulate sources in real time), summarizer second (make the graph legible), router third (route queries intelligently), blend synthesis fourth (combine inner and outer ring), critic as needed (keep the graph clean). Each has a trigger, not a timeline.

That's what we figured out tonight.

---

*Session: Robert van Stedum + Claude · June 3, 2026*
*Save to: mini-moi-private/guild/design-sessions/DESIGN_SESSION_INTELLIGENCE_LAYER_2026-06-03.md*
*Do not commit to public repo.*
