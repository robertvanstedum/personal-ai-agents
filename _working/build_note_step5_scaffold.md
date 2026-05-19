# Build Note: Step 5 — Flask Scaffold with Six-Tab Skeleton
**Date:** 2026-05-19
**Branch:** feat/german-html-interface
**Spec:** docs/GERMAN_HTML_BUILD_PLAN_v1.0.md — Step 5

---

## What Step 5 is

Empty shell for the German HTML interface. Flask app, routing, navigation rail,
typography, colour palette. No tab content yet — all tabs show step-number placeholders.

---

## Files created

| File | Purpose |
|---|---|
| `html_server.py` | Flask app, six routes + redirect, entry point on port 8767 |
| `static/german.css` | CSS custom properties, reset, tab rail, layout, mobile nav |
| `templates/german_base.html` | Jinja2 base: font imports, nav rail with active state, main block |
| `templates/german_lesen.html` | Lesen placeholder (Step 6) |
| `templates/german_schreiben.html` | Schreiben placeholder (Step 7) |
| `templates/german_ueben.html` | Üben placeholder (Step 10) |
| `templates/german_bibliothek.html` | Bibliothek placeholder (Step 9) |
| `templates/german_admin.html` | Admin placeholder (Step 11) |
| `templates/german_archiv.html` | Archiv placeholder (v1.1 — tab is disabled) |

`static/` directory created (new). `templates/` already existed (curator_server uses it);
all new templates prefixed `german_` to avoid conflicts.

---

## Design decisions

**Port 8767:** 8765 is curator_server in launch.json; 8766 is curator_server's actual
running process. 8767 is the first free port in the sequence.

**Accent: terracotta (#c4623a).** Warm, café-culture Vienna. Pairs naturally with
charcoal (#1e1c1a) and warm off-white (#e8e0d5). To swap to deep teal: change
`--accent`, `--accent-dim`, `--accent-bg` in german.css (three lines).

**Server-side routing (one Flask route per tab):** Each tab is a separate URL
(`/lesen`, `/schreiben`, etc.). `active` variable passed to every template so nav
can highlight current tab with Jinja2 conditional. Simpler to scaffold and debug
than client-side show/hide; can be enhanced with AJAX later per tab.

**Archiv disabled:** Spec marks Archiv as v1.1. Tab renders in nav rail as greyed-out
with `pointer-events: none` — visible but not clickable.

**Mobile nav:** Tab rail collapses to bottom fixed bar below 768px, with top-border
active indicator instead of bottom-border. Matches spec requirement.

**Typography:** Google Fonts CDN — Playfair Display (headings), DM Sans (body),
IBM Plex Mono (corrections/transcripts). All three loaded in base template.

---

## Verified in browser

- `/` redirects to `/lesen` ✓
- All six tabs render in nav rail ✓
- Active tab shows terracotta underline ✓
- Playfair Display headings render ✓
- Charcoal background (#1e1c1a), warm off-white text ✓
- No console errors ✓
- Mobile layout: bottom nav bar with Bibliothek active ✓ (screenshot in session)
