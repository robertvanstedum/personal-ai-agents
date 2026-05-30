# Curator UI Redesign — Design Plan

*Status: Approved for build. Awaiting OpenClaw issue.*
*Authored by Claude Code from plan mode — 2026-05-29.*
*Screenshots: `_working/curator-screenshots-2026-05-29.pdf`*

---

## Problem

The Curator web app (port 8766) proxied through the mini-moi portal has two compounding problems:

1. **Layout battles**: The existing template uses `body { display: flex }` for the domain-rail + main-content split, and `overflow-x: hidden` on `.main-content`. The portal injects a 38px fixed nav bar treated as a flex item. `overflow-x: hidden` also breaks `position: sticky` in Chrome/Safari — a documented browser quirk. The current proxy fix partially works but the sticky header inside `overflow-x: hidden` still misbehaves.

2. **Stale visual design**: Functional but dated — emoji icons, no article summaries, dense rank-list format. The German app and portal were recently refreshed; Curator should match.

**Decision**: Redesign as portal-first (no standalone domain-rail — the portal provides app-switching). All 5 Curator pages in one pass for visual consistency.

**Key insight**: `summary` field already exists in `curator_latest.json` (e.g., *"Nuclear guarantees cannot replace U.S. forces in Europe."*). Surfacing it requires no pipeline changes.

---

## Architecture Change: Portal-First

**Current (broken under portal):**
```
body { display: flex }
  .domain-rail  ← 200px sticky sidebar (app switcher)
  .main-content { overflow-x: hidden }
    header { position: sticky; top: 0 }  ← broken by overflow-x bug
    <main> articles </main>
```

**New (portal-first):**
```
body { display: block }
  nav.curator-subnav { position: sticky; top: 0 }  ← single sticky element
    Daily · Library · Deep Dives · Observations · 🎯
  main.briefing-main { max-width: 860px; margin: auto }
    articles
```

No domain-rail. No flex body. No `overflow-x: hidden`. One sticky element that the portal adjusts cleanly via `top: 38px !important`.

---

## Files to Change

### 1. `minimoi_portal/proxy.py`

Update Curator offset CSS in `_portal_nav_html()`:

```python
if portal_prefix == "/app/curator":
    offset_css = (
        "body{padding-top:38px!important;}"
        "nav.curator-subnav{top:38px!important;}"
    )
```

### 2. `curator_briefing.html` — full rewrite (Jinja2 template)

**Structure:**
```html
<head>
  <!-- Playfair Display + DM Mono + Source Sans 3 (keep existing fonts) -->
  <!-- CSS vars matching portal.css tokens exactly -->
  <!-- /research/static/css/annotations.css (keep) -->
</head>
<body data-run-mode="..." data-domain="curator" data-page="daily">

  <nav class="curator-subnav">
    <a href="/" class="subnav-tab active">Daily</a>
    <a href="/curator_library.html" class="subnav-tab">Library</a>
    <a href="/interests/2026/deep-dives/index.html" class="subnav-tab">Deep Dives</a>
    <a href="/curator_intelligence.html" class="subnav-tab">Observations</a>
    <a href="/curator_priorities.html" class="subnav-tab subnav-focus">🎯 Focus</a>
  </nav>

  <main class="briefing-main">
    <div class="briefing-header">
      <span class="briefing-date-label">{{ day_str }}, {{ date_str }}</span>
      <span class="briefing-meta-label">{{ model_display }}</span>
    </div>
    <div class="article-list">
      {% for entry in articles %}
      <article class="article-card" data-hash-id="{{ entry.hash_id }}" ...>
        <div class="rank-badge">{{ loop.index }}</div>
        <div class="card-body">
          <div class="card-meta">
            <span class="cat-chip cat-{{ entry.category }}">{{ entry.category }}</span>
            <span class="source-label">{{ entry.source }}</span>
            <span class="score-label">{{ "%.1f"|format(entry.raw_score) }}{% if entry.interest_boosted %} ⭐{% endif %}</span>
            <span class="time-label">{{ entry.time_ago }}</span>
          </div>
          <h2 class="card-title">
            <a href="{{ entry.link }}" target="_blank">{{ entry.title }}</a>
          </h2>
          {% if entry.summary %}
          <p class="card-summary">{{ entry.summary }}</p>
          {% endif %}
          <div class="card-actions">
            <button class="action-btn btn-like" ...>👍 Like</button>
            <button class="action-btn btn-dislike" ...>👎 Pass</button>
            <button class="action-btn btn-save" ...>💾 Save</button>
          </div>
        </div>
      </article>
      {% endfor %}
    </div>
  </main>

  <script><!-- existing showFeedback JS — no changes needed --></script>
</body>
```

**Score display**: Show `entry.raw_score` (Grok score, 0–10 scale) not `entry.final_score` (which includes interest boosts and can exceed 20). If `entry.interest_boosted`, add ⭐ to the score label.

### 3. `curator_library.html` (static HTML)

