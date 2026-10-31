// The conversation, real mode. The thread shows the opening briefing,
// platform lines (explain cards, Save results) and Robert's notes. On the
// record, Send keeps the message on the server as a note; then, only when the
// server says Master Craftsman takes turns (data-mc-turns), the kept note is
// sent to it server side (POST /mc/turns) and its answer, or an honest
// "did not answer", is shown. Private questions reach MC through the private
// path; their thread and drafts stay in page memory and are not kept by MiniMoi.
import { $, $$, clone, slot, setSlot, el, announce, localTime } from './dom.js';
import { live, setOff, onChange } from './state.js';
import { apiGet, apiPost, apiPrivate, apiStream, apiStop, recordMode, RECORD_UNKNOWN_TEXT } from './api.js';
import { newKey } from './actions.js';
import { answerFailed, answerArrived, answerStopped } from './floorlayout.js';
import { platformNotes, noteWatch, confirmedStop } from './platform_notes.js';

// The conversation partner's name: the server says who answers on this portal (Master Craftsman, or the Chief of Staff).
const mcName = () => document.body.dataset.mcName || 'Master Craftsman';
const mcSay = (text) => String(text).split('Master Craftsman').join(mcName());
import { applyConversation } from './conversations.js';
import { takeTray, restoreTray, trayBusy, trayCount } from './attachments.js';
import { initJobs } from './jobs.js';
import { initAgentRequests } from './agent_requests.js';
import { initQueue } from './mc_queue.js';

let page, panel, thread, pill;
const inPage = () => document.body.dataset.page === 'floor';
let mode = 'pill';

// An unsent on-record draft follows this conversation within this browser tab.
// Private and unknown-mode drafts never enter storage. This is not a server note.
const draftKey = () => page?.conversation?.id ? `${page.storage_ns || 'guild'}.chat-draft.${page.conversation.id}` : null;
function keepDraft(input) {
  const key = draftKey();
  if (!key || !live.known || live.off) return;
  try { if (input.value) window.sessionStorage.setItem(key, input.value); else window.sessionStorage.removeItem(key); } catch (e) { /* typing still works without storage */ }
}
function restoreDraft(input) {
  const key = draftKey();
  if (!key || !live.known || live.off) return;
  try { input.value = window.sessionStorage.getItem(key) || ''; } catch (e) { /* no saved draft available */ }
}
function forgetSentDraft(key, text) {
  try { if (key && window.sessionStorage.getItem(key) === text) window.sessionStorage.removeItem(key); } catch (e) { /* storage is optional */ }
}

// The Workshop page chooses topic items for ONE message ("Ask Master Craftsman about this"): ids only, owner-checked on the server,
// sent with that message and then forgotten here. Nothing is added unless the page put it in window.guildTopicContext.
function takeTopicContext() {      // the chosen items go with THIS message and are forgotten by the page
  const ctx = Array.isArray(window.guildTopicContext) ? window.guildTopicContext.slice() : [];
  if (ctx.length) { window.guildTopicContext = []; document.dispatchEvent(new CustomEvent('guild:topic-context-sent')); }
  return ctx;
}
function restoreTopicContext(ctx) {   // the server refused before sending anything: give the choice back (after anything chosen since)
  if (!ctx.length) return;
  const now = Array.isArray(window.guildTopicContext) ? window.guildTopicContext : [];
  const key = (c) => `${c.topic_id}/${c.item_id}`;
  window.guildTopicContext = [...now, ...ctx.filter((c) => !now.some((n) => key(n) === key(c)))];
  document.dispatchEvent(new CustomEvent('guild:topic-context-restored'));
}

// One message's turn, fixed when it is sent (issue #346 P3): its note, request id, files, topic items and conversation.
// Nothing about a turn is read from page state again, so a later message (typed while this one waits or runs) can never
// lend it its files, items or conversation, or take its answer.
// The target is read at Send, before anything is awaited (sendTarget); the note and request id are added once the note is kept.
function sendTarget() {
  return Object.freeze({ conversationId: page.conversation ? page.conversation.id : undefined, chatScope: page.chat_scope });
}
function snapshot(note, files, ctx, target) {
  const at = target || sendTarget();
  return Object.freeze({ note, requestId: newKey(), files: Object.freeze((files || []).slice()), ctx: Object.freeze((ctx || []).slice()),
    conversationId: at.conversationId, chatScope: at.chatScope });
}
function turnBody(t) {
  return { note_request_id: t.note.request_id, record_mode: recordMode(), request_id: t.requestId, chat_scope: t.chatScope,
    conversation_id: t.conversationId, ...(t.files.length ? { attachment_ids: t.files.map((f) => f.id) } : {}),
    ...(t.ctx.length ? { topic_context: t.ctx.map((c) => ({ topic_id: c.topic_id, item_id: c.item_id, rev: c.rev || undefined })) } : {}) };
}
function giveBack(t) {             // nothing was recorded or sent: the files and items return for the next message
  if (t.files.length) restoreTray(t.files.slice());
  restoreTopicContext(t.ctx.slice());
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
  const head = $('.mc-head', panel);                                   // the header is a tab stop only while the panel floats and can be moved
  if (head) { if (mode === 'floating' && window.matchMedia('(min-width: 641px)').matches) head.setAttribute('tabindex', '0'); else head.removeAttribute('tabindex'); }
}

function fitPanel() {
  if (!panel || inPage() || mode !== 'docked') return;
  const top = Math.max(0, panel.getBoundingClientRect().top);
  panel.style.setProperty('--mc-panel-height', `${Math.max(180, (window.visualViewport?.height || window.innerHeight) - top)}px`);
}
function setMode(next) { mode = next; applyMode(); syncFloatPosition(); requestAnimationFrame(fitPanel); requestAnimationFrame(syncFloatPosition); }

