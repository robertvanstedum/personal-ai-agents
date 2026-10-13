// The Workshop Log: a topic's documents, versions, comments and the collaboration record, as a plain list (the conversation itself is the Craftsman Chat page).
// Reads and writes only through the topic API (js/api.js guards). All data is set with textContent; the only HTML inserted is the server's sanitised
// Markdown render. Nothing here calls a model or sends a message.
import { $, $$, toast } from './dom.js';
import { apiGet, apiPost, recordMode } from './api.js';
import { live } from './state.js';
import { newKey } from './actions.js';


const FLOW = ['queued', 'delivered', 'acknowledged', 'working', 'returned'];
const KIND_WORDS = { note: 'Note', document: 'Document', design: 'Design', request: 'Request' };
const DISP_WORDS = { accepted: 'Accepted', changed: 'Changed', declined: 'Declined', deferred: 'Deferred' };
const JOURNAL_KINDS = [['note', 'Note'], ['question', 'Question'], ['decision', 'Decision'], ['evidence', 'Evidence'], ['alternative', 'Alternative'], ['disagreement', 'Disagreement'], ['finding', 'Finding']];

let page, root, S;
let readerRequest = 0;

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

// ── the Workshop Log page: a topic's documents as a plain list (the 'discuss' pane is the log), with the topic record and Attach ──
// A document or the record opens in the same place for review (a "pane") and has one way back. There is no conversation here: "Discuss in Chat" goes to the
// topic's Craftsman conversation. Everything written before (items, versions, comments, the record) is kept and reachable from its row or the record link.
function applyPane() {
  document.body.dataset.wtPane = S.pane;
  renderHead();
  renderBanners();
  renderBody();
  const u = sp(); u.delete('view'); if (S.tid) u.set('topic', S.tid);
  if (S.pane === 'doc' && S.openId) u.set('item_id', S.openId); else u.delete('item_id');
  window.history.replaceState(window.history.state, '', `${window.location.pathname}?${u}`);
}
const here = () => window.location.pathname;
function goTopic(id) { window.location.href = `${here()}?topic=${id}`; }
const narrow = () => window.matchMedia('(max-width: 900px)').matches;
// The topics are a list on the left (a narrow window has the topic as a short list with ‹ ›). Beside the title, two quiet things: the documents (only
// when there are some) and a small ⋯ for Attach and the topic record. Documents are optional; most topics have none.
function renderHead() {
  const box = $('[data-wt-top]'); if (!box) return;
  box.replaceChildren();
  renderRail();
  if (!S.tid || !S.topic) { box.append(h('h1', { class: 'wt-title', 'data-wt-title': '' }, 'Workshop Log')); return; }
  if (narrow()) {
    const list = (S.topics || []).slice();
    if (!list.some((t) => t.id === S.tid)) list.unshift({ id: S.tid, title: S.topic.title });
    const i = list.findIndex((t) => t.id === S.tid);
    const sel = h('select', { class: 'wt-topicsel', 'aria-label': 'Topic', 'data-wt-topicsel': '' }, list.map((t) => h('option', { value: t.id, ...(t.id === S.tid ? { selected: true } : {}) }, t.title)), h('option', { value: '__new' }, '＋ Start a new topic…'));
    sel.addEventListener('change', () => { if (sel.value === '__new') { S.naming = true; renderRail(); renderHead(); } else if (sel.value !== S.tid) goTopic(sel.value); });
    if (S.naming) {
      box.append(newTopicInput());
    } else {
      box.append(h('h1', { class: 'visually-hidden', 'data-wt-title': '' }, `Workshop Log · ${S.topic.title}`),
        h('button', { type: 'button', class: 'wt-step', 'data-wt-prev': '', 'aria-label': 'Previous topic', disabled: i <= 0, onclick: () => goTopic(list[i - 1].id) }, '‹'), sel,
        h('button', { type: 'button', class: 'wt-step', 'data-wt-next': '', 'aria-label': 'Next topic', disabled: i < 0 || i >= list.length - 1, onclick: () => goTopic(list[i + 1].id) }, '›'));
    }
  } else box.append(h('h1', { class: 'wt-title', 'data-wt-title': '' }, S.topic.title));
  box.append(h('span', { class: 'wt-construction', 'data-wt-construction': '' }, 'In construction'));
}
function newTopicInput() {
  const name = h('input', { type: 'text', 'aria-label': 'Name for the new topic', maxlength: '120', placeholder: 'Name the topic, then Enter', 'data-wt-newname': '' });
  const make = async () => { if (!name.value.trim()) { S.naming = false; renderHead(); return; } const r = await post('/topics/create', { title: name.value }); if (r) goTopic(r.topic.id); };
  name.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); make(); } else if (e.key === 'Escape') { S.naming = false; renderRail(); renderHead(); } });
  name.addEventListener('blur', () => setTimeout(() => { if (S.naming && !name.value.trim()) { S.naming = false; renderRail(); renderHead(); } }, 150));
  window.requestAnimationFrame(() => name.focus());
  return name;
}
function renderRail() {
  const lay = document.querySelector('.gu-layout'); if (!lay) return;
  let rail = $('[data-wt-rail]');
  if (!rail) { rail = h('nav', { class: 'wt-rail', 'aria-label': 'Topics', 'data-wt-rail': '' }); lay.prepend(rail); }
  rail.replaceChildren();
  if (!S.tid || !S.topic) return;
  const list = (S.topics || []).slice();
  if (!list.some((t) => t.id === S.tid)) list.unshift({ id: S.tid, title: S.topic.title });
  rail.append(h('p', { class: 'wt-rail-h' }, 'Topics'));
  const ul = h('ul', { class: 'wt-rail-list' });
  for (const t of list) ul.append(h('li', {}, h('button', { type: 'button', class: 'wt-rail-item', 'data-id': t.id, title: t.title, ...(t.id === S.tid ? { 'aria-current': 'true' } : {}), onclick: () => { if (t.id !== S.tid) goTopic(t.id); } }, t.title)));
  rail.append(ul);
  if (S.naming && !narrow()) rail.append(h('div', { class: 'wt-rail-new' }, newTopicInput()));
  else rail.append(h('button', { type: 'button', class: 'wt-link wt-rail-add', 'data-wt-newtopic': '', onclick: () => { S.naming = true; renderRail(); } }, '＋ New topic'));
}
function renderBanners() {
  const b = $('[data-wt-banners]'); b.replaceChildren();
  if (S.error) b.append(h('div', { class: 'wt-banner bad', role: 'alert' }, h('span', {}, h('strong', {}, S.error)), h('button', { type: 'button', class: 'wt-btn', onclick: () => load() }, 'Retry')));
  if (live.off) b.append(h('div', { class: 'wt-banner warn', role: 'status' }, 'You are off the record (Private): looking is fine; adding and commenting are paused.'));
  if (S.convNote) b.append(h('div', { class: 'wt-banner warn', role: 'status' }, S.convNote));
  const R = S.record;
  if (R && ['damaged', 'incomplete', 'missing', 'unavailable'].includes(R.status) && S.pane === 'record') {
    b.append(h('div', { class: `wt-banner ${R.status === 'damaged' ? 'bad' : 'warn'}`, role: 'status', 'data-wt-record-banner': R.status }, h('span', {}, h('strong', {}, 'Shared record: '), R.notice), R.failed ? h('button', { type: 'button', class: 'wt-btn', 'data-wt-record-retry': '', onclick: async () => { await loadRecord(); renderBody(); } }, 'Try again') : null));
  }
}

