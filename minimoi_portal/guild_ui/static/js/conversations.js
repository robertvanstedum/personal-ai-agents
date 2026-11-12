// Conversations (Guild 1.1 slice 2): the history column (a drawer on a
// phone). New, Rename, Pin/Unpin, Remove from list / Archive this chat (archive, with Undo; nothing is
// erased), the Archive view and Restore. Every action is one owner-guarded,
// CSRF-checked write to the server's conversation files; none calls a model.
// Switching conversations is a navigation (?c=<id>): the page and Master
// Craftsman's session are then that conversation's only.
import { $, $$, el, notice } from './dom.js';
import { apiGet, apiPost, recordMode } from './api.js';
import { newKey } from './actions.js';

let page;
const chatTitle = title => !title || title === 'New conversation' ? 'New chat' : title;

function floorUrl(cid) {
  if (page.chat_scope) {
    const next = new URL(window.location.href);
    if (cid) next.searchParams.set('c', cid); else next.searchParams.delete('c');
    next.searchParams.set('chat', document.body.dataset.mcMode === 'floating' ? 'floating' : 'open');
    return next.href;
  }
  return cid ? `${page.urls.floor}?c=${encodeURIComponent(cid)}` : page.urls.floor;
}

function initPicker() {
  const picker = $('[data-conv-picker]'), body = $('[data-conv-picker-body]');
  if (!picker || !body) return;
  let rows = [], offset = 0, archived = false, generation = 0;
  const button = (label, action) => { const b = el('button', {type: 'button', class: 'btn-quiet'}, label); b.addEventListener('click', action); return b; };
  function draw() {
    const list = el('ol', {class: 'mc-history-list', 'aria-label': archived ? 'Archived conversations' : 'Recent conversations'});
    for (const conv of rows.slice(offset, offset + 10)) {
      const link = el('a', {href: floorUrl(conv.id), 'data-conv-pick': conv.id}, conv.title);
      if (conv.id === page.conversation?.id) link.setAttribute('aria-current', 'true');
      const row = el('li'); row.append(link); list.append(row);
    }
    body.replaceChildren(list);
    if (!rows.length) body.append(el('p', {}, archived ? 'Nothing archived.' : 'No conversations yet.'));
    const controls = el('div', {class: 'mc-history-controls'});
    if (offset) controls.append(button('Newer', () => { offset -= 10; draw(); }));
    if (offset + 10 < rows.length) controls.append(button('Older', () => { offset += 10; draw(); }));
    controls.append(button(archived ? 'Recent' : 'Archived', () => { archived = !archived; load(); }));
    body.append(controls);
  }
  async function load() {
    const current = ++generation;
    body.replaceChildren(el('p', {role: 'status'}, 'Loading…'));
    const r = await apiGet(`/conversations?view=${archived ? 'archived' : 'active'}`);
    if (current !== generation) return;
    if (!r.ok || !Array.isArray(r.body?.conversations)) {
      body.replaceChildren(el('p', {role: 'status'}, 'History is unavailable right now.'), button('Retry', load)); return;
    }
    rows = r.body.conversations; offset = 0; draw();
    if (r.body.unreadable) body.append(el('p', {role: 'status'}, 'Some conversations could not be read.'));
  }
  picker.addEventListener('toggle', () => { if (picker.open) load(); });
  picker.addEventListener('keydown', e => { if (e.key === 'Escape') { e.stopPropagation(); picker.open = false; picker.querySelector('summary').focus(); } });
}

