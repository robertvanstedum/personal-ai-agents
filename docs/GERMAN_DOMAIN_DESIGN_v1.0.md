# Mein Deutsch — German Domain Design Specification
## Version 1.0 — Baseline
**Date:** 2026-05-20
**Status:** Approved baseline — build target June 1, 2026
**Author:** Robert van Stedum + Claude.ai
**Repo:** github.com/robertvanstedum/personal-ai-agents

---

## 0. Vision

> *Duolingo knows what lesson you're on. Mein Deutsch knows why you're learning,
> where you're going, and what you specifically struggle with. The difference is
> memory and context — applied to your situation, not a generic curriculum.*

Mein Deutsch is the HTML interface for the German language domain of mini-moi.
The domain was built and validated during a real trip to Vienna (May 2026) using
Telegram as the primary interface. This document specifies the HTML layer that
completes the domain to v1.0 feature parity with the Curator model.

The interface is not a language-learning app. It is a **creative environment** —
part reading room, part writing desk, part practice studio. The design reflects
that intent at every level.

---

## 1. Design Philosophy

### The book metaphor
The interface is structured like a book:
- **Cover** — the landing page. Dark, atmospheric, minimal. Sets the tone.
- **Pages** — all working pages. Light parchment. Where you read, write, and practice.
- **Bibliography** — Archiv. What you've accumulated over time.

### Cover vs pages
| Surface | Tone | Purpose |
|---|---|---|
| Landing | Dark, atmospheric, café photo | Cover — draws you in |
| All working pages | Warm parchment | Pages — where you work |
| Nav bar | Dark warm, consistent | Anchor across all surfaces |

### The learning loop
Every design decision serves this chain:

```
Lesen → encounter unknown word → save to Wörter
Wörter → promote to drill → Üben
Üben → practice with persona → Telegram session
Schreiben → write about what you read → LLM correction
Everything → Archiv over time
```

### Duolingo vs Mein Deutsch
Duolingo presents a generic curriculum. Mein Deutsch knows:
- What you read today (Lesen feed, Austrian register)
- Which words you didn't know (Wörter, captured in context)
- Which scenes you struggle with (Üben session history)
- What you wrote and where the errors were (Schreiben correction arc)
- How your German has changed over months (Archiv error arc)

---

## 2. Information Architecture

### Page map

| Tab | Template | Purpose | Mobile |
|---|---|---|---|
| *(Landing)* | `german_landing.html` | Cover — navigation only | ✓ |
| Lesen | `german_lesen.html` | Browse Austrian news, read, capture words | Simplified |
| Schreiben | `german_schreiben.html` | Journaling, Im Kontext writing, word practice | ✓ |
| Üben | `german_uben.html` | Scene selector, Telegram bridge, drills | ✓ |
| Wörter | `german_woerter.html` | Saved words notebook, promote, export | ✓ |
| Archiv | `german_archiv.html` | Bibliography — saved articles, sessions, history | Desktop |
| Admin | `german_admin.html` | Personas, content weights, system config | Desktop |

### Navigation
All pages share a persistent dark nav bar (`#2A1F14`) defined once in
`german_base.html`. Tab names: Lesen · Schreiben · Üben · Wörter · Archiv · Admin.
Active tab: white text with terracotta underline. Inactive: `rgba(255,255,255,0.4)`.
Archiv and Admin: further muted on mobile (hidden or collapsed).

---

## 3. Design System

### Colour palette

| Token | Value | Usage |
|---|---|---|
| `--md-dark` | `#2A1F14` | Nav bar, dark hero overlay, text on parchment |
| `--md-parch` | `#F5F0E8` | Page background (all working pages) |
| `--md-parch-mid` | `#EDE7DC` | Sidebar, input backgrounds, secondary surfaces |
| `--md-parch-deep` | `#E0D8CC` | Borders, dividers on parchment |
| `--md-accent` | `#C68A5E` | Terracotta — logo, active states, headings, CTAs |
| `--md-text-primary` | `#2A1F14` | Body text on parchment |
| `--md-text-secondary` | `#7A5C3A` | Labels, bylines, secondary text |
| `--md-text-muted` | `#9A8270` | Hints, dates, tertiary text |
| `--md-strip-dark` | `#1a1008` | Photo strip placeholder background |

