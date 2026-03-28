# BUILD_COMMENT_ANYWHERE_P1_v2 — Full Spec
*Date: March 27, 2026*
*Author: claude.ai design session*
*Status: Ready for Claude Code*

---

## ⚠️ SCOPE BOUNDARY — READ FIRST

**This build adds annotation/comment functionality only.**

**DO NOT:**
- Rename any tabs or navigation labels
- Change any navigation structure or order
- Reorganize any page layout
- Add comment boxes to pages not listed below
- Touch Priorities, Dashboard, Candidates, or Save pages

A major UI/nav redesign is planned but NOT YET SPECCED.
It is a separate session. If anything seems like it should be
reorganized or renamed — stop, do not do it, note it for later.

**Pages to add comment box to in this build (3 only):**
1. `web/observe.html` — Research Observations
2. `web/sessions.html` — Research Sessions  
3. `curator_latest.html` — Curator Daily Briefing

**All other pages:** untouched. Steps 7 and 8 in this doc
describe rolling out to more pages — skip those steps for now.
Stop after Step 6 (observe.html confirmed working) and the
equivalent additions to sessions.html and curator_latest.html only.

**Telegram:** No impact. This build does not touch curator_rss_v2.py,
the Telegram send path, or any pipeline code.

---

## Pre-Flight (Before Any Code)

```bash
# 1. Confirm clean working state
git status
# Must be clean. If not, commit everything first.

git push origin main
# Confirm pushed to remote — this is your rollback point.

# 2. Confirm server is running and baseline works
curl http://localhost:8765/
curl http://localhost:8765/research/observe
# Both should return 200.

# 3. Note current passing state
# Manually verify in browser:
# - Curator Daily loads
# - Research Sessions loads
# - Research Observations loads
# - Deep Dive opens from Deep Dives archive
# If anything is already broken, stop and fix before proceeding.
```

---

## What This Build Does

Adds a comment/annotation layer to every page in mini-moi:
- A **comment box at the bottom** of every page (always visible, no hunting)
- A **text selection popup** on article/finding content (select text → "Add note")
- Both write to a new `data/annotations/` directory
- No changes to existing routes, existing JSON files, or existing Python logic

**Purely additive. Nothing existing is modified except HTML files (append only).**

---

## Step 1 — Create Data Directory

```bash
mkdir -p ~/Projects/personal-ai-agents/data/annotations/curator
mkdir -p ~/Projects/personal-ai-agents/data/annotations/research/general

# Verify
ls ~/Projects/personal-ai-agents/data/annotations/
# → curator/  research/
```

No Python changes yet. Just directories.

---

## Step 2 — Backend: New Python Module (annotations.py)

**Create new file** — do not modify any existing Python file in this step.

File: `~/Projects/personal-ai-agents/annotations.py`

