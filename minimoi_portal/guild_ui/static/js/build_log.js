// The Build Log (Guild 1.1 slice 2, spec §4.1): every item in every status,
// drawn from the same answer GET /api/v1/queue?scope=all gives. Saved views,
// column filters, sort, search, View options (density, pinned ranks,
// columns) and an "N of M" count are client-side only. The drawer shows the
// summary, spec link, trouble reason, the status Save, the rank and the
// history from the file journal. Every write goes through the API with the
// usual guards; nothing is kept in the browser except view settings. No model.
import { $, $$, el, announce } from './dom.js';
import { apiGet, apiPost, recordMode } from './api.js';
import { saveStatus, markChecked, kindOf, newKey } from './actions.js';
import { load, save, isObj } from './state.js';

const PHONE = '(max-width: 640px)';
const TROUBLE = ['blocked', 'rework'];
const VIEWS = {
  next: (r) => !!r.owner_rank || ['design', 'in_build'].includes(r.status),
  ready: (r) => r.status === 'spec_ready',
  progress: (r) => ['design', 'in_build'].includes(r.status),
  trouble: (r) => TROUBLE.includes(r.status),
  roadmap: (r) => ['idea', 'backlog', 'deferred'].includes(r.status),
  all: () => true,
};
const COLS = [
  { k: 'rank', label: 'Rank', filter: 'select' },
  { k: 'title', label: 'ID / title', filter: 'text', locked: true },
  { k: 'status', label: 'Status', filter: 'select' },
  { k: 'priority', label: 'Priority', filter: 'select' },
  { k: 'notes', label: 'Notes · next', filter: 'text' },
  { k: 'author', label: 'Spec author', filter: 'select' },
  { k: 'updated', label: 'Updated', filter: 'select' },
];
const PHONE_COLS = ['rank', 'title', 'status', 'notes'];
const SETTLED = ['saved', 'conflict', 'idempotency_mismatch', 'invalid', 'not_found'];

let page;
let data;                 // { status, items, rank_digest, statuses, ... }
let ui;
let openId = null;
const keys = new Map();   // one idempotency key per (action, item, change), reused on retry