// A kept note may have titled its conversation (the first message): show it.
export function applyConversation(conv) {
  if (!conv || !page || !page.conversation || conv.id !== page.conversation.id) return;
  page.conversation = conv;
  for (const n of $$('[data-conv-current-title]')) n.textContent = chatTitle(conv.title);
  const row = $(`[data-conv="${conv.id}"] [data-conv-title]`);
  if (row) {
    const pin = row.querySelector('.fh-pin');
    row.replaceChildren(...(pin ? [pin] : []), document.createTextNode(chatTitle(conv.title)));
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

// Archive this chat (FF-02): one write to the per-conversation archive route, the row leaves the list, and "Archived · Undo"
// shows for a few seconds. Archiving the open chat lands on the next one in the list (else a new chat); the Undo then rides
// the navigation in this tab's sessionStorage, read once on the next page. Undo restores it and opens it again.
const UNDO_KEY = 'guild-archive-undo', UNDO_MS = 8000;

function rememberUndo(cid, title) {
  try { window.sessionStorage.setItem(UNDO_KEY, JSON.stringify({ cid, title, at: Date.now() })); } catch (_) { /* no Undo across pages */ }
}

function takeUndo() {
  try {
    const raw = window.sessionStorage.getItem(UNDO_KEY);
    window.sessionStorage.removeItem(UNDO_KEY);
    const v = raw ? JSON.parse(raw) : null;
    const age = v ? Date.now() - Number(v.at) : NaN;
    return v && typeof v.cid === 'string' && age >= 0 && age < 30000 ? v : null;     // only just now, in this tab
  } catch (_) { return null; }
}

function listRow(cid) { return $$('[data-conv-list] [data-conv]').find(r => r.dataset.conv === cid) || null; }

function nextChat(cid) {
  const rows = $$('[data-conv-list] [data-conv]').filter(r => !r.hidden || r.dataset.conv === cid);
  const i = rows.findIndex(r => r.dataset.conv === cid);
  const next = i < 0 ? rows[0] : (rows[i + 1] || rows[i - 1]);
  return next && next.dataset.conv !== cid ? next.dataset.conv : null;
}

export function showArchivedUndo(cid, title, reopen) {
  for (const old of $$('[data-undo-toast]')) old.remove();
  const box = el('div', { class: 'gu-toast gu-undo-toast', role: 'status', 'data-undo-toast': true });
  const words = el('span', {}, 'Archived');
  if (title) words.setAttribute('title', chatTitle(title));
  const undo = el('button', { type: 'button', class: 'gu-undo', 'data-undo-archive': cid }, 'Undo');
  undo.setAttribute('aria-label', `Undo: bring back ${chatTitle(title)}`);
  box.append(words, document.createTextNode(' · '), undo);
  let timer = 0;
  const arm = () => { window.clearTimeout(timer); timer = window.setTimeout(() => box.remove(), UNDO_MS); };
  box.addEventListener('focusin', () => window.clearTimeout(timer));   // reading or reaching Undo keeps it
  box.addEventListener('pointerenter', () => window.clearTimeout(timer));
  box.addEventListener('focusout', arm);
  box.addEventListener('pointerleave', arm);
  undo.addEventListener('click', async () => {
    undo.disabled = true;
    const r = await write(`/conversations/${encodeURIComponent(cid)}/restore`, {});
    if (!r.ok) { box.remove(); return; }
    window.location.assign(reopen ? floorUrl(cid) : floorUrl(page.conversation && page.conversation.id));
  });
  document.body.append(box);
  arm();
  return box;
}

export async function archiveChat(cid) {
  if (!cid) return false;
  const row = listRow(cid);
  const title = row ? (row.querySelector('[data-conv-title]') || row).textContent.replace('📌', '').trim()
    : (page.conversation && page.conversation.id === cid ? page.conversation.title : '');
  const current = page.conversation && page.conversation.id;
  const next = cid === current ? nextChat(cid) : null;      // chosen before the row leaves the list
  const r = await write(`/conversations/${encodeURIComponent(cid)}/archive`, {});
  if (!r.ok) return false;
  if (row) row.remove();
  if (cid !== current) { showArchivedUndo(cid, title, false); return true; }
  rememberUndo(cid, title);
  if (next) { window.location.assign(floorUrl(next)); return true; }
  const made = await write('/conversations', page.chat_scope ? { scope: page.chat_scope } : {});
  window.location.assign(floorUrl(made.ok && made.body && made.body.conversation ? made.body.conversation.id : null));
  return true;
}

export function initConversations(p) {
  page = p;
  initPicker();
  if (page.conv_notice) notice(page.conv_notice);
  const undo = takeUndo();
  if (undo) showArchivedUndo(undo.cid, undo.title, true);
  for (const b of $$('[data-fb-archive]')) {
    b.addEventListener('click', () => {
      const more = b.closest('details');
      if (more) more.open = false;
      archiveChat(page.conversation && page.conversation.id);
    });
  }
  for (const b of $$('[data-conv-new]')) {
    b.addEventListener('click', async () => {
      const about = b.dataset.aboutItem ? Number(b.dataset.aboutItem) : undefined;
      const body = about ? { about_item: about } : {};
      if (page.chat_scope) body.scope = page.chat_scope;
      const r = await write('/conversations', body);
      if (r.ok && r.body && r.body.conversation) {
        window.location.assign(floorUrl(r.body.conversation.id));
      }
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
    if (action === 'archive') { archiveChat(cid); return; }
    const r = await write(`/conversations/${encodeURIComponent(cid)}/${action}`, {});
    if (!r.ok) return;
    if (action === 'restore') window.location.assign(floorUrl(cid));
    else window.location.reload();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenus(); });
  document.addEventListener('click', (e) => { if (!e.target.closest('[data-conv]')) closeMenus(); });
}
