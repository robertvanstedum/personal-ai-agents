# Handoff — Curator IA Phase 6 (Final Cleanup)
*mini-moi · personal-ai-agents · Curator domain*

- **Authored:** 2026-06-07 07:11 CDT (12:11 UTC) — Claude.ai
- **Status:** READY TO BUILD — approved by Robert
- **Branch:** `feat/curator-ia` (same as Phase 1–5)
- **Prerequisite:** Phase 4b/5 complete and signed off. Baseline at `docs/screenshots/curator/current/` (2026-06-07 08:56, 9 pages).
- **Scope:** Two small Archive refinements + hard cleanup of dead routes and templates. After this phase passes sign-off, `feat/curator-ia` is ready to merge to `main`.

---

## Task 1 — Archive: Dives formatted title

**Problem:** The Archive Dives section renders thread slugs (`strait-of-hormuz`, `hellscape-taiwan-porcupine`) instead of the formatted display title ("Strait of Hormuz", "Hellscape Taiwan Porcupine") that the Scans & Dives page uses.

**Fix:** In the Archive template's Dives section, render the thread's `title` or `name` field from the JSON object rather than the slug/key. Check the thread JSON structure — if a display title field exists, use it. If the JSON only stores slugs, apply a simple slug-to-title conversion as a fallback:

```python
def slug_to_title(slug):
    return slug.replace('-', ' ').title()
```

Consistent with how Scans & Dives displays "Hellscape Taiwan Porcupine".

**Test:** Archive Dives section shows "Strait of Hormuz", "Hellscape Taiwan Porcupine" etc. — not raw slugs.

---

## Task 2 — Archive: align content column width

**Problem:** The Archive content column appears narrower than the other single-column pages (Desk, Leanings, Scans & Dives).

**Fix:** Check the `max-width` value on the Archive page's content container and align it to match the value used by the other single-column pages. Likely one CSS property or template variable to change.

**Test:** Open Archive and Desk side by side. Content columns should be the same width.

---

## Task 3 — Remove dead pages from screenshot capture list

The screenshot tool still captures `/observations` (page 8) and `/priorities` (page 9) in the baseline. Remove both from `tools/capture_screenshots.py`.

After this task the baseline should capture exactly **7 pages**: Landing, Daily, Reading Room, Scans & Dives, Leanings, Archive, Desk.

**Test:** Running `python3 tools/capture_screenshots.py` produces exactly 7 screenshots + 1 PDF with 7 pages.

---

## Task 4 — Delete old template files

The following template files are no longer rendered — their routes redirect rather than call `render_template`. Safe to delete:

- `templates/curator_observations.html` (route now 301-redirects to `/leanings`)
- `templates/curator_priorities.html` (route now 301-redirects to `/daily`)

Check whether a legacy Research template exists (used before the Desk was wired) — if so, delete it too.

**Test:** Confirm templates are gone. Routes still redirect correctly (the 301 redirect route is separate from the template — deleting the template doesn't affect the redirect). `curl -I http://localhost:8766/observations` still returns `301`.

---

## Task 5 — Remove redirect route stubs

The redirect routes for `/observations`, `/priorities`, and `/research` can now be removed entirely. 301 responses are cached permanently by browsers; anyone with an old bookmark will already have the cached redirect. The nav no longer links to any of these.

Find and remove the three redirect stubs from the Curator blueprint:

```python
# Remove these:
@curator.route('/observations')
def observations_redirect(): ...

@curator.route('/priorities')
def priorities_redirect(): ...

@curator.route('/research')
def research_redirect(): ...
```

**Test:** `curl -I http://localhost:8766/observations` now returns `404`. `curl -I http://localhost:8766/priorities` returns `404`. `curl -I http://localhost:8766/research` returns `404`. No other pages are affected. Daily briefing pipeline and Telegram delivery unaffected.

---

## Definition of done

- [ ] Archive Dives section shows formatted titles, not slugs.
- [ ] Archive content column width matches Desk and Scans & Dives.
- [ ] `/observations`, `/priorities`, `/research` removed from screenshot capture list.
- [ ] `python3 tools/capture_screenshots.py` produces exactly 7 screenshots + a 7-page PDF.
- [ ] Old template files deleted (`curator_observations.html`, `curator_priorities.html`, legacy Research template if present).
- [ ] Redirect route stubs removed; the three old routes return 404.
- [ ] `python3 tools/health_check.py` passes (all three services alive).
- [ ] Daily briefing cron and Telegram pipeline regression-checked (unaffected).
- [ ] **Robert visual sign-off on the 7-page baseline before push to main.**
- [ ] **`feat/curator-ia` merged to `main` after sign-off.** This closes the Curator IA migration.

If any step cannot be tested, say so explicitly — do not commit and claim done.

---

## Guardrails

- `main` is production — stays stable until the merge at the end of this phase.
- Do the Archive refinements first (Tasks 1–2), test, then the cleanup (Tasks 3–5).
- Verify each redirect returns 404 after removal — do not assume.

---

## After this phase: merge

Once Robert signs off on the 7-page baseline:

```
git checkout main
git merge feat/curator-ia
git push origin main
git branch -d feat/curator-ia
```

The Curator IA migration is complete. All six pages live on `main`, `feat/curator-ia` retired.

---

## Suggested commit sequence

```
git add templates/... routes/... static/...
git commit -m "phase6: Archive dives formatted titles, column width fix"

git add tools/capture_screenshots.py
git commit -m "phase6: remove /observations and /priorities from screenshot capture"

git add templates/... routes/...
git commit -m "phase6: delete dead templates and redirect stubs"

git add docs/screenshots/...
git commit -m "phase6: final 7-page baseline (Curator IA complete)"
```

---

*Curator IA Phase 6 · feat/curator-ia → main · ready to build · 2026-06-07 07:11 CDT*
