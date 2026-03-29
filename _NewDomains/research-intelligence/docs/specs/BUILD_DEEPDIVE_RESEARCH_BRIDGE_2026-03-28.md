# BUILD_DEEPDIVE_RESEARCH_BRIDGE_2026-03-28.md
*Date: March 28, 2026*
*Author: claude.ai design session*
*Status: Ready for Claude Code — Priority build for use case validation*

---

## The Use Case This Unlocks

```
Daily briefing
  → save article                        ✅ working
  → deep dive with your interest        ✅ working
  → comment on the deep dive            ❌ missing (Fix 1)
  → start a research thread from it     ❌ missing (Fix 3)
  → research runs for 4-5 days         ⚠️ manual for now
  → observe synthesis                   ✅ working
  → comment and close                   ✅ working
```

Fixes 1 and 2 are small. Fix 3 is the meaningful build.

---

## Fix 1 — Floating Note Button on Deep Dive Pages

**Problem:** No way to comment on a deep dive. Can't correct
your interest statement or react to the analysis.

**File:** Deep dive pages are generated HTML under
`interests/2026/deep-dives/` — BUT they're now served
via Flask route `/research/deep-dive/<id>` which renders
from a template or regenerated HTML.

**Confirm first:**
```bash
# Which template/file serves the deep dive detail page?
grep -n "deep.dive\|deepdive" ~/Projects/personal-ai-agents/curator_server.py | head -20
```

**Add to the deep dive page template:**
```html
<body data-domain="curator" data-page="deep-dive" 
      data-topic="" data-ref-id="{{ dive_id }}">
```

**Add before closing `</body>`:**
```html
<link rel="stylesheet" href="/research/static/css/annotations.css">
<script src="/research/static/js/annotations.js"></script>
<script>
  document.addEventListener('DOMContentLoaded', () => {
    AnnotationSystem.initFloating();
    AnnotationSystem.initSelectionPopup();
  });
</script>
```

**Test:**
```
Open any deep dive page
✓ Floating 💬 button visible bottom-right
✓ Click → compact overlay
✓ Type note → saves to data/annotations/curator/YYYY-MM-DD.json
✓ Context captures: domain=curator, page=deep-dive, ref-id=hash
✓ Text selection on analysis → popup → note saves with selected text
```

---

## Fix 2 — Deep Dive Nav: Move Priorities to ···

**Problem:** Deep dive pages still show Priorities as a direct
nav tab instead of tucked in `···`.

**File:** The deep dive template (confirmed by grep above)

**Find nav and replace** with standard Curator nav pattern:
```html
<nav class="header-nav">
  <a href="/" class="nav-link">Daily</a>
  <a href="/curator_library.html" class="nav-link">Library</a>
  <a href="/interests/2026/deep-dives/index.html" class="nav-link active">Deep Dives</a>
  <a href="/curator_intelligence.html" class="nav-link">Observations</a>
  <div class="nav-more-wrapper">
    <button class="nav-more-btn">···</button>
    <div class="nav-more-dropdown">
      <a href="/curator_priorities.html">Priorities</a>
    </div>
  </div>
  <a href="/curator_priorities.html" class="nav-link nav-icon">🍎</a>
</nav>
```

Also add nav.css and nav.js to the deep dive template head:
```html
<link rel="stylesheet" href="/research/static/css/nav.css">
<script src="/research/static/js/nav.js"></script>
```

**Test:**
```
Open any deep dive page
✓ Nav reads: Daily · Library · Deep Dives · Observations · ··· · 🍎
✓ Priorities not visible in main nav
✓ Click ··· → Priorities in dropdown
```

---

## Fix 3 — Start Research Thread from Deep Dive

**This is the meaningful build. The bridge between Curator and Research.**

### What it looks like

On every deep dive page, after the bibliography section, add:

```
┌─────────────────────────────────────────────────────┐
│  🔬 Start Research Thread                            │
│                                                      │
│  Topic name: [taiwan-defense          ]              │
│                                                      │
│  Motivation (pre-filled from your interest):         │
│  "I suspect that Taiwan wants to fight China         │
│   less than other countries want it to..."           │
│  [editable textarea]                                 │
│                                                      │
│  Starting queries (from bibliography):               │
│  ☑ Taiwan porcupine defense drone warfare            │
│  ☑ Taiwan independence credible deterrence           │
│  ☑ China Taiwan historical trading relationship      │
│  ☐ US Taiwan Relations Act defense obligations       │
│                                                      │
│  Duration: [3 days ▾]  (3 / 5 / 7 / 14)            │
│                                                      │
│  [Launch Research Thread →]                          │
└─────────────────────────────────────────────────────┘
```

