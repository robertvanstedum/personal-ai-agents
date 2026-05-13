# mini-moi — Language Domain Case Study Outline
## From Pipeline to Partnership: German in Vienna
### Draft Outline — v1.0 Release Artifact

*Not a demo. Not a prototype. A growing capability, used in a real life.*

---

## The Arc in One Paragraph

A trip to Vienna. A real deadline. A productive gap in German that no existing tool
addressed the right way. The decision: build the right tool rather than use the wrong one.

Over six weeks, the pipeline evolves from a transcript parser to a three-layer learning
system, a field-capture library, and a dual-agent architecture that navigated real
Viennese streets on the last day of the trip. The system was wrong, fixed, rebuilt,
and improved in real time. That's the story.

*Duolingo knows what lesson you're on. mini-moi knows why you're learning, where
you're going, and what you specifically struggle with. The difference is memory and
context — applied to your situation, not a generic curriculum. That's the approach
across every domain.*

---

## Part 1 — The Pipeline Begins (April 2026)

### 1.1 The Problem
- Intermediate German, productive gap: understand more than can produce
- Vienna trip as forcing function — fixed deadline, real stakes
- Existing tools know what lesson you're on — they don't know your specific context,
  your deadline, your gaps, or your intent
- Decision: build the right tool rather than use the wrong one

### 1.2 The First Build
- `parse_transcript.py` — turn a Grok Voice session into structured data
- `reviewer.py` — LLM extracts errors by type, generates Anki CSV, queues next lesson
- Eight Vienna personas: hotel, café, museum, pharmacy, U-Bahn, restaurant, bakery, Airbnb
- Two-message Telegram delivery: briefing (read this) + AI prompt (paste this)
- Session pipeline validated end-to-end on real transcripts

### 1.3 What the Data Showed Early
- Vocabulary errors dominant from session one
- *der/die/das* errors every session — structural, not situational
- Receptive competence ahead of productive competence
- The stall is sentence assembly speed, not vocabulary knowledge

---

## Part 2 — The System Learns What the Learner Needs (Late April)

### 2.1 Scaffold Phrases
- Observation: conversation stalls at output generation, not comprehension
- Solution: pre-load three phrases per session — transaction, preference, recovery
- Scaffold appears in Message 1 only — AI never sees it — tests spontaneous deployment
- Georg persona added: casual local conversation, Sie→du shift, honest recommendations
- Scaffold deployment rate tracked: 0/3 early → 2/3 consistently by Vienna

### 2.2 Drill Mode
- *der/die/das* errors don't resolve through conversation alone — structural problem
- Three levels: L0 conjugation, L1 lock (one template, learner reps), L2 vary (noun rotation)
- L2 is the key insight: rotate noun across genders → forces article variation
- 16 core verbs pre-seeded with Vienna-relevant phrases
- Drill → Anki: friction items only, earned not dumped
- The volleyball court use case: drill on laptop, open Anki on phone — cards not there

### 2.3 The Three-Layer Model
- Scene → Drill Pool → Drill → Anki
- Cards are earned through friction, not generated from exposure
- Architecture designed around how this specific learner actually learns
- Not designed upfront — emerged from six weeks of data

---

## Part 3 — Vienna: Real World Test (May 10–17, 2026)

### 3.1 What the System Did in the Field
- 70+ sessions, 265+ Anki cards, 640+ practice minutes
- Sessions before real interactions: hotel check-in practiced in the room before the lobby
- Scaffold phrases deployed in actual cafés: *Einen Moment bitte — ich überlege kurz*
- Recovery phrase fired when needed: *Entschuldigung — könnten Sie das bitte wiederholen?*
- Real hotel interaction: asked for beds pushed together — learned *zusammenschieben lassen*
- Georg neighborhood chat with Vera: introduced wife mid-session, asked gemeinsam vs zusammen

### 3.2 The Gap That Only Vienna Revealed
- Standing in a shop: shopkeeper says a phrase, you repeat it, you walk out
- The phrase lives in your head for 10 minutes
- No way to capture it without interrupting the moment
- The pipeline had no field capture layer — the gap was invisible until real use

