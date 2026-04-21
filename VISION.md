# Mini-moi — Platform Vision
## The Personal Growth Intelligence System
**Version:** 1.2  
**Status:** Working document — direction, not prescription  
**Date:** 2026-04-20  
**Author:** Robert van Stedum  
**Maintained by:** Robert, with input from Claude.ai, OpenClaw, Grok

> This document captures the long-horizon direction for mini-moi. It is not a build plan or a release spec. It will change as the system evolves and new domains prove or disprove the pattern. Version it, don't freeze it.

---

## What Mini-moi Is Becoming

The curator domain proved one thing: a feedback loop that learns from your behavior produces something more valuable than any single session of output. After six weeks of daily use, the morning briefing is meaningfully different from week one — not because the model changed, but because the system knows you better.

The language-german domain, scaffolded in a single day in April 2026 and validated on real voice sessions that same day, proved something else: the pipeline pattern generalizes. The same architecture that ingests RSS feeds and scores articles can ingest voice transcripts and score German errors. The core loop — **ingest → analyze → deliver → feedback → learn** — is domain-agnostic.

Mini-moi is not a curator with a language learning add-on. It is a personal growth intelligence system where the curator was the first domain and language learning is the second. Health, writing, professional skills, and others will follow the same pattern.

The name was always right. Not a mini version of a large language model. A system that carries your specific point of view, learns from your behavior, and gets better with you over time.

---

## Core Architectural Principles

These principles apply to every domain, every build, every agent, from day one. They are not guidelines — they are constraints.

**Model-agnostic by design.**  
Different models play different roles: Grok for voice practice and operational speed, Claude for planning and synthesis, local models for private or zero-cost tasks. Any model at any layer must be swappable without rewriting the pipeline. The architecture never assumes a specific provider. This applies to scoring, reviewing, lesson planning, and intelligence generation equally.

**One step at a time.**  
No implementation step begins until the previous step is confirmed working. Models — including the ones building this system — have a tendency to race ahead. That tendency is explicitly countered by the Ways of Working: Implementation Agent waits for green light before writing any new code. Memory Agent validates before build starts. Robert is the decision point between all agents. This is not bureaucracy — it is what keeps the system from accumulating hidden debt.

**Local-first, data stays with you.**  
All personal data — session transcripts, feedback signals, vocabulary, preferences — lives on local hardware. Cloud APIs are used for inference only. The data layer of you is yours.

**Feedback signal before everything else.**  
A domain without a feedback loop is a script. A domain with one is a learning system. The feedback signal must be present in v1.0 of every domain — not deferred. The curator's like/dislike/save generated 415+ signals before the intelligence layer needed them. That runway is what makes intelligence useful.

**Timestamps are non-negotiable.**  
Every session, every feedback record, every event across every domain must carry a consistent ISO timestamp from day one. The cross-domain intelligence layer and the backtrace vision require queryable, consistent timestamps across years of data. Retrofit is painful. Start clean.

---

## The Curator as the Pattern — Not the Imitation

The curator established five non-optional elements that every domain must implement. Fewer than five and you have a script. All five and you have a learning system.

**1. Structured JSON data, pre-formatted for database migration**  
Flat files today, Postgres tomorrow, Neo4j on top of that. Every JSON file is keyed and structured to match a relational schema. No rewrite when the migration comes.

**2. The feedback signal — present in v1.0, always**  
Like/dislike/save in the curator. Session quality rating in German. The signal differs by domain; the principle is identical. No feedback, no learning. Build it first.

**3. The HTML layer as the human interface**  
The pipeline produces JSON. The HTML layer makes it usable. Every domain needs one. Build it incrementally — one page at a time, each earning its place by making data more useful.

**4. Telegram as the mobile delivery channel**  
Content arrives on iPhone regardless of which domain produced it. The user shouldn't need to know the routing. Consistent delivery builds consistent habit.

**5. A model-agnostic scoring/analysis layer**  
A structured prompt, a normalized JSON output, a downstream consumer that doesn't care which model produced it. Swap the model, keep the pipeline.

---

## Language Learning as the Second Domain

The German domain is the first major test of the pattern beyond information consumption — and it differs from the curator in one important way: **voice is the primary input.**

Speaking confidence is the goal. Not reading comprehension, not grammar exercises — the ability to walk into a Viennese bakery and order without people switching to English. That goal requires output practice, not passive consumption, which means the mobile experience is not a delivery channel for the German domain. It is the practice environment.

**The daily split:**
- **iPhone (Grok Voice):** 10–20 minutes of speaking practice with a persona. Voice is first-class here. The session happens on the phone because that is where voice feels natural.
- **MacBook (pipeline):** Structured review, error analysis, Anki generation, next-day lesson plan. Heavy processing stays where the compute is.

The handoff between them — transcript from iPhone to pipeline on MacBook — is the critical seam. It must be as close to frictionless as possible. The Telegram bridge is the current solution. Automation of that bridge is a near-term priority.

This mobile-first, voice-first design will define the language domain pattern for French, Portuguese, Spanish, and any language that follows.

---

## The Data Layer of You

The curator builds a model of what you read and care about. The language domain builds a model of how you learn. These are not separate data stores — they are two facets of the same underlying model: you as a learner, a thinker, a person with specific interests and specific gaps.

The database migration happens in two sequential stages, not one:

**Stage 1 — Postgres (v1.2):** Relational migration. Curator history, session data, feedback signals, cost logs — all move from flat JSON to structured tables. The JSON was always shaped for this. A `COPY` command and it's done.

