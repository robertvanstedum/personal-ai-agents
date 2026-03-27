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
