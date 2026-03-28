# mini-moi — Conversational Intelligence Vision
*Captured: March 26, 2026*
*Private — not for README or public docs*
*Source: design conversation, claude.ai*

---

## The Insight

Everything the pipeline produces gets saved.
Everything Robert thinks and discusses *getting there* disappears.

The README.md collaboration, the ROADMAP decisions, the "why we built it this way" — the artifact gets saved, the thinking that produced it doesn't. The Arrighi synthesis reaction, the Iran/Caspian analysis, the design thinking on journal vs pipeline, the mini-moi.ai domain decision — gone when the session closes.

**This is the gap. Filling it is the next phase of mini-moi.**

---

## What mini-moi Actually Is

Not a dashboard. Not a pipeline manager.

A **conversational intelligence partner** with a long memory. A system that reads with you, thinks with you, and remembers everything — so that over months and years it knows your intellectual history better than you do.

The formal front end (Daily, Deep Dives, Research threads) produces curated content.
The conversational layer is where thinking happens.
Both feed the same journal.
The journal is what gets mined.

---

## The Two Layers

### Layer 1 — Formal Front End (exists, keep as-is)

- **Daily briefing** — morning read, intelligent feed that learns your appetite
- **Deep Dive** — quick analysis + bibliography on demand. Not deeply deep, but the right gesture. Fast turnaround, real bibliography for follow-on reading.
- **Research threads** — background polling, set and forget. "Follow this for 14 days." Runs a little every day, stops or extends. You check in when you want.

These are *produced* content. Don't change what's working.

### Layer 2 — Conversational Back End (to build)

- You react, speculate, push back, ask questions anywhere
- mini-moi responds, challenges, connects to what you wrote three weeks ago
- Occasionally produces something longer — a synthesis, an essay, a "here's where your thinking has gone"
- You write something longer too — and it goes in the same place
- The conversation *is* the journal

---

## The Journal

**Not a form. Not a filing system. Not a pipeline stage.**

A place where everything intentionally saved accumulates:
- Saved conversations with Claude
- Comments made anywhere in the UI (article, deep dive, observation, research finding)
- Essays — formal outputs from joint collaboration (like README.md sessions)
- Speculations — "I think X will happen" with a date stamp for future backtrace
- Reactions to research findings and observations

**Key design principle: Intentional, not automatic.**

A "Save" gesture on any conversation or thought. Not everything gets saved — thinking experiments, personal reasoning, things that shouldn't persist — those stay ephemeral by design. The save is a deliberate act that says "this matters, keep it."

Privacy is structural, not a setting.

---

## What Gets Saved When You Hit Save

```
Conversation with Claude (today's design session, for example)
    → markdown export
    → title + date + loose topic tag
    → joins journal alongside deep dives, observations, findings
    → no summarization needed yet — just capture

Future LLM can mine:
    "In March 2026 Robert was thinking about dollar hegemony 
     transmission mechanisms. Here's the thread that led to 
     the mini-moi.ai vision. Three months later these sources 
     confirmed the SWIFT angle he flagged."
```

---

## The Backtrace Vision

Speculate with a date stamp. Come back later.

"Was I right? What led me there? What was I reading?"

Not just article history — the thinking that surrounded the reading. The graph + local LLM (Ollama, v2.0) mines across:
- What you were reading (saved articles, deep dives)
- What you were thinking (journal entries, conversation saves)
- What the research found (sessions, observations)
- What actually happened (new briefing content over time)

The system doesn't just remember what you read. It remembers how you thought.

---

## The Conversational Partner Vision

mini-moi talks back:

- "You've been circling Iran's oil strategy for three weeks — here's what's shifted"
- "Your speculation about dollar hegemony from March — two new data points either confirm or complicate it"
- "You and I spent four hours on the README last Tuesday. The reasoning that led to the architecture decision is here."

This is not a notification. It's a conversational prompt that invites a response — which itself gets saved if you choose.

---

## mini-moi.ai

The domain anchors the vision. This isn't a tool you use. It's a version of you that reads everything and never forgets — but only keeps what you tell it to keep.

---

## Mobile Vision (future)

The conversational layer is natural on mobile. Voice input especially.

Language Learning domain is the first mobile-native use case: conversation with mini-moi *is* the lesson. It remembers your errors, your vocabulary gaps, the contexts where you've struggled. The journal captures your language learning journey the same way it captures your intellectual one.

The "save this conversation" gesture works identically on mobile — intentional, not automatic.

---

## What to Build Next (Near Term)

**One thing: Save conversation button in the Claude.ai UI**

Export the current exchange as markdown into the journal:
- Title (editable)
- Date (automatic)
- Loose topic tag (optional, from a short list)
- Destination: `data/journal/YYYY-MM-DD-[title].md`

No LLM processing yet. Just capture. The mining comes later when the graph is ready.

**Second thing: Comment anywhere**

A floating note gesture on every page — article, deep dive, observation, session finding. One click, free text, saves to journal with context (what page, what item, what date). No form. No category required.

These two gestures — save conversation, comment anywhere — are the entire near-term build. Everything else is already working or deferred to v2.0.

---

## What Not to Build

- Pipeline management UI (candidates, promote/retire, inbox complexity)
- Admin panels visible in daily use
- Forms requiring internal IDs or system knowledge
- Automatic capture of anything (privacy is structural)

---

## Architecture Note

The journal is append-only markdown files today.
Neo4j + Ollama mine it in v2.0.
Nothing needs to change about the file format when the graph comes online —
the content is already natural language, already dated, already tagged loosely.
Just ingest and index.

The foundation being built today is correct. Don't redesign it —
add the conversational layer on top.

---

*This document itself is an example of what should be saved.*
*It was produced in a design conversation on March 26, 2026,*
*during a session that also fixed B-012/B-013, built the Research UI,*
*and registered mini-moi.ai.*
