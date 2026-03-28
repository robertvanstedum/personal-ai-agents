# BUILD_SITE_CLEANUP_A_2026-03-27.md
*Date: March 27, 2026*
*Author: claude.ai design session*
*Status: Ready for Claude Code*

---

## ⚠️ SCOPE BOUNDARY — READ FIRST

**This build fixes broken UI and improves layouts only.**

**DO NOT:**
- Rename any tabs or navigation labels
- Move or restructure navigation
- Touch utility pages (Dashboard, Queries, Save, Priorities)
- Change any API routes or backend Python
- Touch annotations.py, research.py, observe.py, curator_rss_v2.py
- Touch Telegram path

Nav restructure and utility page tucking is a separate session, not yet specced.

**Telegram:** No impact whatsoever.

---

## Changes in This Build

| # | What | Where | Risk |
|---|---|---|---|
| 1 | Fix notes: floating button not full panel | curator_briefing.html (Jinja2 template) | Low |
| 2 | Notes tab: bigger, more contrast | annotations.css | Low |
| 3 | Daily article list: two-line condensed layout | curator_briefing.html | Medium |
| 4 | B-016: source title truncation fix | annotations.css + sessions.html | Low |

---

## Pre-Flight

```bash
git status   # must be clean
git push     # confirm remote is rollback point

# Verify baseline
curl http://localhost:8765/
curl http://localhost:8765/research/sessions
curl http://localhost:8765/research/observe

# Confirm which template Flask renders for Daily
grep -r "curator_briefing" curator_server.py
grep -r "curator_latest" curator_server.py
# Identify the correct template file before touching anything
```

---

## Fix 1 — Curator Daily Notes: Floating Button

**Problem:** Full ADD NOTE panel visible on right side of Daily briefing,
compressing article list. Should be floating button + compact overlay only.

**File:** `templates/curator_briefing.html` (confirm with grep above)

**Find the current init block** (will look something like):
```html
<div id="ann-comment-area"></div>
<script src="/static/js/annotations.js"></script>
<script>
  document.addEventListener('DOMContentLoaded', () => {
    AnnotationSystem.init('ann-comment-area');
    // or initCollapsiblePanel(...)
  });
</script>
```

**Replace with:**
```html
<script src="/research/static/js/annotations.js"></script>
<script>
  document.addEventListener('DOMContentLoaded', () => {
    AnnotationSystem.initFloating();
    AnnotationSystem.initSelectionPopup();
  });
</script>
```

Note: Remove `<div id="ann-comment-area">` entirely — floating mode
injects its own elements, no container div needed.

**Also verify `<body>` tag has correct data attributes:**
```html
<body data-domain="curator" data-page="daily">
```

**Test:**
```bash
# Hard refresh http://localhost:8765/
# ✓ No right panel visible
# ✓ Article list uses full width
# ✓ Small 💬 button visible bottom-right
# ✓ Click button → compact overlay appears
# ✓ Type note → Save → overlay closes, toast appears
# ✓ Existing like/dislike/save buttons still work
# ✓ curl http://localhost:8765/ → 200
```

---

## Fix 2 — Notes Tab Visibility

**Problem:** `💬 Notes` tab on right edge of Research pages is small
and hard to see.

**File:** `web/static/css/annotations.css`

**Find `.ann-panel-tab` and replace with:**
```css
.ann-panel-tab {
  position: fixed;
  right: 0;
  top: 50%;
  transform: translateY(-50%);
  background: var(--accent, #8b5e2a);  /* changed: accent color, not surface */
  border: none;                          /* changed: no border needed with accent bg */
  border-radius: 6px 0 0 6px;
  padding: 14px 10px;                   /* changed: more padding */
  cursor: pointer;
  z-index: 500;
  font-family: 'DM Mono', monospace;
  font-size: 12px;                      /* changed: slightly larger */
  font-weight: 600;                     /* changed: bolder */
  color: white;                         /* changed: white on accent */
  writing-mode: vertical-rl;
  text-orientation: mixed;
  display: flex;
  align-items: center;
  gap: 6px;
  letter-spacing: 0.05em;
  box-shadow: -2px 0 8px rgba(0,0,0,0.12);  /* added: subtle shadow */
}

.ann-panel-tab:hover {
  background: var(--accent-dim, #7a5226);  /* slightly darker on hover */
}
```

**Test:**
```bash
# Hard refresh http://localhost:8765/research/observe
# ✓ Notes tab clearly visible on right edge
# ✓ Accent color (brown), white text
# ✓ Click opens panel as before
# ✓ No regression on panel open/close/pin
```

---

## Fix 3 — Curator Daily Article List: Condensed Two-Line Layout

**Problem:** Seven-column table layout wastes space. Score and time
dominate columns they don't need. Title gets truncated.