// A floating panel can be moved by its header on a desktop (never by a button or a field inside it). The position is kept in this browser, is
// pulled back inside the window after a resize, and can be moved with the arrow keys (Home puts it back). A phone keeps its own bottom dock.
const POS_KEY = 'guild.mc.pos';
const canMove = () => mode === 'floating' && !inPage() && window.matchMedia('(min-width: 641px)').matches;
const keptPos = () => { try { const v = JSON.parse(window.localStorage.getItem(POS_KEY) || 'null'); return v && Number.isFinite(v.x) && Number.isFinite(v.y) ? v : null; } catch (e) { return null; } };
function placePanel(x, y, { keep = true } = {}) {
  const r = panel.getBoundingClientRect();
  const nx = Math.round(Math.min(Math.max(8, x), Math.max(8, window.innerWidth - r.width - 8)));
  const ny = Math.round(Math.min(Math.max(8, y), Math.max(8, window.innerHeight - 56)));      // the header always stays on screen
  panel.style.setProperty('--mc-x', `${nx}px`); panel.style.setProperty('--mc-y', `${ny}px`);
  panel.dataset.pos = 'xy';
  const reset = $('[data-mc-resetpos]'); if (reset) reset.hidden = false;
  if (keep) { try { window.localStorage.setItem(POS_KEY, JSON.stringify({ x: nx, y: ny })); } catch (e) { /* the place is a convenience */ } }
}
function resetPanel() {
  panel.style.removeProperty('--mc-x'); panel.style.removeProperty('--mc-y'); panel.dataset.pos = 'right';
  const reset = $('[data-mc-resetpos]'); if (reset) reset.hidden = true;
  try { window.localStorage.removeItem(POS_KEY); } catch (e) { /* nothing was kept */ }
}
function syncFloatPosition() {                       // on opening, on resize: where it was left, or the default; never off screen, never on a phone
  if (!panel) return;
  const kept = keptPos();
  if (canMove() && kept) { placePanel(kept.x, kept.y, { keep: false }); return; }
  if (panel.dataset.pos === 'xy') { panel.dataset.pos = 'right'; panel.style.removeProperty('--mc-x'); panel.style.removeProperty('--mc-y'); }
  const reset = $('[data-mc-resetpos]'); if (reset) reset.hidden = !(canMove() && kept);
}
function initMove() {
  const head = $('.mc-head', panel); if (!head || inPage()) return;
  head.setAttribute('role', 'group');
  head.setAttribute('aria-label', 'Conversation window. When this is focused, the arrow keys move it and Home puts it back.');
  let drag = null;
  head.addEventListener('pointerdown', (e) => {
    if (!canMove() || e.button !== 0 || e.target.closest('button, a, input, textarea, select')) return;
    const r = panel.getBoundingClientRect();
    drag = { dx: e.clientX - r.left, dy: e.clientY - r.top, id: e.pointerId };
    head.setPointerCapture(e.pointerId); panel.classList.add('is-dragging');
  });
  head.addEventListener('pointermove', (e) => { if (drag && e.pointerId === drag.id) placePanel(e.clientX - drag.dx, e.clientY - drag.dy, { keep: false }); });
  const end = (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    drag = null; panel.classList.remove('is-dragging');
    try { head.releasePointerCapture(e.pointerId); } catch (er) { /* already released */ }
    const r = panel.getBoundingClientRect(); placePanel(r.left, r.top);
  };
  head.addEventListener('pointerup', end); head.addEventListener('pointercancel', end);
  head.addEventListener('keydown', (e) => {
    if (e.target !== head || !canMove()) return;
    const step = e.shiftKey ? 64 : 16; const r = panel.getBoundingClientRect();
    const to = { ArrowLeft: [r.left - step, r.top], ArrowRight: [r.left + step, r.top], ArrowUp: [r.left, r.top - step], ArrowDown: [r.left, r.top + step] }[e.key];
    if (to) { e.preventDefault(); placePanel(to[0], to[1]); } else if (e.key === 'Home') { e.preventDefault(); resetPanel(); announce('The conversation window is back in its usual place.'); }
  });
  const reset = $('[data-mc-resetpos]'); if (reset) reset.addEventListener('click', () => { resetPanel(); announce('The conversation window is back in its usual place.'); });
  window.addEventListener('resize', () => syncFloatPosition());
}
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

// Each message has one small details control (reachable by keyboard and by tap): it shows the time, the context and "kept as a note". Not on the Chat page.
function decorateMessages() {
  if (inPage() || !thread) return;
  for (const li of $$('li.msg-note', thread)) {
    if (li.dataset.detailsReady) continue;
    li.dataset.detailsReady = '1';
    const meta = li.querySelector('.msg-meta'); if (!meta) continue;
    const who = ((li.querySelector('.msg-label') || {}).textContent || '').trim() || 'message';
    const b = el('button', { type: 'button', class: 'msg-info', 'aria-expanded': 'false', 'aria-label': `Details of this message from ${who}`, title: 'Time, context and how this is kept' }, 'ⓘ');
    b.addEventListener('click', () => { const on = li.dataset.details !== 'open'; if (on) li.dataset.details = 'open'; else delete li.dataset.details; b.setAttribute('aria-expanded', String(on)); });
    meta.append(b);
  }
}

export function setBriefing(briefing) {
  if (!briefing) return;
  const text = $('[data-briefing-text]');
  // The server writes the time in UTC; show it in local time like every other time here.
  if (text) text.textContent = briefing.text.replace(/^As of [^·]+·/, `As of ${localTime(briefing.observed_at)} ·`);
}

