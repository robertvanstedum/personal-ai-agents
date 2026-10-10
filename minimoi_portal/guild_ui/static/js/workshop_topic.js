// The topic Workshop (W1): topics, cards, comments and the collaboration record over the topic's own Master Craftsman
// conversation. Chat | Cards | Split drives the page's chat panel (js/conversation.js): Split docks it beside the cards, Chat fills the
// page, Cards folds it away. Reads and writes only through the topic API (js/api.js guards). All data is set with textContent; the
// only HTML inserted is the server's sanitised Markdown render. Nothing here calls a model or sends a message by itself.
import { $, $$, toast, announce } from './dom.js';
import { apiGet, apiPost, recordMode } from './api.js';
import { live } from './state.js';
import { newKey } from './actions.js';
import { setPanelMode } from './conversation.js';

const mcName = () => document.body.dataset.mcName || 'Master Craftsman';
const mcSay = (text) => String(text).split('Master Craftsman').join(mcName());
// A partner that reads no documents (the Chief of Staff) cannot be shown a topic item: the "Ask about this" actions are off,
// not promised and then refused. The server refuses the same turn (files_unsupported).
const askOff = () => document.body.dataset.mcFiles === 'false';
const askOffWhy = () => `${mcName()} can’t read topic items yet. Nothing was added to your message.`;

const SPLIT_MIN = 1100;                       // MC about 380 px and the work side about 640 px
const FLOW = ['queued', 'delivered', 'acknowledged', 'working', 'returned'];
const KIND_WORDS = { note: 'Note', document: 'Document', design: 'Design', request: 'Request' };
const DISP_WORDS = { accepted: 'Accepted', changed: 'Changed', declined: 'Declined', deferred: 'Deferred' };
const JOURNAL_KINDS = [['note', 'Note'], ['question', 'Question'], ['decision', 'Decision'], ['evidence', 'Evidence'], ['alternative', 'Alternative'], ['disagreement', 'Disagreement'], ['finding', 'Finding']];
const wide = () => window.innerWidth >= SPLIT_MIN;
const phone = () => window.matchMedia('(max-width: 700px)').matches;

let page, root, S;

function h(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') n.className = v; else if (k === 'style') n.style.cssText = v;     // set through the CSSOM: the page's CSP has no inline styles
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of kids.flat()) if (c != null && c !== false) n.append(c.nodeType ? c : document.createTextNode(String(c)));
  return n;
}
const api = (p) => `${page.urls.api}${p}`;
const itemOf = (id) => S.items.find((i) => i.id === id);
const sp = () => new URLSearchParams(window.location.search);

// ── writes: refused off the record, one idempotency key per action ──
async function post(path, body) {
  if (live.off) { toast(page.off_record_text); return null; }
  const r = await apiPost(path, { idempotency_key: newKey(), ...(body || {}) });
  if (!r.ok) { toast((r.body && r.body.message) || 'That could not be saved. Nothing was changed.'); return null; }
  return r.body;
}
async function upload(path, file, fields) {
  if (live.off) { toast(page.off_record_text); return null; }
  const fd = new FormData();
  fd.append('file', file);
  for (const [k, v] of Object.entries(fields || {})) if (v) fd.append(k, v);
  let res;
  try {
    res = await fetch(api(path), { method: 'POST', credentials: 'same-origin', cache: 'no-store', body: fd,
      headers: { Accept: 'application/json', 'X-CSRF-Token': page.csrf_token, 'X-Record-Mode': recordMode(), 'Idempotency-Key': newKey() } });
  } catch (e) { toast('The server could not be reached. Nothing was added.'); return null; }
  let data = {};
  try { data = await res.json(); } catch (e) { data = {}; }
  if (!res.ok) { toast(data.message || 'The image could not be added. Nothing was changed.'); return null; }
  return data;
}

// ── dialogs and menus ──
function dialog(title, ...content) {
  const d = h('dialog', { class: 'wt-dialog', 'aria-labelledby': 'wt-d-title' });
  const close = h('button', { type: 'button', class: 'wt-btn wt-quiet', 'aria-label': 'Close dialog' }, '✕');
  d.append(h('header', {}, h('h2', { id: 'wt-d-title' }, title), close), ...content);
  const back = document.activeElement;
  close.addEventListener('click', () => d.close());
  d.addEventListener('close', () => { d.remove(); if (back && document.contains(back)) back.focus(); });
  document.body.append(d);
  d.showModal();
  return d;
}
function closeMenus() { $$('.wt-menu').forEach((m) => { const b = m.parentElement.querySelector('[aria-haspopup]'); if (b) b.setAttribute('aria-expanded', 'false'); m.remove(); }); }
document.addEventListener('click', (e) => { if (!e.target.closest('.wt-menuwrap')) closeMenus(); });
function menu(wrap, items) {
  const btn = wrap.querySelector('[aria-haspopup]');
  if (wrap.querySelector('.wt-menu')) { closeMenus(); return; }
  closeMenus();
  const m = h('div', { class: 'wt-menu', role: 'menu' });
  for (const [label, fn, off] of items) {
    const b = h('button', { type: 'button', role: 'menuitem', disabled: !!off }, label);
    b.addEventListener('click', () => { closeMenus(); btn.focus(); fn(); });
    m.append(b);
  }
  m.addEventListener('keydown', (e) => {
    const bs = $$('button:not(:disabled)', m); const i = bs.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); bs[(i + 1) % bs.length].focus(); }
    if (e.key === 'ArrowUp') { e.preventDefault(); bs[(i - 1 + bs.length) % bs.length].focus(); }
    if (e.key === 'Escape') { e.stopPropagation(); closeMenus(); btn.focus(); }
  });
  wrap.append(m); btn.setAttribute('aria-expanded', 'true');
  placeMenu(m, btn);
  const first = $('button:not(:disabled)', m); if (first) first.focus({ preventScroll: true });
}
// A menu always opens inside the window: below its button when there is room, above it when there is not, and it scrolls itself
// when neither side is tall enough (Robert, 8 Oct: the card menu dropped below the view).
function placeMenu(m, btn) {
  const b = btn.getBoundingClientRect(); const vw = window.innerWidth; const vh = window.innerHeight; const gap = 6;
  m.style.position = 'fixed'; m.style.right = 'auto'; m.style.top = '0px'; m.style.left = '0px'; m.style.maxHeight = ''; m.style.overflowY = 'auto';
  const w = m.offsetWidth; const hgt = m.offsetHeight;
  const below = vh - b.bottom - gap * 2; const above = b.top - gap * 2;
  const down = hgt <= below || below >= above;
  const room = Math.max(120, down ? below : above); const used = Math.min(hgt, room);
  m.style.maxHeight = `${room}px`;
  m.style.top = `${Math.max(gap, down ? b.bottom + gap : b.top - used - gap)}px`;
  m.style.left = `${Math.max(gap, Math.min(b.right - w, vw - w - gap))}px`;
}
window.addEventListener('resize', () => closeMenus());
window.addEventListener('scroll', (e) => { if (!(e.target && e.target.closest && e.target.closest('.wt-menu'))) closeMenus(); }, true);
const menuButton = (label, items, cls = 'wt-btn wt-quiet more') => {
  const wrap = h('span', { class: 'wt-menuwrap' });
  const b = h('button', { type: 'button', class: cls, 'aria-haspopup': 'menu', 'aria-expanded': 'false', 'aria-label': label }, label.startsWith('Actions') ? '⋯' : label);
  b.addEventListener('click', (e) => { e.stopPropagation(); menu(wrap, items()); });
  wrap.append(b);
  return wrap;
};