**Target layout — two lines per article:**
```
[1] geo_major          War on the Rocks
    Hellscape Taiwan: A Porcupine Defense in the Drone Age    14h · 23.5  👍 👎 🔖

[2] geo_major          Foreign Affairs
    Trump, Xi, and the Specter of 1914                         2d · 19.0  👍 👎 🔖
```

**File:** `templates/curator_briefing.html`

This is a CSS + HTML template change. The article list is rendered
by Jinja2 from JSON data. 

**Approach:**
1. Find the article row template in the Jinja2 loop
2. Replace the table/column structure with a two-line card structure
3. Title gets full line 2, no truncation
4. Score + time move to end of line 2 (muted, smaller)
5. Action buttons stay right-aligned

**New article row HTML structure (inside the Jinja2 loop):**
```html
<div class="article-row">
  <div class="article-rank">{{ loop.index }}</div>
  <div class="article-body">
    <div class="article-meta">
      <span class="article-category">{{ article.category }}</span>
      <span class="article-source">{{ article.source }}</span>
    </div>
    <div class="article-title-row">
      <span class="article-title">{{ article.title }}</span>
      <span class="article-stats">{{ article.time_ago }} · {{ article.score }}</span>
      <div class="article-actions">
        <!-- existing like/dislike/save buttons unchanged -->
      </div>
    </div>
  </div>
</div>
```

**New CSS (append to existing stylesheet or inline in template):**
```css
.article-row {
  display: flex;
  align-items: flex-start;
  padding: 12px 0;
  border-bottom: 1px solid var(--border, #ddd6c8);
  gap: 12px;
}

.article-row:hover {
  background: var(--surface, #faf7f2);
  margin: 0 -16px;
  padding-left: 16px;
  padding-right: 16px;
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
  margin-top: 2px;
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

.article-title-row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}

.article-title {
  font-family: 'Playfair Display', serif;
  font-size: 15px;
  color: var(--text, #2a2418);
  line-height: 1.4;
  flex: 1;
  min-width: 0;
}

.article-title a {
  color: inherit;
  text-decoration: none;
}

.article-title a:hover {
  color: var(--accent, #8b5e2a);
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

**⚠️ Important:** The Jinja2 template variable names (article.category,
article.time_ago, article.score etc.) must match what curator_server.py
actually passes to the template. **Read the existing template loop first
before writing new HTML** to confirm exact variable names.

**Test:**
```bash
# Hard refresh http://localhost:8765/
# ✓ Two-line layout visible for each article
# ✓ Title not truncated
# ✓ Score and time visible but not dominant
# ✓ Like/dislike/save buttons still work
# ✓ Deep dive button still works
# ✓ Article links still work
# ✓ curl http://localhost:8765/ → 200, no 500
```

---

## Fix 4 — B-016 Source Title Truncation in Sessions

**Problem:** Source titles in session finding cards truncate mid-word
("Hegemonic Transitio", "Hegemonic Tra").

**File:** `web/static/css/annotations.css` or `web/sessions.html` inline CSS

**Find the finding card title style and add:**
```css
.finding-source-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
  display: block;
}
```

Or if the truncation is happening because of a fixed container width,
add `title="{{ source_title }}"` attribute to the element so full title
appears on hover.

**Note:** Claude Code should inspect the actual element in devtools first
to confirm what's causing the truncation before applying a fix.

**Test:**
```bash
# Hard refresh http://localhost:8765/research/sessions
# ✓ Source titles not cut off mid-word
# ✓ Long titles either wrap or show ellipsis at word boundary
# ✓ Hover shows full title if ellipsis applied
```

---

## Build Order

**Do in this exact sequence. Test each before proceeding.**

1. Fix 1 (Curator notes floating) — most impactful, fix broken first
2. Fix 2 (Notes tab visibility) — CSS only, safe
3. Fix 4 (B-016 truncation) — CSS only, safe
4. Fix 3 (Article list layout) — most complex, last

Rationale: Get the two CSS-only fixes done quickly (2+4), then tackle
the template restructure (3) with a clean working state.

---

## Final Commit

```bash
# Verify all pages
curl http://localhost:8765/           → 200
curl http://localhost:8765/research/sessions  → 200
curl http://localhost:8765/research/observe   → 200

# Check no annotation API regression
curl http://localhost:8765/api/research/annotations?domain=curator → 200

git add templates/curator_briefing.html
git add web/static/css/annotations.css
git add web/sessions.html  # if touched for B-016
git status  # review — should be only these files

git commit -m "fix(ui): floating notes on daily, tab visibility, condensed article list, B-016 truncation"
git push origin main
```

---

## Rollback

```bash
git checkout templates/curator_briefing.html
git checkout web/static/css/annotations.css
git checkout web/sessions.html
```

---

## What This Does NOT Touch

- annotations.py — untouched
- research.py, observe.py, curator_rss_v2.py — untouched
- All API routes — untouched
- Nav labels or structure — untouched
- Dashboard, Queries, Save, Priorities pages — untouched
- Telegram path — untouched
- Any JSON data files — untouched
