// The Board (Guild 1.1 slice 3, spec §5.1): relaxed post-its with Done,
// labels, links and photos; your order by drag, the ‹ › buttons or
// Alt+Arrow; Trash with Restore and a confirmed Empty. Drawn from the same
// answer GET /api/v1/board gives; every write goes through the API with the
// guards (CSRF, record mode, one idempotency key per change, the note's
// version, the board's order and trash revisions). Only the view (Show,
// label, search) stays in the page. No model call.
import { $, $$, el, announce, localTime } from './dom.js';
import { inspectText, attachCounter } from './board_text.js';
import { apiGet, apiPost, apiUpload, recordMode, RECORD_UNKNOWN_TEXT } from './api.js';
import { live } from './state.js';
import { kindOf, newKey } from './actions.js';
import { W as BOARD_W, NOTE_W, NOTE_H, STEP, layoutAll, boardHeight, nearestFree, stepFrom, movesFor, snap, clamp, widthOf, heightOf, isFree, MIN_W, MIN_H, MAX_W, MAX_H } from './board_layout.js';

let page;
let data;                         // { status, active, done, trash, order_rev, trash_rev, counts, labels }
let editing = null;               // the note being edited in place: { id, value, conflict }
let creating = null;              // a new note being typed straight into the Board: { value }
let syncRev = null;               // the Board's change signal as of the last read (GET /board/rev)
let syncPending = false;          // a change was seen while a note was being edited; shown when that finishes
let syncBusy = false;
let geometryBusy = false;
const channel = 'BroadcastChannel' in window ? new BroadcastChannel('guild-board') : null;   // a hint only; the server is the truth
const POLL_MS = 2000;
let syncFailures = 0;
const ui = { show: 'active', label: '', q: '' };
const keys = new Map();
const opened = new Set();      // long notes the owner expanded; kept across redraws
const SETTLED = ['added', 'done', 'undone', 'labelled', 'linked', 'moved', 'emptied', 'restored', 'binned', 'edited', 'unchanged', 'occupied',
  'conflict', 'idempotency_mismatch', 'invalid', 'not_found', 'in_trash', 'not_a_note', 'already_done', 'already_active',
  'already_binned', 'asset_trashed', 'gone', 'duplicate', 'trashed'];
const OK = ['added', 'done', 'undone', 'labelled', 'linked', 'binned', 'restored', 'moved', 'emptied', 'edited', 'unchanged'];
const LABEL_NAMES = { decide: 'Decide', blocked: 'Blocked', remember: 'Remember', followup: 'Follow-up', idea: 'Idea', fyi: 'FYI' };

// ── CoS's wall (Review 06, slice 3): the CoS Board only (page.wall). The same notes and every hook, drawn as a whiteboard:
// stickies by their label (no label words), a pinned clipping for an FYI with a link, a Polaroid for a photo, CoS's mark on
// what CoS put up; the grid only (no free placement); a note opens on the right. Guild's Board never sets page.wall.
let wallOpen = null;              // the note opened on the right, on the wall
const wall = () => !!(page && page.wall);
const URL_RE = /\bhttps?:\/\/[^\s<>"']+/i;
function wallLook(p) {
  if (p.kind === 'photo') return 'polaroid';
  if (p.label === 'blocked') return 'worry';
  if (p.label === 'fyi' && (p.item_ref || URL_RE.test(p.text))) return 'clipping';
  if (p.label === 'decide' || p.label === 'followup') return 'marked';
  return 'sticky';
}
const COS_NAME = /^(cos|chief[ _]of[ _]staff)$/i;
const fromCos = (p) => p.author_kind === 'agent' && (COS_NAME.test(String(p.author || '')) || COS_NAME.test(String(p.author_label || '')));
function wallSource(p) {
  if (p.item_ref) return `from the Build Log · #${p.item_ref}${p.item && p.item.title ? ` ${p.item.title}` : ''}`;
  const m = URL_RE.exec(p.text);
  if (!m) return '';
  try { return new URL(m[0]).hostname.replace(/^www\./, ''); } catch (e) { return 'a link'; }
}

function h(tag, attrs, ...kids) { const n = el(tag, attrs); n.append(...kids); return n; }
function keyFor(name) { if (!keys.has(name)) keys.set(name, newKey()); return keys.get(name); }
function settle(name, code) { if (SETTLED.includes(code)) keys.delete(name); }

function say(text, kind = 'ok', where = $('[data-bd-result]')) {
  where.hidden = !text;
  where.dataset.kind = kind;
  where.textContent = text || '';
  if (text) announce(text);
  if (text && kind === 'ok') window.setTimeout(() => { if (where.textContent === text) where.hidden = true; }, 2200);       // a confirmation is not a notice: it fades (the live region already said it)
}

const list = (name) => (data && Array.isArray(data[name]) ? data[name] : []);
const allNotes = () => list('active').concat(list('done'), list('trash'));
const filtersOn = () => !!(ui.label || ui.q);

function matches(p) {
  if (ui.label && (p.label || '') !== ui.label) return false;
  if (ui.q) {
    const hay = `${p.text} ${p.item_ref ? `#${p.item_ref}` : ''} ${p.item && p.item.title ? p.item.title : ''} ${p.label || ''}`;
    if (!hay.toLowerCase().includes(ui.q.toLowerCase())) return false;
  }
  return true;
}

function linkLine(p) {
  if (!p.item_ref) return null;
  const a = el('a', { class: 'bd-link', href: `${page.urls.build_log}?item=${p.item_ref}`, 'data-bd-item': p.item_ref }, `#${p.item_ref}`);
  const line = h('p', { class: 'bd-linkline' }, a);
  if (p.item && p.item.title) line.append(` ${p.item.title}`);
  else line.append(el('span', { class: 'bd-link-unknown' }, ` · ${p.item ? p.item.status : 'unknown'}`));
  return line;
}

// Every action on a note lives in one small ⋯ menu (10 Oct, Robert: no buttons inside the Post-it; 11 Oct: "a lot of options on a post-it"). The note itself is
// edited by clicking its text and moved by dragging; the menu holds only Edit and Discard (plus the order buttons, until placement replaces them). Labels, links
// and Done are no longer set from here: notes that already have them keep them, and Done notes are still reachable under the Done filter.
function menuBox(p, n, shown, reorderable) {
  const photo = p.kind === 'photo';
  const box = el('details', { class: 'bd-edit' });
  box.append(el('summary', { title: 'Actions for this note', 'aria-label': `Actions for this ${photo ? 'photo' : 'note'}` }, '⋯'));
  const body = el('div', { class: 'bd-edit-body' });
  const btn = (label, attrs, cls = '') => el('button', { type: 'button', class: `bd-btn ${cls}`.trim(), ...attrs }, label);
  if (ui.show === 'active') {
    if (!photo) body.append(btn('Edit text', { 'data-bd-edit-open': p.id, 'aria-label': `Edit "${p.text.slice(0, 30)}"` }));
    if (reorderable) {
    const prev = btn('Move earlier ‹', { 'data-bd-move': 'prev', 'data-id': p.id, 'aria-label': `Move "${p.text.slice(0, 30)}" earlier` }, 'bd-move');
    const next = btn('Move later ›', { 'data-bd-move': 'next', 'data-id': p.id, 'aria-label': `Move "${p.text.slice(0, 30)}" later` }, 'bd-move');
    prev.disabled = n === 0; next.disabled = n === shown.length - 1;
    body.append(prev, next);
    }
    body.append(btn(photo ? 'Remove' : 'Discard', { 'data-bd-bin': p.id }, 'bd-discard'));
  } else if (ui.show === 'done') {
    body.append(btn('Undone', { 'data-bd-undone': p.id }), btn('Discard', { 'data-bd-bin': p.id }, 'bd-discard'));
  } else {
    body.append(btn('Restore', { 'data-bd-restore': p.id }));
  }
  box.append(body);
  return box;
}


// ── a new note, typed straight into the Board ───────────────────────────────
function newCard() {
  const li = el('li', { class: 'bd-note bd-new', 'data-bd-new-card': '' });
  const ta = el('textarea', { class: 'bd-edit-text', 'data-bd-new-field': '',
    'aria-label': 'New note', placeholder: 'Type or paste…', rows: '3', title: 'Leaving the note saves it · an empty note is never kept · Esc cancels' });
  ta.value = creating.value;
  const counter = attachCounter(ta);
  ta.addEventListener('input', () => { creating.value = ta.value; });
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); stopCreating(); }
    else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); saveNew(); }
  });
  li.addEventListener('focusout', () => setTimeout(() => {
    if (li.isConnected && creating && !li.contains(document.activeElement)) { if (creating.value.trim()) saveNew(); else stopCreating(); }   // leaving it saves it; an empty note is never kept
  }, 0));
  li.append(ta, counter);
  return li;
}