Structural refresh only — keep all JS and API calls intact:
- Replace `<div class="domain-rail">` + `<header>` block with `<nav class="curator-subnav">` (Library tab active)
- Remove flex body; add CSS vars matching the new palette
- Remove `overflow-x: hidden` on any wrapper divs
- Update button styles to pill shape (`border-radius: 20px`)
- Remove the "Your reading library" page title (redundant under portal nav)
- Keep existing filter/sort/search JS unchanged

### 4. `curator_priorities.html` (static HTML)

Same structural swap as Library:
- Replace domain-rail + header with `<nav class="curator-subnav">` (🎯 Focus tab active)
- Remove flex body
- Remove the "Signal Priorities" page title (redundant under portal nav)
- Refresh priority card borders/shadows to match new palette
- Keep all API call JS and priority feed logic unchanged

### 5. `curator_intelligence.html` (static HTML)

Same structural swap:
- Replace domain-rail + header with `<nav class="curator-subnav">` (Observations tab active)
- Remove flex body
- Observation cards get the same parchment card treatment

### 6. `curator_index.html` (static HTML)

Lightest change — swap structure and refresh archive list styling.

---

## Shared CSS Block (identical across all pages)

```css
:root {
  --bg: #f5f0e8; --bg-mid: #ede7dc; --surface: #faf7f2; --surface2: #f0ebe0;
  --border: rgba(42,31,20,0.15); --shadow: 0 2px 12px rgba(42,31,20,0.12);
  --text: #2a1f14; --text-muted: #7a6558; --text-dim: #9e9080;
  --accent: #8b4513; --accent-light: #c8703a; --accent-dim: rgba(139,69,19,0.08);
  --font-serif: 'Playfair Display', Georgia, serif;
  --font-sans: 'Source Sans 3', system-ui, sans-serif;
  --font-mono: 'DM Mono', 'Courier New', monospace;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: var(--font-sans); background: var(--bg); color: var(--text); font-size: 14px; line-height: 1.5; }

nav.curator-subnav {
  position: sticky; top: 0; z-index: 100;
  height: 44px; background: rgba(245,240,232,0.96);
  backdrop-filter: blur(8px); border-bottom: 1px solid var(--border);
  display: flex; align-items: center; padding: 0 1.5rem;
}
.subnav-tab {
  font-family: var(--font-mono); font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--text-muted); text-decoration: none;
  padding: 0 14px; height: 44px;
  display: flex; align-items: center;
  border-bottom: 2px solid transparent;
}
.subnav-tab.active { color: var(--accent); border-bottom-color: var(--accent); }
.subnav-focus { margin-left: auto; }

.article-card {
  display: flex; gap: 16px;
  padding: 18px 20px; border-bottom: 1px solid var(--border);
}
.rank-badge {
  width: 26px; height: 26px; background: var(--accent); color: white;
  border-radius: 4px; font-family: var(--font-mono); font-size: 11px;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0; margin-top: 2px;
}
.card-title {
  font-family: var(--font-serif); font-size: 1.05rem; font-weight: 600;
  line-height: 1.35; margin-bottom: 5px;
}
.card-summary { font-size: 0.875rem; color: var(--text-muted); line-height: 1.55; margin-bottom: 10px; }
.source-label { font-family: var(--font-mono); font-size: 11px; font-weight: 600; text-transform: uppercase; }
.score-label { font-family: var(--font-mono); font-size: 11px; color: var(--text-dim); margin-left: auto; }
.action-btn {
  font-size: 0.78rem; font-weight: 500;
  padding: 3px 12px; border: 1px solid var(--border);
  border-radius: 20px; background: transparent;
  color: var(--text-muted); cursor: pointer;
}
.action-btn.active { background: var(--text); color: var(--bg); }

/* Category chips */
.cat-geo_major  { background: #e8f4e8; color: #2d6a2d; }
.cat-monetary   { background: #e8eef8; color: #2d4a8a; }
.cat-fiscal     { background: #f8f4e8; color: #8a6a2d; }
.cat-other      { background: var(--bg-mid); color: var(--text-muted); }
```

---

## What Stays Unchanged

- `curator_server.py` — no Python changes
- All JS functions: `showFeedback()`, library filter/sort, priority API calls, deep dive modal
- All API endpoint URLs — only HTML structure changes
- `summary` field in `curator_latest.json` — already exists, no pipeline work
- Feedback loop, scoring, budget enforcement

---

## Verification Checklist

1. Restart portal: `launchctl stop com.vanstedum.minimoi-portal && launchctl start com.vanstedum.minimoi-portal`
2. `https://minimoi.ai/app/curator` — verify:
   - Portal nav bar (38px, dark) at top; Curator sub-nav (44px, parchment) directly below; no gap or overlap
   - Articles show: title (Playfair serif), summary (Source Sans), source (DM Mono caps), score with ⭐ if boosted, category chip, pill action buttons
   - Scrolling: sub-nav sticks, articles scroll naturally
3. `http://localhost:8766` directly — sub-nav sticks at `top: 0` (no portal offset)
4. Click all sub-nav tabs — Library, Deep Dives, Observations, 🎯 Focus — each loads with matching nav
5. Submit a like/dislike — verify POST to `/feedback` succeeds (200, signal recorded)
6. Library: filter/sort still work; deep dive modal opens
7. Focus: priority feed loads; activate/deactivate a priority