### What it does when you click Launch

1. Creates a new topic in `research_config.json`:
```json
{
  "topic": "taiwan-defense",
  "motivation": "I suspect Taiwan wants to fight China less...",
  "source": "deep_dive",
  "deep_dive_id": "43c03",
  "deep_dive_title": "Hellscape Taiwan: A Porcupine Defense in the Drone Age",
  "created": "2026-03-28",
  "duration_days": 5,
  "expires": "2026-04-02",
  "queries": [
    "Taiwan porcupine defense drone warfare",
    "Taiwan independence credible deterrence",
    "China Taiwan historical trading relationship"
  ]
}
```

2. Writes a thread record to `data/threads/taiwan-defense.json`:
```json
{
  "topic": "taiwan-defense",
  "motivation": "...",
  "source_deep_dive": "43c03",
  "created": "2026-03-28",
  "status": "active"
}
```

3. Redirects to `/research/sessions?topic=taiwan-defense`
   with a success toast: "Research thread started — 
   first session will run tonight"

### Backend — new route in research_routes.py

```python
POST /api/research/spawn-thread
{
  "topic": "taiwan-defense",
  "motivation": "...",
  "queries": [...],
  "deep_dive_id": "43c03",
  "deep_dive_title": "...",
  "duration_days": 5
}
```

**Validation:**
- Topic name: slug only (lowercase, hyphens, no spaces)
- At least 1 query required
- Topic must not already exist in research_config.json

**Response:**
```json
{
  "status": "created",
  "topic": "taiwan-defense",
  "redirect": "/research/sessions?topic=taiwan-defense"
}
```

### Bibliography parsing for pre-population

The deep dive bibliography is already being parsed by the
bibliography parser from the Deep Dive → Research Inbox work.
Reuse that parser to populate the query checkboxes.

Each bibliography item becomes a suggested query:
- Title → truncated to ~60 chars as query text
- URL present → show as link next to checkbox
- Pre-checked by default, user can uncheck

### Pre-population of motivation field

The "Your Interest" section from the deep dive is already
captured in the deep dive HTML/data. Pre-fill the motivation
textarea with that text so the user can edit rather than
retype. This is where Robert would have corrected
"more" → "less" in his Taiwan interest statement.

---

## Build Order

1. Fix 2 (nav) — CSS only, 5 min
2. Fix 1 (floating note) — additive, 15 min  
3. Fix 3 (spawn thread) — the main build, 60-90 min

**Do not start Fix 3 until Fixes 1 and 2 are committed and tested.**

---

## Pre-Flight

```bash
git status   # must be clean
git push     # confirm rollback point

# Confirm deep dive template location
grep -n "deep.dive\|deepdive" ~/Projects/personal-ai-agents/curator_server.py | head -20

# Confirm research_config.json structure
cat ~/Projects/personal-ai-agents/research_config.json | python3 -m json.tool | head -30
```

---

## Scope Boundary

**DO NOT:**
- Touch curator_rss_v2.py
- Touch existing research.py session logic
- Auto-schedule runs — duration_days is metadata only for now,
  user still runs sessions manually
- Touch Telegram path
- Change any existing routes

The spawn creates the topic and motivation. Running the sessions
remains manual for this sprint. Scheduling is v1.2 scope.

---

## What This Proves

Once built, the full use case works end to end:

```
1. Read Daily briefing
2. Save article that interests you
3. Deep Dive — write your interest/hypothesis
4. Comment on the analysis with reactions
5. Start Research Thread — pre-populated from bibliography
   and your interest statement
6. Run research sessions over 4-5 days (manually for now)
7. Observe synthesis across sessions
8. Comment on synthesis, close thread
```

This is the mini-moi.ai core loop. Everything else is
infrastructure supporting this flow.

---

## Note for OpenClaw

Read `research_config.json` structure before speccing the
spawn-thread route. The new topic must follow exactly the
same schema as existing topics or research.py will fail.

Also confirm: does `threads.py` need to be called when
spawning, or is writing to `research_config.json` sufficient
to activate the topic for the next session run?
