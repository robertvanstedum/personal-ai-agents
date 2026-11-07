// Actions: CoS's working table (Review 06, Option A, slice 3). One row per thing in motion, from the CoS store through
// the CoS app (GET /ui/api/in-flight, POST /ui/api/in-flight/<op>); nothing here reaches the store directly, and nothing
// is shown that the store didn't give. Columns: P (click to change), Item, Kind, With, Status, Moved, Latest comment.
// Sorted by priority, then last moved; a header sorts by its column. The last row adds one. Views: Open, Mine, Waiting.
// A row opens on the right with its thread. Done moves it to Earlier (closed rows, folded by week). On a phone each row is
// a card. Download for Excel saves what's shown as a .csv (UTF-8, which Excel opens).
const PANEL = document.getElementById('tab-flight');
const BASE = location.pathname.replace(/\/ui(\/[^/]*)?\/?$/, '') + '/ui/';
const API = BASE + 'api/in-flight';
const PHONE = window.matchMedia('(max-width: 639px)');
const KIND = { action: 'Action', briefing: 'Briefing', 'follow-up': 'Follow-up', research: 'Research', decision: 'Decision', risk: 'Risk' };
const WITH = { robert: 'you', cos: 'CoS', mc: 'MC' };
const WHO = { robert: 'You', cos: 'CoS', mc: 'MC' };
const STATUS = { open: 'Open', doing: 'Working', waiting: 'Waiting', done: 'Completed' };
const COLUMNS = [['priority', 'Priority'], ['title', 'Item'], ['kind', 'Type'], ['with', 'Assigned to'], ['status', 'Status'], ['moved', 'Updated'], ['comment', 'Latest comment']];

const state = { loaded: false, data: null, view: 'all', type: '', priority: '', newType: 'action', q: '', sort: 'priority', dir: 1, open: null, busy: false, draft: '' };
const $ = (sel, root = PANEL) => root.querySelector(sel);

function h(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === 'class') n.className = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) n.append(kid);
  return n;
}

