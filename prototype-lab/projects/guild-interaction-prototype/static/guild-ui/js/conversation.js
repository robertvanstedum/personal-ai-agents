// Floating Master Craftsman conversation: modes, drag, context, participants,
// thread rendering and the composer. Markup comes from <template> elements in
// templates/guild/_ui_mc_conversation.html.
import { $, $$, clone, slot, setSlot, el, announce } from './dom.js';
import { world, persist, changed, onChange, context, addMessage, newId, nowLabel } from './world.js';
import { confirm, cancel, edit, receiptLabel, fmtSize, OFF_REASON } from './proposals.js';
import { handle, invite, voiceLine, goOffRecord, goOnRecord, bindUi, scheduleJoins } from './scenario.js';

let panel, thread, pill;
// Follow the newest turn until the reader scrolls up; decided from real scroll
// events, not from a hidden (unrendered) thread, which always reads as 0.
let follow = true;
const nearBottom = () => thread.scrollHeight - thread.scrollTop - thread.clientHeight < 40;
const inPage = () => document.body.dataset.page === 'floor';
const openEdits = new Set();

export function openPanel() {
  if (!inPage() && world.conv.mode === 'pill') setMode('floating');
  const input = $('[data-mc-input]');
  if (input && !window.matchMedia('(max-width: 640px)').matches) input.focus();
}

function setMode(mode) {
  world.conv.mode = mode;
  if (mode !== 'pill') world.conv.unread = 0;
  persist();
  applyMode();
  settleScroll();
}

function applyMode() {
  if (inPage()) {               // docked in the page; the saved floating/pill choice is kept for other pages
    panel.dataset.mode = 'inpage';
    document.body.dataset.mcMode = 'inpage';
    $('[data-mc-unread]').hidden = true;
    return;
  }
  const mode = world.conv.mode;
  panel.dataset.mode = mode;
  document.body.dataset.mcMode = mode;
  pill.setAttribute('aria-expanded', String(mode !== 'pill'));
  $('[data-mc-dock]').setAttribute('aria-pressed', String(mode === 'docked'));
  $('[data-mc-dock]').textContent = mode === 'docked' ? 'Undock' : 'Dock';
  const pos = world.conv.pos || { preset: 'right' };
  if (pos.preset) {
    panel.dataset.pos = pos.preset;
  } else {
    panel.dataset.pos = 'xy';
    panel.style.setProperty('--mc-x', `${pos.x}px`); // documented geometry exception
    panel.style.setProperty('--mc-y', `${pos.y}px`);
  }
  for (const b of $$('[data-mc-preset]')) b.setAttribute('aria-pressed', String(pos.preset === b.dataset.mcPreset));
  const unread = $('[data-mc-unread]');
  unread.hidden = !world.conv.unread;
  unread.textContent = world.conv.unread ? `${world.conv.unread} unread` : '';
}

function initDrag() {
  const head = $('[data-mc-drag]');
  let start = null;
  head.addEventListener('pointerdown', (e) => {
    if (inPage() || world.conv.mode !== 'floating' || e.target.closest('button')) return;
    const r = panel.getBoundingClientRect();
    start = { dx: e.clientX - r.left, dy: e.clientY - r.top };
    head.setPointerCapture(e.pointerId);
  });
  head.addEventListener('pointermove', (e) => {
    if (!start) return;
    const w = panel.offsetWidth, h = panel.offsetHeight;
    const x = Math.max(0, Math.min(window.innerWidth - w, e.clientX - start.dx));
    const y = Math.max(0, Math.min(window.innerHeight - Math.min(h, 120), e.clientY - start.dy));
    world.conv.pos = { x: Math.round(x), y: Math.round(y) };
    applyMode();
  });
  const end = () => { if (start) { start = null; persist(); } };
  head.addEventListener('pointerup', end);
  head.addEventListener('pointercancel', end);
}

function whoLabel(m) {
  if (m.who === 'robert') return `Robert · ${m.via === 'voice' ? 'voice (simulated)' : 'text'}`;
  if (m.who === 'mc') return 'Master Craftsman';
  const p = world.scenario.participants.find((x) => x.id === m.who);
  return p ? p.label : m.who;
}

function attachmentState(att) {
  let s = att.state === 'simulated-filing'
    ? `simulated filing · local receipt ${att.receipt} · file not uploaded`
    : 'shared for discussion · not filed';
  if (!world.liveFiles.has(att.id)) s += ' · file content not kept';
  return s;
}