### 3.3 The Phrase Library: Field-Validated Feature
- Design spec written the same day the gap was identified
- Built and shipped during the trip — not post-trip retrospective
- `!phrase german | english` — zero-friction typed capture
- Two-step voice capture: "save a phrase" → say it → LLM corrects + translates → confirm
- LLM prompt preserves Austrian German and informal register
- UX fixes from field testing: fuzzy threshold, transcription noise variants,
  auto-detect practice, paginated list, drill exit on capture trigger
- This is the mini-moi beta test producing a field-validated feature in real time

---

## Part 4 — The Dual-Agent Day (May 13, 2026)

### 4.1 The Architecture in Action
- `minimoi_cmd_bot`: structured pipeline — German domain, phrase capture, session review
- OpenClaw agent: dynamic ad-hoc assistant — city guide, navigation, memory, context
- First uninterrupted dual-bot day: all stability fixes validated in production
- Map links generated on demand, home base pinned, exploratory mode active

### 4.2 What This Demonstrates
- Two independent agents, different concerns, same underlying infrastructure
- `minimoi_cmd_bot` runs whether OpenClaw exists or not — model-agnostic by design
- OpenClaw adds the conversational intelligence layer without owning the pipeline
- Either agent can be replaced without touching the other
- This is the architecture working as designed — not as tested in isolation, but as lived

### 4.3 The Real-World Framing
- Not a demo. Not built to impress anyone.
- Built because the right tool didn't exist.
- Used in a real city, on real streets, with a real trip deadline.
- The system was wrong, fixed, and improved in real time.
- That's what a growing capability looks like.

---

## Part 5 — What v1.0 Closes (Post-Vienna)

### 5.1 The Open Loop: Scene → Drill Pool Gate
- Currently: scene sessions generate vocab cards directly to Anki
- Gap: cards arrive before the learner has drilled the item
- Fix: vocab routes to drill pool first, earns Anki through friction
- Closes the three-layer model completely

### 5.2 Scaffold Tracking
- Delivered but not yet measured: did the scaffold phrases deploy?
- `scaffold_used` / `scaffold_avoided` closes the delivery → deployment loop
- Avoided phrases become drill targets automatically

### 5.3 Noun/Article Drill (L0)
- *der/die/das* structural gap needs its own drill level
- Flash *Schlüssel* → type *der* — the gender table gets into muscle memory

### 5.4 HTML Administrative Layer — The Next Domain Surface
- The curator domain: HTML first, Telegram second
- The language domain: Telegram first, HTML next
- Sequence reversed — same destination
- What the HTML layer adds:
  - Session history and error trends across the full arc
  - Phrase library browser (by scene, by status, by practice count)
  - Drill pool visibility — what's pending, what's approved, what's been drilled
  - Anki deck management without leaving the system
  - New persona creation interface
  - Reading and writing practice surface — keyboard-first, not voice-dependent
  - Cross-session intelligence: when did the dative confusion actually resolve?

### 5.5 Cross-Domain Intelligence (v1.3)
- Neo4j activation: the language domain feeds the same graph as the intelligence domain
- Error patterns as nodes — when did *Meine Frau* stop appearing as *Mein Frau*?
- The backtrace vision applied to language: I struggled with dative in April. When did it close?

---

## Epilogue — What This Proves

The mini-moi thesis: specific intelligence, for a specific person, in a specific situation.

The curator domain proved the pipeline. The language domain proved it generalizes.
The phrase library proved the feedback loop — the system observed its own gap and closed it.
The dual-agent day proved the architecture holds under real conditions.

What's next isn't a new feature. It's a new domain. The pipeline is ready.
The question is always the same: what specific problem, for what specific person,
in what specific situation?

---

## Appendix — Data at Vienna Trip End

| Metric | Value |
|---|---|
| Total sessions | 70+ |
| Voice sessions | 41+ |
| Writing sessions | 13+ |
| Drill sessions | active |
| Phrase library entries | 8+ |
| Anki cards | 265+ |
| Practice minutes | 640+ |
| Active personas | 9 |
| Core drill verbs | 16 |
| Tests passing | 49/49 |
| Dual-bot uptime | First clean day: May 13 |

---

*mini-moi Language Domain Case Study — Outline v1 — May 2026 — Robert van Stedum*
*Full draft to follow post-Vienna with session data, error arc, and epilogue*