```python
"""
annotations.py — Comment anywhere system
Writes to data/annotations/{domain}/{topic}/{YYYY-MM-DD}.json
Purely additive — no existing modules modified.
"""

import json
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path

# Base path — same pattern as rest of project
BASE_DIR = Path(__file__).parent
ANNOTATIONS_DIR = BASE_DIR / "data" / "annotations"


def _get_annotation_path(domain: str, topic: str = None) -> Path:
    """
    Returns the path for today's annotation file.
    domain: "curator" or "research"
    topic: e.g. "empire-landpower", None → "general"
    """
    if domain == "curator":
        dir_path = ANNOTATIONS_DIR / "curator"
    else:
        topic_slug = topic if topic else "general"
        dir_path = ANNOTATIONS_DIR / "research" / topic_slug

    dir_path.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    return dir_path / f"{date_str}.json"


def _load_annotations(path: Path) -> list:
    """Load existing annotations for today, or empty list."""
    if not path.exists():
        return []
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _generate_note_id(note: str, timestamp: str) -> str:
    """Short hash ID for the note — same pattern as rest of project."""
    content = f"{note}{timestamp}"
    return hashlib.md5(content.encode()).hexdigest()[:8]


def save_annotation(
    note: str,
    domain: str,           # "curator" or "research"
    page: str,             # "daily", "observe", "sessions", etc.
    topic: str = None,     # research topic slug or None
    ref_type: str = None,  # "article", "finding", "session", None
    ref_id: str = None,    # hash ID of referenced item
    ref_title: str = None, # title truncated to 80 chars
    ref_text: str = None,  # selected text passage
    url: str = None,       # article URL if available
    annotation_type: str = "reaction"  # "reaction", "note", "direction_shift"
) -> dict:
    """
    Save a single annotation. Returns the saved record.
    Deduplication: skips exact (note + ref_id) duplicates.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    note_id = _generate_note_id(note, timestamp)

    record = {
        "note_id": note_id,
        "timestamp": timestamp,
        "domain": domain,
        "page": page,
        "type": annotation_type,
        "note": note.strip(),
        "topic": topic,
        "ref_type": ref_type,
        "ref_id": ref_id,
        "ref_title": ref_title[:80] if ref_title else None,
        "ref_text": ref_text[:500] if ref_text else None,
        "url": url,
        "influenced_sessions": []  # reserved for graph layer
    }

    path = _get_annotation_path(domain, topic)
    existing = _load_annotations(path)

    # Dedup check — same note text + same ref
    for existing_record in existing:
        if (existing_record.get("note") == record["note"] and
                existing_record.get("ref_id") == record["ref_id"]):
            return existing_record  # already saved, return existing

    existing.append(record)

    with open(path, "w") as f:
        json.dump(existing, f, indent=2)

    return record


def get_recent_annotations(
    domain: str,
    topic: str = None,
    limit: int = 10
) -> list:
    """
    Returns most recent N annotations for a domain/topic.
    Reads today's file only for now — expand to multi-day later.
    """
    path = _get_annotation_path(domain, topic)
    annotations = _load_annotations(path)
    # Newest first
    return sorted(annotations, key=lambda x: x["timestamp"], reverse=True)[:limit]
```

**Test the module before continuing:**
```bash
cd ~/Projects/personal-ai-agents
python3 -c "
from annotations import save_annotation, get_recent_annotations
result = save_annotation(
    note='Test annotation',
    domain='research',
    page='observe',
    topic='empire-landpower'
)
print('Saved:', result['note_id'])
recent = get_recent_annotations('research', 'empire-landpower')
print('Recent count:', len(recent))
"
# Should print note_id and count: 1
# If error → fix before continuing. Do not proceed to routes.
```

---

## Step 3 — Backend: Add Routes to research_routes.py

**Append to the END of `research_routes.py` only.**
Do not modify any existing route. Do not touch the top of the file.

```python
# ─────────────────────────────────────────────
# ANNOTATIONS — Comment anywhere
# Added: 2026-03-27
# ─────────────────────────────────────────────

from annotations import save_annotation, get_recent_annotations

@research_bp.route('/api/research/annotate', methods=['POST'])
def annotate():
    """
    Save an annotation from any research page.
    Body: { note, domain, page, topic?, ref_type?, ref_id?,
            ref_title?, ref_text?, url?, type? }
    """
    try:
        data = request.get_json()
        if not data or not data.get('note', '').strip():
            return jsonify({'error': 'note required'}), 400

        record = save_annotation(
            note=data['note'],
            domain=data.get('domain', 'research'),
            page=data.get('page', 'unknown'),
            topic=data.get('topic'),
            ref_type=data.get('ref_type'),
            ref_id=data.get('ref_id'),
            ref_title=data.get('ref_title'),
            ref_text=data.get('ref_text'),
            url=data.get('url'),
            annotation_type=data.get('type', 'reaction')
        )
        return jsonify({'status': 'saved', 'note_id': record['note_id']})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@research_bp.route('/api/research/annotations', methods=['GET'])
def get_annotations():
    """
    Get recent annotations.
    Query params: domain, topic, limit (default 10)
    """
    try:
        domain = request.args.get('domain', 'research')
        topic = request.args.get('topic')
        limit = int(request.args.get('limit', 10))
        annotations = get_recent_annotations(domain, topic, limit)
        return jsonify({'annotations': annotations, 'count': len(annotations)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
```