function renderProposal(li, p) {
  li.dataset.status = p.status;
  li.dataset.proposalId = p.id;
  const sec = slot(li, 'title').closest('[data-proposal]');
  const hid = `ptitle-${p.id}`;
  slot(li, 'title').id = hid;
  sec.setAttribute('aria-labelledby', hid);
  setSlot(li, 'title', p.title);
  const dl = slot(li, 'fields');
  for (const f of p.fields) { dl.append(el('dt', {}, f.label), el('dd', {}, f.value)); }
  setSlot(li, 'note', p.note);
  const state = p.status === 'confirmed'
    ? `Confirmed · ${receiptLabel(world.overlay.receipts.find((r) => r.id === p.receipt) || { id: p.receipt, kind: '', title: p.title })}`
    : p.status === 'cancelled' ? 'Cancelled · no receipt'
      : (p.revised ? 'Revised · awaiting your confirmation' : 'Awaiting your confirmation');
  setSlot(li, 'state', state);
  const form = slot(li, 'editform');
  if (openEdits.has(p.id) && p.status === 'proposed') {
    form.hidden = false;
    const box = slot(li, 'editfields');
    for (const f of p.fields.filter((x) => x.editable)) {
      const id = `pe-${p.id}-${f.key}`;
      const label = el('label', { for: id }, f.label);
      let input;
      if (f.options) {
        input = el('select', { id, name: f.key });
        for (const o of f.options) { const opt = el('option', { value: o }, o); if (o === f.value) opt.selected = true; input.append(opt); }
      } else {
        input = el('input', { id, name: f.key, value: f.value });
      }
      label.append(input);
      box.append(label);
    }
  }
}

function renderMessage(m) {
  let li;
  if (m.kind === 'system') {
    li = clone('tpl-system'); setSlot(li, 'text', m.text);
  } else if (m.kind === 'attachment') {
    const att = world.conv.attachments.find((a) => a.id === m.ref) || m.snapshot;
    if (!att) return null;
    li = clone('tpl-attachment');
    setSlot(li, 'who', 'Robert · attached'); setSlot(li, 'when', m.when);
    setSlot(li, 'chip', `📎 ${att.name} · ${fmtSize(att.size)}${att.sha256 ? ` · sha256:${att.sha256.slice(0, 12)}…` : ''}`);
    setSlot(li, 'state', m.offrecord ? 'refused off the record · not kept' : attachmentState(att));
    li.dataset.attachmentId = att.id;
    li.dataset.state = att.state;
  } else if (m.kind === 'proposal') {
    const p = world.conv.proposals.find((x) => x.id === m.ref);
    if (!p) return null;
    li = clone('tpl-proposal'); renderProposal(li, p);
  } else if (m.kind === 'receipt') {
    const r = world.overlay.receipts.find((x) => x.id === m.ref);
    if (!r) return null;
    li = clone('tpl-receipt');
    setSlot(li, 'id', `Receipt ${r.id}`); setSlot(li, 'text', receiptLabel(r)); setSlot(li, 'when', r.when);
    li.dataset.receipt = r.id;
  } else {
    li = clone('tpl-msg');
    setSlot(li, 'who', whoLabel(m)); setSlot(li, 'when', m.when);
    setSlot(li, 'label', m.simulated ? 'Simulated reply' : '');
    setSlot(li, 'text', m.text);
    setSlot(li, 'prov', m.context ? `from ${m.context.area} · ${m.context.item}` : '');
    if (m.link) {
      slot(li, 'linkwrap').hidden = false;
      const a = slot(li, 'link'); a.href = m.link.href; a.textContent = m.link.text;
    }
    li.dataset.who = m.who;
  }
  if (m.offrecord) li.dataset.offrecord = 'true';
  li.dataset.msgId = m.id;
  return li;
}

function renderThread() {
  const priorTop = thread.scrollTop;
  if (thread.clientHeight > 0 && thread.scrollHeight > thread.clientHeight) follow = nearBottom();
  thread.replaceChildren();
  for (const m of world.conv.messages) { const li = renderMessage(m); if (li) thread.append(li); }
  if (world.off) {
    const marker = clone('tpl-system');
    marker.classList.add('msg-offmarker');
    marker.dataset.offMarker = 'true';
    setSlot(marker, 'text', `Off the record from ${world.off.since} — not recorded; gone on reload`);
    thread.append(marker);
    for (const m of world.off.messages) { const li = renderMessage(m); if (li) thread.append(li); }
  }
  thread.scrollTop = follow ? thread.scrollHeight : priorTop;
}