async function startCreating() {
  if (creating) { const f = $('[data-bd-new-field]'); if (f) f.focus(); return; }
  if (editing) { if (editing.conflict) { say('Finish the note that changed in another window first.', 'warn'); return; } await leaveEdit(editing.id); if (editing) return; }
  ui.show = 'active';
  creating = { value: '' };
  render();
  const f = $('[data-bd-new-field]'); if (f) f.focus();
}

async function stopCreating() {
  creating = null;
  render();
  if (syncPending) { syncPending = false; await syncNow(true); }
}

async function saveNew() {
  if (!creating) return;
  const draft = inspectText(creating.value);
  if (!draft.valid && draft.code !== "text_empty") { say("Shorten this note to 280 characters before saving. Your full draft is kept.", "warn"); return; }
  const text = draft.normalized;
  if (!draft.text) { await stopCreating(); return; }
  const body = { text, label: null, item_ref: null };
  const name = `add:${JSON.stringify(body)}`;
  const mine = creating.value;
  creating = null;                                                    // the blur that follows must not save it twice
  const r = await apiPost('/postits', { ...body, idempotency_key: keyFor(name), record_mode: recordMode() });
  const res = r.body || {};
  const code = res.result || res.error || 'failed';
  settle(name, code);
  say(res.message || 'Nothing was added', code === 'added' ? 'ok' : kindOf(code));
  if (code !== 'added') {
    if (!creating && !editing) { creating = { value: mine }; render(); const f = $('[data-bd-new-field]'); if (f) f.focus(); return; }   // their words are kept
    say(`The new note could not be saved, and you have moved on. Your words: "${mine}"`, 'warn');                         // never over a newer draft
    return;
  }
  await reload();
  announceChange();
}

// ── editing a note in place ─────────────────────────────────────────────────
function textEditor(p) {
  const box = el('div', { class: 'bd-texteditor' });
  const ta = el('textarea', { class: 'bd-edit-text', 'data-bd-edit-field': p.id,
    'aria-label': 'Note text', rows: '3', title: 'Leaving the note saves it · Esc cancels' });
  ta.value = editing.value;
  const counter = attachCounter(ta);
  ta.addEventListener('input', () => { editing.value = ta.value; });
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); stopEditing(false); }
    else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); saveEdit(p.id); }
  });
  box.append(ta, counter);
  if (editing.conflict) {                                 // the one time buttons are needed: two versions exist, so the person chooses
    box.append(el('p', { class: 'bd-conflict', role: 'status' }, 'This note changed in another window. Your text is kept here.'),
      h('div', { class: 'bd-edit-row' },
        el('button', { type: 'button', class: 'bd-btn', 'data-bd-edit-save': p.id }, 'Replace with mine'),
        el('button', { type: 'button', class: 'bd-btn', 'data-bd-edit-cancel': p.id }, 'Keep the current note')));
  } else {
    box.addEventListener('focusout', () => setTimeout(() => { if (box.isConnected && editing && editing.id === p.id && !editing.conflict && !box.contains(document.activeElement)) leaveEdit(p.id); }, 0));
  }
  return box;
}

async function startEditing(id) {
  const p0 = noteById(id);
  if (!p0 || p0.kind === 'photo' || p0.state === 'binned') return;
  if (editing && editing.id !== id) { if (editing.conflict) return; await leaveEdit(editing.id); if (editing) return; }   // one edit at a time: the other is finished first
  const p = noteById(id);
  if (!p) return;
  editing = { id, value: p.text, conflict: false };
  render();
  const ta = $(`[data-bd-edit-field="${id}"]`);
  if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
}

async function stopEditing(saved, inst = null) {
  if (inst && editing !== inst) { if (syncPending && !editing && !creating) { syncPending = false; await syncNow(true); } return; }     // a newer editor exists: an older save never clears it
  editing = null;
  render();
  if (syncPending) { syncPending = false; await syncNow(true); }
}

// Leaving the note (clicking elsewhere) saves a changed note, leaves an unchanged one alone, and never turns a note into an empty one.
let leaving = null;                                  // one leave at a time: the blur and the click that caused it share the same save
function leaveEdit(id) {
  if (leaving) return leaving;
  if (!editing || editing.id !== id) return Promise.resolve();
  leaving = (async () => {
    if (!editing.value.trim()) { await stopEditing(false); return; }
    await saveEdit(id);
  })().finally(() => { leaving = null; });
  return leaving;
}

function saveEdit(id) {                                              // the keyboard save, the blur and the click all come through here: one save per editor at a time
  if (!editing || editing.id !== id) return Promise.resolve();
  const inst = editing;
  if (inst.saving) return inst.saving;
  inst.saving = saveEditNow(id, inst).finally(() => { inst.saving = null; });
  return inst.saving;
}

async function saveEditNow(id, inst) {                               // this editor, whatever happens to the page while the save is away
  const p = noteById(id);
  const draft = inspectText(inst.value);
  if (!draft.valid && draft.code !== "text_empty") { say("Shorten this note to 280 characters before saving. Your full draft is kept.", "warn"); return; }
  const text = draft.normalized;
  const where = $(`[data-bd-note-result="${id}"]`);
  if (!p) { await stopEditing(false, inst); return; }
  if (!draft.text) { say('A note cannot be empty. Use Discard to remove it.', 'warn', where || $('[data-bd-result]')); return; }
  if (draft.text === p.text) { await stopEditing(false, inst); return; }
  const mine = inst.value;
  const { code } = await write(`/postits/${id}/text`, { text, version: p.version }, `text:${id}:${p.version}:${text}`, id, { keepEditing: true });
  if ((code === 'edited' || code === 'unchanged') && editing === inst && inst.value.trim() !== text) { render(); return; }   // more was typed in this editor while the save was away: it stays open with those words
  if (code === 'edited' || code === 'unchanged') await stopEditing(true, inst);
  else if (editing === inst) {
    editing = { id, value: mine, conflict: code === 'conflict' };      // their words are never lost: kept in the editor
    render();
    const ta = $(`[data-bd-edit-field="${id}"]`); if (ta) ta.focus();
  } else say('That note could not be saved just now, and you have moved on. Its text is kept in the note as it was; open it to try again.', 'warn');
}

