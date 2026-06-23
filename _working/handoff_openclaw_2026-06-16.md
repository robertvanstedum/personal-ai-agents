# Handoff — OpenClaw
**Date:** 2026-06-16 evening  
**From:** Claude Code session  
**To:** OpenClaw (planning, memory, documentation layer)

---

## 1. Immediate cleanup needed — cos_agenda.json noise

`data/guild/cos_agenda.json` has **~50 duplicate** `[memory_cap_approaching] devagent_memory.md` entries from the devagent watcher firing on a tight loop yesterday and today. They are all pending and identical except for timestamp. They clutter the CoS agenda view and inflate the file.

**Action:** Remove all but the most recent `[memory_cap_approaching]` entry for `devagent_memory.md` from `cos_agenda.json`. Keep entries from other domains (german, curator, mini_moi, design_dev non-duplicate) unchanged.

Also: investigate why the devagent watcher is firing so frequently (every ~1–7 minutes instead of at a reasonable cadence). The watcher should deduplicate or rate-limit before writing to the agenda.

---

## 2. Session summary — what Claude Code shipped today (2026-06-16)

Full mobile audit of `minimoi.ai` and `app.minimoi.ai` at 390px. Robert tested on iPhone. 11 fixes shipped:

**Live portal:**
- German pages blank on mobile (critical) — `proxy.py` nav override scoped to desktop only
- Guild Briefing 2-column → single column on mobile
- German Archiv horizontal overflow — source/mode columns hidden on mobile
- German Lesen category cards — `display:flex nowrap` → `display:grid 2×2` on mobile
- German Admin buttons — `flex-wrap:wrap` on mobile
- Gespräche: `selectPersona()` always resets `detail.scrollTop = 0` (buttons were scrolled off-screen after sessions)
- Gespräche: `scrollIntoView` on persona tap for mobile viewports

**Preview static pages (all 17):**
- Banner restructured for mobile (3-span flex → 1-span + button with `flex-wrap`)
- German nav override scoped to desktop in `portal-offset-css` inline style
- Guild Queue 5-column kanban → single column on mobile
- Guild Briefing same as live fix

**Files changed:** `minimoi_portal/proxy.py`, `tools/capture_snapshot.py`, `domains/german/static/german.css`, `static/public/preview/assets/german.css`, `domains/german/templates/german_gesprache.html`, `minimoi_portal/templates/guild/guild_landing.html`, all 17 `static/public/preview/**/*.html`

All changes are on `main`. No commits yet — Robert will review and commit.

---

## 3. What's in the build queue

**Item #50** — `spec_ready` — "Gespräche — Transcript Paste Panel"  
Spec: `_working/spec_gesprache_transcript_paste_panel_2026-06-16.md`  
Robert plans to build this tonight (Claude Code session).

---

## 4. Spec and enhancement doc for your records

- `_working/spec_gesprache_transcript_paste_panel_2026-06-16.md` — final spec, authored Claude.ai, queued for build
- `_working/spec_mobile_ui_enhancements_2026-06-16.md` — full write-up of what was fixed today + 10 enhancement ideas (E1–E10) for future planning

The 10 enhancement ideas (E1–E10) in the mobile UI doc are candidates for future GitHub issues. OpenClaw should review and file issues for any that warrant it, coordinating with Robert first.

---

## 5. No action needed on

- The Telegram session transcript (session `2026-06-16_003`, inbox `raw_2026-06-16_14-45.txt`) — Maria café session, 18 turns, received and stored correctly.
- GitHub issue #41 (Lesen writing drill) and #42 (DB password hardening) — still open, no change.
