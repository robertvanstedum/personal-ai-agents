# Handoff — Curator IA Redesign, Phase 2 complete
*mini-moi · 2026-06-06 · Claude Code → OpenClaw*

---

## What was built this session

### Phase 1 — Landing page (committed prior session)
`feat/curator-ia` branch, commit `f329c9a`

- New `templates/curator_landing.html` — masthead + four-drawer card catalog
- Routes `/` and `/landing` serve the landing; `/briefing` is now the daily page
- Fixed evergreen card copy per build plan §6

### Phase 2 — Six-tab nav scaffold
`feat/curator-ia` branch, commits `32d7387` → `44d820d`

**New IA nav across every Curator page:**
```
Daily · Reading Room · Scans & Dives · Leanings · Archive · Desk
```
Retired: Library, Observations, Research, 🎯 Focus

**Pages updated:** curator_briefing.html (template), curator_intelligence.html,
curator_library.html, curator_priorities.html, curator_server.py (archive
placeholder), interests/2026/scans/index.html, research_routes.py (scan detail),
_NewDomains/research-intelligence/web/leanings.html,
_NewDomains/research-intelligence/web/dashboard.html

**New routes in curator_server.py:**
- `/archive` — on-brand placeholder ("Coming — Phase 5")
- `/reading-room` — alias for /curator_library.html
- `/observations` — alias for /curator_intelligence.html (backward compat)

**Subnav CSS standardized** across all pages:
- 44px height, DM Mono 11px, letter-spacing 0.08em, position sticky, blur(8px)
- leanings.html and dashboard.html restructured (body block + .page-layout wrapper)
  to allow full-width subnav above the domain-rail sidebar

**Screenshot baseline captured** (9 pages, labeled with name + timestamp):
- PNGs: `docs/screenshots/curator/current/`
- PDF: `_working/curator-redesign/baseline_2026-06-06.pdf`

---

## Dependency note — screenshot tooling

`tools/capture_screenshots.py` requires two packages not in the base stack:

```bash
pip3 install playwright img2pdf
python3 -m playwright install chromium
```

These are now documented in `requirements.txt` (bottom section) and in the
docstring of the capture script. Must be installed once per machine. The
launchd services do not need them — they are dev/doc tooling only.

---

## What's next (Robert's call)

**Design session first** — Robert flagged that subnav interaction design
and per-page layout need a design session before any Phase 3+ build work.

The build plan phases still queued:
3. Rename + fold (Library → Reading Room, Observations strip into Leanings)
4. Split Research (artifacts → Scans & Dives; config → Desk)
5. Archive page (unified browse from existing JSON)
6. Cleanup (remove dead routes/redirects)

Review PDF is in `_working/curator-redesign/baseline_2026-06-06.pdf`.

---

## Branch state
- Branch: `feat/curator-ia`
- Not yet merged to main — Robert reviews before merge
- All 10 Curator routes return 200
