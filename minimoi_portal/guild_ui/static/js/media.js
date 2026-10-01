// The shared Media library (Guild 1.1 slice 3, spec §5.2): thumbnails, a
// type filter, Add (upload), Trash and Restore, and a permanent delete that
// the server refuses while an image is still used, saying where. Every write
// goes through the API with the guards; the upload sends its idempotency key
// in a header. No model call.
import { $, el, announce } from './dom.js';
import { apiGet, apiPost, apiUpload, recordMode } from './api.js';
import { kindOf, newKey } from './actions.js';

let page;
const ui = { show: 'active', kind: '' };
let data = null;                       // the last GET /media answer
const keys = new Map();
const SETTLED = ['added', 'duplicate', 'trashed', 'restored', 'purged', 'conflict', 'in_use', 'not_found', 'gone',
  'already_trashed', 'already_active', 'idempotency_mismatch', 'invalid', 'too_large', 'unsupported'];
const OK = ['added', 'duplicate', 'trashed', 'restored', 'purged'];
let pendingPurge = null;

function keyFor(name) { if (!keys.has(name)) keys.set(name, newKey()); return keys.get(name); }
function settle(name, code) { if (SETTLED.includes(code)) keys.delete(name); }

function say(text, kind = 'ok') {
  const out = $('[data-md-result]');
  out.hidden = !text;
  out.dataset.kind = kind;
  out.textContent = text || '';
  if (text) announce(text);
}

function where(uses) {
  return uses.map((u) => (u.domain === 'guild' && u.ref_kind === 'postit' ? `Board note #${u.ref_id}` : `${u.domain} ${u.ref_kind} ${u.ref_id}`)).join(', ');
}

function item(a) {
  const li = el('li', { class: 'md-item', 'data-md-asset': a.id, 'data-state': a.state });
  if (a.thumb_url) li.append(el('img', { src: a.thumb_url, alt: a.title || 'A library image', loading: 'lazy' }));
  else if (a.emoji) li.append(el('p', { class: 'bd-text' }, a.emoji));
  const body = el('div', { class: 'md-body' });
  body.append(el('span', {}, `${a.kind} · ${a.width || '?'}×${a.height || '?'} · ${Math.round((a.bytes || 0) / 1024)} KB`),
    el('span', { class: 'md-uses', 'data-md-uses': a.id }, a.uses ? `Used in ${a.uses} place${a.uses === 1 ? '' : 's'}` : 'Not used'));
  if (a.state === 'active') body.append(el('button', { type: 'button', class: 'bd-btn', 'data-md-trash': a.id }, 'Move to Trash'));
  else {
    body.append(el('button', { type: 'button', class: 'bd-btn', 'data-md-restore': a.id }, 'Restore'),
      el('button', { type: 'button', class: 'bd-btn', 'data-md-purge': a.id }, 'Delete permanently'));
  }
  body.append(el('span', { class: 'md-where', 'data-md-where': a.id, hidden: true }));
  li.append(body);
  return li;
}

async function reload() {
  const q = `/media?state=${ui.show}${ui.kind ? `&kind=${ui.kind}` : ''}`;
  const r = await apiGet(q);
  const body = r.body || {};
  const grid = $('[data-md-grid]');
  if (!r.ok || !Array.isArray(body.assets)) {
    data = null;
    $('[data-md-counts]').textContent = 'unknown';
    $('[data-md-state]').textContent = body.message || 'The media library could not be read. Nothing here is empty or zero.';
    grid.replaceChildren();
    return;
  }
  data = body;
  $('[data-md-counts]').replaceChildren(el('strong', {}, String(body.counts.active)), ' in the library · ',
    el('strong', {}, String(body.counts.trash)), ' in Trash');
  $('[data-md-show] option[value="trash"]').textContent = `Trash (${body.counts.trash})`;
  $('[data-md-state]').textContent = body.files === 'ok' ? '' : 'The media folder is not available on this portal: adding images is paused.';
  grid.replaceChildren(...body.assets.map(item));
  if (!body.assets.length) grid.append(el('li', { class: 'bd-empty' }, ui.show === 'trash' ? 'The library Trash is empty.' : 'No images yet. Add one.'));
}

const assetById = (id) => (data && data.assets || []).find((a) => a.id === id);

async function write(path, body, name) {
  const r = await apiPost(path, { ...body, idempotency_key: keyFor(name), record_mode: recordMode() });
  const res = r.body || {};
  const code = res.result || res.error || 'failed';
  settle(name, code);
  return { code, res };
}

function bind() {
  $('[data-md-show]').addEventListener('change', (e) => { ui.show = e.target.value; say(''); reload(); });
  $('[data-md-kind]').addEventListener('change', (e) => { ui.kind = e.target.value; reload(); });
  $('[data-md-file]').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    say('Uploading…');
    const name = `upload:${file.name}:${file.size}:${file.lastModified}`;
    const r = await apiUpload('/media', file, keyFor(name));
    const body = r.body || {};
    const code = body.result || body.error || 'failed';
    settle(name, code);
    say(body.message || 'Not uploaded. Nothing was added.', OK.includes(code) ? 'ok' : kindOf(code));
    e.target.value = '';
    await reload();
  });
  $('[data-md-grid]').addEventListener('click', async (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.mdTrash || b.dataset.mdRestore) {
      const id = b.dataset.mdTrash || b.dataset.mdRestore;
      const a = assetById(id);
      const action = b.dataset.mdTrash ? 'trash' : 'restore';
      const { code, res } = await write(`/media/${id}/${action}`, { version: a.version }, `${action}:${id}:${a.version}`);
      say(res.message || 'Nothing was changed', OK.includes(code) ? 'ok' : kindOf(code));
      await reload();
    } else if (b.dataset.mdPurge) {
      pendingPurge = assetById(b.dataset.mdPurge);
      $('[data-md-confirm-text]').textContent = 'Delete this image permanently? It is removed from the server and cannot be restored.';
      $('[data-md-confirm]').hidden = false;
      $('[data-md-confirm-yes]').focus();
    }
  });
  $('[data-md-confirm-no]').addEventListener('click', () => { $('[data-md-confirm]').hidden = true; pendingPurge = null; });
  $('[data-md-confirm-yes]').addEventListener('click', async () => {
    $('[data-md-confirm]').hidden = true;
    const a = pendingPurge;
    pendingPurge = null;
    if (!a || !data) return;
    const items = [{ id: a.id, version: a.version }];
    const { code, res } = await write('/media/purge', { library_trash_rev: data.library_trash_rev, items, confirm: 'purge' },
      `purge:${data.library_trash_rev}:${a.id}:${a.version}`);
    say(res.message || 'Nothing was deleted', OK.includes(code) ? 'ok' : kindOf(code));
    const uses = code === 'in_use' && res.current && res.current.in_use ? res.current.in_use[a.id] : null;
    await reload();
    if (uses) {
      const line = $(`[data-md-where="${a.id}"]`);
      if (line) { line.hidden = false; line.textContent = `Still used: ${where(uses)}`; }
    }
  });
}

export function initMedia(p) {
  page = p;
  if (!$('[data-md-grid]')) return;
  bind();
  reload();
}