function opId() {
  const b = new Uint8Array(12);
  crypto.getRandomValues(b);
  return 'op-' + [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
}

function writeHeaders() {
  const token = document.querySelector('meta[name="minimoi-csrf-token"]')?.content;
  return { 'Content-Type': 'application/json', Accept: 'application/json', ...(token ? { 'X-CSRF-Token': token } : {}) };
}

// ── time words ─────────────────────────────────────────────────────────────
function day(iso) { const d = new Date(iso); return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }
function moved(iso) {
  if (!iso) return '';
  const today = day(new Date().toISOString());
  const diff = Math.round((today - day(iso)) / 86400000);
  if (diff <= 0) return 'today';
  if (diff === 1) return 'yesterday';
  if (diff < 7) return new Date(iso).toLocaleDateString([], { weekday: 'short' });
  return new Date(iso).toLocaleDateString([], { day: 'numeric', month: 'short' });
}
const since = (iso) => new Date(iso).toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' });

// ── reading and writing ────────────────────────────────────────────────────
async function load() {
  let body = null;
  try {
    const r = await fetch(API, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
    body = await r.json().catch(() => null);
  } catch (e) { body = null; }
  state.loaded = true;
  state.data = body && typeof body.state === 'string' ? body
    : { state: 'unavailable', message: "Actions couldn't be read just now. Nothing is lost; try again." };
  if (state.open && !rowById(state.open)) state.open = null;
  render();
}

async function send(op, fields) {
  const body = { op_id: opId(), at: new Date().toISOString(), ...fields };
  let answer = null;
  for (let tries = 0; tries < 2 && !answer; tries++) {        // the same op_id and time: a retry replays, never writes twice
    try {
      const r = await fetch(`${API}/${op}`, { method: 'POST', credentials: 'same-origin', headers: writeHeaders(), body: JSON.stringify(body) });
      answer = await r.json().catch(() => ({ outcome: 'failed', message: "The answer couldn't be read. Nothing is claimed; reload to check." }));
    } catch (e) { answer = tries ? { outcome: 'failed', message: "Actions couldn't be reached. Nothing is claimed; try again." } : null; }
  }
  if (answer.outcome !== 'accepted') say(answer.message || "That change wasn't saved.", 'warn');
  else say('');
  await load();
  return answer;
}

function say(text, kind = 'ok') {
  const line = $('[data-fl-say]');
  line.textContent = text || '';
  line.dataset.kind = kind;
  line.hidden = !text;
}

// ── what's shown ───────────────────────────────────────────────────────────
const openRows = () => (state.data && state.data.state === 'ok' ? state.data.open : []);
const earlierRows = () => (state.data && state.data.state === 'ok' ? state.data.earlier.flatMap((w) => w.rows) : []);
const rowById = (id) => openRows().concat(earlierRows()).find((r) => r.item_id === id);

function shown() {
  let rows = ['completed', 'cancelled'].includes(state.view) ? earlierRows().filter(r => r.outcome === (state.view === 'completed' ? 'done' : 'dropped')) : state.view === 'all' ? openRows().concat(earlierRows()) : openRows();
  if (state.view === 'doing') rows = rows.filter(r => r.status === 'doing');
  if (state.view === 'mine') rows = rows.filter((r) => r.with === 'cos');
  if (state.view === 'waiting') rows = rows.filter((r) => r.status === 'waiting');
  if (state.view === 'open') rows = rows.filter(r => r.status === 'open');
  if (state.type) rows = rows.filter(r => r.kind === state.type);
  if (state.priority) rows = rows.filter(r => r.priority === Number(state.priority));
  if (state.q) {
    const q = state.q.toLowerCase();
    rows = rows.filter((r) => `${r.title} ${KIND[r.kind]} ${WITH[r.with]} ${r.comments.map((c) => c.text).join(' ')}`.toLowerCase().includes(q));
  }
  const key = {
    priority: (r) => [r.priority, -Date.parse(r.last_moved_at)], title: (r) => [r.title.toLowerCase()], kind: (r) => [r.kind],
    with: (r) => [r.with], status: (r) => [r.closed_at ? (r.outcome === 'dropped' ? 4 : 3) : ['open', 'doing', 'waiting', 'done'].indexOf(r.status)],
    moved: (r) => [-Date.parse(r.last_moved_at)], comment: (r) => [-(r.latest_comment ? Date.parse(r.latest_comment.at) : 0)],
  }[state.sort];
  return [...rows].sort((a, b) => {
    const x = key(a), y = key(b);
    for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return (x[i] < y[i] ? -1 : 1) * (i === 0 ? state.dir : 1);
    return 0;
  });
}

// ── controls ───────────────────────────────────────────────────────────────
function priorityChip(row, closed = row.closed_at != null) {
  return h('button', { type: 'button', class: `fl-pri p${row.priority}`, 'data-fl-priority': row.item_id, disabled: closed,
    title: closed ? `Priority ${row.priority}` : `Priority ${row.priority}: click to change`, 'aria-label': `Priority ${row.priority}${closed ? '' : ', click to change'}`,
    onclick: (e) => { e.stopPropagation(); send('update', { item_id: row.item_id, priority: row.priority % 3 + 1 }); } }, `P${row.priority}`);
}

function statusSelect(row, closed = row.closed_at != null) {
  if (closed) return h('span', { class: 'fl-outcome' }, row.outcome === 'dropped' ? 'Cancelled' : 'Completed');
  const sel = h('select', { class: `fl-st ${row.status}`, 'data-fl-status': row.item_id, 'aria-label': `Status of ${row.title}`, disabled: closed,
    onclick: (e) => e.stopPropagation(),
    onchange: (e) => {
      const to = e.target.value;
      if (to === 'done') send('close', { item_id: row.item_id, outcome: 'done' });
      else if (to === 'dropped') send('close', { item_id: row.item_id, outcome: 'dropped' });    // as "Cancel work" does
      else send('update', { item_id: row.item_id, status: to });
    } },
  // Cancelled sits in the list too, not only behind the row's "Cancel work" (Robert, 7 Nov: no cancel in the dropdown).
  ...Object.entries({ ...STATUS, dropped: 'Cancelled' }).map(([v, label]) => h('option', { value: v, selected: v === row.status }, label)));
  return sel;
}

function typeSelect(row, closed) {
  return h('select', {'aria-label':'Record type', disabled:closed, onchange:e=>send('update',{item_id:row.item_id,kind:e.target.value})},
    ...Object.entries(KIND).map(([value,label])=>h('option',{value,selected:value===row.kind},label)));
}

function latest(row) {
  const c = row.latest_comment;
  return c ? `${WHO[c.author]}: ${c.text}` : '';
}

function newRowInput() {
  const input = h('input', { type: 'text', class: 'fl-new-input', 'data-fl-new': '', maxlength: '200', autocomplete: 'off',
    placeholder: 'Add a record for CoS (press Enter)', 'aria-label': 'Add work for CoS: type what it is and press Enter' });
  input.value = state.draft;
  input.addEventListener('input', () => { state.draft = input.value; });
  input.addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter' || e.isComposing || !input.value.trim() || state.busy) return;
    e.preventDefault();
    state.busy = true;
    const answer = await send('open', { title: input.value.trim(), priority: 2, status: 'open', kind: state.newType, with: 'cos' });
    state.busy = false;
    if (answer.outcome === 'accepted') { state.draft = ''; render(); const again = $('[data-fl-new]'); if (again) again.focus(); }
  });
  const type = h('select', { 'aria-label': 'Type of new record', onchange: e => {state.newType=e.target.value;} }, ...Object.entries(KIND).map(([value,label]) => h('option', {value, selected:value===state.newType}, label)));
  return h('div', {class:'fl-add-record'}, type, input);
}

