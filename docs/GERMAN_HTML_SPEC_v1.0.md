# German Language Domain — HTML Interface
## Design & Build Specification v1.0
**Date:** 2026-05-19
**Status:** Design approved — build target June 1, 2026
**Author:** Robert van Stedum + Claude.ai
**Review:** Grok + OpenClaw handoff pending
**Repo:** github.com/robertvanstedum/personal-ai-agents

---

## 0. North Star

> *The German domain reaches v1.0 when it has feature parity with the Curator model:
> a primary HTML interface + Telegram as the mobile channel. All domain logic lives
> in the language program. HTML and Telegram are thin display layers only.*

The HTML interface is not a dashboard. It is a **creative environment** — part
reading room, part writing desk, part language coach. You open it and something
fresh is waiting. You read, you react, you write. The system shapes the language.

The natural rhythm:

```
Lesen  → read something real in German → react, save, comment
Schreiben → write about it, or write freely → correction loop fires
Üben  → structured session with a persona → reinforce what you learned
```

Voice and writing are not separate modes. They are two entry points into the same
correction loop. You start however the moment calls for it.

---

## 1. Architecture Decision — Refactor First

**This is the most important decision in this document.**

### Current state (wrong)
`telegram_bot.py` holds most domain logic: drill routing, phrase scoring, persona
selection, session triggers, scaffold generation, phrase library commands. Telegram
is not a thin adapter — it is the application layer.

### Target state (right)
```
german_domain.py          ← all domain logic lives here
    ├── telegram_bot.py   ← thin adapter (routing + delivery only)
    └── html_server.py    ← thin adapter (Flask routes + rendering only)
```

Both channels call into `german_domain.py`. Neither holds logic.

### Why this must come first
Building the HTML interface on top of the current architecture means either:
- Duplicating all domain logic in the HTML server (unmaintainable), or
- Importing from `telegram_bot.py` into the HTML server (wrong dependency direction)

The refactor is two weeks of work that makes the HTML build possible and makes
both channels easier to test. Without it, every HTML feature requires touching
two files instead of one.

### Refactor scope
- Extract: session generation, drill engine, phrase library, scaffold logic,
  reviewer pipeline triggers, persona selection
- Keep in `telegram_bot.py`: message routing, voice transcription (Whisper),
  Telegram-specific formatting
- Keep in `html_server.py`: Flask routes, Jinja2 rendering, Web Speech API bridge
- New: `german_domain.py` with a clean Python API that both adapters call

### Caution
This refactor touches the most central file in the system. Do it in a feature
branch. Run all 49 tests after every extraction step. Do not merge until green.
Budget 3–4 days before any HTML work starts.

---

## 2. Tab Architecture

Six tabs. Each has one job. German names throughout — consistent with the
aesthetic and the purpose. Navigation mirrors Curator's left rail pattern.

```
┌─────────────────────────────────────────────────┐
│  mini-moi  🇩🇪                          [status] │
├──────┬──────────────────────────────────────────┤
│      │                                          │
│  📰  │  LESEN       ← daily driver, fresh content│
│  📝  │  SCHREIBEN   ← write / speak / correct   │
│  🎭  │  ÜBEN        ← session, persona practice │
│  📚  │  BIBLIOTHEK  ← library, lessons, drills  │
│  🗂️  │  ADMIN       ← personas, config          │
│  📦  │  ARCHIV      ← archive, history, error arc│
│      │                                          │
└──────┴──────────────────────────────────────────┘
```

**Tab order reflects the natural daily rhythm:**
Open the app → something fresh is waiting (Lesen) → read, react, write about it
(Schreiben) → practice with a persona (Üben) → manage and organize (Bibliothek,
Admin) → retire and review (Archiv).

Lesen is Tab 1 because it gives you a reason to open the app every day.
A fresh headline in German, something actually interesting, 3-5 items only.
You consume first, then produce. That's the learning rhythm.

---

## 3. Tab 1 — Lesen (Read)

**The daily driver. Open the app — something fresh is waiting.**

### 3.1 What it is

A lightweight German reading feed. 3-5 items per day, pulled from colloquial
German-language sources. Headlines, first 2-3 paragraphs, source attribution,
link to full article. Designed to keep you current with how everyday German is
spoken — not formal news analysis, not academic language, but the register you
actually encounter in cafés, on the street, and in casual conversation.

