// Rooms (Guild 1.1 slice 4, spec §6): group chat on Records, through the dev
// bridge at /app/records/api/ with Records' own sign-in. This page never
// holds a Records credential: a 401 shows "Sign in to Rooms" (Records' own
// page) and the draft stays in this tab. Records' own actor and grant checks
// decide every action; a paused room refuses writes (409) and says so. Agent
// messages carry their Records actor and an "agent" label; no live presence
// is implied. Take to a Room shares a kept, on-the-record Chat note by id
// only: the Guild API answers with the stored note, and only that is sent.
// No model call.
import { $, $$, el, announce } from './dom.js';
import { apiPost, recordMode } from './api.js';
import { newKey } from './actions.js';

let cfg;
let page;
let rooms = [];
let room = null;              // the open room, as Records answers it
let current = null;           // its id
let signedOut = false;
let takeNote = null;          // { id, text, ... } from the Guild API
let poll = null;
const keys = new Map();
const IMAGE = /\.(png|jpe?g|gif|webp)$/i;
const TEXT = /\.(txt|md|csv|json)$/i;

function h(tag, attrs, ...kids) { const n = el(tag, attrs); n.append(...kids); return n; }
function keyFor(name) { if (!keys.has(name)) keys.set(name, newKey()); return keys.get(name); }
const store = {
  get(k) { try { return window.sessionStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { if (v) window.sessionStorage.setItem(k, v); else window.sessionStorage.removeItem(k); } catch (e) { /* the draft then lives in the page only */ } },
};
const draftKey = () => `${page.storage_ns}.rooms.draft.${current || 'none'}`;
const when = (iso) => { const t = Date.parse(iso); return Number.isNaN(t) ? '' : new Date(t).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); };
const initial = (label) => (label || '?').trim().slice(0, 2).toUpperCase();