// ── free placement (11 Oct): beside a wide window the board is a surface and a note sits where it was put; narrower, or filtered, it is the list again ──
const FREE_MIN_PX = 900;                  // the board's drawn width below which the page keeps the list (a placement is never rewritten by a resize)
let free = null;                          // { scale, layout: Map(id -> {x, y, placed}), height } while the board is drawn as a surface
const anyPlaced = () => list('active').some((p) => Number.isInteger(p.pos_x) && Number.isInteger(p.pos_y));

function corkWidth() { const c = $('.bd-cork'); return c ? c.clientWidth : 0; }

function freeWanted() {
  return !wall() && !!(data && data.status === 'ok' && ui.show === 'active' && !filtersOn() && corkWidth() >= FREE_MIN_PX);
}

function applyFree(grid, items) {
  const cork = grid.closest('.bd-cork');
  const layout = layoutAll(items);
  const scale = clamp(corkWidth() / BOARD_W, FREE_MIN_PX / BOARD_W, 1.5);
  free = { scale, layout, height: boardHeight(layout) };
  cork.dataset.free = 'true';
  grid.classList.add('bd-free');
  grid.style.setProperty('--bd-scale', String(scale));
  cork.style.height = `${Math.ceil(free.height * scale)}px`;
  return layout;
}

function leaveFree(grid) {
  free = null;
  const cork = grid.closest('.bd-cork');
  if (cork) { delete cork.dataset.free; cork.style.height = ''; }
  grid.classList.remove('bd-free');
  grid.style.removeProperty('--bd-scale');
}

function placeAt(li, pos) { li.style.setProperty("--note-lines", String(Math.max(2,Math.floor((heightOf(pos)-70)/21)))); li.style.setProperty("--card-w", `${widthOf(pos)}px`); li.style.setProperty("--card-h", `${heightOf(pos)}px`); li.style.setProperty('--x', String(pos.x)); li.style.setProperty('--y', String(pos.y)); }

function card(p, n, shown) {
  const photo = p.kind === 'photo';
  const size = photo ? 'm' : (p.text.length > 150 || p.text.split('\n').length > 4 ? 'l' : p.text.length > 48 || p.text.includes('\n') ? 'm' : 's');
  const li = el('li', { class: `bd-note${photo ? ' bd-photo' : ''}`, 'data-bd-note': p.id, 'data-state': p.state, 'data-size': size,
    'data-label': p.label || '', tabindex: '0', 'aria-label': `${photo ? 'Photo' : 'Note'}: ${p.text}` });
  if (p.size_w != null) { li.dataset.resized = "true"; li.style.setProperty("--card-w", `${p.size_w}px`); li.style.setProperty("--card-h", `${p.size_h}px`); }
  const reorderable = ui.show === 'active' && !filtersOn() && !free && !anyPlaced();       // list order is changed by dragging only while no note has been placed on the board
  if (reorderable) li.draggable = true;
  if (!photo && !wall()) li.append(el('p', { class: 'bd-kicker' }, p.label ? (LABEL_NAMES[p.label] || p.label) : ''));
  if (wall()) {
    li.dataset.look = wallLook(p);
    if (fromCos(p)) { li.dataset.author = 'cos'; li.append(el('img', { class: 'wall-mark', src: page.urls.cos_mark, alt: 'Put up by CoS', title: 'Put up by CoS', draggable: 'false' })); }
  }
  if (photo) {
    // The picture moves with its card; the browser's native image drag would cancel our pointer gesture.
    li.append(el('img', { src: p.thumb_url, alt: p.text === 'Photo' ? 'A photo on the Board' : p.text, loading: 'lazy', draggable: 'false' }));
  }
  if (editing && editing.id === p.id && !photo) li.append(textEditor(p));
  else {
    const t = el('p', { class: 'bd-text' }, p.text);
    if (!photo && ui.show !== 'trash') { t.dataset.bdEditText = p.id; t.title = 'Click to edit'; }
    li.append(t);
  }
  {
    // Every note and photo caption can open in place. The button is shown only while the text is really
    // cut off (fitMore below), so a short note has none, and a narrow screen that wraps more lines gets one.
    const open = opened.has(p.id);
    if (open) li.dataset.open = 'true';
    li.append(el('button', { type: 'button', class: 'bd-more', 'data-bd-more': p.id, 'aria-expanded': String(open), hidden: !open },
      open ? 'Less' : 'More'));
  }
  const link = wall() ? null : linkLine(p);
  if (link) li.append(link);
  if (wall() && li.dataset.look === 'clipping') {
    const t = li.querySelector('.bd-text:not([data-bd-edit-field])');
    const title = p.text.replace(URL_RE, '').replace(/[\s:·-]+$/, '').trim();
    if (t && title && !(editing && editing.id === p.id)) t.textContent = title;    // the clipping's title; the link is its source line
    const src = wallSource(p);
    const line = el('p', { class: 'wall-source' });
    if (p.item_ref) line.append(el('a', { href: `${page.urls.build_log}?item=${p.item_ref}`, 'data-bd-item': p.item_ref, target: '_top' }, src));
    else { const m = URL_RE.exec(p.text); line.append(m && /^https?:/i.test(m[0]) ? el('a', { href: m[0], target: '_blank', rel: 'noopener noreferrer' }, src) : src); }
    li.append(line);
  }
  const when = ui.show === 'trash' ? p.binned_at : (ui.show === 'done' ? p.done_at : p.created_at);
  li.append(el('p', { class: 'bd-meta' }, `${p.author_label} · ${ui.show === 'trash' ? 'binned ' : ui.show === 'done' ? 'done ' : ''}${localTime(when)}`));
  const acts = el('div', { class: 'bd-acts' });
  acts.append(menuBox(p, n, shown, reorderable));
  li.append(acts);
  if (free && ui.show === 'active' && !editing) li.append(el('button', { type: 'button', class: 'bd-resize', 'data-bd-resize': p.id, 'aria-label': `Resize ${photo ? 'picture' : 'note'}; arrow keys change size`, title: 'Drag to resize; arrow keys when focused' }));
  li.append(el('p', { class: 'bd-result', 'data-bd-note-result': p.id, role: 'status', hidden: true }));
  return li;
}

// Show More only where the text is actually cut off by the preview, or where it was opened (then it says Less).
function fitMore() {
  for (const li of $$('[data-bd-note]')) {
    const text = li.querySelector('.bd-text');
    const btn = li.querySelector('[data-bd-more]');
    if (!text || !btn) continue;
    if (li.dataset.open === 'true') { btn.hidden = false; continue; }
    // Cut off? Compare the preview's height with the full text's (scrollHeight alone is off by a pixel or two on some fonts).
    const preview = text.offsetHeight;
    li.dataset.open = 'true';
    const full = text.offsetHeight;
    delete li.dataset.open;
    btn.hidden = !(full > preview + 2);
  }
}
let fitTimer = null;
window.addEventListener('resize', () => {
  if (fitTimer) window.cancelAnimationFrame(fitTimer);
  fitTimer = window.requestAnimationFrame(fitMore);
});

