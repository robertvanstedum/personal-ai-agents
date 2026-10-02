// Rooms (Guild 1.1 slice 4, spec §6): group chat on Records, through the dev
// bridge at /app/records/api/ with Records' own sign-in. This page never
// holds a Records credential: a 401 shows "Sign in to Rooms" (Records' own
// page) and the draft stays in this tab. Records' own actor and grant checks
// decide every action; a paused room refuses writes (409) and says so. Agent
// messages carry their Records actor and an "agent" label; no live presence
// is implied. Take to a Room shares a kept, on-the-record Chat note by id
// only: the Guild API answers with the stored note, and only that is sent.
// No model call here. Rooms R1 (docs/specs/minimoi-connected-work/ROOMS_R1.md):
// Master Craftsman answers in the room through the Rooms worker; this page
// shows the meeting from Records (/turns): who is here or reachable, what MC
// is doing, and what Robert can do next (Retry, Continue without, End).
import { $, $$, el, announce } from './dom.js';
import { apiPost, recordMode } from './api.js';
import { live, onChange, setOff } from './state.js';
import { newKey } from './actions.js';

let cfg;
let page;
let rooms = [];
let room = null;              // the open room, as Records answers it
let current = null;           // its id
let signedOut = false;
let takeNote = null;          // { id, text, ... } from the Guild API
let poll = null;
let meeting = null;           // Records' /turns view: participants, turns, routing notes (Rooms R1)
let cards = [];               // teammate cards (owner)
let target = null;            // the recipient chip, if one is chosen
let fast = null;              // the quick poll while a turn is in flight
let here = null;              // the presence heartbeat
let pendingAct = null;        // 'paused' or 'closed', waiting for its note
const dismissed = new Set();  // turns Robert chose to continue without
const ACTIVE = ['queued', 'claimed', 'running', 'recovering', 'cancel_requested'];
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
const signedOutDraftKey = () => `${page.storage_ns}.rooms.draft.none`;
const when = (iso) => { const t = Date.parse(iso); return Number.isNaN(t) ? '' : new Date(t).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); };
const initial = (label) => (label || '?').trim().slice(0, 2).toUpperCase();

