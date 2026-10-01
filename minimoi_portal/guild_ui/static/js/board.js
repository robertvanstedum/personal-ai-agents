// The Board (Guild 1.1 slice 3, spec §5.1): relaxed post-its with Done,
// labels, links and photos; your order by drag, the ‹ › buttons or
// Alt+Arrow; Trash with Restore and a confirmed Empty. Drawn from the same
// answer GET /api/v1/board gives; every write goes through the API with the
// guards (CSRF, record mode, one idempotency key per change, the note's
// version, the board's order and trash revisions). Only the view (Show,
// label, search) stays in the page. No model call.
import { $, $$, el, announce, localTime } from './dom.js';
import { apiGet, apiPost, apiUpload, recordMode } from './api.js';
import { kindOf, newKey } from './actions.js';

let page;
let data;                         // { status, active, done, trash, order_rev, trash_rev, counts, labels }
const ui = { show: 'active', label: '', q: '' };
const keys = new Map();
const SETTLED = ['added', 'done', 'undone', 'labelled', 'linked', 'moved', 'emptied', 'restored', 'binned',
  'conflict', 'idempotency_mismatch', 'invalid', 'not_found', 'in_trash', 'already_done', 'already_active',
  'already_binned', 'asset_trashed', 'gone', 'duplicate', 'trashed'];
const OK = ['added', 'done', 'undone', 'labelled', 'linked', 'binned', 'restored', 'moved', 'emptied'];
const LABEL_NAMES = { decide: 'Decide', blocked: 'Blocked', remember: 'Remember', followup: 'Follow-up', idea: 'Idea', fyi: 'FYI' };

function h(tag, attrs, ...kids) { const n = el(tag, attrs); n.append(...kids); return n; }
function keyFor(name) { if (!keys.has(name)) keys.set(name, newKey()); return keys.get(name); }
function settle(name, code) { if (SETTLED.includes(code)) keys.delete(name); }