// A note opened on the wall: the same card (so editing, ⋯ and every hook work as they do on the wall), held in a sheet on the
// right (full screen on a phone, with "← Board"), with when it went up and who put it up.
function wallSheet(p, li) {
  li.dataset.wallOpen = 'true';
  li.removeAttribute('draggable');
  const sheet = el('li', { class: 'wall-sheet', 'data-wall-sheet': p.id, role: 'dialog', 'aria-label': 'A note, up close' });
  const bar = el('div', { class: 'wall-sheet-bar' });
  bar.append(el('button', { type: 'button', class: 'wall-back', 'data-wall-close': '' }, '← Board'),
    el('span', { class: 'wall-sheet-title' }, ui.show === 'active' ? 'On the wall' : ui.show === 'done' ? 'Taken down: done' : 'Taken down: in the bin'),
    el('button', { type: 'button', class: 'wall-x', 'data-wall-close': '', 'aria-label': 'Close' }, '✕'));
  const held = el('ol', { class: 'wall-sheet-note' });
  held.append(li);
  const since = el('div', { class: 'wall-sheet-since' });
  since.append(el('p', { class: 'wall-sheet-h' }, 'Since then'));
  const trail = (when, words) => { const t = el('p', { class: 'wall-trail' }); t.append(el('span', {}, localTime(when)), ` ${words}`); since.append(t); };
  trail(p.created_at, `Put up by ${fromCos(p) ? 'CoS' : (p.author_label || 'you')}.`);
  if (p.restored_at) trail(p.restored_at, 'Put back on the wall.');
  if (p.done_at) trail(p.done_at, 'Taken down: done.');
  if (p.binned_at) trail(p.binned_at, 'Taken down to the bin.');
  const later = el('p', { class: 'wall-sheet-later' });
  later.append(el('button', { type: 'button', class: 'wall-btn', disabled: true, title: 'later' }, 'Talk it over with CoS'), el('span', { class: 'wall-later' }, 'later'));
  sheet.append(bar, held, since, later);
  return sheet;
}

function closeWallNote() {
  if (wallOpen === null) return;
  const id = wallOpen;
  wallOpen = null;
  render();
  const again = $(`[data-bd-note="${id}"]`);
  if (again) again.focus({ preventScroll: false });
}

function bindWall(grid) {
  const interactive = (t) => t.closest('button, a, summary, details, textarea, input, select, [data-bd-edit-text-active]');
  grid.addEventListener('click', (e) => {               // capture: a note on the wall opens first; inside the sheet it edits as before
    if (e.target.closest('[data-wall-close]')) { e.stopPropagation(); closeWallNote(); return; }
    const li = e.target.closest('[data-bd-note]');
    if (!li || li.dataset.wallOpen === 'true' || interactive(e.target)) return;
    e.stopPropagation();
    wallOpen = Number(li.dataset.bdNote);
    render();
    const sheet = $('[data-wall-sheet]');
    if (sheet) sheet.querySelector('[data-wall-close]').focus({ preventScroll: true });
  }, true);
  grid.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.altKey || !e.target.matches('[data-bd-note]') || e.target.dataset.wallOpen === 'true') return;
    e.preventDefault();
    wallOpen = Number(e.target.dataset.bdNote);
    render();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || wallOpen === null || editing || creating || e.target.closest('textarea') || $$('.bd-edit[open]').length) return;
    closeWallNote();
  }, true);
  const taken = $('[data-wall-taken]');
  if (taken) taken.addEventListener('click', () => { ui.show = ui.show === 'active' ? 'done' : 'active'; wallOpen = null; render(); });
}

function render() {
  const act = document.activeElement;                  // keep the person's place in a note they are typing in across a redraw
  let keep = null;
  if (act && act.matches && (act.matches('[data-bd-new-field]') || act.matches('[data-bd-edit-field]'))) {
    keep = { sel: act.matches('[data-bd-new-field]') ? '[data-bd-new-field]' : `[data-bd-edit-field="${act.dataset.bdEditField}"]`, s: act.selectionStart, e: act.selectionEnd };
  } else if (act && act.matches && act.matches('[data-bd-resize], [data-bd-note]')) {
    // Layout/image loading and background refresh may redraw between key presses.
    keep = { sel: act.matches('[data-bd-resize]') ? `[data-bd-resize="${act.dataset.bdResize}"]` : `[data-bd-note="${act.dataset.bdNote}"]` };
  }
  drawBoard();
  if (keep) { const f = $(keep.sel); if (f && document.activeElement !== f) { f.focus({ preventScroll: true }); try { f.setSelectionRange(keep.s, keep.e); } catch (e) { /* no selection to restore */ } } }
}

function drawBoard() {
  const known = data && data.status === 'ok';
  const counts = (data && data.counts) || {};
  for (const button of $$('[data-bd-view]')) {
    const view = button.dataset.bdView;
    const label = (wall() ? { active: '← The wall', done: 'Done', trash: 'Bin' } : { active: 'Active', done: 'Done', trash: 'Bin' })[view];
    button.textContent = `${label} ${known ? counts[view] : '?'}`;
    button.setAttribute('aria-pressed', String(ui.show === view));
  }
  const unk = $('[data-bd-unknown]'); if (unk) unk.hidden = known;
  if (wall()) {
    const taken = $('[data-wall-taken-count]');
    if (taken) taken.textContent = known ? `(${(counts.done || 0) + (counts.trash || 0)})` : '';
    document.body.dataset.wallShow = ui.show;
  }
  let shown = list(ui.show).filter(matches);
  if (wallOpen !== null && !shown.some((p) => p.id === wallOpen)) wallOpen = null;     // the opened note left this view
  if (editing && !shown.some((p) => p.id === editing.id)) editing = null;     // the edited note is no longer on screen
  const grid = $('[data-bd-grid]');
  const surface = freeWanted();
  let layout = null;
  if (surface) layout = applyFree(grid, [...(creating ? [{ id: 0 }] : []), ...shown]);
  else {
    leaveFree(grid);
    // A narrower window shows the same arrangement as a list: by where the notes sit (top to bottom, left to right), never rewriting any position.
    if (ui.show === 'active' && !filtersOn() && anyPlaced()) {
      const lay = layoutAll(shown);
      shown = [...shown].sort((a, b) => (lay.get(a.id).y - lay.get(b.id).y) || (lay.get(a.id).x - lay.get(b.id).x) || (b.id - a.id));
    }
  }
  const cards = shown.map((p, n) => card(p, n, shown));
  const fresh = creating ? newCard() : null;
  if (layout) {
    shown.forEach((p, i) => placeAt(cards[i], layout.get(p.id)));
    if (fresh) placeAt(fresh, layout.get(0));
    cards.sort((a, b) => (layout.get(Number(a.dataset.bdNote)).y - layout.get(Number(b.dataset.bdNote)).y) || (layout.get(Number(a.dataset.bdNote)).x - layout.get(Number(b.dataset.bdNote)).x));
  }
  if (wall()) {
    document.body.toggleAttribute('data-wall-open', wallOpen !== null);
    const at = shown.findIndex((p) => p.id === wallOpen);
    if (at >= 0) cards[at] = wallSheet(shown[at], cards[at]);
  }
  grid.replaceChildren(...(fresh ? [fresh] : []), ...cards);
  fitMore();
  const note = $('[data-bd-state-note]');
  if (!known) note.textContent = '';
  else if (ui.show === 'active') note.textContent = wall() ? '' : filtersOn() ? 'Clear the label and search to change the order.' : surface ? 'Drag a note anywhere there is room, or use the arrow keys on a selected note.' : anyPlaced() ? 'The notes keep the places you gave them on a wider screen; here they are listed in that order.' : 'Drag a note to change the order.';
  else if (ui.show === 'done') note.textContent = 'Done notes have left Active. Undone puts one back.';
  else note.textContent = 'Discarded notes stay here until you empty the Trash. Restore puts one back as it was. Emptying never changes linked work or library images.';
  if (known && !shown.length && !creating) {
    if (ui.show === 'active' && !filtersOn()) {                // a truly empty Board: a playful example, plainly NOT a saved note (never shown while loading or unreadable)
      const li = el('li', { class: 'bd-empty bd-example' });
      const note = h('div', { class: 'bd-note bd-example-note', 'data-bd-example': '', role: 'img', 'aria-label': 'An example Post-it, not a saved note' },
        el('p', { class: 'bd-kicker' }, 'Example · not a saved note'),
        el('p', { class: 'bd-text bd-example-text' }, 'Paste a link, jot a thought, doodle an idea ✏️'),
        h('p', { class: 'bd-example-art', 'aria-hidden': 'true' }, '🎨  ✦  ☕'));
      li.append(note, el('p', { class: 'bd-empty-line' }, 'No notes on the Board yet. Tap ＋ to add one: type or paste straight into it.'));
      grid.append(li);
    } else grid.append(el('li', { class: 'bd-empty' }, ui.show === 'trash' ? 'The Trash is empty.' : filtersOn() ? 'No notes match.' : ui.show === 'done' ? 'Nothing done yet.' : 'No notes on the Board.'));
  }
  if (known && ui.show === 'trash' && list('trash').length) {
    const empty = el('li', { class: 'bd-empty' });
    empty.append(el('button', { type: 'button', class: 'btn', 'data-bd-empty': true }, `Empty trash (${list('trash').length})`));
    grid.prepend(empty);
  }
}

