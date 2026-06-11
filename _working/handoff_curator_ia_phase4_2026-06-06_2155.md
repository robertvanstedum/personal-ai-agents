# Handoff — Curator IA Phase 4
*mini-moi · personal-ai-agents · Curator domain*

- **Authored:** 2026-06-06 21:55 CDT (2026-06-07 02:55 UTC) — Claude.ai, design session
- **Status:** READY TO BUILD — approved by Robert
- **Branch:** `feat/curator-ia` (same as Phase 1–3)
- **Prerequisite:** Phase 3 complete and signed off (Leanings sidebar removed, Observations folded in, `/observations` and `/priorities` redirected)
- **Scope:** Clean up the Desk page (rename, remove portal sidebar, group threads), add `/research` redirect. No changes to Scans & Dives.

---

## Context: what Phase 3 left

The Desk page (`DESK` tab, far right of top nav) currently shows:
- Heading: **"Research Intelligence"** — needs rename
- Left sidebar: **Curator / RESEARCH / Language / Jobs** — the mini-moi portal domain switcher, should not appear on a Curator page
- Thread list: all threads in one undivided list (active and never-run mixed together)
- Budget tracker, agent status — both correct, keep as-is

Scans & Dives is Phase 4-stable — no changes needed.

---

## Phase 4 tasks (in order)

### Task 1 — Rename heading and remove portal sidebar

**1a. Rename heading**
In the Desk template, change:
- `Research Intelligence` → `The Desk`
- Subtitle stays: `Your active research threads.`

**1b. Remove the portal-level left sidebar**
The sidebar showing `Curator / RESEARCH / Language / Jobs` is the mini-moi domain switcher. It should not appear when navigating within the Curator domain via the `DESK` tab.

Check how the sidebar is injected — likely one of:
- A base template / layout flag (e.g. `{% extends "portal_layout.html" %}` → change to `{% extends "curator_layout.html" %}`)
- A conditional block driven by a route prefix or context variable
- An explicit `{% include "portal_sidebar.html" %}` in the Desk template

Fix whichever applies. The Desk page, when reached via the Curator `DESK` tab, should use the Curator layout (Curator top nav, no portal sidebar) — matching Reading Room, Scans & Dives, Leanings, and Archive.

If the same page is also reachable from the portal-level `RESEARCH` link in the mini-moi sidebar and that path *needs* the portal sidebar, handle it with a context flag or a separate route. But do not let the portal sidebar leak into the Curator layout.

**Test:** Load `/desk` (or whatever the Desk route is). No portal sidebar visible. Curator top nav present. `DESK` tab underlined.

---

### Task 2 — Group threads into two labeled sections

Currently all 9 threads render in one flat list. Split into two groups:

**Group 1 — "Active"** — threads where `sessions > 0`, ordered by session count descending:
- empire-landpower · hellscape-taiwan-porcupine · gold-geopolitics · strait-of-hormuz · china-rise (based on current data — the sort handles future changes automatically)

**Group 2 — "Not yet run"** — threads where `sessions == 0`:
- quad-flexibility-not · polls-open-colombia · china-ai-local-llms · china-and-taiwan

**Row structure (keep existing HTML as close as possible, adjust layout only):**

| Element | Active threads | Not-yet-run threads |
|---|---|---|
| Thread name | full weight, primary color | muted weight, secondary color |
| Stats | `N sessions · N saved · N candidates` (muted) | `0 sessions` (muted) |
| Action | `Generate deeper dive →` or `View deeper dive →` (accent) | `never run` (muted, no link) |

**Section kickers:** mono, uppercase, small, muted — same styling as the "DIVES 5" and "SCANS 30" kickers on Scans & Dives. Place the kicker above each group's first row.

```
ACTIVE                                     [kicker above first group]
──────────────────────────────────────────
empire-landpower    14 sessions · 3 saved · 8 candidates    Generate deeper dive →
hellscape-taiwan-porcupine    10 sessions                    View deeper dive →
...

NOT YET RUN                                [kicker above second group]
──────────────────────────────────────────
quad-flexibility-not    0 sessions                           never run
...
```

