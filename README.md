# Mini-moi — Personal AI Agent

### *Mini-moi — not a general intelligence, but a specific one. Yours.*

A personal AI agent system. Local memory, local model, swappable LLMs
when you need them — not the other way around. The goal is an agent that
knows you, challenges you, and gets better with you over time.

The first domain is geopolitics and finance (v1.1, daily use since February 2026). The second domain is German language learning (v0.9 beta). It uses the same local-first, model-agnostic pipeline for spoken practice in real Viennese scenes — with scaffold phrases, focused drill mode, and Anki cards earned only from actual friction. Language and Jobs are next.

Learns from your history and preferences, with deliberate friction built
in. Not a curated feed that confirms what you already think — an agent
that surfaces what you should be seeing, including content that cuts
against the grain.

> Production system, daily use since Feb 2026. Local-first, model-agnostic,
> designed to expand across domains — see [Where It Goes Next](#where-it-goes-next).

---

## What This Is Really About

The cloud LLMs have the world's knowledge. That problem is largely solved.

The hard part — the part that actually matters for real decisions — is
acting in your specific situation. Your history. Your goals. Your risk
tolerance. Your team's context and motivation. General intelligence is
widely available now. Specific intelligence, the kind that knows you and
acts for you, isn't.

That's what this builds toward.

Local memory and a local model are the foundation — context, preferences,
and learned behavior stay with you. Cloud LLMs are central to how this
works — they bring the world's knowledge, reasoning, and current events
into the picture. But memory, context, and what the agent has learned
about you stay local. That's what maintains autonomy. Switch models,
switch providers, go offline — the agent still knows you.

The vision is bigger than a personal project or one domain. If I had a
team at work — people and agents together — I'd want this same local
context and motivation at the center of it. Not a generic assistant that
knows everything about the world but nothing about us. A specific
capability, grounded in our history, our goals, our way of making
decisions under uncertainty.

That's what Mini-moi — mini-me — is about. Not a mini version of a large
language model. A system that carries your particular point of view and
acts on your behalf — in your real world, for you, now.

And it gets better as you use it. Not because someone updated a model —
because you and the agent are learning together.

---

## Why I Built This

The best way to understand something is to build it. This project started
as exactly that — learning by doing, with a real problem and a real use
case, not a tutorial exercise.

The future of AI isn't just larger models with more world knowledge — it's
systems that carry specific context: your history, your goals, your way of
reasoning through uncertainty. The cloud LLMs are powerful — but they don't
know your personal context. And if they did, you'd risk being locked into
one provider, with your history and preferences held somewhere you don't
control.

I wanted to build that layer. Not as a prototype or a tutorial exercise,
but as something I actually use. Geopolitics and finance were the natural
first domain — areas I follow closely, where I have a real point of view
that a generic feed can't capture.

The approach — local context, model-agnostic, flat files structured for
future migration — was designed to scale beyond one person and one domain.
Health, language learning, team environments at work. The architecture
anticipates that. The first domain just had to be one I cared enough about
to build it right.

---

## How It Was Built

I set out to build this the right way — production quality, not a hack.
AI lowers the barrier to building, but it can also take over if you let it.
The discipline here was to use it as a genuine collaborator: coach,
reviewer, and implementer — but never the one driving.

Not designed up front then implemented. The vision was fixed. The path
was iterative — a working slice first, production feedback, then
refinement. Each phase complete before the next one started.

### The Foundation

The intent of the project kept the foundation disciplined.

From day one: a structured database for content, history, and memory, and
a graph database for learned relationships. Both installed, both in use
early. But the tension surfaced quickly — too much time on infrastructure,
not enough on functionality. The breakthrough came from an unexpected
direction. The LLM suggested structuring the JSON files to match a database
schema — write local first, load later. I knew immediately that was the
answer. One migration command when the time comes, no rewrite.

We undid some early work and rebuilt around that principle. The result:
a system that is database-independent today, just as it is LLM-independent.
The data is ready. The databases are waiting. The functionality came first.

The same principle applied to models. User profile injection at the
dispatcher level, not inside any model's prompt. Swap any model at any
layer without touching the personalization logic. Database-independent.
LLM-independent. The pattern held throughout.

A side benefit emerged later: a dry-run mode that validates code against
the local model instantly, at no cost. The architecture rewarded the
approach in ways that weren't fully anticipated at the start.

---

## What It Does

### The Daily Briefing

Every morning, a three-stage pipeline runs automatically:

- **Gather & enrich** — RSS feeds and X bookmarks are pulled and merged;
  destination content is fetched from source URLs, giving the scorer full
  article context, not just headlines
- **Score** — a reasoning model ranks ~700 candidates with your learned
  profile injected
- **Deliver** — top 20 to the web portal, top 10 to the mobile messaging
  channel

Each stage uses a swappable model, tuned for cost and quality. The
architecture supports a bulk pre-filter between gather and score — ready
to activate as the candidate pool grows.

Later in the day, **AI Observations** arrives: the system's own read on
what it surfaced — what's surging, what's missing, where the story is.
You respond. Those responses feed the learning loop.

### Research Intelligence (v1.1)

Background research threads accumulate sources daily against your defined
topics. Each session scores candidates against your research targets,
builds a library of qualified sources, and tracks what's been seen — so
subsequent sessions surface fresh material rather than repeating the same
sources.

Five active threads as of v1.1: empire-landpower, china-rise,
gold-geopolitics, strait-of-hormuz, hellscape-taiwan-porcupine.

### Deeper Dives

On-demand synthesis documents generated from research threads. A
Synthesizer pass assembles findings and bibliography; a Challenger pass
pressure-tests the hypothesis. The result is a structured document you
can return to and build on. Dives are iterative — generate again after
new sessions accumulate and get a fresh synthesis against a richer source
base.

The reading-to-research loop: morning article → Deep Dive → spawn thread
→ sessions accumulate → generate Deeper Dive → repeat.

### On Demand

- **React & learn** — like, dislike, or save any article on either surface.
  Every signal feeds the learning loop and shapes future scoring — including
  the friction reserve, where the system surfaces content that cuts against
  the grain of your usual reading, keeping the feed from becoming a
  confirmation loop.
- **Deep Dive** — flag any article from the web portal for a structured AI
  brief. Add your own notes first — what caught your attention, what you
  want explored — and the deep dive is shaped by that context, not generic.
- **Signal Priorities** — inject a focus area directly into the scoring
  pipeline, with a boost level and expiry.

---

## How It's Built

mini-moi uses a three-agent workflow with strict separation of concerns:

| Agent | Role |
|-------|------|
| Claude.ai | Design, strategy, architecture decisions |
| Claude Code | Implementation, commits, file-level execution |
| OpenClaw | Planning, documentation, memory, issue tracking |

One agent is active on the repository at a time. Robert is the decision
point between them. New domains and features stage in `_NewDomains/`
before graduating to the main repo on release — Research Intelligence
followed this path. Language and Jobs are next.

### Cost baseline (build phase)

These reflect active development. Production costs for a stable daily-use
system will be meaningfully lower.

| Item | Cost |
|------|------|
| Monthly API spend | $35–45 (build phase) |
| Per research session | ~$0.002–0.005 |
| Per deeper dive | ~$0.21–0.24 |
| Research pilot budget | $20 allocated · $0.08 used at v1.1 |

Cost discipline is a design constraint, not an afterthought.

---

## Where It Goes Next

### v1.2 — Mac Mini + infrastructure

Always-on Mac Mini eliminates sleep interruptions. PostgreSQL activates
for structured storage. Reading Room lands — full article text, not just
links. Curator novelty scoring with 7-day windowed implementation.

### v1.3 — Intelligence layer

Neo4j graph activation. Cross-thread pattern detection. Local LLM for
mining latent connections across months of accumulated sessions. The
backtrace vision: *"I thought X in March 2026 — was I right, and what
led me there."*

See [ROADMAP.md](ROADMAP.md) for full detail.

---

## Upstream Contributions

Issues and observations filed during active development — real production gaps, not theoretical:

- **openclaw/openclaw #18160** — Direct exec mode for cron jobs (feature request)
- **openclaw/openclaw #52314** — Pre-compaction memory flush hook for agents (feature request, emerged from OPS-001)
- **openclaw/openclaw #19249** — Model failover on billing exhaustion: confirmed tool-layer local models don't automatically become session fallbacks when cloud billing fails (comment with production evidence)

All three emerged from building and operating this system.

---

## Screenshots

| Daily Briefing | Signal Priorities |
|---|---|
| ![Daily Briefing](docs/screenshots/daily-briefing.png) | ![Signal Priorities](docs/screenshots/priorities.png) |

| AI Observations | Deep Dives | Morning Briefing |
|---|---|---|
| ![AI Observations](docs/screenshots/ai-observations.png) | ![Deep Dives](docs/screenshots/deep-dives.png) | ![Morning Briefing](docs/screenshots/morning-briefing.png) |

---

**Status:** v1.1 — Research Intelligence (March 2026) · v0.9 — Language Learning beta (May 2026)
**Author:** Robert van Stedum
**Release notes:** [docs/releases/RELEASE_v1.1_2026-03-29.md](docs/releases/RELEASE_v1.1_2026-03-29.md)