function say(text, kind = 'ok', where = $('[data-bd-result]')) {
  where.hidden = !text;
  where.dataset.kind = kind;
  where.textContent = text || '';
  if (text) announce(text);
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

function editBox(p) {
  const box = el('details', { class: 'bd-edit' });
  box.append(el('summary', {}, 'Label and link'));
  const body = el('div', { class: 'bd-edit-body' });
  const sel = el('select', { 'data-bd-edit-label': p.id, 'aria-label': 'Label' });
  sel.append(el('option', { value: '' }, 'no label'));
  for (const l of data.labels || []) {
    const o = el('option', { value: l }, LABEL_NAMES[l] || l);
    if (p.label === l) o.selected = true;
    sel.append(o);
  }
  const item = el('input', { 'data-bd-edit-item': p.id, 'aria-label': 'Work item number', inputmode: 'numeric',
    placeholder: '#', value: p.item_ref ? String(p.item_ref) : '' });
  body.append(sel, el('button', { type: 'button', class: 'bd-btn', 'data-bd-save-label': p.id }, 'Save label'),
    item, el('button', { type: 'button', class: 'bd-btn', 'data-bd-save-link': p.id }, 'Save link'));
  box.append(body);
  return box;
}

function card(p, n, shown) {
  const photo = p.kind === 'photo';
  const li = el('li', { class: `bd-note${photo ? ' bd-photo' : ''}`, 'data-bd-note': p.id, 'data-state': p.state,
    'data-label': p.label || '', tabindex: '0', 'aria-label': `${photo ? 'Photo' : 'Note'}: ${p.text}` });
  const reorderable = ui.show === 'active' && !filtersOn();
  if (reorderable) li.draggable = true;
  if (!photo) li.append(el('p', { class: 'bd-kicker' }, p.label ? (LABEL_NAMES[p.label] || p.label) : ''));
  if (photo) {
    li.append(el('img', { src: p.thumb_url, alt: p.text === 'Photo' ? 'A photo on the Board' : p.text, loading: 'lazy' }));
  }
  li.append(el('p', { class: 'bd-text' }, p.text));
  const link = linkLine(p);
  if (link) li.append(link);
  const when = ui.show === 'trash' ? p.binned_at : (ui.show === 'done' ? p.done_at : p.created_at);
  li.append(el('p', { class: 'bd-meta' }, `${p.author_label} · ${ui.show === 'trash' ? 'binned ' : ui.show === 'done' ? 'done ' : ''}${localTime(when)}`));
  const acts = el('div', { class: 'bd-acts' });
  if (ui.show === 'active') {
    if (!photo) acts.append(el('button', { type: 'button', class: 'bd-btn', 'data-bd-done': p.id }, 'Done'));
    acts.append(el('button', { type: 'button', class: 'bd-btn', 'data-bd-bin': p.id }, photo ? 'Remove' : 'Discard'));
    acts.append(el('span', { class: 'bd-sp' }));
    const prev = el('button', { type: 'button', class: 'bd-btn bd-move', 'data-bd-move': 'prev', 'data-id': p.id,
      'aria-label': `Move "${p.text.slice(0, 30)}" earlier` }, '‹');
    const next = el('button', { type: 'button', class: 'bd-btn bd-move', 'data-bd-move': 'next', 'data-id': p.id,
      'aria-label': `Move "${p.text.slice(0, 30)}" later` }, '›');
    prev.disabled = !reorderable || n === 0;
    next.disabled = !reorderable || n === shown.length - 1;
    acts.append(prev, next);
  } else if (ui.show === 'done') {
    acts.append(el('button', { type: 'button', class: 'bd-btn', 'data-bd-undone': p.id }, 'Undone'),
      el('button', { type: 'button', class: 'bd-btn', 'data-bd-bin': p.id }, 'Discard'));
  } else {
    acts.append(el('button', { type: 'button', class: 'bd-btn', 'data-bd-restore': p.id }, 'Restore'));
  }
  li.append(acts);
  if (ui.show !== 'trash' && !photo) li.append(editBox(p));
  li.append(el('p', { class: 'bd-result', 'data-bd-note-result': p.id, role: 'status', hidden: true }));
  return li;
}

function render() {
  const known = data && data.status === 'ok';
  const counts = (data && data.counts) || {};
  const countsEl = $('[data-bd-counts]');
  if (known) {
    countsEl.replaceChildren(el('strong', {}, String(counts.active)), ' active · ', el('strong', {}, String(counts.done)),
      ' done · ', el('strong', {}, String(counts.trash)), ' in Trash');
  } else countsEl.textContent = 'unknown';
  const trashOpt = $('[data-bd-show] option[value="trash"]');
  trashOpt.textContent = known ? `Trash (${counts.trash})` : 'Trash (?)';
  const shown = list(ui.show).filter(matches);
  const grid = $('[data-bd-grid]');
  grid.replaceChildren(...shown.map((p, n) => card(p, n, shown)));
  const note = $('[data-bd-state-note]');
  if (!known) note.textContent = '';
  else if (ui.show === 'active') note.textContent = filtersOn() ? 'Clear the label and search to change the order.' : 'Drag a note, use ‹ ›, or Alt+Arrow to change the order.';
  else if (ui.show === 'done') note.textContent = 'Done notes have left Active. Undone puts one back.';
  else note.textContent = 'Discarded notes stay here until you empty the Trash. Restore puts one back as it was. Emptying never changes linked work or library images.';
  if (known && !shown.length) grid.append(el('li', { class: 'bd-empty' }, ui.show === 'trash' ? 'The Trash is empty.' : filtersOn() ? 'No notes match.' : ui.show === 'done' ? 'Nothing done yet.' : 'No notes on the Board.'));
  if (known && ui.show === 'trash' && list('trash').length) {
    const empty = el('li', { class: 'bd-empty' });
    empty.append(el('button', { type: 'button', class: 'btn', 'data-bd-empty': true }, `Empty trash (${list('trash').length})`));
    grid.prepend(empty);
  }
}

async function reload() {
  const r = await apiGet('/board');
  const body = r.body || {};
  if (r.ok && body.status === 'ok') data = { ...data, ...body, status: 'ok' };
  else if (r.status !== 0) data = { ...data, status: 'unknown', active: null, done: null, trash: null };
  render();
}

function noteById(id) { return allNotes().find((p) => p.id === id); }

async function write(path, body, name, where) {
  const r = await apiPost(path, { ...body, idempotency_key: keyFor(name), record_mode: recordMode() });
  const res = r.body || {};
  const code = res.result || res.error || 'failed';
  settle(name, code);
  await reload();
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
  grid.addEventListener('dragover', (e) => {
    const li = e.target.closest('[data-bd-note]');
    if (dragId == null || !li || Number(li.dataset.bdNote) === dragId) return;
    e.preventDefault();
    for (const n of $$('[data-drop]', grid)) if (n !== li) delete n.dataset.drop;
    li.dataset.drop = side(li, e);
  });
  grid.addEventListener('dragleave', (e) => {
    const li = e.target.closest('[data-bd-note]');
    if (li && !li.contains(e.relatedTarget)) delete li.dataset.drop;
  });
  grid.addEventListener('drop', (e) => {
    const li = e.target.closest('[data-bd-note]');
    if (dragId == null || !li) return;
    e.preventDefault();
    const target = Number(li.dataset.bdNote);
    const where = side(li, e);
    const id = dragId;
    dragId = null;
    if (target !== id) reorder(where === 'before' ? { id, before_id: target } : { id, after_id: target });
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

function bind() {
  $('[data-bd-show]').addEventListener('change', (e) => { ui.show = e.target.value; render(); });
  $('[data-bd-label-filter]').addEventListener('change', (e) => { ui.label = e.target.value; render(); });
  $('[data-bd-search]').addEventListener('input', (e) => { ui.q = e.target.value; render(); });
  const add = $('[data-bd-add]');
  add.addEventListener('click', (e) => {
    const b = e.target.closest('[data-bd-open]');
    if (!b) return;
    add.open = false;
    $('[data-bd-note-form]').hidden = b.dataset.bdOpen !== 'note';
    $('[data-bd-photo-form]').hidden = true;
    if (b.dataset.bdOpen === 'note') $('[data-bd-note-text]').focus();
    else openPhoto();
  });
  document.addEventListener('click', (e) => { if (add.open && !add.contains(e.target)) add.open = false; });
  for (const c of $$('[data-bd-close]')) c.addEventListener('click', () => { c.closest('form').hidden = true; });
  const text = $('[data-bd-note-text]');
  text.addEventListener('input', () => { $('[data-bd-note-count]').textContent = `${text.value.length} / ${page.postit_max}`; });
  $('[data-bd-note-form]').addEventListener('submit', async (e) => {
    e.preventDefault();
    const out = $('[data-bd-add-result]');
    const t = text.value.trim();
    const label = $('[data-bd-note-label]').value || null;
    const itemRaw = $('[data-bd-note-item]').value.trim().replace(/^#/, '');
    if (!t) { say('Write something first.', 'warn', out); return; }
    if (itemRaw && !/^\d+$/.test(itemRaw)) { say('A work item is a number, like 12.', 'warn', out); return; }
    const body = { text: t, label, item_ref: itemRaw ? Number(itemRaw) : null };
    const name = `add:${JSON.stringify(body)}`;
    const r = await apiPost('/postits', { ...body, idempotency_key: keyFor(name), record_mode: recordMode() });
    const res = r.body || {};
    const code = res.result || res.error || 'failed';
    settle(name, code);
    say(res.message || 'Nothing was added', code === 'added' ? 'ok' : kindOf(code), out);
    if (code === 'added') { text.value = ''; $('[data-bd-note-item]').value = ''; ui.show = 'active'; $('[data-bd-show]').value = 'active'; }
    await reload();
  });
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
  });
  const grid = $('[data-bd-grid]');
  grid.addEventListener('click', async (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    const pick = (attr) => Number(b.getAttribute(attr));
    if (b.hasAttribute('data-bd-done') || b.hasAttribute('data-bd-undone')) {
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
    } else if (b.hasAttribute('data-bd-save-label')) {
      const p = noteById(pick('data-bd-save-label'));
      const label = $(`[data-bd-edit-label="${p.id}"]`).value || null;
      await write(`/postits/${p.id}/label`, { label, version: p.version }, `label:${p.id}:${p.version}:${label}`, p.id);
    } else if (b.hasAttribute('data-bd-save-link')) {
      const p = noteById(pick('data-bd-save-link'));
      const raw = $(`[data-bd-edit-item="${p.id}"]`).value.trim().replace(/^#/, '');
      if (raw && !/^\d+$/.test(raw)) { say('A work item is a number, like 12.', 'warn', $(`[data-bd-note-result="${p.id}"]`)); return; }
      const item = raw ? Number(raw) : null;
      await write(`/postits/${p.id}/link`, { item_ref: item, version: p.version }, `link:${p.id}:${p.version}:${item}`, p.id);
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
  bindDrag(grid);
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
  for (const sel of [$('[data-bd-label-filter]'), $('[data-bd-note-label]')]) {
    for (const l of data.labels || []) sel.append(el('option', { value: l }, LABEL_NAMES[l] || l));
  }
  bind();
  render();
}