### Typography

| Role | Font | Size | Weight | Colour |
|---|---|---|---|---|
| Logo wordmark | Georgia, serif, italic | 17–20px | normal | `--md-accent` |
| Page heading | Georgia, serif | 36–52px (landing), 17–22px (pages) | 700 | `#fff` or `--md-text-primary` |
| Article headline | Georgia, serif | 16–18px | 700 | `--md-text-primary` |
| Body text | System sans-serif | 13–14px | 400 | `--md-text-primary` |
| Labels/tabs | System sans-serif | 11–13px | 400 | varies |
| Bylines/meta | System sans-serif | 10–11px | 400 | `--md-text-muted` |

### Photo strips
Each working page has a narrow photo strip (40–48px) below the nav bar.
In v1.0: dark placeholder `#1a1008` with small label text.
In production: real narrow Wien photo, long and thin, consistent aspect ratio.

| Page | Strip content |
|---|---|
| Lesen (reading) | Wien Stadtbild — city roofline |
| Schreiben | Schreibtisch — writing desk |
| Üben | Wien Außenaufnahme — Vienna exterior |
| Wörter | Notizbuch — open notebook |
| Archiv | Bibliothek — library shelves |
| Admin | No strip — functional page |

### Buttons
- Consistent height: 26–28px throughout each page
- No hover size scaling — size never changes on hover
- Border-radius: 14px for pill actions, 4px for utility buttons
- Primary action: `rgba(198,138,94,0.15)` bg, `rgba(198,138,94,0.3)` border, `#8B4513` text
- Secondary action: `rgba(42,31,20,0.08)` bg, `rgba(42,31,20,0.18)` border, `--md-text-secondary`
- Destructive: red border, red text — confirm before executing

---

## 4. Landing Page

### Purpose
The cover of the book. Entry point only. No content, no feed, no text boxes.
Bold and minimal. Sets the tone for the whole system.

### Layout
- Full viewport dark hero — the café photo with warm dark overlay
- `Mein Deutsch` centred, large italic serif, terracotta
- Subtitle: *Dein persönlicher Sprachraum* — small, muted, uppercase
- Four chapter navigation links below: Lesen · Schreiben · Üben · Wörter
- No other content

### Mobile
Same layout. Chapter names stack vertically. Full screen.

---

## 5. Lesen Page

### Purpose
Daily reading room. Browse Austrian headlines, read first paragraphs, capture
unknown words. Not a news aggregator — a curated reading practice tool.

### Two states

**State 1 — Browse (dark, photo dominant)**
- Full viewport hero with café photo and warm dark overlay
- *Lesen* heading and subtitle upper left
- Narrow headline strip pinned to bottom of viewport
- Strip shows: source label + headline only — no summaries
- Click headline → strip expands in place, showing 2–3 paragraphs + actions
- Pinned articles render first with pin indicator
- Empty state: *Artikel laden* prompt in strip
- Manual refresh button when pool has fewer than 2 items

**State 2 — Reading (parchment, two-column)**
- Triggered by clicking *Merken* or a headline in expanded state
- Dark nav persists. Wien cityscape photo strip below nav.
- Article bar: `← Liste` · `‹ 2/5 ›` · `↗ Artikel öffnen`
- Left column: article (headline, byline under headline, 2–3 paragraphs with
  overflow scroll, footer with feed nudges + Merken)
- Right sidebar (248px, `--md-parch-mid`): Wörter panel (tall, scrollable saved
  list) + Notizen panel (short, 2–3 lines)
- Highlight any text → translation popover → + In Bibliothek

