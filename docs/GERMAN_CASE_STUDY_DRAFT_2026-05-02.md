# mini-moi — German Domain
## How a Language Learning Pipeline Learned to Actually Teach

*Case Study — v1 Draft · Epilogue to be written after Vienna*

---

## Origin — From Roadmap to Real Use Case

mini-moi was designed from the start as a multi-domain personal intelligence platform. The core loop — ingest → analyze → deliver → feedback → learn — was conceived as domain-agnostic. The first domain, news curation, proved the pattern. The language learning domain was always on the roadmap as the natural second test: could the same architecture that delivers a morning geopolitical briefing also coach someone through a conversation in a foreign city?

What moved language learning from roadmap item to active build was a concrete, time-boxed use case: a trip to Vienna approximately four weeks out. German is the target language — spoken at an intermediate level, read reasonably well, but with the persistent productive gap that plagues most adult learners. Understanding outpaces speaking. Transactions work. Open conversation freezes.

The Vienna trip didn't just create urgency. It created a real-world test with a fixed deadline and observable outcomes. The pipeline would either work in a Viennese café or it wouldn't.

The language domain was built and put into active daily use simultaneously — design-before-build discipline applied to the architecture, but real sessions from day one. What follows is the story of how those sessions reshaped the system.

---

| Sessions | Anki Cards | Minutes | Days to Vienna |
|---|---|---|---|
| 53+ | 213+ | 469+ | ~8 at time of writing |

---

## Act 1 — The Pipeline: Parse, Review, Learn

The initial design followed the same backend-first methodology as Curator: prove the pipeline works on real data before investing in UX. The core loop was straightforward — conduct a German conversation session with an AI persona, paste the transcript to the mini-moi bot, and the pipeline takes over.

| Stage | What happens |
|---|---|
| Parse | Transcript split into turns. Speaker identified. Metadata extracted. |
| Review | LLM analyzes Robert's turns only. Errors categorized by type. |
| Anki | Error phrases extracted as card candidates. Imported to deck. |
| Lesson | Next session generated with carry-forward from this session's gaps. |

Eight Vienna personas were designed from the start — each representing a real scene: Herr Fischer (hotel), Maria (café), Dr. Huber (museum), Stefan (U-Bahn), Frau Novak (pharmacy), Klaus (restaurant), Frau Berger (bakery), Anna (Airbnb).

The pipeline worked from session one. And in every session, without fail, the error logs showed **der/die/das/dem** errors. Measured perfectly. Not yet fixed.

---

## Act 2 — Writing Mode: See the Words

The first deliberate addition was writing mode — not from a feature request, but from a specific pedagogical insight. In voice sessions, errors disappear into the air. You say the wrong article, the persona corrects it in their next line, and the moment is gone.

Writing mode was designed to slow things down. See the words. Build muscle memory. Force the brain to construct sentences rather than speak them reflexively.

Writing sessions feed the same pipeline as voice. The reviewer has more signal to work with — spelling errors, word construction choices, and structural patterns are all visible. Early writing sessions revealed something voice sessions had hidden: spelling was phonetic-English, not German. *Shmecht leker* for *schmeckt lecker*. *Zwansig* for *zwanzig*.

Writing mode gave better visibility into the gaps. **der/die/das/dem** errors appeared in writing at the same rate as voice. The problem was not speaking under pressure. It was structural.

---

## Act 3 — Ease of Use: Getting Out of the Way

With voice and writing modes operational, the next friction point became the interface itself. The pipeline required exact trigger phrases. Voice messages were silently dropped. Transcripts had to arrive in a precise format.

Real language practice doesn't happen on a schedule with a clean interface. It happens on a phone, mid-morning, between other things. Every point of friction is a session that doesn't happen.

Three changes shipped in rapid succession:

- **Voice message handling via Whisper** — speak the trigger, don't type it
- **Natural language routing** — "next session", "café session", "again" all work without command prefixes
- **Two-message delivery** — briefing (what to prepare) separated from AI prompt (what to paste into Grok)

Scene-first trigger design emerged from a key observation: Robert remembers scenes, not persona names. "Pharmacy session" fires. "Frau Novak session" does not. The keyword map was redesigned around how a learner actually thinks.

The interface got out of the way. Sessions per day increased. **der/die/das/dem** errors: still there. Every session.

---

## Act 4 — Scaffold: Pre-loading the Output

By session 45, the error logs told a clear story. Vocabulary errors dominated — 70+ occurrences. But the session summaries told a different story: the comprehension was solid. The personas were understood. The scenes were navigated.

> Receptive competence was ahead of productive competence. The stall happened at sentence assembly speed, not vocabulary knowledge.

The insight that drove the scaffold design: the fix is not more input. More sessions, more listening, more reading would not fix the output stall. The fix is pre-loading complete, ready-to-deploy sentences into muscle memory before each session.

Three phrase types matched the specific stall patterns:

| Type | What it fixes | Example |
|---|---|---|
| Transaction sentence | Core scene action | *Ich hätte gerne einen kleinen Brauner, bitte.* |
| Preference expression | Active choice vocabulary under pressure | *Ich möchte lieber ein ruhiges Zimmer.* |
| Recovery phrase | What to say when completely frozen | *Einen Moment bitte — ich überlege kurz.* |

The scaffold block appears in Message 1 (YOUR BRIEFING) before each session — three phrases from the active persona's bank, rotating so the full bank of six surfaces across a week. Message 2 (the AI prompt) never sees the scaffold. This tests whether Robert deploys the phrases spontaneously under pressure, not whether he can recall them when prompted.

Scaffold phrases delivered. Output quality improving. **der/die/das/dem** errors in every session: still there. Reliably, persistently, mockingly there.

