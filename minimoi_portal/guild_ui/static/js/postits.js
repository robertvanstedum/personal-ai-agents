// Post-its (S7, decision 4): added and removed directly, one request each, no
// proposal, confirm or receipt. Removed ones go to the kept bin and can be
// restored. Every list is re-drawn from the server's answer; nothing about
// post-its is kept in this browser. Off the record, nothing is sent.
import { $, $$, el, localTime, announce } from './dom.js';
import { apiGet, apiPost, recordMode } from './api.js';
import { live } from './state.js';
import { newKey } from './actions.js';

let page;
let lastListRead = new Date().toISOString();
const addKeys = new Map();   // one key per composed post-it, dropped when its text changes

const KINDS = { added: 'ok', binned: 'ok', restored: 'ok', already_binned: 'warn', already_active: 'warn' };

function result(container, kind, text) {
  const out = $('[data-postit-result]', container) || $('[data-postit-result]');
  if (!out) return;
  out.hidden = false;
  out.dataset.kind = kind;
  out.textContent = text;
  announce(text);
}

function meta(p, binned) {
  const author = el('span', { 'data-postit-author': true, 'data-author-kind': p.author_kind }, p.author_label);
  const line = el('p', { class: 'postit-meta' });
  if (binned) {
    line.append(author, document.createTextNode(` · binned ${localTime(p.binned_at)}${p.binned_by_label ? ` by ${p.binned_by_label}` : ''}`));
  } else {
    line.append(author, document.createTextNode(` · ${localTime(p.created_at)}`));
  }
  return line;
}

function short(text) { return text.length > 40 ? `${text.slice(0, 39)}…` : text; }

export function postitRow(p) {
  const li = el('li', { class: 'postit', 'data-postit': p.id, 'data-row': true });
  li.append(el('p', { class: 'postit-text' }, p.text), meta(p, false),
    el('button', { type: 'button', class: 'btn-mini postit-act', 'data-postit-bin': p.id,
      'aria-label': `Remove post-it to the bin: ${short(p.text)}` }, 'Remove'));
  return li;
}

export function binRow(p) {
  const li = el('li', { class: 'postit is-binned', 'data-bin-item': p.id, 'data-row': true });
  li.append(el('p', { class: 'postit-text' }, p.text), meta(p, true),
    el('button', { type: 'button', class: 'btn-mini postit-act', 'data-postit-restore': p.id,
      'aria-label': `Restore post-it: ${short(p.text)}` }, 'Restore'));
  return li;
}

function setEnabled(container, on) {
  for (const n of $$('[data-postit-input], [data-postit-add-btn]', container)) n.disabled = !on;
}

function unavailable(container, text) {
  const body = $('[data-postits-body]', container);
  body.replaceChildren(el('p', { class: 'unknown-line', 'data-postits-line': true, 'data-row': true }, text));
  setEnabled(container, false);
}

function list(rows) {
  const ul = el('ul', { class: 'postit-list' });
  for (const r of rows) ul.append(r);
  return ul;
}

// The rail: the newest few, then "more" and the bin (zone from GET /floor or built from GET /postits).
export function renderRail(zone) {
  for (const c of $$('[data-postits][data-mode="rail"]')) {
    if (zone.state !== 'ok') { unavailable(c, zone.text); continue; }
    setEnabled(c, true);
    const body = $('[data-postits-body]', c);
    const parts = [];
    if (!zone.shown.length) parts.push(el('p', { class: 'small', 'data-postits-line': true }, zone.text));
    parts.push(list(zone.shown.map(postitRow)));
    const links = el('p', { class: 'postit-links small', 'data-postits-links': true });
    if (zone.more) links.append(el('a', { href: page.urls.postits, 'data-postits-more': true }, `${zone.more} more →`), document.createTextNode(' · '));
    links.append(el('a', { href: `${page.urls.postits}#bin`, 'data-postits-bin-link': true }, `Bin (${zone.bin_total}) →`));
    parts.push(links);
    body.replaceChildren(...parts);
  }
  for (const n of $$('[data-postits-count]')) n.textContent = zone.state === 'ok' ? `· ${zone.active_total} on the board` : '· unavailable';
  for (const n of $$('[data-ps-postits]')) n.textContent = zone.state === 'ok' ? `Post-its (${zone.active_total}) →` : 'Post-its →';
}

function renderBoard(r) {
  for (const c of $$('[data-postits][data-mode="board"]')) {
    if (!r.ok || !r.body.available) { unavailable(c, (r.body && r.body.message) || 'Post-its unavailable — add and remove are paused'); continue; }
    setEnabled(c, true);
    const rows = r.body.postits;
    const parts = [];
    if (!rows.length) parts.push(el('p', { class: 'small', 'data-postits-line': true }, 'No post-its on the board'));
    parts.push(list(rows.map(postitRow)));
    $('[data-postits-body]', c).replaceChildren(...parts);
  }
}