**Stage 2 — Neo4j (v1.3):** Graph layer on top of Postgres. Nodes for articles, sessions, vocabulary, errors. Edges for connections: you saved 12 articles about Austria → your German vocabulary accelerated that month. The question "what am I actually getting better at?" becomes queryable.

Postgres must exist before Neo4j makes sense. The graph discovers connections in relational data — it does not replace it.

---

## Domain Architecture — The Graduation Model

Every domain follows the same lifecycle:

```
_NewDomains/[domain]/     ← prototype, gitignored, private
    SPEC.md               ← design
    PLAN.md               ← implementation
    CHECKLIST.md          ← verification
    [action plan].md      ← time-bound delivery plan
    [domain code]

        ↓ graduation criteria met

[domain]/                 ← main repo, live, versioned

        ↓ shared patterns proven across 2+ graduated domains

[domain_core]/            ← shared library, extracted
    parse.py
    reviewer.py
    status.py
    base_progress.json
```

**Graduation criteria — all must be true:**
- Feedback loop is active and generating real data
- HTML interface exists and is used
- Telegram delivery is working
- Progress tracking reveals meaningful patterns across sessions
- Value is proven in real use, not just test runs

**The shared library is extracted only after two domains have graduated.** Abstraction must be discovered from real reuse, not designed speculatively.

---

## The Intelligence Layer — When the Data Exists

The curator has `curator_intelligence.py` — a script that surfaces what the scoring found but the user might have missed. Every domain gets an equivalent. For German: `german_intelligence.py` runs weekly and requires 4-6 weeks of session data to produce signal rather than noise.

Weekly output delivered Sunday morning via Telegram:
- Persistent errors — appearing in 3+ sessions, with examples
- Vocabulary retention — words seen vs. correctly used later
- Improvement trajectory — is the V2 word order error declining?
- Confidence map — which scenarios feel easy, which feel hard?
- Recommended focus for next week

**Do not build this before the data exists.** Build it post-Vienna when 15-20 sessions have accumulated.

---

## The Cross-Domain Intelligence Vision

This is the v1.3+ direction. Named now so the data architecture supports it from day one.

**The question mini-moi should eventually answer:**  
*"What am I actually getting better at? What patterns in my reading predict what I learn? When I engage deeply with a topic in the curator, does my ability to discuss it in German improve?"*

These questions require connecting data across domains — a graph where curator preferences, German session data, writing exercises, and future domain data are all nodes in the same structure. The connections are the intelligence.

**The backtrace vision:** *"I thought X in March 2026 — was I right, and what led me there?"*  
For language: *"I couldn't order confidently in April 2026 — what changed by July?"*

The data being generated right now is the foundation. The graph connections will be discovered from real data, not invented speculatively.

---

## Platform Roadmap

Directional. Dates are targets, not commitments. Each version unlocks the next. Future versions are intentionally underspecified — they will be detailed when the prior version is complete and real requirements are known.

### Current: v1.1 (March–April 2026)
- Geopolitics curator — production, daily use, learning loop active
- Research intelligence — active threads accumulating
- language-german — scaffolded and validated April 2026, Vienna trip in progress

### v1.2 — Infrastructure + German v1.0 (Summer 2026)
- Mac Mini migration — always-on, Tailscale, launchd auto-restart
- PostgreSQL activation — structured migration from flat files
- German HTML layer — session review page, progress dashboard
- `!german start` — persona selection via Telegram
- Session quality 1-5 rating — feedback loop closes for German domain
- Curator novelty scoring

### v1.3 — Intelligence Layer (Fall 2026)
- `german_intelligence.py` — weekly analysis, post-Vienna data required
- Neo4j graph layer — on top of Postgres
- First cross-domain connections — curator ↔ German session data
- Details to be specified when v1.2 is complete

### v2.0 — The Personal Growth System (2027)
- 3+ domains active, at least 2 graduated
- `domain_core/` shared library extracted
- Cross-domain intelligence — the data layer of you, queryable
- Details to be specified when v1.3 is complete

---

## What This Is Not

**Not an imitation of existing tools.** Duolingo optimizes for engagement. Mini-moi optimizes for actual performance under real conditions. The goal is not streaks — it is walking into a Viennese bakery and ordering without hesitation.

**Not a general assistant.** The value is specificity. A system that knows your error patterns, your vocabulary gaps, your reading preferences, and your learning trajectory is more useful than a system that knows everything about German but nothing about you.

**Not a finished product.** This is a prototype proving the pattern. The curator v1.0 took six weeks. The German domain was scaffolded in a day because the pattern was proven. Each subsequent domain takes less time. The platform accelerates as it matures.

---

## The Principle That Runs Through Everything

From the README:

> *"General intelligence is widely available now. Specific intelligence — the kind that knows you and acts for you — isn't. That's what this builds toward."*

Every design decision in mini-moi serves this principle. Local data. Feedback loops. Cross-domain connections. The HTML layer that makes data human-readable. The Telegram interface that meets you where you are.

The curator proved that a system which learns from your behavior produces something more valuable than any single output. The German domain is proving it again. The graph layer will prove it across domains.

Mini-moi is the system that knows you.

---

## Document Changelog

| Version | Date | Changes |
|---|---|---|
| 1.0 | 2026-04-20 | Initial draft — Claude.ai |
| 1.1 | 2026-04-20 | OpenClaw review: session quality rating moved to v1.2; Postgres and Neo4j separated as sequential stages; "built in a day" corrected to "scaffolded"; timestamp discipline added — Claude.ai + OpenClaw |
| 1.2 | 2026-04-20 | Grok review: model-agnostic principle elevated to Core Architectural Principles; mobile-first voice added as standalone section; guardrails made explicit; future roadmap versions intentionally underspecified — Claude.ai + Grok |