function settleScroll() {
  if (follow && thread.clientHeight > 0) thread.scrollTop = thread.scrollHeight;
}

function renderParticipants() {
  const ul = $('[data-mc-participants]');
  ul.replaceChildren();
  for (const p of world.scenario.participants) {
    let text, state;
    if (!p.invitable) { text = p.note ? `${p.label} · ${p.note}` : p.label; state = 'member'; }
    else {
      const s = world.conv.participants[p.id] || { state: 'none' };
      if (s.state === 'none') continue;
      state = s.state;
      text = s.state === 'joined' ? `${p.label} · joined ${s.joined_at}` : `${p.label} · invited ${s.invited_at} · not joined`;
    }
    const li = clone('tpl-participant');
    li.dataset.state = state; li.dataset.participant = p.id;
    setSlot(li, 'text', text);
    ul.append(li);
  }
}

function renderContext() {
  const c = context();
  const rec = world.off ? `off the record since ${world.off.since} · say "back on the record"` : 'recording on · say "off the record"';
  $('[data-mc-context]').textContent = `Context: ${c.area} · ${c.item} · ${rec}`;
  const btn = $('[data-mc-record]');
  btn.setAttribute('aria-pressed', String(!!world.off));
  btn.textContent = world.off ? 'Back on the record' : 'Off the record';
}

function renderGuards() {
  const off = !!world.off;
  for (const b of $$('[data-consequential-ui]')) {
    if (b.closest('.msg-proposal') && b.closest('.msg-proposal').dataset.status !== 'proposed') continue;
    b.disabled = off || b.dataset.locked === 'true';
    if (off) b.title = OFF_REASON(); else b.removeAttribute('title');
  }
  const refusal = $('[data-mc-refusal]');
  refusal.hidden = !off;
  if (off) refusal.textContent = OFF_REASON();
}

function renderSuggestions() {
  const ul = $('[data-mc-suggest]');
  if (!ul) return;
  ul.replaceChildren();
  for (const s of world.scenario.suggestions || []) {
    const li = el('li');
    const b = el('button', { type: 'button', class: 'btn-quiet', 'data-suggest': s }, s);
    li.append(b); ul.append(li);
  }
}

export function render() {
  applyMode(); renderContext(); renderParticipants(); renderThread(); renderGuards(); settleScroll();
}

async function digest(buffer) {
  try {
    const h = await window.crypto.subtle.digest('SHA-256', buffer);
    return Array.from(new Uint8Array(h)).map((b) => b.toString(16).padStart(2, '0')).join('');
  } catch (e) { return null; }
}

export async function attach(name, size, buffer, file, origin) {
  if (world.off) { refuse(OFF_REASON()); return null; }
  const att = { id: newId('a'), name, size, sha256: await digest(buffer), origin, state: 'discussion', receipt: null, at: nowLabel() };
  if (world.off) { refuse(OFF_REASON()); return null; } // went off record while hashing
  world.conv.attachments.push(att);          // metadata only; never bytes
  if (file) world.liveFiles.set(att.id, file);
  else world.liveFiles.set(att.id, true);
  addMessage({ kind: 'attachment', who: 'robert', ref: att.id });
  persist(); changed();
  announce(`${name} shared for discussion, not filed`);
  return att;
}

function refuse(text) {
  const r = $('[data-mc-refusal]');
  r.hidden = false; r.textContent = text;
  announce(text);
}

