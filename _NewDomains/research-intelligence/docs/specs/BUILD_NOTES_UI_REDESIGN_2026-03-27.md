# BUILD_NOTES_UI_REDESIGN_2026-03-27.md
*Date: March 27, 2026*
*Author: claude.ai design session*
*Status: Ready for Claude Code*
*Depends on: P1 annotations (commit a556590) — must be merged first*

---

## ⚠️ SCOPE BOUNDARY — READ FIRST

**This build redesigns the notes UI only.**
- No new annotation functionality
- No nav tab renames
- No page restructuring
- No changes to annotations.py, API routes, or JSON storage
- CSS and JS changes only, plus small HTML modifications

**The API contract is unchanged. Only the presentation layer changes.**

---

## Design Principle

**The faster the reading mode, the lighter the notes UI.**

Three tiers:

| Tier | Pages | Pattern |
|---|---|---|
| Speed-read | Curator Daily, Deep Dives | Floating 💬 button only → compact overlay |
| Review | Research Observations, Curator Saved, AI Feedback | Collapsible right panel, pinnable |
| Topic-scoped | Research Sessions | Notes extend left panel, no right panel |

---

## What Changes Per Page

### Research Sessions (`sessions.html`)
**Current:** Notes panel on far right, compresses reading area
**New:** Remove right panel entirely. Notes move into the existing left panel, below the "NOTE FOR NEXT RUN" form.

Left panel bottom section:
```
NOTE FOR NEXT RUN
[textarea]
[Save note →]

──────────────────
RECENT NOTES  (3)
reaction · 3/27
a first test note from Robert

note · 3/26
↳ china-rise-002
Arrighi cycles connect here...
```

Reading area (center + right) gets full width back.

### Research Observations (`observe.html`)
**Current:** Notes panel on far right, always visible
**New:** Collapsible right panel, collapsed by default

Collapsed state:
```
[reading area — full width]    [💬 3]  ← thin tab, right edge
```

Expanded state (click tab):
```
[reading area — reduced]    [ADD NOTE        📌]
                             [textarea          ]
                             [reaction  Save →  ]
                             
                             RECENT NOTES
                             ...
```

📌 pin icon: toggles `.notes-pinned` class on body. Pinned state persists in `localStorage('notes-pinned-observe')`.

### Curator Daily (`curator_latest.html`, `curator_briefing.html`)
**Current:** Notes panel right side
**New:** Floating button only. No panel. Compact overlay on click.

Floating button: fixed bottom-right, `💬` with unread count badge if notes exist today.

Click → compact overlay (not full panel):
```
┌─────────────────────────────┐
│ Quick note                  │
│ [textarea, 2 rows          ]│
│ [reaction ▾]  [Save  Done →]│
└─────────────────────────────┘
```

Overlay appears above the button, disappears after save. No "recent notes" visible — Daily is for reading, not reviewing. Notes are saved and accessible later via Journal (future tab).

### Curator Deep Dives
**Current:** Notes panel right side
**New:** Same as Curator Daily — floating button + compact overlay only.

Deep Dives already has the "+ Research" bibliography action. Notes should be equally light — same floating button pattern, no panel.

### Curator Saved / AI Feedback (`curator_library.html`, `curator_intelligence.html`)
**Current:** Notes panel right side
**New:** Same collapsible right panel as Research Observations. These are review pages, not speed-read pages. Panel pattern is appropriate.

---

## Implementation — CSS Changes

Add to `annotations.css`:

