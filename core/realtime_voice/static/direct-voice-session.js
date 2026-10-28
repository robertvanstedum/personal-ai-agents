// One browser session module for agent voice on the direct path (Spec 165 §4.3; Spec 164 v0.3 §3–§9; UI_CONTRACT_chunk_B.md),
// shared by every host page (MC on the Guild today). A page loads it only when the capabilities answer offers the direct
// path (direct_voice); German and Portuguese never load it.
//
// The provider speaks directly, as in German: the shared RealtimeVoiceController in direct mode. This module bridges the
// model's two functions (consult, save_note) to the person's own typed turn on the server, posts every turn as it completes
// (and a heartbeat), and ends with End. While the session is live nothing that is said is shown: the live state is voice on,
// the time so far, End and Check with <person>. End reveals the session's kept records (the host page renders them). A
// refusal says "Voice unavailable: <reason>"; nothing else is tried, and no other voice path is a fallback.
//
// Words that name the person take direct_voice.person ({name, short}) from the server: "Check with MC", "Master Craftsman's
// turns are off here" (the refusal code <short>_turns_off). The controller is read only through its public methods
// (sessionId(), state(), speak(instruction) and the playback ones), never a private field (tests/realtime_voice/test_no_copy.py).
//
// No imports: the host page hands in the page, the fetch, the controller's loader and its ui, so the node tests drive this
// file with a fake controller, a fake fetch and fake timers.

// The host's own words come with direct_voice.words (MC: "a saved conversation", "on the record"); these are the defaults.
export const NO_CONVERSATION = 'Open a conversation before using voice.';
export const ENDED = 'Voice ended. What was said is in the conversation.';
export const NOT_DONE = 'That did not go through. Nothing was done.';
export const SUMMARY_UNAVAILABLE = 'Summary unavailable.';
export const NOT_KEPT = 'Some of what was said could not be kept.';
export const CAP = 'This voice session has kept all it may.';
// The server refused the session's posts (UI review #2): the voice is ended here too, so nothing goes on being said that
// cannot be kept. `cap` alone only stops posting (CAP above).
export const SERVER_ENDED = {
  session_ended: 'Voice stopped: the server ended this voice session. What was said before is kept.',
  csrf: 'Voice stopped: this page is out of date. Reload it. What was said before is kept.',
  refused: 'Voice stopped: the server refused this voice session. What was said before is kept.',
};
export const ALREADY_CHECKED = 'Already checked; say it again to re-ask.';
// Some of what was said is not known to be kept (review B4): said instead of ENDED, never a claim that all of it was.
// Spec 165 §5.2's words, with the count when it is known (the server's, from End's answer, else the page's own).
export const INCOMPLETE = 'Voice ended. Some of what was said was not kept. What was kept is in the conversation.';
export function incompleteWords(n) {
  return Number.isInteger(n) && n > 0
    ? `Voice ended. Some of what was said was not kept (${n} turn${n === 1 ? '' : 's'}). What was kept is in the conversation.`
    : INCOMPLETE;
}
// A consult's four outcomes (Spec 165 §5.1): done (its reply), not_done (NOT_DONE or the refusal), pending, unknown. Unknown is
// a consult whose answer did not come back (review B7): never "failed", which would invite asking again.
export const UNKNOWN = "I didn't get the result back. Check it in writing before asking again.";
export const PENDING = "That's taking longer; the answer will be in the conversation.";
// The summary's words, each true for what the server is doing (review R1 P3-7). The server's own call is bounded under 45 s
// (core/realtime_voice/summary_gateway.py), so after the page's bound nothing is still being written: unavailable. An answer
// lost on the way back (no answer, or a proxy's 502/504) may still have kept a proposal: it may still appear.
export const SUMMARY_MAY_APPEAR = 'The summary may still appear in the conversation.';
export const SUMMARY_REFUSED = 'The summary could not be asked for.';
export const KEEP_TALKING_FAILED = 'Voice ended: Keep talking did not reach the server, so a later yes could have ended the session.';
// A model call that came before its own words were transcribed, and they did not come in time (review R1 P1-2): nothing is
// sent, so nothing is done on older words.
export const NOT_HEARD = "I didn't catch that; say it again.";
const HEAR_WAIT_MS = 3000;                // a model call waits this long for the transcript of the owner speech it answers
const HOLDING_MS = 10000;                 // Chunk A's holding line: one "Still checking." when a consult is slow
const QUIET_WAIT_MS = 8000;               // a line for the voice waits this long for a pause, then is sent (the pause was missed)
const SUMMARY_WAIT_MS = 45000;            // the summary's answer is awaited this long (Spec 165 §5.3); then it may still appear
const FLUSH_WAIT_MS = 5000;               // the barrier waits this long for the last posts' answers; after it, unanswered is not kept
const END_WAIT_MS = 10000;                // End's answer is awaited this long; past it the page says the server did not confirm
const REVEAL_WAIT_MS = 10000;             // the thread's reload is awaited this long before the session's end is shown
const DRAIN_CAP_MS = 90000;               // the spoken summary plays at most 15 characters a second plus 5 s, and never past this
const START_END_MS = 15000;               // End pressed while still connecting waits for the connection at most this long
const POST_EVENTS = 20, POST_BYTES = 60000;   // the server takes 20 events and 64 KB a post; JSON overhead stays inside
const CLOSE_BYTES = 64 * 1024 - 1024;     // the leaving page's one close envelope (End with its events and report), whole
const TEXT_BYTES = 16000;

