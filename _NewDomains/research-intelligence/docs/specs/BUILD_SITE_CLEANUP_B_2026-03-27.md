# BUILD_SITE_CLEANUP_B_2026-03-27.md
*Date: March 27, 2026*
*Author: claude.ai design session*
*Status: Ready for Claude Code*
*Depends on: site-cleanup-A committed and pushed*

---

## ⚠️ SCOPE BOUNDARY — READ FIRST

**This build covers six specific fixes. Nothing else.**

**DO NOT:**
- Rename any tabs (Journal, Threads, AI Feedback — future session)
- Restructure page layouts beyond what's specified
- Touch annotations.py, research.py, observe.py, curator_rss_v2.py
- Touch Telegram path
- Add new features beyond what's listed

**Telegram:** No impact.

---

## Six Fixes in This Build

| # | What | Risk |
|---|---|---|
| 1 | Curator Daily — floating notes button (still broken) | Low |
| 2 | Warm parchment palette on cold/white pages | Low |
| 3 | Left rail gap — tighten content margin | Low |
| 4 | AI Observations — add note gesture | Low |
| 5 | Utility tabs → `···` dropdown (Dashboard, Priorities, Queries, Save) | Medium |
| 6 | Curator Daily — condensed two-line article list | Medium |

---

## Pre-Flight

```bash
git status   # must be clean
git push     # confirm remote is rollback point

# Baseline check
curl http://localhost:8765/                    → 200
curl http://localhost:8765/research/sessions   → 200
curl http://localhost:8765/research/observe    → 200
curl http://localhost:8765/curator_intelligence.html → 200

# Confirm template file for Curator Daily
grep -n "curator_briefing\|curator_latest" curator_server.py
# Note which template Flask actually renders for / route
```

---

## Fix 1 — Curator Daily: Floating Notes Button

**Problem:** Full ADD NOTE panel still showing on right side of Daily.
Fix 1 from cleanup-A did not land correctly.

**File:** Whichever template the grep above identifies for the `/` route.
Likely `templates/curator_briefing.html`.

**Find current annotation init** — will look like one of:
```html
<div id="ann-comment-area"></div>
...AnnotationSystem.init('ann-comment-area')
or
...AnnotationSystem.initCollapsiblePanel(...)
```

**Replace entire annotation block with:**
```html
<script src="/research/static/js/annotations.js"></script>
<link rel="stylesheet" href="/research/static/css/annotations.css">
<script>
  document.addEventListener('DOMContentLoaded', () => {
    AnnotationSystem.initFloating();
    AnnotationSystem.initSelectionPopup();
  });
</script>
```

**Remove** any `<div id="ann-comment-area">` — floating mode injects
its own elements, no container needed.

**Verify `<body>` tag:**
```html
<body data-domain="curator" data-page="daily">
```

**Test:**
```
Hard refresh http://localhost:8765/
✓ No right panel visible
✓ Article list uses full width  
✓ Small 💬 button bottom-right
✓ Click → compact overlay
✓ Save → toast, overlay closes
✓ Like/dislike/save buttons unaffected
```

---

## Fix 2 — Warm Parchment Palette on Cold Pages

**Problem:** Some pages feel too white/stark compared to the warm
parchment Morning Briefing tone.

**Target palette (already defined in curator pages):**
```css
--bg: #f5f0e8;
--surface: #faf7f2;
--text: #2a2418;
--accent: #8b5e2a;
--border: #ddd6c8;
--text-muted: #6b5f4e;
--text-dim: #9e9080;
```