// ── views: Chat | Cards | Split ──
function effective() { return S.view === 'split' && !wide() ? 'cards' : S.view; }
function setView(v, { remember = true } = {}) {
  S.view = v;
  if (remember) { S.pref = v; if (v !== 'chat') S.last = v; if (S.tid && !live.off) apiPost(`/topics/${S.tid}/layout`, { idempotency_key: newKey(), last_view: v }); }
  applyView();
}
function applyView() {
  const eff = effective();
  document.body.dataset.wtView = eff;
  setPanelMode(eff === 'split' ? 'docked' : eff === 'chat' ? 'full' : 'pill');
  renderControl();
  renderBanners();
  renderBody();
  const u = sp(); u.set('view', eff); if (S.tid) u.set('topic', S.tid); S.openId ? u.set('item_id', S.openId) : u.delete('item_id');
  window.history.replaceState(window.history.state, '', `${window.location.pathname}?${u}`);
}
function renderControl() {
  const eff = effective(); const box = $('[data-wt-views]'); box.replaceChildren();
  const seg = (id, label, on, extra = {}) => h('button', { type: 'button', class: 'wt-btn', 'data-view': id, 'aria-pressed': String(on), ...extra, onclick: () => setView(id) }, label);
  box.append(seg('chat', 'Chat', eff === 'chat'), seg('cards', 'Cards', eff === 'cards'),
    seg('split', 'Split', eff === 'split', wide() ? {} : { disabled: true, title: 'Split needs a window about 1100 px wide', 'aria-describedby': 'wt-split-why' }),
    h('span', { id: 'wt-split-why', class: 'visually-hidden' }, 'Split is available on windows about 1100 pixels wide or more.'));
  $('[data-wt-title]').textContent = S.topic ? S.topic.title : 'Workshop';
}
function renderBanners() {
  const b = $('[data-wt-banners]'); b.replaceChildren();
  if (S.view === 'split' && !wide()) b.append(h('div', { class: 'wt-banner info', role: 'status', 'data-wt-narrow': '' }, h('span', {}, h('strong', {}, 'Split needs a wider window (about 1100 px). '), 'Showing Cards instead. Your chat draft and the open item are kept, and widening the window brings Split back.')));
  if (S.error) b.append(h('div', { class: 'wt-banner bad', role: 'alert' }, h('span', {}, h('strong', {}, S.error)), h('button', { type: 'button', class: 'wt-btn', onclick: () => load() }, 'Retry')));
  if (live.off) b.append(h('div', { class: 'wt-banner warn', role: 'status' }, 'You are off the record (Private): looking is fine; adding, commenting and moving cards are paused.'));
  if (S.convNote) b.append(h('div', { class: 'wt-banner warn', role: 'status' }, S.convNote));
  const R = S.record;
  if (R && ['damaged', 'incomplete', 'missing', 'unavailable'].includes(R.status)) {
    b.append(h('div', { class: `wt-banner ${R.status === 'damaged' ? 'bad' : 'warn'}`, role: 'status', 'data-wt-record-banner': R.status }, h('span', {}, h('strong', {}, 'Shared record: '), R.notice), R.failed ? h('button', { type: 'button', class: 'wt-btn', 'data-wt-record-retry': '', onclick: async () => { await loadRecord(); renderBody(); } }, 'Try again') : null));
  }
}

// ── the divider: the owner sizes the chat and the topic by dragging, instead of choosing a fixed view ──
function setupDivider() {
  const lay = document.querySelector('.gu-layout'); if (!lay || lay.querySelector('[data-wt-divider]')) return;
  const MIN = 18, MAX = 82, DEFAULT = 42;
  const d = h('div', { class: 'wt-divider', role: 'separator', 'aria-orientation': 'vertical', tabindex: '0', 'data-wt-divider': '',
    'aria-label': 'Resize the chat and the topic. Arrow keys move it, Enter resets it.', 'aria-valuemin': String(MIN), 'aria-valuemax': String(MAX) });
  lay.append(d);
  const stored = () => { try { const v = parseFloat(window.localStorage.getItem('wt-chat-pct')); return v >= MIN && v <= MAX ? v : DEFAULT; } catch (e) { return DEFAULT; } };
  const keep = (v) => { try { window.localStorage.setItem('wt-chat-pct', String(v)); } catch (e) { /* the width is a convenience, not a record */ } };
  const clamp = (v) => Math.max(MIN, Math.min(MAX, v));
  const apply = (v) => { lay.style.setProperty('--wt-chat-w', `${v}%`); d.setAttribute('aria-valuenow', String(Math.round(v))); };
  const side = () => { try { return window.localStorage.getItem('wt-chat-side') === 'right' ? 'right' : 'left'; } catch (e) { return 'left'; } };
  const showSide = () => { document.body.dataset.wtSide = side(); };
  showSide();
  let now = stored(), raw = now, sizing = false;
  apply(now);
  const head = document.querySelector('.mc-panel .mc-head');          // small controls in the chat's own header: move it, or close it
  if (head && !head.querySelector('[data-wt-panelctl]')) {
    head.append(h('div', { class: 'wt-panelctl', 'data-wt-panelctl': '' },
      h('button', { type: 'button', class: 'wt-pc', 'data-wt-swap': '', title: 'Move the chat to the other side', 'aria-label': 'Move the chat to the other side', onclick: () => {
        try { window.localStorage.setItem('wt-chat-side', side() === 'left' ? 'right' : 'left'); } catch (e) { /* a convenience only */ }
        showSide();
      } }, '⇄'),
      h('button', { type: 'button', class: 'wt-pc', 'data-wt-close': '', title: 'Close the chat (the Chat button brings it back)', 'aria-label': 'Close the chat', onclick: () => setView('cards') }, '✕')));
  }
  d.addEventListener('pointerdown', (e) => { e.preventDefault(); sizing = true; d.setPointerCapture(e.pointerId); lay.classList.add('wt-sizing'); });
  d.addEventListener('pointermove', (e) => {
    if (!sizing) return;
    const r = lay.getBoundingClientRect(); raw = Math.max(4, Math.min(96, (((side() === 'left' ? e.clientX - r.left : r.right - e.clientX)) / r.width) * 100));
    now = clamp(raw); apply(now);
  });
  const stop = (e, commit) => {
    if (!sizing) return;
    sizing = false; lay.classList.remove('wt-sizing');
    try { d.releasePointerCapture(e.pointerId); } catch (er) { /* already released */ }
    if (!commit) { apply(now = stored()); return; }
    if (raw <= 12) { apply(now = stored()); setView('cards'); }              // dragged all the way left: the chat folds away
    else if (raw >= 88) { apply(now = stored()); setView('chat'); }          // all the way right: the topic steps aside
    else keep(now);
  };
  d.addEventListener('pointerup', (e) => stop(e, true));
  d.addEventListener('pointercancel', (e) => stop(e, false));
  d.addEventListener('keydown', (e) => {
    const step = (e.shiftKey ? 10 : 3) * (side() === 'left' ? 1 : -1);
    if (e.key === 'ArrowLeft') now = clamp(now - step);
    else if (e.key === 'ArrowRight') now = clamp(now + step);
    else if (e.key === 'Home') now = MIN;
    else if (e.key === 'End') now = MAX;
    else if (e.key === 'Enter') now = DEFAULT;
    else return;
    e.preventDefault(); apply(now); keep(now);
  });
  d.addEventListener('dblclick', () => { apply(now = DEFAULT); keep(now); });
}