// A revealed voice record (Spec 164 §6): Robert's spoken words and MC's, each as said; one compact line per consult (its
// receipt); MC's full answer only when it says more than was spoken; the consult's own note hidden (it repeats his words).
// The server-rendered thread does the same (_mc_conversation.html, voice_row).
const VOICE_HIDDEN = ['consult_note', 'consult_request'];
const NOT_ACTED = 'Not acted on: say it again if you still want it';
const CUT_OFF = 'Cut off at End';                  // a turn whose reply his End cut off: not "not acted on" (Gate 2 Q N12)
function voiceLine(note) {
  const v = note.voice;
  const hidden = VOICE_HIDDEN.includes(v.kind) || (v.kind === 'consult_reply' && v.full_differs === false);
  const turn = v.kind === 'owner_turn' || v.kind === 'assistant_turn';
  if (!hidden && !turn && v.kind !== 'consult_receipt' && v.kind !== 'consult_reply') return null;     // summaries, outcomes: as notes
  const li = el('li', { class: `msg msg-voice ${v.kind === 'consult_receipt' ? 'msg-platform msg-voice-receipt' : 'msg-note'}`,
    'data-note': note.id, 'data-voice-kind': v.kind, 'data-voice-session': v.session_id, 'data-at': v.started_at || note.created_at,
    'data-author-kind': v.kind === 'owner_turn' ? 'owner' : turn || v.kind === 'consult_reply' ? 'agent' : 'platform' });
  if (hidden) { li.hidden = true; return li; }
  if (v.kind === 'consult_receipt' && v.outcome) {
    // "Checked with MC · answered", then what was spoken; the reply / operation / job id stays in the record and the
    // title, never in the visible text (UI review D1).
    if (v.ref) { li.setAttribute('data-voice-ref', v.ref); li.setAttribute('title', `${v.ref_kind || 'Receipt'} ${v.ref}`); }
    const head = el('p', { class: 'msg-meta' });
    head.append(el('span', { class: 'msg-label' }, `Checked with MC · ${v.outcome}`));
    li.append(head);
    if (v.spoken) li.append(el('p', { class: 'msg-text small' }, v.spoken));
    return li;
  }
  if (v.kind === 'consult_receipt') { li.append(el('p', { class: 'msg-text small' }, v.text || note.text)); return li; }
  if (turn && Number(v.part) > 1) { li.setAttribute('data-voice-part', String(v.part)); li.append(el('p', { class: 'msg-text' }, v.text)); return li; }
  const label = v.kind === 'owner_turn' ? 'You · spoken' : v.kind === 'assistant_turn' ? `${mcName()} · spoken` : `${mcName()}'s full answer`;
  const meta = el('p', { class: 'msg-meta' });
  meta.append(el('span', { class: 'msg-label' }, label), document.createTextNode(' · '),
    el('span', { 'data-iso': v.started_at || note.created_at }, localTime(v.started_at || note.created_at)));
  if (v.interrupted) meta.append(document.createTextNode(' · interrupted'));
  else if (v.partial) meta.append(document.createTextNode(' · partial'));
  // His words that were not acted on (review B S3, N2: a reply cancelled or overtaken, or a commit let go): say so.
  if (v.not_acted) meta.append(document.createTextNode(' · '), el('span', { 'data-voice-not-acted': '' }, NOT_ACTED));
  else if (v.cut_off) meta.append(document.createTextNode(' · '), el('span', { 'data-voice-cut-off': '' }, CUT_OFF));
  li.append(meta);
  if (turn) { li.append(el('p', { class: 'msg-text' }, v.text)); return li; }
  const body = el('div', { class: 'msg-text msg-md' });
  if (typeof note.html === 'string' && note.html) body.innerHTML = note.html;          // sanitised on the server
  else body.textContent = note.text;
  li.append(body);
  return li;
}

function noteLine(note) {
  if (note && note.voice && typeof note.voice === 'object') {
    const voiced = voiceLine(note);
    if (voiced) return voiced;
  }
  const li = clone('tpl-note');
  li.dataset.note = note.id;
  if (note.created_at) li.dataset.at = note.created_at;
  if (note.voice && typeof note.voice === 'object') { li.dataset.voiceKind = note.voice.kind; li.dataset.voiceSession = note.voice.session_id; }
  if (note.request_id) li.dataset.noteRequest = note.request_id;
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

// Notes this page did not have yet (a voice session revealed at End): each put in its place by id, never twice. Only notes
// newer than the oldest one shown are added, as watchPlatformNotes reads only after its mark: older history the page never
// showed stays unloaded (UI review #6). Returns how many were added. Nothing is re-sent: these are the server's kept records.
export function mergeNotes(notes) {
  if (!thread || !Array.isArray(notes)) return 0;
  const shown = Array.from(thread.querySelectorAll('[data-note]'), (n) => Number(n.dataset.note)).filter(Number.isSafeInteger);
  const oldest = shown.length ? Math.min(...shown) : 0;
  let added = 0;
  for (const note of notes.slice().sort((a, b) => Number(a.id) - Number(b.id))) {
    if (!note || note.id == null || !(Number(note.id) > oldest) || thread.querySelector(`[data-note="${note.id}"]`)) continue;
    const next = Array.from(thread.querySelectorAll('[data-note]')).find((n) => Number(n.dataset.note) > Number(note.id));
    thread.insertBefore(noteLine(note), next || thread.querySelector('[data-live-turn]') || null);
    added += 1;
  }
  if (added) follow();
  return added;
}

function appendNote(note) {
  thread.append(noteLine(note));
  follow();
}

// Every first page of notes this page reads for a conversation is announced, so the voice thread (mc_voice.js) repaints its
// recovering / elsewhere-live lines and summary cards from it, with no read of its own (UI review #7).
function notesRead(conversationId, r, before) {
  if (!before && r?.ok && Array.isArray(r.body?.voice_sessions)) {
    document.dispatchEvent(new CustomEvent('guild:notes-read', { detail: { conversationId: conversationId || null,
      notes: Array.isArray(r.body.notes) ? r.body.notes : [], voice_sessions: r.body.voice_sessions } }));
  }
  return r;
}

// The assistant response is not the platform's proposal. Read the persisted
// cards independently, scoped to the conversation captured when Send was pressed.
// This also serves buffered turns; it never retries a chat/tool write.
function watchPlatformNotes(t) {
  const after = Number(t.note.id) || Math.max(0, ...Array.from(thread.querySelectorAll('[data-note]'), (n) => Number(n.dataset.note) || 0));
  let disposed = false;
  const current = () => !disposed && live.known && !live.off && !document.hidden
    && page.conversation?.id === t.conversationId && page.chat_scope === t.chatScope;
  const sync = platformNotes({ after, current,
    fetchPage: async (before) => {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 5000);
      try {
        const conv = t.conversationId ? `&conversation=${encodeURIComponent(t.conversationId)}` : '';
        return notesRead(t.conversationId, await apiGet(`/notes?limit=200${conv}${before ? `&before=${before}` : ''}`, undefined, controller.signal), before);
      } finally { window.clearTimeout(timeout); }
    },
    insert: (note) => {
      if (thread.querySelector(`[data-note="${note.id}"]`)) return;
      const rows = Array.from(thread.querySelectorAll('[data-note]'));
      const next = rows.find((n) => Number(n.dataset.note) > note.id);
      const active = thread.querySelector('[data-live-turn]');
      thread.insertBefore(noteLine(note), next || active || null);
      follow();
    },
  });
  const dispose = () => watcher.dispose();
  const wake = () => { if (!document.hidden) void watcher.wake(); };
  const watcher = noteWatch({ refresh: () => sync.refresh(), onDispose: () => {
    disposed = true;
    window.removeEventListener('pagehide', dispose);
    window.removeEventListener('online', wake);
    document.removeEventListener('visibilitychange', wake);
  } });
  window.addEventListener('pagehide', dispose, { once: true });
  window.addEventListener('online', wake);
  document.addEventListener('visibilitychange', wake);
  void watcher.wake();
  return () => watcher.finish();
}

