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
      page:   body.dataset.page   || 'unknown',
      topic:  body.dataset.topic  || null,
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
      <button onclick="AnnotationSystem.confirmSelectionNote()">Save</button>
    `;
    // Store data on the input so we don't need to escape into onclick string
    const input = document.getElementById('ann-sel-input');
    if (input) {
      input.dataset.selectedText = selectedText;
      input.dataset.refTitle = refTitle;
      input.focus();
    }
  },

  async confirmSelectionNote() {
    const input = document.getElementById('ann-sel-input');
    const note = input?.value?.trim();
    if (!note) return;

    const selectedText = input.dataset.selectedText || '';
    const refTitle = input.dataset.refTitle || document.title;

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

// ─── ··· Nav dropdown ───

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