**Test routes before touching any HTML:**
```bash
# Restart server
launchctl kickstart -k gui/$(id -u)/com.vanstedum.curator

# Test POST
curl -X POST http://localhost:8765/api/research/annotate \
  -H "Content-Type: application/json" \
  -d '{"note": "test from curl", "domain": "research", "page": "observe", "topic": "empire-landpower"}'
# → {"status": "saved", "note_id": "xxxxxxxx"}

# Test GET
curl "http://localhost:8765/api/research/annotations?domain=research&topic=empire-landpower"
# → {"annotations": [...], "count": 1}

# Confirm existing routes still work
curl http://localhost:8765/api/research/dashboard
curl http://localhost:8765/research/observe
# Both → 200, no errors
```

**If any existing route breaks → rollback immediately:**
```bash
git checkout research_routes.py
# Remove the appended block and re-test
```

---

## Step 4 — Shared JS Module

**Create new file** — do not modify any existing JS.

File: `~/Projects/personal-ai-agents/web/static/js/annotations.js`

```javascript
/**
 * annotations.js — Comment anywhere system
 * Include on any page that should support annotations.
 * Reads page context from data-* attributes on <body>.
 * 
 * Usage: <body data-domain="research" data-page="observe" data-topic="empire-landpower">
 */

const AnnotationSystem = {

  // Get page context from body attributes
  getContext() {
    const body = document.body;
    return {
      domain: body.dataset.domain || 'research',
      page: body.dataset.page || 'unknown',
      topic: body.dataset.topic || null,
    };
  },

  // Save annotation via API
  async save(note, extra = {}) {
    const context = this.getContext();
    const payload = {
      note,
      ...context,
      ...extra  // ref_type, ref_id, ref_title, ref_text, url, type
    };

    try {
      const res = await fetch('/api/research/annotate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (data.status === 'saved') {
        this.showToast('Note saved ✓');
        this.refreshSidebar();
        return data;
      }
    } catch (err) {
      this.showToast('Save failed — try again', true);
      console.error('Annotation save error:', err);
    }
  },

  // Load recent annotations for sidebar
  async loadRecent() {
    const { domain, topic } = this.getContext();
    const params = new URLSearchParams({ domain, limit: 10 });
    if (topic) params.append('topic', topic);

    try {
      const res = await fetch(`/api/research/annotations?${params}`);
      const data = await res.json();
      return data.annotations || [];
    } catch (err) {
      console.error('Annotation load error:', err);
      return [];
    }
  },

  // Render sidebar list
  async refreshSidebar() {
    const sidebar = document.getElementById('ann-sidebar-list');
    if (!sidebar) return;

    const annotations = await this.loadRecent();
    if (annotations.length === 0) {
      sidebar.innerHTML = '<p class="ann-empty">No notes yet.</p>';
      return;
    }

    sidebar.innerHTML = annotations.map(a => `
      <div class="ann-item">
        <span class="ann-type-badge ann-type-${a.type}">${a.type}</span>
        <span class="ann-date">${new Date(a.timestamp).toLocaleDateString()}</span>
        ${a.ref_title ? `<div class="ann-ref">↳ ${a.ref_title}</div>` : ''}
        <div class="ann-note">${a.note}</div>
      </div>
    `).join('');
  },

  // Toast notification
  showToast(message, isError = false) {
    const existing = document.getElementById('ann-toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.id = 'ann-toast';
    toast.className = `ann-toast ${isError ? 'ann-toast-error' : ''}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 2500);
  },

  // Inject bottom comment box HTML into target element
  injectCommentBox(targetId) {
    const target = document.getElementById(targetId);
    if (!target) return;

    target.innerHTML = `
      <div class="ann-box">
        <div class="ann-box-header">ADD NOTE</div>
        <textarea 
          id="ann-textarea" 
          class="ann-textarea" 
          placeholder="Reaction? Question? Next step? Just type..."
          rows="3"
        ></textarea>
        <div class="ann-box-footer">
          <select id="ann-type" class="ann-type-select">
            <option value="reaction">reaction</option>
            <option value="note">note</option>
            <option value="direction_shift">direction shift</option>
          </select>
          <button id="ann-save-btn" class="ann-save-btn" onclick="AnnotationSystem.saveFromBox()">
            Save note →
          </button>
        </div>
      </div>
      <div class="ann-sidebar">
        <div class="ann-sidebar-header">RECENT NOTES</div>
        <div id="ann-sidebar-list"></div>
      </div>
    `;

    this.refreshSidebar();
  },

  // Save from bottom box
  async saveFromBox() {
    const textarea = document.getElementById('ann-textarea');
    const typeSelect = document.getElementById('ann-type');
    const note = textarea?.value?.trim();
    if (!note) return;

    await this.save(note, { type: typeSelect?.value || 'reaction' });
    textarea.value = '';
  },

  // Text selection popup
  initSelectionPopup() {
    document.addEventListener('mouseup', (e) => {
      const existing = document.getElementById('ann-selection-popup');
      if (existing) existing.remove();

      const selection = window.getSelection();
      const text = selection?.toString()?.trim();
      if (!text || text.length < 10) return;

      // Don't trigger inside the annotation box itself
      if (e.target.closest('.ann-box') || e.target.closest('.ann-sidebar')) return;

      const range = selection.getRangeAt(0);
      const rect = range.getBoundingClientRect();

      const popup = document.createElement('div');
      popup.id = 'ann-selection-popup';
      popup.className = 'ann-selection-popup';
      popup.innerHTML = `<button onclick="AnnotationSystem.saveSelectionNote()">💬 Add note</button>`;
      popup.style.top = `${window.scrollY + rect.top - 40}px`;
      popup.style.left = `${window.scrollX + rect.left}px`;

      // Store selected text for saving
      popup.dataset.selectedText = text;
      popup.dataset.refTitle = document.title;

      document.body.appendChild(popup);
    });

    // Remove popup on click elsewhere
    document.addEventListener('mousedown', (e) => {
      if (!e.target.closest('#ann-selection-popup')) {
        document.getElementById('ann-selection-popup')?.remove();
      }
    });
  },

  // Save from text selection
  async saveSelectionNote() {
    const popup = document.getElementById('ann-selection-popup');
    if (!popup) return;

    const selectedText = popup.dataset.selectedText;
    const refTitle = popup.dataset.refTitle;

    // Replace popup with inline input
    popup.innerHTML = `
      <input id="ann-sel-input" type="text" placeholder="Your note on this..." style="width:250px">
      <button onclick="AnnotationSystem.confirmSelectionNote('${selectedText.replace(/'/g, "\\'")}', '${refTitle.replace(/'/g, "\\'")}')">Save</button>
    `;
    document.getElementById('ann-sel-input')?.focus();
  },

  async confirmSelectionNote(selectedText, refTitle) {
    const input = document.getElementById('ann-sel-input');
    const note = input?.value?.trim();
    if (!note) return;

    await this.save(note, {
      ref_type: 'selection',
      ref_title: refTitle,
      ref_text: selectedText,
      type: 'reaction'
    });

    document.getElementById('ann-selection-popup')?.remove();
  },

  // Initialize everything
  init(commentBoxTargetId = 'ann-comment-area') {
    this.injectCommentBox(commentBoxTargetId);
    this.initSelectionPopup();
  }
};
```

**No server restart needed — static file.**
**No existing JS modified.**

---

## Step 5 — Shared CSS

**Append to existing stylesheet** or create new file.
Safest: create `web/static/css/annotations.css` (new file, no existing CSS touched).

```css
/* annotations.css — Comment anywhere system */

