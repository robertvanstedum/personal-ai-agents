// Conversations (Guild 1.1 slice 2): the history column (a drawer on a
// phone). New, Rename, Pin/Unpin, Remove from list (archive; nothing is
// erased), the Archive view and Restore. Every action is one owner-guarded,
// CSRF-checked write to the server's conversation files; none calls a model.
// Switching conversations is a navigation (?c=<id>): the page and Master
// Craftsman's session are then that conversation's only.
import { $, $$, el, notice } from './dom.js';
import { apiPost, recordMode } from './api.js';
import { newKey } from './actions.js';

let page;

function floorUrl(cid) {
  return cid ? `${page.urls.floor}?c=${encodeURIComponent(cid)}` : page.urls.floor;
}

// A kept note may have titled its conversation (the first message): show it.
export function applyConversation(conv) {
  if (!conv || !page || !page.conversation || conv.id !== page.conversation.id) return;
  page.conversation = conv;
  for (const n of $$('[data-conv-current-title]')) n.textContent = conv.title;
  const row = $(`[data-conv="${conv.id}"] [data-conv-title]`);
  if (row) {
    const pin = row.querySelector('.fh-pin');
    row.replaceChildren(...(pin ? [pin] : []), document.createTextNode(conv.title));
  }
}

async function write(path, body) {
  const r = await apiPost(path, { idempotency_key: newKey(), record_mode: recordMode(), ...body });
  if (!r.ok) notice((r.body && r.body.message) || 'Nothing was changed.');
  return r;
}

function closeMenus(except = null) {
  for (const m of $$('[data-conv-actions]')) {
    if (m === except) continue;
    m.hidden = true;
    const b = m.parentElement.querySelector('[data-conv-menu]');
    if (b) b.setAttribute('aria-expanded', 'false');
  }
}

function startRename(row, cid) {
  const title = row.querySelector('[data-conv-title]');
  const input = el('input', { class: 'fh-rename', 'data-conv-rename': true, maxlength: '80', 'aria-label': 'New title' });
  input.value = (page.conversation && page.conversation.id === cid ? page.conversation.title : title.textContent).trim();
  const link = row.querySelector('[data-conv-link]');
  link.hidden = true;
  row.prepend(input);
  input.focus();
  input.select();
  let finished = false;
  const done = async (save) => {
    if (finished) return;
    finished = true;
    input.remove();
    link.hidden = false;
    if (!save) { row.querySelector('[data-conv-menu]').focus(); return; }
    const r = await write(`/conversations/${encodeURIComponent(cid)}/rename`, { title: input.value });
    if (r.ok) window.location.assign(floorUrl(page.conversation && page.conversation.id !== cid ? page.conversation.id : cid));
  };
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); done(true); }
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(false); }
  });
  input.addEventListener('blur', () => done(false));
}

export function initConversations(p) {
  page = p;
  if (page.conv_notice) notice(page.conv_notice);
  for (const b of $$('[data-conv-new]')) {
    b.addEventListener('click', async () => {
      const about = b.dataset.aboutItem ? Number(b.dataset.aboutItem) : undefined;
      const r = await write('/conversations', about ? { about_item: about } : {});
      if (r.ok && r.body && r.body.conversation) window.location.assign(floorUrl(r.body.conversation.id));
    });
  }
  const list = $('[data-conv-list]');
  if (!list) return;
  list.addEventListener('click', async (e) => {
    const menuBtn = e.target.closest('[data-conv-menu]');
    if (menuBtn) {
      const menu = menuBtn.parentElement.querySelector('[data-conv-actions]');
      const open = menu.hidden;
      closeMenus(menu);
      menu.hidden = !open;
      menuBtn.setAttribute('aria-expanded', String(open));
      if (open) { const first = menu.querySelector('button'); if (first) first.focus(); }
      return;
    }
    const act = e.target.closest('[data-conv-act]');
    if (!act) return;
    const row = act.closest('[data-conv]');
    const cid = row.dataset.conv;
    closeMenus();
    const action = act.dataset.convAct;
    if (action === 'rename') { startRename(row, cid); return; }
    const r = await write(`/conversations/${encodeURIComponent(cid)}/${action}`, {});
    if (!r.ok) return;
    const current = page.conversation && page.conversation.id;
    if (action === 'archive' && cid === current) window.location.assign(floorUrl(null));        // the latest one opens
    else if (action === 'restore') window.location.assign(floorUrl(cid));
    else window.location.reload();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenus(); });
  document.addEventListener('click', (e) => { if (!e.target.closest('[data-conv]')) closeMenus(); });
}