async function withPlatformNotes(t, work) {
  const finish = watchPlatformNotes(t);
  try { return await work(); } finally { await finish(); }
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
  li.dataset.liveTurn = 'true';
  const elapsed = li.querySelector('[data-slot="elapsed"]');
  const started = Date.now();
  const still = li.querySelector('[data-slot="still"]');
  // After 20 s of silence a quiet "still working" joins the clock (11 Oct: a healthy turn can think for 20 s before its first word, and a bare counter
  // looked like a stall). It claims nothing about what Master Craftsman is doing; it only says this page is still waiting for the answer.
  const tick = () => {
    const secs = Math.floor((Date.now() - started) / 1000);
    elapsed.textContent = clock(secs);
    if (still) still.hidden = secs < 20;
  };
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

// Shows a finished (non-streamed, or repeated) answer in place of `holder`. Returns {answered, error} for this turn only.
function showAnswer(holder, r, t) {
  const body = r.body || {};
  applyHeader(body);
  const kept = () => ({ answered: true, error: null });
  if (r.ok && body.status === 'answered' && body.reply_note) {
    if ($(`[data-note="${body.reply_note.id}"]`)) holder.remove();
    else holder.replaceWith(noteLine(body.reply_note));
    announce(body.message || mcSay('Master Craftsman answered'));
    answerArrived();
    agentUi.kick();
    follow();
    return kept();
  } else if (r.ok && body.operation?.type === 'board_postit' && body.operation.saved === true && Number.isInteger(body.operation.id) && body.operation.id > 0) {
    // The Board committed but its separate conversation receipt failed. Show
    // the real saved outcome without pretending this line survived a reload.
    holder.replaceWith(platformLine('Board', `${body.message} The Board is saved; this confirmation could not be kept in the conversation.`));
    announce(body.message);
    answerArrived();
    follow();
    return kept();
  } else if (r.ok && body.operation?.type === 'library_intake' && body.operation.saved === true && ['saved', 'already_saved_attached'].includes(body.operation.receipt?.result)) {
    // Library committed; a separate conversation-receipt failure cannot undo it.
    holder.replaceWith(platformLine('Library', `${body.message} The Library item is saved; this confirmation could not be kept in the conversation.`));
    announce(body.message);
    answerArrived();
    follow();
    return kept();
  } else if (r.ok && ['cos_note', 'reminder_set'].includes(body.operation?.type) && body.operation.saved === true) {
    holder.replaceWith(platformLine(body.operation.type === 'cos_note' ? 'Notes' : 'Reminders', `${body.message} This confirmation could not be kept in the conversation.`));
    announce(body.message);
    answerArrived();
    follow();
    return kept();
  } else if (r.ok && body.operation?.type === 'work_register' && body.operation.saved === true && /^MC-[0-9]{4,8}$/.test(body.operation.id)) {
    // Canonical card committed; the separate chat receipt is not durable.
    holder.replaceWith(platformLine('Register', `${body.message} The register entry is saved; this confirmation could not be kept in the conversation.`));
    announce(body.message);
    answerArrived();
    follow();
    return kept();
  }
  if (confirmedStop(body)) {
    if (holder.stopTicking) holder.stopTicking();
    holder.replaceWith(platformLine('Guild platform', body.message || 'Stopped. Your message is kept.'));
    answerStopped();
    announce(body.message || 'Stopped');
    follow();
    return { answered: false, error: null };
  }
  // Another tab is sending it, or a pause holds it: nothing to say here (the line above the composer shows a pause and its Continue).
  if (t.queued && ['busy', 'not_next', 'not_queued', 'paused'].includes(body.error)) { holder.remove(); return { answered: false, error: body.error }; }
  // A waiting message refused at its turn stays on the server (first in line, or failed with a pause): its files are not handed back.
  if (!t.queued && NOT_DISPATCHED.includes(body.error)) giveBack(t);
  const text = body.message || mcSay('Master Craftsman did not answer. Your message is kept.');
  holder.replaceWith(failureLine(text));
  announce(text);
  answerFailed(body.mc_header || mcSay('Master Craftsman did not answer'));
  follow();
  return { answered: false, error: body.error || (r.status === 0 ? 'network' : null) };
}

// Which files went into the request for a note, exactly as the server reports them (it computes this from the text it
// really put in the request). "Sent" is the honest word: it says what the request carried, not that the model read it.
// A file that went in full is only a chip with its name; a cut or a file that did not go says so.
const FILE_WORDS = { read: () => '', partly_read: (f) => `sent in part (${f.reason})`, staged: (f) => `copy placed for Master Craftsman to open (${f.reason})`, not_read: (f) => `not sent (${f.reason})` };
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
let jobsUi = { accepted() {}, refresh() {} };
let agentUi = { kick() {} };

// One turn per kept note: streamed when the server says so (data-mc-stream,
// computed from the switch and the backend), else the non-streaming path.
// One request id per turn, so the server can refuse any second dispatch.
// Resolves to {answered, error} for this turn (the voice path speaks a reply only when one was kept). `ctx` and `target`
// were taken at Send; a spoken message carries no chosen topic items and no tray (those stay for the next typed message).
function askMasterCraftsman(note, files, opts, ctx, target) {
  clearFailureLines();
  if (live.off || document.body.dataset.mcTurns !== 'true') { restoreTray(files || []); restoreTopicContext(ctx || []); return Promise.resolve({ answered: false, error: 'mc_turns_off' }); }   // no turn: nothing was sent, the files stay in the tray
  const t = snapshot(note, files, opts && opts.voice ? [] : ctx, target);
  return withPlatformNotes(t, () => document.body.dataset.mcStream === 'true' ? streamMasterCraftsman(t) : turnMasterCraftsman(t));
}

// A waiting message's turn (mc_queue.js): only the server's ids are needed; the server sends exactly what was queued.
// This tab's own snapshot, when it has one, is used for showing the files it reported; it is never sent again.
const queuedHere = new Map();      // request id -> this tab's snapshot of a message it queued
function dispatchQueued(entry, here) {
  const own = queuedHere.get(entry.request_id);
  const t = Object.freeze({ note: own ? own.note : { id: null, request_id: entry.note_request_id }, requestId: entry.request_id,
    files: own ? own.files : Object.freeze([]), ctx: own ? own.ctx : Object.freeze([]), conversationId: entry.conversation_id,
    chatScope: own ? own.chatScope : page.chat_scope, queued: entry.id });
  const row = thread.querySelector(`[data-note-request="${CSS.escape(entry.note_request_id)}"]`);
  if (row && !t.note.id) t.note.id = Number(row.dataset.note);
  const chip = row && row.querySelector('[data-queued-chip]');
  if (chip) chip.remove();                     // it is going out now; the answer (or what happened) appears below
  const path = `/mc/turns/queue/${entry.id}/dispatch`;
  if (entry.conversation_id !== here) return dispatchElsewhere(path);
  const done = withPlatformNotes(t, () => document.body.dataset.mcStream === 'true' ? streamMasterCraftsman(t, { path, body: { stream: true } })
    : turnMasterCraftsman(t, null, { path, body: { stream: false } }));
  return done.finally(() => queuedHere.delete(entry.request_id));
}

// The head belongs to another conversation: send it (the queue is one line for the whole owner) and say only where its answer is kept.
async function dispatchElsewhere(path) {
  const r = await apiPost(path, { stream: false });
  const body = r.body || {};
  if (r.ok && body.status === 'answered') {
    addPlatform('Guild platform', 'A waiting message from another conversation was sent and answered; its answer is kept in that conversation.');
    return { answered: true, error: null };
  }
  if (body.error && !['busy', 'not_next', 'not_queued'].includes(body.error)) addPlatform('Guild platform', body.message || 'A waiting message from another conversation was not answered.');
  return { answered: false, error: body.error || (r.status === 0 ? 'network' : null) };
}

async function turnMasterCraftsman(t, holder, via) {
  const waiting = holder || waitingLine();
  let r;
  try {
    r = await apiPost(via ? via.path : '/mc/turns', via ? via.body : turnBody(t));
  } finally {
    if (waiting.stopTicking) waiting.stopTicking();
  }
  if (r && r.body && r.body.job_command) { waiting.remove(); jobsUi.accepted(r.body); return { answered: true, error: null }; }     // a spoken job command: the server answered it
  if (r && r.body && r.body.files && t.note.id) showFiles(t.note.id, r.body.files);
  const out = showAnswer(waiting, r, t);
  if (r && r.body && r.body.job) jobsUi.accepted({ job: r.body.job });
  return out;
}

// ── Streaming (streaming spec v0.2 §3 and §6, v0.3 §2 and §5) ──
// Deltas are text and only ever set with textContent. The only HTML inserted
// is the server's sanitised rendering (render and done events). Closing the
// tab is not Stop: the server finishes and keeps a real answer. Stop is the
// explicit control, and says plainly what it cannot save.
const STREAM_LINE_CAP = 2 * 1024 * 1024;

function streamLine() {
  const li = clone('tpl-mc-stream');
  li.dataset.liveTurn = 'true';
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

// A render event carries the whole answer so far (#402). Replacing it all twice a second tore down and rebuilt every
// settled paragraph and table, and snapped an opened Details shut. Instead the blocks that did not change are left
// alone: only the first changed block and what follows it are replaced, and a Details fold is kept (with its open
// state) while its own blocks are brought up to date the same way.
function sameNode(a, b) {
  if (a.nodeType !== b.nodeType) return false;
  return a.nodeType === 1 ? a.outerHTML === b.outerHTML : a.textContent === b.textContent;
}

function morphChildren(parent, next) {
  const cur = Array.from(parent.childNodes);
  let i = 0;
  while (i < cur.length && i < next.length && sameNode(cur[i], next[i])) i += 1;
  if (i < cur.length && i < next.length && cur[i].nodeName === 'DETAILS' && next[i].nodeName === 'DETAILS') {
    morphChildren(cur[i], Array.from(next[i].childNodes));
    i += 1;
  }
  for (const n of cur.slice(i)) n.remove();
  for (const n of next.slice(i)) parent.append(n);
}

function applyRendered(rendered, html, doc = document) {
  const t = doc.createElement('template');
  t.innerHTML = html;                                    // sanitised on the server (markdown_render.py)
  morphChildren(rendered, Array.from(t.content.childNodes));
}

function endLine(li, state) {
  li.stopTicking();
  li.dataset.ended = 'true';
  delete li.dataset.liveTurn;
  li.removeAttribute('aria-busy');
  setSlot(li, 'state', state);
  slot(li, 'elapsed').textContent = clock(Math.floor((Date.now() - (li.startedAt || Date.now())) / 1000));   // the time it took stays on the message
  const controls = slot(li, 'controls');
  if (controls) controls.remove();
}

async function streamMasterCraftsman(t, via) {
  const note = t.note;
  const li = streamLine();
  const controller = new AbortController();
  const onHide = () => controller.abort();          // harmless: the server finishes and keeps the answer
  window.addEventListener('pagehide', onHide);
  let res;
  let answered = false;
  try {
    res = await apiStream(via ? via.path : '/mc/turns/stream', via ? via.body : turnBody(t), controller.signal);
  } catch (e) {
    window.removeEventListener('pagehide', onHide);
    endLine(li, 'unknown');
    const text = 'Unknown · it may have run; nothing was retried. Your note is kept.';
    thread.append(platformLine('Guild platform', text));
    announce(text);
    answerFailed(mcSay('Master Craftsman: unknown'));
    follow();
    return { answered: false, error: 'network' };
  }
  if (res.refused) {
    window.removeEventListener('pagehide', onHide);
    li.remove();
    return { answered: false, error: 'not_listening' };
  }
  const type = res.headers.get('Content-Type') || '';
  if (!type.includes('application/x-ndjson')) {
    window.removeEventListener('pagehide', onHide);
    let data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    if (data.job_command) { li.stopTicking(); li.remove(); jobsUi.accepted(data); return { answered: true, error: null }; }
    // Only an explicit, server-declared pre-dispatch refusal falls back to the
    // non-streaming path; nothing was sent, so this is not a second dispatch.
    // (A waiting message refused this way is first in line again; the same dispatch is asked for without streaming.)
    if (res.status === 404 || res.status === 405 || ['stream_off', 'stream_unsupported'].includes(data.error)) {
      li.stopTicking();
      li.remove();
      return turnMasterCraftsman(t, null, via ? { path: via.path, body: { stream: false } } : undefined);
    }
    li.stopTicking();
    return showAnswer(li, { ok: res.ok, status: res.status, body: data }, t);
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
      // Receipt of this turn is confirmed; its intended scope comes from MC, not the transport.
      setSlot(li, 'state', 'Received · starting');
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
      applyRendered(rendered, ev.html);       // only the changed blocks are replaced (#402)
      upto = Math.min(ev.deltas, deltas.length);
      showTail();
      follow();
    } else if (ev.t === 'done') {
      ended = true;
      li.stopTicking();
      if (ev.reply_note) answered = true;
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
      agentUi.kick();
      follow();
    } else if (ev.t === 'error') {
      ended = true;
      applyHeader(ev);
      const stopped = confirmedStop(ev);
      endLine(li, stopped ? 'Stopped · partial answer not saved' : '(interrupted)');
      if (!deltas.length) li.remove();          // partial text, if any, stays shown once and is never kept
      const text = ev.message || mcSay('Master Craftsman did not answer. Your message is kept.');
      thread.append(stopped ? platformLine('Guild platform', text) : failureLine(text));
      announce(text);
      if (stopped) answerStopped();
      else answerFailed(ev.mc_header || mcSay('Master Craftsman did not answer'));
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
    return { answered: false, error: 'network' };
  }
  return { answered, error: null };
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
  if (box) { box.value = ''; restoreDraft(box); checkLength(box); box.style.height = 'auto'; }      // never carry a Private draft back on the record
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
let queue = null;     // waiting messages (mc_queue.js), only while the turn ledger is on
let noteKey = null;   // one key per composed note; a new one when the text changes

// The History picker hides the thread while it is open, so a send closes it and the answer is seen arriving.
function closeHistory() {
  const picker = $('[data-conv-picker]');
  if (picker && picker.open) picker.open = false;
}

// A spoken message (MC voice, v1): the owner's final transcript, kept as an ordinary note and put to Master Craftsman exactly like a typed one, but on the record
// only, with no tray, no topic items and no change to the typed draft. One turn at a time (shared with typing). Resolves to {ok, note_request_id} or {ok:false, reason, message}.
// The caller (mc_voice.js) speaks the kept answer; nothing is spoken unless this says an answer was kept.
export async function submitVoiceUtterance(text, requestId) {
  const say = (reason, message) => ({ ok: false, reason, message });
  text = (text || '').trim();
  if (!text) return say('empty', 'Nothing was heard, so nothing was sent.');
  if (!live.known) return say('record_unknown', RECORD_UNKNOWN_TEXT);
  if (live.off) return say('private', 'Voice is not built for Private yet, so nothing was sent.');
  if (document.body.dataset.mcTurns !== 'true') return say('mc_off', mcSay('Master Craftsman is not switched on here, so nothing was sent.'));
  if (page.chat_scope && !page.conversation) return say('no_conversation', 'This conversation is unavailable, so nothing was sent.');
  if (page.note_max && codePoints(text) > page.note_max) return say('too_long', `That was ${codePoints(text).toLocaleString('en-US')} characters; the limit is ${page.note_max.toLocaleString('en-US')}. Nothing was sent.`);
  if (sendingNote) return say('busy', 'A message is still being answered, so nothing was sent. Say it again when it finishes.');
  closeHistory();
  sendingNote = true;
  const send = $('[data-mc-send]');
  if (send) send.disabled = true;
  try {
    const r = await apiPost('/notes', { request_id: requestId, text, record_mode: recordMode(), chat_scope: page.chat_scope,
      conversation_id: page.conversation ? page.conversation.id : undefined,
      context: { area: document.body.dataset.area || null, item_ref: page.item_id || null, page: page.page } });
    const body = r.body || {};
    if (!(r.ok && body.result === 'kept')) return say('not_kept', body.message || 'Not saved — notes unavailable');
    if (body.repeated) return say('duplicate', 'That message was already sent; nothing was sent again.');
    if (!$(`[data-note="${body.note.id}"]`)) appendNote(body.note);
    applyConversation(body.conversation);
    announce(body.message);
    const out = await askMasterCraftsman(body.note, [], { voice: true });
    return out.answered ? { ok: true, note_request_id: body.note.request_id } : say('no_answer', 'Master Craftsman did not answer. Your message is kept.');
  } finally {
    sendingNote = false;
    if (send) send.disabled = false;
  }
}

// A long message is never silently clipped (an HTML maxlength would cut a paste without a word): the whole text stays in the box, a plain line says how long it is and what the
// limit is, and nothing is sent until it fits. Counted in characters (code points), the way the server counts them.
const codePoints = (s) => { let n = 0; for (const _c of s) n += 1; return n; };    // eslint-disable-line no-unused-vars
let lengthShown = false;
function checkLength(input) {
  const max = live.off ? page.private_max : page.note_max;           // a Private question has its own, longer limit; both are said before anything is sent
  if (!max) { clearLength(input); return false; }
  const n = codePoints(input.value.trim());
  if (n <= max) { clearLength(input); return false; }
  const text = `This message is ${n.toLocaleString('en-US')} characters; the limit is ${max.toLocaleString('en-US')}. Nothing was sent, and your full text is still here. Shorten it, or send it as two messages.`;
  const r = $('[data-mc-refusal]');
  if (!lengthShown) refuse(text); else { r.hidden = false; r.textContent = text; }   // announced once, then updated quietly as the count changes
  lengthShown = true;
  input.setAttribute('aria-invalid', 'true');
  return true;
}
function clearLength(input) {
  if (!lengthShown) return;
  lengthShown = false;
  input.removeAttribute('aria-invalid');
  const r = $('[data-mc-refusal]');
  if (r) { r.hidden = true; r.textContent = ''; }
}

// With the turn ledger on, a kept message is queued, not sent here: Send is free again as soon as the server has recorded it,
// and mc_queue.js sends waiting messages one at a time, in order. Only the note save and this record are one at a time per tab.
// An answer that does not say what happened (the connection dropped, a gateway error page): the server may have recorded it.
const UNCLEAR = (r) => r.status === 0 || ['network', 'unreadable'].includes((r.body || {}).error) || (r.status >= 500 && !(r.body || {}).error);
async function queueTurn(note, files, ctx, target) {
  clearFailureLines();
  const t = snapshot(note, files, ctx, target);
  queuedHere.set(t.requestId, t);
  const body = { ...turnBody(t), queue: true };
  let r = await apiPost('/mc/turns', body);
  // Unclear: ask again with the SAME ids. The server answers an exact repeat with the same waiting entry (or what became
  // of it), so this can never put the message in line twice; nothing is said to be "not sent" until the server says so.
  for (const wait of [1000, 3000, 9000]) {
    if (!UNCLEAR(r)) break;
    await new Promise((ok) => { window.setTimeout(ok, wait); });
    r = await apiPost('/mc/turns', body);
  }
  if (r.status === 202 && r.body && r.body.status === 'queued') { queue.queued(r.body); return; }
  const answer = r.body || {};
  if (UNCLEAR(r)) {                             // still unknown: keep its files with it, and let the ledger view settle it
    thread.append(failureLine('It is not confirmed whether this message was put in line or sent. It is kept; '
      + 'if it is waiting, it shows as waiting, and if it was answered, the answer is kept.'));
    announce('Not confirmed.');
    queue.pump();
    follow();
    return;
  }
  queuedHere.delete(t.requestId);
  if (!['already_done', 'already_dispatched', 'already_queued'].includes(answer.error)) giveBack(t);     // refused: nothing was recorded, so files and items come back
  thread.append(failureLine(answer.message || 'This message could not be put in line. It is kept; nothing was sent.'));
  announce(answer.message || 'Not queued.');
  follow();
}

async function sendNote(input, send) {
  const originalDraft = input.value;
  const originalDraftKey = draftKey();
  // A neutral attachment label preserves the existing kept-note contract without inventing an instruction.
  const text = input.value.trim() || (!live.off && trayCount() ? 'Attached files.' : '');
  if (!text) {
    if (trayBusy()) refuse('A file is still being read. Wait for it to finish, then send.');
    return;
  }
  if (checkLength(input)) return;
  if (live.off && document.body.dataset.mcPrivate === 'false') { refuse(`${privateOffWhy()}. Nothing was sent. Your draft is kept.`); return; }
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
  closeHistory();
  sendingNote = true;
  send.disabled = true;
  // Everything this message carries is fixed now, before anything is awaited: what is chosen while it saves is for the next one.
  const target = sendTarget();
  const files = takeTray();         // the ready files go with THIS message; a message that is not kept gives them back
  const ctx = takeTopicContext();
  try {
  const r = await apiPost('/notes', {
    request_id: noteKey, text, record_mode: recordMode(), chat_scope: target.chatScope, conversation_id: target.conversationId,
    context: { area: document.body.dataset.area || null, item_ref: page.item_id || null, page: page.page },
  });
  const body = r.body || {};
  if (r.ok && body.result === 'kept') {
    if (!$(`[data-note="${body.note.id}"]`)) appendNote(body.note);
    applyConversation(body.conversation);
    forgetSentDraft(originalDraftKey, originalDraft);
    // A note may finish saving after another draft was typed or Private began.
    if (!live.off && input.value === originalDraft) {
      input.value = '';
      input.style.height = 'auto';
    }
    noteKey = null;
    for (const n of $$('[data-notes-unavailable]')) n.remove();
    const r2 = $('[data-mc-refusal]');
    if (!live.off) { r2.hidden = true; r2.textContent = ''; }
    announce(body.message);
    if (body.repeated) { restoreTray(files); restoreTopicContext(ctx); }
    else if (queue && document.body.dataset.mcTurns === 'true') await queueTurn(body.note, files, ctx, target);
    else await askMasterCraftsman(body.note, files, undefined, ctx, target);
  } else {
    restoreTray(files);
    restoreTopicContext(ctx);
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

// Why Private cannot be chosen here: the server's own reason (the partner keeps its own record, or every message is
// recorded before it is sent), never a guess made on the page.
function privateOffWhy() {
  const btn = $('[data-mc-record]');
  return (btn && btn.dataset.privateOffWhy) || `Private is not available with the ${mcName()}: it keeps its own record`;
}

function renderRecord() {
  const draftBox = $('[data-mc-input]');
  $('[data-mc-send]').disabled = sendingNote && !live.off;
  const btn = $('[data-mc-record]');
  btn.setAttribute('aria-pressed', String(live.off));
  if (document.body.dataset.mcPrivate === 'false' || btn.hasAttribute('data-private-off')) {
    btn.setAttribute('aria-label', 'Private is not available');
    btn.title = privateOffWhy();
  } else {
    btn.setAttribute('aria-label', live.off ? 'Exit Private' : 'Go Private');
    btn.title = live.off ? 'Exit Private: go back on the record' : mcSay('Go Private: Master Craftsman answers, MiniMoi keeps nothing');
  }
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
  if (draftBox) checkLength(draftBox); // after clearing old messages: restored and mode-specific limits must stay visible
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

// The small "..." options menu (18 Oct). Escape or a click elsewhere closes it, like the other menus; its Escape stops here, so it
// never reaches the panel's Escape (which minimizes the panel, and minimizing ends voice). While Dictate is listening the menu stays
// open, even against its own summary, so Stop dictation can always be reached. Other pressed controls (the Private lock) do not hold it.
function wireOptionsMenu(options, doc) {
  if (!options) return;
  const summary = options.querySelector('summary');
  const dictating = () => Boolean(options.querySelector('[data-dictate][aria-pressed="true"]'));
  options.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !options.open) return;
    e.stopPropagation(); e.preventDefault();
    if (!dictating()) options.open = false;
    if (summary) summary.focus();
  });
  if (summary) summary.addEventListener('click', (e) => {
    if (options.open && dictating()) e.preventDefault();      // the native toggle would hide Stop dictation
  });
  options.addEventListener('toggle', () => {
    if (!options.open && dictating()) options.open = true;    // any other route that closes it while listening
  });
  doc.addEventListener('click', (e) => {
    if (options.open && !options.contains(e.target) && !dictating()) options.open = false;
  });
}

export function initConversation(p) {
  page = p;
  panel = $('[data-mc-panel]'); thread = $('[data-mc-thread]'); pill = $('[data-mc-pill]');
  decorateMessages();
  new MutationObserver(decorateMessages).observe(thread, { childList: true, subtree: true });
  // On The Work, Board and Build Log the pill is the ask bar and opens as a sheet over the work; elsewhere it opens the side panel.
  const asksInPlace = ['board', 'board_popout', 'build_log'].includes(document.body.dataset.page);
  pill.addEventListener('click', () => { setMode(asksInPlace ? 'floating' : 'docked'); const i = $('[data-mc-input]'); if (i) i.focus(); });
  $('[data-mc-min]').addEventListener('click', () => { setMode('pill'); pill.focus(); });
  initMove();
  $('[data-mc-dock]').addEventListener('click', () => setMode(mode === 'docked' ? 'floating' : 'docked'));
  panel.addEventListener('keydown', (e) => {
    if (!inPage() && e.key === 'Escape' && mode === 'floating') { setMode('pill'); pill.focus(); }
  });
  const requestedMode = new URLSearchParams(window.location.search).get('chat');
  if (!inPage() && ['open', 'floating'].includes(requestedMode)) setMode(requestedMode === 'floating' ? 'floating' : 'docked');
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
  input.addEventListener('input', () => { noteKey = null; checkLength(input); keepDraft(input); });
  restoreDraft(input);
  const confirm = $('[data-mc-record-confirm]');
  if (confirm) {
    confirm.addEventListener('click', () => {
      setOff(false);
      addPlatform('Guild platform', 'On the record in this tab');
    });
  }
  const toggleRecord = () => {
    const wasOff = live.off;
    setOff(!live.off);
    if (wasOff) {
      endPrivateSitting();
      for (const n of $$('[data-off-record-line]')) n.remove();
      addPlatform('Guild platform', 'Back on the record · nothing from the off-the-record stretch was kept');
    }
  };
  $('[data-mc-record]').addEventListener('click', toggleRecord);
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
  // Exit always works, even where the lock cannot be chosen (a tab still in Private when Private became unavailable):
  // it only ever goes back on the record, by the owner's own click, never by itself.
  if (exit) exit.addEventListener('click', () => { if (live.off) toggleRecord(); });
  wireOptionsMenu($('[data-mc-options]'), document);
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
  fit();
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
  if (page.mc_queue) {
    // After reload the original stream belongs to the old page. Read saved
    // messages while its ledger entry runs; never redispatch that turn.
    const conversationId = page.conversation?.id;
    const chatScope = page.chat_scope;
    const ids = Array.from(thread.querySelectorAll('[data-note]'), (n) => Number(n.dataset.note)).filter(Number.isSafeInteger);
    const recovery = platformNotes({
      after: ids.length ? Math.max(0, Math.min(...ids) - 1) : 0,
      kinds: ['platform', 'agent'],
      current: () => live.known && !live.off && !document.hidden
        && page.conversation?.id === conversationId && page.chat_scope === chatScope
        && !thread.querySelector('[data-live-turn]'),
      fetchPage: async (before) => {
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 5000);
        try {
          const conv = conversationId ? `&conversation=${encodeURIComponent(conversationId)}` : '';
          return notesRead(conversationId, await apiGet(`/notes?limit=200${conv}${before ? `&before=${before}` : ''}`, undefined, controller.signal), before);
        } finally { window.clearTimeout(timeout); }
      },
      insert: (note) => {
        if (thread.querySelector(`[data-note="${note.id}"]`)) return;
        const next = Array.from(thread.querySelectorAll('[data-note]')).find((n) => Number(n.dataset.note) > note.id);
        thread.insertBefore(noteLine(note), next || null);
        if (note.author_kind === 'agent') {
          applyHeader({ mc_header: mcSay('Master Craftsman answered · saved reply recovered') });
          answerArrived();
        }
        follow();
      },
    });
    queue = initQueue({ page, thread, dispatch: dispatchQueued, follow,
      readBack: () => thread.querySelector('[data-live-turn]') ? Promise.resolve(true) : recovery.refresh() });
  }
  agentUi = initAgentRequests({ page, thread, noteLine, follow });
}