/* Bottom comment box */
.ann-box {
  margin: 32px 0 16px 0;
  padding: 16px;
  background: var(--surface, #faf7f2);
  border: 1px solid var(--border, #ddd6c8);
  border-radius: 6px;
}

.ann-box-header {
  font-family: 'DM Mono', monospace;
  font-size: 10px;
  letter-spacing: 0.08em;
  color: var(--text-muted, #6b5f4e);
  margin-bottom: 8px;
}

.ann-textarea {
  width: 100%;
  font-family: 'Source Sans 3', sans-serif;
  font-size: 14px;
  color: var(--text, #2a2418);
  background: var(--bg, #f5f0e8);
  border: 1px solid var(--border, #ddd6c8);
  border-radius: 4px;
  padding: 8px 10px;
  resize: vertical;
  box-sizing: border-box;
}

.ann-textarea:focus {
  outline: none;
  border-color: var(--accent, #8b5e2a);
}

.ann-box-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
}

.ann-type-select {
  font-family: 'DM Mono', monospace;
  font-size: 11px;
  background: transparent;
  border: 1px solid var(--border, #ddd6c8);
  border-radius: 4px;
  padding: 4px 8px;
  color: var(--text-muted, #6b5f4e);
}

.ann-save-btn {
  font-family: 'DM Mono', monospace;
  font-size: 11px;
  background: var(--accent, #8b5e2a);
  color: white;
  border: none;
  border-radius: 4px;
  padding: 6px 14px;
  cursor: pointer;
}

.ann-save-btn:hover {
  opacity: 0.85;
}

/* Recent notes sidebar */
.ann-sidebar {
  margin-top: 16px;
}

.ann-sidebar-header {
  font-family: 'DM Mono', monospace;
  font-size: 10px;
  letter-spacing: 0.08em;
  color: var(--text-muted, #6b5f4e);
  margin-bottom: 8px;
}

.ann-item {
  padding: 10px 0;
  border-bottom: 1px solid var(--border, #ddd6c8);
  font-size: 13px;
}

.ann-type-badge {
  font-family: 'DM Mono', monospace;
  font-size: 9px;
  padding: 2px 6px;
  border-radius: 3px;
  background: var(--surface, #faf7f2);
  border: 1px solid var(--border, #ddd6c8);
  color: var(--text-muted, #6b5f4e);
  margin-right: 6px;
}

.ann-type-badge.ann-type-direction_shift {
  border-color: var(--accent, #8b5e2a);
  color: var(--accent, #8b5e2a);
}

.ann-date {
  font-family: 'DM Mono', monospace;
  font-size: 10px;
  color: var(--text-dim, #9e9080);
}

.ann-ref {
  font-size: 11px;
  color: var(--text-muted, #6b5f4e);
  margin: 4px 0 2px 0;
  font-style: italic;
}

.ann-note {
  color: var(--text, #2a2418);
  line-height: 1.5;
  margin-top: 4px;
}

.ann-empty {
  font-size: 12px;
  color: var(--text-dim, #9e9080);
  font-style: italic;
}

/* Text selection popup */
.ann-selection-popup {
  position: absolute;
  z-index: 1000;
  background: var(--text, #2a2418);
  border-radius: 4px;
  padding: 4px 6px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.2);
}

.ann-selection-popup button {
  background: none;
  border: none;
  color: white;
  font-size: 12px;
  cursor: pointer;
  font-family: 'DM Mono', monospace;
  white-space: nowrap;
}

.ann-selection-popup input {
  font-size: 12px;
  padding: 3px 6px;
  border-radius: 3px;
  border: none;
}

/* Toast */
.ann-toast {
  position: fixed;
  bottom: 24px;
  right: 24px;
  background: var(--text, #2a2418);
  color: white;
  padding: 10px 18px;
  border-radius: 6px;
  font-family: 'DM Mono', monospace;
  font-size: 12px;
  z-index: 2000;
  animation: ann-fadein 0.2s ease;
}

.ann-toast-error {
  background: #8b2a2a;
}

@keyframes ann-fadein {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}
```

---

## Step 6 — Add to ONE Page First (observe.html)

**Test on one page before touching all pages.**

Add to `<head>` of `observe.html`:
```html
<link rel="stylesheet" href="/static/css/annotations.css">
```

Add to `<body>` tag:
```html
<body data-domain="research" data-page="observe" data-topic="">
```
*(topic value populated dynamically by existing JS when topic is selected)*

Add before closing `</body>`:
```html
<div id="ann-comment-area"></div>
<script src="/static/js/annotations.js"></script>
<script>
  // Populate topic from existing page state
  document.addEventListener('DOMContentLoaded', () => {
    const activeTopic = document.querySelector('.topic-item.active')?.dataset?.topic || '';
    document.body.dataset.topic = activeTopic;
    AnnotationSystem.init('ann-comment-area');
  });
</script>
```

Add `id="ann-comment-area"` div at the bottom of the main content area (before closing `</main>` or equivalent).

**Test observe.html end to end:**
```bash
# Hard refresh
# Observe page loads — no visual regression
# Scroll to bottom — comment box visible
# Type a note, click Save → toast appears
# Refresh page — note appears in Recent Notes
# Select text in a synthesis → popup appears → add note → saves

# Check file was written
cat ~/Projects/personal-ai-agents/data/annotations/research/empire-landpower/$(date +%Y-%m-%d).json
# → array with your test notes
```

**Only proceed to other pages if observe.html passes all checks.**

---

## Step 7 — Roll Out to Remaining Research Pages

Once observe.html confirmed working, add same three blocks to:
- `sessions.html` — `data-page="sessions"`
- `candidates.html` — `data-page="candidates"`  
- `save.html` — `data-page="save"`
- `dashboard.html` — `data-page="dashboard"`

**One page at a time. Test each before the next.**

---

## Step 8 — Add to Curator Pages

Curator pages use a different blueprint. Add same pattern to:
- `curator_latest.html` — `data-domain="curator" data-page="daily"`
- `curator_briefing.html` — `data-domain="curator" data-page="daily"`

For curator, topic is null — the context captured is domain + page + article ref (from selection).

Curator annotations write to `data/annotations/curator/YYYY-MM-DD.json`.

**Test curator daily separately** — different template system (Jinja2 via Flask). Confirm the static files aren't being regenerated and overwriting the changes (B-012 is fixed, but verify).

---

## Step 9 — Final Commit

```bash
# Verify all pages load
curl http://localhost:8765/
curl http://localhost:8765/research/observe
curl http://localhost:8765/research/sessions
curl http://localhost:8765/research/candidates

# Verify annotation API
curl "http://localhost:8765/api/research/annotations?domain=research"

# Check no existing routes broken
curl http://localhost:8765/api/research/dashboard
curl http://localhost:8765/api/research/topics

git add .
git status
# Review — should only see:
# new: annotations.py
# new: web/static/js/annotations.js
# new: web/static/css/annotations.css
# modified: web/observe.html (+ 3 lines)
# modified: web/sessions.html (+ 3 lines)
# ... etc
# new: data/annotations/ (directory + test files)

git commit -m "feat(notes): comment anywhere — floating box + text selection + per-domain annotations"
git push origin main
```

---

## Rollback Plan

If anything breaks at any step:

```bash
# Revert HTML changes only (safest)
git checkout web/observe.html
git checkout web/sessions.html
# etc — revert individual files

# Revert everything
git reset --hard HEAD
git push origin main --force
```

The new files (annotations.py, annotations.js, annotations.css, data/annotations/) 
can be left in place — they have no effect if not referenced by HTML.

---

## What This Does NOT Touch

- `curator_rss_v2.py` — untouched
- `research.py` — untouched  
- `observe.py` — untouched
- `threads.py` — untouched
- `curator_server.py` — untouched
- All existing routes — untouched
- All existing JSON data files — untouched
- `candidates.json` — untouched

---

## After P1 Passes — Next Build (P2)

Save Conversation → Journal. Separate session.
Do not start P2 until P1 is committed and pushed clean.
