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
// turns are off here" (the refusal code <short>_turns_off). The controller is read only through its public sessionId()
// and speak(instruction).
//
// No imports: the host page hands in the page, the fetch, the controller's loader and its ui, so the node tests drive this
// file with a fake controller, a fake fetch and fake timers.

export const NO_CONVERSATION = 'Open a saved conversation before using voice.';
export const ENDED = 'Voice ended. What was said is in the conversation.';
export const NOT_DONE = 'That did not go through. Nothing is claimed.';
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
  not_found: 'Voice unavailable: that conversation is not available.',
  unavailable: 'Voice unavailable here right now.',
};
// The words that name the person ({name, short}, from direct_voice.person). The host's own "turns are off" refusal is
// <short>_turns_off (MC: mc_turns_off); without a person there is none.
export function personWords(person) {
  const name = typeof person?.name === 'string' && person.name ? person.name : 'the agent';
  const short = typeof person?.short === 'string' && person.short ? person.short : name;
  const off = typeof person?.short === 'string' && person.short ? `${person.short.toLowerCase()}_turns_off` : null;
  return {
    off,
    start: off ? { ...START_WORDS, [off]: `Voice unavailable: ${name}'s turns are off here.` } : START_WORDS,
    serverEnded: off ? { ...SERVER_ENDED, [off]: `Voice stopped: ${name}'s turns are off here. What was said before is kept.` } : SERVER_ENDED,
    sayFirst: `Say something first: Check with ${short} sends your last words.`,
  };
}
export function startRefusal(info, person) {
  const words = personWords(person).start;
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
export function createCapture({ send, clock = Date.now, onAnswer = () => {}, warn = () => {}, turnsOff = null }) {
  const items = [];
  const queued = new Set(), inflight = new Set();      // batches waiting on the chain; batches sent and not yet answered
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
    inflight.add(batch);
    let r;
    try { r = await send(batch, opts); } finally { inflight.delete(batch); }
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
    stopped = ['session_ended', 'not_listening', 'cap', 'not_your_session', 'csrf', turnsOff].includes(code) || r.status === 403 || stopped;
    return onAnswer({ failed: code || (r.status ? 'unavailable' : 'network'), status: r.status });
  }
  function enqueue(events, opts) {
    for (const batch of batches(events)) {
      queued.add(batch);
      chain = chain.then(() => { queued.delete(batch); return post(batch, false, opts); }).catch((e) => warn('voice transcript post failed', e));
    }
    return chain;
  }
  return {
    update(turns) { observe(turns); return enqueue(due(false)); },
    finish(turns, opts) { observe(turns); return enqueue(due(true), opts); },
    // The page is going away (UI review #1): no answer to a post can be awaited now, and a post still in flight is cancelled
    // with the page. Everything not known to be kept (posts in flight, batches waiting on the chain, what is due at the end)
    // is handed back in batches to send at once, and posting stops. A turn sent twice is kept once: the ids are the same.
    drain(turns) {
      observe(turns);
      const waiting = [...inflight, ...queued].flat();
      inflight.clear(); queued.clear();
      const out = batches([...waiting, ...due(true)]);
      stopped = true;
      return out;
    },
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

// One direct voice session. `ui` is the host page's: say(text, {error, loud, transient}), phase(state), live(on),
// elapsed(text), notice(text), propose(on), checking(on) (Check with <person> in flight), ended(words, {error}) (the session
// ended by itself; the page resets its button).
export function directVoice(d) {
  const { page, direct, ui } = d;
  const words = personWords(direct.person);
  const clock = d.clock || Date.now;
  const later = d.setTimeout || ((fn, ms) => setTimeout(fn, ms));
  const cancel = d.clearTimeout || ((t) => clearTimeout(t));
  const every = d.setInterval || ((fn, ms) => setInterval(fn, ms));
  const stopEvery = d.clearInterval || ((t) => clearInterval(t));
  let c = null, sid = null, cid = null, provider = null, lastOwner = '', capture = null;
  let connected = false, finalized = false, failed = false, proposing = false, keepalive = false;
  let ticker = null, beat = null, started = 0, summary = null, speakTimer = null, endAfterTurn = null, ending = null, endedWords = null;
  let held = null, heldTimer = null, closed, sentFinal = false, settled = false;
  let ownerTurn = 0, consultedTurn = -1, checking = false;          // Robert's turns, counted; the last one consulted
  const done = new Promise((resolve) => { closed = resolve; });     // settles once End is sent and the thread is reloaded
  done.then(() => { settled = true; });

  const at = (template) => String(template).split('{session_id}').join(encodeURIComponent(sid || ''));
  const postJson = jsonPost(d.fetch, d.headers);
  const sessionId = () => { const own = c?.sessionId?.(); return sid || (typeof own === 'string' ? own : null); };
  const speak = (instruction) => { try { return Boolean(c?.speak(instruction)); } catch (e) { return false; } };
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

  // §5's answers: Sum up is offered, a spoken yes asks for the summary. The cap stops posting only; a session the server
  // has ended or refuses (409 session_ended or the person's turns off, any 403) is ended here too, with the reason left showing.
  function heard(body) {
    if (finalized && !body.failed) return;
    if (body.propose_end) propose(true);
    if (body.end_confirmed) sumUp();
    if (body.failed === 'not_kept') ui.say(NOT_KEPT, { error: true });
    else if (body.failed === 'cap') ui.say(CAP, { error: true });
    else if (body.failed === 'not_listening' && !finalized) c?.endSession('not_listening');
    else if (!finalized && (['session_ended', words.off].includes(body.failed) || body.status === 403)) {
      failed = true;
      ui.say(words.serverEnded[body.failed] || SERVER_ENDED.refused, { loud: true, error: true });
      endNow('server_ended');
    }
  }

  // §4: the function bridge. The server acts on Robert's own last words, never on the model's arguments; whatever comes back
  // (any status) is the function's output, which the model is told to speak.
  async function consult(callId, name, args) {
    const holding = later(() => speak('Still checking.'), HOLDING_MS);
    let advisory = args;
    if (typeof advisory === 'string') { try { advisory = JSON.parse(advisory); } catch (e) { advisory = {}; } }
    if (!advisory || typeof advisory !== 'object' || Array.isArray(advisory)) advisory = {};
    if (lastOwner) consultedTurn = ownerTurn;                          // by the model or by Check with <person>
    try {
      const r = await postJson(direct.consult_url, { session_id: sessionId(), call_id: String(callId), name, owner_transcript_item: lastOwner,
        arguments: advisory });
      if (r.status === 0) return { ok: false, error: 'network', spoken: NOT_DONE };
      // The server has ended or refuses this session (409 "session ended", any 403): the answer still goes back to the
      // model, then the voice is ended here as for a refused transcript post (UI review #2).
      if ((r.status === 409 && r.body.error === 'session ended') || r.status === 403) {
        const code = r.status === 409 ? 'session_ended' : String(r.body.error || 'refused').replace(/ /g, '_');
        Promise.resolve().then(() => heard({ failed: code, status: r.status }));
      }
      return Object.keys(r.body).length ? r.body : { ok: false, error: 'unavailable', spoken: NOT_DONE };   // an unreadable answer: not done
    } finally { cancel(holding); }
  }

  // Check with <person>: the host asks for Robert's last words, then has the voice say the answer, adding nothing. One at a time
  // (the button is disabled meanwhile), and never twice for the same turn of his, whoever consulted it: a repeat would run a
  // second typed turn and could start a second job (UI review #3). Saying the words again makes a new turn to check.
  async function check() {
    if (!connected || finalized || checking) return;
    if (!lastOwner) { ui.say(words.sayFirst, { transient: true }); return; }
    if (consultedTurn === ownerTurn) { ui.say(ALREADY_CHECKED, { transient: true }); return; }
    checking = true; ui.checking?.(true);
    try {
      ui.phase('working');
      const body = await consult(`host-${clock()}`, 'consult', {});
      if (finalized) return;
      const spoken = body && typeof body.spoken === 'string' && body.spoken.trim() ? body.spoken.trim() : NOT_DONE;
      speak(`Say this briefly, adding nothing: ${spoken}`);
      ui.phase('listening');
    } finally { checking = false; ui.checking?.(false); }
  }

  // §7: the summary. The route ends capture and asks the person's own model path; the voice says the spoken part, then the
  // session ends.
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

  // The page is going away: the last turns and End leave in this same tick, with keepalive, and nothing waits for a reply
  // (none arrives after pagehide). End carries the final events itself, so they cannot arrive after it; only events beyond
  // one post (rare: more than 20 turns or 60 KB unsent) go as transcript posts just before it. Once per session.
  function sendFinal(turns) {
    const id = sessionId();
    if (!id || sentFinal) return;
    sentFinal = true;
    const parts = capture ? capture.drain(turns || []) : [];
    const last = parts.pop();
    for (const events of parts) postJson(at(direct.transcript_url), { events }, { keepalive: true });
    postJson(at(direct.end_url), last ? { events: last } : {}, { keepalive: true });
  }

  // §6: End on the server (after the last turns are posted), then the reveal. Once, whatever ended the session.
  function finalize(result) {
    if (finalized) return ending;
    finalized = true; connected = false;
    stopTimers(); propose(false); ui.live(false);
    if (keepalive) {
      sendFinal(result?.turns);
      ending = Promise.resolve();                            // nothing more to show on a page that is leaving
      ending.then(closed, closed);
      return ending;
    }
    ending = (async () => {
      const id = sessionId();
      if (id) {
        await capture?.finish(result?.turns || []);
        if (sentFinal) return;                               // the page went away meanwhile: End already left with keepalive
        const r = await postJson(at(direct.end_url), {});
        if (sentFinal) return;
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
      warn: d.warn || (() => {}), turnsOff: words.off });
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
        ui.say(startRefusal(info, direct.person), { loud: true, error: true });
        if (info?.reason === 'bootstrap_rejected' || info?.reason === 'bootstrap_network_error') finalize({ turns: [] });
      },
      onPlayback: () => {},
      onTranscriptUpdate: (turns) => { if (!finalized) capture.update(turns); },
      onUserTurn: (text) => { if (typeof text === 'string' && text.trim()) { lastOwner = text.trim(); ownerTurn += 1; } },
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
      if (finalized) {
        // An End already under way (its last post or End still waiting on the server) when the page goes away: send them
        // again now with keepalive, since the replies it waits for will never arrive (UI review #1).
        if (keepalive && !settled) sendFinal([]);
        return done;
      }
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