// ── loading ──
async function load() {
  S.error = null;
  const r = await apiGet(`/topics/${S.tid}`);
  if (!r.ok) { S.error = (r.body && r.body.message) || 'The topic could not be loaded.'; S.topic = S.topic || null; renderBanners(); renderBody(); return; }
  S.topic = r.body.topic; S.items = r.body.items; S.layout = r.body.layout; S.conv = r.body.conversation;
  if (r.body.conversation_missing) {                  // looking never writes: linking the topic's conversation is its own guarded step
    if (live.off) { S.convNote = 'This topic has no conversation yet. Leave Private to link one.'; }
    else {
      const linked = await post(`/topics/${S.tid}/conversation`, {});
      if (linked) { window.location.replace(`${window.location.pathname}?topic=${S.tid}`); return; }
      S.convNote = 'The topic’s conversation could not be linked just now. Cards still work.';
    }
  }
  if (S.topic.conversation_id && (!page.conversation || page.conversation.id !== S.topic.conversation_id)) {
    let again = false;
    try { again = window.sessionStorage.getItem(`wt-relink-${S.tid}`) === '1'; window.sessionStorage.setItem(`wt-relink-${S.tid}`, '1'); } catch (e) { again = true; }
    if (!again) { window.location.replace(`${window.location.pathname}?topic=${S.tid}`); return; }          // the page's own chat was built for another conversation
  }
  const asked = sp().get('view');
  const start = ['chat', 'cards', 'split'].includes(asked) ? asked : (S.layout && S.layout.last_view) || (wide() ? 'split' : 'cards');
  S.pref = start; S.last = start === 'chat' ? 'cards' : start;
  S.view = start;
  const want = sp().get('item_id');
  applyView();
  if (want && itemOf(want)) openItem(want, { push: false });
  await loadJournal();
}
async function loadJournal() {
  const r = await apiGet(`/topics/${S.tid}/journal`);
  S.journal = r.ok ? r.body.entries : [];
  const w = await apiGet(`/topics/${S.tid}/inbox`);            // looking only counts files; nothing is imported until asked
  S.inbox = w.ok ? w.body : { waiting: {}, total: 0 };
  await loadRecord();
  if (!S.openId) renderBody();
}
// The shared Workshop record (the journal) for the topic this one is linked to. Read only; a failure is shown as a failure.
async function loadRecord() {
  const tid = S.tid;
  const r = await apiGet(`/topics/${tid}/record`);
  if (S.tid !== tid) return;                                  // the page moved to another topic meanwhile: never show this answer there
  // A failed call is shown as a failure: not empty, not unlinked, no link claimed, nothing kept from an earlier answer.
  S.record = r.ok && r.body && r.body.status ? r.body : { status: 'unavailable', failed: true, link: null, notice: 'The shared record could not be reached just now. Nothing was changed.', rows: [], waiting_for_you: [] };
  renderBanners();
}
async function refreshIndex() {
  const r = await apiGet(`/topics/${S.tid}`);
  if (r.ok) { S.topic = r.body.topic; S.items = r.body.items; S.layout = r.body.layout; }
}

// ── the board ──
function orderedItems(archived = false) {
  const order = (S.layout && S.layout.order) || [];
  const pos = (id) => { const i = order.indexOf(id); return i < 0 ? 1e6 : i; };
  return S.items.filter((i) => !!i.archived === archived).sort((a, b) => pos(a.id) - pos(b.id) || (a.created < b.created ? -1 : 1));
}
async function saveLayout(patch) {
  const r = await post(`/topics/${S.tid}/layout`, patch);
  if (r) S.layout = r.layout;
  return !!r;
}
async function move(id, d) {
  const ids = orderedItems().map((i) => i.id); const i = ids.indexOf(id); const j = i + d;
  if (j < 0 || j >= ids.length) return;
  [ids[i], ids[j]] = [ids[j], ids[i]];
  if (await saveLayout({ order: ids })) { renderBody(); toast('Moved. Arrangement only: status and approval are unchanged.'); }
}
async function toggleWide(id) {
  const w = new Set((S.layout && S.layout.wide) || []);
  if (w.has(id)) w.delete(id); else w.add(id);
  if (await saveLayout({ wide: [...w] })) renderBody();
}
async function archive(id, away) {
  const r = await post(`/topics/${S.tid}/items/${id}/archive`, { archived: away });
  if (!r) return;
  await refreshIndex();
  if (S.openId === id && away) closeItem();
  renderBody();
  toast(away ? 'Put away. The item and its discussion are kept.' : 'Brought back to current work.');
}
function stepsNode(req) {
  const at = FLOW.indexOf(req.stage);
  return h('ol', { class: 'wt-steps', 'aria-label': `Delivery to ${req.to}` }, FLOW.map((s, i) => h('li', { class: req.stage === 'paused' ? '' : i < at ? 'done' : i === at ? 'here' : '', ...(i === at ? { 'aria-current': 'step' } : {}) }, s[0].toUpperCase() + s.slice(1))),
    req.stage === 'paused' ? h('li', { class: 'here', 'aria-current': 'step' }, 'Paused') : null);
}
function cardNode(it, idx, count) {
  const wideCard = ((S.layout && S.layout.wide) || []).includes(it.id);
  const art = h('article', { class: `wt-card${wideCard ? ' wide' : ''}`, 'data-kind': it.kind, 'data-id': it.id });
  const grip = h('button', { type: 'button', class: 'grip', draggable: 'true', 'aria-label': `Drag ${it.title}`, title: 'Drag to reorder' }, '⠿');
  grip.addEventListener('dragstart', (e) => { S.dragId = it.id; art.classList.add('dragging'); e.dataTransfer.setData('text/plain', it.id); });
  grip.addEventListener('dragend', () => art.classList.remove('dragging'));
  art.addEventListener('dragover', (e) => e.preventDefault());
  art.addEventListener('drop', async (e) => {
    e.preventDefault(); if (!S.dragId || S.dragId === it.id) return;
    const ids = orderedItems().map((i) => i.id); const a = ids.indexOf(S.dragId); const b = ids.indexOf(it.id);
    if (a < 0 || b < 0) return; ids.splice(b, 0, ids.splice(a, 1)[0]); S.dragId = null;
    if (await saveLayout({ order: ids })) renderBody();
  });
  art.append(h('div', { class: 'top' }, h('span', { class: 'wt-eyebrow' }, KIND_WORDS[it.kind]), grip),
    h('button', { type: 'button', class: 'open', onclick: () => openItem(it.id) }, it.title));
  if (it.kind === 'design') art.append(h('img', { class: 'thumb', src: api(`/topics/${S.tid}/items/${it.id}/image?v=thumb`), alt: '', loading: 'lazy' }));
  if (it.request) art.append(stepsNode(it.request));
  const rev = it.revisions.find((r) => r.rev === it.current_rev) || {};
  art.append(h('p', { class: 'txt' }, it.kind === 'design' ? `Version ${it.current_rev} · ${rev.width || '?'} × ${rev.height || '?'}` : `Version ${it.current_rev} · ${it.by}`));
  art.append(h('div', { class: 'foot' },
    h('span', { class: `wt-badge${it.comments_open ? ' warn' : ''}` }, it.comments_open ? `${it.comments_open} open comment${it.comments_open === 1 ? '' : 's'}` : (it.request ? `To ${it.request.to}` : 'No open comments')),
    menuButton(`Actions for ${it.title}`, () => [['Open', () => openItem(it.id)], ...(askOff() ? [] : [[mcSay('Ask Master Craftsman about this'), () => askAbout(it.id)]]),
      ['Move earlier', () => move(it.id, -1), idx === 0], ['Move later', () => move(it.id, 1), idx === count - 1],
      [wideCard ? 'Make compact' : 'Make wider', () => toggleWide(it.id)], ['Put away', () => archive(it.id, true)]])));
  return art;
}
function renderTools() {                       // the toolbar lives in the header row, beside the title: the cards start right under it
  const box = $('[data-wt-tools]'); if (!box) return;
  box.replaceChildren();
  if (!S.tid || !S.topic || S.openId || effective() === 'chat') return;
  const items = orderedItems();
  const away = S.items.filter((i) => i.archived).length;
  box.append(
    menuButton('+ New', () => [['Note', () => newDialog('note')], ['Document', () => newDialog('document')], ['Design (image)', () => newDialog('design')], ['Request or handoff', () => newDialog('request')]], 'wt-btn primary'),
    h('button', { type: 'button', class: 'wt-btn', onclick: previousDialog }, `Previous (${away})`),
    h('button', { type: 'button', class: 'wt-btn', onclick: topicsDialog }, 'Topics'),
    h('span', { class: 'wt-chip' }, `${items.length} card${items.length === 1 ? '' : 's'}`, ' · ', `${items.filter((i) => i.request && ['queued', 'delivered', 'acknowledged', 'working'].includes(i.request.stage)).length} waiting`));
}
function boardNode() {
  const items = orderedItems();
  const wrap = h('div', {});
  const board = h('div', { class: 'wt-board', 'data-wt-board': '' });
  if (!items.length) board.append(h('div', { class: 'wt-empty' }, h('h2', {}, 'Nothing on the desk yet'), h('p', { class: 'wt-note' }, 'Add a note, a document or a design, or bring something back from Previous.'),
    h('button', { type: 'button', class: 'wt-btn primary', onclick: () => newDialog('note') }, '+ Add a note')));
  items.forEach((it, i) => board.append(cardNode(it, i, items.length)));
  wrap.append(board);
  return wrap;
}