function table(rows) {
  const head = h('tr', {}, ...COLUMNS.map(([key, label]) => h('th', { scope: 'col', 'aria-sort': state.sort === key ? (state.dir > 0 ? 'ascending' : 'descending') : false },
    h('button', { type: 'button', 'data-fl-sort': key, onclick: () => { state.dir = state.sort === key ? -state.dir : 1; state.sort = key; render(); } },
      label, state.sort === key ? (state.dir > 0 ? ' ▾' : ' ▴') : ''))));
  const body = rows.map((r) => h('tr', { 'data-fl-row': r.item_id, class: state.open === r.item_id ? 'sel' : '', tabindex: '0',
      onclick: () => openRow(r.item_id), onkeydown: (e) => { if (e.key === 'Enter' && e.target === e.currentTarget) openRow(r.item_id); } },
    h('td', {}, priorityChip(r)), h('td', { class: 'ti' }, r.title), h('td', { class: 'mu' }, KIND[r.kind]), h('td', { class: 'mu' }, WITH[r.with]),
    h('td', {}, statusSelect(r)), h('td', { class: 'mu' }, moved(r.last_moved_at)),
    h('td', { class: 'cm' }, latest(r) || h('span', { class: 'add' }, '＋ comment'))));
  const add = h('tr', { class: 'fl-newrow' }, h('td'), h('td', { colspan: '6' }, newRowInput()));
  return h('table', { class: 'fl-table', 'data-fl-table': '' }, h('thead', {}, head), h('tbody', {}, ...body, ...(!rows.length ? [h('tr', {}, h('td', { colspan: '7', class: 'fl-empty' }, 'No records match this view.'))] : []), ...(['completed','cancelled'].includes(state.view) ? [] : [add])));
}

function cards(rows) {
  return h('div', { class: 'fl-cards', 'data-fl-cards': '' },
    ...rows.map((r) => h('div', { class: `fl-card${state.open === r.item_id ? ' sel' : ''}`, 'data-fl-row': r.item_id, tabindex: '0', role: 'button',
        onclick: () => openRow(r.item_id), onkeydown: (e) => { if (e.key === 'Enter' && e.target === e.currentTarget) openRow(r.item_id); } },
      h('div', { class: 'fl-card-top' }, priorityChip(r), statusSelect(r), h('span', { class: 'fl-card-kind' }, `${KIND[r.kind]} · ${WITH[r.with]}`)),
      h('div', { class: 'fl-card-ti' }, r.title),
      h('div', { class: `fl-card-cm${r.latest_comment ? '' : ' add'}` }, '💬 ', latest(r) || 'Add a comment'))),
    ...(!rows.length ? [h('p', {class:'fl-empty'}, 'No records match this view.')] : []),
    ...(['completed','cancelled'].includes(state.view) ? [] : [h('div', { class: 'fl-card-new' }, newRowInput())]));
}

// ── a row, opened on the right ─────────────────────────────────────────────
function openRow(id) { state.open = id; render(); const back = $('[data-fl-close]'); if (back) back.focus({ preventScroll: true }); }
function closeRow() {
  const id = state.open;
  state.open = null;
  render();
  const row = id && PANEL.querySelector(`[data-fl-row="${CSS.escape(id)}"]`);
  if (row) row.focus({ preventScroll: true });
}

