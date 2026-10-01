// The conversation, real mode. The thread shows the opening briefing,
// platform lines (explain cards, Save results) and Robert's notes. On the
// record, Send keeps the message on the server as a note; then, only when the
// server says Master Craftsman takes turns (data-mc-turns), the kept note is
// sent to it server side (POST /mc/turns) and its answer, or an honest
// "did not answer", is shown. Off the record, what is typed stays in this
// page's memory only: it is never sent, never kept, never reaches MC.
import { $, $$, clone, slot, setSlot, el, announce, localTime } from './dom.js';
import { live, setOff, onChange } from './state.js';
import { apiGet, apiPost, apiStream, apiStop, recordMode } from './api.js';
import { newKey } from './actions.js';
import { answerFailed, answerArrived } from './floorlayout.js';
import { applyConversation } from './conversations.js';

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
  li.dataset.authorKind = note.author_kind || '';
  const body = slot(li, 'text');
  // The server renders Markdown and sanitises it with an allow-list
  // (markdown_render.py: no raw HTML, scripts, handlers, javascript: URLs or
  // images). Without it, the note's own text is shown as plain text.
  if (typeof note.html === 'string' && note.html) body.innerHTML = note.html;
  else body.textContent = note.text;
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
    const conv = page.conversation ? `&conversation=${encodeURIComponent(page.conversation.id)}` : '';
    const r = await apiGet(`/notes?limit=20${conv}`);
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

function applyHeader(body) {
  if (body.mc_header) for (const n of $$('[data-mc-header]')) n.textContent = body.mc_header;
  if (body.mc_state) document.body.dataset.mcState = body.mc_state;
}

// Shows a finished (non-streamed, or repeated) answer in place of `holder`.
function showAnswer(holder, r) {
  const body = r.body || {};
  applyHeader(body);
  if (r.ok && body.status === 'answered' && body.reply_note) {
    if ($(`[data-note="${body.reply_note.id}"]`)) holder.remove();
    else holder.replaceWith(noteLine(body.reply_note));
    announce(body.message || 'Master Craftsman answered');
    answerArrived();
  } else {
    const text = body.message || 'Master Craftsman did not answer. Your note is kept.';
    holder.replaceWith(platformLine('Guild platform', text));
    announce(text);
    answerFailed(body.mc_header || 'Master Craftsman did not answer');
  }
  follow();
}

// One turn per kept note: streamed when the server says so (data-mc-stream,
// computed from the switch and the backend), else the non-streaming path.
// One request id per turn, so the server can refuse any second dispatch.
function askMasterCraftsman(note) {
  if (live.off || document.body.dataset.mcTurns !== 'true') return undefined;
  const requestId = newKey();
  return document.body.dataset.mcStream === 'true' ? streamMasterCraftsman(note, requestId)
    : turnMasterCraftsman(note, requestId);
}

async function turnMasterCraftsman(note, requestId, holder) {
  const waiting = holder || waitingLine();
  let r;
  try {
    r = await apiPost('/mc/turns', { note_request_id: note.request_id, record_mode: recordMode(), request_id: requestId,
      conversation_id: page.conversation ? page.conversation.id : undefined });
  } finally {
    if (waiting.stopTicking) waiting.stopTicking();
  }
  showAnswer(waiting, r);
}

// ── Streaming (streaming spec v0.2 §3 and §6, v0.3 §2 and §5) ──
// Deltas are text and only ever set with textContent. The only HTML inserted
// is the server's sanitised rendering (render and done events). Closing the
// tab is not Stop: the server finishes and keeps a real answer. Stop is the
// explicit control, and says plainly what it cannot save.
const STREAM_LINE_CAP = 2 * 1024 * 1024;

function streamLine() {
  const li = clone('tpl-mc-stream');
  setSlot(li, 'label', document.body.dataset.mcState === 'stub' ? 'Master Craftsman stub · scripted' : 'Master Craftsman');
  const elapsed = slot(li, 'elapsed');
  const started = Date.now();
  const tick = () => { elapsed.textContent = `${Math.floor((Date.now() - started) / 1000)}s`; };
  li.ticker = window.setInterval(tick, 1000);
  li.stopTicking = () => window.clearInterval(li.ticker);
  thread.append(li);
  follow();
  return li;
}

function endLine(li, state) {
  li.stopTicking();
  li.dataset.ended = 'true';
  li.removeAttribute('aria-busy');
  setSlot(li, 'state', state);
  slot(li, 'elapsed').textContent = '';
  const controls = slot(li, 'controls');
  if (controls) controls.remove();
}