// ── the record (journal): quiet by default, one line per entry, everything still there when you open it ──
const REC_SHOWN = 6;
function recordNode() {
  const R = S.record || { status: 'unlinked', rows: [], waiting_for_you: [] };
  const linked = !['unlinked', 'unconfigured'].includes(R.status);
  const own = (S.journal || []).slice().reverse();                        // this page's own notes, newest first
  const shared = linked ? (R.rows || []).slice().reverse() : [];         // the shared record, newest first
  const rows = linked ? shared : own;
  const wait = (S.inbox && S.inbox.total) || 0;
  const asking = linked ? (R.waiting_for_you || []).length : 0;
  const sec = h('details', { class: 'wt-journal', 'aria-labelledby': 'wt-j-h', 'data-wt-record': R.status });
  if (S.recOpen) sec.setAttribute('open', '');
  sec.addEventListener('toggle', () => { S.recOpen = sec.open; });
  const state = { damaged: ['bad', 'damaged'], incomplete: ['warn', 'incomplete'], missing: ['warn', 'not synced'], unavailable: ['warn', 'unavailable'] }[R.status];
  sec.append(h('summary', {}, h('span', { id: 'wt-j-h', class: 'wt-j-title' }, 'Record'),
    h('span', { class: 'wt-note' }, linked && ['damaged', 'missing', 'unavailable'].includes(R.status) ? '' : `${rows.length} entr${rows.length === 1 ? 'y' : 'ies'}`),
    asking ? h('span', { class: 'wt-badge warn' }, `${asking} waiting for you`) : null,
    state ? h('span', { class: `wt-badge ${state[0] === 'bad' ? 'warn' : state[0]}`, 'data-wt-record-state': R.status }, state[1]) : null,
    wait ? h('span', { class: 'wt-badge warn' }, `${wait} contribution${wait === 1 ? '' : 's'} waiting`) : null));
  const body = h('div', { class: 'wt-j-body' });
  body.append(h('p', { class: 'wt-note' }, linked && !R.link
    ? 'The shared record for this topic could not be reached, so nothing from it is shown. Notes kept by this page are below.'
    : linked
    ? `The shared record for “${R.link.topic}” in ${R.link.workshop}: who asked, picked up and reported what. Times are yours. An approval that nothing confirms is shown as claimed; a report from an agent is a report, not a check.`
    : 'Who said what on this topic, with links to the source. An entry an agent left is shown as “claimed”: nothing proves who wrote it.'));
  if (R.status === 'unlinked') {
    body.append(h('p', { class: 'wt-note', 'data-wt-record-unlinked': '' }, R.notice, ' ',
      h('button', { type: 'button', class: 'wt-btn wt-quiet', onclick: linkDialog }, 'Link a shared record')));
  }
  if (R.status === 'unconfigured') body.append(h('p', { class: 'wt-note', 'data-wt-record-unconfigured': '' }, R.notice));
  if (linked && R.notice) body.append(h('div', { class: `wt-banner ${R.status === 'damaged' ? 'bad' : 'warn'}`, role: 'status' }, R.notice));
  if (linked && asking) {
    body.append(h('div', { class: 'wt-banner warn', role: 'group', 'aria-label': 'Waiting for you', 'data-wt-waiting': '' },
      h('div', {}, h('strong', {}, `Waiting for you (${asking})`), h('ul', {}, R.waiting_for_you.map((q) => h('li', {}, `${q.text} `, h('small', {}, `entry ${q.seq} · ${localTimeText(q.at)}`)))), h('p', { class: 'wt-note' }, R.answer_note))));
  }
  const claimed = linked && R.decisions ? (R.decisions.unconfirmed || []) : [];
  for (const c of claimed) {
    body.append(h('div', { class: 'wt-banner info', role: 'status', 'data-wt-claimed': '' }, h('span', {}, h('strong', {}, 'Claimed, not confirmed. '),
      `Entry ${c.seq} (${c.actor}) says “${c.text}” as an ${c.record_event_kind}. Nothing confirms it came from you, so it settles nothing.`)));
  }
  if (linked && R.changed_since && R.changed_since.entries) body.append(h('p', { class: 'wt-note' }, `${R.changed_since.entries} new since entry ${R.changed_since.since_seq}.`));
  if (wait) {
    const who = Object.entries(S.inbox.waiting).map(([a, n]) => `${a} (${n})`).join(', ');
    body.append(h('div', { class: 'wt-inbox', role: 'group', 'aria-label': 'Contributions waiting' },
      h('span', {}, `${wait} contribution${wait === 1 ? '' : 's'} waiting from ${who}. They join the record only when you import them.`),
      h('button', { type: 'button', class: 'wt-btn', onclick: async () => {
        const before = new Set((S.journal || []).map((e) => e.id));
        const r = await post(`/topics/${S.tid}/inbox/import`, {});
        if (!r) return;
        S.recOpen = true; await loadJournal();
        const fresh = (S.journal || []).filter((e) => !before.has(e.id));
        S.fresh = new Set(fresh.map((e) => e.id));
        const bad = (r.rejected || []).length;
        const what = fresh.map((e) => `${e.kind === 'proposal' ? 'a proposal' : `a ${e.kind.replace('_', ' ')}`} from ${e.author.name}`).join(', ');
        S.importNote = fresh.length
          ? `Imported ${fresh.length} from the inbox: ${what}. ${fresh.length === 1 ? 'It is' : 'They are'} now in the Record below (highlighted), shown as claimed, not verified. The original file${fresh.length === 1 ? ' is' : 's are'} kept in the topic folder.`
          : `Nothing new was added${r.already_imported ? ` (${r.already_imported} already in the Record)` : ''}.`;
        if (bad) S.importNote += ` ${bad} file${bad === 1 ? ' was' : 's were'} not valid and set aside, not imported.`;
        if (r.left_waiting) S.importNote += ` ${r.left_waiting} more still waiting: press Import again.`;
        toast(fresh.length ? `Imported ${fresh.length}: now in the Record below.` : 'Nothing new to import.');
        renderBody();
        const first = document.querySelector('.wt-journal li.fresh'); if (first) first.scrollIntoView({ block: 'center', behavior: 'smooth' });
      } }, 'Import contributions')));
  }
  if (S.importNote) body.append(h('div', { class: 'wt-banner info', role: 'status', 'data-wt-import-note': '' }, h('span', {}, S.importNote),
    h('button', { type: 'button', class: 'wt-btn wt-quiet', onclick: () => { S.importNote = null; S.fresh = new Set(); renderBody(); } }, 'Dismiss')));
  const list = h('ol', {});
  if (!rows.length) list.append(h('li', {}, h('span', { class: 'wt-note' }, linked && ['damaged', 'missing', 'unavailable'].includes(R.status) ? 'No entries are shown while the record cannot be read whole.' : 'Nothing recorded yet.')));
  const fresh = S.fresh || new Set();
  const shown = S.recAll ? rows : rows.filter((e, i) => i < REC_SHOWN || fresh.has(e.id));
  for (const e of shown) {
    const who = `${e.author.name}${e.author.model ? ` (${e.author.model})` : ''}`;
    const via = e.via === 'inbox' ? (e.kind === 'proposal' ? 'proposal · not a decision' : 'claimed') : e.via === 'mc' ? 'Master Craftsman' : '';
    const text = h('div', { class: 'wt-j-text' }, e.text);
    const badges = (e.badges || []).map((b) => h('span', { class: `wt-badge${b.tone === 'warn' ? ' warn' : b.tone === 'ok' ? ' ok' : ''}` }, b.text));
    const li = h('li', { 'data-via': e.via, ...(fresh.has(e.id) ? { class: 'fresh' } : {}) },
      h('div', { class: 'wt-j-head' }, fresh.has(e.id) ? h('span', { class: 'wt-badge ok' }, 'just imported') : null, h('strong', {}, e.kind.replace('_', ' ')), h('span', {}, who), via ? h('span', { class: `wt-badge${e.via === 'inbox' ? ' warn' : ''}`, title: e.via === 'inbox' ? 'Left by an agent through the inbox: claimed, not verified' : '' }, via) : null, badges, h('small', {}, localTimeText(e.at))),
      text);
    if (e.text.length > 170) li.append(h('button', { type: 'button', class: 'wt-btn wt-quiet wt-more-text', 'aria-expanded': 'false', onclick: (ev) => { const on = text.classList.toggle('open'); ev.target.textContent = on ? 'Less' : 'More'; ev.target.setAttribute('aria-expanded', String(on)); } }, 'More'));
    if ((e.refs || []).length) li.append(h('small', { class: 'wt-j-refs' }, (e.refs || []).map((r) => `${r.type}: ${r.ref}${r.quote ? ` · “${r.quote}”` : ''}`).join('  ·  ')));
    list.append(li);
  }
  body.append(list);
  const windowed = linked && R.rows_total > R.rows_shown;
  if (windowed) body.append(h('p', { class: 'wt-note', 'data-wt-record-window': '' }, `Showing the latest ${R.rows_shown} of ${R.rows_total} entries. The older ones are in the journal itself, not on this page.`));
  if (rows.length > REC_SHOWN) body.append(h('button', { type: 'button', class: 'wt-btn wt-quiet', onclick: () => { S.recAll = !S.recAll; S.recOpen = true; renderBody(); } }, S.recAll ? 'Show fewer' : windowed ? `Show all ${rows.length} loaded` : `Show all ${rows.length}`));
  if (linked && own.length) {
    const older = h('details', { class: 'wt-j-add', 'data-wt-own-notes': '' }, h('summary', {}, `Notes kept by this page (${own.length})`));
    const ol = h('ol', {});
    for (const e of own) ol.append(h('li', { 'data-via': e.via }, h('div', { class: 'wt-j-head' }, h('strong', {}, e.kind.replace('_', ' ')), h('span', {}, e.author.name), h('small', {}, localTimeText(e.at))), h('div', {}, e.text)));
    older.append(ol);
    body.append(older);
  }
  const kind = h('select', { 'aria-label': 'Kind of entry' }, JOURNAL_KINDS.map(([v, l]) => h('option', { value: v }, l)));
  const text = h('textarea', { 'aria-label': 'Your entry', placeholder: 'A decision, a question, evidence, an alternative…' });
  body.append(h('details', { class: 'wt-j-add' }, h('summary', {}, linked ? 'Add a note to this page' : 'Add to the record'), kind, text, h('div', { class: 'wt-acts' }, h('button', { type: 'button', class: 'wt-btn', onclick: async () => {
    if (!text.value.trim()) { toast('Write the entry first.'); return; }
    if (await post(`/topics/${S.tid}/journal/add`, { kind: kind.value, text: text.value })) { text.value = ''; S.recOpen = true; await loadJournal(); renderBody(); }
  } }, 'Add entry'))));
  if (linked && R.link) body.append(h('p', { class: 'wt-note' }, h('button', { type: 'button', class: 'wt-btn wt-quiet', onclick: async () => {
    if (await post(`/topics/${S.tid}/record-link`, { clear: true })) { S.recOpen = true; await loadRecord(); renderBody(); }
  } }, 'Unlink the shared record')));
  sec.append(body);
  return sec;
}
// Choose which topic of a workshop's journal this topic follows. Nothing is guessed from titles.
function linkDialog() {
  const workshop = h('input', { type: 'text', 'aria-label': 'Workshop', value: 'workshop-neubau', maxlength: '40', autocomplete: 'off' });
  const res = h('div', {});
  const find = async () => {
    const r = await apiGet(`/topics/${S.tid}/record?candidates=${encodeURIComponent(workshop.value.trim())}`); res.replaceChildren();
    const rows = r.ok ? r.body.candidates : [];
    for (const c of rows) res.append(h('button', { type: 'button', class: 'wt-btn', style: 'display:block;width:100%;text-align:left;margin:6px 0', onclick: async () => {
      if (await post(`/topics/${S.tid}/record-link`, { workshop: workshop.value.trim(), topic: c.topic })) { d.close(); S.recOpen = true; await loadRecord(); renderBody(); }
    } }, h('strong', {}, c.topic), h('br'), h('span', { class: 'wt-note' }, `${c.entries} entr${c.entries === 1 ? 'y' : 'ies'} · last ${localTimeText(c.last_at)}`)));
    if (!rows.length) res.append(h('p', { class: 'wt-note' }, 'No topics were found there. The workshop may not be synced to this server yet.'));
  };
  const d = dialog('Link a shared record', h('p', { class: 'wt-note' }, 'Pick the topic in the Workshop journal that this topic follows. It is read only: this page never writes to it.'),
    h('label', {}, 'Workshop'), workshop, h('div', { class: 'wt-acts' }, h('button', { type: 'button', class: 'wt-btn', onclick: find }, 'Find topics')), res);
  find();
}
function localTimeText(iso) { try { return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }); } catch (e) { return iso; } }

