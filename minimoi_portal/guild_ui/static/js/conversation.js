// The conversation, real mode. The thread shows the opening briefing,
// platform lines (explain cards, Save results) and Robert's notes. On the
// record, Send keeps the message on the server as a note; then, only when the
// server says Master Craftsman takes turns (data-mc-turns), the kept note is
// sent to it server side (POST /mc/turns) and its answer, or an honest
// "did not answer", is shown. Off the record, what is typed stays in this
// page's memory only: it is never sent, never kept, never reaches MC.
import { $, $$, clone, slot, setSlot, el, announce, localTime } from './dom.js';
import { live, setOff, onChange } from './state.js';
import { apiGet, apiPost, apiPrivate, apiStream, apiStop, recordMode } from './api.js';
import { newKey } from './actions.js';
import { answerFailed, answerArrived } from './floorlayout.js';

// The conversation partner's name: the server says who answers on this portal (Master Craftsman, or the Chief of Staff).
const mcName = () => document.body.dataset.mcName || 'Master Craftsman';
const mcSay = (text) => String(text).split('Master Craftsman').join(mcName());
import { applyConversation } from './conversations.js';
import { takeTray, restoreTray, trayBusy } from './attachments.js';
import { initJobs } from './jobs.js';

let page, panel, thread, pill;
const inPage = () => document.body.dataset.page === 'floor';
let mode = 'pill';

// The Workshop page chooses topic items for ONE message ("Ask Master Craftsman about this"): ids only, owner-checked on the server,
// sent with that message and then forgotten here. Nothing is added unless the page put it in window.guildTopicContext.
let turnCtx = [];            // the items chosen for the turn now going out
function topicContext() {
  return turnCtx.length ? { topic_context: turnCtx.map((c) => ({ topic_id: c.topic_id, item_id: c.item_id, rev: c.rev || undefined })) } : {};
}
function takeTopicContext() {      // the chosen items go with THIS message and are forgotten by the page
  const ctx = Array.isArray(window.guildTopicContext) ? window.guildTopicContext : [];
  turnCtx = ctx.slice();
  if (ctx.length) { window.guildTopicContext = []; document.dispatchEvent(new CustomEvent('guild:topic-context-sent')); }
}
function restoreTopicContext() {   // the server refused before sending anything: give the choice back
  if (turnCtx.length) { window.guildTopicContext = turnCtx.slice(); turnCtx = []; document.dispatchEvent(new CustomEvent('guild:topic-context-restored')); }
}

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
  dock.textContent = mode === 'docked' ? 'Float panel' : 'Side panel';
}

function fitPanel() {
  if (!panel || inPage() || mode !== 'docked') return;
  const top = Math.max(0, panel.getBoundingClientRect().top);
  panel.style.setProperty('--mc-panel-height', `${Math.max(180, (window.visualViewport?.height || window.innerHeight) - top)}px`);
}
function setMode(next) { mode = next; applyMode(); requestAnimationFrame(fitPanel); }
// The Workshop page's Chat | Cards | Split control drives the same panel: docked (Split), pill (Cards), full (Chat).
export function setPanelMode(next) { if (['docked', 'pill', 'full', 'floating'].includes(next)) setMode(next); }
export const panelMode = () => mode;