function sidePanel() {
  const r = rowById(state.open);
  const aside = $('[data-fl-panel]');
  aside.hidden = !r;
  PANEL.toggleAttribute('data-fl-open', !!r);
  if (!r) { aside.replaceChildren(); return; }
  const closed = r.closed_at != null;
  const withSel = h('select', { class: 'fl-with', 'data-fl-with': r.item_id, disabled: closed, 'aria-label': 'With',
    onchange: (e) => send('update', { item_id: r.item_id, with: e.target.value }) },
  ...Object.entries(WITH).map(([v, label]) => h('option', { value: v, selected: v === r.with }, `with ${label}`)));
  const box = h('textarea', { class: 'fl-comment-box', 'data-fl-comment': '', rows: '3', maxlength: '4000', placeholder: 'Add a comment…', 'aria-label': 'Add a comment' });
  const addComment = async () => {
    const text = box.value.trim();
    if (!text || state.busy) return;
    state.busy = true;
    const answer = await send('comment', { item_id: r.item_id, text });
    state.busy = false;
    if (answer.outcome !== 'accepted') { const again = $('[data-fl-comment]'); if (again) again.value = text; }
    else { const again = $('[data-fl-comment]'); if (again) again.focus(); }
  };
  box.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); addComment(); } });
  aside.replaceChildren(
    h('div', { class: 'fl-ph' },
      h('button', { type: 'button', class: 'fl-back', 'data-fl-close': '', onclick: closeRow }, '← Actions'),
      h('span', { class: 'fl-ph-t' }, `P${r.priority} · ${KIND[r.kind]} · with ${WITH[r.with]}`),
      h('button', { type: 'button', class: 'fl-x', 'data-fl-close': '', 'aria-label': 'Close', onclick: closeRow }, '✕')),
    h('div', { class: 'fl-doc' },
      h('h3', { class: 'fl-doc-ti' }, r.title),
      h('p', { class: 'fl-since' }, `${KIND[r.kind]} · since ${since(r.opened_at)}${closed ? ` · ${r.outcome === 'dropped' ? 'cancelled' : 'done'} ${moved(r.closed_at)}` : ''}`),
      h('p', { class: 'fl-ctl' }, typeSelect(r, closed), statusSelect(r, closed), priorityChip(r, closed), withSel),
      h('p', { class: 'fl-h' }, 'Comments'),
      h('div', { class: 'fl-thread', 'data-fl-thread': '' }, ...(r.comments.length ? r.comments.map((c) =>
        h('p', { class: 'fl-trail' }, h('span', {}, moved(c.at)), h('b', {}, WHO[c.author]), ': ', c.text))
        : [h('p', { class: 'fl-quiet' }, 'No comments yet.')]),
        r.close_note ? h('p', { class: 'fl-trail' }, h('span', {}, moved(r.closed_at)), h('b', {}, 'Closed'), ': ', r.close_note) : null),
      box,
      h('p', { class: 'fl-acts' },
        h('button', { type: 'button', class: 'fl-btn', 'data-fl-add-comment': '', onclick: addComment }, 'Add comment'),
        closed ? null : h('button', { type: 'button', class: 'fl-btn', 'data-fl-cancel': r.item_id, onclick: () => send('close', { item_id: r.item_id, outcome: 'dropped' }) }, 'Cancel work'),
        closed ? null : h('button', { type: 'button', class: 'fl-btn fl-done', 'data-fl-done': r.item_id, onclick: () => send('close', { item_id: r.item_id, outcome: 'done' }) }, 'Done'),
        h('button', { type: 'button', class: 'fl-btn', disabled: true, title: 'later' }, 'Back to the Board'),
        h('button', { type: 'button', class: 'fl-btn', disabled: true, title: 'later' }, 'Talk it over'))));
}