// ── an opened item ──
async function openItem(id, { push = true, rev = null } = {}) {
  const r = await apiGet(`/topics/${S.tid}/items/${id}${rev ? `?rev=${rev}` : ''}`);
  if (!r.ok) { toast((r.body && r.body.message) || 'That item could not be opened.'); return; }
  if (S.openId !== id) S.scrollBoard = window.scrollY;
  S.openId = id; S.open = r.body; S.rev = r.body.item.rev.rev; S.commenting = false; S.editing = false; S.editText = null;
  if (push) window.history.pushState({ wt: id }, '', window.location.href);
  if (effective() === 'chat') setView(wide() && S.pref === 'split' ? 'split' : 'cards', { remember: false }); else applyView();
  window.scrollTo({ top: 0 });
  const t = $('#wt-item-title'); if (t) t.focus({ preventScroll: true });
}
function closeItem({ fromPop = false } = {}) {
  if (dirty()) { toast('You have unsaved changes. Save or Cancel first.'); if (fromPop) window.history.pushState({ wt: S.openId }, '', window.location.href); return; }
  if (!fromPop && window.history.state && window.history.state.wt) { window.history.back(); return; }     // Back and Escape are the same step: the popstate below closes it
  const id = S.openId; S.openId = null; S.open = null; S.commenting = false; S.editing = false; S.editText = null;
  applyView();
  window.scrollTo({ top: S.scrollBoard || 0 });
  const c = id && document.querySelector(`.wt-card[data-id="${id}"] .open`); if (c) c.focus({ preventScroll: true });
}
window.addEventListener('popstate', () => { if (S && S.openId) closeItem({ fromPop: true }); });