export function initConversation() {
  panel = $('[data-mc-panel]'); thread = $('[data-mc-thread]'); pill = $('[data-mc-pill]');
  world.inPage = inPage();
  bindUi({ openEdit: (id) => { openEdits.add(id); changed(); }, refuse });
  pill.addEventListener('click', () => { setMode('floating'); render(); openPanel(); });
  thread.addEventListener('scroll', () => { if (thread.clientHeight > 0) follow = nearBottom(); });
  $('[data-mc-min]').addEventListener('click', () => { setMode('pill'); pill.focus(); });
  $('[data-mc-dock]').addEventListener('click', () => setMode(world.conv.mode === 'docked' ? 'floating' : 'docked'));
  for (const b of $$('[data-mc-preset]')) {
    b.addEventListener('click', () => { world.conv.pos = { preset: b.dataset.mcPreset }; if (world.conv.mode !== 'floating') world.conv.mode = 'floating'; persist(); applyMode(); });
  }
  panel.addEventListener('keydown', (e) => {
    if (!inPage() && e.key === 'Escape' && world.conv.mode === 'floating') { setMode('pill'); pill.focus(); }
  });
  initDrag();

  const form = $('[data-mc-composer]');
  const input = $('[data-mc-input]');
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = input.value;
    input.value = '';
    handle(text, 'text');
  });
  const fileInput = $('[data-mc-file]');
  $('[data-mc-attach]').addEventListener('click', () => { if (world.off) { refuse(OFF_REASON()); return; } fileInput.click(); });
  fileInput.addEventListener('change', async () => {
    const f = fileInput.files && fileInput.files[0];
    if (f) await attach(f.name, f.size, await f.arrayBuffer(), f, 'picked');
    fileInput.value = '';
  });
  for (const b of $$('[data-mc-sample-attach]')) {
    b.addEventListener('click', async () => {
      const s = world.scenario.sample_attachments[Number(b.dataset.mcSampleAttach)];
      const bytes = new TextEncoder().encode(s.content);
      await attach(s.name, bytes.byteLength, bytes, null, 'sample');
    });
  }
  $('[data-mc-invite]').addEventListener('click', () => {
    if (world.off) { refuse(OFF_REASON()); return; }
    handle('Bring Claude and Codex in.', 'text');
  });
  $('[data-mc-record]').addEventListener('click', () => { if (world.off) goOnRecord(); else goOffRecord(); });
  for (const v of $$('[data-mc-voice]')) {
    v.addEventListener('click', () => {
      handle(voiceLine(), 'voice');
      if (window.matchMedia('(max-width: 640px)').matches) panel.scrollIntoView({ block: 'start' });
    });
  }
  const typeBtn = $('[data-mc-type]');
  if (typeBtn) {
    typeBtn.addEventListener('click', () => {
      const on = document.body.dataset.typing !== 'true';
      document.body.dataset.typing = String(on);
      typeBtn.setAttribute('aria-expanded', String(on));
      if (on) input.focus();
    });
  }
  const suggest = $('[data-mc-suggest]');
  if (suggest) suggest.addEventListener('click', (e) => {
    const b = e.target.closest('[data-suggest]');
    if (b) handle(b.dataset.suggest, 'text');
  });
  thread.addEventListener('click', (e) => {
    const b = e.target.closest('[data-proposal-act]');
    if (!b) return;
    const li = b.closest('[data-proposal-id]');
    const id = li.dataset.proposalId;
    const act = b.dataset.proposalAct;
    let res = {};
    if (act === 'confirm') res = confirm(id);
    else if (act === 'cancel') res = cancel(id);
    else if (act === 'edit') { if (world.off) res = { refused: OFF_REASON() }; else { openEdits.add(id); render(); focusEdit(id); } }
    else if (act === 'edit-cancel') { openEdits.delete(id); render(); }
    if (res.refused) refuse(res.refused);
    if (act === 'confirm' && res.receipt) focusReceipt(res.receipt.id);
  });
  thread.addEventListener('submit', (e) => {
    const form2 = e.target.closest('[data-slot="editform"]');
    if (!form2) return;
    e.preventDefault();
    const li = form2.closest('[data-proposal-id]');
    const values = Object.fromEntries(new FormData(form2).entries());
    const res = edit(li.dataset.proposalId, values);
    if (res.refused) refuse(res.refused);
    openEdits.delete(li.dataset.proposalId);
    render();
    const again = thread.querySelector(`[data-proposal-id="${li.dataset.proposalId}"] [data-proposal-act="confirm"]`);
    if (again) again.focus();
  });
  document.addEventListener('guild:context', renderContext);
  onChange(render);
  renderSuggestions();
  scheduleJoins();
  render();
}

function focusEdit(id) {
  const first = thread.querySelector(`[data-proposal-id="${id}"] [data-slot="editform"] input, [data-proposal-id="${id}"] [data-slot="editform"] select`);
  if (first) first.focus();
}
function focusReceipt() {
  const input = $('[data-mc-input]');
  if (input && !window.matchMedia('(max-width: 640px)').matches) input.focus();
}
