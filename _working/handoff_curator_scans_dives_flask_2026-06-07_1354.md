# Handoff — Scans & Dives: Convert to Flask Route
*mini-moi · personal-ai-agents · Curator domain*

- **Authored:** 2026-06-07 13:54 CDT (18:54 UTC) — Claude.ai
- **Status:** READY TO BUILD — approved by Robert
- **Branch:** `feat/curator-ia` (same as Phase 1–6)
- **Prerequisite:** Phase 6 complete. All other Curator IA pages (Landing, Reading Room, Leanings, Archive, Desk) are Flask-rendered. Scans & Dives is the last page still served from a static file.
- **Scope:** Port Scans & Dives to a Flask route. Matches all other IA pages in architecture and nav.

---

## Background

Scans & Dives is currently served from a static file at:
```
minimoi.ai/app/curator/interests/2026/scans/index.html
```
This file has the old pre-IA nav hardcoded into it. All other Curator IA pages are Flask-rendered. Robert has confirmed: all pages should use the same method.

The two other old static file URLs (`curator_priorities.html`, `curator_intelligence.html`) are already 404 after Phase 6 cleanup — that is acceptable, those pages are retired.

---

## Task 1 — Create the Flask route

Add a route for Scans & Dives in the Curator Flask blueprint, following the **exact same pattern** as Daily, Reading Room, Leanings, Archive, and Desk.

- Use the same URL prefix/convention as the other Curator Flask routes (check the existing routes and match the pattern exactly — e.g., if others are at `/app/curator/reading-room`, use `/app/curator/scans-dives`).
- The route reads dives and scans from the same JSON source(s) the existing static generation script uses. Find those JSON paths by inspecting either the static file generator or the Archive page's Dives/Scans sections (which already read this data correctly).
- Pass dives list and scans list to the template as context variables.

---

## Task 2 — Create the Jinja2 template

Create `curator_scans_dives.html` (or follow the naming convention of the other Curator templates) extending the **same base template** as the other IA pages.

Template structure — match the existing static file content exactly:

```
[extends same base as curator_reading_room.html / curator_leanings.html etc.]

Page heading: "Scans & Dives"   ← Georgia serif, same size as other page headings
Subtitle: "N dives · N scans"  ← live count from data

DIVES   N                                    show all →
──────────────────────────────────────────────────────
[THREAD]  Hellscape Taiwan Porcupine   2 sessions · 4 sources · $0.18   Mar 29, 2026
          Your original hypothesis contains several testable claims...
[THREAD]  ...
+ N more threads

SCANS   N                                    show all →
──────────────────────────────────────────────────────
Jun 7, 2026   Al Jazeera   How have countries around the world...
Jun 3, 2026   Al Jazeera   Does Israel have nukes?...
...
+ N more scans
```

This matches the current static page layout. Do not redesign — just port it to Jinja2.

**Dive row fields:** THREAD badge, title (formatted — not slug), hypothesis snippet, sessions · sources · cost · date.
**Scan row fields:** date, source name, title.

Use the same formatted title fix applied to the Archive Dives section (Task 1 of Phase 6) — render the display title, not the slug.

---

## Task 3 — Update nav links

Find every Curator template that contains a nav link to Scans & Dives and update the `href` to point to the new Flask route URL.

The old link almost certainly points to something like:
```
/app/curator/interests/2026/scans/index.html
```
or a relative equivalent. Replace with the new Flask route URL (matching the pattern of the other nav links in those same templates).

**Templates to check:** the shared nav include or base template (wherever the top nav bar is defined). It will be in one place if the nav is a shared include, or in each template if it's duplicated. Update all instances.

---

## Task 4 — Update screenshot capture tool

Update `tools/capture_screenshots.py` to use the new Flask route URL for Scans & Dives instead of the old static file path.

---

## Task 5 — Leave the old static file in place

Do **not** delete `interests/2026/scans/index.html`. It is a generated output file under the interests pipeline, not a Jinja2 template. It is no longer linked from the nav, so it causes no harm. It can be cleaned up later as part of the interests/research pipeline work.

---

## Definition of done

- [ ] Scans & Dives Flask route exists and returns a 200 response.
- [ ] The page renders with the correct new IA nav (DAILY · READING ROOM · SCANS & DIVES · LEANINGS · ARCHIVE · DESK).
- [ ] The page extends the same base template as the other Curator IA pages.
- [ ] Dive titles show as formatted titles (not slugs).
- [ ] Dives count and Scans count match the existing static file data.
- [ ] Nav link for "Scans & Dives" in all other Curator pages points to the new Flask route.
- [ ] Clicking "Scans & Dives" in the live site navigates to the new Flask page, not the old static file.
- [ ] Screenshot capture tool uses the new Flask route URL.
- [ ] `python3 tools/capture_screenshots.py` produces 7 clean pages, all with the new nav.
- [ ] `python3 tools/health_check.py` passes.
- [ ] **Robert visual sign-off on the 7-page baseline before merge.**
- [ ] **`feat/curator-ia` merged to `main`.** Curator IA migration complete.

If any step cannot be tested, say so explicitly — do not commit and claim done.

---

## Suggested commit sequence

```
git add routes/... templates/...
git commit -m "feat: convert Scans & Dives to Flask route (matches all other IA pages)"

git add templates/...
git commit -m "fix: update Scans & Dives nav links to Flask route in all templates"

git add tools/capture_screenshots.py
git commit -m "fix: update screenshot capture URL for Scans & Dives"

git add docs/screenshots/...
git commit -m "phase6-final: 7-page baseline, all pages Flask-rendered"
```

Then merge:
```
git checkout main
git merge feat/curator-ia
git push origin main
git branch -d feat/curator-ia
```

---

*Curator IA — Scans & Dives Flask conversion · feat/curator-ia → main · ready to build · 2026-06-07 13:54 CDT*