// ── loading ──
async function load() {
  S.error = null;
  const r = await apiGet(`/topics/${S.tid}`);
  if (!r.ok) { S.error = (r.body && r.body.message) || 'The topic could not be loaded.'; S.topic = S.topic || null; renderBanners(); renderBody(); return; }
  S.topic = r.body.topic; S.items = r.body.items; S.layout = r.body.layout; S.conv = r.body.conversation;
  const want = sp().get('item_id');
  S.pane = 'discuss';
  applyPane();
  if (want && itemOf(want)) await openItem(want, { push: false });
  await loadJournal();
}
async function loadTopics() {                         // the topics to step through: a plain read; nothing is created by looking
  const r = await apiGet('/topics');
  S.topics = r.ok && r.body && Array.isArray(r.body.topics) ? r.body.topics : [];
  if (S.topic) renderHead();
}
async function loadJournal() {
  const r = await apiGet(`/topics/${S.tid}/journal`);
  S.journal = r.ok ? r.body.entries : [];
  const w = await apiGet(`/topics/${S.tid}/inbox`);            // looking only counts files; nothing is imported until asked
  S.inbox = w.ok ? w.body : { waiting: {}, total: 0 };
  await loadRecord();
  if (!S.editing) { renderHead(); renderBody(); }               // a half-written edit is never redrawn over
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
  if (S.openId === id) await refreshOpen(); else { renderHead(); renderBody(); }
  toast(away ? 'Put away. The item and its discussion are kept.' : 'Brought back to current work.');
}
function stepsNode(req) {
  const at = FLOW.indexOf(req.stage);
  return h('ol', { class: 'wt-steps', 'aria-label': `Delivery to ${req.to}` }, FLOW.map((s, i) => h('li', { class: req.stage === 'paused' ? '' : i < at ? 'done' : i === at ? 'here' : '', ...(i === at ? { 'aria-current': 'step' } : {}) }, s[0].toUpperCase() + s.slice(1))),
    req.stage === 'paused' ? h('li', { class: 'here', 'aria-current': 'step' }, 'Paused') : null);
}
async function attachFiles(files) {
  let added = 0;
  for (const f of files) {
    const name = (f.name.replace(/\.[^.]+$/, '') || 'Document').slice(0, 120);
    let r = null;
    if (/^image\//.test(f.type)) r = await upload(`/topics/${S.tid}/items/design`, f, { title: name });
    else {
      if (f.size > 200000) { toast(`“${f.name}” is too large to attach here (200 KB of text). Nothing was added.`); continue; }
      const text = await f.text();
      if (!text.trim()) { toast(`“${f.name}” has no text. Nothing was added.`); continue; }
      r = await post(`/topics/${S.tid}/items`, { kind: 'document', title: name, text });
    }
    if (r) added += 1;
  }
  if (added) { await refreshIndex(); renderHead(); renderBody(); toast(`Attached ${added === 1 ? 'one document' : `${added} documents`}. It opens from the Documents row.`); }
}
function openRecord() {
  readerRequest += 1;
  if (S.pane === 'discuss') window.history.pushState({ wt: 'record' }, '', window.location.href);
  S.pane = 'record'; S.openId = null; S.open = null; applyPane(); window.scrollTo({ top: 0 });
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
async function openItem(id, { push = true, rev = null, quiet = false } = {}) {
  if (!id) return;
  if (dirty() && id !== S.openId) { toast('You have unsaved changes. Save or Cancel first.'); return; }
  const token = ++readerRequest;
  const r = await apiGet(`/topics/${S.tid}/items/${id}${rev ? `?rev=${rev}` : ''}`);
  if (token !== readerRequest) return;
  if (!r.ok) { toast((r.body && r.body.message) || 'That document could not be opened.'); return; }
  if (S.pane === 'discuss' && push) window.history.pushState({ wt: id }, '', window.location.href);      // Back returns to the log
  S.pane = 'doc'; S.openId = id; S.open = r.body; S.rev = r.body.item.rev.rev; S.commenting = false; S.editing = false; S.editText = null; S.replyTo = null;
  applyPane();
  if (!quiet) { window.scrollTo({ top: 0 }); const t = $('#wt-item-title'); if (t) t.focus({ preventScroll: true }); }
}
// After a comment, a reply or a resolve the document is drawn again with the same version and place.
async function refreshOpen({ id = S.openId, rev = S.rev, token = readerRequest, quiet = true } = {}) {
  if (!id) return;
  await refreshIndex();
  // Back, another document, or the topic record wins over a late refresh.
  if (token !== readerRequest || S.openId !== id || S.pane !== 'doc') return;
  return openItem(id, { push: false, rev, quiet });
}
// The one way back to the log (the button, Escape, and the browser's Back are the same step).
function closeItem({ fromPop = false } = {}) {
  if (dirty()) { toast('You have unsaved changes. Save or Cancel first.'); if (fromPop) window.history.pushState({ wt: S.openId || 'record' }, '', window.location.href); return; }
  readerRequest += 1;
  if (!fromPop && window.history.state && window.history.state.wt) { window.history.back(); return; }
  const id = S.openId; S.pane = 'discuss'; S.openId = null; S.open = null; S.commenting = false; S.editing = false; S.editText = null; S.replyTo = null;
  applyPane();
  window.scrollTo({ top: 0 });
  const c = id && document.querySelector(`[data-wt-open="${id}"]`); if (c) c.focus({ preventScroll: true });
}
window.addEventListener('popstate', () => { if (S && S.pane !== 'discuss') closeItem({ fromPop: true }); });

function anchorWords(a) {
  if (!a || a.type === 'item') return 'the whole item';
  if (a.type === 'block') return `paragraph ${a.index + 1}${a.quote ? ` (“${a.quote.slice(0, 60)}”)` : ''}`;
  return `a point ${Math.round(a.x * 100)}% across, ${Math.round(a.y * 100)}% down`;
}
function commentNode(c, all) {
  const replies = all.filter((x) => x.reply_to === c.id);
  const n = h('li', { class: 'wt-cmt', 'data-cid': c.id });
  n.append(h('div', { class: 'wt-ch' }, h('strong', {}, c.by.name), h('span', { class: 'wt-small' }, ` · version ${c.rev} · ${localTimeText(c.at || c.created)} · `),
    h('button', { type: 'button', class: 'wt-btn wt-quiet', 'data-jump': '', onclick: () => jumpTo(c) }, `on ${anchorWords(c.anchor)} ↗`)),
    h('p', {}, c.text));
  for (const r of replies) n.append(h('div', { class: 'reply' }, h('strong', {}, r.by.name), ' · ', h('span', {}, r.text)));
  if (c.status === 'resolved') n.append(h('span', { class: 'wt-badge ok' }, 'Resolved'));
  if (c.disposition) n.append(' ', h('span', { class: 'wt-badge ok' }, `${DISP_WORDS[c.disposition.value]} · ${c.disposition.by} · version ${c.disposition.rev}`));
  const acts = h('div', { class: 'acts' });
  acts.append(h('button', { type: 'button', class: 'wt-btn', 'aria-expanded': String(S.replyTo === c.id), onclick: () => { S.replyTo = S.replyTo === c.id ? null : c.id; renderBody(); const t = $(`.wt-cmt[data-cid="${c.id}"] textarea`); if (t) t.focus(); } }, 'Reply'),
    h('button', { type: 'button', class: 'wt-btn', onclick: async () => { if (await post(`/topics/${S.tid}/items/${S.openId}/comments/${c.id}/resolve`, { resolved: c.status !== 'resolved' })) refreshOpen(); } }, c.status === 'resolved' ? 'Reopen' : 'Resolve'));
  if (!c.disposition) for (const [v, label] of Object.entries(DISP_WORDS)) acts.append(h('button', { type: 'button', class: 'wt-btn', 'data-disp': v, onclick: async () => { if (await post(`/topics/${S.tid}/items/${S.openId}/comments/${c.id}/disposition`, { value: v })) refreshOpen(); } }, label));
  n.append(acts);
  if (S.replyTo === c.id) {                                                  // the reply is written here, under the comment: no window
    const ta = h('textarea', { 'aria-label': `Your reply to ${c.by.name}`, rows: '2', maxlength: '4000' });
    ta.value = (S.replyDraft && S.replyDraft[c.id]) || '';
    ta.addEventListener('input', () => { (S.replyDraft = S.replyDraft || {})[c.id] = ta.value; });
    n.append(h('div', { class: 'wt-replyform', 'data-wt-reply': '' }, ta, h('div', { class: 'wt-acts' },
      h('button', { type: 'button', class: 'wt-btn primary', onclick: async () => {
        if (!ta.value.trim()) return;
        if (await post(`/topics/${S.tid}/items/${S.openId}/comments`, { text: ta.value, reply_to: c.id })) { delete (S.replyDraft || {})[c.id]; S.replyTo = null; refreshOpen(); }
      } }, 'Add reply'), h('button', { type: 'button', class: 'wt-btn', onclick: () => { S.replyTo = null; renderBody(); } }, 'Cancel'))));
  }
  return n;
}
function jumpTo(c) {
  const a = c.anchor; let t = null;
  if (a && a.type === 'block') t = document.querySelector(`.wt-doc [data-block="${a.index}"]`);
  else if (a && a.type === 'point') t = document.querySelector('.wt-img');
  if (t) { t.scrollIntoView({ block: 'center' }); t.classList.add('marked'); setTimeout(() => t.classList.remove('marked'), 1500); }
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
function commentForm(anchor, where) {           // a comment on one paragraph or one point: written under the discussion, with the item
  const ta = h('textarea', { 'aria-label': `Comment on ${where}`, maxlength: '4000' });
  const form = h('li', { class: 'wt-cmt', 'data-wt-form': '' }, h('label', {}, `Comment on ${where} · version ${S.rev}`), ta,
    h('div', { class: 'wt-acts' }, h('button', { type: 'button', class: 'wt-btn primary', onclick: async () => {
      if (!ta.value.trim()) return;
      if (await post(`/topics/${S.tid}/items/${S.openId}/comments`, { text: ta.value, rev: S.rev, anchor })) { S.commenting = false; refreshOpen(); }
    } }, 'Add comment'), h('button', { type: 'button', class: 'wt-btn', onclick: () => form.remove() }, 'Cancel')));
  const list = $('[data-wt-comments]'); if (list) { $$('[data-wt-form]', list).forEach((f) => f.remove()); list.append(form); form.scrollIntoView({ block: 'nearest' }); ta.focus(); }
}
function requestControls(it) {
  const req = it.request; const box = h('div', { class: 'wt-view', style: 'min-height:0;margin-bottom:10px' });
  box.append(h('h3', {}, `Request to ${req.to}`), stepsNode(req), h('p', { class: 'wt-note' }, `Stage ${req.stage_source}: a manual status, not proof that any agent received, started, paused or was reassigned. Queued does not mean received; returned does not mean approved. No agent is connected to report this yet, and changing the recipient or marking it Paused moves or stops nothing.`));
  if (req.included) box.append(h('p', {}, h('strong', {}, 'Included: '), req.included));
  const sel = h('select', { 'aria-label': 'Stage' }, ['queued', 'delivered', 'acknowledged', 'working', 'returned', 'paused'].map((s) => h('option', { value: s, ...(s === req.stage ? { selected: true } : {}) }, s)));
  const who = h('input', { type: 'text', 'aria-label': 'Recipient', value: req.to });
  box.append(h('div', { class: 'wt-acts' }, h('label', {}, 'Stage'), sel, h('label', {}, 'Recipient'), who,
    h('button', { type: 'button', class: 'wt-btn', onclick: async () => {
      const token = readerRequest, rev = S.rev;
      if (await post(`/topics/${S.tid}/items/${it.id}/stage`, { stage: sel.value, recipient: who.value })) {
        await refreshOpen({ id: it.id, rev, token });
      }
    } }, 'Save stage and recipient')));
  return box;
}
function readerNode() {
  const o = S.open; const it = o.item;
  const node = h('section', { class: 'wt-reader', 'aria-labelledby': 'wt-item-title', 'data-wt-reader': '' });
  const revSel = it.revisions.length > 1 ? h('select', { 'aria-label': 'Version', ...(S.editing ? { disabled: true } : {}), onchange: (e) => openItem(it.id, { push: false, rev: e.target.value, quiet: true }) }, it.revisions.map((r) => h('option', { value: r.rev, ...(r.rev === S.rev ? { selected: true } : {}) }, `Version ${r.rev}${r.rev === it.current_rev ? ' (current)' : ''}`))) : null;
  const where = it.archived ? 'previous' : 'current';
  const head = h('div', { class: 'wt-head' }, h('div', {}, h('div', { class: 'wt-eyebrow', 'data-wt-kindline': '' }, `${KIND_WORDS[it.kind]} · ${where} · version ${S.rev} · ${o.item.rev.by}`), h('h2', { id: 'wt-item-title', tabindex: '-1' }, it.title)),
    h('div', { class: 'wt-acts', style: 'margin:0' }, revSel,
      ...(it.kind !== 'design' && !S.editing && !it.archived ? [h('button', { type: 'button', class: 'wt-btn', 'data-wt-edit': '', onclick: startEdit }, 'Edit')] : []),
      menuButton('More', () => [...(it.kind === 'design' ? [['Replace the image', () => reviseDialog(it)]] : []), ...(S.topic && S.topic.conversation_id ? [['Discuss in Chat', () => { window.location.href = chatHref(); }]] : []),
        it.archived ? ['Bring back', () => archive(it.id, false)] : ['Put away', () => archive(it.id, true)]], 'wt-btn')));
  node.append(h('nav', { class: 'wt-crumb', 'aria-label': 'Where you are' }, h('button', { type: 'button', class: 'wt-btn', 'data-wt-back': '', onclick: () => closeItem() }, '‹ Back to the log')));
  node.append(head);
  const main = h('div', { class: 'wt-readmain' });
  if (it.request) main.append(requestControls(it));
  if (S.editing && it.kind !== 'design') main.append(editorNode(it));
  else { const view = h('div', { class: 'wt-view' }); view.append(itemView(o)); main.append(view); }
  node.append(main, discussionNode(o));
  return node;
}
// The Discussion belongs to this item and this version. The comment box sits directly under it: choosing an item, reading it and
// commenting happen in one place, with no second window. A half-written comment is kept per item until it is sent.
function discussionNode(o) {
  const it = o.item; const cm = o.comments; const here = cm.filter((c) => c.rev === S.rev && !c.reply_to);
  const sec = h('details', { class: 'wt-discussion', 'aria-labelledby': 'wt-c-h', 'data-wt-discussion': '', ...(here.length || S.commentsOpen ? { open: true } : {}) });
  sec.addEventListener('toggle', () => { S.commentsOpen = sec.open; });
  sec.append(h('summary', { id: 'wt-c-h' }, here.length ? `Comments on this version (${here.length})` : 'Comments on this version · none yet'));
  sec.append(h('p', { class: 'wt-note' }, 'Seen by everyone on this topic and tied to this version. This is not a chat; the conversation is on the Chat page.'));
  const list = h('ol', { class: 'wt-comments', 'data-wt-comments': '', 'aria-label': 'Comments' });
  here.forEach((c) => list.append(commentNode(c, cm)));
  sec.append(list);
  const draft = (S.drafts = S.drafts || {});
  const ta = h('textarea', { id: 'wt-ct', 'data-wt-draft': it.id, rows: '2', maxlength: '4000', placeholder: 'Write a comment', 'aria-label': `Comment on this ${KIND_WORDS[it.kind].toLowerCase()}: ${it.title}, version ${S.rev}` });
  ta.value = draft[it.id] || '';
  ta.addEventListener('input', () => { draft[it.id] = ta.value; });
  const send = async () => {
    if (!ta.value.trim()) return;
    if (await post(`/topics/${S.tid}/items/${it.id}/comments`, { text: ta.value, rev: S.rev, anchor: { type: 'item' } })) { delete draft[it.id]; S.commentsOpen = true; refreshOpen(); }
  };
  ta.addEventListener('keydown', (e) => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); send(); } });
  sec.append(h('form', { class: 'wt-commentform', 'data-wt-commentform': '', autocomplete: 'off', onsubmit: (e) => e.preventDefault() },
    h('label', { for: 'wt-ct', class: 'wt-small' }, `Comment on this ${KIND_WORDS[it.kind].toLowerCase()} · ${it.title} · version ${S.rev} `, h('span', {}, '(kept with it; not a question)')), ta,
    h('div', { class: 'wt-acts' }, h('button', { type: 'button', class: 'wt-btn primary', 'data-wt-send-comment': '', onclick: send }, 'Comment'),
      h('button', { type: 'button', class: 'wt-btn', 'data-wt-cmode': '', 'aria-pressed': String(S.commenting), onclick: () => { S.commenting = !S.commenting; S.commentsOpen = true; renderBody(); } }, S.commenting ? (it.kind === 'design' ? 'Click the place in the image' : 'Click the paragraph') : (it.kind === 'design' ? 'Comment on a place in the image' : 'Comment on a paragraph')),
      live.off ? h('small', { class: 'wt-note' }, 'Off the record (Private): commenting is paused.') : null)));
  return sec;
}
function reviseDialog(it) {                      // only for a design: choose a new image. Text is edited in the page itself (startEdit)
  const f = h('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp,image/gif', 'aria-label': 'New image' }); const note = h('input', { type: 'text', 'aria-label': 'Note (optional)' });
  const d = dialog('Replace the image', h('p', { class: 'wt-note' }, 'The earlier image is kept; choose it again in the Version menu.'), h('label', {}, 'Image'), f, h('label', {}, 'Note (optional)'), note, h('div', { class: 'wt-acts' }, h('button', { type: 'button', class: 'wt-btn primary', onclick: async () => {
    if (!f.files[0]) { toast('Choose an image first.'); return; }
    const token = readerRequest;
    if (await upload(`/topics/${S.tid}/items/${it.id}/revisions/design`, f.files[0], { note: note.value })) {
      d.close(); await refreshOpen({ id: it.id, rev: null, token, quiet: false });
    }
  } }, 'Save')));
}
// Editing a note, document or request happens in the page, in a large box, not in a small window. Saving keeps the earlier text as an
// earlier version automatically (the Version menu); nobody has to know what a revision is or write what changed.
function dirty() { return !!(S.editing && S.open && S.editText != null && S.editText !== S.open.text); }
function startEdit() { S.editing = true; S.editText = S.open.text; renderBody(); const t = $('[data-wt-editor]'); if (t) t.focus(); }
function cancelEdit() { S.editing = false; S.editText = null; renderBody(); }
async function saveEdit(note) {
  const it = S.open.item;
  const token = readerRequest;
  if (!S.editText || !S.editText.trim()) { toast('There is nothing to save.'); return; }
  if (S.editText === S.open.text) { toast('Nothing changed.'); return; }
  if (await post(`/topics/${S.tid}/items/${it.id}/revisions`, { text: S.editText, note: note || '' })) {
    if (token === readerRequest && S.openId === it.id) { S.editing = false; S.editText = null; }
    await refreshOpen({ id: it.id, rev: null, token, quiet: false });
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
// ── the log: a plain list of the topic's documents, a short "In construction", and a link back to its Craftsman conversation ──
// No composer here: the conversation lives on the Craftsman Chat page (one chat, one place). Reading, versions, comments, Attach and the record work as before.
const chatHref = () => `${page.urls.floor}${S.topic && S.topic.conversation_id ? `?c=${encodeURIComponent(S.topic.conversation_id)}` : ''}`;
function logNode() {
  const items = orderedItems(); const gone = orderedItems(true);
  const sec = h('section', { class: 'wt-log', 'data-wt-log': '', 'aria-label': 'Workshop Log' });
  const bar = h('div', { class: 'wt-logbar' });
  if (S.topic.conversation_id) bar.append(h('a', { class: 'wt-link', 'data-wt-chat-link': '', href: chatHref() }, 'Discuss in Chat →'));
  const input = h('input', { type: 'file', class: 'visually-hidden', 'data-wt-attach-input': '', multiple: true, tabindex: '-1', 'aria-label': 'Attach a document or an image',
    accept: '.md,.markdown,.txt,text/plain,text/markdown,image/png,image/jpeg,image/webp,image/gif' });
  input.addEventListener('change', async () => { const files = [...input.files]; input.value = ''; await attachFiles(files); });
  const waiting = ((S.inbox && S.inbox.total) || 0) + ((S.record && S.record.waiting_for_you) || []).length;
  bar.append(input, h('button', { type: 'button', class: 'wt-link', 'data-wt-attach': '', onclick: () => input.click() }, '＋ Attach'),
    h('button', { type: 'button', class: 'wt-link', 'data-wt-open-record': '', onclick: openRecord }, waiting ? `Topic record · ${waiting} waiting` : 'Topic record'));
  sec.append(bar);
  if (!items.length) sec.append(h('p', { class: 'wt-note', 'data-wt-nodocs': '' }, gone.length ? 'Everything here is put away. Open Earlier below, or attach a document.' : 'No documents on this topic yet. Attach one, or discuss it in Chat.'));
  const list = h('ul', { class: 'wt-loglist', 'aria-label': 'Documents' });
  const row = (it) => h('li', { class: 'wt-logrow', 'data-id': it.id, 'data-kind': it.kind },
    h('button', { type: 'button', class: 'wt-logtitle', 'data-wt-open': it.id, onclick: () => openItem(it.id) }, it.title),
    h('span', { class: 'wt-logmeta' }, `${KIND_WORDS[it.kind]} · version ${it.current_rev}${it.comments_open ? ` · ${it.comments_open} open comment${it.comments_open === 1 ? '' : 's'}` : ''}${it.request ? ` · to ${it.request.to}, ${it.request.stage}` : ''}`),
    h('span', { class: 'wt-logwhen' }, localTimeText(it.updated || it.created)));
  items.forEach((it) => list.append(row(it)));
  sec.append(list);
  if (gone.length) {
    const d = h('details', { class: 'wt-earlier', 'data-wt-earlier': '' }, h('summary', {}, `Earlier (${gone.length})`));
    const old = h('ul', { class: 'wt-loglist' }); gone.forEach((it) => old.append(row(it))); d.append(old); sec.append(d);
  }
  return sec;
}

// ── body ──
function renderBody() {
  const body = $('[data-wt-body]'); if (!body) return;
  const scroll = body.scrollTop;
  body.replaceChildren();
  if (!S.tid) { body.append(startNode()); return; }
  if (!S.topic) { body.append(h('p', { class: 'wt-note' }, S.error || 'Loading the topic…')); return; }
  if (S.pane === 'doc' && S.open) body.append(readerNode());
  else if (S.pane === 'record') {
    S.recOpen = true;
    body.append(h('nav', { class: 'wt-crumb', 'aria-label': 'Where you are' }, h('button', { type: 'button', class: 'wt-btn', 'data-wt-back': '', onclick: () => closeItem() }, '‹ Back to the log')), recordNode());
  } else body.append(logNode());
  body.scrollTop = scroll;
}
function startNode() {
  const box = h('div', { class: 'wt-empty' }, h('h2', {}, 'Start a topic'), h('p', { class: 'wt-note' }, 'A topic is a place to talk through one subject. Documents are optional and it does not need a spec or issue number.'));
  const title = h('input', { type: 'text', 'aria-label': 'Name for the topic', maxlength: '120', placeholder: 'Guild chat improvements' });
  const make = async () => {
    if (!title.value.trim()) { toast('Give the topic a name first.'); return; }
    const r = await post('/topics/create', { title: title.value });
    if (r) window.location.href = `${window.location.pathname}?topic=${r.topic.id}`;
  };
  title.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); make(); } });
  box.append(title, h('button', { type: 'button', class: 'wt-btn primary', onclick: make }, 'Start topic'));
  return box;
}

