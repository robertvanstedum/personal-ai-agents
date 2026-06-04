# BUILD PLAN — Curator information architecture redesign

- **Project:** mini-moi · personal-ai-agents · Curator domain
- **Authored:** 2026-06-04 13:54 CDT (18:54 UTC) — Claude.ai, design
- **Status:** QUEUED — register for later. Do not start build this session.
- **Branch (proposed):** `feat/curator-ia` off `main` (NOT `guild` — that's spine work; NOT `main` directly)
- **Handoff:** Claude.ai authored → Grok review pass recommended → Claude Code implements + commits → Robert confirms before every push
- **Scope (one line):** Reorganize Curator into six pages on a pipeline spine, split the overloaded "Research" tab, fold Observations into Leanings, and ship the four-button landing with fixed evergreen copy.

---

## 1 — Purpose

Today's Curator nav muddles three jobs across shared surfaces. Two specific tangles:

1. **"Research" does two jobs at once** — it holds both the *artifacts* (scans, dives) and the *config* (what topics/threads are tracked). Output you browse vs. a control panel you set.
2. **Library and Archive overlap** because they're split on the wrong axis (kind-of-thing) instead of the right one (active vs. permanent).

The fix is to let the existing tagline be the navigation:

> targeted search → analysis → opinion/doubt → long-term archive

Each stage becomes a room; one control surface (the Desk) sits off the flow because it *steers* the machine rather than being part of it.

---

## 2 — Locked decisions (this session)

- **Six pages in Curator:** Daily · Reading Room · Scans & Dives · Leanings · Archive · Desk.
- **Library → Reading Room.** Active working set: saved articles flagged to read or act on, with Read / Scan / Dive actions.
- **Archive** = the permanent, searchable record. Split from Reading Room on the active-vs-permanent axis; an item graduates from Reading Room to Archive once handled.
- **"Research" tab splits:** artifacts → Scans & Dives; config (Topics, Sources, Groups, research threads, tag aliases) → Desk.
- **AI Observations → Leanings** (a strip inside Leanings; the AI's reads sit next to your positions). The standalone Observations tab is retired.
- **Focus → Desk** if/when it is built (it's the attention lever, same family as Topics). Not building Focus now.
- **Daily keeps its name.**
- **Landing page = four entry buttons,** not all six. Buttons: Daily · Scans & Dives · Leanings · Archive. The front-page research entry is Scans & Dives (the alive output); the Desk is an in-app destination only. Reading Room and Desk are reachable from the in-app top nav.
- **Landing card bodies:** counts/details removed; fixed evergreen copy now (see §6). This is the version that ships and gets screenshotted for the public GitHub repo.

## 3 — Open decisions (need Robert before build)

- **Landing background image.** Options reviewed: A `hero-shadow-10` (bookshop/street), B `hero-shadow-11` (neoclassical hall), or none. Recommendation: **B** — radial fade keeps the cleanest area under the masthead and cards, texture pushed to margins. Not yet confirmed. (Check card-strip legibility on the live render at full resolution before locking.)

---

## 4 — Target IA

Top nav order (follows the pipeline; Desk set apart on the right):

`Daily · Reading Room · Scans & Dives · Leanings · Archive` ............. `Desk`

Pipeline mapping:

| Stage | Room |
|---|---|
| intake (targeted search) | Daily |
| triage | Reading Room |
| analysis | Scans & Dives |
| opinion / doubt | Leanings |
| long-term archive | Archive |
| *control (off the flow)* | Desk |

Object → home (one home each, nothing orphaned):

| Object | Home |
|---|---|
| Source (saved article) | Reading Room (active) → Archive (retired) |
| Scan | Scans & Dives → Archive |
| Dive (thread) | Scans & Dives → Archive |
| Leaning | Leanings |
| Observation | Leanings (strip) |
| Topic, Group, research thread, tag_aliases, Focus | Desk |
| Note | attached to whatever it annotates |

---

## 5 — Page specs

JSON remains source of truth for every page (Postgres/Neo4j are a rebuildable projection; daily use never depends on the DB).

**Daily** — keep as-is. Today's scored briefing (top 20 to portal, top 10 to Telegram). No structural change.

**Reading Room** (was Library) — the active saved set. Same table content as today's Library (Date, Title & Note, Source, Category, Score, Type, Actions), filters (All / Liked / Saved / X Posts / categories). Rename only for this build; the active-vs-permanent lifecycle (items graduating to Archive) is a follow-on, not required for v1.

**Scans & Dives** — keep page; confirm it surfaces all research artifacts that were reachable under the old "Research" tab. Dives (THREAD objects: sessions · sources · cost) and Scans listed as today.

**Leanings** — standalone page. Question → Lean → Hold gradient with evidence and AI teammate reads. **Add an Observations strip** (AI's unprompted reads) sourced from the existing observations JSON. No AI inference triggered on page load.

**Archive** (new) — unified, searchable, read-only browse with sections: Daily editions · Scans · Dives · Observations · retired Sources. Search across time. Reads existing JSON; no new data model.

**Desk** (new) — the control surface. Manage Topics (state machine: active-pull / dormant-on-radar / one-shot), Sources/feeds, Groups, research threads, tag aliases (currently headless — keep so unless a thin UI is wanted). Focus lands here later. This is where threads are *set up* (a control action), distinct from Scans & Dives where finished work is *read*.

**Landing** — masthead + four-card catalog. Background per §3. Card copy per §6. Tagline retained: *targeted search → analysis → opinion/doubt → long-term archive*. Buttons link Daily / Scans & Dives / Leanings / Archive.

---

## 6 — Landing card copy (FIXED, ships now)

Per-card body = kicker (mono, uppercase) + line (serif) + link (accent, mono, uppercase). No live data, no counts, evergreen, takes no position — safe for a public repo screenshot.

| Card | Kicker | Line | Link |
|---|---|---|---|
| Daily | Daily briefing | The day's stories, scored and ranked by morning. | Today › |
| Scans & Dives | Research | Where a quick read becomes a deep dive. | Explore › |
| Leanings | Working views | Open questions, held until the evidence lands. | Review › |
| Archive | The stacks | Everything kept, and searchable over time. | Browse › |

Design tokens (existing system, do not invent new ones):

- nav `#2A1F14` · parchment `#F5F0E8` · card `#EDE7DC` · accent `#C68A5E` · border `#C4B49A`
- muted text `#8A7060` / `#A89880`
- Georgia serif for headlines/titles; monospace for tabs, kickers, dates, links
- card tab bar: dark `#2A1F14`, mono uppercase, letter-spacing ~0.14em

---

## 7 — Migration map (old → new)

| Old | New |
|---|---|
| Daily | Daily (unchanged) |
| Library | Reading Room (rename) |
| Scans & Dives | Scans & Dives (absorbs artifacts surfaced under old Research) |
| Observations / AI Observations | folded into Leanings (strip); standalone tab retired |
| Research | split: artifacts → Scans & Dives; config (Topics/Sources/Groups/threads/tags) → Desk; Leanings → its own page |
| Priorities (legacy) | already replaced by Investigate modal; no page |
| Focus button | → Desk (deferred) |
| — | Archive (new unified browse) |
| — | Desk (new control surface) |

Add temporary redirects from old routes (`/library`, `/research`, `/observations`) to new homes so nothing breaks mid-migration; remove only after the new nav is confirmed.

---

## 8 — Build sequence (phased; test gate + Robert sign-off per phase)

Bones first — put the page containers in before moving content into them.

1. **Landing page.** Build from `_working/curator-landing-build-spec.md`, refined by §6 (fixed copy) and §3 (background pick). Run `tools/capture_screenshots.py`. Robert visual sign-off → push. *(This is the most self-contained piece and the one that screenshots for GitHub.)*
2. **Six-tab nav scaffold.** Introduce new nav order + empty/placeholder Archive and Desk routes. Old pages still reachable via redirects. Test routes (curl/200s). Screenshot. Sign-off.
3. **Rename + fold.** Library → Reading Room. Observations strip into Leanings (read existing JSON). Retire standalone Observations tab. Test. Sign-off.
4. **Split Research.** Route artifacts to Scans & Dives; move Topics/Sources/Groups/threads/tags config to Desk. Test. Sign-off.
5. **Archive page.** Wire the unified browse sections from existing JSON. Test. Sign-off.
6. **Cleanup.** Remove dead routes/redirects after the new nav is confirmed stable.

Each phase tests what's testable (curl, pytest, direct invocation). Robert tests UI flows end-to-end. No phase merges to `main` until Robert confirms. Re-run the screenshot baseline at the end.

---

## 9 — Definition of done

- All six pages reachable from the new nav; landing shows four buttons with the §6 copy.
- Old routes redirect (then are removed in phase 6); nothing 404s mid-migration.
- Observations visible inside Leanings; no standalone Observations tab.
- Topics/Sources/Groups/threads live on the Desk; artifacts on Scans & Dives.
- Daily briefing pipeline and Telegram delivery unaffected (regression-checked).
- Screenshot baseline re-captured and committed to `docs/screenshots/curator/current/`.
- If a step can't be tested, that is stated explicitly — not committed and claimed done.

---

## 10 — Deferred / v2 (write the note now so it isn't forgotten)

- **Live teaser headlines on landing cards.** Pull the *latest item* per room from existing JSON (no background inference — fits "spend follows attention"). Replaces the fixed §6 copy on the three living rooms; Archive can show a live count.
  - **Gate (required before v2 ships):** a sanitization rule for what is allowed to surface on a public/guest-facing surface — no charged or sensitive items on the front door. Guest search stays generic.
- Reading Room active-vs-permanent lifecycle (items auto-graduating to Archive).
- Optional thin UI for tag aliases on the Desk.

---

## 11 — Guardrails

- **Branch discipline:** `main` stays production-stable; do this on `feat/curator-ia`; do not merge until Robert signs off.
- **JSON is source of truth;** DB not a dependency for any of this.
- **Test-before-commit;** Robert confirms before every push on visual/production-touching changes.
- **One agent active at a time.** Claude Code owns all git.

---

## 12 — Git action for now

Claude Code (via OpenClaw command) to commit this plan as a queued artifact — work itself deferred:

```
git add _working/BUILD_PLAN_curator-ia-redesign_2026-06-04_1354.md
git commit -m "plan: queue Curator IA redesign (six-page model, four-button landing, fixed copy)"
```

Suggested repo location: `_working/`. Branch creation (`feat/curator-ia`) is deferred to build time, not now.

---

*mini-moi · personal-ai-agents · Curator IA redesign · queued 2026-06-04 13:54*