```css
/* ─── Tier 1: Floating button + compact overlay ─── */

.ann-float-btn {
  position: fixed;
  bottom: 24px;
  right: 24px;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: var(--accent, #8b5e2a);
  color: white;
  border: none;
  cursor: pointer;
  font-size: 18px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.15);
  z-index: 500;
  display: flex;
  align-items: center;
  justify-content: center;
}

.ann-float-btn .ann-count {
  position: absolute;
  top: -4px;
  right: -4px;
  background: var(--text, #2a2418);
  color: white;
  font-size: 9px;
  font-family: 'DM Mono', monospace;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.ann-compact-overlay {
  position: fixed;
  bottom: 76px;
  right: 24px;
  width: 300px;
  background: var(--surface, #faf7f2);
  border: 1px solid var(--border, #ddd6c8);
  border-radius: 8px;
  padding: 14px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.12);
  z-index: 600;
  display: none;
}

.ann-compact-overlay.visible {
  display: block;
  animation: ann-fadein 0.15s ease;
}

.ann-compact-header {
  font-family: 'DM Mono', monospace;
  font-size: 10px;
  letter-spacing: 0.08em;
  color: var(--text-muted, #6b5f4e);
  margin-bottom: 8px;
}

.ann-compact-textarea {
  width: 100%;
  font-family: 'Source Sans 3', sans-serif;
  font-size: 13px;
  color: var(--text, #2a2418);
  background: var(--bg, #f5f0e8);
  border: 1px solid var(--border, #ddd6c8);
  border-radius: 4px;
  padding: 7px 9px;
  resize: none;
  box-sizing: border-box;
  min-height: 56px;
}

.ann-compact-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
}

/* ─── Tier 2: Collapsible right panel ─── */

.ann-panel-tab {
  position: fixed;
  right: 0;
  top: 50%;
  transform: translateY(-50%);
  background: var(--surface, #faf7f2);
  border: 1px solid var(--border, #ddd6c8);
  border-right: none;
  border-radius: 6px 0 0 6px;
  padding: 12px 8px;
  cursor: pointer;
  z-index: 500;
  font-family: 'DM Mono', monospace;
  font-size: 11px;
  color: var(--text-muted, #6b5f4e);
  writing-mode: vertical-rl;
  text-orientation: mixed;
  display: flex;
  align-items: center;
  gap: 6px;
}

.ann-panel-tab:hover {
  background: var(--bg, #f5f0e8);
}

.ann-right-panel {
  position: fixed;
  right: -320px;
  top: 60px;
  width: 300px;
  height: calc(100vh - 60px);
  background: var(--surface, #faf7f2);
  border-left: 1px solid var(--border, #ddd6c8);
  padding: 16px;
  overflow-y: auto;
  transition: right 0.2s ease;
  z-index: 490;
}

.ann-right-panel.open {
  right: 0;
}

.ann-panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.ann-panel-title {
  font-family: 'DM Mono', monospace;
  font-size: 10px;
  letter-spacing: 0.08em;
  color: var(--text-muted, #6b5f4e);
}

.ann-pin-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 14px;
  opacity: 0.4;
  padding: 2px;
}

.ann-pin-btn.pinned {
  opacity: 1;
  color: var(--accent, #8b5e2a);
}

/* Pinned: push content left */
body.notes-pinned .main-content,
body.notes-pinned .content-area {
  margin-right: 310px;
  transition: margin-right 0.2s ease;
}

/* ─── Tier 3: Left panel notes (sessions page) ─── */

.ann-left-section {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--border, #ddd6c8);
}

.ann-left-header {
  font-family: 'DM Mono', monospace;
  font-size: 10px;
  letter-spacing: 0.08em;
  color: var(--text-muted, #6b5f4e);
  margin-bottom: 10px;
  display: flex;
  justify-content: space-between;
}

/* Compact note item for left panel */
.ann-left-item {
  padding: 8px 0;
  border-bottom: 1px solid var(--border, #ddd6c8);
  font-size: 12px;
}

.ann-left-item:last-child {
  border-bottom: none;
}
```

---

## Implementation — JS Changes

Add new methods to `annotations.js` (append, do not replace existing):