async function peekRev() {
  const r = await apiGet('/board/rev');
  return r.ok && r.body && r.body.status === 'ok' ? r.body.rev : null;
}

// A refresh that fails never wipes notes that were already shown (10 Oct, Dev: a stalled read blanked the main Board while the pop-out still had the notes,
// and the page then believed it was up to date and never looked again). The last good notes stay, the page says so, and it tries again on the next beat.
let needsRetry = false;
async function reload() {
  const rev = await peekRev();
  const r = await apiGet('/board');
  const body = r.body || {};
  if (r.ok && body.status === 'ok') { data = { ...data, ...body, status: 'ok' }; if (rev != null) syncRev = rev; needsRetry = false; stamp('Up to date'); }
  else {
    needsRetry = true;
    if (data && data.status === 'ok') stamp(r.status === 0 ? 'Cannot reach the server · showing what was last loaded' : 'Could not refresh just now · showing what was last loaded · trying again', true);
    else if (r.status !== 0) data = { ...data, status: 'unknown', active: null, done: null, trash: null };
    else stamp('Cannot reach the server', true);
  }
  render();
}

// ── keeping a second window current (the Board and its pop-out) ─────────────
function announceChange() { if (channel) channel.postMessage({ t: 'changed' }); }

function stamp(text, warn = false) {
  const el_ = $('[data-bd-sync]');
  if (!el_) return;
  const t = new Date();
  el_.dataset.kind = warn ? 'warn' : 'ok';
  el_.textContent = warn ? text : `${text} · ${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`;
}

async function syncNow(force = false) {
  if (syncBusy) return;
  if (editing || creating || geometryBusy) { syncPending = true; return; }
  syncBusy = true;
  try {
    const rev = await peekRev();
    if (rev == null) { syncFailures++; stamp('Cannot reach the server · showing what was last loaded', true); return; }
    syncFailures = 0;
    if (force || needsRetry || rev !== syncRev) await reload(); else stamp('Up to date');
  } finally { syncBusy = false; }
}

function startSync() {
  if (channel) channel.onmessage = () => { syncNow(); };
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') syncNow(); });
  window.addEventListener('focus', () => { syncNow(); });
  document.addEventListener('minimoi:board-changed', () => { syncNow(); });
  const poll = async () => {
    if (document.visibilityState === 'visible') await syncNow();
    window.setTimeout(poll, Math.min(30000, POLL_MS * (2 ** Math.min(syncFailures, 4))));
  };
  window.setTimeout(poll, POLL_MS);
}

// ── Pop out: the same Board in its own window ───────────────────────────────
function popOut() {
  const url = `${page.urls.board}/popout`.replace('//', '/');
  const small = window.matchMedia('(max-width: 640px)').matches;              // a phone opens it as a tab
  let w = null;
  try { w = window.open(url, page.storage_ns === 'cos.board' ? 'cos-board-popout' : 'guild-board-popout', small ? '' : 'popup=yes,width=440,height=720,left=40,top=60'); } catch (e) { w = null; }
  const out = $('[data-bd-result]');
  if (!w) {
    out.hidden = false; out.dataset.kind = 'warn';
    out.replaceChildren('Your browser blocked the pop-out. Allow pop-ups for this site and try again, or ',
      el('a', { href: url, target: '_blank', rel: 'noopener' }, 'open the Board in its own tab'), '.');
    announce('The pop-out was blocked by the browser. A link to open it in a tab is shown.');
    return;
  }
  try { w.focus(); } catch (e) { /* ignore */ }
  say('The Board is open in its own window. It is the same Board: change it in either place.', 'ok');
}

function noteById(id) { return allNotes().find((p) => p.id === id); }

async function write(path, body, name, where, opts = {}) {
  const r = await apiPost(path, { ...body, idempotency_key: keyFor(name), record_mode: recordMode() });
  const res = r.body || {};
  const code = res.result || res.error || 'failed';
  settle(name, code);
  await reload();
  if (r.ok) announceChange();
  const out = (where && $(`[data-bd-note-result="${where}"]`)) || $('[data-bd-result]');
  say(res.message || 'Nothing was changed', OK.includes(code) ? 'ok' : kindOf(code), out);
  return { code, body: res };
}

async function move(id, dir) {
  const shown = list('active');
  const i = shown.findIndex((p) => p.id === id);
  const j = dir === 'prev' ? i - 1 : i + 1;
  if (i < 0 || j < 0 || j >= shown.length) return;
  const body = dir === 'prev' ? { id, before_id: shown[j].id } : { id, after_id: shown[j].id };
  await reorder(body);
  const again = $(`[data-bd-note="${id}"]`);
  if (again) again.focus();
}

async function reorder(body) {
  const name = `reorder:${JSON.stringify(body)}:${data.order_rev}`;
  const { code } = await write('/postits/reorder', { ...body, expect_order_rev: data.order_rev }, name);
  if (code === 'conflict') say('The order changed elsewhere. Here is the current order; move it again if you still want to.', 'warn');
}

// Place a note: the first move saves where every note is drawn (so nothing jumps), later ones only the moved note. The server refuses an overlap and a stale arrangement.
async function commitPlace(id, to) {
  const moves = movesFor(id, to, free.layout, anyPlaced());
  const name = `place:${data.order_rev}:${JSON.stringify(moves)}`;
  const { code } = await write('/postits/layout', { moves, expect_order_rev: data.order_rev }, name);
  if (code === 'conflict') say('The board changed in another window. Here it is now; place the note again if you still want to.', 'warn');
  else if (code === 'occupied') say('That spot is taken by another note. Nothing moved.', 'warn');
  const again = $(`[data-bd-note="${id}"]`);
  if (again && !document.activeElement.closest('[data-bd-note]')) again.focus({ preventScroll: true });
}

function othersThan(id) {
  return [...free.layout.entries()].filter(([k]) => k !== id && k !== 0).map(([, p]) => ({ ...p }));
}

