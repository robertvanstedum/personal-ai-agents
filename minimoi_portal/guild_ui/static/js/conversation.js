// The conversation, real mode. The thread shows the opening briefing,
// platform lines (explain cards, Save results) and Robert's notes. On the
// record, Send keeps the message on the server as a note; then, only when the
// server says Master Craftsman takes turns (data-mc-turns), the kept note is
// sent to it server side (POST /mc/turns) and its answer, or an honest
// "did not answer", is shown. Off the record, what is typed stays in this
// page's memory only: it is never sent, never kept, never reaches MC.
import { $, $$, clone, slot, setSlot, el, announce, localTime } from './dom.js';
import { live, setOff, onChange } from './state.js';
import { apiGet, apiPost, recordMode } from './api.js';
import { newKey } from './actions.js';

let page, panel, thread, pill;
const inPage = () => document.body.dataset.page === 'floor';
let mode = 'pill';

function applyMode() {
  if (inPage()) {
    panel.dataset.mode = 'inpage';
    document.body.dataset.mcMode = 'inpage';
    return;
  }
  panel.dataset.mode = mode;
  document.body.dataset.mcMode = mode;
  pill.setAttribute('aria-expanded', String(mode !== 'pill'));
  const dock = $('[data-mc-dock]');
  dock.setAttribute('aria-pressed', String(mode === 'docked'));
  dock.textContent = mode === 'docked' ? 'Undock' : 'Dock';
}

function setMode(next) { mode = next; applyMode(); }

function follow() { thread.scrollTop = thread.scrollHeight; }

function platformLine(label, text) {
  const li = clone('tpl-platform');
  setSlot(li, 'label', label);
  setSlot(li, 'when', localTime(new Date().toISOString()));
  setSlot(li, 'text', text);
  return li;
}

export function addPlatform(label, text) {
  const li = platformLine(label, text);
  thread.append(li);
  follow();
  announce(text);
  return li;
}

export function explain(light) {
  const li = clone('tpl-explain');
  setSlot(li, 'label', page.rules_label);
  setSlot(li, 'title', light.label);
  setSlot(li, 'word', light.word);
  setSlot(li, 'reason', light.reason);
  setSlot(li, 'source', light.source_mark);
  setSlot(li, 'observed', light.observed_at ? localTime(light.observed_at) : 'not observed');
  setSlot(li, 'evidence', light.evidence || '—');
  const ul = slot(li, 'detail');
  for (const line of light.detail || []) ul.append(el('li', {}, line));
  li.dataset.explain = light.id;
  thread.append(li);
  follow();
  announce(`${light.label}: ${light.word}, ${light.reason}`);
  if (!inPage() && mode === 'pill') setMode('floating');
}

export function setBriefing(briefing) {
  if (!briefing) return;
  const text = $('[data-briefing-text]');
  // The server writes the time in UTC; show it in local time like every other time here.
  if (text) text.textContent = briefing.text.replace(/^As of [^·]+·/, `As of ${localTime(briefing.observed_at)} ·`);
}