async function streamMasterCraftsman(note, requestId) {
  const li = streamLine();
  const controller = new AbortController();
  const onHide = () => controller.abort();          // harmless: the server finishes and keeps the answer
  window.addEventListener('pagehide', onHide);
  const body = { note_request_id: note.request_id, record_mode: recordMode(), request_id: requestId,
    conversation_id: page.conversation ? page.conversation.id : undefined };
  let res;
  try {
    res = await apiStream('/mc/turns/stream', body, controller.signal);
  } catch (e) {
    window.removeEventListener('pagehide', onHide);
    endLine(li, 'unknown');
    const text = 'Unknown · it may have run; nothing was retried. Your note is kept.';
    thread.append(platformLine('Guild platform', text));
    announce(text);
    answerFailed('Master Craftsman: unknown');
    follow();
    return;
  }
  if (res.refused) {
    window.removeEventListener('pagehide', onHide);
    li.remove();
    return;
  }
  const type = res.headers.get('Content-Type') || '';
  if (!type.includes('application/x-ndjson')) {
    window.removeEventListener('pagehide', onHide);
    let data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    // Only an explicit, server-declared pre-dispatch refusal falls back to the
    // non-streaming path; nothing was sent, so this is not a second dispatch.
    if (res.status === 404 || res.status === 405 || ['stream_off', 'stream_unsupported'].includes(data.error)) {
      li.stopTicking();
      li.remove();
      await turnMasterCraftsman(note, requestId);
      return;
    }
    li.stopTicking();
    showAnswer(li, { ok: res.ok, status: res.status, body: data });
    return;
  }
  const rendered = slot(li, 'rendered');
  const tail = slot(li, 'tail');
  const stop = li.querySelector('[data-mc-stop]');
  const stopNote = slot(li, 'stopnote');
  const deltas = [];
  let upto = 0;
  let acked = false;
  let ended = false;
  const showTail = () => { tail.textContent = deltas.slice(upto).join(''); };
  stop.addEventListener('click', async () => {
    if (!li.turnId) return;
    stop.disabled = true;
    setSlot(li, 'state', 'Stopping…');
    await apiStop(`/mc/turns/${encodeURIComponent(li.turnId)}/stop`);
  });
  const handle = (ev) => {
    if (ev.t === 'ack' && typeof ev.turn_id === 'string') {
      acked = true;
      li.turnId = ev.turn_id;
      stop.hidden = false;
      stopNote.hidden = false;
      announce('Master Craftsman is answering');
    } else if (ev.t === 'delta' && typeof ev.text === 'string') {
      if (!deltas.length) setSlot(li, 'state', 'Writing…');
      deltas.push(ev.text);
      showTail();
      follow();
    } else if (ev.t === 'render' && typeof ev.html === 'string' && Number.isInteger(ev.deltas)) {
      rendered.innerHTML = ev.html;           // sanitised on the server (markdown_render.py)
      upto = Math.min(ev.deltas, deltas.length);
      showTail();
    } else if (ev.t === 'done') {
      ended = true;
      li.stopTicking();
      applyHeader(ev);
      if (ev.reply_note && !$(`[data-note="${ev.reply_note.id}"]`)) li.replaceWith(noteLine(ev.reply_note));
      else li.remove();
      if (live.off) addPlatform('Guild platform', 'This answer was asked for on the record, so it is kept.');
      announce(ev.message || 'Master Craftsman answered');
      answerArrived();
      follow();
    } else if (ev.t === 'error') {
      ended = true;
      applyHeader(ev);
      endLine(li, ev.failure_class === 'stopped' ? 'Stopped' : '(interrupted)');
      if (!deltas.length) li.remove();          // partial text, if any, stays shown once and is never kept
      const text = ev.message || 'Master Craftsman did not answer. Your note is kept.';
      thread.append(platformLine('Guild platform', text));
      announce(text);
      answerFailed(ev.mc_header || 'Master Craftsman did not answer');
      follow();
    }
  };
  try {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const raw = buf.slice(0, nl);
        buf = buf.slice(nl + 1);
        let ev = null;
        try { ev = JSON.parse(raw); } catch (e) { ev = null; }
        if (ev && typeof ev === 'object') handle(ev);
      }
      if (buf.length > STREAM_LINE_CAP) { controller.abort(); break; }
    }
  } catch (e) {
    // the connection went (or the page is leaving); said below
  } finally {
    window.removeEventListener('pagehide', onHide);
  }
  if (!ended) {
    endLine(li, acked ? '(interrupted)' : 'unknown');
    if (!deltas.length) li.remove();
    const text = acked
      ? 'The connection ended before the answer finished · if Master Craftsman finishes, its answer is kept: reload to see it.'
      : 'Unknown · it may have run; nothing was retried. Your note is kept.';
    thread.append(platformLine('Guild platform', text));
    announce(text);
    answerFailed('Master Craftsman: connection ended');
    follow();
  }
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
    request_id: noteKey, text, record_mode: recordMode(), conversation_id: page.conversation ? page.conversation.id : undefined,
    context: { area: document.body.dataset.area || null, item_ref: page.item_id || null, page: page.page },
  });
  send.disabled = false;
  const body = r.body || {};
  if (r.ok && body.result === 'kept') {
    if (!$(`[data-note="${body.note.id}"]`)) appendNote(body.note);
    applyConversation(body.conversation);
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
  // The Chat header's record chip (Guild 1.1 slice 1): display only.
  const chip = $('[data-record-chip]');
  if (chip) {
    chip.dataset.mode = !live.known ? 'unknown' : live.off ? 'off' : 'on';
    chip.textContent = !live.known ? 'Record mode unknown' : live.off ? 'Off the record · not kept' : 'On the record';
  }
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
  // The three explanatory lines under the composer fold behind ⓘ.
  const info = $('[data-mc-info]');
  const lines = $('[data-mc-off-lines]');
  if (info && lines) {
    info.addEventListener('click', () => {
      lines.hidden = !lines.hidden;
      info.setAttribute('aria-expanded', String(!lines.hidden));
    });
  }
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