const nice = (s) => (s || '').replace(/_/g, ' ');
// An element with child nodes or text (el() takes text only).
function h(tag, attrs, ...kids) { const n = el(tag, attrs); n.append(...kids); return n; }
const day = (iso) => (iso ? String(iso).slice(0, 10) : '');
// Date and time in the browser's own zone, for the drawer and the history.
function when(iso) {
  const t = Date.parse(iso);
  if (!iso || Number.isNaN(t)) return iso || '';
  return new Date(t).toLocaleString(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function defaults() {
  const phone = window.matchMedia(PHONE).matches;
  return { view: 'next', density: 'compact', pin: true,
           hidden: phone ? COLS.filter((c) => !PHONE_COLS.includes(c.k)).map((c) => c.k) : [] };
}

function loadPrefs() {
  const p = load('buildlog.view', (v) => (isObj(v) && typeof v.view === 'string' && VIEWS[v.view]
    && ['compact', 'comfortable'].includes(v.density) && typeof v.pin === 'boolean' && Array.isArray(v.hidden) ? v : null),
  defaults, 'Build Log view');
  return { ...p, q: '', sort: { k: 'rank', dir: 1 }, filters: {} };
}

function savePrefs() { save('buildlog.view', { view: ui.view, density: ui.density, pin: ui.pin, hidden: ui.hidden }); }

const rows = () => (data && Array.isArray(data.items) ? data.items : []);
const inView = (r) => !r.status_known || VIEWS[ui.view](r);   // unknown rows are never hidden by a view

function age(iso) {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : (Date.now() - t) / 86400000;
}

function passes(r) {
  const f = ui.filters;
  if (f.rank === 'ranked' && !r.owner_rank) return false;
  if (f.rank === 'unranked' && r.owner_rank) return false;
  if (f.title && !`#${r.id} ${r.title}`.toLowerCase().includes(f.title.toLowerCase())) return false;
  if (f.status && (f.status === 'unknown' ? r.status_known : r.status !== f.status)) return false;
  if (f.priority && (r.priority || '(none)') !== f.priority) return false;
  if (f.notes && !(r.notes || '').toLowerCase().includes(f.notes.toLowerCase())) return false;
  if (f.author && (r.spec_author || '(blank)') !== f.author) return false;
  if (f.updated) {
    const a = age(r.last_transition_at);
    if (a == null || a > Number(f.updated) + (f.updated === '0' ? 1 : 0)) return false;
  }
  if (ui.q) {
    const hay = [r.id, r.title, r.summary, r.notes, r.spec_author, r.status_label, r.trouble_reason, r.priority]
      .join(' ').toLowerCase();
    if (!hay.includes(ui.q.toLowerCase())) return false;
  }
  return true;
}

function sortValue(r, k) {
  switch (k) {
    case 'rank': return r.owner_rank || 99;
    case 'title': return r.title.toLowerCase();
    case 'status': return r.status_label;
    case 'priority': return { critical: 0, high: 1, normal: 2, low: 3 }[r.priority] ?? 4;
    case 'notes': return (r.notes || '').toLowerCase();
    case 'author': return (r.spec_author || '￿').toLowerCase();
    case 'updated': return r.last_transition_at || '';
    default: return '';
  }
}

function compare(a, b) {
  const { k, dir } = ui.sort;
  const va = sortValue(a, k); const vb = sortValue(b, k);
  if (va < vb) return -dir;
  if (va > vb) return dir;
  return (b.last_transition_at || '').localeCompare(a.last_transition_at || '');   // newest first on ties
}

function shown() {
  let list = rows().filter(inView).filter(passes).sort(compare);
  if (ui.pin) {
    const pinned = list.filter((r) => r.owner_rank).sort((a, b) => a.owner_rank - b.owner_rank);
    list = pinned.concat(list.filter((r) => !r.owner_rank));
  }
  return list;
}

function options(k) {
  const all = rows();
  if (k === 'rank') return [['', 'Any'], ['ranked', 'Ranked'], ['unranked', 'Unranked']];
  if (k === 'status') return [['', 'Any']].concat((data.statuses || []).map((s) => [s, nice(s)]), [['unknown', 'unknown row']]);
  if (k === 'priority') return [['', 'Any']].concat([...new Set(all.map((r) => r.priority || '(none)'))].sort().map((p) => [p, p]));
  if (k === 'author') return [['', 'Any']].concat([...new Set(all.map((r) => r.spec_author || '(blank)'))].sort().map((a) => [a, a]));
  if (k === 'updated') return [['', 'Any time'], ['0', 'Today'], ['7', 'Last 7 days'], ['30', 'Last 30 days']];
  return [];
}

function renderHead() {
  const head = $('[data-bl-head]');
  const tr = el('tr');
  for (const c of COLS) {
    if (ui.hidden.includes(c.k)) continue;
    const sorted = ui.sort.k === c.k;
    const th = el('th', { scope: 'col', 'data-col': c.k, 'aria-sort': sorted ? (ui.sort.dir > 0 ? 'ascending' : 'descending') : 'none' });
    const b = el('button', { type: 'button', 'data-bl-sort': c.k }, c.label);
    b.append(el('span', { class: 'bl-ar', 'aria-hidden': 'true' }, sorted && ui.sort.dir < 0 ? ' ▼' : ' ▲'));
    th.append(b);
    const fl = el('div', { class: 'bl-fl' });
    const label = `Filter ${c.label}`;
    if (c.filter === 'text') {
      fl.append(el('input', { type: 'text', 'data-bl-filter': c.k, placeholder: 'Filter', 'aria-label': label, value: ui.filters[c.k] || '' }));
    } else {
      const sel = el('select', { 'data-bl-filter': c.k, 'aria-label': label });
      for (const [v, t] of options(c.k)) {
        const o = el('option', { value: v }, t);
        if ((ui.filters[c.k] || '') === v) o.selected = true;
        sel.append(o);
      }
      fl.append(sel);
    }
    th.append(fl);
    tr.append(th);
  }
  head.replaceChildren(tr);
}

function statusCell(r) {
  const td = el('td', { 'data-col': 'status' });
  if (!r.status_known) {
    td.append(el('span', { class: 'bl-st bl-st-unknown', title: 'This row could not be read; check the queue file' }, r.status_label));
    return td;
  }
  td.append(el('span', { class: `bl-st bl-st-${r.status}` }, nice(r.status)));
  if (r.trouble && r.trouble_reason) {
    td.title = `${nice(r.status)}: ${r.trouble_reason}`;
  }
  return td;
}

function cell(r, k) {
  switch (k) {
    case 'rank': {
      const td = el('td', { 'data-col': 'rank' });
      td.append(r.owner_rank ? el('span', { class: 'bl-rank', 'aria-label': `Rank ${r.owner_rank}` }, String(r.owner_rank))
        : el('span', { class: 'bl-norank', 'aria-hidden': 'true' }, '·'));
      return td;
    }
    case 'title': {
      const td = el('td', { 'data-col': 'title' });
      const b = el('button', { type: 'button', class: 'bl-open', 'data-bl-open': r.id, 'aria-label': `Open #${r.id} ${r.title}` });
      b.append(el('span', { class: 'bl-id' }, `#${r.id}`), ' ', el('span', { class: 'bl-ttl' }, r.title));
      td.append(b);
      return td;
    }
    case 'status': return statusCell(r);
    case 'priority': return el('td', { 'data-col': 'priority', class: `bl-pr bl-pr-${r.priority || 'none'}` }, r.priority || '—');
    case 'notes': {
      const td = el('td', { 'data-col': 'notes' });
      const text = r.trouble && r.trouble_reason ? `${nice(r.status)}: ${r.trouble_reason}` : (r.notes || '');
      td.append(el('span', { class: 'bl-nx', title: text }, text || '—'));
      return td;
    }
    case 'author': return el('td', { 'data-col': 'author', class: r.spec_author ? '' : 'bl-blank' }, r.spec_author || '');
    case 'updated': return el('td', { 'data-col': 'updated' }, day(r.last_transition_at));
    default: return el('td');
  }
}

function renderBody() {
  const body = $('[data-bl-body]');
  const visible = COLS.filter((c) => !ui.hidden.includes(c.k));
  if (!data || data.status !== 'ok') {
    body.replaceChildren(el('tr', {}, ''));
    body.firstChild.append(el('td', { colspan: visible.length, class: 'bl-empty' }, 'Unknown: the queue could not be read.'));
    $('[data-bl-count]').textContent = 'unknown';
    return;
  }
  const list = shown();
  const trs = list.map((r, n) => {
    const tr = el('tr', { 'data-bl-row': r.id, 'data-status': r.status || 'unknown' });
    if (ui.pin && r.owner_rank) tr.classList.add('bl-pinned');
    if (ui.pin && r.owner_rank && !(list[n + 1] && list[n + 1].owner_rank)) tr.classList.add('bl-pin-last');
    if (r.trouble) tr.classList.add('bl-trouble');
    if (!r.status_known) tr.classList.add('bl-unknown-row');
    if (openId === r.id) tr.classList.add('bl-is-open');
    for (const c of visible) tr.append(cell(r, c.k));
    return tr;
  });
  if (!trs.length) {
    const tr = el('tr');
    tr.append(el('td', { colspan: visible.length, class: 'bl-empty' }, 'No items match. Clear filters or pick another view.'));
    trs.push(tr);
  }
  body.replaceChildren(...trs);
  $('[data-bl-matrix]').className = `bl-matrix ${ui.density}`;
  const total = rows().length;
  $('[data-bl-count]').replaceChildren(el('strong', {}, `${list.length} of ${rows().filter(inView).length}`), ` · ${total} total`);
}

function renderViews() {
  for (const b of $$('[data-bl-view]')) {
    const k = b.dataset.blView;
    b.setAttribute('aria-pressed', String(k === ui.view));
    $('[data-bl-view-n]', b).textContent = data && data.status === 'ok' ? String(rows().filter((r) => !r.status_known || VIEWS[k](r)).length) : '?';
  }
  for (const b of $$('[data-bl-density]')) b.setAttribute('aria-pressed', String(b.dataset.blDensity === ui.density));
  $('[data-bl-pin]').checked = ui.pin;
  const cols = $('[data-bl-cols]');
  cols.replaceChildren(...COLS.map((c) => {
    const label = el('label');
    const box = el('input', { type: 'checkbox', 'data-bl-col': c.k });
    box.checked = !ui.hidden.includes(c.k);
    if (c.locked) box.disabled = true;
    label.append(box, ` ${c.label}`);
    return label;
  }));
}

function render() { renderViews(); renderHead(); renderBody(); }

// ── The drawer ───────────────────────────────────────────────────────────────

function fact(dl, term, value, attrs = {}) {
  dl.append(el('dt', {}, term));
  const dd = el('dd', attrs);
  if (value instanceof Node) dd.append(value); else dd.textContent = value;
  dl.append(dd);
}

function result(node, kind, text) {
  node.hidden = !text;
  node.dataset.kind = kind;
  node.textContent = text || '';
  if (text) announce(text);
}

function keyFor(name) {
  if (!keys.has(name)) keys.set(name, newKey());
  return keys.get(name);
}

function rankBlock(r) {
  const sec = el('section', { class: 'bl-dsec', 'aria-labelledby': 'bl-rank-h' });
  sec.append(el('h3', { id: 'bl-rank-h' }, 'Your rank'));
  const group = el('div', { class: 'bl-seg', role: 'group', 'aria-label': `Rank of #${r.id}` });
  for (const v of [1, 2, 3, null]) {
    const b = el('button', { type: 'button', 'data-bl-rank': v == null ? 'none' : String(v),
      'aria-pressed': String((r.owner_rank || null) === v) }, v == null ? 'None' : String(v));
    group.append(b);
  }
  sec.append(group, el('p', { class: 'small' }, 'Setting a rank moves the item that held it down one; a fourth is cleared.'),
    el('p', { class: 'save-result', 'data-bl-rank-result': true, role: 'status', hidden: true }));
  return sec;
}

function statusBlock(r) {
  const sec = el('section', { class: 'bl-dsec', 'aria-labelledby': 'bl-status-h' });
  sec.append(el('h3', { id: 'bl-status-h' }, 'Status'));
  if (!r.status_known) {
    sec.append(el('p', { class: 'small' }, 'This row could not be read, so it cannot be saved here. Check the queue file.'));
    return sec;
  }
  const form = el('form', { class: 'bl-status', 'data-bl-status': r.id });
  const sel = el('select', { id: 'bl-st-sel', 'data-bl-status-select': true, 'aria-label': `Status of #${r.id}` });
  for (const s of data.statuses) {
    const o = el('option', { value: s }, nice(s));
    if (s === r.status) o.selected = true;
    sel.append(o);
  }
  const reason = el('input', { 'data-bl-reason': true, maxlength: '500', placeholder: 'Reason (required for blocked or rework)',
    'aria-label': 'Reason', value: r.trouble ? r.trouble_reason : '' });
  const saveBtn = el('button', { type: 'submit', class: 'btn', 'data-bl-save': true }, 'Save');
  form.append(sel, reason, saveBtn);
  if (r.trouble && r.trouble_from) {
    const back = el('button', { type: 'button', class: 'btn-quiet', 'data-bl-back': r.trouble_from },
      `Suggest: back to ${nice(r.trouble_from)}`);
    form.append(back);
  }
  form.append(el('p', { class: 'save-result', 'data-bl-status-result': true, role: 'status', hidden: true }));
  const sync = () => {
    reason.hidden = !TROUBLE.includes(sel.value);
    saveBtn.hidden = sel.value === r.status && !(TROUBLE.includes(sel.value) && reason.value.trim() !== (r.trouble_reason || ''));
  };
  sel.addEventListener('change', sync);
  reason.addEventListener('input', sync);
  sync();
  sec.append(form, el('p', { class: 'small' },
    'Save writes the live queue: locked, checked against the version you see, read back, with a receipt. '
    + 'Leaving blocked or rework is only ever an explicit Save.'));
  return sec;
}

function historyBlock() {
  const sec = el('section', { class: 'bl-dsec', 'aria-labelledby': 'bl-hist-h' });
  sec.append(el('h3', { id: 'bl-hist-h' }, 'History'), h('ol', { class: 'bl-hist', 'data-bl-history': true },
    el('li', { class: 'small' }, 'Reading the journal…')));
  return sec;
}

function historyLine(e) {
  const li = el('li', { 'data-bl-hist-op': e.op || '' });
  const at = el('span', { class: 'bl-hist-at' }, e.at ? when(e.at) : '');
  let what;
  if (e.op === 'rank') {
    what = e.shifted_by != null && e.shifted_by !== undefined
      ? `Rank ${e.from ?? 'none'} → ${e.to ?? 'none'} (moved by a rank on #${e.shifted_by})`
      : `Rank ${e.from ?? 'none'} → ${e.to ?? 'none'}`;
  } else if (e.op === 'create') {
    what = `Created as ${nice(e.to)}`;
  } else if (e.op === 'status' && e.reason_edit) {
    what = `Reason changed (${nice(e.to)}): ${e.reason_from || '—'} → ${e.reason_to || '—'}`;
  } else if (e.op === 'status') {
    what = `${nice(e.from) || '—'} → ${nice(e.to)}${e.reason_to ? ` · ${e.reason_to}` : ''}`;
  } else {
    what = `${e.op}: ${JSON.stringify(e.from)} → ${JSON.stringify(e.to)}`;
  }
  li.append(at, el('span', { class: 'bl-hist-what' }, what),
    el('span', { class: 'small bl-hist-who' }, `${e.principal || 'unknown'} · ${e.via || ''}${e.receipt_id ? ` · ${e.receipt_id}` : ''}${e.recovered ? ' · recovered' : ''}`));
  return li;
}

async function loadHistory(id) {
  const r = await apiGet(`/queue/items/${id}/journal`);
  const list = $('[data-bl-history]');
  if (!list || openId !== id) return;
  const body = r.body || {};
  if (!r.ok || body.status !== 'ok' || !Array.isArray(body.journal)) {
    list.replaceChildren(el('li', { class: 'bl-unknown-line' }, 'History unknown: the journal could not be read.'));
    return;
  }
  if (!body.journal.length) {
    list.replaceChildren(el('li', { class: 'small' }, 'No journaled changes yet (changes from before the journal are not shown).'));
    return;
  }
  list.replaceChildren(...body.journal.slice().reverse().map(historyLine));
}

function renderDrawer() {
  const d = $('[data-bl-drawer]');
  const layout = $('[data-bl-layout]');
  const r = rows().find((x) => x.id === openId);
  if (!r) { d.hidden = true; layout.classList.remove('bl-drawer-open'); return; }
  const top = el('div', { class: 'bl-d-top' });
  top.append(el('span', { class: 'bl-id' }, `#${r.id}`),
    el('span', { class: `bl-st bl-st-${r.status_known ? r.status : 'unknown'}` }, r.status_known ? nice(r.status) : r.status_label));
  if (r.owner_rank) top.append(el('span', { class: 'bl-rank' }, String(r.owner_rank)));
  top.append(el('span', { class: 'bl-sp' }), el('button', { type: 'button', class: 'btn-quiet', 'data-bl-close': true }, 'Close ✕'));
  const dl = el('dl', { class: 'bl-facts' });
  fact(dl, 'Status', r.status_known ? nice(r.status) : r.status_label);
  fact(dl, 'Rank', r.owner_rank ? String(r.owner_rank) : 'none');
  fact(dl, 'Priority', r.priority || '—');
  fact(dl, 'Spec author', r.spec_author || 'blank: no author line in the spec', { class: r.spec_author ? '' : 'bl-blank-dd', 'data-bl-author': true });
  fact(dl, 'Updated', r.last_transition_at ? when(r.last_transition_at) : '—');
  if (r.github_issue) fact(dl, 'GitHub', r.github_issue);
  const parts = [top, el('h2', { class: 'bl-d-title' }, r.title), dl];
  if (r.trouble) {
    const t = el('section', { class: 'bl-dsec bl-trouble-box', 'data-bl-trouble': true });
    t.append(el('h3', {}, r.status === 'rework' ? 'Rework' : 'Blocked'),
      el('p', {}, r.trouble_reason || 'No reason recorded (from before reasons were required).'));
    if (r.trouble_from) t.append(el('p', { class: 'small' }, `In trouble from ${nice(r.trouble_from)}${r.trouble_since ? ` since ${when(r.trouble_since)}` : ''}.`));
    parts.push(t);
  }
  if (r.summary) parts.push(h('section', { class: 'bl-dsec' }, el('h3', {}, 'Summary'), el('p', {}, r.summary)));
  if (r.notes) parts.push(h('section', { class: 'bl-dsec' }, el('h3', {}, 'Notes'), el('p', { class: 'bl-notes' }, r.notes)));
  const spec = el('section', { class: 'bl-dsec' });
  spec.append(el('h3', {}, 'Spec'));
  if (r.spec_file) {
    const a = el('a', { href: `/guild/build/spec/${encodeURIComponent(r.spec_file)}`, 'data-bl-spec': true }, r.spec_file);
    spec.append(h('p', {}, a));
  } else spec.append(el('p', { class: 'small' }, 'No spec file.'));
  parts.push(spec, statusBlock(r), rankBlock(r), historyBlock(),
    h('p', { class: 'small' }, el('a', { href: page.urls.item.replace('__ID__', r.id) }, 'Open the item page →')));
  d.replaceChildren(...parts);
  d.hidden = false;
  layout.classList.add('bl-drawer-open');
  loadHistory(r.id);
}

function openItem(id, focus = true) {
  openId = id;
  renderBody();
  renderDrawer();
  if (focus) $('[data-bl-drawer]').focus({ preventScroll: true });
}

function closeDrawer() {
  const id = openId;
  openId = null;
  renderBody();
  renderDrawer();
  const b = $(`[data-bl-open="${id}"]`);
  if (b) b.focus();
}

async function reload() {
  const r = await apiGet('/queue?scope=all');
  const body = r.body || {};
  if (r.ok && body.status === 'ok') {
    data = { ...data, status: 'ok', items: body.items, rank_digest: body.rank_digest, statuses: body.statuses || data.statuses };
  } else if (r.status !== 0) {
    data = { ...data, status: 'unknown', items: null };
  }
  render();
  if (openId != null) renderDrawer();
}

async function onSaveStatus(form) {
  const r = rows().find((x) => x.id === Number(form.dataset.blStatus));
  if (!r) return;
  const to = $('[data-bl-status-select]', form).value;
  const reason = $('[data-bl-reason]', form).value.trim();
  const out = $('[data-bl-status-result]', form);
  if (TROUBLE.includes(to) && !reason) { result(out, 'warn', 'Blocked and Rework need a reason. Nothing was saved.'); return; }
  const name = `status:${r.id}:${r.item_digest}:${to}:${reason}`;
  const btn = $('[data-bl-save]', form);
  btn.disabled = true;
  const res = await saveStatus(r.id, to, TROUBLE.includes(to) ? reason : null, r.item_digest, keyFor(name));
  btn.disabled = false;
  if (SETTLED.includes(res.code)) keys.delete(name);
  await reload();
  const again = $(`[data-bl-status="${r.id}"] [data-bl-status-result]`);
  result(again || out, res.kind, res.message || 'Nothing was saved');
}

async function onRank(btn) {
  const id = openId;
  const v = btn.dataset.blRank === 'none' ? null : Number(btn.dataset.blRank);
  const name = `rank:${id}:${v}:${data.rank_digest}`;
  for (const b of $$('[data-bl-rank]')) b.disabled = true;
  const r = await apiPost(`/queue/items/${id}/rank`, { rank: v, expect_rank_digest: data.rank_digest,
    idempotency_key: keyFor(name), record_mode: recordMode() });
  const body = r.body || {};
  const code = body.result || body.error || 'failed';
  if (SETTLED.includes(code)) keys.delete(name);
  await reload();
  const out = $('[data-bl-rank-result]');
  if (out) result(out, kindOf(code), body.message || 'Nothing was changed');
}

async function onNew(form) {
  const title = $('[data-bl-new-title]', form).value.trim();
  const status = $('[data-bl-new-status]', form).value;
  const out = $('[data-bl-new-result]', form);
  if (!title) { result(out, 'warn', 'A title is needed.'); return; }
  const name = `new:${title}:${status}`;
  const btn = $('[data-bl-new-add]', form);
  btn.disabled = true;
  const r = await apiPost('/queue/items', { spec_title: title, status, idempotency_key: keyFor(name), record_mode: recordMode() });
  btn.disabled = false;
  const body = r.body || {};
  const code = body.result || body.error || 'failed';
  if (SETTLED.includes(code)) keys.delete(name);
  result(out, kindOf(code), body.message || 'Nothing was added');
  if (code === 'saved') {
    $('[data-bl-new-title]', form).value = '';
    await reload();
    if (body.item_id) { ui.view = 'all'; savePrefs(); render(); openItem(body.item_id); }
  }
}

function bind() {
  $('[data-bl-views]').addEventListener('click', (e) => {
    const b = e.target.closest('[data-bl-view]');
    if (!b) return;
    ui.view = b.dataset.blView; savePrefs(); render();
  });
  $('[data-bl-search]').addEventListener('input', (e) => { ui.q = e.target.value; renderBody(); });
  $('[data-bl-clear]').addEventListener('click', () => {
    ui.filters = {}; ui.q = ''; $('[data-bl-search]').value = ''; render();
  });
  const head = $('[data-bl-head]');
  head.addEventListener('click', (e) => {
    const b = e.target.closest('[data-bl-sort]');
    if (!b) return;
    const k = b.dataset.blSort;
    ui.sort = { k, dir: ui.sort.k === k ? -ui.sort.dir : (k === 'updated' ? -1 : 1) };
    renderHead(); renderBody();
    const again = $(`[data-bl-sort="${k}"]`); if (again) again.focus();
  });
  head.addEventListener('input', (e) => {
    const f = e.target.closest('[data-bl-filter]');
    if (!f) return;
    ui.filters[f.dataset.blFilter] = f.value;
    renderBody();
  });
  $('[data-bl-vopts]').addEventListener('click', (e) => {
    const d = e.target.closest('[data-bl-density]');
    if (d) { ui.density = d.dataset.blDensity; savePrefs(); render(); }
  });
  $('[data-bl-vopts]').addEventListener('change', (e) => {
    if (e.target.matches('[data-bl-pin]')) { ui.pin = e.target.checked; savePrefs(); renderBody(); }
    const c = e.target.closest('[data-bl-col]');
    if (c) {
      const k = c.dataset.blCol;
      ui.hidden = c.checked ? ui.hidden.filter((x) => x !== k) : ui.hidden.concat(k);
      savePrefs(); renderHead(); renderBody();
    }
  });
  $('[data-bl-body]').addEventListener('click', (e) => {
    const tr = e.target.closest('[data-bl-row]');
    if (tr) openItem(Number(tr.dataset.blRow));
  });
  const drawer = $('[data-bl-drawer]');
  drawer.addEventListener('click', (e) => {
    if (e.target.closest('[data-bl-close]')) { closeDrawer(); return; }
    const rk = e.target.closest('[data-bl-rank]');
    if (rk) { onRank(rk); return; }
    const back = e.target.closest('[data-bl-back]');
    if (back) {
      const form = back.closest('form');
      $('[data-bl-status-select]', form).value = back.dataset.blBack;
      $('[data-bl-status-select]', form).dispatchEvent(new Event('change'));
    }
  });
  drawer.addEventListener('submit', (e) => {
    const form = e.target.closest('[data-bl-status]');
    if (!form) return;
    e.preventDefault();
    onSaveStatus(form);
  });
  // View options closes on Escape or a click outside it, like the More menu.
  const vopts = $('[data-bl-vopts]');
  document.addEventListener('click', (e) => { if (vopts.open && !vopts.contains(e.target)) vopts.open = false; });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (vopts.open) { vopts.open = false; $('summary', vopts).focus(); return; }
    if (openId != null) closeDrawer();
  });
  const newForm = $('[data-bl-new]');
  const newOpen = $('[data-bl-new-open]');
  newOpen.addEventListener('click', () => {
    newForm.hidden = !newForm.hidden;
    newOpen.setAttribute('aria-expanded', String(!newForm.hidden));
    if (!newForm.hidden) $('[data-bl-new-title]').focus();
  });
  $('[data-bl-new-cancel]').addEventListener('click', () => { newForm.hidden = true; newOpen.setAttribute('aria-expanded', 'false'); newOpen.focus(); });
  newForm.addEventListener('submit', (e) => { e.preventDefault(); onNew(newForm); });
  for (const b of $$('[data-mark-checked]')) {
    b.addEventListener('click', async () => {
      b.disabled = true;
      const res = await markChecked(b.dataset.markChecked);
      result(b.parentElement.querySelector('[data-check-result]'), res.kind, res.message || 'Nothing was changed');
      if (res.code === 'checked') b.remove(); else b.disabled = false;
    });
  }
}

export function initBuildLog(p) {
  page = p;
  const raw = document.getElementById('build-log-data');
  if (!raw || !$('[data-bl-matrix]')) return;
  data = JSON.parse(raw.textContent);
  ui = loadPrefs();
  bind();
  render();
  const wanted = Number(new URLSearchParams(window.location.search).get('item'));
  if (wanted && rows().some((r) => r.id === wanted)) {
    if (!inView(rows().find((r) => r.id === wanted))) { ui.view = 'all'; render(); }
    openItem(wanted, false);
  }
}