async function records(path, { method = 'GET', body, key } = {}) {
  const headers = { Accept: 'application/json' };
  if (method !== 'GET') { headers['Content-Type'] = 'application/json'; headers['Idempotency-Key'] = key || newKey(); }
  let res;
  try {
    res = await fetch(`${cfg.api}${path}`, { method, headers, credentials: 'same-origin', cache: 'no-store', redirect: 'manual',
      body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) {
    return { status: 0, body: { error: 'Rooms could not be reached. Nothing was sent; your draft is kept.' } };
  }
  if (res.type === 'opaqueredirect') { portalSignedOut(); return { status: 401, body: { error: 'Your mini-moi sign-in ended.' } }; }
  let data = {};
  try { data = await res.json(); } catch (e) { data = {}; }
  if (res.status === 401) showSignin();
  return { status: res.status, body: data };
}

function portalSignedOut() {
  // The portal session (not Records') ended: the bridge redirects to the
  // mini-moi login. Say so instead of showing nothing (#288 review F9).
  signedOut = true;
  $('[data-rm-send]').disabled = true;
  error('Your mini-moi sign-in ended. Sign in again, then come back; your draft is kept.');
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
  if (e.origin && e.origin.source_application === 'guild-chat' && e.actor_kind !== 'agent') meta.append(el('span', { class: 'rm-tag', 'data-tag': 'chat' }, 'from Guild Chat'));
  if (earlier().has(e.id)) meta.append(el('span', { class: 'rm-tag', 'data-tag': 'earlier', title: 'You wrote again while this reply was being written.' }, 'answered an earlier message'));
  if (lateInRound().has(e.id)) meta.append(el('span', { class: 'rm-tag', 'data-tag': 'late', title: 'The round went on without this reply; it arrived afterwards.' }, 'answered later in this round'));
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
  renderPeople();
  const files = $('[data-rm-files-toggle]');
  files.disabled = false;
  files.textContent = `Files · ${(room.documents || []).length}`;
  const stick = thread.scrollTop + thread.clientHeight >= thread.scrollHeight - 40;
  thread.replaceChildren(...room.events.map(eventLine));
  if (stick) thread.scrollTop = thread.scrollHeight;
  const writable = room.state === 'active' && !signedOut;
  $('[data-rm-send]').disabled = !writable;
  $('[data-rm-attach]').disabled = !writable;
  $('[data-rm-input]').disabled = room.state === 'closed' || signedOut;
  $('[data-rm-closed]').hidden = room.state !== 'closed';
  renderControls();
  if (room.state !== 'active') status(room.state === 'paused' ? 'Paused: this room is not recording, so nothing can be added. Your draft is kept.' : 'Closed: the record is kept. Continue the conversation to go on.');
  else if (/^(Paused|Closed):/.test($('[data-rm-status]').textContent)) status('');
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
  const parts = [facts, el('p', { class: 'small' }, 'Teammates show as here, reachable or away from real check-ins only. Other agents post with their own Records keys.')];
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
  if (r.status === 200) { const changed = room?.id !== r.body.id; room = r.body; error(''); renderRoom(); loadMeeting(); if (changed) heartbeat(); return true; }
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
    // A draft typed before the Records sign-in (or before any room) was kept
    // under "none"; it follows into the room now (#288 review F3).
    const early = store.get(signedOutDraftKey());
    const draft = store.get(draftKey()) || early || $('[data-rm-input]').value;
    $('[data-rm-input]').value = draft;
    if (early) { store.set(draftKey(), draft); store.set(signedOutDraftKey(), ''); }
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
  const body = { body: text, kind: 'message' };
  if (target && (meeting?.participants || []).some((p) => p.id === target)) body.target = target;
  const r = await records(`/v1/rooms/${encodeURIComponent(current)}/events`, { method: 'POST', body, key: keyFor(name) });
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
  save.append(el('label', { for: `rm-save-${d.id}` }, 'Project home'),
    el('input', { id: `rm-save-${d.id}`, 'data-rm-save-path': true, placeholder: 'docs/design/…', required: true }),
    el('button', { type: 'submit', class: 'rm-btn' }, 'Record home'),
    el('p', { class: 'small' }, 'Records keeps the chosen project path as this file\'s home, version v1, with its SHA-256. Nothing is written into the repository; commit the file there yourself.'));
  details.append(save);
  const download = el('a', { href: `${cfg.api}/v1/documents/${encodeURIComponent(d.id)}`, download: d.name }, 'Download');
  body.replaceChildren(preview, download, details);
  loadPreview(d, preview);
}

async function loadPreview(d, box) {
  if (!IMAGE.test(d.name) && !TEXT.test(d.name)) { box.textContent = 'No preview for this type here. Download it to open.'; return; }
  let res;
  try { res = await fetch(`${cfg.api}/v1/documents/${encodeURIComponent(d.id)}`, { credentials: 'same-origin', cache: 'no-store', redirect: 'manual' }); } catch (e) { box.textContent = 'The preview could not be loaded.'; return; }
  if (res.type === 'opaqueredirect') { portalSignedOut(); box.textContent = 'Sign in to mini-moi again to see this file.'; return; }
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
const takeKeyName = (noteId) => `${page.storage_ns}.rooms.take.${current}.${noteId}`;

function dropTakeParam() {
  const url = new URL(window.location.href);
  url.searchParams.delete('take');
  window.history.replaceState(null, '', url);
}

// Switching mode, either way, discards a prepared Take (spec §8, §11): it is
// never sent after the switch; open it again from Chat (#288 review F2).
function discardTake(reason) {
  const shown = takeNote || !$('[data-rm-take]').hidden;
  takeNote = null;
  $('[data-rm-take-send]').disabled = true;
  $('[data-rm-take]').hidden = true;
  dropTakeParam();
  if (shown && reason) status(reason);
}

async function prepareTake() {
  const box = $('[data-rm-take]');
  if (live.off) { dropTakeParam(); return; }
  if (!live.known) {
    // This tab does not know its record mode yet: nothing is fetched until
    // Robert confirms on the record here (#288 re-check R1).
    box.hidden = false;
    $('[data-rm-take-text]').textContent = '';
    $('[data-rm-take-meta]').textContent = 'This tab does not know whether you are on the record. Confirm on the record to prepare this share.';
    $('[data-rm-take-confirm]').hidden = false;
    $('[data-rm-take-send]').disabled = true;
    return;
  }
  $('[data-rm-take-confirm]').hidden = true;
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
  if (live.off || !live.known) { discardTake('Not shared: you went off the record. Open Take to a Room from Chat again.'); return; }
  // One idempotency key per room and note, kept for this tab, so a reload
  // cannot share the same note twice (#288 review F8).
  const name = `take:${current}:${takeNote.id}`;
  const stored = store.get(takeKeyName(takeNote.id));
  if (stored && !keys.has(name)) keys.set(name, stored);
  store.set(takeKeyName(takeNote.id), keyFor(name));
  const body = { kind: 'message', body: `From Guild Chat · note #${takeNote.id}\n\n${takeNote.text}`,
    origin: { source_application: 'guild-chat', mode: 'relay', execution_id: `guild-note-${takeNote.id}` } };
  const r = await records(`/v1/rooms/${encodeURIComponent(current)}/events`, { method: 'POST', body, key: keyFor(name) });
  const out = $('[data-rm-take-result]');
  out.hidden = false;
  if (r.status === 201 || r.status === 200) {
    out.textContent = 'Shared into this room.';
    $('[data-rm-take-send]').disabled = true;
    takeNote = null;
    dropTakeParam();
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
    status(r.status === 201 || r.status === 200 ? `Project home recorded: ${path} · v1. Nothing was written to the repository.` : `Not recorded: ${r.body.error || 'Rooms answered with an error'}.`);
    if (r.status === 201 || r.status === 200) { keys.delete(name); await loadRoom(current); }
  });
  $('[data-rm-details-body]').addEventListener('click', async (e) => {
    const b = e.target.closest('[data-rm-set-state]');
    if (!b) return;
    const note = $('[data-rm-state-note]').value.trim();
    if (!note) { status('Add a short note for the record first.'); return; }
    if (await setState(b.dataset.rmSetState, note)) $('[data-rm-details]').open = false;
  });
  $('[data-rm-list-open]').addEventListener('click', () => { $('[data-rm]').dataset.list = 'open'; });
  $('[data-rm-list-close]').addEventListener('click', () => { $('[data-rm]').dataset.list = 'closed'; });
  $('[data-rm-take-send]').addEventListener('click', sendTake);
  $('[data-rm-take-cancel]').addEventListener('click', () => { $('[data-rm-take]').hidden = true; takeNote = null; dropTakeParam(); });
  $('[data-rm-take-confirm]').addEventListener('click', () => setOff(false));
  onChange(() => {
    const wanted = cfg.take && new URLSearchParams(window.location.search).has('take');
    // Off the record, a pending or prepared Take is always dropped (#288 re-check R2).
    if (takeNote || live.off) {
      discardTake('Take to a Room was discarded because the record mode changed. Nothing was shared.');
    } else if (wanted && live.known && !signedOut) {
      prepareTake();                         // confirmed on the record after loading: prepare it now
    }
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && current && !signedOut) loadRoom(current); });
}

// ── the meeting (Rooms R1, ROOMS_R1.md §3.11) ────────────────────────────────
// Everything here comes from Records: the participant strip (cards and
// people only), turn states, routing notes. Nothing is inferred on the page.
const labelOf = (id) => ((meeting?.participants || []).find((p) => p.id === id) || {}).label
  || ((room?.members || []).find((m) => m.id === id) || {}).label || id;
const handle = (id) => (id === 'everyone' ? 'everyone' : id.length <= 3 ? id.toUpperCase() : labelOf(id).split(' ')[0]);
const facilitator = () => meeting?.meeting?.facilitator || null;

function lateInRound() {
  return new Set((meeting?.turns || []).filter((t) => t.state === 'committed' && t.late_in_round && t.event_id).map((t) => t.event_id));
}

const ROUND_WORD = (t) => {
  if (t.state === 'committed') return t.late_in_round ? 'answered (later)' : 'answered';
  if (['claimed', 'running', 'recovering'].includes(t.state)) return 'is answering…';
  if (t.state === 'queued') return 'next';
  if (t.state === 'cancel_requested') return 'stopping…';
  if (t.state === 'uncertain') return 'may have answered';
  if (t.state === 'cancelled' && t.disposition === 'skipped_away') return 'skipped (away)';
  if (t.state === 'cancelled' && t.disposition === 'round_deadline') return 'stopped (out of time)';
  if (t.state === 'cancelled' && t.disposition === 'budget_exhausted') return 'out of turns this hour';
  if (t.state === 'superseded') return 'superseded by your newer message';
  return t.state === 'failed' || t.state === 'expired' ? 'not answered' : t.state;
};

function roundLine() {
  // Rooms R3a: the newest round, one line: "Round: MC answered · Claude Code is answering… · Codex next".
  const inRounds = (meeting?.turns || []).filter((t) => t.round);
  if (!inRounds.length) return null;
  const newest = inRounds.reduce((a, b) => (a.created > b.created ? a : b)).round.round_id;
  const members = inRounds.filter((t) => t.round.round_id === newest).sort((a, b) => a.round.position - b.round.position);
  return { text: `Round: ${members.map((t) => `${labelOf(t.addressee)} ${ROUND_WORD(t)}`).join(' · ')}`, ids: new Set(members.map((t) => t.id)) };
}

function earlier() {
  return new Set((meeting?.turns || []).filter((t) => t.state === 'committed' && t.answered_earlier && t.event_id).map((t) => t.event_id));
}

async function loadMeeting() {
  if (!current || signedOut) return;
  const r = await records(`/v1/rooms/${encodeURIComponent(current)}/turns`);
  if (r.status !== 200) return;
  const committed = new Set((meeting?.room === current ? meeting.turns : []).filter((t) => t.state === 'committed').map((t) => t.id));
  const first = !meeting || meeting.room !== current;
  meeting = r.body;
  renderPeople();
  renderTurns();
  renderTo();
  renderControls();
  const fresh = meeting.turns.some((t) => t.state === 'committed' && !committed.has(t.id));
  if (fresh && !first) await loadRoom(current);         // the reply arrived: show it
  else if (earlier().size) renderRoomThreadTags();
  window.clearTimeout(fast);
  const joining = meeting.participants.some((p) => p.teammate && p.rsvp === 'invited' && p.proven_at);
  if (joining || meeting.turns.some((t) => ACTIVE.includes(t.state))) fast = window.setTimeout(loadMeeting, 2500);
}

function renderRoomThreadTags() {
  for (const id of earlier()) {
    const meta = $(`[data-rm-event="${id}"] .rm-meta`);
    if (meta && !meta.querySelector('[data-tag="earlier"]')) {
      meta.append(el('span', { class: 'rm-tag', 'data-tag': 'earlier' }, 'answered an earlier message'));
    }
  }
}

const REACH = { here: 'here', reachable: 'reachable', answering: 'answering…', away: 'away' };

function renderPeople() {
  const people = $('[data-rm-people]');
  const list = meeting && meeting.room === current ? meeting.participants
    : (room?.members || []).filter((m) => m.kind === 'human').map((m) => ({ ...m, rsvp: 'accepted', reach: { state: 'away' } }));
  people.replaceChildren(...list.map((p) => {
    const reach = p.reach || { state: 'away' };
    const why = reach.state === 'away' && reach.reason ? `away: ${reach.reason}` : REACH[reach.state] || reach.state;
    const chip = el('span', { class: 'rm-person', 'data-person': p.id, 'data-reach': reach.state, 'data-rsvp': p.rsvp,
      title: `${p.label} · ${why}${p.rsvp && p.rsvp !== 'accepted' ? ` · ${p.rsvp.replace('_', ' ')}` : ''}` });
    chip.append(el('span', { class: 'rm-av', 'data-kind': p.kind, 'aria-hidden': 'true' }, initial(p.label)),
      el('span', { class: 'rm-dot', 'aria-hidden': 'true' }), el('span', { class: 'rm-pname' }, p.label));
    if (p.rsvp && p.rsvp !== 'accepted') chip.append(el('span', { class: 'rm-rsvp' }, p.rsvp === 'no_response' ? 'no response' : p.rsvp));
    chip.append(el('span', { class: 'visually-hidden' }, `: ${why}`));
    return chip;
  }));
}

// Why a teammate did not answer, said plainly with what to do next.
function failureText(who, reason) {
  switch (reason) {
    case 'signed_out': return `${who} is signed out on your Mac (its sign-in was rejected). Sign in again in Terminal, then Retry.`;
    case 'runner_unavailable': return `${who} could not start on your Mac (the app is missing or would not launch).`;
    case 'startup_inputs': return `${who} did not start: your Mac has settings or files for it that Rooms does not allow.`;
    case 'not_proven_with_this_runner': return `${who} is not proven yet. Use Invite → Prove first.`;
    case 'runner_changed_since_proof': return `${who} was updated since it was proven. Use Invite → Prove again.`;
    case 'relay_busy': return `${who} was busy.`;
    case 'empty_reply': return `${who} answered with nothing.`;
    case 'unresolved_started': return `${who} started but no reply arrived in time. It may still have answered; nothing was received.`;
    default: return `${who}'s reply failed (${(reason || 'unknown').replace(/_/g, ' ')}).`;
  }
}

function turnLine(t) {
  const who = labelOf(t.addressee);
  switch (t.state) {
    case 'queued': return { text: `${who} will answer next…`, wait: true };
    case 'claimed': case 'running': case 'recovering': return { text: `${who} is answering…`, wait: true };
    case 'cancel_requested': return { text: `Stopping ${who}'s reply…`, wait: true };
    case 'uncertain':
      if (t.disposition === 'confirmed_absent') return { text: `Not answered: ${who}'s worker stopped before answering.`, actions: ['retry', 'continue'] };
      if (t.disposition === 'unresolved_started') return { text: failureText(who, 'unresolved_started'), actions: ['attempt', 'continue'] };
      return { text: `Checking whether ${who}'s reply was saved…`, actions: ['continue'] };
    case 'expired': return { text: `Not answered: ${who} was busy with an earlier message.`, actions: ['retry', 'continue', 'end'] };
    case 'failed': return { text: `Not answered: ${failureText(who, t.disposition)}`, actions: ['retry', 'continue', 'end'] };
    case 'cancelled':
      if (t.disposition === 'budget_exhausted') return { text: `${who} has used its ${meeting?.meeting?.max_turns || 20} turns for this hour.`, actions: ['renew'] };
      if (t.disposition === 'window_expired') return { text: `${who}'s hour in this meeting is over.`, actions: ['renew'] };
      if (t.stop_ack === 'worker') return { text: 'Reply stopped.' };
      if (t.stop_ack === 'none') return { text: "Reply fenced; worker didn't confirm the stop." };
      if (t.disposition === 'continued_without') return null;
      if (['paused_by_owner', 'meeting_ended', 'stopped_by_owner', 'resumed'].includes(t.disposition)) return null;
      return { text: `Not answered: ${(t.disposition || 'cancelled').replace(/_/g, ' ')}.`, actions: ['retry', 'continue'] };
    default: return null;
  }
}

function noteLine(n) {
  const f = facilitator();
  const alt = f ? ` ${labelOf(f)} can answer — say @${handle(f)} or leave it unaddressed.` : '';
  if (n.disposition === 'no_connector') return `Not sent to ${n.label}: not available in Rooms yet.${alt}`;
  if (n.disposition === 'not_proven') return `Not sent to ${n.label}: not yet proven. Use Invite → Prove first.`;
  if (n.disposition === 'not_invited') return `Not sent to ${n.label}: not invited to this meeting. Use Invite.`;
  return `Not sent to ${n.label}: not in this meeting.`;
}

function renderTurns() {
  const box = $('[data-rm-turns]');
  const rows = [];
  if (meeting && meeting.room === current) {
    const round = roundLine();
    if (round) rows.push({ text: round.text, round: true });
    const latest = new Map();
    for (const t of meeting.turns) if (!latest.has(t.addressee)) latest.set(t.addressee, t);   // newest first
    // An unresolved earlier turn stays visible beside newer ones (review F9).
    const shown = [...meeting.turns.filter((t) => t.state === 'uncertain' && latest.get(t.addressee) !== t), ...latest.values()];
    for (const t of shown) {
      if (dismissed.has(t.id)) continue;
      // Round turns are summed up in the round line; only those needing an action get their own.
      if (round && round.ids.has(t.id) && !['uncertain', 'failed', 'expired'].includes(t.state)) continue;
      const line = turnLine(t);
      if (line) rows.push({ ...line, turn: t });
    }
    const lastMine = [...(room?.events || [])].reverse().find((e) => e.actor === 'robert' && e.kind === 'message');
    for (const n of meeting.routing_notes) if (lastMine && n.trigger_seq === lastMine.seq) rows.push({ text: noteLine(n), note: true });
  }
  box.hidden = !rows.length;
  box.replaceChildren(...rows.map((row) => {
    const p = el('p', { class: 'rm-turn', 'data-rm-turn': row.turn ? row.turn.id : '', 'data-state': row.turn ? row.turn.state : (row.round ? 'round' : 'note') });
    p.append(el('span', {}, row.text));
    for (const a of row.actions || []) {
      const label = { retry: 'Retry', attempt: 'Start a new attempt', continue: `Continue without ${labelOf(row.turn.addressee)}`, end: 'End',
        renew: `Give ${labelOf(row.turn.addressee)} another hour` }[a];
      p.append(el('button', { type: 'button', class: 'rm-btn', 'data-rm-turn-action': a, 'data-turn': row.turn.id }, label));
    }
    return p;
  }));
}

async function turnAction(button) {
  const id = button.dataset.turn;
  const action = button.dataset.rmTurnAction;
  if (action === 'end') { openAct('closed'); return; }
  if (action === 'renew') {
    const r = await records(`/v1/rooms/${encodeURIComponent(current)}/renew`, { method: 'POST', body: {}, key: keyFor(`renew:${id}`) });
    if (r.status !== 200) { status(`Not renewed: ${r.body.error || 'Rooms answered with an error'}.`); return; }
    status('Another hour: write again and it answers.');
    dismissed.add(id);
    await loadMeeting();
    return;
  }
  if (action === 'continue') {
    const t = meeting.turns.find((x) => x.id === id);
    if (t && ['queued', 'claimed', 'running', 'recovering'].includes(t.state)) {
      await records(`/v1/rooms/${encodeURIComponent(current)}/turns/${encodeURIComponent(id)}/cancel`, { method: 'POST', body: {}, key: keyFor(`cancel:${id}`) });
    }
    dismissed.add(id);
    renderTurns();
    return;
  }
  // Retry / a new attempt may use a second turn: one more click confirms it.
  if (button.dataset.armed !== 'yes') {
    button.dataset.armed = 'yes';
    button.textContent = 'Confirm: may use a second turn';
    return;
  }
  const r = await records(`/v1/rooms/${encodeURIComponent(current)}/turns/${encodeURIComponent(id)}/retry`, { method: 'POST', body: { confirm: true }, key: keyFor(`retry:${id}`) });
  status(r.status === 201 || r.status === 200 ? '' : `Not retried: ${r.body.error || 'Rooms answered with an error'}.`);
  await loadMeeting();
}

function renderTo() {
  const box = $('[data-rm-to]');
  const mates = (meeting?.participants || []).filter((p) => p.teammate && p.rsvp === 'accepted');
  if (target && !mates.some((p) => p.id === target)) target = null;
  box.hidden = !mates.length || !room || room.state === 'closed';
  box.replaceChildren(el('span', { class: 'small' }, 'To:'), ...mates.map((p) => el('button', {
    type: 'button', class: 'rm-chip', 'data-rm-to-chip': p.id, 'aria-pressed': String(target === p.id),
    title: target === p.id ? `Only ${p.label} answers` : `Send to ${p.label}` }, p.label)),
  el('span', { class: 'small rm-to-hint' }, target ? '' : `Nobody chosen: ${facilitator() ? labelOf(facilitator()) : 'nobody'} answers.`));
}

function renderControls() {
  const box = $('[data-rm-controls]');
  box.hidden = !room || signedOut || room.state === 'closed' || cfg.state !== 'on';
  if (!room) return;
  $('[data-rm-pause]').textContent = room.state === 'paused' ? 'Resume' : 'Pause';
  $('[data-rm-stop]').disabled = room.state !== 'active';
}

function openAct(next) {
  pendingAct = next;
  const form = $('[data-rm-act]');
  form.hidden = false;
  $('[data-rm-act-label]').textContent = next === 'closed' ? 'End the meeting: a closing note for the record'
    : next === 'paused' ? 'Pause: why? (a note for the record)' : 'Resume: a note for the record';
  $('[data-rm-act-confirm]').textContent = next === 'closed' ? 'End meeting' : next === 'paused' ? 'Pause' : 'Resume';
  $('[data-rm-act-note]').focus();
}

async function setState(next, note) {
  const name = `state:${current}:${room.version}:${next}`;
  const r = await records(`/v1/rooms/${encodeURIComponent(current)}/state`, { method: 'POST',
    body: { state: next, version: room.version, checkpoint: note }, key: keyFor(name) });
  if (r.status === 200) { status(''); await loadRoom(current); return true; }
  status(`Not changed: ${r.body.error || 'Rooms answered with an error'}.`);
  return false;
}

async function stopAll() {
  const r = await records(`/v1/rooms/${encodeURIComponent(current)}/stop`, { method: 'POST', body: {}, key: keyFor(`stop:${current}:${room.version}`) });
  if (r.status === 200) { await loadRoom(current); status('Stop requested; the meeting is paused. Each reply shows when it has actually stopped.'); }
  else status(`Not stopped: ${r.body.error || 'Rooms answered with an error'}.`);
}

// ── Invite and Prove (M1: first use is explicit and server-enforced) ────────
async function loadCards() {
  const r = await records('/v1/teammates');
  cards = r.status === 200 ? r.body.teammates : [];
  return cards;
}

function cardState(c) {
  return c.proven_at ? `Proven ${when(c.proven_at)}` : 'Not yet proven — Prove first (one turn, needs your OK)';
}

async function openInvite() {
  await loadCards();
  renderCards();
  $('[data-rm-invite-status]').textContent = '';
  $('[data-rm-invite-dialog]').showModal();
}

function renderCards() {
  const list = $('[data-rm-cards]');
  const inRoom = new Map((meeting?.participants || []).map((p) => [p.id, p]));
  list.replaceChildren(...cards.map((c) => {
    const li = el('li', { class: 'rm-card-row', 'data-rm-card': c.principal });
    const member = inRoom.get(c.principal);
    li.append(el('strong', {}, c.label), el('span', { class: 'small', 'data-rm-card-state': true }, c.connected ? cardState(c) : 'No connector in Rooms yet'));
    if (c.connected && !c.proven_at) li.append(el('button', { type: 'button', class: 'rm-btn', 'data-rm-prove': c.principal }, 'Prove first'));
    const invited = member && member.rsvp !== 'declined';
    li.append(el('button', { type: 'button', class: 'rm-btn', 'data-rm-invite-card': c.principal, disabled: invited || !room || room.state === 'closed' ? '' : null },
      invited ? (member.rsvp === 'accepted' ? 'In this meeting' : 'Invited') : 'Invite'));
    return li;
  }));
  if (!cards.length) list.append(el('li', { class: 'small' }, 'No teammate cards yet. A teammate needs a card and a connector before it can join.'));
}

async function inviteCard(principal) {
  const r = await records(`/v1/rooms/${encodeURIComponent(current)}/invite`, { method: 'POST', body: { actor: principal }, key: keyFor(`invite:${current}:${principal}`) });
  const out = $('[data-rm-invite-status]');
  if (r.status !== 201 && r.status !== 200) { out.textContent = `Not invited: ${r.body.error || 'Rooms answered with an error'}.`; return; }
  const rsvp = r.body.result.rsvp;
  const name = (cards.find((c) => c.principal === principal) || {}).label || principal;
  out.textContent = rsvp.rsvp === 'accepted' ? `${name} joined.` : rsvp.rsvp_reason === 'not_proven'
    ? `${name} is invited and answers once proven. Prove first.` : `${name} is invited and joins within a few seconds.`;
  await loadMeeting();
  renderCards();
}

async function prove(button) {
  const principal = button.dataset.rmProve;
  if (button.dataset.armed !== 'yes') {
    button.dataset.armed = 'yes';
    button.textContent = 'Confirm: use one paid turn';
    return;
  }
  button.disabled = true;
  const out = $('[data-rm-invite-status]');
  const name = (cards.find((c) => c.principal === principal) || {}).label || principal;
  const r = await records(`/v1/teammates/${encodeURIComponent(principal)}/prove`, { method: 'POST', body: { confirm: true }, key: keyFor(`prove:${principal}`) });
  if (r.status !== 202 && r.status !== 200) { out.textContent = `Proof not started: ${r.body.error || 'Rooms answered with an error'}.`; button.disabled = false; return; }
  out.textContent = `Proving ${name}: one question, one reply…`;
  const until = Date.now() + 240000;              // the turn limit (180 s) plus recovery
  while (Date.now() < until) {
    await new Promise((done) => { window.setTimeout(done, 2000); });
    const s = await records(`/v1/teammates/${encodeURIComponent(principal)}/prove`);
    if (s.status !== 200) continue;
    if (s.body.proven_at) {
      keys.delete(`prove:${principal}`);
      out.textContent = `${name} is proven (${when(s.body.proven_at)}). It joins this meeting on its next check-in.`;
      await loadCards(); renderCards(); await loadMeeting();
      return;
    }
    const t = s.body.turn;
    if (t && ['failed', 'cancelled', 'expired', 'uncertain', 'abandoned'].includes(t.state)) {
      keys.delete(`prove:${principal}`);
      out.textContent = `The proof did not finish. ${failureText(name, t.disposition || t.state)} You can try again.`;
      await loadCards(); renderCards();
      return;
    }
  }
  out.textContent = `${name} has not answered the proof yet. Its worker may be away; you can close this and check later.`;
}

// ── New conversation and Continue conversation ──────────────────────────────
async function openNew() {
  await loadCards();
  const box = $('[data-rm-new-cards]');
  box.replaceChildren(...cards.filter((c) => c.connected).map((c) => {
    const label = el('label', { class: 'rm-new-card' });
    label.append(el('input', { type: 'checkbox', value: c.principal, 'data-rm-new-card': c.principal, checked: '' }),
      el('span', {}, ` ${c.label} · ${c.proven_at ? 'proven' : 'not yet proven (Prove from Invite)'}`));
    return label;
  }));
  if (!box.children.length) box.append(el('p', { class: 'small' }, 'No teammate is connected yet; you can still record a conversation.'));
  $('[data-rm-new-status]').textContent = '';
  $('[data-rm-new-dialog]').showModal();
  $('[data-rm-new-title]').focus();
}

async function createNew() {
  const title = $('[data-rm-new-title]').value.trim();
  const purpose = $('[data-rm-new-purpose]').value.trim();
  const out = $('[data-rm-new-status]');
  if (!title || !purpose) { out.textContent = 'A title and a purpose are needed: both go on the record.'; return; }
  const name = `new:${title}:${purpose}`;
  const r = await records('/v1/rooms', { method: 'POST', body: { title, purpose, mode: 'meeting', recording_acknowledged: true }, key: keyFor(name) });
  if (r.status !== 201 && r.status !== 200) { out.textContent = `Not started: ${r.body.error || 'Rooms answered with an error'}.`; return; }
  const id = r.body.result.id;
  for (const box of $$('[data-rm-new-card]')) {
    if (!box.checked) continue;
    await records(`/v1/rooms/${encodeURIComponent(id)}/invite`, { method: 'POST', body: { actor: box.value }, key: keyFor(`invite:${id}:${box.value}`) });
  }
  keys.delete(name);
  window.location.search = `?room=${encodeURIComponent(id)}`;
}

async function continueConversation() {
  const r = await records(`/v1/rooms/${encodeURIComponent(current)}/continue`, { method: 'POST', body: {}, key: keyFor(`continue:${current}`) });
  if (r.status === 201 || r.status === 200) window.location.search = `?room=${encodeURIComponent(r.body.result.session_id)}`;
  else status(`Not continued: ${r.body.error || 'Rooms answered with an error'}.`);
}

// ── @ suggestions ───────────────────────────────────────────────────────────
function mentionFragment(input) {
  const before = input.value.slice(0, input.selectionStart);
  const m = before.match(/(^|\s)@([A-Za-z0-9_-]*)$/);
  return m ? { start: before.length - m[2].length - 1, text: m[2].toLowerCase() } : null;
}

function renderMentions() {
  const input = $('[data-rm-input]');
  const list = $('[data-rm-mentions]');
  const frag = mentionFragment(input);
  const people = (meeting?.participants || []).filter((p) => p.id !== 'robert');
  const joined = people.filter((p) => p.teammate && p.rsvp === 'accepted');
  const options = joined.length >= 2 ? [{ id: 'everyone', label: 'everyone (one turn each, in order)' }, ...people] : people;
  const hits = frag ? options.filter((p) => p.id.startsWith(frag.text) || p.label.toLowerCase().startsWith(frag.text)) : [];
  list.hidden = !hits.length;
  list.replaceChildren(...hits.map((p) => h('li', { role: 'option' }, el('button', { type: 'button', 'data-rm-mention': p.id }, `@${handle(p.id)} · ${p.label}`))));
}

function insertMention(id) {
  const input = $('[data-rm-input]');
  const frag = mentionFragment(input);
  if (!frag) return;
  const end = input.selectionStart;
  input.value = `${input.value.slice(0, frag.start)}@${handle(id)} ${input.value.slice(end)}`;
  store.set(draftKey(), input.value);
  $('[data-rm-mentions]').hidden = true;
  input.focus();
}

function heartbeat() {
  if (!current || signedOut || document.hidden || !room || room.state === 'closed') return;
  records(`/v1/rooms/${encodeURIComponent(current)}/presence`, { method: 'PUT', body: { kind: 'here' } });
}

function bindMeeting() {
  $('[data-rm-new]').addEventListener('click', openNew);
  $('[data-rm-new-cancel]').addEventListener('click', () => $('[data-rm-new-dialog]').close());
  $('[data-rm-new-form]').addEventListener('submit', (e) => { e.preventDefault(); createNew(); });
  $('[data-rm-invite]').addEventListener('click', openInvite);
  $('[data-rm-dialog-close]').addEventListener('click', () => $('[data-rm-invite-dialog]').close());
  $('[data-rm-cards]').addEventListener('click', (e) => {
    const p = e.target.closest('[data-rm-prove]');
    if (p) { prove(p); return; }
    const i = e.target.closest('[data-rm-invite-card]');
    if (i) inviteCard(i.dataset.rmInviteCard);
  });
  $('[data-rm-pause]').addEventListener('click', () => openAct(room.state === 'paused' ? 'active' : 'paused'));
  $('[data-rm-end]').addEventListener('click', () => openAct('closed'));
  $('[data-rm-stop]').addEventListener('click', stopAll);
  $('[data-rm-act-cancel]').addEventListener('click', () => { $('[data-rm-act]').hidden = true; pendingAct = null; });
  $('[data-rm-act]').addEventListener('submit', async (e) => {
    e.preventDefault();
    const note = $('[data-rm-act-note]').value.trim();
    if (!note || !pendingAct) return;
    if (await setState(pendingAct, note)) { $('[data-rm-act]').hidden = true; $('[data-rm-act-note]').value = ''; pendingAct = null; }
  });
  $('[data-rm-turns]').addEventListener('click', (e) => { const b = e.target.closest('[data-rm-turn-action]'); if (b) turnAction(b); });
  $('[data-rm-continue]').addEventListener('click', continueConversation);
  $('[data-rm-to]').addEventListener('click', (e) => {
    const b = e.target.closest('[data-rm-to-chip]');
    if (!b) return;
    target = target === b.dataset.rmToChip ? null : b.dataset.rmToChip;
    renderTo();
  });
  const input = $('[data-rm-input]');
  input.addEventListener('input', renderMentions);
  input.addEventListener('keyup', (e) => { if (e.key === 'Escape') $('[data-rm-mentions]').hidden = true; });
  $('[data-rm-mentions]').addEventListener('click', (e) => { const b = e.target.closest('[data-rm-mention]'); if (b) insertMention(b.dataset.rmMention); });
  here = window.setInterval(heartbeat, 30000);
  document.addEventListener('visibilitychange', heartbeat);
}

export function initRooms(p) {
  page = p;
  const raw = document.getElementById('rooms-data');
  if (!raw || !$('[data-rm]')) return;
  cfg = JSON.parse(raw.textContent);
  bind();
  bindMeeting();
  loadAll();
  poll = window.setInterval(() => { if (!document.hidden && current && !signedOut) loadRoom(current); }, 15000);
}

export const _forTests = { stop: () => { window.clearInterval(poll); window.clearInterval(here); window.clearTimeout(fast); } };