function bindPlace(grid) {
  let drag = null;
  let suppressClick = false;
  const interactive = (t) => t.closest('button, a, summary, details, textarea, input, select, [data-bd-edit-text-active]');
  grid.addEventListener('pointerdown', (e) => {
    if (!free || geometryBusy || e.button !== 0 || (e.pointerType === 'touch' && false)) return;
    const li = e.target.closest('[data-bd-note]');
    const resizing = !!e.target.closest("[data-bd-resize]");
    if (!li || (!resizing && interactive(e.target)) || (editing && editing.id === Number(li.dataset.bdNote))) return;
    const id = Number(li.dataset.bdNote);
    const pos = free.layout.get(id);
    if (!pos) return;
    drag = { id, li, pos, sx: e.clientX, sy: e.clientY, moved: false, resizing, pid: e.pointerId };
  });
  grid.addEventListener('pointermove', (e) => {
    if (!drag || e.pointerId !== drag.pid) return;
    const dx = (e.clientX - drag.sx) / free.scale;
    const dy = (e.clientY - drag.sy) / free.scale;
    if (!drag.moved && Math.hypot(dx, dy) * free.scale < 5) return;
    if (!drag.moved) { opened.delete(drag.id); delete drag.li.dataset.open; drag.moved = true; geometryBusy = true; drag.li.dataset.dragging = 'true'; try { drag.li.setPointerCapture(e.pointerId); } catch (err) { /* the drag still works without capture */ } }
    if (drag.resizing) {
      const now = { ...drag.pos, w: clamp(snap(widthOf(drag.pos) + dx), MIN_W, Math.min(MAX_W, BOARD_W - drag.pos.x)), h: clamp(snap(heightOf(drag.pos) + dy), MIN_H, MAX_H) };
      if (isFree(now, othersThan(drag.id))) drag.now = now;
      else if (!drag.now) drag.now = drag.pos;
    } else drag.now = { ...drag.pos, x: clamp(drag.pos.x + dx, 0, BOARD_W - widthOf(drag.pos)), y: Math.max(0, drag.pos.y + dy) };
    placeAt(drag.li, drag.now);
    const previewHeight = Math.max(free.height, drag.now.y + heightOf(drag.now) + 40);
    grid.closest('.bd-cork').style.height = `${Math.ceil(previewHeight * free.scale)}px`;
  });
  const finish = async (e, cancelled) => {
    if (!drag || (e && e.pointerId !== drag.pid)) return;
    const d = drag;
    drag = null;
    delete d.li.dataset.dragging;
    if (!d.moved) return;
    geometryBusy = false;
    suppressClick = true;                                         // the click that follows a drag is not an edit
    window.setTimeout(() => { suppressClick = false; }, 0);
    if (cancelled) { placeAt(d.li, d.pos); grid.closest('.bd-cork').style.height = `${Math.ceil(free.height * free.scale)}px`; if (syncPending) { syncPending = false; await syncNow(true); } return; }
    const to = d.resizing ? d.now : nearestFree({ ...d.now, x: snap(d.now.x), y: snap(d.now.y) }, othersThan(d.id));
    if (!to) { placeAt(d.li, d.pos); say('There is no free spot for it on the board.', 'warn'); return; }
    if (d.pos.placed && to.x === d.pos.x && to.y === d.pos.y && widthOf(to) === widthOf(d.pos) && heightOf(to) === heightOf(d.pos)) { placeAt(d.li, d.pos); return; }
    geometryBusy = true;
    try { await commitPlace(d.id, to); } finally { geometryBusy = false; }
  };
  grid.addEventListener('pointerup', (e) => finish(e, false));
  grid.addEventListener('pointercancel', (e) => finish(e, true));
  grid.addEventListener('lostpointercapture', (e) => finish(e, true));
  window.addEventListener('blur', () => finish(null, true));
  document.addEventListener('visibilitychange', () => { if (document.visibilityState !== 'visible') finish(null, true); });
  grid.addEventListener('click', (e) => { if (suppressClick) { e.stopImmediatePropagation(); e.preventDefault(); } }, true);
  grid.addEventListener('keydown', async (e) => {
    if (free && e.key === 'Escape' && drag) { e.preventDefault(); finish(null, true); return; }
    const handle = e.target.closest('[data-bd-resize]');
    if (free && handle && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) {
      e.preventDefault(); if (geometryBusy) return;
      const id = Number(handle.dataset.bdResize), pos = free.layout.get(id);
      const delta = e.shiftKey ? STEP * 5 : STEP;
      const to = {...pos, w: clamp(widthOf(pos) + (e.key === 'ArrowRight' ? delta : e.key === 'ArrowLeft' ? -delta : 0), MIN_W, Math.min(MAX_W, BOARD_W-pos.x)), h: clamp(heightOf(pos) + (e.key === 'ArrowDown' ? delta : e.key === 'ArrowUp' ? -delta : 0), MIN_H, MAX_H)};
      if (!isFree(to, othersThan(id))) { announce('Another note is in the way.'); return; }
      geometryBusy = true;
      try { await commitPlace(id,to); } finally { geometryBusy = false; }
      $(`[data-bd-resize="${id}"]`)?.focus({preventScroll:true});
      announce(`Size ${to.w} by ${to.h}; minimum ${MIN_W} by ${MIN_H}, maximum ${MAX_W} by ${MAX_H}`); return;
    }
    if (!free || geometryBusy || e.altKey || e.ctrlKey || e.metaKey || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return;
    const li = e.target.closest('[data-bd-note]');
    if (!li || e.target !== li) return;                          // arrows inside a text box keep moving the caret
    const id = Number(li.dataset.bdNote);
    const pos = free.layout.get(id);
    if (!pos) return;
    e.preventDefault();
    const to = stepFrom(pos, e.key, othersThan(id), e.shiftKey);
    if (!to) { say('There is a note in the way, or this is the edge of the board.', 'warn'); return; }
    geometryBusy = true;
    try { await commitPlace(id, to); } finally { geometryBusy = false; }
    announce(`Moved to column ${Math.floor(to.x / STEP) + 1}, row ${Math.floor(to.y / STEP) + 1}`);
  });
  // A resize or a zoom redraws the surface at the new scale (or the list, when narrow); it never saves anything.
  let lastWidth = 0;
  let frame = null;
  const cork = grid.closest('.bd-cork');
  if (cork && 'ResizeObserver' in window) {
    new ResizeObserver(() => {
      const w = cork.clientWidth;
      if (w === lastWidth) return;
      lastWidth = w;
      if (frame) window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => { if (!editing && !creating && !drag && !geometryBusy) render(); });
    }).observe(cork);
  }
}