```javascript
// ─── Tier 1: Floating button + compact overlay ───

AnnotationSystem.initFloating = function() {
  // Inject floating button
  const btn = document.createElement('button');
  btn.className = 'ann-float-btn';
  btn.innerHTML = '💬<span class="ann-count" style="display:none">0</span>';
  btn.onclick = () => this.toggleCompactOverlay();
  document.body.appendChild(btn);

  // Inject compact overlay
  const overlay = document.createElement('div');
  overlay.id = 'ann-compact-overlay';
  overlay.className = 'ann-compact-overlay';
  overlay.innerHTML = `
    <div class="ann-compact-header">QUICK NOTE</div>
    <textarea 
      id="ann-compact-textarea" 
      class="ann-compact-textarea"
      placeholder="Reaction? Question? Just type..."
      rows="2"
    ></textarea>
    <div class="ann-compact-footer">
      <select id="ann-compact-type" class="ann-type-select">
        <option value="reaction">reaction</option>
        <option value="note">note</option>
        <option value="direction_shift">direction shift</option>
      </select>
      <button class="ann-save-btn" onclick="AnnotationSystem.saveFromCompact()">
        Save →
      </button>
    </div>
  `;
  document.body.appendChild(overlay);

  // Close on outside click
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.ann-float-btn') && 
        !e.target.closest('#ann-compact-overlay')) {
      this.closeCompactOverlay();
    }
  });

  this.updateFloatCount();
};

AnnotationSystem.toggleCompactOverlay = function() {
  const overlay = document.getElementById('ann-compact-overlay');
  overlay.classList.toggle('visible');
  if (overlay.classList.contains('visible')) {
    document.getElementById('ann-compact-textarea')?.focus();
  }
};

AnnotationSystem.closeCompactOverlay = function() {
  document.getElementById('ann-compact-overlay')?.classList.remove('visible');
};

AnnotationSystem.saveFromCompact = async function() {
  const textarea = document.getElementById('ann-compact-textarea');
  const typeSelect = document.getElementById('ann-compact-type');
  const note = textarea?.value?.trim();
  if (!note) return;

  await this.save(note, { type: typeSelect?.value || 'reaction' });
  textarea.value = '';
  this.closeCompactOverlay();
  this.updateFloatCount();
};

AnnotationSystem.updateFloatCount = async function() {
  const annotations = await this.loadRecent();
  const countEl = document.querySelector('.ann-float-btn .ann-count');
  if (!countEl) return;
  if (annotations.length > 0) {
    countEl.textContent = annotations.length;
    countEl.style.display = 'flex';
  } else {
    countEl.style.display = 'none';
  }
};

// ─── Tier 2: Collapsible right panel ───

AnnotationSystem.initCollapsiblePanel = function(storageKey) {
  // Inject tab trigger
  const tab = document.createElement('div');
  tab.className = 'ann-panel-tab';
  tab.innerHTML = '💬 Notes';
  tab.onclick = () => this.toggleRightPanel();
  document.body.appendChild(tab);

  // Inject panel
  const panel = document.createElement('div');
  panel.id = 'ann-right-panel';
  panel.className = 'ann-right-panel';
  panel.innerHTML = `
    <div class="ann-panel-header">
      <span class="ann-panel-title">NOTES</span>
      <button class="ann-pin-btn" id="ann-pin-btn" 
              onclick="AnnotationSystem.togglePin('${storageKey}')" 
              title="Pin panel open">📌</button>
    </div>
    <div class="ann-box">
      <textarea 
        id="ann-textarea" 
        class="ann-textarea" 
        placeholder="Reaction? Question? Next step?"
        rows="3"
      ></textarea>
      <div class="ann-box-footer">
        <select id="ann-type" class="ann-type-select">
          <option value="reaction">reaction</option>
          <option value="note">note</option>
          <option value="direction_shift">direction shift</option>
        </select>
        <button class="ann-save-btn" onclick="AnnotationSystem.saveFromBox()">
          Save →
        </button>
      </div>
    </div>
    <div class="ann-sidebar">
      <div class="ann-sidebar-header">RECENT NOTES</div>
      <div id="ann-sidebar-list"></div>
    </div>
  `;
  document.body.appendChild(panel);

  // Restore pinned state
  const pinned = localStorage.getItem(storageKey) === 'true';
  if (pinned) {
    panel.classList.add('open');
    document.body.classList.add('notes-pinned');
    document.getElementById('ann-pin-btn')?.classList.add('pinned');
  }

  this.refreshSidebar();
};

AnnotationSystem.toggleRightPanel = function() {
  const panel = document.getElementById('ann-right-panel');
  panel?.classList.toggle('open');
};

AnnotationSystem.togglePin = function(storageKey) {
  const panel = document.getElementById('ann-right-panel');
  const btn = document.getElementById('ann-pin-btn');
  const isPinned = panel?.classList.contains('open') && 
                   btn?.classList.contains('pinned');

  if (isPinned) {
    btn?.classList.remove('pinned');
    document.body.classList.remove('notes-pinned');
    localStorage.setItem(storageKey, 'false');
  } else {
    panel?.classList.add('open');
    btn?.classList.add('pinned');
    document.body.classList.add('notes-pinned');
    localStorage.setItem(storageKey, 'true');
  }
};

// ─── Tier 3: Left panel injection ───

AnnotationSystem.initLeftPanel = function(leftPanelId) {
  const leftPanel = document.getElementById(leftPanelId);
  if (!leftPanel) return;

  const section = document.createElement('div');
  section.className = 'ann-left-section';
  section.innerHTML = `
    <div class="ann-left-header">
      <span>NOTES</span>
      <span id="ann-left-count"></span>
    </div>
    <div id="ann-left-list"></div>
  `;
  leftPanel.appendChild(section);

  this.refreshLeftPanel();
};

AnnotationSystem.refreshLeftPanel = async function() {
  const list = document.getElementById('ann-left-list');
  const countEl = document.getElementById('ann-left-count');
  if (!list) return;

  const annotations = await this.loadRecent();
  if (countEl) countEl.textContent = annotations.length || '';

  if (annotations.length === 0) {
    list.innerHTML = '<p class="ann-empty">No notes yet.</p>';
    return;
  }

  list.innerHTML = annotations.map(a => `
    <div class="ann-left-item">
      <span class="ann-type-badge ann-type-${a.type}">${a.type}</span>
      <span class="ann-date">${new Date(a.timestamp).toLocaleDateString()}</span>
      ${a.ref_title ? `<div class="ann-ref">↳ ${a.ref_title}</div>` : ''}
      <div class="ann-note">${a.note}</div>
    </div>
  `).join('');
};
```