**Pages to check and fix:**
For each page, open in browser and check:
- Is the background white (#fff) or parchment (#f5f0e8)?
- Are headings using Playfair Display or a default serif/sans?
- Does the warm left border accent appear on the active domain?

**Likely offenders** (check these first):
- `web/dashboard.html`
- `web/save.html`
- `web/candidates.html`
- Any page where `background: white` or `background: #fff` appears

**Fix pattern** — for each cold page, find the CSS variables or
body/html background declaration and replace:
```css
/* Before */
background: #fff;
background-color: white;
background: #ffffff;

/* After */
background: var(--bg, #f5f0e8);
```

And ensure CSS variables are declared at `:root` level if not already:
```css
:root {
  --bg: #f5f0e8;
  --surface: #faf7f2;
  --text: #2a2418;
  --accent: #8b5e2a;
  --border: #ddd6c8;
  --text-muted: #6b5f4e;
  --text-dim: #9e9080;
}
```

**The warm brown border** on the left side of the domain rail active
state should be consistent — check `border-left` on `.rail-item.active`
is using `var(--accent)` not a hardcoded color.

**Test:**
```
Open each page — background should be warm parchment, not white
Headings should use Playfair Display
Left rail active state shows warm brown left border
No text readability regressions
```

---

## Fix 3 — Left Rail Gap: Tighten Content Margin

**Problem:** Gap between left rail (200-220px) and page content feels
like dead space on some pages.

**Fix:** Ensure main content area has correct `margin-left` matching
rail width exactly, with no extra padding creating a dead zone.

**Find in CSS** (likely in a shared stylesheet or per-page):
```css
.main-content,
.content-area,
main {
  margin-left: 200px; /* or whatever rail width is */
}
```

**Check:** Is there extra padding on the content wrapper that adds
to the margin? If so, remove the double-spacing.

Also check: on pages where the left panel (topics sidebar) exists
inside the content area, is the outer margin correct?

**Test:**
```
Sessions page: content starts immediately after rail, no dead zone
Observations page: same
Daily briefing: same
No horizontal scrollbar introduced
```

---

## Fix 4 — AI Observations: Add Note Gesture

**Problem:** AI Observations page (`curator_intelligence.html`) has
inline reaction/save per observation block but no floating note button
for general page reactions.

**File:** `curator_intelligence.html`

**Add to `<body>` tag:**
```html
<body data-domain="curator" data-page="ai-observations">
```

**Add before closing `</body>`:**
```html
<script src="/static/js/annotations.js"></script>
<link rel="stylesheet" href="/static/css/annotations.css">
<script>
  document.addEventListener('DOMContentLoaded', () => {
    AnnotationSystem.initFloating();
    AnnotationSystem.initSelectionPopup();
  });
</script>
```

Note: The existing inline reaction/save per observation block stays
unchanged — the floating button is an addition, not a replacement.
Users can react to a specific observation inline OR add a general
page note via the floating button.

**Test:**
```
Hard refresh http://localhost:8765/curator_intelligence.html
✓ Existing inline reaction/note/save per observation still works
✓ Floating 💬 button visible bottom-right
✓ Click → compact overlay
✓ Save → writes to data/annotations/curator/YYYY-MM-DD.json
✓ No regression on date navigation (← prev · today →)
```

---

## Fix 5 — Utility Tabs → `···` Dropdown

**What moves into `···`:**

| Domain | Moves to `···` | Stays in nav |
|---|---|---|
| Curator | Priorities | Daily · Library · Deep Dives · Observations |
| Research | Dashboard · Queries · Save | Sessions · Observations |

**New nav bars:**
```
Curator:   Daily · Library · Deep Dives · Observations · ···
Research:  Sessions · Observations · ···
```

**The `···` dropdown contains:**
```
Curator ···:
  Priorities
  
Research ···:
  Dashboard
  Queries  
  Save
```

**Implementation:**

Add to shared CSS (annotations.css or a new nav.css):
```css
.nav-more-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-family: 'DM Mono', monospace;
  font-size: 13px;
  color: var(--text-muted, #6b5f4e);
  padding: 4px 8px;
  border-radius: 4px;
  letter-spacing: 0.05em;
  position: relative;
}

.nav-more-btn:hover {
  background: var(--surface, #faf7f2);
  color: var(--text, #2a2418);
}

.nav-more-dropdown {
  display: none;
  position: absolute;
  top: 100%;
  right: 0;
  background: var(--surface, #faf7f2);
  border: 1px solid var(--border, #ddd6c8);
  border-radius: 6px;
  padding: 6px 0;
  min-width: 140px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.1);
  z-index: 400;
}

.nav-more-dropdown.open {
  display: block;
  animation: ann-fadein 0.15s ease;
}

.nav-more-dropdown a {
  display: block;
  padding: 8px 16px;
  font-family: 'DM Mono', monospace;
  font-size: 12px;
  color: var(--text-muted, #6b5f4e);
  text-decoration: none;
  white-space: nowrap;
}

.nav-more-dropdown a:hover {
  background: var(--bg, #f5f0e8);
  color: var(--text, #2a2418);
}

.nav-more-wrapper {
  position: relative;
  display: inline-block;
}
```

Add to shared JS (annotations.js or inline):
```javascript
// Nav more dropdown
document.addEventListener('DOMContentLoaded', () => {
  const btn = document.querySelector('.nav-more-btn');
  const dropdown = document.querySelector('.nav-more-dropdown');
  if (!btn || !dropdown) return;

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    dropdown.classList.toggle('open');
  });

  document.addEventListener('click', () => {
    dropdown.classList.remove('open');
  });
});
```

**HTML change — Curator nav (all curator pages):**
```html
<!-- Before -->
<nav class="header-nav">
  <a href="/" class="nav-link">Daily</a>
  <a href="/curator_library.html" class="nav-link">Library</a>
  <a href="/interests/2026/deep-dives/index.html" class="nav-link">Deep Dives</a>
  <a href="/curator_intelligence.html" class="nav-link">Observations</a>
  <a href="/curator_priorities.html" class="nav-link nav-focus">🎯</a>
</nav>

<!-- After -->
<nav class="header-nav">
  <a href="/" class="nav-link">Daily</a>
  <a href="/curator_library.html" class="nav-link">Library</a>
  <a href="/interests/2026/deep-dives/index.html" class="nav-link">Deep Dives</a>
  <a href="/curator_intelligence.html" class="nav-link">Observations</a>
  <div class="nav-more-wrapper">
    <button class="nav-more-btn">···</button>
    <div class="nav-more-dropdown">
      <a href="/curator_priorities.html">Priorities</a>
    </div>
  </div>
</nav>
```

**HTML change — Research nav (all research pages):**
```html
<!-- Before -->
<nav class="header-nav">
  <a href="/research/dashboard" class="nav-link">Dashboard</a>
  <a href="/research/sessions" class="nav-link">Sessions</a>
  <a href="/research/observe" class="nav-link">Observations</a>
  <a href="/research/candidates" class="nav-link">Queries</a>
  <a href="/research/save" class="nav-link">Save</a>
</nav>

<!-- After -->
<nav class="header-nav">
  <a href="/research/sessions" class="nav-link">Sessions</a>
  <a href="/research/observe" class="nav-link">Observations</a>
  <div class="nav-more-wrapper">
    <button class="nav-more-btn">···</button>
    <div class="nav-more-dropdown">
      <a href="/research/dashboard">Dashboard</a>
      <a href="/research/candidates">Queries</a>
      <a href="/research/save">Save</a>
    </div>
  </div>
</nav>
```

**Files to update:**
Curator pages (update nav in each):
- `templates/curator_briefing.html`
- `curator_intelligence.html`
- `curator_library.html`
- `interests/2026/deep-dives/index.html`
- `curator_priorities.html`

Research pages (update nav in each):
- `web/sessions.html`
- `web/observe.html`
- `web/dashboard.html`
- `web/candidates.html`
- `web/save.html`

**⚠️ Active state:** Each page should still highlight its own tab.
Dashboard, Queries, Save active states now show inside the dropdown
with a subtle highlight — add `.active` class to the dropdown link
on its own page.

**Test:**
```
Curator Daily:
✓ Nav shows: Daily · Library · Deep Dives · Observations · ···
✓ Priorities not visible in main nav
✓ Click ··· → dropdown shows Priorities
✓ Click Priorities → navigates correctly
✓ Click elsewhere → dropdown closes

Research Sessions:
✓ Nav shows: Sessions · Observations · ···
✓ Dashboard/Queries/Save not in main nav
✓ Click ··· → dropdown shows all three
✓ All links navigate correctly

All pages:
✓ Active tab highlighted correctly
✓ No regression on existing navigation
```

---

## Fix 6 — Curator Daily: Condensed Two-Line Article List

**Problem:** Seven-column table. Score and time dominate. Title cramped.

**File:** `templates/curator_briefing.html`

**⚠️ Read the Jinja2 template loop first** before writing any HTML.
Identify exact variable names passed from curator_server.py.
Do not assume variable names — confirm them.

**Target layout:**
```
[1] geo_major · War on the Rocks
    Hellscape Taiwan: A Porcupine Defense in the Drone Age
                                              15h · 23.5  👍 👎 🔖

[2] geo_major · Foreign Affairs  
    Trump, Xi, and the Specter of 1914
                                               2d · 19.0  👍 👎 🔖
```

**New article row structure (adapt variable names to match template):**
```html
<div class="article-row">
  <div class="article-rank">{{ loop.index }}</div>
  <div class="article-body">
    <div class="article-meta">
      <span class="article-category">{{ article.category }}</span>
      <span class="article-source">{{ article.source }}</span>
    </div>
    <div class="article-title-line">
      <a href="{{ article.url }}" 
         target="_blank" 
         class="article-title">{{ article.title }}</a>
    </div>
    <div class="article-footer">
      <span class="article-stats">{{ article.time_ago }} · {{ article.score }}</span>
      <div class="article-actions">
        <!-- existing like/dislike/save/deep-dive buttons — unchanged -->
      </div>
    </div>
  </div>
</div>
```

**CSS (append to existing styles):**
```css
.article-row {
  display: flex;
  align-items: flex-start;
  padding: 14px 0;
  border-bottom: 1px solid var(--border, #ddd6c8);
  gap: 14px;
}

.article-row:hover {
  background: var(--surface, #faf7f2);
  border-radius: 6px;
  padding-left: 8px;
  padding-right: 8px;
  margin: 0 -8px;
}

.article-rank {
  width: 28px;
  height: 28px;
  min-width: 28px;
  background: var(--accent, #8b5e2a);
  color: white;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: 'DM Mono', monospace;
  font-size: 12px;
  font-weight: 600;
  margin-top: 3px;
}

.article-body {
  flex: 1;
  min-width: 0;
}

.article-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.article-category {
  font-family: 'DM Mono', monospace;
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 3px;
  background: var(--surface, #faf7f2);
  border: 1px solid var(--border, #ddd6c8);
  color: var(--text-muted, #6b5f4e);
}

.article-source {
  font-family: 'DM Mono', monospace;
  font-size: 11px;
  color: var(--text-muted, #6b5f4e);
}

.article-title-line {
  margin-bottom: 6px;
}

.article-title {
  font-family: 'Playfair Display', serif;
  font-size: 15px;
  color: var(--text, #2a2418);
  line-height: 1.4;
  text-decoration: none;
}

.article-title:hover {
  color: var(--accent, #8b5e2a);
}

.article-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.article-stats {
  font-family: 'DM Mono', monospace;
  font-size: 10px;
  color: var(--text-dim, #9e9080);
  white-space: nowrap;
}

.article-actions {
  display: flex;
  gap: 4px;
  align-items: center;
}
```

**Test:**
```
Hard refresh http://localhost:8765/
✓ Two-line layout per article
✓ Title full width, not truncated
✓ Score and time visible but muted
✓ Category badge visible
✓ Like/dislike/save buttons work
✓ Deep Dive button works
✓ Article links open correctly
✓ curl http://localhost:8765/ → 200, no 500
```

---

## Build Order

**Safest sequence — one fix at a time, test before next:**

1. Fix 2 (palette) — CSS only, lowest risk, do first
2. Fix 3 (rail gap) — CSS only, safe
3. Fix 1 (floating notes) — template change, verify carefully
4. Fix 4 (AI Observations notes) — additive, safe
5. Fix 5 (··· dropdown) — medium complexity, multiple files
6. Fix 6 (article layout) — most complex, last

If Fix 5 or Fix 6 runs into trouble, they can be deferred.
Fixes 1-4 are independent and must ship regardless.

---

## Final Commit

```bash
# Verify all pages
curl http://localhost:8765/                         → 200
curl http://localhost:8765/research/sessions        → 200
curl http://localhost:8765/research/observe         → 200
curl http://localhost:8765/curator_intelligence.html → 200
curl http://localhost:8765/api/research/annotations?domain=curator → 200

git add -A
git status  # review carefully before committing

git commit -m "fix(ui): floating notes daily, parchment palette, rail gap, AI obs notes, utility dropdown, condensed article list"
git push origin main
```

---

## Rollback Per Fix

```bash
# Fix 1 only
git checkout templates/curator_briefing.html

# Fix 5 only (nav dropdown)
git checkout web/sessions.html web/observe.html web/dashboard.html
git checkout web/candidates.html web/save.html
git checkout curator_intelligence.html curator_library.html
git checkout templates/curator_briefing.html

# Fix 6 only (article layout)
git checkout templates/curator_briefing.html

# All CSS changes
git checkout web/static/css/annotations.css
```

---

## What This Does NOT Touch

- annotations.py — untouched
- research.py, observe.py, curator_rss_v2.py — untouched
- All API routes — untouched
- Tab label names (Journal, Threads etc.) — future session
- Left rail structure — untouched
- JSON data files — untouched
- Telegram path — untouched