async function records(path, { method = 'GET', body, key } = {}) {
  const headers = { Accept: 'application/json' };
  if (method !== 'GET') { headers['Content-Type'] = 'application/json'; headers['Idempotency-Key'] = key; }
  let res;
  try {
    res = await fetch(`${cfg.api}${path}`, { method, headers, credentials: 'same-origin', cache: 'no-store', redirect: 'manual',
      body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) {
    return { status: 0, body: { error: 'Rooms could not be reached. Nothing was sent; your draft is kept.' } };
  }
  if (res.type === 'opaqueredirect') return { status: 401, body: { error: 'Your mini-moi sign-in ended.' } };
  let data = {};
  try { data = await res.json(); } catch (e) { data = {}; }
  if (res.status === 401) showSignin();
  return { status: res.status, body: data };
}

function showSignin() {
  signedOut = true;
  $('[data-rm-signin]').hidden = false;
  $('[data-rm-send]').disabled = true;
  announce('Sign in to Rooms. Your draft is kept.');
}

function error(text) {
  const box = $('[data-rm-error]');
  box.hidden = !text;
  box.textContent = text || '';
}

function status(text) {
  const out = $('[data-rm-status]');
  out.hidden = !text;
  out.textContent = text || '';
  if (text) announce(text);
}

// ── the room list ────────────────────────────────────────────────────────────
function renderList() {
  const list = $('[data-rm-list]');
  list.replaceChildren(...rooms.map((r) => {
    const a = el('a', { href: `?room=${encodeURIComponent(r.id)}`, 'data-rm-room': r.id });
    a.append(el('span', {}, r.title), el('small', {}, `${r.state === 'active' ? 'recording' : r.state} · ${when(r.updated)}`));
    if (r.id === current) a.setAttribute('aria-current', 'true');
    return h('li', {}, a);
  }));
  if (!rooms.length && !signedOut) list.append(el('li', { class: 'small' }, 'No rooms yet. Open one in Records.'));
}

// ── the conversation ─────────────────────────────────────────────────────────
const KIND_TAG = { proposal: 'proposal', decision: 'decision', task: 'task', task_update: 'task update', checkpoint: 'checkpoint' };
const SYSTEM = ['session_opened', 'state_change', 'membership', 'moderator', 'artifact_link', 'note_filed'];

function eventLine(e) {
  if (SYSTEM.includes(e.kind)) {
    return el('li', { class: 'rm-sys', 'data-rm-event': e.id, 'data-kind': e.kind }, `${e.actor_label} · ${e.body} · ${when(e.created)}`);
  }
  const li = el('li', { class: 'rm-msg', 'data-rm-event': e.id, 'data-kind': e.kind, 'data-actor': e.actor, 'data-actor-kind': e.actor_kind });
  li.append(el('span', { class: 'rm-av', 'data-kind': e.actor_kind, 'aria-hidden': 'true' }, initial(e.actor_label)));
  const card = el('div', { class: 'rm-card' });
  const meta = h('p', { class: 'rm-meta' }, el('b', { 'data-rm-actor': e.actor }, e.actor_label), el('span', {}, when(e.created)));
  if (e.actor_kind === 'agent') meta.append(el('span', { class: 'rm-tag', 'data-tag': 'agent', title: 'An agent, posting with its own Records key. No live presence is implied.' }, 'agent'));
  if (KIND_TAG[e.kind]) meta.append(el('span', { class: 'rm-tag', 'data-tag': e.kind }, KIND_TAG[e.kind]));
  if (e.origin && e.origin.source_application === 'guild-chat') meta.append(el('span', { class: 'rm-tag', 'data-tag': 'chat' }, 'from Guild Chat'));
  card.append(meta);
  if (e.kind === 'document') {
    const doc = (room.documents || []).find((d) => d.id === e.reference);
    card.append(el('p', { class: 'rm-body' }, 'Shared a file'));
    if (doc) {
      const b = el('button', { type: 'button', class: 'rm-file', 'data-rm-open-file': doc.id });
      b.append(el('span', { class: 'rm-fi' }, (doc.name.split('.').pop() || 'file').toUpperCase().slice(0, 4)), el('span', {}, doc.name));
      card.append(b);
    }
  } else {
    card.append(el('p', { class: 'rm-body' }, e.body));
  }
  li.append(card);
  return li;
}

function renderRoom() {
  const thread = $('[data-rm-thread]');
  if (!room) { thread.replaceChildren(); return; }
  $('[data-rm-title]').textContent = room.title;
  const st = $('[data-rm-state]');
  st.hidden = room.state === 'active';
  st.textContent = room.state === 'paused' ? 'Paused' : room.state === 'closed' ? 'Closed' : '';
  const people = $('[data-rm-people]');
  people.replaceChildren(...(room.members || []).map((m) => el('span', { class: 'rm-av', 'data-kind': m.kind,
    title: `${m.label} · ${m.kind === 'agent' ? 'agent (no live presence)' : 'person'} · ${m.role}` }, initial(m.label))));
  const files = $('[data-rm-files-toggle]');
  files.disabled = false;
  files.textContent = `Files · ${(room.documents || []).length}`;
  const stick = thread.scrollTop + thread.clientHeight >= thread.scrollHeight - 40;
  thread.replaceChildren(...room.events.map(eventLine));
  if (stick) thread.scrollTop = thread.scrollHeight;
  const writable = room.state === 'active' && !signedOut;
  $('[data-rm-send]').disabled = !writable;
  $('[data-rm-attach]').disabled = !writable;
  if (room.state !== 'active') status(room.state === 'paused' ? 'Paused: this room is not recording, so nothing can be added. Your draft is kept.' : 'Closed: open a new session in Records to continue.');
  renderDetails();
  if (!$('[data-rm-files]').hidden) renderFiles();
}

function renderDetails() {
  const box = $('[data-rm-details-body]');
  const facts = h('dl', { class: 'rm-facts' });
  const add = (k, v) => facts.append(el('dt', {}, k), el('dd', {}, v));
  add('Purpose', room.purpose || '');
  add('State', room.state);
  add('Moderator', (room.members.find((m) => m.id === room.moderator) || {}).label || room.moderator);
  add('Opened', when(room.created));
  add('People', room.members.map((m) => `${m.label}${m.kind === 'agent' ? ' (agent)' : ''}`).join(', '));
  const parts = [facts, el('p', { class: 'small' }, 'Agents post with their own Records keys through roomctl; nothing here shows them as online.')];
  if (room.state !== 'closed') {
    const next = room.state === 'active' ? 'paused' : 'active';
    const note = el('input', { 'data-rm-state-note': true, placeholder: next === 'paused' ? 'Why pause? (required)' : 'Resumption note (required)', 'aria-label': 'Checkpoint note' });
    parts.push(note, el('button', { type: 'button', class: 'rm-btn', 'data-rm-set-state': next }, next === 'paused' ? 'Pause recording' : 'Resume recording'));
  }
  parts.push(el('a', { href: `${cfg.api}/v1/rooms/${encodeURIComponent(room.id)}/export?format=markdown` }, 'Download the transcript (Markdown)'));
  box.replaceChildren(...parts);
}

async function loadRoom(id) {
  const r = await records(`/v1/rooms/${encodeURIComponent(id)}`);
  if (r.status === 200) { room = r.body; error(''); renderRoom(); return true; }
  if (r.status === 401) return false;
  room = null;
  renderRoom();
  error(r.status === 403 ? 'You have no access to this room.' : r.status === 404 ? 'That room is not there, or you have no access to it.' : (r.body.error || 'Rooms could not be read.'));
  return false;
}

async function loadAll() {
  if (cfg.state !== 'on') { $('[data-rm-off]').hidden = false; $('[data-rm-send]').disabled = true; return; }
  const r = await records('/v1/rooms');
  if (r.status !== 200) { if (r.status !== 401) error(r.body.error || 'Rooms could not be read.'); renderList(); return; }
  signedOut = false;
  $('[data-rm-signin]').hidden = true;
  rooms = r.body.rooms || [];
  const wanted = new URLSearchParams(window.location.search).get('room') || store.get(`${page.storage_ns}.rooms.last`);
  current = rooms.some((x) => x.id === wanted) ? wanted : (rooms[0] && rooms[0].id) || null;
  renderList();
  if (current) {
    store.set(`${page.storage_ns}.rooms.last`, current);
    $('[data-rm-input]').value = store.get(draftKey()) || $('[data-rm-input]').value;
    await loadRoom(current);
  } else {
    $('[data-rm-title]').textContent = 'Rooms';
    $('[data-rm-send]').disabled = true;
  }
  if (cfg.take) await prepareTake();
}

// ── writing ──────────────────────────────────────────────────────────────────
async function send() {
  const input = $('[data-rm-input]');
  const text = input.value.trim();
  if (!text || !current) return;
  const name = `msg:${current}:${text}`;
  $('[data-rm-send]').disabled = true;
  const r = await records(`/v1/rooms/${encodeURIComponent(current)}/events`, { method: 'POST', body: { body: text, kind: 'message' }, key: keyFor(name) });
  $('[data-rm-send]').disabled = signedOut;
  if (r.status === 201 || r.status === 200) {
    keys.delete(name);
    input.value = '';
    store.set(draftKey(), '');
    status('');
    await loadRoom(current);
  } else if (r.status === 409) {
    keys.delete(name);
    status(`Not sent: ${r.body.error || 'the room is paused'}. Your draft is kept.`);
    await loadRoom(current);
  } else if (r.status !== 401) {
    status(`Not sent: ${r.body.error || 'Rooms answered with an error'}. Your draft is kept; Send again to retry safely.`);
  }
}

async function attach(file) {
  if (!file || !current) return;
  if (file.size > cfg.max_bytes) { status(`Not shared: ${file.name} is larger than ${cfg.max_bytes.toLocaleString()} bytes, today's limit for a Room file.`); return; }
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  const name = `file:${current}:${file.name}:${file.size}:${file.lastModified}`;
  status(`Sharing ${file.name}…`);
  const r = await records(`/v1/rooms/${encodeURIComponent(current)}/documents`, { method: 'POST', body: { name: file.name, base64: btoa(binary) }, key: keyFor(name) });
  if (r.status === 201 || r.status === 200) { keys.delete(name); status(`Shared ${file.name}.`); await loadRoom(current); }
  else if (r.status !== 401) status(`Not shared: ${r.body.error || 'Rooms answered with an error'}.`);
}

// ── files ────────────────────────────────────────────────────────────────────
let openFile = null;

function docEvent(doc) { return room.events.find((e) => e.kind === 'document' && e.reference === doc.id); }
function who(id) { const m = room.members.find((x) => x.id === id); return m ? m.label : id; }

function renderFiles() {
  const body = $('[data-rm-files-body]');
  $('[data-rm-files-back]').hidden = !openFile;
  if (!openFile) {
    $('[data-rm-files-h]').textContent = `Files · ${room.documents.length}`;
    const list = el('div', { class: 'rm-flist' });
    for (const d of room.documents) {
      const b = el('button', { type: 'button', 'data-rm-open-file': d.id });
      const text = el('span');
      text.append(el('strong', {}, d.name), el('small', {}, `${who(d.actor)} · ${when(d.created)} · ${Math.ceil(d.size / 1024)} KB${d.source_note ? ` · ${d.source_note}` : ''}`));
      b.append(el('span', { class: 'rm-fi' }, (d.name.split('.').pop() || 'file').toUpperCase().slice(0, 4)), text);
      list.append(b);
    }
    if (!room.documents.length) list.append(el('p', { class: 'small' }, 'No files yet. Attach one from the composer.'));
    body.replaceChildren(list);
    return;
  }
  const d = room.documents.find((x) => x.id === openFile);
  if (!d) { openFile = null; renderFiles(); return; }
  $('[data-rm-files-h]').textContent = d.name;
  const preview = el('div', { class: 'rm-preview', 'data-rm-preview': d.id }, 'Loading the preview…');
  const facts = h('dl', { class: 'rm-facts' });
  const add = (k, v) => facts.append(el('dt', {}, k), el('dd', {}, v));
  const ev = docEvent(d);
  add('Shared by', who(d.actor));
  add('When', when(d.created));
  add('Size', `${d.size.toLocaleString()} bytes`);
  add('SHA-256', d.sha256);
  if (d.source_note) add('Description', d.source_note);
  if (ev) add('Record', `event ${ev.id.slice(0, 8)}`);
  const refs = (room.artifact_refs || []).filter((a) => ev && a.event_id === ev.id);
  for (const a of refs) add('Project home', `${a.value} · ${a.revision}`);
  const details = h('details', { class: 'rm-details', 'data-rm-file-details': d.id }, el('summary', {}, 'Version and location details'), facts);
  const save = el('form', { class: 'rm-save', 'data-rm-save': d.id });
  save.append(el('label', { for: `rm-save-${d.id}` }, 'Save to project'),
    el('input', { id: `rm-save-${d.id}`, 'data-rm-save-path': true, placeholder: 'docs/design/…', required: true }),
    el('button', { type: 'submit', class: 'rm-btn' }, 'Save'),
    el('p', { class: 'small' }, 'Records keeps the chosen project path as this file\'s home, version v1, with its SHA-256. This page does not write into the repository.'));
  details.append(save);
  const download = el('a', { href: `${cfg.api}/v1/documents/${encodeURIComponent(d.id)}`, download: d.name }, 'Download');
  body.replaceChildren(preview, download, details);
  loadPreview(d, preview);
}

async function loadPreview(d, box) {
  if (!IMAGE.test(d.name) && !TEXT.test(d.name)) { box.textContent = 'No preview for this type here. Download it to open.'; return; }
  let res;
  try { res = await fetch(`${cfg.api}/v1/documents/${encodeURIComponent(d.id)}`, { credentials: 'same-origin', cache: 'no-store' }); } catch (e) { box.textContent = 'The preview could not be loaded.'; return; }
  if (res.status === 401) { showSignin(); box.textContent = 'Sign in to Rooms to see this file.'; return; }
  if (!res.ok) { box.textContent = 'The preview could not be loaded.'; return; }
  const blob = await res.blob();
  if (TEXT.test(d.name)) {
    const text = await blob.text();
    box.replaceChildren(el('pre', {}, text.length > 200000 ? `${text.slice(0, 200000)}\n…` : text));
    return;
  }
  const type = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp' }[d.name.split('.').pop().toLowerCase()];
  const reader = new FileReader();
  reader.onload = () => box.replaceChildren(el('img', { src: reader.result, alt: d.name }));   // a data: URL, allowed by the CSP
  reader.readAsDataURL(new Blob([blob], { type }));
}

function toggleFiles(open) {
  const pane = $('[data-rm-files]');
  pane.hidden = !open;
  $('[data-rm]').dataset.files = open ? 'open' : 'closed';
  $('[data-rm-files-toggle]').setAttribute('aria-expanded', String(open));
  if (open) renderFiles();
}

// ── Take to a Room (a kept note, by id only) ─────────────────────────────────
async function prepareTake() {
  const box = $('[data-rm-take]');
  const r = await apiPost(`/notes/${cfg.take}/share`, { idempotency_key: newKey(), record_mode: recordMode() });
  box.hidden = false;
  if (!r.ok || !r.body.note) {
    $('[data-rm-take-text]').textContent = '';
    $('[data-rm-take-meta]').textContent = (r.body && r.body.message) || 'That note cannot be shared.';
    $('[data-rm-take-send]').disabled = true;
    return;
  }
  takeNote = r.body.note;
  $('[data-rm-take-text]').textContent = takeNote.text;
  $('[data-rm-take-meta]').textContent = `${takeNote.author_label} · ${when(takeNote.created_at)} · note #${takeNote.id}. Only this kept note is shared, never a selection or text from the screen.`;
  $('[data-rm-take-send]').disabled = !current || signedOut || (room && room.state !== 'active');
}

async function sendTake() {
  if (!takeNote || !current) return;
  const name = `take:${current}:${takeNote.id}`;
  const body = { kind: 'message', body: `From Guild Chat · note #${takeNote.id}\n\n${takeNote.text}`,
    origin: { source_application: 'guild-chat', mode: 'relay', execution_id: `guild-note-${takeNote.id}` } };
  const r = await records(`/v1/rooms/${encodeURIComponent(current)}/events`, { method: 'POST', body, key: keyFor(name) });
  const out = $('[data-rm-take-result]');
  out.hidden = false;
  if (r.status === 201 || r.status === 200) {
    out.textContent = 'Shared into this room.';
    $('[data-rm-take-send]').disabled = true;
    const url = new URL(window.location.href);
    url.searchParams.delete('take');
    window.history.replaceState(null, '', url);
    await loadRoom(current);
  } else if (r.status !== 401) {
    out.textContent = `Not shared: ${r.body.error || 'Rooms answered with an error'}.`;
  }
}

function bind() {
  const input = $('[data-rm-input]');
  input.addEventListener('input', () => {
    store.set(draftKey(), input.value);
    input.style.setProperty('height', 'auto');
    input.style.setProperty('height', `${Math.min(input.scrollHeight, 160)}px`);
  });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
  $('[data-rm-composer]').addEventListener('submit', (e) => { e.preventDefault(); send(); });
  $('[data-rm-attach]').addEventListener('change', (e) => { attach(e.target.files[0]); e.target.value = ''; });
  $('[data-rm-files-toggle]').addEventListener('click', () => { openFile = null; toggleFiles($('[data-rm-files]').hidden); });
  $('[data-rm-files-close]').addEventListener('click', () => toggleFiles(false));
  $('[data-rm-files-back]').addEventListener('click', () => { openFile = null; renderFiles(); });
  document.addEventListener('click', (e) => {
    const f = e.target.closest('[data-rm-open-file]');
    if (f) { openFile = f.dataset.rmOpenFile; toggleFiles(true); }
  });
  $('[data-rm-files]').addEventListener('submit', async (e) => {
    const form = e.target.closest('[data-rm-save]');
    if (!form) return;
    e.preventDefault();
    const d = room.documents.find((x) => x.id === form.dataset.rmSave);
    const path = $('[data-rm-save-path]', form).value.trim();
    if (!path || path.startsWith('/') || path.split('/').includes('..')) { status('Use a project path inside the repository, like docs/design/name.md.'); return; }
    const ev = docEvent(d);
    const name = `save:${d.id}:${path}`;
    const r = await records(`/v1/rooms/${encodeURIComponent(current)}/artifact-refs`, { method: 'POST',
      body: { event_id: ev.id, kind: 'work_artifact', value: path, revision: 'v1', sha256: d.sha256, label: d.name }, key: keyFor(name) });
    status(r.status === 201 || r.status === 200 ? `Saved to project: ${path} · v1.` : `Not saved: ${r.body.error || 'Rooms answered with an error'}.`);
    if (r.status === 201 || r.status === 200) { keys.delete(name); await loadRoom(current); }
  });
  $('[data-rm-details-body]').addEventListener('click', async (e) => {
    const b = e.target.closest('[data-rm-set-state]');
    if (!b) return;
    const note = $('[data-rm-state-note]').value.trim();
    if (!note) { status('Add a short note for the record first.'); return; }
    const name = `state:${current}:${room.version}:${b.dataset.rmSetState}`;
    const r = await records(`/v1/rooms/${encodeURIComponent(current)}/state`, { method: 'POST',
      body: { state: b.dataset.rmSetState, version: room.version, checkpoint: note }, key: keyFor(name) });
    status(r.status === 200 ? (b.dataset.rmSetState === 'paused' ? 'Paused.' : 'Recording again.') : `Not changed: ${r.body.error || 'Rooms answered with an error'}.`);
    if (r.status === 200) { status(''); $('[data-rm-details]').open = false; await loadRoom(current); }
  });
  $('[data-rm-list-open]').addEventListener('click', () => { $('[data-rm]').dataset.list = 'open'; });
  $('[data-rm-list-close]').addEventListener('click', () => { $('[data-rm]').dataset.list = 'closed'; });
  $('[data-rm-take-send]').addEventListener('click', sendTake);
  $('[data-rm-take-cancel]').addEventListener('click', () => { $('[data-rm-take]').hidden = true; takeNote = null; });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && current && !signedOut) loadRoom(current); });
}

export function initRooms(p) {
  page = p;
  const raw = document.getElementById('rooms-data');
  if (!raw || !$('[data-rm]')) return;
  cfg = JSON.parse(raw.textContent);
  bind();
  loadAll();
  poll = window.setInterval(() => { if (!document.hidden && current && !signedOut) loadRoom(current); }, 15000);
}

export const _forTests = { stop: () => window.clearInterval(poll) };