---

## Act 5 — Confronting der/die/das/dem Directly

Here is the honest diagnosis that took 50 sessions to fully articulate: **der/die/das/dem** is not a vocabulary problem. It is not a speaking-under-pressure problem. It is a structural acquisition problem — and conversational practice is the wrong tool to fix it.

This is documented in German acquisition research. Native speakers take years to fully internalize the declension grid. Conversational exposure reinforces patterns but does not drill the underlying table.

| Case | Masculine | Feminine | Neuter | Vienna usage |
|---|---|---|---|---|
| Nominative | der | die | das | Subject — *Der Schlüssel ist hier* |
| Accusative | den | die | das | After möchten — *Ich nehme den Schlüssel* |
| Dative | dem | der | dem | After mit — *mit dem Bus / mit der U-Bahn* |

The fix is not another conversational session. The fix is five minutes a day of mechanical repetition on this specific grid — the kind of drilling that language classrooms do with verb tables.

> The pipeline was built to observe and measure. Eight days before Vienna, it needed to drill. That required a new mode.

---

## Act 6 — Drill Mode and the Closing Loop

Drill mode repurposes the existing `--drill` infrastructure as a focused grammar drill with a 5-minute feedback loop. No persona, no scenario, no story. Three levels:

| Level | Trigger | What it does |
|---|---|---|
| 0 — Conjugate | `conjugate können` | Verb table fill-in-the-blank |
| 1 — Lock | `drill tatsächlich` | Word in one fixed template, learner controls reps |
| 2 — Vary | `drill hotel nouns` | Rotate noun across genders — forces article variation |

Three-tier word selection gives agency while keeping the system intelligent:

| Tier | Source | Example |
|---|---|---|
| Core | Pre-loaded Vienna-critical verbs and nouns | `drill können` / `drill hotel nouns` |
| Session-fed | Errors promoted from session logs | `drill my mistakes` |
| On-demand | Natural language — request exactly what you want | `drill mit + dative` |

Scaffold tracking closes the final loop. `scaffold_used` and `scaffold_avoided` track whether Robert deployed the scaffold phrases in each session. Phrases consistently avoided become drill targets automatically — the scaffold identifies the gap, the drill closes it, the reviewer tracks whether it closed.

The loop is now complete: session → error measurement → scaffold delivery → deployment tracking → drill targeting → Anki reinforcement → next session. **der/die/das/dem** has a place in the loop now. It took 50 sessions to get here.

---

## Act 7 — The Volleyball Court

Two weeks of daily practice. 280+ Anki cards. And a concrete moment at a volleyball tournament that made the design gap impossible to ignore.

Drilling *können* on a laptop at a café during breaks. Later, in the gym with time between matches, opening Anki on a phone to reinforce what had been drilled. The 6 phrases from that drill session weren't there. The work had been done. The system had thrown it away.

> You drill it on your laptop. You open Anki on your phone. It isn't there. The drill results evaporate. You are doing the work twice — when they should be the same thing.

But the volleyball court use case revealed a second, deeper problem: the 280 cards in Anki had arrived without being drilled at all. Every scene session generated 15–20 vocabulary cards directly — words encountered but not yet produced under pressure. Anki was receiving unearned cards faster than they could be reviewed.

> Spaced repetition reinforces what you've practiced. It cannot substitute for the practice itself.

The fix: a three-layer model where scene feeds the drill pool, drill feeds Anki — and Anki only receives friction items. Cards earned, not dumped. Phase A (drill → Anki on session end) ships before Vienna. Phase B (scene → drill pool gate) ships after.

---

## The Architectural Lesson

mini-moi's core loop was designed for news curation. The German language domain stress-tested it harder than any information domain could.

| Phase | Built | Learned |
|---|---|---|
| Domain launch | Session pipeline: parse → review → Anki → lesson | The infrastructure was right. The intelligence about what this specific learner needed had to be discovered through use. |
| Writing mode | Second session type, same pipeline | Errors visible in writing that voice hides. Some gaps are structural, not situational. |
| Ease of use | Voice triggers, NL routing, two-message delivery, scene-first keywords | Friction is sessions that don't happen. System must match how a learner thinks. |
| Scaffold | Pre-loaded phrase banks per persona, rotating, recovery phrases | Receptive ≠ productive. Output stall is sentence assembly speed. Pre-loading complete sentences works. |
| Drill mode | Short feedback loop, three-tier word selection, article/case grid | Conversational practice cannot fix structural gaps alone. Some problems need mechanical repetition. |
| Scene → Drill → Anki | Three-layer model, earned cards only | Delivery ≠ deployment. The system needs to know what the learner avoids, not just what it delivers. |

> The pipeline was right from the start. What evolved was the intelligence about what this specific learner needed, at this specific stage, with this specific trip 8 days away. That intelligence could not have been designed upfront. It emerged from the data.

This is the mini-moi thesis in practice: not a generic AI tool, but specific intelligence that knows you. The German domain proved the architecture is extensible. Every domain that follows will inherit the loop and compress the learning curve.

---

## Epilogue — To Be Written After Vienna

This case study is a first draft. The most important section is not yet written.

After the Vienna trip, the epilogue will answer:

- Did the scaffold phrases deploy in real Viennese cafés and hotels?
- Did the *der/die/das/dem* drill move the needle in 8 days?
- Which personas were most used — and which scenes caused the real freezes?
- What did the system miss that only real-world use revealed?
- What does the language domain build next?

The pipeline will have session data from Vienna itself. The epilogue writes itself from the data.

---

*mini-moi German Domain — Case Study v1 Draft — May 2026 — Robert van Stedum*