function renderBin(r) {
  for (const c of $$('[data-postits][data-mode="bin"]')) {
    if (!r.ok || !r.body.available) { unavailable(c, (r.body && r.body.message) || 'Bin unavailable — treat as unknown'); continue; }
    const { bin, total } = r.body;
    const line = total ? `${total} in the bin${total > bin.length ? ` · newest ${bin.length} shown` : ''} · kept, never emptied`
      : 'The bin is empty · kept, never emptied';
    $('[data-postits-body]', c).replaceChildren(el('p', { class: 'small', 'data-postits-line': true }, line), list(bin.map(binRow)));
  }
}

const LISTS = '[data-postits][data-mode="board"], [data-postits][data-mode="bin"]';

function markListsStale() {
  for (const c of $$(LISTS)) {
    c.dataset.listStale = 'true';
    if (!$('[data-list-fresh]', c)) {
      c.prepend(el('span', { class: 'stale-mark', 'data-list-fresh': true }, `Stale · this list could not be re-read; last read ${localTime(lastListRead)}. `));
    }
  }
}

function clearListsStale() {
  lastListRead = new Date().toISOString();
  for (const c of $$(LISTS)) {
    delete c.dataset.listStale;
    for (const m of $$('[data-list-fresh]', c)) m.remove();
  }
}

// The full board and the bin (bench, Post-its page), re-read from the server.
// A failed read keeps the lists on screen, marked stale, never as current.
const unread = (r) => r.status === 0 || r.status === 401 || (r.status >= 500 && !(r.body && 'available' in r.body));

export async function reloadLists() {
  if (!$(LISTS)) return;
  const board = await apiGet('/postits');
  if (unread(board)) { markListsStale(); return; }
  const bin = $('[data-postits][data-mode="bin"]') ? await apiGet('/postits/bin') : null;
  if (bin && unread(bin)) { markListsStale(); return; }
  renderBoard(board);
  if (bin) renderBin(bin);
  clearListsStale();
}

export async function reloadPostits() {
  const board = await apiGet('/postits');
  const ok = board.ok && board.body.available;
  const cap = (board.body && board.body.cap) || 4;
  const rows = ok ? board.body.postits : [];
  renderRail(ok ? { state: 'ok', shown: rows.slice(0, cap), active_total: rows.length, more: Math.max(0, rows.length - cap),
    bin_total: board.body.bin_total, text: rows.length ? `${rows.length} on the board` : 'No post-its on the board' }
    : { state: 'unavailable', text: (board.body && board.body.message) || 'Post-its unavailable — add and remove are paused' });
  renderBoard(board);
  const bin = $('[data-postits][data-mode="bin"]') ? await apiGet('/postits/bin') : null;
  if (bin) renderBin(bin);
  if (!unread(board) && !(bin && unread(bin))) clearListsStale();
}

async function onAdd(form) {
  const container = form.closest('[data-postits]');
  const input = $('[data-postit-input]', form);
  const text = input.value.trim();
  if (!text) return;
  if (live.off) { result(container, 'warn', page.off_record_text); return; }   // never sent off the record
  const id = input.id;
  if (!addKeys.has(id)) addKeys.set(id, newKey());
  const btn = $('[data-postit-add-btn]', form);
  btn.disabled = true;
  const r = await apiPost('/postits', { text, idempotency_key: addKeys.get(id), record_mode: recordMode() });
  btn.disabled = false;
  const body = r.body || {};
  if (r.ok && body.result === 'added') {
    addKeys.delete(id);
    input.value = '';
    result(container, 'ok', body.message);
  } else {
    result(container, body.error === 'not_listening' ? 'warn' : 'bad', body.message || 'Post-it not added');
  }
  await reloadPostits();
}

async function onMove(btn, action) {
  const container = btn.closest('[data-postits]');
  const id = btn.dataset.postitBin || btn.dataset.postitRestore;
  if (!btn.dataset.key) btn.dataset.key = newKey();   // one key per shown row, reused on retry
  btn.disabled = true;
  const r = await apiPost(`/postits/${id}/${action}`, { idempotency_key: btn.dataset.key, record_mode: recordMode() });
  const body = r.body || {};
  result(container, KINDS[body.result] || (body.error === 'not_listening' ? 'warn' : 'bad'), body.message || 'Nothing was changed');
  if (r.ok) await reloadPostits(); else btn.disabled = false;
}

export function initPostits(p) {
  page = p;
  window.addEventListener('focus', () => { reloadLists(); });
  document.addEventListener('submit', (e) => {
    const form = e.target.closest('[data-postit-add]');
    if (!form) return;
    e.preventDefault();
    onAdd(form);
  });
  document.addEventListener('input', (e) => {
    const input = e.target.closest('[data-postit-input]');
    if (input) addKeys.delete(input.id);
  });
  document.addEventListener('click', (e) => {
    const bin = e.target.closest('[data-postit-bin]');
    if (bin) { onMove(bin, 'bin'); return; }
    const restore = e.target.closest('[data-postit-restore]');
    if (restore) onMove(restore, 'restore');
  });
}