function follow() {
  thread.scrollTop = thread.scrollHeight;
  // Composer/header updates can shrink the thread later in the same frame.
  requestAnimationFrame(() => { thread.scrollTop = thread.scrollHeight; });
}

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
    foot.hidden = !note.turn.done_text.includes('not saved');
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
// Time only, no description of what for (Robert, 7 Oct: "just so I ground my perception in reality").
const clock = (s) => (s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`);

function waitingLine() {
  const li = clone('tpl-mc-waiting');
  const elapsed = li.querySelector('[data-slot="elapsed"]');
  const started = Date.now();
  const tick = () => { elapsed.textContent = clock(Math.floor((Date.now() - started) / 1000)); };
  li.stopTicking = () => window.clearInterval(li.ticker);
  li.ticker = window.setInterval(tick, 1000);
  thread.append(li);
  follow();
  return li;
}

function applyHeader(body) {
  if (body.mc_header) for (const n of $$('[data-mc-header]')) { n.dataset.publicHeader = body.mc_header; n.textContent = live.off ? 'Private · not kept by MiniMoi' : body.mc_header; }
  if (body.mc_state) document.body.dataset.mcState = body.mc_state;
}

// Shows a finished (non-streamed, or repeated) answer in place of `holder`.
function showAnswer(holder, r) {
  const body = r.body || {};
  applyHeader(body);
  if (r.ok && body.status === 'answered' && body.reply_note) {
    if ($(`[data-note="${body.reply_note.id}"]`)) holder.remove();
    else holder.replaceWith(noteLine(body.reply_note));
    announce(body.message || mcSay('Master Craftsman answered'));
    answerArrived();
  } else {
    if (NOT_DISPATCHED.includes(body.error)) { if (turnFiles.length) restoreTray(turnFiles); restoreTopicContext(); }
    const text = body.message || mcSay('Master Craftsman did not answer. Your message is kept.');
    holder.replaceWith(failureLine(text));
    announce(text);
    answerFailed(body.mc_header || mcSay('Master Craftsman did not answer'));
  }
  follow();
}

// Which files went into the request for a note, exactly as the server reports them (it computes this from the text it
// really put in the request). "Sent" is the honest word: it says what the request carried, not that the model read it.
// A file that went in full is only a chip with its name; a cut or a file that did not go says so.
const FILE_WORDS = { read: () => '', partly_read: (f) => `sent in part (${f.reason})`, not_read: (f) => `not sent (${f.reason})` };
function showFiles(noteId, files) {
  if (!Array.isArray(files) || !files.length) return;
  const note = thread.querySelector(`[data-note="${noteId}"]`);
  if (!note) return;
  for (const old of note.querySelectorAll('[data-note-files]')) old.remove();
  const ul = el('ul', { class: 'msg-files small', 'data-note-files': '', 'aria-label': mcSay('Files in the request to Master Craftsman') });
  for (const f of files) {
    const li = el('li', { class: 'msg-file', 'data-status': f.status });
    li.append(el('span', { class: 'msg-file-icon', 'aria-hidden': 'true' }, '📄 '), el('span', { class: 'msg-file-name' }, f.name));
    const words = (FILE_WORDS[f.status] || (() => 'not sent'))(f);
    if (words) li.append(document.createTextNode(` · ${words}`));
    ul.append(li);
  }
  note.append(ul);
  follow();
}

// One failure line at a time: a fresh attempt replaces the last attempt's notice, so repeated tries read as one clean
// state and never as a stack of identical lines. The kept message stays in the thread; only the notice is replaced.
function failureLine(text) {
  clearFailureLines();
  const li = platformLine('Guild platform', text);
  li.dataset.turnFailure = 'true';
  return li;
}
function clearFailureLines() { for (const n of thread.querySelectorAll('[data-turn-failure]')) n.remove(); }

// An answer that says nothing was dispatched (the server refused before it sent anything to Master Craftsman) gives the
// files back to the tray, so nothing is lost. A network failure or an unclear end is NOT in this list: those may have
// been dispatched, and nothing is replayed automatically.
const NOT_DISPATCHED = ['not_found', 'invalid', 'too_many', 'too_large', 'busy', 'mc_unavailable', 'mc_turns_off', 'unavailable',
  'jobs_off', 'queue_full', 'not_connected', 'not_a_conversation', 'no_original', 'too_big', 'quota', 'bad_ids', 'exists'];
let turnFiles = [];
let jobsUi = { accepted() {}, refresh() {} };

// One turn per kept note: streamed when the server says so (data-mc-stream,
// computed from the switch and the backend), else the non-streaming path.
// One request id per turn, so the server can refuse any second dispatch.
function askMasterCraftsman(note, files) {
  turnFiles = files || [];
  clearFailureLines();
  if (live.off || document.body.dataset.mcTurns !== 'true') { restoreTray(turnFiles); return undefined; }   // no turn: nothing was sent, the files stay in the tray
  takeTopicContext();
  const requestId = newKey();
  return document.body.dataset.mcStream === 'true' ? streamMasterCraftsman(note, requestId)
    : turnMasterCraftsman(note, requestId);
}

const fileIds = () => turnFiles.map((f) => f.id);

async function turnMasterCraftsman(note, requestId, holder) {
  const waiting = holder || waitingLine();
  let r;
  try {
    r = await apiPost('/mc/turns', { note_request_id: note.request_id, record_mode: recordMode(), request_id: requestId,
      chat_scope: page.chat_scope, conversation_id: page.conversation ? page.conversation.id : undefined,
      ...(turnFiles.length ? { attachment_ids: fileIds() } : {}), ...topicContext() });
  } finally {
    if (waiting.stopTicking) waiting.stopTicking();
  }
  if (r && r.body && r.body.job_command) { waiting.remove(); jobsUi.accepted(r.body); return; }     // a spoken job command: the server answered it
  if (r && r.body && r.body.files) showFiles(note.id, r.body.files);
  showAnswer(waiting, r);
  if (r && r.body && r.body.job) jobsUi.accepted({ job: r.body.job });
}

// ── Streaming (streaming spec v0.2 §3 and §6, v0.3 §2 and §5) ──
// Deltas are text and only ever set with textContent. The only HTML inserted
// is the server's sanitised rendering (render and done events). Closing the
// tab is not Stop: the server finishes and keeps a real answer. Stop is the
// explicit control, and says plainly what it cannot save.
const STREAM_LINE_CAP = 2 * 1024 * 1024;

function streamLine() {
  const li = clone('tpl-mc-stream');
  setSlot(li, 'label', document.body.dataset.mcState === 'stub' ? mcSay('Master Craftsman stub · scripted') : mcSay('Master Craftsman'));
  const elapsed = slot(li, 'elapsed');
  const started = Date.now();
  li.startedAt = started;
  const tick = () => { elapsed.textContent = clock(Math.floor((Date.now() - started) / 1000)); };
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
  slot(li, 'elapsed').textContent = clock(Math.floor((Date.now() - (li.startedAt || Date.now())) / 1000));   // the time it took stays on the message
  const controls = slot(li, 'controls');
  if (controls) controls.remove();
}

async function streamMasterCraftsman(note, requestId) {
  const li = streamLine();
  const controller = new AbortController();
  const onHide = () => controller.abort();          // harmless: the server finishes and keeps the answer
  window.addEventListener('pagehide', onHide);
  const body = { note_request_id: note.request_id, record_mode: recordMode(), request_id: requestId,
    chat_scope: page.chat_scope, conversation_id: page.conversation ? page.conversation.id : undefined,
    ...(turnFiles.length ? { attachment_ids: fileIds() } : {}), ...topicContext() };
  let res;
  try {
    res = await apiStream('/mc/turns/stream', body, controller.signal);
  } catch (e) {
    window.removeEventListener('pagehide', onHide);
    endLine(li, 'unknown');
    const text = 'Unknown · it may have run; nothing was retried. Your note is kept.';
    thread.append(platformLine('Guild platform', text));
    announce(text);
    answerFailed(mcSay('Master Craftsman: unknown'));
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
    if (data.job_command) { li.stopTicking(); li.remove(); jobsUi.accepted(data); return; }
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
  const deltas = [];
  let upto = 0;
  let acked = false;
  let ended = false;
  const showTail = () => { tail.textContent = deltas.slice(upto).join(''); };
  stop.addEventListener('click', async () => {
    if (!li.turnId) return;
    stop.disabled = true;
    li.dataset.stopping = 'true';
    setSlot(li, 'state', 'Stopping…');
    await apiStop(`/mc/turns/${encodeURIComponent(li.turnId)}/stop`);
  });
  const handle = (ev) => {
    if (ev.t === 'ack' && typeof ev.turn_id === 'string') {
      acked = true;
      li.turnId = ev.turn_id;
      if (Array.isArray(ev.files)) showFiles(note.id, ev.files);
      stop.hidden = false;
      follow();
      announce(mcSay('Master Craftsman is answering'));
    } else if (ev.t === 'delta' && typeof ev.text === 'string') {
      li.hasText = true;
      if (!deltas.length) setSlot(li, 'state', 'Writing…');
      deltas.push(ev.text);
      showTail();
      follow();
    } else if (ev.t === 'render' && typeof ev.html === 'string' && Number.isInteger(ev.deltas)) {
      rendered.innerHTML = ev.html;           // sanitised on the server (markdown_render.py)
      upto = Math.min(ev.deltas, deltas.length);
      showTail();
      follow();
    } else if (ev.t === 'done') {
      ended = true;
      li.stopTicking();
      applyHeader(ev);
      if (ev.reply_note && !$(`[data-note="${ev.reply_note.id}"]`)) {
        const done = noteLine(ev.reply_note);
        const meta = done.querySelector('.msg-meta');
        if (meta) meta.append(' · ', el('span', { class: 'mc-elapsed', 'data-turn-time': '' }, clock(Math.floor((Date.now() - li.startedAt) / 1000))));   // the time it took, nothing else
        li.replaceWith(done);
      } else li.remove();
      if (ev.job) jobsUi.accepted({ job: ev.job });             // the reply proposed a job and the server started it
      if (live.off) addPlatform('Guild platform', 'This answer was asked for on the record, so it is kept.');
      announce(ev.message || mcSay('Master Craftsman answered'));
      answerArrived();
      follow();
    } else if (ev.t === 'error') {
      ended = true;
      applyHeader(ev);
      endLine(li, ev.failure_class === 'stopped' ? 'Stopped' : '(interrupted)');
      if (!deltas.length) li.remove();          // partial text, if any, stays shown once and is never kept
      const text = ev.message || mcSay('Master Craftsman did not answer. Your message is kept.');
      thread.append(failureLine(text));
      announce(text);
      answerFailed(ev.mc_header || mcSay('Master Craftsman did not answer'));
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
      ? mcSay('The connection ended before the answer finished · if Master Craftsman finishes, its answer is kept: reload to see it.')
      : 'Unknown · it may have run; nothing was retried. Your message is kept.';
    thread.append(failureLine(text));
    announce(text);
    answerFailed(mcSay('Master Craftsman: connection ended'));
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

// Private (7 Oct 2026): the question goes to Master Craftsman and he answers; MiniMoi keeps nothing. The thread lives in
// this page only (every line carries data-off-record-line and goes when Private ends). The sitting id is memory-only too:
// it is what lets the agent keep the thread of one Private sitting, and it is dropped when Private ends.
let privateSession = null;
let privateBusy = false;
let privateGen = 0;          // bumped whenever a sitting ends; an answer that arrives for an older sitting is dropped, never shown
// ONE teardown for every way a sitting can end: Exit, Confirm on the record, the record button, leaving the page, and a page
// restored from the back/forward cache. It forgets the thread, the sitting id and any private draft.
export function endPrivateSitting() {
  privateGen += 1; privateSession = null; privateBusy = false;
  for (const n of $$('[data-off-record-line]')) n.remove();
  const box = $('[data-mc-input]');
  if (box) { box.value = ''; box.style.height = 'auto'; }      // a draft typed in Private is private too
}
onChange(() => { if (!live.off && (privateSession || $('[data-off-record-line]'))) endPrivateSitting(); });
window.addEventListener('pagehide', () => { if (live.off) endPrivateSitting(); });
window.addEventListener('pageshow', (e) => { if (e.persisted && live.off) endPrivateSitting(); });

async function askPrivate(text, input) {
  if (privateBusy) { refuse(mcSay('Master Craftsman is still answering your last Private message. Your draft is kept.')); return; }
  privateBusy = true;
  appendOffRecord(text);
  input.value = '';
  input.style.height = 'auto';
  if (!privateSession) privateSession = `p${newKey()}`;
  const gen = privateGen;
  const wait = waitingLine();
  wait.dataset.offRecordLine = '';
  const started = Date.now();
  const r = await apiPrivate(text, privateSession);
  wait.stopTicking();
  const took = clock(Math.floor((Date.now() - started) / 1000));
  if (gen !== privateGen) { wait.remove(); return; }      // that sitting ended while it was thinking: its answer is dropped, never shown
  privateBusy = false;
  if (!live.off) { wait.remove(); return; }
  const body = r.body || {};
  if (r.ok && body.status === 'answered' && body.reply && body.reply.text) {
    const li = clone('tpl-private-answer');
    setSlot(li, 'when', localTime(new Date().toISOString()));
    setSlot(li, 'elapsed', took);
    const box = slot(li, 'text');
    if (typeof body.reply.html === 'string' && body.reply.html) box.innerHTML = body.reply.html; else box.textContent = body.reply.text;
    wait.replaceWith(li);
    announce(mcSay('Master Craftsman answered · not kept by MiniMoi'));
  } else {
    const line = platformLine('Guild platform', body.message || mcSay('Master Craftsman did not answer. MiniMoi kept nothing.'));
    line.dataset.offRecordLine = '';
    wait.replaceWith(line);
    announce(body.message || mcSay('Master Craftsman did not answer'));
  }
  applyHeader(body);
  follow();
}

let sendingNote = false;
let noteKey = null;   // one key per composed note; a new one when the text changes

async function sendNote(input, send) {
  const text = input.value.trim();
  if (!text) return;
  if (live.off && document.body.dataset.mcPrivate === 'false') { refuse(`Private is not available with the ${mcName()}: it keeps its own record. Nothing was sent. Your draft is kept.`); return; }
  if (live.off) {       // Private: sent, answered, never kept
    if (document.body.dataset.mcTurns !== 'true') { refuse(mcSay('Master Craftsman is not switched on here, so nothing was sent. Your draft is kept.')); return; }
    await askPrivate(text, input);
    return;
  }
  if (page.chat_scope && !page.conversation) {
    const warning = $('[data-mc-refusal]');
    warning.hidden = false;
    warning.textContent = 'This conversation is unavailable. Your draft is still here; reload to retry.';
    return;
  }
  if (sendingNote) return; // Keep an on-record draft until the current turn finishes.
  if (trayBusy()) {        // a file is still being read: do not send without it, and do not carry it into a later message
    refuse('A file is still being read. Wait for it to finish, or dismiss it, then send. Your draft is kept.');
    return;
  }
  if (document.body.dataset.mcFiles === 'false' && (document.querySelector('[data-mc-tray] .att')
      || (Array.isArray(window.guildTopicContext) && window.guildTopicContext.length))) {   // this partner reads no documents: say so before anything is kept, sent or locked
    refuse(`${mcName()} does not read attached documents or topic items. Remove them, then send. Your draft is kept.`);
    return;
  }
  if (!noteKey) noteKey = newKey();
  sendingNote = true;
  send.disabled = true;
  const files = takeTray();         // the ready files go with THIS message; a message that is not kept gives them back
  try {
  const r = await apiPost('/notes', {
    request_id: noteKey, text, record_mode: recordMode(), chat_scope: page.chat_scope, conversation_id: page.conversation ? page.conversation.id : undefined,
    context: { area: document.body.dataset.area || null, item_ref: page.item_id || null, page: page.page },
  });
  const body = r.body || {};
  if (r.ok && body.result === 'kept') {
    if (!$(`[data-note="${body.note.id}"]`)) appendNote(body.note);
    applyConversation(body.conversation);
    input.value = '';
    input.style.height = 'auto';
    noteKey = null;
    for (const n of $$('[data-notes-unavailable]')) n.remove();
    const r2 = $('[data-mc-refusal]');
    if (!live.off) { r2.hidden = true; r2.textContent = ''; }
    announce(body.message);
    if (body.repeated) restoreTray(files);
    else await askMasterCraftsman(body.note, files);
  } else {
    restoreTray(files);
    refuse(body.message || 'Not saved — notes unavailable');
  }
  } finally {
    sendingNote = false;
    send.disabled = false;
  }
}

export function refuse(text) {
  const r = $('[data-mc-refusal]');
  r.hidden = false;
  r.textContent = text;
  announce(text);
}

function renderRecord() {
  $('[data-mc-send]').disabled = sendingNote && !live.off;
  const btn = $('[data-mc-record]');
  btn.setAttribute('aria-pressed', String(live.off));
  btn.setAttribute('aria-label', live.off ? 'Exit Private' : 'Go Private');
  btn.title = live.off ? 'Exit Private: go back on the record' : mcSay('Go Private: Master Craftsman answers, MiniMoi keeps nothing');
  const privateChip = $('[data-private-chip]');
  if (privateChip) privateChip.hidden = !live.off;
  if (!live.off) closePrivateExplain();
  const confirm = $('[data-mc-record-confirm]');
  if (confirm) confirm.hidden = live.known;
  const ctx = $('[data-mc-context]');
  const area = document.body.dataset.area || 'Guild';
  const item = document.body.dataset.contextItem || '';
  const mode = !live.known ? 'record mode unknown: nothing is sent until you choose'
    : live.off ? `off the record since ${localTime(live.offSince)}` : 'on the record';
  ctx.textContent = `Context: ${area}${item && item !== area ? ` · ${item}` : ''} · ${mode}`;
  const r = $('[data-mc-refusal]');
  r.hidden = true;
  r.textContent = '';             // the chip explains Private on demand; a refusal shows again from refuse()
  document.body.dataset.offRecord = String(live.off);
  document.body.dataset.recordKnown = String(live.known);
  for (const n of $$('[data-mc-header]')) {
    if (!n.dataset.publicHeader) n.dataset.publicHeader = n.textContent;
    n.textContent = live.off ? 'Private · not kept by MiniMoi' : n.dataset.publicHeader;
  }
  // Record state and its explicit action stay separate from availability.
  const chip = $('[data-record-chip]');
  if (chip) {
    chip.dataset.mode = !live.known ? 'unknown' : live.off ? 'off' : 'on';
    chip.textContent = !live.known ? 'Record mode unknown' : live.off ? 'Private' : 'On the record';
  }
}

function closePrivateExplain() {
  const why = $('[data-private-why]');
  const box = $('[data-private-explain]');
  if (box) box.hidden = true;
  if (why) why.setAttribute('aria-expanded', 'false');
}

let soonTimer = null;
function inviteSoon() {
  const out = $('[data-invite-soon]');
  if (!out) return;
  out.textContent = 'Coming soon.';
  out.hidden = false;
  announce('Coming soon.');
  window.clearTimeout(soonTimer);
  soonTimer = window.setTimeout(() => { out.hidden = true; out.textContent = ''; }, 5000);
}

export function initConversation(p) {
  page = p;
  panel = $('[data-mc-panel]'); thread = $('[data-mc-thread]'); pill = $('[data-mc-pill]');
  pill.addEventListener('click', () => { setMode('docked'); const i = $('[data-mc-input]'); if (i) i.focus(); });
  $('[data-mc-min]').addEventListener('click', () => { setMode('pill'); pill.focus(); });
  $('[data-mc-dock]').addEventListener('click', () => setMode(mode === 'docked' ? 'floating' : 'docked'));
  panel.addEventListener('keydown', (e) => {
    if (!inPage() && e.key === 'Escape' && mode === 'floating') { setMode('pill'); pill.focus(); }
  });
  if (!inPage() && new URLSearchParams(window.location.search).get('chat') === 'open') setMode('docked');
  window.addEventListener('resize', fitPanel);
  window.addEventListener('scroll', fitPanel, { passive: true });
  const chip = $('[data-record-chip]');
  if (chip) chip.addEventListener('click', () => $('[data-mc-record]').click());
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
      endPrivateSitting();
      for (const n of $$('[data-off-record-line]')) n.remove();
      addPlatform('Guild platform', 'Back on the record · nothing from the off-the-record stretch was kept');
    }
  });
  // Private: a compact chip with the explanation on demand and an easy exit.
  const why = $('[data-private-why]');
  const box = $('[data-private-explain]');
  if (why && box) {
    why.addEventListener('click', () => {
      box.hidden = !box.hidden;
      why.setAttribute('aria-expanded', String(!box.hidden));
    });
  }
  const exit = $('[data-private-exit]');
  if (exit) exit.addEventListener('click', () => $('[data-mc-record]').click());
  // Invite: grey but clickable; the whole answer is "Coming soon.". No agent is connected.
  const invite = $('[data-mc-invite]');
  if (invite) invite.addEventListener('click', inviteSoon);
  const typeBtn = $('[data-mc-type]');
  if (typeBtn) typeBtn.addEventListener('click', () => {
    document.body.dataset.typing = 'true';
    input.scrollIntoView({ block: 'nearest' });
    input.focus();
  });
  // The Ask box grows with what is typed (up to about six lines, then it scrolls) so a long message can be read and edited in place.
  // Enter sends on a computer; Shift+Enter starts a new line. On a phone Enter is a new line and the Send button sends.
  const fit = () => { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 150)}px`; };
  input.addEventListener('input', fit);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !window.matchMedia('(pointer: coarse)').matches) {
      e.preventDefault();
      sendNote(input, send);
    }
  });

  onChange(renderRecord);
  if (!live.known) {
    addPlatform('Guild platform', 'This tab does not know whether you are on the record (it may have been opened from a tab that is off the record, or the setting could not be read). Nothing is sent from it, and Continue is not updated, until you choose: Confirm on the record, or go Off the record.');
  }
  setBriefing(page.floor && page.floor.briefing);
  applyMode();
  renderRecord();
  follow();
  jobsUi = initJobs({ page, thread, noteLine, follow });
}