This is not Curator. Curator scores for geopolitical signal and analytical depth.
Lesen scores for **linguistic accessibility and cultural relevance** — is this
something a German speaker would actually talk about today? Is the language
conversational?

| | Curator | Lesen |
|---|---|---|
| Content | Geopolitics, finance, analysis | Culture, sport, local news, entertainment |
| Register | Formal, analytical | Conversational, everyday |
| Volume | 20 items daily | 3-5 items daily |
| Action | Deep dive, research | Read, react, write commentary |
| Depth | Full articles | First 2-3 paragraphs + link |

### 3.2 The feed

**Sources — curated, not open:**
Start with a small, reliable list of conversational German-language sources:
- *Heute* (Vienna tabloid — very colloquial, local flavour)
- *Kronen Zeitung* culture/entertainment section (Austria's biggest daily)
- *Spiegel Online* Panorama section (light, cultural, accessible)
- *Kicker* (football — World Cup, Bundesliga — natural sports language)
- *Wiener Zeitung* local section (Vienna-specific, useful for Austrian dialect)
- X/Twitter in German — trending topics, public figures, everyday register

**Scoring criteria (different from Curator):**
- Is the language register conversational rather than formal?
- Is the topic culturally relevant (sport, entertainment, local news, light politics)?
- Is it short enough to read in 2-3 minutes?
- Is it Austrian/Viennese content where possible? (builds local cultural context)

**Technical path:** Reuse Curator's existing RSS fetcher and `web_fetch` pattern.
New scoring prompt focused on register and accessibility rather than geopolitical
signal. This is the lowest-complexity path — infrastructure already exists.

**Caution:** Source curation matters more here than in Curator. The risk is the
wrong register — too formal (broadsheet news-speak) or too sensational (pure
clickbait). A curated list of 6-8 sources, reviewed and adjusted based on
thumbs feedback over the first month, is better than an open feed.

### 3.3 Item display

Each item shows:
- **Headline** — in German, large and readable
- **Source + date** — small, subdued
- **First 2-3 paragraphs** — body text, comfortable reading size
- **Three actions:** 👍 interesting | 👎 not relevant | 🔖 save for later
- **Pen icon** → opens Schreiben tab with this article as context

The save action adds the item to a reading list accessible from Archiv.
The thumbs signals feed back into tomorrow's scoring — same learning loop
as Curator's feedback mechanism.

### 3.4 The Lesen → Schreiben connection

This is the most important interaction in the tab. One tap on the pen icon:
- Jumps to Schreiben
- Article headline and first paragraph appear as context
- Prompt: *"Was denkst du darüber? Schreib 2-3 Sätze auf Deutsch."*
  (What do you think about this? Write 2-3 sentences in German.)
- Correction loop fires on submission
- New vocabulary from the article can be flagged directly to the phrase library

This is how Lesen feeds language production. You read something real,
you react to it in German, you get corrected on your reaction. The content
is self-motivating — you're not practicing for practice's sake.

### 3.5 Refresh and volume

**3-5 items only.** This is a deliberate constraint. Curator surfaces 20 items
because the use case is comprehensive daily intelligence. Lesen is a 5-minute
morning habit — just enough to read something interesting and maybe write a
few sentences about one of them.

Refresh daily (morning). Items persist until dismissed or until the next refresh.
If you didn't open the app yesterday, yesterday's items are still there — not
replaced, just older.

**Future:** Personalisation strengthens over time as thumbs signals accumulate.
Week 1 — generic feed. Week 4 — it knows you care about Austrian football
and Viennese local news and surfaces accordingly. Same Curator learning loop,
different content domain.

---

## 4. Tab 2 — Schreiben (Write / Speak)

**The creative environment. Daily use. Most important tab.**

### 3.1 Two entry modes, one correction loop

**Freeform / Journal**
An open text field — no prompt, no scenario, no instructions. Write anything:
what you did today, what you want to do this weekend, what you noticed on the
trip, what you're thinking about. The system processes it through the correction
loop when you submit.

This is the most important writing mode. Language acquisition happens fastest
when the content is personally meaningful. The system provides the correction;
you provide the reason to write.

**Guided / Post-session**
After a session, a structured prompt appears based on what you just practiced:
*"You just checked in to a hotel. Write 3 sentences about what you asked for
and what happened."* This reinforces the specific vocabulary and structures
from the session.

Both modes feed the same correction engine. The interface makes the mode
visible but doesn't restrict switching.

### 3.2 Voice entry

Browser-native Web Speech API (Chrome/Safari). No backend dependency, no
Whisper call needed. A microphone button in the writing field — click to
speak, transcript appears in real time. Then submit for correction.

**The reinforcement loop:**
1. Speak → see transcription appear
2. Submit → see corrected version
3. Read the correction
4. Type it yourself (optional but high-value — the act of retyping locks it in)

**Technical note:** Web Speech API is browser-only and requires HTTPS or localhost.
Works on MacBook without any backend changes. For production deployment, HTTPS
is a requirement. Flag this for the Mac Mini nginx config.

**Alternative path:** If Web Speech API proves unreliable on the target system,
fall back to a local Whisper call (already exists for Telegram). Would require
a small Flask endpoint. Note this as the fallback, not the first implementation.

### 3.3 Correction display

Show the corrected text alongside the original. Make errors visible without
being clinical:

```
You wrote:
  Ich bin gegangen zum Café und bestellt ein Kaffee.

Corrected:
  Ich bin ins Café gegangen und habe einen Kaffee bestellt.

Notes:
  • ins (= in das) — contraction before neuter accusative nouns
  • haben auxiliary — action verbs take haben, not sein, in perfect tense
  • einen — accusative masculine article (der Kaffee → einen Kaffee)
```

Gender and case errors get specific notes. Vocabulary substitutions get a one-line
explanation. Word order corrections show the rule, not just the fix.

**Design note:** The correction display is the most important visual element in
this tab. It should feel like feedback from a thoughtful teacher, not a red-pen
markup. Warm tone, clear structure, never condescending.

### 3.4 Phrase capture from writing

Any corrected phrase can be flagged directly into the phrase library from
the correction view. One click — no re-typing, no `!phrase` command.
The phrase pre-fills with the corrected German and the original English intent.

### 3.5 History

Last 10 writing sessions accessible from the tab. Each shows: date, first line,
error count, phrases captured. Click to review full correction.

---

## 5. Tab 3 — Üben (Session)

**Practice with personas. Voice or writing. Desktop-native.**

### 4.1 Session launcher

Select persona and scenario from the HTML UI. Replaces the Telegram
"next session" trigger for desktop use. Shows:
- Persona name and description
- Available scenarios for that persona
- Today's scaffold phrases (preview before starting)
- Carry-forward from last session

Hit Start — the assembled prompt appears ready to paste into Grok, OR
(future) auto-dispatches via the Grok API directly.

**Recommendation:** For v1.0, keep the paste-to-Grok pattern. Auto-dispatch
via API is the right long-term destination but adds complexity (session
management, streaming) that risks the June 1 target. Note it as v1.1.

### 4.2 Transcript submission

Paste the transcript from Grok into the HTML interface directly — an
alternative to the Telegram paste flow. Submit triggers the same
`reviewer.py` pipeline. Results appear in the browser.

### 4.3 Session results view

The reviewer output displayed in the browser:
- Error summary by type (gender, word order, verb conjugation, vocabulary)
- Scaffold deployment: which phrases were used, which were avoided
- Anki cards generated this session
- Next session recommendation
- Carry-forward phrases highlighted

**This is the view Telegram delivers as text.** In HTML it can be structured,
scannable, and linkable (click an error → see the example in context).

### 4.4 Voice on MacBook

Same Web Speech API as Tab 1. Speak your side of the session instead of
typing it. The transcript builds in real time. Submit when done.

**Caution:** Web Speech API transcription quality varies by accent and
background noise. For production sessions, the user may prefer to use
Grok Voice on iPhone (existing flow) and paste the transcript here.
Don't deprecate the paste flow.

### 4.5 Session history

Filterable list of all past sessions. Filter by persona, date range, error type.
Click any session to see the full transcript and reviewer output.
Flag individual exchanges for the phrase library.

---

## 6. Tab 4 — Bibliothek (Library)

**See and organize everything you have.**

### 5.1 Phrase library browser

Paginated, filterable view of all phrases in `phrasebook.json`.
Filter by: status (library / drilling / archived), scene, verb_hint,
practice count, date added.

Inline editing — fix German/English pairs, update verb_hint, change status.
No JSON editing required.

### 5.2 Lesson grouping

**New concept — not currently in the system.**

A lesson is a named collection of phrases with a theme:
*"Café ordering," "Hotel stay," "Asking for directions," "Small talk with Georg."*

- Create a lesson, give it a name and optional description
- Assign phrases to one or more lessons (many-to-many)
- Mark lessons as active, backlog, or archived
- Active lessons feed the drill pool; backlog is visible but dormant

**Implementation note:** Lessons are a new data structure. Options:

Option A — Add to `phrasebook.json`:
```json
{
  "lessons": [
    {"id": "ls_001", "name": "Café ordering", "status": "active",
     "phrase_ids": ["ph_20260512_001", "ph_20260513_003"]}
  ]
}
```

Option B — Separate `lessons.json` file alongside `phrasebook.json`.

Recommendation: Option B. Keeps files focused and matches the existing
pattern (drill_pool.json, phrasebook.json). Easier to back up and inspect.

### 5.3 Drill pool visibility

Current state of `drill_pool.json`:
- Core verbs (16) with phrase counts
- Session-fed pending items
- On-demand log

Visual: a grid of verb cards, each showing phrase count, last drilled date,
and a mini progress indicator. Click a verb to see all its phrases and drill
directly from the browser.

### 5.4 Media intake — articles and X posts

**New channel — replaces the video/YouTube idea with something more buildable.**

Paste a short article URL or X post URL. The system fetches the text
(same pattern as Curator's source fetcher), displays it, and prompts:
*"Read this. Write 3–5 sentences in German about what it says or what
you think about it."*

That writing goes through the correction loop (Tab 1 engine).
New vocabulary from the article can be flagged directly to the phrase library.

**Scope constraint for v1.0:**
- Paste URL → fetch text → display → write commentary
- No audio, no video, no auto-transcription
- Target: 200–500 word articles or X posts/threads
- Sources: German-language news sites, Austrian media, X in German

**Technical path:** Reuse Curator's existing `web_fetch` / `requests` pattern.
One new Flask endpoint: `POST /api/media/fetch` → returns cleaned text.
No new dependencies.

**Caution:** Some sites block scraping. Start with a curated list of
reliable German-language sources (Der Standard, ORF, Zeit Online)
rather than arbitrary URLs. This mirrors Curator's trusted-source approach.

---

## 7. Tab 5 — Admin

**Create and manage personas, scenes, and system config. Weekly use.**

### 6.1 Persona editor

**Create new persona:**
- Name, role, location, age/background
- Register (formal Sie / informal du / flexible)
- Personality notes (what makes this persona distinctive)
- Scenarios: add/edit scenario labels and opening lines
- Scaffold phrases: add/edit the six-phrase bank (transaction, preference, recovery)
- Prompt preview: see the assembled `georg_local.txt`-style prompt before saving

**Fork existing persona:**
Select an existing persona → Fork → creates a versioned variant.
*"Maria café v2 — evening shift, quieter, fewer tourists."*
Original preserved. Fork gets a new persona ID and can diverge independently.

**Edit existing persona:**
Same form as create. Changes write to the persona's `.txt` prompt file.
Version history not required for v1.0 but note it as v1.1.

### 6.2 Persona context / memory

**The "hey, you're back" feature.**

Each persona maintains a rolling context window: last 3–5 sessions with
that persona, summarized into a brief memory string.

*"Guest has visited twice before. Ordered Verlängerten both times.
Mentioned wife Vera. Learning German for travel."*

This memory string is injected into the persona prompt at session start —
within scene guardrails. Maria the café waitress gets the memory but
doesn't suddenly know you're a product manager.

**Storage:** A `persona_memory.json` file per persona in the `prompts/` directory.
Lightweight — just the rolling summary and last 5 session references.
Not a full transcript store.

**Retention:** Last 5 sessions worth of summary. Older sessions drop off.
This is intentionally ephemeral — the goal is continuity within a learning
period, not a permanent record.

**Caution:** The memory injection adds tokens to every session prompt.
Monitor assembled prompt size — stay under the 4096 Telegram limit if
the session is also delivered via Telegram. HTML sessions have no such
limit. This asymmetry may require separate prompt assembly paths for
each channel. Flag for architecture review.

### 6.3 System config

- Active verb pool: add/remove verbs, edit conjugation tables
- Scaffold rotation settings
- Drill thresholds (fuzzy match, promotion threshold)
- Model routing: which LLM for which task

---

## 8. Tab 6 — Archiv (Archive)

**Retire without deleting.**

### 7.1 Archive flow

Phrases, verbs, lessons, and personas can be archived from any tab.
Archived items:
- Excluded from active drills and session selection
- Fully searchable and restorable
- Never deleted unless explicitly requested

### 7.2 Recall

Surface archived content on demand:
- *"Show me what I drilled in Vienna"* — filter archive by date range
- *"Show me all archived café phrases"* — filter by scene/lesson
- Restore any item to active with one click

### 7.3 Session history timeline

A chronological view of all sessions. Not a dashboard — a simple activity
log. Each entry: date, persona, scenario, error count, session duration.
Click to see full session review.

**The error arc view:**
A simple chart showing error type counts over time. When did gender errors
peak? When did they start declining? This is the cross-session intelligence
the case study calls out — and it requires no Neo4j, just the existing
session JSONs.

Implementation: a lightweight Chart.js or D3 line chart. Server computes
the aggregation from session JSON files on request. No new data storage.

---

## 9. Look and Feel

**Neubau, Vienna. Grungy-upscale. Editorial, not clinical.**

### 8.1 Aesthetic direction

The Neubau neighborhood is the reference: independent cafés, reclaimed wood,
aged plaster walls, deliberate imperfection alongside considered design.
Not sterile. Not gamified. A place where you'd sit for two hours and write.

Avoid:
- Progress bars and achievement badges (language app clichés)
- Clean white backgrounds with blue accents (SaaS generic)
- Any visual language that says "learning tool"

Go toward:
- Warm dark neutrals — charcoal, deep warm gray, aged parchment
- Accent: terracotta or deep teal (not both — pick one and commit)
- Generous whitespace alternating with controlled density
- Typography that feels editorial — a display font with character paired
  with a refined body font for readability
- Subtle texture — a very light grain or noise overlay on backgrounds

### 8.2 Typography direction

Display / headings: something with personality — consider a humanist serif
(like Playfair Display, Cormorant, or a condensed grotesque like Barlow
Condensed). Should feel like a Viennese newspaper masthead, not a tech product.

Body / interface: clean and readable — something like DM Sans, Epilogue,
or Syne. Not Inter, not Roboto, not system fonts.

Monospace (for German text display, corrections, transcripts): a slightly
warm monospace — IBM Plex Mono or Fira Code. The correction display
and transcript views should feel like a typewriter's output.

### 8.3 Mobile-first layout

Primary use case is still the phone for sessions. HTML is the management
and desktop layer — but it must work on mobile too.

Single-column below 768px. The tab rail collapses to a bottom nav on mobile.
Touch-friendly tap targets throughout.

### 8.4 The one unforgettable thing

When you open the Write tab to a blank field, the placeholder text should
feel like an invitation, not an instruction:

*"Was hast du heute gesehen?"* — What did you see today?

Rotating placeholders, one per session, drawn from real Vienna scenes. Not
"Enter text here."

---

## 10. Build Sequence

**Two weeks. June 1 target. Strict priority order.**

### Week 1 — Foundation (May 19–25)

**Day 1–3: Architecture refactor**
Extract `german_domain.py` from `telegram_bot.py`.
All 49 tests must pass after every extraction step.
Feature branch: `feat/german-domain-extraction`

**Day 4–5: Flask server scaffold**
`html_server.py` with basic routing.
Static file serving. Six-tab navigation skeleton with German names.
No functionality yet — just the shell with the right aesthetic bones.

**Day 6–7: Lesen tab — feed infrastructure**
RSS fetcher wired to 4-5 seed sources.
Item display: headline, paragraphs, three actions.
Thumbs feedback stored. No scoring personalisation yet — curated list only.
This tab launches the app with something to look at immediately.

### Week 2 — Features (May 26–31)

**Day 8–9: Schreiben tab — core correction loop**
Freeform text input → LLM correction → correction display.
Lesen → Schreiben handoff (article as context).
No voice yet. This is the MVP of the production side.

**Day 10: Voice entry**
Web Speech API in Schreiben tab.
Test on target MacBook before committing.
Whisper fallback endpoint documented if unreliable.

**Day 11–12: Bibliothek tab**
Phrase browser, filter, inline edit.
Lessons.json data structure and lesson grouping UI.

**Day 13: Üben tab**
Session launcher, transcript paste, results view.
Persona context/memory (lightweight — rolling 3-session summary).

**Day 14: Admin tab + look and feel pass**
Persona editor (create + fork).
Typography, colour palette, texture, German placeholder text.
Mobile layout check.

### Not in v1.0 (explicitly deferred)

| Feature | Reason | Target |
|---|---|---|
| Lesen personalisation / scoring | Needs thumbs data to train | v1.1 |
| Auto-dispatch to Grok API | Adds streaming complexity | v1.1 |
| Error arc chart | Nice-to-have, not blocking | v1.1 |
| Persona version history | Low priority | v1.2 |
| Archiv tab | Lower frequency, lower priority | v1.1 |
| Neo4j cross-session intelligence | Requires v1.3 infrastructure | v1.3 |

**Caution on scope:** Two weeks is tight for six tabs. The minimum viable
v1.0 is: Lesen (gives you a reason to open the app) + Schreiben (the core
production loop) + Bibliothek (organisation). If refactor runs long, cut
Üben and Admin to v1.1. Ship the reading-writing loop first.

---

## 11. Open Technical Questions

These are not blocking — they need answers before or during build.

**1. Flask vs FastAPI**
The Curator uses Flask. Staying with Flask is the consistency choice.
FastAPI offers async support which matters if the correction loop is slow
(LLM calls). For v1.0, Flask is fine — LLM calls are fast enough that
sync is acceptable. Revisit if latency becomes an issue.

**2. Jinja2 vs React**
Curator uses Flask + Jinja2. React would enable a richer, more interactive
correction display but adds build tooling. Recommendation: Jinja2 for v1.0,
migrate to React for the Admin tab in v1.1 when the form complexity justifies it.

**3. Web Speech API reliability**
Chrome on macOS is the target. Safari has a different implementation.
Test on the actual MacBook before committing to browser-native.
If unreliable, the Whisper fallback endpoint is a one-day addition.

**4. Prompt assembly for HTML sessions**
If HTML sessions can be longer than Telegram sessions (no 4096 char limit),
persona prompts can be richer. But if sessions must also work via Telegram,
the same prompt assembler must respect both limits. Two assembler paths
or one with a `channel` parameter? Decide before building the session launcher.

**5. Persona memory prompt injection**
How many tokens does the memory string add? Test with the current Georg
and Maria prompts at max assembled size. If it pushes either over the
Telegram limit, the memory feature may need to be HTML-only for v1.0.

---

## 12. GitHub Presence

This document commits to `docs/GERMAN_HTML_SPEC_v1.0.md`.

### Release note framing
The v1.0 language domain release represents the completion of the mobile-first
beta and the addition of a desktop creative environment. The sequence was
deliberately reversed from the Curator domain: Telegram first (mobile, Vienna
trip), HTML second (desktop, long-term use). Same architecture, same pipeline,
two channels.

### GitHub issue
File as: `feat: German domain HTML interface v1.0 — design approved`
Labels: `enhancement`, `language-domain`, `v1.0-milestone`
Milestone: June 1, 2026

### GERMAN.md update
Add a section: *"Desktop Interface (v1.0)"* — one paragraph, link to spec.
Update status from `v0.9 beta` to `v1.0 in progress`.

---

## 13. Handoff Notes

**For Grok:**
Review the architecture decision (Section 1) and the build sequence (Section 9).
The two-week timeline is tight — are there scope cuts you'd recommend to protect
the core Write/Speak + Library delivery? Also review the Jinja2 vs React question
and the Flask vs FastAPI question with current state of those ecosystems in mind.

**For OpenClaw:**
- Commit this spec to `docs/GERMAN_HTML_SPEC_v1.0.md`
- File GitHub issue: `feat: German domain HTML interface v1.0 — design approved`
  Labels: enhancement, language-domain, v1.0-milestone
- Update `GERMAN.md` status line: v0.9 beta → v1.0 in progress
- Update `OPERATIONS_ROADMAP.md`: add HTML interface as v1.0 milestone
- Append to MEMORY.md: German HTML spec approved 2026-05-19, build target
  June 1, architecture refactor first, Write/Speak tab is MVP

**For Claude Code (when build starts):**
Start with the architecture refactor. Feature branch `feat/german-domain-extraction`.
Do not touch HTML until `german_domain.py` exists and all 49 tests pass against it.
The spec is the source of truth — flag anything that conflicts with current
implementation before building around it.

---

*German Language Domain — HTML Interface Spec v1.0*
*2026-05-19 — Robert van Stedum + Claude.ai*
*Grok + OpenClaw review pending — build target June 1, 2026*
