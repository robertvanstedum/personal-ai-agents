// Talking with Master Craftsman on the direct voice path (Spec 164 v0.3 §3–§9; UI_CONTRACT_chunk_B.md). mc_voice.js loads this
// file only when the capabilities answer carries direct_voice; without it the file is never asked for, the page does not list
// it, and the server does not serve it.
//
// The provider speaks directly, as in German: the shared RealtimeVoiceController in direct mode. This file bridges the model's
// two functions (consult, save_note) to MC's own typed turn on the server, posts every turn as it completes (and a heartbeat),
// and ends with End. While the session is live nothing that is said is shown: the live state is voice on, the time so far,
// End and Check with MC. End reveals the session's kept records in the thread. A refusal says "Voice unavailable: <reason>";
// nothing else is tried, and the chained relay is never a fallback.
//
// No imports: mc_voice.js hands in the page, the fetch, the controller's loader and the thread, so the node tests drive this
// file with a fake controller, a fake fetch and a small fake DOM.

export const NO_CONVERSATION = 'Open a saved conversation before using voice.';
export const ENDED = 'Voice ended. What was said is in the conversation.';
export const NOT_DONE = 'That did not go through. Nothing is claimed.';
export const LIVE_ELSEWHERE = 'Voice session in progress; what is said appears when it ends.';
export const RECOVERING = 'Voice session ended unexpectedly; recovering what was said.';
export const CONFIRM_SCOPE = 'Confirming confirms the summary only. Anything it mentions still needs its own request.';
export const SUMMARY_UNAVAILABLE = 'Summary unavailable.';
export const NOT_KEPT = 'Some of what was said could not be kept.';
export const CAP = 'This voice session has kept all it may.';
export const EDIT_MAX = 1860;
const HOLDING_MS = 10000;                 // Chunk A's holding line: one "Still checking." when a consult is slow
const SPEAK_SUMMARY_MS = 45000;           // the spoken summary ends the session at its next turn, or after this
const START_END_MS = 15000;               // End pressed while still connecting waits for the connection at most this long
const POST_EVENTS = 20, POST_BYTES = 60000;   // the server takes 20 events and 64 KB a post; JSON overhead stays inside
const TEXT_BYTES = 16000;

// §3: what the bootstrap's refusal means, in the words the contract gives. `detail` is the body's `error` (a code, or words).
const START_WORDS = {
  not_allowed: "Voice unavailable: this needs the owner's sign-in.",
  csrf: 'Voice unavailable: this page is out of date. Reload it.',
  not_listening: 'Voice is on the record only. Go back on the record to use it.',
  invalid: 'Voice unavailable: open a saved conversation, on the record.',
  mc_turns_off: "Voice unavailable: Master Craftsman's turns are off here.",
  not_found: 'Voice unavailable: that conversation is not available.',
  unavailable: 'Voice unavailable here right now.',
};
export function startRefusal(info) {
  const reason = info?.reason, status = Number(info?.status) || 0;
  const detail = typeof info?.detail === 'string' ? info.detail.trim().slice(0, 240) : '';
  if (reason === 'bootstrap_network_error') return 'Voice unavailable: the server did not answer. Typing still works.';
  if (reason === 'connection_failed') return 'Voice could not start: the voice provider did not connect. Typing still works.';
  if (reason === 'microphone_denied' || reason === 'microphone_unavailable') return 'Voice unavailable: the microphone is not available to this page. Typing still works.';
  if (reason !== 'bootstrap_rejected') return 'Voice stopped: the voice connection ended. What was said is kept; typing still works.';
  if (status === 401) return 'Voice unavailable: sign in again.';
  if (Object.hasOwn(START_WORDS, detail)) return START_WORDS[detail];
  if (status === 400) return 'Voice unavailable: this voice is not set up correctly.';
  if (status === 429 && /^rate limited$/i.test(detail)) return 'Too many voice starts in a minute. Wait a moment.';
  if ((status === 429 || status === 503) && detail) return detail;          // the budget's own words, as given
  if (status === 502) return 'Voice could not start: the voice provider refused. Typing still works.';
  if (status === 503) return START_WORDS.unavailable;
  return 'Voice unavailable: the server did not start voice. Typing still works.';
}