function anchorWords(a) {
  if (!a || a.type === 'item') return 'the whole item';
  if (a.type === 'block') return `paragraph ${a.index + 1}${a.quote ? ` (“${a.quote.slice(0, 60)}”)` : ''}`;
  return `a point ${Math.round(a.x * 100)}% across, ${Math.round(a.y * 100)}% down`;
}
function commentNode(c, all) {
  const replies = all.filter((x) => x.reply_to === c.id);
  const n = h('div', { class: 'wt-cmt', 'data-cid': c.id });
  n.append(h('div', {}, h('strong', {}, c.by.name), ` · version ${c.rev} · `, h('button', { type: 'button', class: 'wt-btn wt-quiet', 'data-jump': '', onclick: () => jumpTo(c) }, `on ${anchorWords(c.anchor)} ↗`)),
    h('p', {}, c.text));
  for (const r of replies) n.append(h('div', { class: 'reply' }, h('strong', {}, r.by.name), ' · ', h('span', {}, r.text)));
  if (c.status === 'resolved') n.append(h('span', { class: 'wt-badge ok' }, 'Resolved'));
  if (c.disposition) n.append(' ', h('span', { class: 'wt-badge ok' }, `${DISP_WORDS[c.disposition.value]} · ${c.disposition.by} · version ${c.disposition.rev}`));
  const acts = h('div', { class: 'acts' });
  acts.append(h('button', { type: 'button', class: 'wt-btn', onclick: () => replyDialog(c) }, 'Reply'),
    h('button', { type: 'button', class: 'wt-btn', onclick: async () => { if (await post(`/topics/${S.tid}/items/${S.openId}/comments/${c.id}/resolve`, { resolved: c.status !== 'resolved' })) openItem(S.openId, { push: false, rev: S.rev }); } }, c.status === 'resolved' ? 'Reopen' : 'Resolve'));
  if (!c.disposition) for (const [v, label] of Object.entries(DISP_WORDS)) acts.append(h('button', { type: 'button', class: 'wt-btn', 'data-disp': v, onclick: async () => { if (await post(`/topics/${S.tid}/items/${S.openId}/comments/${c.id}/disposition`, { value: v })) openItem(S.openId, { push: false, rev: S.rev }); } }, label));
  n.append(acts);
  return n;
}
function jumpTo(c) {
  const a = c.anchor; let t = null;
  if (a && a.type === 'block') t = document.querySelector(`.wt-doc [data-block="${a.index}"]`);
  else if (a && a.type === 'point') t = document.querySelector('.wt-img');
  if (t) { t.scrollIntoView({ block: 'center' }); t.classList.add('marked'); setTimeout(() => t.classList.remove('marked'), 1500); }
}
function replyDialog(c) {
  const ta = h('textarea', { 'aria-label': 'Your reply' });
  const d = dialog('Reply', h('p', { class: 'wt-note' }, `To ${c.by.name} on ${anchorWords(c.anchor)}, version ${c.rev}.`), ta,
    h('div', { class: 'wt-acts' }, h('button', { type: 'button', class: 'wt-btn primary', onclick: async () => {
      if (!ta.value.trim()) return;
      if (await post(`/topics/${S.tid}/items/${S.openId}/comments`, { text: ta.value, reply_to: c.id })) { d.close(); openItem(S.openId, { push: false, rev: S.rev }); }
    } }, 'Add reply')));
  ta.focus();
}
function itemView(o) {
  const it = o.item;
  if (it.kind === 'design') {
    const box = h('div', { class: `wt-img${S.commenting ? ' commenting' : ''}`, 'data-wt-image': '' });
    box.append(h('img', { src: api(`/topics/${S.tid}/items/${it.id}/image?rev=${S.rev}`), alt: `${it.title}, version ${S.rev}` }));
    o.comments.filter((c) => c.rev === S.rev && !c.reply_to && c.anchor.type === 'point').forEach((c, i) => {
      box.append(h('button', { type: 'button', class: 'wt-pin', style: `left:${c.anchor.x * 100}%;top:${c.anchor.y * 100}%`, 'aria-label': `Comment ${i + 1}`, onclick: () => $(`.wt-cmt[data-cid="${c.id}"]`)?.scrollIntoView({ block: 'center' }) }, String(i + 1)));
    });
    box.addEventListener('click', (e) => {
      if (!S.commenting || e.target.closest('.wt-pin')) return;
      const r = box.getBoundingClientRect();
      commentForm({ type: 'point', x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)), w: window.innerWidth }, 'this point');
    });
    return box;
  }
  const doc = h('div', { class: `wt-doc${S.commenting ? ' commenting' : ''}`, 'data-wt-doc': '' });
  doc.innerHTML = o.html || '';                         // the server's sanitised Markdown render (each top-level block numbered)
  o.comments.filter((c) => c.rev === S.rev && !c.reply_to && c.anchor.type === 'block').forEach((c, i) => {
    const b = doc.querySelector(`[data-block="${c.anchor.index}"]`);
    if (b) { b.classList.add('marked'); b.append(h('span', { class: 'wt-pin-inline', 'aria-hidden': 'true' }, String(i + 1))); }
  });
  doc.addEventListener('click', (e) => {
    if (!S.commenting) return;
    const b = e.target.closest('[data-block]'); if (!b || !doc.contains(b)) return;
    e.preventDefault();
    commentForm({ type: 'block', index: Number(b.dataset.block), quote: b.textContent.trim().replace(/\s+/g, ' ').slice(0, 120) }, `paragraph ${Number(b.dataset.block) + 1}`);
  });
  return doc;
}
function commentForm(anchor, where) {
  const ta = h('textarea', { 'aria-label': `Comment on ${where}` });
  const form = h('div', { class: 'wt-cmt', 'data-wt-form': '' }, h('label', {}, `Comment on ${where} · version ${S.rev}`), ta,
    h('div', { class: 'wt-acts' }, h('button', { type: 'button', class: 'wt-btn primary', onclick: async () => {
      if (!ta.value.trim()) return;
      if (await post(`/topics/${S.tid}/items/${S.openId}/comments`, { text: ta.value, rev: S.rev, anchor })) { S.commenting = false; openItem(S.openId, { push: false, rev: S.rev }); }
    } }, 'Add comment'), h('button', { type: 'button', class: 'wt-btn', onclick: () => form.remove() }, 'Cancel')));
  const list = $('[data-wt-comments]'); if (list) { list.prepend(form); ta.focus(); }
}
function requestControls(it) {
  const req = it.request; const box = h('div', { class: 'wt-view', style: 'min-height:0;margin-bottom:10px' });
  box.append(h('h3', {}, `Request to ${req.to}`), stepsNode(req), h('p', { class: 'wt-note' }, `Stage ${req.stage_source}: a manual status, not proof that any agent received, started, paused or was reassigned. Queued does not mean received; returned does not mean approved. No agent is connected to report this yet, and changing the recipient or marking it Paused moves or stops nothing.`));
  if (req.included) box.append(h('p', {}, h('strong', {}, 'Included: '), req.included));
  const sel = h('select', { 'aria-label': 'Stage' }, ['queued', 'delivered', 'acknowledged', 'working', 'returned', 'paused'].map((s) => h('option', { value: s, ...(s === req.stage ? { selected: true } : {}) }, s)));
  const who = h('input', { type: 'text', 'aria-label': 'Recipient', value: req.to });
  box.append(h('div', { class: 'wt-acts' }, h('label', {}, 'Stage'), sel, h('label', {}, 'Recipient'), who,
    h('button', { type: 'button', class: 'wt-btn', onclick: async () => { if (await post(`/topics/${S.tid}/items/${it.id}/stage`, { stage: sel.value, recipient: who.value })) { await refreshIndex(); openItem(it.id, { push: false, rev: S.rev }); } } }, 'Save stage and recipient')));
  return box;
}
function readerNode() {
  const o = S.open; const it = o.item; const eff = effective();
  const node = h('section', { class: 'wt-reader', 'aria-labelledby': 'wt-item-title' });
  const revSel = it.revisions.length > 1 ? h('select', { 'aria-label': 'Version', ...(S.editing ? { disabled: true } : {}), onchange: (e) => openItem(it.id, { push: false, rev: e.target.value }) }, it.revisions.map((r) => h('option', { value: r.rev, ...(r.rev === S.rev ? { selected: true } : {}) }, `Version ${r.rev}${r.rev === it.current_rev ? ' (current)' : ''}`))) : null;
  const head = h('div', { class: 'wt-head' }, h('div', {}, h('div', { class: 'wt-eyebrow' }, `${KIND_WORDS[it.kind]} · version ${S.rev} · ${o.item.rev.by}`), h('h2', { id: 'wt-item-title', tabindex: '-1' }, it.title)),
    h('div', { class: 'wt-acts', style: 'margin:0' }, revSel,
      ...(it.kind !== 'design' && !S.editing ? [h('button', { type: 'button', class: 'wt-btn', 'data-wt-edit': '', onclick: startEdit }, 'Edit')] : []),
      h('button', { type: 'button', class: 'wt-btn', 'data-wt-cmode': '', 'aria-pressed': String(S.commenting), onclick: () => { S.commenting = !S.commenting; renderBody(); } }, S.commenting ? 'Commenting: click where' : 'Comment'),
      menuButton('More', () => [...(it.kind === 'design' ? [['Replace the image', () => reviseDialog(it)]] : []), ...(askOff() ? [] : [[mcSay('Ask Master Craftsman about this'), () => askAbout(it.id)]]), ['Put away', () => archive(it.id, true)]], 'wt-btn')));
  node.append(h('nav', { class: 'wt-crumb', 'aria-label': 'Where you are' },
    h('button', { type: 'button', class: 'wt-btn', 'data-wt-back': '', onclick: () => closeItem() }, '‹ Back to cards'),
    h('span', { class: 'wt-note' }, `${S.topic ? S.topic.title : 'Topic'} › ${it.title}`)), head);
  const grid = h('div', { class: 'wt-grid' });
  const main = h('div', {});
  if (it.request) main.append(requestControls(it));
  if (S.editing && it.kind !== 'design') main.append(editorNode(it));
  else { const view = h('div', { class: 'wt-view' }); view.append(itemView(o)); main.append(view); }
  const cm = o.comments; const here = cm.filter((c) => c.rev === S.rev && !c.reply_to);
  const rail = h('aside', { class: 'wt-rail', 'aria-label': 'Comments and questions' });
  const secC = h('section', { 'aria-labelledby': 'wt-c-h' }, h('h3', { id: 'wt-c-h' }, `💬 Comments on version ${S.rev}`), h('p', { class: 'wt-note' }, mcSay('Seen by everyone on this topic and tied to this version. This is not a chat with Master Craftsman.')));
  const list = h('div', { 'data-wt-comments': '' });
  if (!here.length) list.append(h('p', { class: 'wt-note' }, 'No comments on this version yet. Turn on Comment and click where, or add one on the whole item below.'));
  here.forEach((c) => list.append(commentNode(c, cm)));
  secC.append(list, h('div', { class: 'wt-acts' }, h('button', { type: 'button', class: 'wt-btn', onclick: () => commentForm({ type: 'item' }, 'the whole item') }, 'Comment on the whole item')));
  const secA = h('section', { class: 'ask', 'aria-labelledby': 'wt-a-h' }, h('h3', { id: 'wt-a-h' }, mcSay('🛠 Ask Master Craftsman')),
    askOff() ? '' : h('p', { class: 'wt-note' }, 'Opens the topic’s conversation with this item attached as context. Nothing is sent until you press Send there.'),
    askOff() ? h('p', { class: 'wt-note', 'data-wt-ask-off': '' }, askOffWhy())
      : h('button', { type: 'button', class: 'wt-btn primary', 'data-wt-ask': '', onclick: () => askAbout(it.id) }, mcSay('Ask Master Craftsman about this ›')));
  rail.append(secC, secA);
  grid.append(main, rail);
  node.append(grid);
  const bottom = h('div', { class: 'wt-bottom' }, h('button', { type: 'button', class: 'wt-btn', onclick: () => { S.commenting = !S.commenting; renderBody(); } }, 'Comment'), ...(askOff() ? [] : [h('button', { type: 'button', class: 'wt-btn primary', onclick: () => askAbout(it.id) }, mcSay('Ask Master Craftsman ›'))]));
  if (eff !== 'chat') node.append(bottom);
  return node;
}
function reviseDialog(it) {                      // only for a design: choose a new image. Text is edited in the page itself (startEdit)
  const f = h('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp,image/gif', 'aria-label': 'New image' }); const note = h('input', { type: 'text', 'aria-label': 'Note (optional)' });
  const d = dialog('Replace the image', h('p', { class: 'wt-note' }, 'The earlier image is kept; choose it again in the Version menu.'), h('label', {}, 'Image'), f, h('label', {}, 'Note (optional)'), note, h('div', { class: 'wt-acts' }, h('button', { type: 'button', class: 'wt-btn primary', onclick: async () => {
    if (!f.files[0]) { toast('Choose an image first.'); return; }
    if (await upload(`/topics/${S.tid}/items/${it.id}/revisions/design`, f.files[0], { note: note.value })) { d.close(); await refreshIndex(); openItem(it.id, { push: false }); }
  } }, 'Save')));
}
// Editing a note, document or request happens in the page, in a large box, not in a small window. Saving keeps the earlier text as an
// earlier version automatically (the Version menu); nobody has to know what a revision is or write what changed.
function dirty() { return !!(S.editing && S.open && S.editText != null && S.editText !== S.open.text); }
function startEdit() { S.editing = true; S.editText = S.open.text; renderBody(); const t = $('[data-wt-editor]'); if (t) t.focus(); }
function cancelEdit() { S.editing = false; S.editText = null; renderBody(); }
async function saveEdit(note) {
  const it = S.open.item;
  if (!S.editText || !S.editText.trim()) { toast('There is nothing to save.'); return; }
  if (S.editText === S.open.text) { toast('Nothing changed.'); return; }
  if (await post(`/topics/${S.tid}/items/${it.id}/revisions`, { text: S.editText, note: note || '' })) {
    S.editing = false; S.editText = null;
    await refreshIndex(); await openItem(it.id, { push: false });
    toast('Saved. The earlier version is kept: choose it in the Version menu.');
  }
}
function editorNode(it) {
  const ta = h('textarea', { 'aria-label': `Edit: ${it.title}`, 'data-wt-editor': '', spellcheck: 'true' });
  ta.value = S.editText == null ? S.open.text : S.editText;
  ta.addEventListener('input', () => { S.editText = ta.value; });
  const note = h('input', { type: 'text', 'aria-label': 'Note (optional)', placeholder: 'Note (optional)', maxlength: '300' });
  ta.addEventListener('keydown', (e) => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); saveEdit(note.value); } });
  return h('div', { class: 'wt-editor' },
    h('div', { class: 'wt-note' }, S.rev !== S.open.item.current_rev ? `You are editing from version ${S.rev}. Saving makes a new current version.` : 'You are editing the current version. Saving keeps the earlier one.'),
    ta, h('div', { class: 'wt-acts' }, note,
      h('button', { type: 'button', class: 'wt-btn primary', 'data-wt-save': '', onclick: () => saveEdit(note.value) }, 'Save'),
      h('button', { type: 'button', class: 'wt-btn', 'data-wt-cancel': '', onclick: cancelEdit }, 'Cancel')));
}
// ── Ask Master Craftsman about this ──
function askAbout(id) {
  if (askOff()) { toast(askOffWhy()); return; }
  const it = itemOf(id); if (!it) return;
  const rev = S.openId === id ? S.rev : it.current_rev;
  window.guildTopicContext = [{ topic_id: S.tid, item_id: id, rev }];
  showContextChip(`${KIND_WORDS[it.kind]}: ${it.title} · version ${rev}`, id);
  if (effective() !== 'split') setView('chat', { remember: false });
  const input = $('[data-mc-input]'); if (input) input.focus({ preventScroll: true });
  announce(`${mcName()} will see ${it.title} with your next message. Nothing has been sent.`);
}
function showContextChip(text, id) {
  let chip = $('[data-wt-ctx]');
  const form = $('[data-mc-composer]'); if (!form) return;
  if (!chip) { chip = h('div', { class: 'wt-ctx', 'data-wt-ctx': '', role: 'status' }); form.before(chip); }
  chip.replaceChildren(h('span', {}, '📄 About: ', h('strong', { 'data-wt-ctx-title': '' }, text), ' ', h('span', { class: 'wt-sim' }, 'for your next message')),
    h('span', { class: 'wt-acts', style: 'margin:0' }, h('button', { type: 'button', class: 'wt-btn wt-quiet', onclick: () => { if (effective() === 'chat') setView('cards', { remember: false }); openItem(id); } }, 'Back to the item'),
      h('button', { type: 'button', class: 'wt-btn wt-quiet', 'aria-label': 'Remove the item from this question', onclick: () => { window.guildTopicContext = []; chip.remove(); } }, '✕')));
  chip.hidden = false;
}
document.addEventListener('guild:topic-context-sent', () => { const c = $('[data-wt-ctx]'); if (c) c.remove(); toast('Sent with your message. The item is not attached to later ones.'); });
document.addEventListener('guild:topic-context-restored', () => { const c = (window.guildTopicContext || [])[0]; const it = c && itemOf(c.item_id); if (it) showContextChip(`${KIND_WORDS[it.kind]}: ${it.title} · version ${c.rev || it.current_rev}`, it.id); });