### Article actions
| Action | Behaviour | Signal |
|---|---|---|
| ＋ Mehr davon | Feed nudge — soft positive | Accumulates in `lesen_feedback.json` |
| － Weniger | Feed nudge — soft negative | Accumulates in `lesen_feedback.json` |
| 🔖 Merken | Save to article pool — persists | Stays until explicitly dismissed |
| ↗ Artikel öffnen | Open source URL in new tab | No signal |

### Article pool logic
- Articles persist until explicitly dismissed (👍 or 👎 from saved page)
- Pinned (🔖) articles always render first
- Target pool size: 3–5 items
- Fetch on manual refresh or when pool drops below 2

### RSS sources (v1.0)
Non-paywalled Austrian/Vienna sources only. Config-driven via `config/lesen_sources.json`.

| Source | Feed | Register |
|---|---|---|
| ORF Wien | `https://rss.orf.at/wien.xml` | Local, public broadcaster |
| Vienna.at | `https://www.vienna.at/rss` | Vienna-specific |
| Der Standard Kultur | `https://www.derstandard.at/rss/kultur` | Culture, accessible |
| ORF Sport | `https://rss.orf.at/sport.xml` | Sport, colloquial |
| Wiener Bezirksblätter | TBC — verify live | Hyper-local, district news |

**Rule:** All sources must be non-paywalled. Verify on addition.
Remove immediately if paywall detected. Do not hardcode in Python —
`config/lesen_sources.json` is the source of truth.

### Word capture flow
1. Highlight text in article body → small popover appears
2. Popover shows: selected text (truncated if long) + translation
3. Translation: check `phrasebook.json` cache first, then LLM call
   (grok-3-mini first, Haiku fallback — existing `_call_llm` chain)
4. One-click *+ In Bibliothek* → saves to `phrasebook.json` with:
   - German word/phrase
   - English translation
   - Source sentence (context)
   - Article title (provenance)
   - Scene tag: `lesen`
5. Word appears in sidebar saved list immediately

### Mobile Lesen
- Simple pulled list — not pushed/auto-loaded
- Headline + source only, tap to expand summary
- Highlight word → translation popover → save (same flow)
- No sidebar, no two-column layout
- No Schreiben handoff button

---

## 6. Schreiben Page

### Purpose
Personal writing environment. Journaling, writing in response to readings,
and word practice. LLM correction inline. Reflective and personal.

### Three modes — one pane

Mode toggle at top: **Tagebuch** · Im Kontext · Wörter üben

**Tagebuch**
- Freeform. No agenda. Write anything.
- Rotating Vienna-themed placeholder prompts (stored in `german_domain.py`
  as `_TAGEBUCH_PROMPTS` — minimum 8 variants):
  - *Was hast du heute in Wien gesehen?*
  - *Beschreib deinen Morgen auf Deutsch.*
  - *Was war interessant heute?*
  - *Erzähl von einem Gespräch heute.*
  - etc.
- Optional LLM correction toggle — sometimes just write, no correction
- Entries saved with date to `writing_sessions.json`, capped at 50

**Im Kontext**
- Writing in response to a Lesen article
- Pre-filled from Lesen ✏️ handoff OR manually started
- Article context snippet above input (collapsible)
- LLM correction via `german_domain.correct_writing()`
- Correction display: original → corrected → notes (gender/case/word order)
- One-click phrase capture from correction view → Wörter

**Wörter üben**
- Write practice sentences using saved words
- Word from Wörter panel suggested as the focus
- Correction confirms correct usage

### Shared session history
Bottom of pane across all three modes:
- Last 10 entries: date, first line, mode badge, error count if corrected
- Click to expand full entry

### LLM correction
All correction calls go through `german_domain.correct_writing()`.
Returns `{corrected, notes}`. JSON parse fallback if LLM returns prose.

---

## 7. Üben Page

### Purpose
The practice hub and Telegram bridge. Scene and persona selection, drill tools,
Anki management. Desktop-primary but mobile-accessible for session launch.