// The bootstrap's voice notices ("openai voice is unset, using marin") as one quiet line for the chosen provider.
export function noticeWords(notices, provider) {
  const mine = (Array.isArray(notices) ? notices : []).filter((n) => typeof n === 'string' && n.startsWith(`${provider} `));
  return mine.map((n) => {
    const unset = /voice is unset, using (\S+)$/.exec(n);
    return unset ? `Default voice (${unset[1]}); not yet chosen.` : n;
  }).join(' ');
}

// A same-origin JSON POST with the page's headers (§2). Never throws: {ok, status, body}; status 0 when the server did not answer.
export function jsonPost(fetchFn, headers) {
  return async (target, body, opts = {}) => {
    try {
      const r = await fetchFn(target, { method: 'POST', credentials: 'same-origin', headers: headers(), body: JSON.stringify(body),
        ...(opts.keepalive ? { keepalive: true } : {}) });
      const json = await r.json().catch(() => ({}));
      return { ok: r.ok, status: r.status, body: json && typeof json === 'object' ? json : {} };
    } catch (e) {
      return { ok: false, status: 0, body: {} };
    }
  };
}

const clockWords = (s) => (s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`);
const bytes = (text) => new TextEncoder().encode(text).length;
function fit(text) {                                   // at most 16,000 bytes; a cut turn is posted as partial
  if (bytes(text) <= TEXT_BYTES) return [text, false];
  let cut = text.slice(0, TEXT_BYTES);
  while (bytes(cut) > TEXT_BYTES) cut = cut.slice(0, -1);
  return [cut, true];
}

// §5, durable capture. Every item the controller reports is keyed by its place ("t" + index, stable for the session), posted
// once when it completes; an assistant item still incomplete when a later user item appears was interrupted; at the end what is
// still incomplete is posted as partial. Posts go one at a time, at most 20 events and 64 KB each; a retried post keeps the
// same ids, so the server keeps each turn once.
export function createCapture({ send, clock = Date.now, onAnswer = () => {}, warn = () => {} }) {
  const items = [];
  let chain = Promise.resolve(), stopped = false;
  const iso = (ms) => new Date(ms).toISOString();

  function observe(turns) {
    const now = clock();
    (Array.isArray(turns) ? turns : []).forEach((t, i) => {
      const it = items[i] || (items[i] = { speaker: t.speaker, text: '', completed: false, started: now, ended: null, posted: false });
      it.speaker = t.speaker === 'user' ? 'user' : 'assistant';
      it.text = typeof t.text === 'string' ? t.text : '';
      if (t.completed && !it.completed) { it.completed = true; it.ended = now; }
    });
  }
  function event(i, it, partial, interrupted) {
    it.posted = true;
    const [text, cut] = fit(it.text.trim());
    const end = Math.max(it.ended ?? clock(), it.started);
    return { role: it.speaker, item_id: `t${i}`, index: 0, text, partial: partial || cut, interrupted: it.speaker === 'assistant' && interrupted,
      started_at: iso(it.started), ended_at: iso(end) };
  }
  function due(final) {
    const out = [];
    items.forEach((it, i) => {
      if (!it || it.posted) return;
      if (!it.text.trim()) { if (it.completed || final) it.posted = true; return; }   // nothing was said: nothing to keep
      if (it.completed) out.push(event(i, it, false, false));
      else if (it.speaker === 'assistant' && items.slice(i + 1).some((n) => n && n.speaker === 'user')) out.push(event(i, it, true, true));
      else if (final) out.push(event(i, it, true, it.speaker === 'assistant'));
    });
    return out;
  }
  function batches(events) {
    const out = [];
    let cur = [], size = 16;
    for (const e of events) {
      const n = bytes(JSON.stringify(e)) + 1;
      if (cur.length && (cur.length >= POST_EVENTS || size + n > POST_BYTES)) { out.push(cur); cur = []; size = 16; }
      cur.push(e); size += n;
    }
    if (cur.length) out.push(cur);
    return out;
  }
  async function post(batch, retried, opts) {
    if (stopped || !batch.length) return;
    const r = await send(batch, opts);
    const code = r.body && r.body.error;
    if (r.status === 200) return onAnswer(r.body || {});
    if (r.status === 413 && batch.length > 1) {                       // split the post
      const half = Math.ceil(batch.length / 2);
      await post(batch.slice(0, half), retried, opts);
      return post(batch.slice(half), retried, opts);
    }
    if (r.status === 409 && code === 'not_kept' && !retried) return post(batch.slice(Math.max(0, Number(r.body.kept) || 0)), true, opts);
    if (r.status === 0 && !retried) return post(batch, true, opts);       // the network: once more, the same ids
    if (r.status === 422) { warn('voice transcript refused', code); return; }   // a page bug or a far-off clock: not retried
    if (r.status === 409 && code === 'not_kept') return onAnswer({ failed: 'not_kept' });
    stopped = ['session_ended', 'not_listening', 'cap', 'not_your_session', 'csrf'].includes(code) || r.status === 403 || stopped;
    return onAnswer({ failed: code || (r.status ? 'unavailable' : 'network'), status: r.status });
  }
  function enqueue(events, opts) {
    for (const batch of batches(events)) chain = chain.then(() => post(batch, false, opts)).catch((e) => warn('voice transcript post failed', e));
    return chain;
  }
  return {
    update(turns) { observe(turns); return enqueue(due(false)); },
    finish(turns, opts) { observe(turns); return enqueue(due(true), opts); },
    heartbeat() {
      if (stopped) return chain;
      chain = chain.then(async () => {
        if (stopped) return;
        const r = await send([]);
        if (r.status === 409 || r.status === 403) { stopped = true; onAnswer({ failed: (r.body && r.body.error) || 'session_ended', status: r.status }); }
      }).catch((e) => warn('voice heartbeat failed', e));
      return chain;
    },
    stop() { stopped = true; },
    get stopped() { return stopped; },
    settled() { return chain; },
  };
}

// One direct voice session. `ui` is mc_voice.js's: say(text, {error, loud, transient}), phase(state), live(on), elapsed(text),
// notice(text), propose(on), ended(words, {error}) (the session ended by itself; mc_voice.js resets its button).
export function directVoice(d) {
  const { page, direct, ui } = d;
  const clock = d.clock || Date.now;
  const later = d.setTimeout || ((fn, ms) => setTimeout(fn, ms));
  const cancel = d.clearTimeout || ((t) => clearTimeout(t));
  const every = d.setInterval || ((fn, ms) => setInterval(fn, ms));
  const stopEvery = d.clearInterval || ((t) => clearInterval(t));
  let c = null, sid = null, cid = null, provider = null, lastOwner = '', capture = null;
  let connected = false, finalized = false, failed = false, proposing = false, keepalive = false;
  let ticker = null, beat = null, started = 0, summary = null, speakTimer = null, endAfterTurn = null, ending = null, endedWords = null;
  let held = null, heldTimer = null, closed;
  const done = new Promise((resolve) => { closed = resolve; });     // settles once End is sent and the thread is reloaded

  const at = (template) => String(template).split('{session_id}').join(encodeURIComponent(sid || ''));
  const postJson = jsonPost(d.fetch, d.headers);
  const sessionId = () => sid || (c && typeof c._sessionId === 'string' ? c._sessionId : null);
  const speak = (instruction) => { try { c?._adapter?.sendContinuationInstruction(instruction); return true; } catch (e) { return false; } };
  function stopTimers() {
    if (ticker) stopEvery(ticker);
    if (beat) stopEvery(beat);
    if (speakTimer) cancel(speakTimer);
    if (heldTimer) cancel(heldTimer);
    ticker = beat = speakTimer = heldTimer = null;
  }
  function endNow(reason) {
    held = null;
    if (!finalized && c && typeof c.endSession === 'function') c.endSession(reason);
    if (!finalized) finalize({ turns: [] });
  }
  function propose(on) { proposing = on; ui.propose(on); }

  // §5's answers: Sum up is offered, a spoken yes asks for the summary, a refusal stops posting.
  function heard(body) {
    if (finalized && !body.failed) return;
    if (body.propose_end) propose(true);
    if (body.end_confirmed) sumUp();
    if (body.failed === 'not_kept') ui.say(NOT_KEPT, { error: true });
    else if (body.failed === 'cap') ui.say(CAP, { error: true });
    else if (body.failed === 'not_listening' && !finalized) c?.endSession('not_listening');
  }

  // §4: the function bridge. The server acts on Robert's own last words, never on the model's arguments; whatever comes back
  // (any status) is the function's output, which the model is told to speak.
  async function consult(callId, name, args) {
    const holding = later(() => speak('Still checking.'), HOLDING_MS);
    let advisory = args;
    if (typeof advisory === 'string') { try { advisory = JSON.parse(advisory); } catch (e) { advisory = {}; } }
    if (!advisory || typeof advisory !== 'object' || Array.isArray(advisory)) advisory = {};
    try {
      const r = await postJson(direct.consult_url, { session_id: sessionId(), call_id: String(callId), name, owner_transcript_item: lastOwner,
        arguments: advisory });
      if (r.status === 0) return { ok: false, error: 'network', spoken: NOT_DONE };
      return Object.keys(r.body).length ? r.body : { ok: false, error: 'unavailable', spoken: NOT_DONE };   // an unreadable answer: not done
    } finally { cancel(holding); }
  }

  // Check with MC: the host asks for Robert's last words, then has the voice say the answer, adding nothing.
  async function check() {
    if (!connected || finalized) return;
    if (!lastOwner) { ui.say('Say something first: Check with MC sends your last words.', { transient: true }); return; }
    ui.phase('working');
    const body = await consult(`host-${clock()}`, 'consult', {});
    if (finalized) return;
    const spoken = body && typeof body.spoken === 'string' && body.spoken.trim() ? body.spoken.trim() : NOT_DONE;
    speak(`Say this briefly, adding nothing: ${spoken}`);
    ui.phase('listening');
  }

  // §7: the summary. The route ends capture and asks MC's own model path; the voice says the spoken part, then the session ends.
  function sumUp() {
    if (summary || finalized || !sessionId()) return summary;
    propose(false);
    ui.say('Summing up…');
    summary = (async () => {
      const r = await postJson(at(direct.summary_url), { conversation_id: cid });
      if (finalized) return;
      const body = r.body || {};
      if (r.status === 200 && body.ok && body.state === 'proposed' && typeof body.spoken === 'string' && body.spoken.trim() && connected
          && speak(`Say only this, then stop: ${body.spoken.trim()}`)) {
        capture?.stop();                                     // the server has ended capture
        endAfterTurn = () => { endAfterTurn = null; c?.endSession('normal_end'); };
        speakTimer = later(() => endAfterTurn?.(), SPEAK_SUMMARY_MS);
        return;
      }
      if (r.status === 200 && body.state === 'unavailable') endedWords = [SUMMARY_UNAVAILABLE, false];
      else if (r.status !== 200) endedWords = [body.message || 'The summary could not be asked for. What was said is kept.', true];
      capture?.stop();
      c?.endSession('normal_end');
    })();
    return summary;
  }

  // §6: End on the server (after the last turns are posted), then the reveal. Once, whatever ended the session.
  function finalize(result) {
    if (finalized) return ending;
    finalized = true; connected = false;
    stopTimers(); propose(false); ui.live(false);
    ending = (async () => {
      const id = sessionId();
      if (id) {
        await capture?.finish(result?.turns || [], { keepalive });
        const r = await postJson(at(direct.end_url), {}, { keepalive });
        if (keepalive) return;                               // the page is going away: nothing more to show
        if (!(r.status === 200 && r.body.ok) && !failed) endedWords = ['Voice ended here, but the server did not confirm it. What was said appears when the session\'s time runs out.', true];
        await d.reveal?.();
      }
      const [words, error] = endedWords || [failed || !id ? null : ENDED, false];
      ui.ended(words, { error });
    })();
    ending.then(closed, closed);
    return ending;
  }

  async function start(chosen) {
    cid = page.conversation?.id || null;
    provider = chosen || direct.default_provider;
    if (!cid) { failed = true; ui.ended(NO_CONVERSATION, { error: true }); return false; }
    const { RealtimeVoiceController } = await d.loadController();
    if (finalized) return false;
    capture = createCapture({ send: (events, opts) => postJson(at(direct.transcript_url), { events }, opts || {}), clock, onAnswer: heard,
      warn: d.warn || (() => {}) });
    c = new RealtimeVoiceController({
      bootstrapUrl: direct.bootstrap_url, requestHeaders: d.headers, includeTranscriptAfterEnd: true,
      openingInstruction: null,                   // the server composes the session's instructions; the page sends none
      onSessionInfo: (info) => {
        if (typeof info?.voice_session_id === 'string') sid = info.voice_session_id;
        // Ended while the server was still starting it: that session is ended on the server too, never left open.
        if (finalized) { if (sid) postJson(at(direct.end_url), {}); return; }
        ui.notice(noticeWords(info?.voice_notices, info?.provider || provider));
      },
      onStateChange: (state) => {
        if (state === 'active' && finalized) { c.endSession('user_ended'); return; }
        if (state === 'active' && held) { endNow(held); return; }
        if (state === 'connecting') ui.phase('connecting');
        if (state === 'active' && !finalized) {
          connected = true; started = clock();
          ui.live(true); ui.elapsed(clockWords(0)); ui.phase('listening'); ui.say('');
          ticker = every(() => ui.elapsed(clockWords(Math.floor((clock() - started) / 1000))), 1000);
          beat = every(() => capture.heartbeat(), Math.max(5, Number(direct.heartbeat_seconds) || 20) * 1000);
        }
        if (state === 'reconnecting' && !finalized) c.endSession('disconnect');     // no silent reconnect: end visibly
      },
      onInputState: (phase) => {
        if (!connected || finalized) return;
        ui.phase(phase === 'speaking' ? 'speaking' : ['consulting', 'understood', 'speech_stopped'].includes(phase) ? 'working' : 'listening');
      },
      onWarning: () => { if (!finalized) ui.say('Voice will stop in about two minutes.', { loud: true }); },
      onStop: () => { endedWords = ['Voice stopped: the session reached its time limit.', false]; },
      onFatalError: (info) => {
        failed = true;
        ui.say(startRefusal(info), { loud: true, error: true });
        if (info?.reason === 'bootstrap_rejected' || info?.reason === 'bootstrap_network_error') finalize({ turns: [] });
      },
      onPlayback: () => {},
      onTranscriptUpdate: (turns) => { if (!finalized) capture.update(turns); },
      onUserTurn: (text) => { if (typeof text === 'string' && text.trim()) lastOwner = text.trim(); },
      onAssistantTurn: () => { if (endAfterTurn) endAfterTurn(); },
      onFunctionCall: (call) => consult(call.call_id, call.name, call.arguments),
      onFinalize: (result) => finalize(result),
    });
    await c.startSession({ provider, persona: direct.persona, scene: '', conversation_id: cid });
    return true;
  }

  return {
    start,
    check,
    sumUp,
    keepTalking() { propose(false); },
    proposing: () => proposing,
    sessionId,
    // Ends the session for any reason (End, the microphone handed over, Private, the page going away). Resolves once End is sent.
    // While the provider is still connecting, the end waits for the connection (at most START_END_MS): the shared adapters take
    // the microphone while they connect, and ending the transport before that could leave it open.
    end(reason) {
      keepalive = reason === 'pagehide';
      if (finalized) return done;
      const starting = c && !connected && ['requesting_mic', 'connecting'].includes(c._state);
      if (starting && !keepalive) {
        if (!held) { held = reason; heldTimer = later(() => endNow(reason), START_END_MS); }
        return done;
      }
      endNow(reason);
      return done;
    },
  };
}

// The thread side of §6 and §7: one quiet line where a recovering or elsewhere-live session started, and the summary card
// (Confirm, Edit, Decline) on a waiting proposal, from any device, however much later.
export function voiceThread(d) {
  const { doc, direct } = d;
  const proposals = new Map();                       // session -> the proposal note's text, for Edit
  const make = (tag, attrs = {}, text) => {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text != null) node.textContent = text;
    return node;
  };
  const whenOf = (row) => Date.parse(row.querySelector?.('[data-iso]')?.getAttribute('data-iso') || row.getAttribute('data-at') || '');

  function place(li, startedAt) {
    const thread = d.thread();
    if (!thread) return;
    const at = Date.parse(startedAt || '');
    const next = Number.isFinite(at) ? Array.from(thread.querySelectorAll('[data-note]')).find((row) => !row.hidden && whenOf(row) > at) : null;
    thread.insertBefore(li, next || thread.querySelector('[data-live-turn]') || null);
  }
  function line(sid, text, startedAt) {
    const thread = d.thread();
    let li = thread?.querySelector(`[data-voice-line="${sid}"]`);
    if (!text) { li?.remove(); return; }
    if (!li) { li = make('li', { class: 'msg msg-voice-line', 'data-voice-line': sid }); place(li, startedAt); }
    li.textContent = text;
  }

  function card(sid) {
    const row = d.thread()?.querySelector(`[data-voice-kind="summary_proposal"][data-voice-session="${sid}"]`);
    if (!row || row.querySelector('[data-voice-summary-card]')) return;
    const box = make('div', { class: 'mc-voice-summary', 'data-voice-summary-card': '', role: 'group', 'aria-label': 'Voice summary proposal' });
    const actions = make('p', { class: 'mc-voice-summary-actions' });
    const buttons = ['Confirm', 'Edit', 'Decline'].map((label) => make('button', { type: 'button', class: 'btn-mini', [`data-voice-summary-${label.toLowerCase()}`]: '' }, label));
    actions.append(...buttons);
    const scope = make('p', { class: 'small mc-voice-summary-note' }, CONFIRM_SCOPE);
    const result = make('p', { class: 'small', role: 'status', 'data-voice-summary-result': '' });
    result.hidden = true;
    box.append(actions, scope, result);
    row.append(box);
    const [confirm, edit, decline] = buttons;
    const busy = (on) => { for (const b of buttons) b.disabled = on; };
    const tell = (text) => { result.textContent = text; result.hidden = !text; };

    async function decide(decision, text) {
      busy(true); tell('');
      const r = await d.post(direct.summary_decision_url.split('{session_id}').join(encodeURIComponent(sid)),
        { conversation_id: d.conversationId(), decision, ...(decision === 'edit' ? { text } : {}) });
      const body = r.body || {};
      if (r.status === 200 && body.ok) {
        actions.remove();
        const editor = box.querySelector('[data-voice-summary-editor]');
        editor?.remove();
        tell({ confirmed: 'Confirmed. It confirms the summary only; it authorizes nothing it mentions.',
          edited: 'Your wording replaces the proposal. It confirms the summary only; it authorizes nothing it mentions.',
          declined: 'Declined. The proposal is not your record of the session.' }[body.state] || 'Kept.');
        await d.reveal?.();
        return true;
      }
      if (r.status === 409 && body.error === 'already_decided') {
        actions.remove();
        tell(`Already decided${body.state ? ` (${body.state})` : ''}. Nothing was changed.`);
        await d.reveal?.();
        return false;
      }
      busy(false);
      tell(body.message || (r.status === 0 ? 'The server did not answer. Nothing was changed.' : 'That was not kept. Nothing was changed.'));
      return false;
    }

    confirm.addEventListener('click', () => decide('confirm'));
    decline.addEventListener('click', () => decide('decline'));
    edit.addEventListener('click', () => {
      if (box.querySelector('[data-voice-summary-editor]')) return;
      const form = make('div', { class: 'mc-voice-summary-edit', 'data-voice-summary-editor': '' });
      const area = make('textarea', { class: 'mc-input', rows: '5', maxlength: String(EDIT_MAX), 'aria-label': 'Your wording of the summary' });
      const written = proposals.get(sid) || '';
      area.value = written.includes('\n\n') ? written.slice(written.indexOf('\n\n') + 2) : written;
      const save = make('button', { type: 'button', class: 'btn-mini', 'data-voice-summary-save': '' }, 'Save');
      const back = make('button', { type: 'button', class: 'btn-mini', 'data-voice-summary-cancel': '' }, 'Cancel');
      const row2 = make('p', { class: 'mc-voice-summary-actions' });
      row2.append(save, back);
      form.append(area, row2);
      box.insertBefore(form, scope);
      actions.hidden = true;
      save.addEventListener('click', async () => {
        const words = String(area.value || '').trim();
        if (!words) { tell('Write the summary, or Cancel.'); return; }
        if (words.length > EDIT_MAX) { tell(`At most ${EDIT_MAX} characters.`); return; }
        save.disabled = true;
        if (!(await decide('edit', words))) { save.disabled = false; actions.hidden = Boolean(box.querySelector('[data-voice-summary-editor]')); }
      });
      back.addEventListener('click', () => { form.remove(); actions.hidden = false; tell(''); });
      area.focus?.();
    });
  }

  return {
    // Paint the sessions the notes answer named: `notes` (optional) supplies the proposal's words for Edit.
    paint(sessions, notes) {
      for (const n of Array.isArray(notes) ? notes : []) {
        if (n && n.voice && n.voice.kind === 'summary_proposal' && typeof n.text === 'string') proposals.set(n.voice.session_id, n.text);
      }
      for (const s of Array.isArray(sessions) ? sessions : []) {
        if (!s || typeof s.session_id !== 'string') continue;
        const text = s.state === 'live' ? (s.session_id === d.mine() ? null : LIVE_ELSEWHERE)
          : ['recovering', 'unknown'].includes(s.state) ? (s.line || RECOVERING) : null;
        line(s.session_id, text, s.started_at);
        if (s.summary === 'proposed') card(s.session_id);
      }
    },
  };
}
