# Bug Handoff — 2026-06-21

Found during EC2 (A4) validation. All three are pre-existing on both Mac and EC2 — not AWS regressions. Please file as GitHub issues.

---

## Bug 1: Curator Archive — articles not visible, not clickable

**URL:** `/app/curator/archive`  
**Symptom:** Page loads but shows only the title. No articles are listed, nothing is clickable.  
**Affected:** Both Mac (minimoi.ai) and EC2 (app.minimoi.ai)  
**Was working before** — this is a regression, not a missing feature.

**Where to look:**
- Route: `curator_server.py` — archive route
- Template: `templates/curator_archive.html`
- Check what data the route passes to the template and whether the template iterates over articles correctly
- Check browser console for JS errors

---

## Bug 2: German Admin — Bearbeiten and Neue Persona buttons do nothing

**URL:** `/app/german/admin`  
**Symptom:** Clicking "+ Neue Persona" or "Bearbeiten" on any persona produces no response — no modal, no navigation, no error. "Löschen" (delete) works correctly.  
**Affected:** Both Mac and EC2  

**Where to look:**
- German domain admin template (likely `domains/german/`)
- JS event listeners on the create/edit buttons
- Browser console shows errors when buttons are clicked

---

## Bug 3: Guild spec viewer — `_working/` file paths return Not Found

**URL:** `/guild/build/spec/_working/<filename>.md`  
**Symptom:** Clicking a spec link whose source file is in `_working/` returns "Not found". Links to `docs/specs/` work correctly.  
**Affected:** Both Mac and EC2  
**Example broken URL:** `app.minimoi.ai/guild/build/spec/_working/NEAR_TERM_PLAN.md`

**Where to look:**
- Spec viewer route in `minimoi_portal/app.py` or guild routes file
- Route likely resolves paths relative to `docs/` only — needs to resolve from repo root
- Add path safety check to ensure only files within repo root are served (no path traversal)