### Main page layout
- Persona/scene cards: Maria (Kaffeehaus) · Herr Fischer (Hotel) · Georg (Lokal)
  + other configured personas
- Each card: persona name, scene context, last session date
- Select a persona → scene options appear → **Telegram-Sitzung starten** button
- Telegram launch: opens `@minimoi_cmd_bot` with scene pre-loaded in prompt

### Sub-pages (breadcrumb navigation)
Each sub-page follows *Üben › [Sub-page name]* pattern.

| Sub-page | Content |
|---|---|
| Szenen | Persona cards + scene selector + Telegram launch |
| Verb-Drill | Verb drill interface — L1/L2 modes, verb selection |
| Phrasen-Drill | Phrase practice from Wörter pool |
| Drill-Pool | Full drill pool visibility — pending, approved, drilled |
| Anki-Karten | Card generation, review, export |

### Telegram bridge
The Telegram session is the authoritative practice interface.
HTML selects the scene. Telegram runs the session.
The launch button assembles the correct session prompt and
provides it ready to paste into the Telegram bot.

### Mobile Üben
- Scene cards prominent — primary use case on mobile
- Telegram launch button large and accessible
- Drill tools accessible but secondary
- Admin hidden on mobile

---

## 8. Wörter Page

### Purpose
The saved words notebook. All vocabulary captured from reading.
Light, immediate, contextual. Not the full drill system (that is Üben).

### Layout
- Word list: German · English · source tag · date
- Filter bar: by source (Lesen/Phrase/Hotel/etc.), by status (new/practiced/promoted)
- Click a word row → expand: see full context sentence, source article,
  add a personal note, practice, promote to Üben drill pool
- Promote to Üben: moves word to `drill_pool.json` under appropriate verb
- Anki export: selected or all words → CSV

### Data source
`phrasebook.json` — same file written to by Lesen word capture,
Telegram `!phrase` commands, and Schreiben phrase capture.

### Mobile Wörter
Full list visible. Tap to expand word detail. Promote and export work on mobile.

---

## 9. Archiv Page

### Purpose
The bibliography at the back of the book. What you've accumulated over time.
Read-only. Organised chronologically. Three sections.

### Sections

**Gespeicherte Artikel**
Articles saved via 🔖 Merken in Lesen. Shows: headline, source, date saved,
number of words captured. Click to re-open in reading view.

**Schreib-Verlauf**
All writing sessions from Schreiben. Shows: date, mode badge, first line,
error count. Click to expand full entry and correction notes.

**Drill-Verlauf**
Completed drill and practice sessions. Shows: date, persona/verb, score.
Error arc chart: error types over time (simple Chart.js line chart,
computed from session JSON files — no Neo4j required for v1.0).

### Desktop only in v1.0
Archiv is not prioritised for mobile. Too much history to browse usefully
on a small screen. Available via nav but not optimised.

---

## 10. Admin Page

### Purpose
System configuration. Persona management. Content control. Functional,
minimal. Less art, more utility.

### Three sections

**Personas**
- List of all configured personas with name, scene, last used date
- Create new persona: name, role, location, register (Sie/du), personality notes,
  scenarios, scaffold phrases, prompt preview
- Fork existing persona: creates a versioned variant
- Edit: same form as create
- Delete: confirm before executing

**Quellen (Sources)**
- List of active RSS sources with status (active/paused/flagged)
- Add new source: URL, name, verify feed live
- Pause or remove a source
- Paywall flag: mark a source as paywalled → excluded from fetch
- Source preference signals: view accumulated feedback per source

**System**
- Drill thresholds (fuzzy match threshold, promotion threshold)
- LLM routing: which model for which task
- Export/backup: download `phrasebook.json`, `drill_pool.json`, `writing_sessions.json`
- Log viewer: last 20 error log entries

### Desktop only
Admin is not available on mobile.

---

## 11. Data Files