// ── dialogs: new items, previous, topics ──
function newDialog(kind) {
  const title = h('input', { type: 'text', 'aria-label': 'Title', maxlength: '120' });
  const body = [h('label', {}, 'Title'), title];
  let text, file, to, included;
  if (kind === 'design') { file = h('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp,image/gif', 'aria-label': 'Image' }); body.push(h('label', {}, 'Image (JPEG, PNG, WebP or GIF; metadata is removed)'), file); }
  else {
    text = h('textarea', { 'aria-label': kind === 'note' ? 'Your thought' : 'Text' }); body.push(h('label', {}, kind === 'note' ? 'Your thought' : kind === 'request' ? 'What you are asking' : 'Text (Markdown)'), text);
    if (kind === 'document') { file = h('input', { type: 'file', accept: '.md,.markdown,.txt,text/plain,text/markdown', 'aria-label': 'Or choose a text file' }); body.push(h('label', {}, 'Or choose a .md or .txt file'), file); file.addEventListener('change', async () => { if (file.files[0]) { text.value = await file.files[0].text(); if (!title.value) title.value = file.files[0].name.replace(/\.[^.]+$/, ''); } }); }
  }
  if (kind === 'request') { to = h('input', { type: 'text', 'aria-label': 'To', placeholder: 'Codex, Claude Code, Grok CLI…' }); included = h('input', { type: 'text', 'aria-label': 'What is included' }); body.push(h('label', {}, 'To'), to, h('label', {}, 'What is included (version, files)'), included, h('p', { class: 'wt-note' }, 'This records the request. No agent is connected, so nothing is delivered; you set the stage by hand.')); }
  const d = dialog(`New ${KIND_WORDS[kind].toLowerCase()}`, ...body, h('div', { class: 'wt-acts' }, h('button', { type: 'button', class: 'wt-btn primary', onclick: async () => {
    let r;
    if (kind === 'design') { if (!file.files[0]) { toast('Choose an image first.'); return; } r = await upload(`/topics/${S.tid}/items/design`, file.files[0], { title: title.value }); }
    else {
      const t = (title.value || (text.value.trim().split('\n')[0] || '')).slice(0, 120);
      if (!t.trim() || (kind !== 'request' && !text.value.trim())) { toast('Add a title and some text first.'); return; }
      r = await post(`/topics/${S.tid}/items`, { kind, title: t, text: text.value, ...(kind === 'request' ? { request: { to: to.value, included: included.value } } : {}) });
    }
    if (r) { d.close(); await refreshIndex(); renderBody(); toast('Added.'); }
  } }, 'Add')));
  title.focus();
}
function previousDialog() {
  const list = h('div', {});
  const gone = orderedItems(true);
  if (!gone.length) list.append(h('p', { class: 'wt-note' }, 'Nothing has been put away. Putting away is not deleting.'));
  for (const it of gone) list.append(h('div', { class: 'wt-cmt' }, h('strong', {}, it.title), ` · ${KIND_WORDS[it.kind]} · version ${it.current_rev}`, h('div', { class: 'wt-acts' }, h('button', { type: 'button', class: 'wt-btn', onclick: async () => { await archive(it.id, false); d.close(); } }, 'Bring back'))));
  const d = dialog('Previous', h('p', { class: 'wt-note' }, 'Earlier work for this topic. Bringing an item back does not replace anything.'), list);
}
async function topicsDialog() {
  const q = h('input', { type: 'search', 'aria-label': 'Search topics by words', placeholder: 'For example: files, private, chat…', autocomplete: 'off' });
  const res = h('div', {});
  const paint = async () => {
    const r = await apiGet(`/topics?q=${encodeURIComponent(q.value)}`); res.replaceChildren();
    const rows = r.ok ? r.body.topics : [];
    for (const t of rows) res.append(h('button', { type: 'button', class: 'wt-btn', style: 'display:block;width:100%;text-align:left;margin:6px 0', onclick: () => { window.location.href = `${window.location.pathname}?topic=${t.id}`; } }, h('strong', {}, t.title), h('br'), h('span', { class: 'wt-note' }, `${t.cards} cards · ${t.waiting} waiting`)));
    if (!rows.length) res.append(h('p', { class: 'wt-note' }, q.value ? 'No topic matches those words.' : 'No topics yet.'));
  };
  q.addEventListener('input', paint);
  const title = h('input', { type: 'text', 'aria-label': 'Name for a new topic', maxlength: '120' });
  dialog('Find or start a topic', h('label', {}, 'Search by words'), q, res, h('label', {}, 'Start a new topic (no spec or issue number needed)'), title,
    h('div', { class: 'wt-acts' }, h('button', { type: 'button', class: 'wt-btn primary', onclick: async () => {
      if (!title.value.trim()) { toast('Give the topic a name first.'); return; }
      const r = await post('/topics/create', { title: title.value });
      if (r) window.location.href = `${window.location.pathname}?topic=${r.topic.id}`;
    } }, 'Start topic')));
  paint();
}