function bindDrag(grid) {
  let dragId = null;
  grid.addEventListener('dragstart', (e) => {
    const li = e.target.closest('[data-bd-note]');
    if (!li || !li.draggable) return;
    dragId = Number(li.dataset.bdNote);
    li.dataset.dragging = 'true';
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', String(dragId));
  });
  const side = (li, e) => {
    const r = li.getBoundingClientRect();
    return e.clientX < r.left + r.width / 2 ? 'before' : 'after';
  };
  // Empty space is a drop target too. Use the nearest row/card to retain
  // ordered placement; no free-position coordinates or new storage model.
  const dropTarget = (e) => {
    const direct = e.target.closest('[data-bd-note]');
    if (direct) return { li: direct, where: side(direct, e) };
    const cards = $$('[data-bd-note]', grid).filter(n => Number(n.dataset.bdNote) !== dragId);
    if (!cards.length) return null;
    let li = cards[0], distance = Infinity;
    for (const card of cards) {
      const r = card.getBoundingClientRect();
      const dx = Math.max(r.left - e.clientX, 0, e.clientX - r.right);
      const dy = Math.max(r.top - e.clientY, 0, e.clientY - r.bottom);
      const d = dx * dx + dy * dy;
      if (d < distance) { li = card; distance = d; }
    }
    const r = li.getBoundingClientRect();
    return { li, where: e.clientY > r.bottom ? 'after' : e.clientY < r.top ? 'before' : side(li, e) };
  };
  grid.addEventListener('dragover', (e) => {
    if (dragId == null) return;
    const target = dropTarget(e);
    if (!target || Number(target.li.dataset.bdNote) === dragId) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    for (const n of $$('[data-drop]', grid)) if (n !== target.li) delete n.dataset.drop;
    target.li.dataset.drop = target.where;
  });
  grid.addEventListener('dragleave', (e) => {
    if (!grid.contains(e.relatedTarget)) {
      for (const n of $$('[data-drop]', grid)) delete n.dataset.drop;
    }
  });
  grid.addEventListener('drop', (e) => {
    if (dragId == null) return;
    const target = dropTarget(e);
    if (!target) return;
    e.preventDefault();
    const targetId = Number(target.li.dataset.bdNote);
    const id = dragId;
    dragId = null;
    for (const n of $$('[data-dragging], [data-drop]', grid)) { delete n.dataset.dragging; delete n.dataset.drop; }
    if (targetId !== id) reorder(target.where === 'before' ? { id, before_id: targetId } : { id, after_id: targetId });
  });
  grid.addEventListener('dragend', () => {
    dragId = null;
    for (const n of $$('[data-dragging], [data-drop]', grid)) { delete n.dataset.dragging; delete n.dataset.drop; }
  });
}

// ── + Note: a note, or a photo from the library or an upload ────────────────
let picked = null;

async function openPhoto() {
  const form = $('[data-bd-photo-form]');
  form.hidden = false;
  picked = null;
  $('[data-bd-photo-add]').disabled = true;
  const pick = $('[data-bd-pick]');
  pick.replaceChildren(el('p', { class: 'small' }, 'Reading your library…'));
  const r = await apiGet('/media?state=active&kind=photo');
  const body = r.body || {};
  if (!r.ok || !Array.isArray(body.assets)) {
    pick.replaceChildren(el('p', { class: 'unknown-line' }, 'The library could not be read. You can still upload a photo.'));
    return;
  }
  if (!body.assets.length) { pick.replaceChildren(el('p', { class: 'small' }, 'Your library has no photos yet: upload one.')); return; }
  pick.replaceChildren(...body.assets.map((a) => {
    const b = el('button', { type: 'button', 'data-bd-pick-asset': a.id, 'aria-pressed': 'false', 'aria-label': `Use ${a.title || 'this photo'}` });
    b.append(el('img', { src: a.thumb_url, alt: '' }));
    return b;
  }));
}

async function uploadThenPick(file) {
  const out = $('[data-bd-photo-result]');
  say('Uploading…', 'ok', out);
  const name = `upload:${file.name}:${file.size}:${file.lastModified}`;
  const r = await apiUpload('/media', file, keyFor(name));
  const body = r.body || {};
  const code = body.result || body.error || 'failed';
  settle(name, code);
  if (!r.ok || !body.asset) { say(body.message || 'Not uploaded. Nothing was added.', kindOf(code), out); return; }
  if (body.asset.state === 'trash') {
    say('That photo is in your library Trash. Restore it in the Media library to place it again.', 'warn', out);
    return;
  }
  picked = body.asset.id;
  $('[data-bd-photo-add]').disabled = false;
  say(`${body.message}. Place it on the Board, with a caption if you like.`, 'ok', out);
}

// Pasting/dropping a picture is one gesture. Keep both keys through an uncertain
// response so an explicit retry reconciles the original operation, never adds twice.
let pastedPicture = null;
let pictureBusy = false;
function pictureFailure(response) {
  const uncertain = response.status === 0 || response.status >= 500 || response.body?.error === 'unreadable';
  say(uncertain ? 'The picture could not be confirmed. Retry to check the same picture without adding it twice.'
    : (response.body?.message || 'The picture was not added.'), 'warn');
  $('[data-bd-paste-actions]').hidden = false;
  // Validation refusals need a different picture, not the same request again.
  $('[data-bd-paste-retry]').hidden = [400, 413, 415, 422].includes(response.status);
}
async function placePastedPicture() {
  if (!pastedPicture || pictureBusy) return;
  if (!live.known || live.off) { say(!live.known ? RECORD_UNKNOWN_TEXT : page.off_record_text, 'warn'); return; }
  const attempt = pastedPicture;
  pictureBusy = true;
  $('[data-bd-paste-actions]').hidden = true;
  say('Adding picture…', 'working');
  try {
    if (!attempt.asset) {
      const up = await apiUpload('/media', attempt.file, attempt.uploadKey);
      if (!up.ok || !up.body?.asset) { pictureFailure(up); return; }
      attempt.asset = up.body.asset.id;
      attempt.file = null;
    }
    const placed = await apiPost('/postits/photo', { asset_id: attempt.asset, caption: null,
      idempotency_key: attempt.placeKey, record_mode: recordMode() });
    if (!placed.ok || placed.body?.result !== 'added') { pictureFailure(placed); return; }
    pastedPicture = null;
    ui.show = 'active'; ui.q = ''; ui.label = '';
    $('[data-bd-search]').value = ''; $('[data-bd-label-filter]').value = '';
    await reload();
    announceChange();
    say('Picture added.');
  } finally { pictureBusy = false; }
}
function takePictureFiles(files) {
  if (!live.known || live.off) { say(!live.known ? RECORD_UNKNOWN_TEXT : page.off_record_text, 'warn'); return; }
  if (pastedPicture) { say(pictureBusy ? 'A picture is still being added.' : 'Retry or dismiss the previous picture first.', 'warn'); return; }
  if (files.length !== 1) { say('Add one picture at a time.', 'warn'); return; }
  const file = files[0];
  if (!['image/jpeg', 'image/png', 'image/webp', 'image/gif'].includes(file.type)) { say('Use a JPEG, PNG, WebP or GIF picture.', 'warn'); return; }
  const max = page.attach?.max_bytes || 8 * 1024 * 1024;
  if (!file.size || file.size > max) { say(`Use a picture smaller than ${Math.round(max / 1048576)} MB.`, 'warn'); return; }
  pastedPicture = { file, uploadKey: newKey(), placeKey: newKey(), asset: null };
  void placePastedPicture();
}
function bindPicturePaste() {
  // Text fields and the MC panel retain their own paste/drop behaviour.
  const elsewhere = (target) => target instanceof Element && target.closest('input, textarea, select, [contenteditable], [data-mc-panel]');
  document.addEventListener('paste', (event) => {
    if (elsewhere(event.target)) return;
    const files = Array.from(event.clipboardData?.files || []);
    if (!files.length) return;
    event.preventDefault(); takePictureFiles(files);
  });
  const cork = $('[data-bd-cork]');
  cork.addEventListener('dragover', (event) => {
    if (!elsewhere(event.target) && Array.from(event.dataTransfer?.types || []).includes('Files')) event.preventDefault();
  });
  cork.addEventListener('drop', (event) => {
    if (elsewhere(event.target)) return;
    const files = Array.from(event.dataTransfer?.files || []);
    if (!files.length) return;       // internal note drags never become uploads
    event.preventDefault(); takePictureFiles(files);
  });
  $('[data-bd-paste-retry]').addEventListener('click', () => { void placePastedPicture(); });
  $('[data-bd-paste-dismiss]').addEventListener('click', () => {
    if (pictureBusy) return;
    pastedPicture = null; $('[data-bd-paste-actions]').hidden = true;
    say('Picture retry dismissed. Any picture already saved is kept.', 'warn');
  });
}