Budget bar and agent status at the bottom remain unchanged.

**Test:** Page renders two labeled groups. Active threads appear before not-yet-run. Adding sessions to a never-run thread should move it to the Active group on next render (this is automatic if the sort is live from JSON data).

---

### Task 3 — Redirect `/research` → `/desk`

The old Research tab route almost certainly still exists from the pre-scaffold architecture. Add a 301 redirect:

```python
@curator.route('/research')
def research_redirect():
    return redirect(url_for('curator.desk'), 301)
```

Adjust route names to match actual blueprint/function names in the codebase.

**Test:** `curl -I http://localhost:8766/research` returns `301` with `Location:` pointing to the Desk route.

---

## Definition of done

- [ ] Desk heading reads "The Desk" — not "Research Intelligence".
- [ ] No portal sidebar (Curator/RESEARCH/Language/Jobs) visible on the Desk page.
- [ ] Curator top nav present with `DESK` active/underlined.
- [ ] Thread list shows two labeled sections: "Active" and "Not yet run".
- [ ] Active threads are ordered by session count descending.
- [ ] Never-run thread names and actions are visually muted (no live link on "never run").
- [ ] Budget bar and agent status unchanged and still rendering.
- [ ] `/research` redirects (301) to the Desk route.
- [ ] Scans & Dives is unchanged — regression check: still shows DIVES + SCANS sections.
- [ ] `python3 tools/health_check.py` passes.
- [ ] Re-run `python3 tools/capture_screenshots.py` — Desk screenshot should show new heading, no sidebar, two grouped sections. Confirm Scans & Dives screenshot unchanged.
- [ ] **Robert visual sign-off before push to main.**

If a step cannot be tested, say so explicitly — do not commit and claim done.

---

## What stays headless (do not build in Phase 4)

- **Sources management UI** — Sources are tracked in JSON; no UI needed until the data volume or workflow demands it.
- **Groups management UI** — Same. Exists in the data model; no screen yet.
- **Tag aliases UI** — Headless by design; not a Phase 4 concern.

These are future Desk sections. Note them in a `TODO` comment in the Desk template if helpful, but do not scaffold empty sections.

---

## Guardrails

- `main` is production — stays stable. Work on `feat/curator-ia`.
- JSON is source of truth — no DB dependency for any of this.
- Portal sidebar removal is the riskiest change (touches layout inheritance) — test it first before the thread grouping work.
- Daily briefing pipeline and Telegram delivery must be unaffected.
- Tasks in order: Task 1 → test → Task 2 → test → Task 3 → test → screenshots → Robert.

---

## Deferred (not Phase 4)

- **Phase 5:** Archive page — wire unified browse sections (Daily editions · Scans · Dives · Observations · Sources) from existing JSON.
- **Phase 6:** Hard cleanup — delete `/observations`, `/priorities`, `/research` templates and redirect stubs.
- **Scans & Dives thread display:** the three "Hellscape Taiwan Porcupine" thread rows are a data/naming issue (same topic, multiple thread objects). Future improvement: group by topic, surface the distinguishing hypothesis as the thread subtitle. Not a Phase 4 blocker.
- **Desk v2:** Sources, Groups, and Tags management UI sections.

---

## Suggested commit sequence

```
# after Task 1
git add templates/...
git commit -m "phase4: rename Desk heading, remove portal sidebar from Curator layout"

# after Task 2
git add templates/... routes/...
git commit -m "phase4: group Desk threads into Active / Not yet run sections"

# after Task 3
git add routes/...
git commit -m "phase4: redirect /research → /desk"

# after screenshots re-run and Robert sign-off
git add docs/screenshots/...
git commit -m "phase4: update screenshot baseline (Desk redesigned)"
```

---

*Curator IA Phase 4 · feat/curator-ia · ready to build · 2026-06-06 21:55*