function noteLine(note) {
  const li = clone('tpl-note');
  li.dataset.note = note.id;
  setSlot(li, 'label', note.author_label);
  setSlot(li, 'when', localTime(note.created_at));
  const ctx = note.context || {};
  setSlot(li, 'context', `${ctx.area ? ` · ${ctx.area}` : ''}${ctx.item_ref ? ` · #${ctx.item_ref}` : ''}`);
  setSlot(li, 'text', note.text);
  if (note.turn && note.turn.done_text) {      // a live MC reply: "Done in 1.2s · 96 output tokens"
    const foot = clone('tpl-note-foot');
    foot.querySelector('[data-turn-done]').textContent = note.turn.done_text;
    setTokens(foot, note.turn);
    li.append(foot);
    if (note.turn.tokens_text == null) askForTokens();
  }
  return li;
}

// Tokens come from the gateway's usage record (usage-record U3), which lands
// a moment after the reply: until then the slot stays empty, and the page asks
// the notes list again, a few times, then leaves it (the server says "tokens
// unknown" once the turn is old enough).
function setTokens(scope, turn) {
  const span = scope.querySelector('[data-turn-usage]');
  if (span) span.textContent = turn && turn.tokens_text ? ` · ${turn.tokens_text}` : '';
}

let tokenAsks = 0;
let tokenTimer = null;
function askForTokens() {
  if (tokenTimer || tokenAsks >= 4) return;
  tokenTimer = window.setTimeout(async () => {
    tokenTimer = null;
    tokenAsks += 1;
    const r = await apiGet('/notes?limit=20');
    let pending = false;
    for (const n of ((r.ok && r.body && r.body.notes) || [])) {
      if (!n.turn) continue;
      const row = $(`[data-note="${n.id}"]`);
      if (row) setTokens(row, n.turn);
      if (n.turn.tokens_text == null) pending = true;
    }
    if (pending) askForTokens(); else tokenAsks = 0;
  }, 4000);
}

function appendNote(note) {
  thread.append(noteLine(note));
  follow();
}

// While Master Craftsman works, a quiet line sits in the thread exactly where
// its reply will appear (Robert, 2026-09-29, after his own OpenClaw chat): MC's
// mark, "Waiting for a response…" and the seconds so far. It is not a platform
// message and never persists: the reply, or the honest failure line, replaces
// it in place. The thread (role=log, polite) announces its arrival and the
// result; the counter is aria-hidden, so seconds are never read out.
function waitingLine() {
  const li = clone('tpl-mc-waiting');
  const elapsed = li.querySelector('[data-slot="elapsed"]');
  const started = Date.now();
  const tick = () => { elapsed.textContent = `${Math.floor((Date.now() - started) / 1000)}s`; };
  li.stopTicking = () => window.clearInterval(li.ticker);
  li.ticker = window.setInterval(tick, 1000);
  thread.append(li);
  follow();
  return li;
}

async function askMasterCraftsman(note) {
  if (live.off || document.body.dataset.mcTurns !== 'true') return;
  const waiting = waitingLine();
  let r;
  try {
    r = await apiPost('/mc/turns', { note_request_id: note.request_id, record_mode: recordMode() });
  } finally {
    waiting.stopTicking();
  }
  const body = r.body || {};
  if (body.mc_header) for (const n of $$('[data-mc-header]')) n.textContent = body.mc_header;
  if (body.mc_state) document.body.dataset.mcState = body.mc_state;
  if (r.ok && body.status === 'answered' && body.reply_note) {
    if ($(`[data-note="${body.reply_note.id}"]`)) waiting.remove();
    else waiting.replaceWith(noteLine(body.reply_note));
    announce(body.message || 'Master Craftsman answered');
  } else {
    const text = body.message || 'Master Craftsman did not answer. Your note is kept.';
    waiting.replaceWith(platformLine('Guild platform', text));
    announce(text);
  }
  follow();
}

function appendOffRecord(text) {
  const li = clone('tpl-off-record');
  setSlot(li, 'when', localTime(new Date().toISOString()));
  setSlot(li, 'text', text);
  thread.append(li);
  follow();
}

let noteKey = null;   // one key per composed note; a new one when the text changes

async function sendNote(input, send) {
  const text = input.value.trim();
  if (!text) return;
  if (live.off) {       // memory only: never sent, never kept
    appendOffRecord(text);
    input.value = '';
    return;
  }
  if (!noteKey) noteKey = newKey();
  send.disabled = true;
  const r = await apiPost('/notes', {
    request_id: noteKey, text, record_mode: recordMode(),
    context: { area: document.body.dataset.area || null, item_ref: page.item_id || null, page: page.page },
  });
  send.disabled = false;
  const body = r.body || {};
  if (r.ok && body.result === 'kept') {
    if (!$(`[data-note="${body.note.id}"]`)) appendNote(body.note);
    input.value = '';
    noteKey = null;
    for (const n of $$('[data-notes-unavailable]')) n.remove();
    const r2 = $('[data-mc-refusal]');
    if (!live.off) { r2.hidden = true; r2.textContent = ''; }
    announce(body.message);
    if (!body.repeated) askMasterCraftsman(body.note);
  } else {
    refuse(body.message || 'Not saved — notes unavailable');
  }
}

export function refuse(text) {
  const r = $('[data-mc-refusal]');
  r.hidden = false;
  r.textContent = text;
  announce(text);
}

function renderRecord() {
  const btn = $('[data-mc-record]');
  btn.setAttribute('aria-pressed', String(live.off));
  btn.textContent = live.off ? 'Back on the record' : 'Off the record';
  const confirm = $('[data-mc-record-confirm]');
  if (confirm) confirm.hidden = live.known;
  const ctx = $('[data-mc-context]');
  const area = document.body.dataset.area || 'Guild';
  const item = document.body.dataset.contextItem || '';
  const mode = !live.known ? 'record mode unknown: nothing is sent until you choose'
    : live.off ? `off the record since ${localTime(live.offSince)}` : 'on the record';
  ctx.textContent = `Context: ${area}${item ? ` · ${item}` : ''} · ${mode}`;
  const r = $('[data-mc-refusal]');
  if (live.off) { r.hidden = false; r.textContent = page.off_record_text; } else { r.hidden = true; r.textContent = ''; }
  document.body.dataset.offRecord = String(live.off);
  document.body.dataset.recordKnown = String(live.known);
}

export function initConversation(p) {
  page = p;
  panel = $('[data-mc-panel]'); thread = $('[data-mc-thread]'); pill = $('[data-mc-pill]');
  pill.addEventListener('click', () => { setMode('floating'); const i = $('[data-mc-input]'); if (i) i.focus(); });
  $('[data-mc-min]').addEventListener('click', () => { setMode('pill'); pill.focus(); });
  $('[data-mc-dock]').addEventListener('click', () => setMode(mode === 'docked' ? 'floating' : 'docked'));
  panel.addEventListener('keydown', (e) => {
    if (!inPage() && e.key === 'Escape' && mode === 'floating') { setMode('pill'); pill.focus(); }
  });
  const form = $('[data-mc-composer]');
  const input = $('[data-mc-input]');
  const send = $('[data-mc-send]');
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    sendNote(input, send);
  });
  input.addEventListener('input', () => { noteKey = null; });
  const confirm = $('[data-mc-record-confirm]');
  if (confirm) {
    confirm.addEventListener('click', () => {
      setOff(false);
      addPlatform('Guild platform', 'On the record in this tab');
    });
  }
  $('[data-mc-record]').addEventListener('click', () => {
    const wasOff = live.off;
    setOff(!live.off);
    if (wasOff) {
      for (const n of $$('[data-off-record-line]')) n.remove();
      addPlatform('Guild platform', 'Back on the record · nothing from the off-the-record stretch was kept');
    }
  });
  const typeBtn = $('[data-mc-type]');
  typeBtn.addEventListener('click', () => {
    document.body.dataset.typing = 'true';
    input.scrollIntoView({ block: 'nearest' });
    input.focus();
  });
  onChange(renderRecord);
  if (!live.known) {
    addPlatform('Guild platform', 'This tab does not know whether you are on the record (it may have been opened from a tab that is off the record, or the setting could not be read). Nothing is sent from it, and Continue is not updated, until you choose: Confirm on the record, or go Off the record.');
  }
  setBriefing(page.floor && page.floor.briefing);
  applyMode();
  renderRecord();
  follow();
}


