# Handoff — Curator IA Phase 3
*mini-moi · personal-ai-agents · Curator domain*

- **Authored:** 2026-06-06 21:45 CDT (2026-06-07 02:45 UTC) — Claude.ai, design session
- **Status:** READY TO BUILD — approved by Robert
- **Branch:** `feat/curator-ia` (same as Phase 1–2)
- **Prerequisite:** Phase 1 (landing) and Phase 2 (six-tab nav scaffold) confirmed done; baseline screenshots at `docs/screenshots/curator/current/` (9 PNGs + PDF, 2026-06-06 16:08)
- **Scope:** Three changes. Remove vestigial sidebar from Leanings, fold Observations into Leanings as a second section, add nav redirects.

---

## Context: what Phase 2 left

The six-tab nav is live: `DAILY · READING ROOM · SCANS & DIVES · LEANINGS · ARCHIVE · DESK`.
Subnav standardized across all 9 pages. Two pages need Phase 3 attention:

- **Leanings** — still carries the old Research domain left-sidebar (Dashboard / Sessions / Leanings / Observations / Scans & Dives). With Leanings now a top-level tab that sidebar is dead.
- **Observations** — still a standalone page in the baseline (`OBSERVATIONS · 2026-06-06 16:08`). Needs to be folded into Leanings and retired from nav.

The Priorities page is also still in the baseline (8 expired, "being retired" banner) — redirect handled in this phase, hard cleanup in Phase 6.

---

## Phase 3 tasks (in order)

### Task 1 — Remove Leanings left sidebar

**File to edit:** the Leanings Jinja2 template (likely `templates/curator_leanings.html` or the Research subnav include it pulls in).

Remove the left-sidebar block entirely. The sidebar currently shows:
```
🔬 Research
  Dashboard  Sessions  Leanings  Observations  Scans & Dives
```
These items are either top-level tabs in the new nav or being folded in — none of them belong in a Leanings sidebar.

**Result:** Leanings becomes single-column, matching Reading Room and Scans & Dives in layout.

**Test:** `curl -s http://localhost:8766/leanings | grep -i "dashboard\|sessions\|sidebar"` should return nothing. Visual check: no sidebar visible.

---

### Task 2 — Add AI Observations section to Leanings page

**Below the existing Leanings list**, add the following HTML structure (adapt to the Jinja2/CSS conventions already in the template):

```
[existing Leanings content — form + list — unchanged above this line]

<!-- section divider -->
<div class="section-divider">
  <hr class="rule-left">
  <span class="divider-label">AI observations</span>  ← mono, uppercase, muted
  <hr class="rule-right">
</div>

<!-- today's observations -->
<div class="observations-header">
  <span class="section-kicker">Today's observations</span>
  <nav class="date-nav">← prev | Jun 6 · today | today →</nav>
</div>
<div class="observations-body">
  <!-- render observations for selected date from JSON, or fallback: -->
  <p class="empty-state">No observations for today yet.</p>
</div>

<!-- weekly connections -->
<div class="weekly-connections">
  <div class="weekly-header">
    <span class="section-kicker">Weekly connections</span>
    <span class="last-updated">last updated [date from JSON]</span>
  </div>
  <details>
    <summary>Show weekly connections</summary>
    <!-- render weekly connections content from JSON -->
  </details>
</div>
```

**Data source:** find the existing `/observations` Flask route — it already reads Today's Observations and Weekly Connections from JSON. Extract that data-loading logic into a shared helper or call it directly from the Leanings route so the same data renders on the Leanings page.

**Decisions already made (do not change):**
- Do NOT add the "5 responses saved" count to the merged page. That was metadata from the standalone page and does not belong on the merged view.
- Weekly Connections stays as a collapsible (`<details>`/`<summary>` or equivalent). Do not change to always-expanded.

**Test:** Navigate to `/leanings`. Both sections should render. Date navigation (prev/today) should work if the Observations route already supports it. Weekly Connections should toggle. `curl -s http://localhost:8766/leanings | grep -i "observations"` should return content.

---

### Task 3 — Add redirects, remove Observations from nav

**3a. Redirect `/observations` → `/leanings`**
Add a Flask redirect route in the Curator blueprint (or wherever nav routes live):
```python
@curator.route('/observations')
def observations_redirect():
    return redirect(url_for('curator.leanings'), 301)
```

**3b. Redirect `/priorities` → `/daily`**
Same pattern:
```python
@curator.route('/priorities')
def priorities_redirect():
    return redirect(url_for('curator.daily'), 301)
```

**3c. Remove Observations from the nav tab bar** (if it's still surfacing as a tab — check the nav template). The standalone route stays alive (the redirect above handles old bookmarks), but there should be no nav tab pointing to it.

**3d. Do NOT delete the `/observations` or `/priorities` routes or templates yet.** Those are Phase 6 cleanup. Just the redirect and nav removal for now.

**Test:** `curl -I http://localhost:8766/observations` should return 301 with `Location: /leanings`. `curl -I http://localhost:8766/priorities` should return 301 with `Location: /daily`.

---

## Definition of done

- [ ] Leanings page renders without a left sidebar in browser.
- [ ] Leanings page shows AI Observations section below a labeled divider.
- [ ] Date navigation in the Observations section works (prev/today arrows).
- [ ] Weekly Connections is a collapsible and toggles correctly.
- [ ] `/observations` redirects (301) to `/leanings`.
- [ ] `/priorities` redirects (301) to `/daily`.
- [ ] No nav tab points to `/observations` anymore.
- [ ] `python3 tools/health_check.py` passes (all three services alive).
- [ ] Re-run `python3 tools/capture_screenshots.py` — Leanings screenshot should show no sidebar + Observations section; Observations page should not appear as a standalone baseline page.
- [ ] **Robert visual sign-off before push to main.**

If a step cannot be tested, say so explicitly — do not commit and claim done.

---

## Guardrails

- `main` is production — stays stable. Work on `feat/curator-ia`.
- JSON is source of truth — no DB dependency for any of this.
- One change at a time: Task 1 → test → Task 2 → test → Task 3 → test → screenshots → Robert.
- Daily briefing pipeline and Telegram delivery must be unaffected (regression: confirm cron still writes `curator_briefing.html` at `Path(__file__).parent`).

---

## Deferred (not Phase 3)

- Phase 4: Research → split to Scans & Dives (artifacts) + Desk rename / content (Topics/Sources/Groups/threads). Design session done; build brief to come.
- Phase 5: Archive page — wire unified browse sections from existing JSON.
- Phase 6: Hard cleanup — delete `/observations` template, delete `/priorities` template, remove redirect stubs.
- Desk: rename "Research Intelligence" heading → "The Desk" (Phase 4).
- Reading Room subtitle ("Saved articles, flagged to act on") — minor polish, any phase.

---

## Suggested commit sequence

```
# after Task 1
git add templates/...
git commit -m "phase3: remove vestigial sidebar from Leanings page"

# after Task 2
git add templates/... routes/...
git commit -m "phase3: fold AI Observations section into Leanings"

# after Task 3
git add routes/...
git commit -m "phase3: redirect /observations → /leanings, /priorities → /daily"

# after screenshots re-run and Robert sign-off
git add docs/screenshots/...
git commit -m "phase3: update screenshot baseline (Leanings merged, Observations retired)"
```

---

*Curator IA Phase 3 · feat/curator-ia · ready to build · 2026-06-06 21:45*