function bind() {
  bindPicturePaste();
  for (const button of $$('[data-bd-view]')) button.addEventListener('click', () => { ui.show = button.dataset.bdView; render(); });
  $('[data-bd-label-filter]').addEventListener('change', (e) => { ui.label = e.target.value; render(); });
  $('[data-bd-search]').addEventListener('input', (e) => { ui.q = e.target.value; render(); });
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-bd-open]');
    if (!b) return;
    $('[data-bd-photo-form]').hidden = true;
    openPhoto();
  });
  document.addEventListener('click', (e) => { for (const d of $$('.bd-edit[open]')) if (!d.contains(e.target)) d.open = false; });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') for (const d of $$('.bd-edit[open]')) { d.open = false; const sm = d.querySelector('summary'); if (sm) sm.focus(); } });
  for (const c of $$('[data-bd-close]')) c.addEventListener('click', () => { c.closest('form').hidden = true; });
  $('[data-bd-pick]').addEventListener('click', (e) => {
    const b = e.target.closest('[data-bd-pick-asset]');
    if (!b) return;
    for (const o of $$('[data-bd-pick-asset]')) o.setAttribute('aria-pressed', String(o === b));
    picked = b.dataset.bdPickAsset;
    $('[data-bd-photo-add]').disabled = false;
  });
  $('[data-bd-photo-file]').addEventListener('change', (e) => { if (e.target.files[0]) uploadThenPick(e.target.files[0]); });
  $('[data-bd-photo-form]').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!picked) return;
    const caption = $('[data-bd-photo-caption]').value.trim() || null;
    const body = { asset_id: picked, caption };
    const name = `photo:${JSON.stringify(body)}`;
    const r = await apiPost('/postits/photo', { ...body, idempotency_key: keyFor(name), record_mode: recordMode() });
    const res = r.body || {};
    const code = res.result || res.error || 'failed';
    settle(name, code);
    say(res.message || 'Nothing was added', code === 'added' ? 'ok' : kindOf(code), $('[data-bd-photo-result]'));
    if (code === 'added') { $('[data-bd-photo-caption]').value = ''; $('[data-bd-photo-file]').value = ''; }
    await reload();
    if (code === 'added') announceChange();
  });
  const grid = $('[data-bd-grid]');
  if (wall()) bindWall(grid);
  grid.addEventListener('click', (e) => {
    const t = e.target.closest('[data-bd-edit-text]');
    if (t && !e.target.closest('textarea')) startEditing(Number(t.dataset.bdEditText));
  });
  grid.addEventListener('click', async (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    const pick = (attr) => Number(b.getAttribute(attr));
    if (b.hasAttribute('data-bd-more')) {
      const id = pick('data-bd-more');
      const li = b.closest('[data-bd-note]');
      const on = !opened.has(id);
      if (on) opened.add(id); else opened.delete(id);
      if (li) li.dataset.open = String(on);
      b.setAttribute('aria-expanded', String(on));
      b.textContent = on ? 'Less' : 'More';
    } else if (b.hasAttribute('data-bd-new-save')) {
      await saveNew();
    } else if (b.hasAttribute('data-bd-new-cancel')) {
      await stopCreating();
    } else if (b.hasAttribute('data-bd-edit-open')) {
      startEditing(pick('data-bd-edit-open'));
    } else if (b.hasAttribute('data-bd-edit-save')) {
      await saveEdit(pick('data-bd-edit-save'));
    } else if (b.hasAttribute('data-bd-edit-cancel')) {
      await stopEditing(false);
    } else if (b.hasAttribute('data-bd-done') || b.hasAttribute('data-bd-undone')) {
      const done = b.hasAttribute('data-bd-done');
      const p = noteById(pick(done ? 'data-bd-done' : 'data-bd-undone'));
      await write(`/postits/${p.id}/${done ? 'done' : 'undone'}`, { version: p.version }, `${done ? 'done' : 'undone'}:${p.id}:${p.version}`);
    } else if (b.hasAttribute('data-bd-bin')) {
      const id = pick('data-bd-bin');
      await write(`/postits/${id}/bin`, {}, `bin:${id}:${noteById(id).version}`);
    } else if (b.hasAttribute('data-bd-restore')) {
      const id = pick('data-bd-restore');
      await write(`/postits/${id}/restore`, {}, `restore:${id}:${noteById(id).version}`);
    } else if (b.hasAttribute('data-bd-move')) {
      await move(Number(b.dataset.id), b.dataset.bdMove);
    } else if (b.hasAttribute('data-bd-empty')) {
      const n = list('trash').length;
      $('[data-bd-confirm-text]').textContent = `Delete ${n} note${n === 1 ? '' : 's'} in the Trash permanently? Linked work and library images are kept.`;
      $('[data-bd-confirm]').hidden = false;
      $('[data-bd-confirm-yes]').focus();
    }
  });
  grid.addEventListener('keydown', (e) => {
    if (!e.altKey || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return;
    const li = e.target.closest('[data-bd-note]');
    if (!li || ui.show !== 'active' || filtersOn()) return;
    e.preventDefault();
    move(Number(li.dataset.bdNote), ['ArrowLeft', 'ArrowUp'].includes(e.key) ? 'prev' : 'next');
  });
  bindDrag(grid.closest('.bd-cork') || grid);
  bindPlace(grid);
  $('[data-bd-confirm-no]').addEventListener('click', () => { $('[data-bd-confirm]').hidden = true; });
  $('[data-bd-confirm-yes]').addEventListener('click', async () => {
    $('[data-bd-confirm]').hidden = true;
    const items = list('trash').map((p) => ({ id: p.id, version: p.version }));
    const name = `empty:${data.trash_rev}:${JSON.stringify(items)}`;
    const { code } = await write('/postits/trash/empty', { trash_rev: data.trash_rev, items, confirm: 'empty' }, name);
    if (code === 'conflict') say('The Trash changed since you looked. Nothing was deleted; here it is now.', 'warn');
  });
}

export function initBoard(p) {
  page = p;
  const raw = document.getElementById('board-data');
  if (!raw || !$('[data-bd-grid]')) return;
  data = JSON.parse(raw.textContent);
  for (const l of data.labels || []) $('[data-bd-label-filter]').append(el('option', { value: l }, LABEL_NAMES[l] || l));
  const requested = new URLSearchParams(window.location.search).get('note');
  const target = /^[1-9][0-9]{0,17}$/.test(requested || '') ? allNotes().find(n => String(n.id) === requested) : null;
  if (target) ui.show = target.state === 'binned' ? 'trash' : target.state === 'done' ? 'done' : 'active';
  bind();
  const pop = $('[data-bd-popout]');
  if (pop) pop.addEventListener('click', popOut);
  const fresh = $('[data-bd-new]');
  if (fresh) fresh.addEventListener('click', startCreating);
  const retry = $('[data-bd-retry]');
  if (retry) retry.addEventListener('click', () => { retry.disabled = true; syncNow(true).finally(() => { retry.disabled = false; }); });
  render();
  if (target) {
    const card = $(`[data-bd-note="${target.id}"]`);
    if (card) { card.setAttribute('tabindex', '-1'); card.focus(); card.scrollIntoView({block:'center'}); }
  }
  if (data.status !== 'ok') {                                  // the page was drawn while the Board could not be read: never treat that as "current"
    needsRetry = true;
    for (const ms of [1500, 4000, 9000]) window.setTimeout(() => { if (needsRetry) syncNow(true); }, ms);
    startSync();
  } else peekRev().then((rev) => { if (rev != null) syncRev = rev; startSync(); });
}
