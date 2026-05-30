# Curator — Build Handoff for OpenClaw
*Date: 2026-05-30. Author: Claude Code.*
*Read by: OpenClaw before creating any Curator issues.*

---

## Branch Convention — Curator Work

**Target branch: `main`** (or `feat/curator-*` for large feature work).

Never commit Curator code or docs to `feat/german-html-interface` or any German branch. The Curator is the original project on `main`; German is a long-running feature branch. They do not cross.

---

## Build Queue — Current State

### Track 1: UI Redesign — READY TO ISSUE

**Spec:** `docs/curator-ui-redesign-plan.md`
**Screenshots of current state:** `_working/curator-screenshots-2026-05-29.pdf`
**Status:** Design approved by Robert. No open questions. Ready for a GitHub issue.

**What it does:** Replaces the broken `body { display: flex }` + `overflow-x: hidden` layout (which breaks sticky headers under the portal's injected nav bar) with a portal-first single-subnav architecture. Simultaneously refreshes all 5 pages to match the portal/German visual design system. Backend unchanged.

**Scope of changes:**
| File | Change type |
|---|---|
| `minimoi_portal/proxy.py` | Update Curator CSS offset target |
| `curator_briefing.html` | Full rewrite (Jinja2 template) |
| `curator_library.html` | Structural swap (static HTML) |
| `curator_priorities.html` | Structural swap (static HTML) |
| `curator_intelligence.html` | Structural swap (static HTML) |
| `curator_index.html` | Structural swap (static HTML) |

**Issue to create:** "Curator UI redesign — portal-first layout + visual refresh (all 5 pages)"
- Label: `enhancement`
- Link to: `docs/curator-ui-redesign-plan.md`
- Claude Code will implement; Robert reviews before merge

---

### Track 2: Research Area Concept — PENDING ROBERT'S DECISIONS

**Concept doc:** `docs/curator-research-concept-v2.md`
**Position paper (analysis + questions):** `_working/curator-research-concept-v2-position.md`
**Status:** Position paper written. Robert is taking it to Claude.ai and Grok for review. 7 decisions pending before any build spec can be written.

**Do not create issues for this track yet.** Wait for Robert to answer the 7 questions in the position paper. Once answered, OpenClaw writes the build spec and issues.

**The 7 open questions (for reference — do not resolve these, they are Robert's calls):**
1. Naming: Leaning + Position, or Leaning + Hold?
2. Pull vs Priority: one decision or two when activating a topic?
3. Merge UI: should "Investigate this" modal offer both Session and Priority, or keep Priority as Curator-only?
4. Boundary: should manually-added Sources go through territory check, or fully Robert's judgment?
5. Teammate read trigger: on demand / on schedule / on significant new evidence?
6. Leaning evidence: unified view across Topics (system filters) or manually attached Sources?
7. Brief / Deep Study naming: do those names work?

**What's already built in the backend** (OpenClaw should know before spec-writing):
- Threads (= Topics) — full lifecycle, JSON records, session pipeline working
- Sessions — 8-step pipeline (preflight → search → triage → cite → translate → synthesize → findings → log). Ollama/Haiku/Sonnet. Cost-tracked.
- Observations — Sonnet synthesis of saved articles ($0.01–0.015/call)
- Deeper dives — multi-section thread analyses (these are "Deep Studies")
- Article signals — save/drop/redirect with notes
- Budget enforcement — daily $3 / weekly $10 / total $20 hard stops in `run.py`
- Reading room — `data/reading_room/{topic}.json` per topic (prototype for Sources)
- Query candidate pipeline — working but UI-only stub

**What isn't built yet:** Leaning object, first-class Sources (cross-topic), One Flow UI ("Investigate this" modal).

---

## Key Curator Files (for issue writing context)

```
curator_server.py          ← Flask server, port 8766, all API routes
curator_briefing.html      ← Jinja2 template, daily briefing page
curator_library.html       ← Static HTML, article library
curator_priorities.html    ← Static HTML, curator priorities / focus
curator_intelligence.html  ← Static HTML, observations
curator_index.html         ← Static HTML, archive index
minimoi_portal/proxy.py    ← Portal reverse proxy, injects nav bar
_NewDomains/research-intelligence/  ← Research backend (threads, sessions, observations)
```

---

## What NOT to Issue Without Robert's Explicit Approval

- Any changes to `curator_server.py` scoring or pipeline logic
- Any changes to `data/` files or `curator_signals.json`
- Research area concept builds (Track 2 — pending decisions)
- Anything touching `_NewDomains/research-intelligence/` backend

---

## Context: How We Got Here

The portal (`minimoi.ai`) was built in May 2026 and wraps Curator via reverse proxy. The portal injects a 38px fixed nav bar at the top of every page. The Curator's existing layout was never designed for this — it uses its own domain-rail sidebar and sticky header patterns that conflict with the injected nav. Two routing gaps were patched in May 2026 (`/interests/` passthrough and top-level static file catch-all) but the core layout problem remains. The redesign in Track 1 is the permanent fix.

The Research area concept (Track 2) is the longer-term evolution: exposing the backend intelligence pipeline (which already runs) through a coherent UI with a new object model (Topics, Sources, Leanings). The backend is largely built; the UI and data model extensions are not.