| File | Purpose | Created by |
|---|---|---|
| `config/phrasebook.json` | Master phrase/word store | Lesen capture, !phrase, Schreiben |
| `config/drill_pool.json` | Drill verb pool | Üben, !phrase drill promote |
| `config/lesen_articles.json` | Article pool — persists until dismissed | Lesen fetch |
| `config/lesen_sources.json` | RSS source list — config-driven | Admin |
| `config/lesen_feedback.json` | Feed nudge signals — accumulated | Lesen actions |
| `config/writing_sessions.json` | Writing history — capped at 50 | Schreiben |
| `config/persona_memory.json` | Rolling session context per persona | Üben sessions |

---

## 12. Flask Routes

### Existing (already built)
| Route | Template |
|---|---|
| `GET /` or `GET /lesen` | `german_lesen.html` |
| `GET /schreiben` | `german_schreiben.html` |
| `GET /uben` | `german_uben.html` |
| `GET /bibliothek` | `german_woerter.html` |
| `GET /admin` | `german_admin.html` |
| `GET /archiv` | `german_archiv.html` |

### New routes needed
| Route | Method | Handler |
|---|---|---|
| `GET /` | GET | Landing page (new) |
| `POST /api/lesen-refresh` | POST | `refresh_lesen_feed()` |
| `POST /api/lesen-action` | POST | `lesen_action(article_id, action)` |
| `POST /api/translate` | POST | `translate_phrase(phrase)` — cache first |
| `POST /api/save-phrase` | POST | `save_lesen_phrase(...)` |
| `POST /api/write-correct` | POST | `correct_writing(text, context)` |
| `POST /api/write-save` | POST | `save_writing_entry(...)` |

---

## 13. Build Sequence

### Pre-requisites (complete)
- `german_domain.py` extracted and in production ✓
- `html_server.py` Flask scaffold live on port 8767 ✓
- `static/german.css` design system locked ✓
- `templates/german_base.html` nav and base structure ✓

### Phase 1 — HTML skeleton (current)
Build all templates as HTML layout only. No backend calls.
All interactions are JS stubs. Screenshot each for review.

**Deliverables:**
- `german_landing.html` — cover page
- `german_lesen.html` — both states (browse + reading), layout only
- `german_schreiben.html` — three modes, layout only
- `german_uben.html` — scene cards + sub-page navigation stubs
- `german_woerter.html` — word list layout
- `german_archiv.html` — three section layout
- `german_admin.html` — three section layout
- CSS additions to `german.css`

### Phase 2 — Backend wiring
Wire each template to its API routes after Phase 1 is approved.
One page at a time, test after each.

### Phase 3 — Polish
Real Wien photos for each strip. Error arc chart in Archiv.
Mobile responsive pass. Performance check.

---

## 14. Constraints

- Vanilla JS only — no client-side frameworks
- All LLM calls go through `german_domain.py` — `html_server.py` stays thin
- Non-paywalled sources only — verify on addition
- No auto-load on Lesen page open — user pulls content manually
- Schreiben mode state lives in sessionStorage — no server state needed
- Admin and Archiv desktop only in v1.0
- Consistent button sizes throughout — no hover size scaling

---

## 15. Open Items (post-v1.0)

| Item | Notes |
|---|---|
| Real Wien photos for each strip | Exterior shots — city, streets, cafés |
| Lesen feed personalisation | Feedback signals influence scoring after accumulation |
| Error arc chart in Archiv | Simple Chart.js, session JSON files as source |
| Voice input in Schreiben (mobile) | Web Speech API for Tagebuch entries |
| Persona version history | Fork creates variant, original preserved |
| Neo4j cross-domain intelligence | v1.3 — error patterns as graph nodes |
| Archiv on mobile | Lower priority, deferred |

---

*Mein Deutsch — German Domain Design Specification v1.0*
*2026-05-20 — Robert van Stedum + Claude.ai*
*Reviewed: Grok (independent) — aligned*
*Build target: June 1, 2026*