// ── body ──
function renderBody() {
  const body = $('[data-wt-body]'); if (!body) return;
  renderTools();
  const scroll = body.scrollTop;
  body.replaceChildren();
  if (!S.tid) { body.append(startNode()); return; }
  if (!S.topic) { body.append(h('p', { class: 'wt-note' }, S.error || 'Loading the topic…')); return; }
  if (effective() === 'chat') { body.append(h('p', { class: 'wt-note' }, mcSay('The Master Craftsman conversation for this topic is shown. Choose Cards to see the topic’s items.'))); return; }
  if (S.openId && S.open) body.append(readerNode()); else { body.append(boardNode(), recordNode()); }
  body.scrollTop = scroll;
  if (S.openId && S.open) { const itemTop = $('.wt-reader'); if (itemTop && document.activeElement === document.body) { /* keep focus where it is */ } }
}
function startNode() {
  const box = h('div', { class: 'wt-empty' }, h('h2', {}, 'Start a topic'), h('p', { class: 'wt-note' }, 'A topic is a place to gather design, documents, discussion and handoffs about one subject. It does not need a spec or issue number.'));
  const title = h('input', { type: 'text', 'aria-label': 'Name for the topic', maxlength: '120', placeholder: 'Guild chat improvements' });
  box.append(title, h('button', { type: 'button', class: 'wt-btn primary', onclick: async () => {
    if (!title.value.trim()) { toast('Give the topic a name first.'); return; }
    const r = await post('/topics/create', { title: title.value });
    if (r) window.location.href = `${window.location.pathname}?topic=${r.topic.id}`;
  } }, 'Start topic'), h('button', { type: 'button', class: 'wt-btn', onclick: topicsDialog }, 'Find an existing topic'));
  return box;
}

document.addEventListener('keydown', (e) => {
  if (!S || e.key !== 'Escape' || !S.openId || S.editing || document.querySelector('dialog[open]') || document.querySelector('.wt-menu')) return;
  if (document.activeElement && document.activeElement.matches('textarea,input,select')) return;
  closeItem();
});

export function initWorkshopTopics(p) {
  page = p;
  if (page.page !== 'workshop') return;
  root = $('[data-wt]');
  S = { tid: page.topic_id || null, topic: null, items: [], layout: { order: [], wide: [], last_view: null }, journal: [], record: null, convNote: null, inbox: { waiting: {}, total: 0 }, recOpen: false, recAll: false, view: 'cards', pref: 'cards', last: 'cards', openId: null, open: null, rev: null, commenting: false, error: null };
  document.body.dataset.wtView = 'cards';
  setupDivider();
  window.guildTopicContext = [];
  window.matchMedia(`(min-width:${SPLIT_MIN}px)`).addEventListener('change', () => { if (S.tid && S.topic) applyView(); });
  if (!S.tid) { S.view = 'cards'; applyView(); return; }
  load();
}