// §3: what the bootstrap's refusal means, in the words the contract gives. `detail` is the body's `error` (a code, or words).
const START_WORDS = {
  not_allowed: "Voice unavailable: this needs the owner's sign-in.",
  csrf: 'Voice unavailable: this page is out of date. Reload it.',
  not_listening: 'Voice is not available in this mode.',
  invalid: 'Voice unavailable: open a conversation first.',
  not_found: 'Voice unavailable: that conversation is not available.',
  unavailable: 'Voice unavailable here right now.',
};
// The words that name the person ({name, short}, from direct_voice.person). The host's own "turns are off" refusal is
// <short>_turns_off (MC: mc_turns_off); without a person there is none.
export function personWords(person, host = {}) {
  const name = typeof person?.name === 'string' && person.name ? person.name : 'the agent';
  const short = typeof person?.short === 'string' && person.short ? person.short : name;
  const off = typeof person?.short === 'string' && person.short ? `${person.short.toLowerCase()}_turns_off` : null;
  const own = (key) => (typeof host?.[key] === 'string' && host[key].trim() ? { [key]: host[key].trim() } : {});
  const start = { ...START_WORDS, ...own('not_listening'), ...own('invalid') };
  return {
    off,
    noConversation: own('no_conversation').no_conversation || NO_CONVERSATION,
    start: off ? { ...start, [off]: `Voice unavailable: ${name}'s turns are off here.` } : start,
    serverEnded: off ? { ...SERVER_ENDED, [off]: `Voice stopped: ${name}'s turns are off here. What was said before is kept.` } : SERVER_ENDED,
    sayFirst: `Say something first: Check with ${short} sends your last words.`,
  };
}
export function startRefusal(info, person, host) {
  const words = personWords(person, host).start;
  const reason = info?.reason, status = Number(info?.status) || 0;
  const detail = typeof info?.detail === 'string' ? info.detail.trim().slice(0, 240) : '';
  if (reason === 'bootstrap_network_error') return 'Voice unavailable: the server did not answer. Typing still works.';
  if (reason === 'connection_failed') return 'Voice could not start: the voice provider did not connect. Typing still works.';
  if (reason === 'microphone_denied' || reason === 'microphone_unavailable') return 'Voice unavailable: the microphone is not available to this page. Typing still works.';
  if (reason !== 'bootstrap_rejected') return 'Voice stopped: the voice connection ended. What was said is kept; typing still works.';
  if (status === 401) return 'Voice unavailable: sign in again.';
  if (Object.hasOwn(words, detail)) return words[detail];
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

// A same-origin JSON GET with the page's headers (a reconciliation read). Never throws: {ok, status, body}.
export function jsonGet(fetchFn, headers) {
  return async (target) => {
    try {
      const r = await fetchFn(target, { method: 'GET', credentials: 'same-origin', headers: headers() });
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

// How long the spoken summary may play (Spec 165 §5.3): 15 characters a second plus 5 s, never more than DRAIN_CAP_MS.
export const summaryBound = (said) => Math.min(DRAIN_CAP_MS, Math.ceil(String(said || '').length / 15) * 1000 + 5000);

// §5, durable capture. Every item the controller reports is keyed by its place ("t" + index, stable for the session), posted
// once when it completes; an assistant item still incomplete when a later user item appears was interrupted; at the end what is
// still incomplete is posted as partial. Posts go one at a time, at most 20 events and 64 KB each; a retried post keeps the
// same ids, so the server keeps each turn once.
//
// The owner turn a consult acts on is named by its capture id (Spec 165 §5.1's owner_item_id): ownerItem() is the id of the
// owner item that completed last, the same "t" + index its transcript post carries, so the server can tie the consult to the
// turn it kept. An earlier item that completes after a later one appeared is still named by its own place.
//
// Each event moves queued → sent → acknowledged (the server said it kept it), or ends failed(reason) after two attempts under
// the same id (Spec 165 §5.2): a refusal (422) at once; the network or a server failure, and a partial write, after one retry.
// Closing capture (close: End, Sum up, the spoken yes, the page going away) is one barrier: from its start nothing new is
// admitted, so the boundary is the last item seen then (last_item_id); what is still being said is posted as partial; and
// completeness is judged only up to that boundary (review R2 S2). report() is what the close carries: {last_item_id,
// unacknowledged}, the ids up to the boundary not acknowledged, from which the server counts what it does not hold. A turn
// that appears after the boundary (the voice's own "Sure, summing up.") is not this session's record: not posted, not counted.
export function createCapture({ send, clock = Date.now, onAnswer = () => {}, warn = () => {}, turnsOff = null }) {
  const items = [];
  const queued = new Set(), inflight = new Set();      // batches waiting on the chain; batches sent and not yet answered
  const fate = new Map();                              // item id -> {event, state: queued | sent | acknowledged | failed, reason, attempts}
  let chain = Promise.resolve(), stopped = false, ownerAt = -1, boundary = null;
  const iso = (ms) => new Date(ms).toISOString();

  function observe(turns) {
    if (boundary !== null) return;                     // closed: nothing new is admitted
    const now = clock();
    (Array.isArray(turns) ? turns : []).forEach((t, i) => {
      const it = items[i] || (items[i] = { speaker: t.speaker, text: '', completed: false, started: now, ended: null, posted: false });
      it.speaker = t.speaker === 'user' ? 'user' : 'assistant';
      it.text = typeof t.text === 'string' ? t.text : '';
      if (t.completed && !it.completed) {
        it.completed = true; it.ended = now;
        if (it.speaker === 'user' && it.text.trim()) ownerAt = i;          // the owner turn that just completed
      }
    });
  }
  function event(i, it, partial, interrupted) {
    it.posted = true;
    const [text, cut] = fit(it.text.trim());
    const end = Math.max(it.ended ?? clock(), it.started);
    const e = { role: it.speaker, item_id: `t${i}`, index: 0, text, partial: partial || cut, interrupted: it.speaker === 'assistant' && interrupted,
      started_at: iso(it.started), ended_at: iso(end) };
    fate.set(e.item_id, { event: e, state: 'queued', reason: null, attempts: 0 });
    return e;
  }
  function mark(batch, state, reason = null) {
    for (const e of batch) {
      const f = fate.get(e.item_id);
      if (!f || f.state === 'acknowledged') continue;
      f.state = state; f.reason = reason;
      if (state === 'sent') f.attempts += 1;
    }
  }
  function due(final) {
    const out = [];
    items.forEach((it, i) => {
      if (!it || it.posted || (boundary !== null && i > boundary)) return;
      if (!it.text.trim()) { if (it.completed || final) it.posted = true; return; }   // nothing was said: nothing to keep
      if (it.completed) out.push(event(i, it, false, false));
      else if (it.speaker === 'assistant' && items.slice(i + 1).some((n) => n && n.speaker === 'user')) out.push(event(i, it, true, true));
      else if (final) out.push(event(i, it, true, it.speaker === 'assistant'));
    });
    return out;
  }
  function batches(events, limit = POST_BYTES) {
    const out = [];
    let cur = [], size = 16;
    for (const e of events) {
      const n = bytes(JSON.stringify(e)) + 1;
      if (cur.length && (cur.length >= POST_EVENTS || size + n > limit)) { out.push(cur); cur = []; size = 16; }
      cur.push(e); size += n;
    }
    if (cur.length) out.push(cur);
    return out;
  }
  async function post(batch, retried, opts) {
    if (stopped || !batch.length) return;
    inflight.add(batch);
    mark(batch, 'sent');
    let r;
    try { r = await send(batch, opts); } finally { inflight.delete(batch); }
    const code = r.body && r.body.error;
    if (r.status === 200) { mark(batch, 'acknowledged'); return onAnswer(r.body || {}); }
    if (r.status === 413 && batch.length > 1) {                       // split the post (not an attempt)
      for (const e of batch) { const f = fate.get(e.item_id); if (f && f.state === 'sent') { f.state = 'queued'; f.attempts -= 1; } }
      const half = Math.ceil(batch.length / 2);
      await post(batch.slice(0, half), retried, opts);
      return post(batch.slice(half), retried, opts);
    }
    if (r.status === 409 && code === 'not_kept' && !retried) {        // a partial write: the rest, once more
      const kept = Math.max(0, Number(r.body.kept) || 0);
      mark(batch.slice(0, kept), 'acknowledged');
      return post(batch.slice(kept), true, opts);
    }
    if ((r.status === 0 || r.status >= 500) && !retried) return post(batch, true, opts);   // no answer: once more, the same ids
    // From here the batch is not kept: failed, with its reason, and the page says so (review B4).
    if (r.status === 422) {                                               // a page bug or a far-off clock: not retried
      mark(batch, 'failed', 'invalid');
      warn('voice transcript refused', code);
      return onAnswer({ failed: 'invalid', status: 422 });
    }
    if (r.status === 409 && code === 'not_kept') { mark(batch, 'failed', 'not_kept'); return onAnswer({ failed: 'not_kept' }); }
    stopped = ['session_ended', 'not_listening', 'cap', 'not_your_session', 'csrf', turnsOff].includes(code) || r.status === 403 || stopped;
    const reason = code || (r.status ? 'unavailable' : 'network');
    mark(batch, 'failed', reason);
    return onAnswer({ failed: reason, status: r.status });
  }
  function enqueue(events, opts) {
    for (const batch of batches(events)) {
      queued.add(batch);
      chain = chain.then(() => { queued.delete(batch); return post(batch, false, opts); }).catch((e) => warn('voice transcript post failed', e));
    }
    return chain;
  }
  // The barrier's start: the last look at what was said, then nothing new is admitted. Idempotent.
  function close(turns) {
    if (boundary === null) { observe(turns); boundary = items.length - 1; }
  }
  // Up to the boundary (or everything, before one): the ids not acknowledged.
  function unacknowledged() {
    const out = [];
    for (const [id, f] of fate) if (f.state !== 'acknowledged' && (boundary === null || Number(id.slice(1)) <= boundary)) out.push(id);
    items.forEach((it, i) => { if (it && !it.posted && it.text.trim() && (boundary === null || i <= boundary)) out.push(`t${i}`); });
    return out;
  }
  // Everything still owed up to the boundary, once: what is due now, partial ones too. Nothing once capture has stopped.
  const last = (turns, opts) => { close(turns); if (stopped) return chain; return enqueue(due(true), opts); };
  return {
    update(turns) { if (stopped || boundary !== null) return chain; observe(turns); return enqueue(due(false)); },
    // The capture id of the owner turn that completed last ("t" + its place), or null before he has said anything.
    ownerItem: () => (ownerAt >= 0 ? `t${ownerAt}` : null),
    finish: last,
    // The capture-closing barrier (review B2, R2 S2): close at the boundary, post what is owed and resolve once every post is
    // answered. The caller never awaits this from inside a post's own answer.
    flush: last,
    // A request after every post before it, on the same chain (Keep talking, review B5).
    after(fn) {
      chain = chain.then(() => fn()).catch((e) => warn('voice control post failed', e));
      return chain;
    },
    // The page is going away (UI review #1): no answer to a post can be awaited now, and a post still in flight is cancelled
    // with the page. Capture closes at the boundary; everything not acknowledged up to it (posts in flight, batches waiting,
    // what is due) goes with End, in one request (the browser bounds what a leaving page may send, and posts sent beside End
    // may arrive after it; Spec 165 v0.2's ordered keepalive batches are not used, review R1). What does not fit is not sent.
    // End carries the report, so the server counts what it does not hold. A turn sent twice is kept once: the ids are the same.
    drain(turns) {
      close(turns);
      const waiting = [...inflight, ...queued].flat();
      inflight.clear(); queued.clear();
      const owing = [...waiting, ...due(true)];
      stopped = true;
      // One bounded envelope (review CX V2-4): the report goes whole, and the events fit in what the keepalive budget leaves.
      const closing = report();
      const owed = batches(owing, Math.max(1024, CLOSE_BYTES - bytes(JSON.stringify(closing))));
      return { events: owed[0] || [], ...closing };
    },
    heartbeat() {
      if (stopped) return chain;
      chain = chain.then(async () => {
        if (stopped) return;
        const r = await send([]);
        if (r.status === 409 || r.status === 403) { stopped = true; onAnswer({ failed: (r.body && r.body.error) || 'session_ended', status: r.status }); }
        else if (r.status === 200) onAnswer(r.body || {});               // it may carry finished results (completed)
      }).catch((e) => warn('voice heartbeat failed', e));
      return chain;
    },
    // Some of what was said up to the boundary is not known to be kept: failed, or never answered.
    incomplete() { return unacknowledged().length > 0; },
    // What a close carries (§5.2): the boundary and the ids up to it not acknowledged.
    report,
    states: () => Object.fromEntries([...fate].map(([id, f]) => [id, f.state === 'failed' ? `failed(${f.reason})` : f.state])),
    stop() { stopped = true; },
    get stopped() { return stopped; },
    settled() { return chain; },
  };
  function report() {
    return { last_item_id: boundary !== null && boundary >= 0 ? `t${boundary}` : (items.length ? `t${items.length - 1}` : null),
      unacknowledged: unacknowledged() };
  }
}

// One direct voice session. `ui` is the host page's: say(text, {error, loud, transient}), phase(state), live(on),
// elapsed(text), notice(text), propose(on), checking(on) (Check with <person> in flight), ended(words, {error}) (the session
// ended by itself; the page resets its button).
export function directVoice(d) {
  const { page, direct, ui } = d;
  const words = personWords(direct.person, direct.words);
  const clock = d.clock || Date.now;
  const later = d.setTimeout || ((fn, ms) => setTimeout(fn, ms));
  const cancel = d.clearTimeout || ((t) => clearTimeout(t));
  const every = d.setInterval || ((fn, ms) => setInterval(fn, ms));
  const stopEvery = d.clearInterval || ((t) => clearInterval(t));
  let c = null, sid = null, cid = null, provider = null, lastOwner = '', capture = null;
  let connected = false, finalized = false, failed = false, proposing = false, keepalive = false;
  let ticker = null, beat = null, started = 0, summary = null, speakTimer = null, ending = null, endedWords = null;
  let lastTurns = [], summaryResponse = undefined;              // the spoken summary's response id, once the controller says
  let held = null, heldTimer = null, closed, sentFinal = false, settled = false;
  let consultedItem = null, checking = false;                     // the owner item last consulted (by the model or Check)
  const binding = [];                                              // model calls waiting for their owner turn's transcript
  const done = new Promise((resolve) => { closed = resolve; });     // settles once End is sent and the thread is reloaded
  done.then(() => { settled = true; });

  const at = (template) => String(template).split('{session_id}').join(encodeURIComponent(sid || ''));
  const postJson = jsonPost(d.fetch, d.headers);
  const getJson = jsonGet(d.fetch, d.headers);
  const sessionId = () => { const own = c?.sessionId?.(); return sid || (typeof own === 'string' ? own : null); };
  const speak = (instruction) => { try { return Boolean(c?.speak(instruction)); } catch (e) { return false; } };
  function stopTimers() {
    if (ticker) stopEvery(ticker);
    if (beat) stopEvery(beat);
    if (speakTimer) cancel(speakTimer);
    if (heldTimer) cancel(heldTimer);
    if (quietTimer) cancel(quietTimer);
    ticker = beat = speakTimer = heldTimer = quietTimer = null;
    lines.length = 0;
  }

  // A line for the voice (Check's answer, a finished result) is sent only at a pause: never while Robert is speaking or a
  // response is under way, since a provider may refuse a second response then (review R2, "at a pause"). The page knows a
  // response is under way from his speech ending (one follows), a function call (its result starts one) or its own line,
  // until the voice's turn completes. A line waits at most QUIET_WAIT_MS: past that the pause was missed (a response
  // with no spoken words reports no turn), and it is sent.
  let speaking = false, responding = false, quietTimer = null;
  const lines = [];
  const quiet = () => !speaking && !responding;
  function sayAtPause(instruction) {
    if (finalized || summary) return;
    lines.push(instruction);
    pump();
  }
  function pump() {
    if (!lines.length || finalized) return;
    if (!quiet()) {
      if (!quietTimer) quietTimer = later(() => { quietTimer = null; speaking = responding = false; pump(); }, QUIET_WAIT_MS);
      return;
    }
    if (quietTimer) { cancel(quietTimer); quietTimer = null; }
    if (speak(lines.shift())) responding = true;
    if (lines.length) pump();
  }
  // Finished results the server reports once (Spec 165 §5.1: a turn answered pending): said once each, at a pause.
  const told = new Set();
  function tell(completed) {
    for (const done of Array.isArray(completed) ? completed : []) {
      const key = done && typeof done.owner_item_id === 'string' ? done.owner_item_id : null;
      if (!key || told.has(key)) continue;
      told.add(key);
      const spoken = typeof done.spoken === 'string' && done.spoken.trim() ? done.spoken.trim() : (done.outcome === 'done' ? 'It is done.' : NOT_DONE);
      sayAtPause(`Say this briefly, adding nothing: About what you asked earlier: ${spoken}`);
    }
  }
  // A promise, or its fallback after `ms` (fake timers in the tests): what a bounded wait resolves to.
  function within(promise, ms, fallback) {
    let timer = null;
    return Promise.race([Promise.resolve(promise).catch(() => fallback), new Promise((resolve) => { timer = later(() => resolve(fallback), ms); })])
      .finally(() => cancel(timer));
  }
  function endNow(reason) {
    held = null;
    if (!finalized && c && typeof c.endSession === 'function') c.endSession(reason);
    if (!finalized) finalize({ turns: [] });
  }
  function propose(on) { proposing = on; ui.propose(on); }

  // The session's named states (Spec 165 §5.3): live → closing (the capture barrier) → summarizing (the summary asked for:
  // capture heartbeats stop and are not expected; the transport stays open) → speaking_summary → ended. End goes straight to
  // ended. Leaving live hides Sum up and Keep talking: the proposal is gone (review R2 N9).
  let lifecycle = 'idle';
  function become(next) {
    if (lifecycle === next || lifecycle === 'ended') return;
    lifecycle = next;
    if (next !== 'live') propose(false);
    ui.lifecycle?.(next);
  }

  // §5's answers: Sum up is offered, a spoken yes asks for the summary (after this answer: the summary's barrier awaits the
  // posts, so it never waits on the post it was asked from, review B2). The cap stops posting only; a session the server has
  // ended or refuses (409 session_ended or the person's turns off, any 403) is ended here too, with the reason left showing.
  // Any other post not kept (refused, or the network twice) says so at once (review B4).
  function heard(body) {
    if (finalized && !body.failed) return;
    if (body.completed) tell(body.completed);
    if (body.propose_end && !summary) propose(true);
    else if (body.proposing === false && proposing) propose(false);     // a later ordinary turn withdrew it (R2 N9)
    if (body.end_confirmed) Promise.resolve().then(sumUp);
    if (!body.failed) return;
    if (body.failed === 'cap') ui.say(CAP, { error: true });
    else if (body.failed === 'not_listening') { if (!finalized) c?.endSession('not_listening'); }
    else if (!finalized && !summary && (['session_ended', words.off].includes(body.failed) || body.status === 403)) {
      failed = true;
      ui.say(words.serverEnded[body.failed] || SERVER_ENDED.refused, { loud: true, error: true });
      endNow('server_ended');
    } else if (!finalized) ui.say(NOT_KEPT, { error: true });
  }

  // §4: the function bridge. The server acts on Robert's own last words, never on the model's arguments; whatever comes back
  // (any status) is the function's output, which the model is told to speak. Each call names the owner turn it acts on by
  // its capture id, owner_item_id (review B1, Spec 165 §5.1): the same for Check with <person> and the model's own call on
  // the same words, so the server runs that turn once and gives a second call the first one's answer; his next words are a
  // new turn, with a new id.
  async function consult(callId, name, args, owner) {
    const holding = later(() => { if (quiet() && speak('Still checking.')) responding = true; }, HOLDING_MS);
    let advisory = args;
    if (typeof advisory === 'string') { try { advisory = JSON.parse(advisory); } catch (e) { advisory = {}; } }
    if (!advisory || typeof advisory !== 'object' || Array.isArray(advisory)) advisory = {};
    const item = owner.item;
    consultedItem = item;                                             // by the model or by Check with <person>
    const request = { session_id: sessionId(), call_id: String(callId), name, owner_transcript_item: owner.text,
      owner_item_id: item, arguments: advisory };
    const lost = (x) => x.status === 0 || x.status >= 500;
    const inFlight = (x) => x.status === 409 && x.body.error === 'in flight';
    try {
      let r = await postJson(direct.consult_url, request);
      // No answer (the network, or the server failing): the same call once more. The server runs a call once and keeps its
      // answer, so this runs it for the first time or returns what it did, never twice. Anything but that answer (no answer
      // again, still running out of reach, or a session the server no longer holds, as after a restart): the first
      // attempt's outcome is unknown, not failed (review B7).
      const retried = lost(r);
      if (retried) r = await postJson(direct.consult_url, request);
      // The server has ended or refuses this session (409 "session ended", any 403): the answer still goes back to the
      // model, then the voice is ended here as for a refused transcript post (UI review #2).
      if ((r.status === 409 && r.body.error === 'session ended') || r.status === 403) {
        const code = r.status === 409 ? 'session_ended' : String(r.body.error || 'refused').replace(/ /g, '_');
        Promise.resolve().then(() => heard({ failed: code, status: r.status }));
      }
      if (retried && !(r.status === 200 && Object.keys(r.body).length)) return reconcile(item);
      if (inFlight(r)) return { ok: false, error: 'in flight', outcome: 'pending', spoken: PENDING };   // the same turn, still running (B1)
      if (Object.keys(r.body).length) return r.body;
      // An unreadable answer: refused is not done; accepted but unreadable is not known (review B7).
      return r.status >= 200 && r.status < 300 ? reconcile(item)
        : { ok: false, error: 'unavailable', outcome: 'not_done', spoken: NOT_DONE };
    } finally { cancel(holding); }
  }
  // The answer did not come back (Spec 165 §5.1): the page asks the server once what became of that owner turn. The
  // server reads its store and runs nothing; done, not done or still running is said as it is, anything else stays unknown.
  async function reconcile(item) {
    const unknown = { ok: false, error: 'unknown', outcome: 'unknown', spoken: UNKNOWN };
    if (!item || typeof direct.reconcile_url !== 'string' || !cid) return unknown;
    const url = `${at(direct.reconcile_url).split('{owner_item_id}').join(encodeURIComponent(item))}?conversation_id=${encodeURIComponent(cid)}`;
    const r = await getJson(url);
    return r.status === 200 && ['done', 'not_done', 'pending'].includes(r.body.outcome) ? r.body : unknown;
  }

  // A model call acts on the owner turn its own response answers, never on whichever turn finished last (review CX A1-C2,
  // R1 P1-2, R2 S4). The controller binds each response, when it starts, to the owner input item it answers, and the call
  // to its response (controller.inputOf(call)): the item's capture id and its words. If that turn's transcript has not
  // completed yet (transcription runs alongside the response), the call waits for it, at most HEAR_WAIT_MS. A call with no
  // binding (no response id, a response that answered no owner input), one whose turn never completes, or whose words came
  // empty is refused before anything is sent: "I didn't catch that; say it again." It is never rebound to newer words.
  function boundOwner(call) {
    const look = () => {
      const b = typeof c?.inputOf === 'function' ? c.inputOf(call) : null;
      if (!b) return null;
      if (b.state !== 'bound') return 'pending';
      const text = String(b.text || '').trim();
      return text ? { item: `t${b.index}`, text } : null;
    };
    const first = look();
    if (first !== 'pending') return Promise.resolve(first);
    return new Promise((resolve) => {
      let timer = null;
      const retry = () => { const b = look(); if (b === 'pending') return false; cancel(timer); resolve(b); return true; };
      timer = later(() => {
        const i = binding.indexOf(retry);
        if (i >= 0) binding.splice(i, 1);
        const b = look();
        resolve(b === 'pending' ? null : b);
      }, HEAR_WAIT_MS);
      binding.push(retry);
    });
  }
  function rebind() { for (const retry of [...binding]) if (retry()) binding.splice(binding.indexOf(retry), 1); }
  async function modelCall(call) {
    responding = false;                                    // the response that called it is done; its result starts the next
    try {
      const owner = await boundOwner(call);
      if (!owner) return { ok: false, outcome: 'not_done', error: 'not_heard', spoken: NOT_HEARD };
      return await consult(call.call_id, call.name, call.arguments, owner);
    } finally { responding = true; }
  }

  // Check with <person>: the host asks for Robert's last words, then has the voice say the answer, adding nothing. One at a time
  // (the button is disabled meanwhile), and never twice for the same turn of his, whoever consulted it: a repeat would run a
  // second typed turn and could start a second job (UI review #3). Saying the words again makes a new turn to check.
  async function check() {
    if (!connected || finalized || checking || summary) return;
    if (!lastOwner || !capture?.ownerItem()) { ui.say(words.sayFirst, { transient: true }); return; }
    if (consultedItem && consultedItem === capture?.ownerItem()) { ui.say(ALREADY_CHECKED, { transient: true }); return; }
    checking = true; ui.checking?.(true);
    try {
      ui.phase('working');
      const body = await consult(`host-${clock()}`, 'consult', {}, { item: capture.ownerItem(), text: lastOwner });
      if (finalized) return;
      const spoken = body && typeof body.spoken === 'string' && body.spoken.trim() ? body.spoken.trim() : NOT_DONE;
      sayAtPause(`Say this briefly, adding nothing: ${spoken}`);
      ui.phase('listening');
    } finally { checking = false; ui.checking?.(false); }
  }

  // §7: the summary, behind a barrier (review B2, B3). No new spoken input is admitted (the microphone is muted; the voice
  // still speaks), the heartbeat stops, the last turns are posted (partial ones too) and every post is answered; only then is
  // the summary asked for, so it covers what was said, and is told when some of it could not be kept. The server closes
  // capture when asked. Its answer is awaited at most SUMMARY_WAIT_MS (a later one is still kept and shown). The voice says the
  // spoken part, and the session ends once that turn has played out (review B6).
  function sumUp() {
    if (summary || finalized || !sessionId()) return summary;
    propose(false);
    ui.say('Summing up…');
    summary = (async () => {
      become('closing');
      try { c?.mute?.(); } catch (e) { /* the barrier holds without it: capture stops below */ }
      if (beat) { stopEvery(beat); beat = null; }
      let wait = null;
      await Promise.race([capture?.flush(lastTurns), new Promise((resolve) => { wait = later(resolve, FLUSH_WAIT_MS); })]);
      cancel(wait);
      if (finalized) return;
      const incomplete = Boolean(capture?.incomplete());
      const closing = capture ? capture.report() : {};
      capture?.stop();
      become('summarizing');
      let timer = null;
      const r = await Promise.race([
        postJson(at(direct.summary_url), { conversation_id: cid, ...closing }),
        new Promise((resolve) => { timer = later(() => resolve({ status: -1, body: {} }), SUMMARY_WAIT_MS); }),
      ]);
      cancel(timer);
      if (finalized) return;
      const body = r.body || {};
      const said = typeof body.spoken === 'string' ? body.spoken.trim() : '';
      if (r.status === 200 && body.ok && body.state === 'proposed' && said && connected) {
        // The summary is one tracked response (Spec 165 §5.3; review B6): the session ends when the controller reports that
        // response's audio drained, at barge-in, or at the bound. Another response draining never closes it.
        const instruction = `Say only this, then stop: ${said}`;
        const tracked = typeof c?.speakTracked === 'function' ? c.speakTracked(instruction) : (speak(instruction) ? Promise.resolve(null) : null);
        if (tracked) {
          become('speaking_summary');
          speakTimer = later(() => summaryHeard('bound'), summaryBound(said));
          tracked.then((id) => { if (id) summaryResponse = id; });   // no id reported: the bound alone ends it
          return;
        }
      }
      // "Summary unavailable" only when the server's attempt concluded so (review CX V2-5). The page's own bound, a lost
      // answer (no answer, a proxy's 502/504) or an attempt still running (pending) may yet keep a proposal (R1 P3-7).
      if (r.status === 200 && body.state === 'unavailable') endedWords = [SUMMARY_UNAVAILABLE, false];
      else if (r.status === -1 || r.status === 0 || r.status >= 500 || (r.status === 200 && body.state === 'pending')) endedWords = [SUMMARY_MAY_APPEAR, false];
      else if (r.status !== 200) endedWords = [body.message || `${SUMMARY_REFUSED}${incomplete ? '' : ' What was said is kept.'}`, true];
      c?.endSession('normal_end');
    })();
    return summary;
  }
  // The spoken summary has been heard to its end (its response drained), interrupted (barge-in), or its bound has passed.
  function summaryHeard() {
    if (lifecycle !== 'speaking_summary' || finalized) return;
    if (speakTimer) { cancel(speakTimer); speakTimer = null; }
    c?.endSession('normal_end');
  }

  // The page is going away: End leaves in this same tick, with keepalive, carrying the last turns itself in one post (the
  // browser bounds what a leaving page may send, and a post sent beside End may arrive after it). What does not fit is not
  // sent. End carries the close's report ({last_item_id, unacknowledged}), so the server writes complete or incomplete(n)
  // from what it holds (review B4, Spec 165 §5.2). Nothing waits for a reply (none arrives after pagehide). Once per session.
  function sendFinal(turns) {
    const id = sessionId();
    if (!id || sentFinal) return;
    sentFinal = true;
    const { events, ...closing } = capture ? capture.drain(turns || []) : { events: [] };
    postJson(at(direct.end_url), { ...(events.length ? { events } : {}), ...closing }, { keepalive: true });
  }

  // §6: End on the server (after the last turns are posted), then the reveal. Once, whatever ended the session.
  function finalize(result) {
    if (finalized) return ending;
    finalized = true; connected = false;
    become('ended');
    stopTimers(); propose(false); ui.live(false);
    if (keepalive) {
      sendFinal(result?.turns);
      ending = Promise.resolve();                            // nothing more to show on a page that is leaving
      ending.then(closed, closed);
      return ending;
    }
    // Every wait here is bounded, and a barrier that already gave up (Sum up's five seconds) is not awaited again: its
    // posts that never answered stay unacknowledged and are reported as such (review CX A1-C3). So End, the reveal and the
    // page's ended line always come, even when a post, End or the reload never settles.
    ending = (async () => {
      const id = sessionId();
      let incomplete = false, missing = null;
      if (id) {
        const abandoned = Boolean(capture?.stopped);
        const delivering = capture?.finish(result?.turns || []);     // closes at the boundary; posts what is owed unless stopped
        if (!abandoned) await within(delivering, FLUSH_WAIT_MS);
        if (sentFinal) return;                               // the page went away meanwhile: End already left with keepalive
        const closing = capture ? capture.report() : {};     // End carries it; the server counts what it does not hold (§5.2)
        incomplete = Boolean(closing.unacknowledged?.length);
        missing = incomplete ? closing.unacknowledged.length : 0;
        const r = await within(postJson(at(direct.end_url), closing), END_WAIT_MS, { status: -1, body: {} });
        if (sentFinal) return;
        if (r.status === 200 && r.body.ok && ['complete', 'incomplete'].includes(r.body.capture)) {
          incomplete = r.body.capture === 'incomplete';      // the server's count: what it holds, not what this page heard back
          missing = Number.isInteger(r.body.missing) ? r.body.missing : null;
        }
        if (!(r.status === 200 && r.body.ok) && !failed) endedWords = ['Voice ended here, but the server did not confirm it. What was said appears when the session\'s time runs out.', true];
        await within(Promise.resolve().then(() => d.reveal?.()), REVEAL_WAIT_MS);
      }
      const fallback = failed || !id ? null : incomplete ? incompleteWords(missing) : ENDED;
      const [line, error] = endedWords || [fallback, incomplete && fallback !== null];
      ui.ended(line, { error });
    })();
    ending.then(closed, closed);
    return ending;
  }

  // Keep talking (Spec 165 §5.3, review B5, R1 P3-8): the server's proposal is withdrawn too, so a later conversational yes
  // ends nothing. It goes after the posts queued before it (a "we're done" still in flight cannot re-propose behind it), is
  // retried once with the same body when it gets no answer, and if it still fails the page says so and ends the voice:
  // a yes heard later must not end the session by a proposal the server still holds.
  function keepTalking() {
    propose(false);
    if (!capture || lifecycle !== 'live') return Promise.resolve();
    return capture.after(async () => {
      const url = at(direct.keep_talking_url);
      let r = await postJson(url, {});
      if (r.status === 0 || r.status >= 500) r = await postJson(url, {});
      if (r.status === 200 && r.body.ok) return;
      if (finalized || lifecycle !== 'live') return;
      failed = true;
      ui.say(KEEP_TALKING_FAILED, { loud: true, error: true });
      endNow('keep_talking_failed');
    });
  }

  async function start(chosen) {
    cid = page.conversation?.id || null;
    provider = chosen || direct.default_provider;
    if (!cid) { failed = true; ui.ended(words.noConversation, { error: true }); return false; }
    const { RealtimeVoiceController } = await d.loadController();
    if (finalized) return false;
    capture = createCapture({ send: (events, opts) => postJson(at(direct.transcript_url), { events }, opts || {}),
      clock, onAnswer: heard, warn: d.warn || (() => {}), turnsOff: words.off });
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
          connected = true; started = clock(); become('live');
          ui.live(true); ui.elapsed(clockWords(0)); ui.phase('listening'); ui.say('');
          ticker = every(() => ui.elapsed(clockWords(Math.floor((clock() - started) / 1000))), 1000);
          beat = every(() => capture.heartbeat(), Math.max(5, Number(direct.heartbeat_seconds) || 20) * 1000);
        }
        if (state === 'reconnecting' && !finalized) c.endSession('disconnect');     // no silent reconnect: end visibly
      },
      onInputState: (phase) => {
        if (phase === 'speech_started') { speaking = true; if (lifecycle === 'speaking_summary') summaryHeard(); }   // barge-in
        if (phase === 'speech_stopped') { speaking = false; responding = true; }         // a response follows his words
        if (!connected || finalized) return;
        ui.phase(phase === 'speaking' ? 'speaking' : ['consulting', 'understood', 'speech_stopped'].includes(phase) ? 'working' : 'listening');
      },
      onWarning: () => { if (!finalized) ui.say('Voice will stop in about two minutes.', { loud: true }); },
      onStop: () => { endedWords = ['Voice stopped: the session reached its time limit.', false]; },
      onFatalError: (info) => {
        failed = true;
        ui.say(startRefusal(info, direct.person, direct.words), { loud: true, error: true });
        if (info?.reason === 'bootstrap_rejected' || info?.reason === 'bootstrap_network_error') finalize({ turns: [] });
      },
      onPlayback: () => {},
      onTranscriptUpdate: (turns) => { lastTurns = turns; if (!finalized) capture.update(turns); rebind(); },
      onUserTurn: (text) => {
        const words = typeof text === 'string' ? text.trim() : '';
        if (words) lastOwner = words;
        rebind();
      },
      onAssistantTurn: () => { responding = false; pump(); },
      // Spec 165 §5.3: a response's audio has played out. Only the summary's own response ends the session.
      onPlaybackDrained: (responseId) => { if (summaryResponse !== undefined && responseId === summaryResponse) summaryHeard(); },
      onFunctionCall: (call) => modelCall(call),
      onFinalize: (result) => finalize(result),
    });
    await c.startSession({ provider, persona: direct.persona, scene: '', conversation_id: cid });
    return true;
  }

  return {
    start,
    check,
    sumUp,
    keepTalking,
    proposing: () => proposing,
    state: () => lifecycle,
    sessionId,
    // Ends the session for any reason (End, the microphone handed over, Private, the page going away). Resolves once End is sent.
    // While the provider is still connecting, the end waits for the connection (at most START_END_MS): the shared adapters take
    // the microphone while they connect, and ending the transport before that could leave it open.
    end(reason) {
      keepalive = reason === 'pagehide';
      if (finalized) {
        // An End already under way (its last post or End still waiting on the server) when the page goes away: send them
        // again now with keepalive, since the replies it waits for will never arrive (UI review #1).
        if (keepalive && !settled) sendFinal([]);
        return done;
      }
      const starting = c && !connected && ['requesting_mic', 'connecting'].includes(c.state?.());
      if (starting && !keepalive) {
        if (!held) { held = reason; heldTimer = later(() => endNow(reason), START_END_MS); }
        return done;
      }
      endNow(reason);
      return done;
    },
  };
}