document.addEventListener('keydown', (e) => {
  if (!S || e.key !== 'Escape' || S.pane === 'discuss' || S.editing || document.querySelector('dialog[open]') || document.querySelector('.wt-menu')) return;
  if (document.activeElement && document.activeElement.matches('textarea,input,select')) return;
  closeItem();
});

export async function initWorkshopTopics(p) {
  page = p;
  if (page.page !== 'workshop') return;
  root = $('[data-wt]');
  S = { tid: page.topic_id || null, topic: null, topics: [], items: [], layout: { order: [], wide: [], last_view: null }, journal: [], record: null, convNote: null, inbox: { waiting: {}, total: 0 }, recOpen: false, recAll: false, pane: 'discuss', openId: null, open: null, rev: null, commenting: false, error: null, naming: false };
  document.body.dataset.wtPane = 'discuss';
  if (!S.tid) {                                       // no topic in the address: go to the most recent one (a plain read), or offer to start the first
    const r = await apiGet('/topics');
    const rows = r.ok && r.body && Array.isArray(r.body.topics) ? r.body.topics : [];
    if (rows.length) { window.location.replace(`${window.location.pathname}?topic=${rows[0].id}`); return; }
    S.topics = []; applyPane(); return;
  }
  loadTopics();
  load();
}