// ── the page ───────────────────────────────────────────────────────────────
function csv() {
  const cell = (v) => `"${String(/^[=+@\-\t\r\n]/.test(String(v ?? '')) ? "'" + v : v ?? '').replace(/"/g, '""')}"`;
  const lines = [['Priority', 'Item', 'Type', 'Assigned to', 'Status', 'Updated', 'Latest comment'].map(cell).join(',')];
  for (const r of shown()) lines.push([`P${r.priority}`, r.title, KIND[r.kind], WITH[r.with], r.closed_at ? (r.outcome === 'dropped' ? 'Cancelled' : 'Completed') : STATUS[r.status], r.last_moved_at.slice(0, 10), latest(r)].map(cell).join(','));
  const blob = new Blob(['﻿' + lines.join('\r\n') + '\r\n'], { type: 'text/csv;charset=utf-8' });
  const a = h('a', { href: URL.createObjectURL(blob), download: `actions-${new Date().toISOString().slice(0, 10)}.csv` });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function render() {
  const keep = document.activeElement && PANEL.contains(document.activeElement) && document.activeElement.matches('[data-fl-new], [data-fl-comment]')
    ? { sel: document.activeElement.matches('[data-fl-new]') ? '[data-fl-new]' : '[data-fl-comment]', value: document.activeElement.value } : null;
  const ok = state.data && state.data.state === 'ok';
  const note = $('[data-fl-state]');
  note.hidden = ok || !state.loaded;
  const outline = $('[data-fl-outline]');
  outline.hidden = !!ok;
  outline.querySelector('.underway-badge').textContent = state.loaded && state.data.state === 'not_configured' ? 'In construction' : 'Status unavailable';
  outline.querySelector('.underway-limit').textContent = !state.loaded ? 'Checking the work record…' : state.data.state === 'not_configured' ? 'Tracking is not connected here yet. No work is being shown or saved on this page.' : 'The work record could not be read. Existing work may still be there; check again before making changes.';
  note.textContent = ''; note.hidden = true;
  PANEL.dataset.flMode = state.loaded ? state.data.state : 'loading';
  for (const b of PANEL.querySelectorAll('[data-fl-view]')) {
    b.setAttribute('aria-pressed', String(b.dataset.flView === state.view));
    const v = b.dataset.flView;
    const labels = {all:'All',open:'Open',doing:'Working',waiting:'Waiting',completed:'Completed',cancelled:'Cancelled'};
    const count = v === 'all' ? openRows().length+earlierRows().length : ['completed','cancelled'].includes(v) ? earlierRows().filter(r => r.outcome === (v === 'completed' ? 'done' : 'dropped')).length : openRows().filter(r => r.status === v).length;
    b.textContent = labels[v] + (ok ? ` (${count})` : '');
  }
  const rows = ok ? shown() : [];
  const empty = h('div', {class:'fl-empty-table'}, h('div', {class:'fl-empty-head'}, ...COLUMNS.map(c => h('span', {}, c[1]))), h('p', {class:'fl-empty'}, 'The record is unavailable. No action counts are claimed.'));
  $('[data-fl-body]').replaceChildren(ok ? (PHONE.matches ? cards(rows) : table(rows)) : empty);
  $('[data-fl-download]').disabled = !ok;
  $('[data-fl-search]').disabled = !ok;
  $('[data-fl-weeks]').replaceChildren();
  sidePanel();
  if (keep) { const f = $(keep.sel); if (f) { if (keep.sel === '[data-fl-comment]') f.value = keep.value; f.focus(); } }
}

function bind() {
  $('[data-fl-retry]').addEventListener('click', load);
  for (const b of PANEL.querySelectorAll('[data-fl-view]')) b.addEventListener('click', () => { state.view = b.dataset.flView; state.open = null; render(); });
  $('[data-fl-search]').addEventListener('input', (e) => { state.q = e.target.value.trim(); render(); });
  $('[data-fl-type]').addEventListener('change', e => {state.type=e.target.value; render();});
  $('[data-fl-priority-filter]').addEventListener('change', e => {state.priority=e.target.value; render();});
  $('[data-fl-download]').addEventListener('click', csv);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && state.open && PANEL.classList.contains('active') && !e.target.closest('textarea, input, select')) closeRow();
  });
  PHONE.addEventListener('change', () => { if (state.loaded) render(); });
}

// The tab itself: the page's own activateTab shows the panel; here the body learns In flight is showing (no page art, no
// conversation list; on a phone the bar says "In flight", from founders.css). The table reads afresh each time the tab
// opens, and when a chat reply lands while it shows: CoS writes records from the chat, and they showed only after a
// hard refresh (Robert, 7 Nov).
let visible = false;
function shownNow() {
  const on = PANEL.classList.contains('active');
  document.body.classList.toggle('show-cos-flight', on);
  if (on && !visible) load();
  visible = on;
}
bind();
new MutationObserver(shownNow).observe(PANEL, { attributes: true, attributeFilter: ['class'] });
document.addEventListener('cos:chat-rendered', () => { if (visible) load(); });
if (typeof INITIAL_TAB !== 'undefined' && INITIAL_TAB === 'flight' && typeof activateTab === 'function') activateTab('flight', false);
shownNow();
window.cosInFlight = { reload: load, get ready() { return state.loaded; } };