---

## HTML Changes — Per Page

### sessions.html
1. Remove right-panel `<div id="ann-comment-area">` and its script block
2. Add `id="ann-left-panel"` to the existing left panel container
3. Replace init script:
```html
<script>
  document.addEventListener('DOMContentLoaded', () => {
    const activeTopic = document.querySelector('.topic-item.active')?.dataset?.topic || '';
    document.body.dataset.topic = activeTopic;
    AnnotationSystem.initLeftPanel('ann-left-panel');
    AnnotationSystem.initSelectionPopup();
    // Update topic on selection change
    document.querySelectorAll('.topic-item').forEach(item => {
      item.addEventListener('click', () => {
        document.body.dataset.topic = item.dataset.topic || '';
        AnnotationSystem.refreshLeftPanel();
      });
    });
  });
</script>
```

### observe.html
Replace init script:
```html
<script>
  document.addEventListener('DOMContentLoaded', () => {
    const activeTopic = document.querySelector('.topic-item.active')?.dataset?.topic || '';
    document.body.dataset.topic = activeTopic;
    AnnotationSystem.initCollapsiblePanel('notes-pinned-observe');
    AnnotationSystem.initSelectionPopup();
  });
</script>
```
Remove `<div id="ann-comment-area">`.

### curator_latest.html / curator_briefing.html
Replace init script:
```html
<script>
  document.addEventListener('DOMContentLoaded', () => {
    AnnotationSystem.initFloating();
    AnnotationSystem.initSelectionPopup();
  });
</script>
```
Remove `<div id="ann-comment-area">`.

---

## Build Steps — Safe Sequence

**Pre-flight:**
```bash
git status  # must be clean
git push    # confirm remote is current rollback point

# Verify baseline
curl http://localhost:8765/
curl http://localhost:8765/research/observe
curl http://localhost:8765/research/sessions
```

**Step 1 — CSS only (no functional change)**
Append new CSS classes to `annotations.css`.
Hard refresh — verify no visual regression (existing notes box should look identical).

**Step 2 — JS only (append, don't replace)**
Append new methods to `annotations.js`.
Console verify: `AnnotationSystem.initFloating` exists as a function.
No page changes yet — nothing should break.

**Step 3 — sessions.html first**
Apply HTML changes to sessions.html only.
Test:
- Left panel shows NOTES section at bottom
- Select a topic → notes scoped to that topic
- Add a note → appears in left panel
- Text selection popup still works
- Reading area has full width ✓
- No right panel visible ✓

**Step 4 — observe.html**
Apply HTML changes to observe.html.
Test:
- Right panel tab visible on right edge
- Click tab → panel slides in
- Add note → saves, appears in panel
- Pin button → panel stays open, content shifts left
- Refresh with pin active → panel stays open ✓
- Text selection popup still works ✓

**Step 5 — curator_latest.html**
Apply HTML changes to curator_latest.html.
Test:
- Floating 💬 button bottom-right
- Click → compact overlay appears
- Type note → Save → overlay closes, toast appears
- Count badge updates ✓
- Reading area completely unaffected ✓

**Step 6 — Regression check**
```bash
curl http://localhost:8765/
curl http://localhost:8765/research/observe
curl http://localhost:8765/research/sessions
curl http://localhost:8765/api/research/annotations?domain=research
# All → 200, no errors
```

**Step 7 — Commit**
```bash
git add web/static/css/annotations.css
git add web/static/js/annotations.js
git add web/observe.html
git add web/sessions.html
git add templates/curator_latest.html  # or wherever curator template lives
git commit -m "feat(notes): tiered UI — floating/collapsible/left-panel by reading mode"
git push
```

---

## Rollback
```bash
# Revert individual files if needed
git checkout web/sessions.html
git checkout web/observe.html
git checkout templates/curator_latest.html

# CSS/JS new methods are additive — safe to leave even if HTML reverted
```

---

## What This Does NOT Touch
- `annotations.py` — untouched
- API routes — untouched
- JSON data files — untouched
- Any other HTML pages — untouched
- Nav labels or structure — untouched
- curator_rss_v2.py — untouched
- Telegram path — untouched

---

## Design Principle (for nav redesign doc reference)
*"The faster the reading mode, the lighter the notes UI."*
- Speed-read pages: floating button only
- Review pages: collapsible panel, pinnable
- Topic-scoped pages: notes in left panel

This principle applies to all future pages including Journal, Threads,
AI Feedback when nav redesign ships.
