// Codex's Chunk B review (REVIEW_codex_chunk_B.md, B1–B7 and the capture edge), as regressions that assert the CORRECT
// behaviour of the shared session module (core/realtime_voice/static/direct-voice-session.js) and the Guild thread. Converted
// from review-evidence-codex/repro_chunk_b.mjs, which asserted the defects. Fake controller, fetch and timers; no DOM,
// network, provider or microphone.
//
// To show a case fails before its fix, point it at an older module: VOICE_SESSION_MODULE=<file> VOICE_THREAD_MODULE=<file>
// (at 93bf00c6 both are minimoi_portal/guild_ui/static/js/mc_voice_direct.js). The fake controller offers the old private
// fields and the new public methods, so either module runs. Every case runs; the failures are listed, then the exit code is 1.
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { fakeDocument } from './js_helpers/fake_dom.mjs';

const here = (p) => new URL(p, import.meta.url);
const D = await import(process.env.VOICE_SESSION_MODULE ? pathToFileURL(process.env.VOICE_SESSION_MODULE) : here('../core/realtime_voice/static/direct-voice-session.js'));
const T = await import(process.env.VOICE_THREAD_MODULE ? pathToFileURL(process.env.VOICE_THREAD_MODULE) : here('../minimoi_portal/guild_ui/static/js/mc_voice_direct.js'));
const flush = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)); };

const SID = '7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f';
const BASE = '/guild-next/api/v1/mc/voice/direct/api/realtime-voice';
const DIRECT = { path: 'direct', providers: [{ provider: 'openai', label: 'OpenAI' }], default_provider: 'openai',
  bootstrap_url: `${BASE}/bootstrap`, consult_url: `${BASE}/consult`, end_url: `${BASE}/session/{session_id}/end`,
  transcript_url: `${BASE}/session/{session_id}/transcript`, summary_url: `${BASE}/session/{session_id}/summary`,
  summary_decision_url: `${BASE}/session/{session_id}/summary/decision`, keep_talking_url: `${BASE}/session/{session_id}/keep_talking`,
  heartbeat_seconds: 20, persona: 'master_craftsman',
  locale: 'en-US', card_release: '0.1.18', person: { name: 'Master Craftsman', short: 'MC' } };
const UNKNOWN = "I didn't get the result back. Check it in writing before asking again.";      // Spec 165 §5.1's words
const INCOMPLETE = (n) => `Voice ended. Some of what was said was not kept (${n} turn${n === 1 ? '' : 's'}). What was kept is in the conversation.`;   // §5.2
const MAY_APPEAR = 'The summary may still appear in the conversation.';   // past the 45 s bound: not failure (review CX V2-5)

function rig({ routes = {} } = {}) {
  const calls = [], said = [], ended = [], reveals = [];
  const timers = new Map(); let nextTimer = 1, now = Date.parse('2026-10-28T10:00:00.000Z');
  const later = (fn, ms, repeat = false) => { const id = nextTimer++; timers.set(id, { fn, ms, repeat, due: now + ms }); return id; };
  const advance = (ms) => {
    const until = now + ms;
    for (;;) {
      const next = [...timers.entries()].filter(([, t]) => t.due <= until).sort((a, b) => a[1].due - b[1].due)[0];
      if (!next) break;
      const [id, t] = next; now = t.due;
      if (t.repeat) t.due += t.ms; else timers.delete(id);
      t.fn();
    }
    now = until;
  };
  const fetch = async (url, opts) => {
    const body = JSON.parse(opts.body);
    calls.push({ url, opts, body });
    const key = Object.keys(routes).sort((a, b) => b.length - a.length).find((k) => url.endsWith(k));
    let out = key ? await routes[key](body, url) : null;
    if (!out) {
      if (url.endsWith('/end')) out = { status: 200, body: { ok: true, ended: true } };
      else if (url.endsWith('/transcript')) out = { status: 200, body: { ok: true, kept: body.events.length } };
      else if (url.endsWith('/consult')) out = { status: 200, body: { ok: true, call_id: body.call_id, answered: true, receipt: 'mc-1', spoken: 'Item 12 is in build.' } };
      else out = { status: 404, body: {} };
    }
    if (out === 'network') throw new Error('offline');
    return { ok: out.status >= 200 && out.status < 300, status: out.status, json: async () => out.body };
  };
  let controller = null;
  class Controller {
    constructor(options) {
      this.options = options; this._state = 'idle'; this._sessionId = null; this.instructions = []; this.ended = []; this.muted = 0;
      this._adapter = { sendContinuationInstruction: (t) => this.instructions.push(t) };   // the 93bf00c6 page reads these
      controller = this;
    }
    sessionId() { return this._sessionId; }                 // the public surface (Spec 165 §4.3)
    state() { return this._state; }
    speak(t) { this.instructions.push(t); return true; }
    speakTracked(t) { this.instructions.push(t); return Promise.resolve('resp_sum'); }   // the summary's tracked response
    mute() { this.muted += 1; }
    async startSession(args) {
      this.args = args; this._state = 'connecting'; this._sessionId = SID;
      this.options.onSessionInfo({ provider: args.provider, voice_session_id: SID, voice_notices: [] });
      this._state = 'active'; this.options.onStateChange('active');
    }
    endSession(reason) {
      if (['ending', 'ended'].includes(this._state)) return;
      this.ended.push(reason); this._state = 'ended'; this.options.onStateChange('ended');
      this.options.onFinalize({ turns: this.turns || [], partial_reason: reason, session_id: this._sessionId });
    }
    inputOf() {             // this fake's responses answer the owner turn that came last (the real binding: test_response_binding.mjs)
      const turns = this.turns || [];
      let i = turns.length - 1;
      while (i >= 0 && turns[i].speaker !== 'user') i -= 1;
      if (i < 0) return null;
      return turns[i].completed ? { state: 'bound', index: i, text: turns[i].text } : { state: 'pending' };
    }
    utter(text) {                                          // a completed owner turn, as the controller reports it: the transcript, then the turn
      this.turns = [...(this.turns || []), { speaker: 'user', text, completed: true }];
      this.options.onTranscriptUpdate(this.turns.map((t) => ({ ...t }))); this.options.onUserTurn(text);
    }
    hear(turns) { this.turns = turns.map((t) => ({ ...t })); this.options.onTranscriptUpdate(this.turns.map((t) => ({ ...t }))); }
  }
  const ui = { say: (text, o = {}) => said.push([text, o]), phase() {}, live() {}, elapsed() {}, notice() {}, propose() {},
    ended: (words, o) => ended.push([words, o]), checking() {} };
  const page = { conversation: { id: 'c-1' } };
  const s = D.directVoice({ page, direct: DIRECT, ui, headers: () => ({ 'Content-Type': 'application/json' }), fetch,
    reveal: async () => { reveals.push(true); }, loadController: async () => ({ RealtimeVoiceController: Controller }), clock: () => now,
    setTimeout: (fn, ms) => later(fn, ms), clearTimeout: (id) => timers.delete(id),
    setInterval: (fn, ms) => later(fn, ms, true), clearInterval: (id) => timers.delete(id) });
  return { s, calls, said, ended, reveals, timers, advance, get c() { return controller; },
    posts: (end) => calls.filter((x) => x.url.endsWith(end)) };
}
const user = (text, completed = true) => ({ speaker: 'user', text, completed });
const failures = [];
let cases = 0;
async function check(name, fn) {
  cases++;
  try { await fn(); } catch (e) { failures.push(`${name}: ${e.message.split('\n')[0]}`); }
}

// ── B1: one owner turn, one effect, whoever dispatches it ───────────────────────────────────────────────────────────────
await check('B1 manual Check then the model on the same words name the same owner turn', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.utter('Save a note: the kiln needs a new seal');
  await r.s.check();
  await r.c.options.onFunctionCall({ call_id: 'provider-repeat', name: 'save_note', arguments: {} });
  const turns = r.posts('/consult').map((p) => p.body.owner_item_id);
  assert.equal(turns.length, 2); assert.ok(turns[0]); assert.equal(turns[0], turns[1]);
});
await check('B1 the same words said again are a new owner turn', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.utter('What is stuck on #12?');
  await r.c.options.onFunctionCall({ call_id: 'a', name: 'consult', arguments: {} });
  r.c.utter('What is stuck on #12?');
  await r.c.options.onFunctionCall({ call_id: 'b', name: 'consult', arguments: {} });
  const [first, second] = r.posts('/consult').map((p) => p.body.owner_item_id);
  assert.ok(first && second); assert.notEqual(first, second);
});
await check('B1 a second dispatch while the first runs is told it is still being checked, not that it failed', async () => {
  const r = rig({ routes: { '/consult': () => ({ status: 409, body: { ok: false, error: 'in flight', spoken: 'Still checking.' } }) } });
  await r.s.start('openai');
  r.c.utter('Run the nightly job.');
  const out = await r.c.options.onFunctionCall({ call_id: 'provider-2', name: 'consult', arguments: {} });
  assert.equal(out.outcome, 'pending'); assert.doesNotMatch(out.spoken, /did not go through/);   // only done is ok (§5.1)
});

// ── B2: Sum up closes the record only after the last turns are kept ─────────────────────────────────────────────────────
await check('B2 the summary is asked only after the final transcript post is answered', async () => {
  let release, order = [];
  const r = rig({ routes: {
    '/transcript': (body) => (body.events.length ? new Promise((res) => { release = () => { order.push('transcript-ack'); res({ status: 200, body: { ok: true, kept: body.events.length } }); }; }) : null),
    '/summary': () => { order.push('summary'); return { status: 200, body: { ok: true, state: 'proposed', spoken: 'We checked twelve.' } }; } } });
  await r.s.start('openai');
  r.c.hear([user('One last detail.')]); await flush();
  const asked = r.s.sumUp(); await flush();
  assert.equal(r.posts('/summary').length, 0, 'summary asked before the transcript was kept');
  release(); await asked; await flush();
  assert.deepEqual(order, ['transcript-ack', 'summary']);
});
await check('B2 words still being said at Sum up are posted (partial) before the summary', async () => {
  const r = rig({ routes: { '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'Done.' } }) } });
  await r.s.start('openai');
  r.c.hear([user('And one more thing about the gl', false)]);
  await r.s.sumUp(); await flush();
  const posted = r.calls.filter((x) => x.url.endsWith('/transcript') && x.body.events.length);
  const summaryAt = r.calls.findIndex((x) => x.url.endsWith('/summary'));
  assert.equal(posted.length, 1); assert.equal(posted[0].body.events[0].partial, true);
  assert.ok(r.calls.indexOf(posted[0]) < summaryAt);
  assert.ok(r.c.muted >= 1, 'no new spoken input is admitted while the record closes');
});
await check('B2 a refused final post makes the summary request say capture is incomplete', async () => {
  const r = rig({ routes: { '/transcript': (body) => (body.events.length ? { status: 422, body: { error: 'time' } } : null),
    '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'Done.' } }) } });
  await r.s.start('openai');
  r.c.hear([user('Final words.')]);
  await r.s.sumUp(); await flush();
  assert.deepEqual(r.posts('/summary')[0].body.unacknowledged, ['t0']);           // the close's report (§5.2)
});
await check('B2 a final post that never answers holds the summary at most five seconds, and the summary says incomplete', async () => {
  const r = rig({ routes: { '/transcript': (body) => (body.events.length ? new Promise(() => {}) : null),
    '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'Done.' } }) } });
  await r.s.start('openai');
  r.c.hear([user('Words in flight.')]); await flush();
  r.s.sumUp(); await flush();
  r.advance(4999); await flush();
  assert.equal(r.posts('/summary').length, 0);
  r.advance(1); await flush();
  assert.equal(r.posts('/summary').length, 1); assert.deepEqual(r.posts('/summary')[0].body.unacknowledged, ['t0']);
});
await check('B2 a spoken yes asks for the summary after its own post, without waiting on itself', async () => {
  const r = rig({ routes: {
    '/transcript': (body) => (body.events.some((e) => e.text === 'Yes.') ? { status: 200, body: { ok: true, kept: 1, end_confirmed: true } } : null),
    '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'We looked at item twelve.' } }) } });
  await r.s.start('openai');
  r.c.hear([user('Yes.')]);
  await flush(20);
  assert.equal(r.posts('/summary').length, 1);
  assert.deepEqual(r.c.instructions, ['Say only this, then stop: We looked at item twelve.']);
});

// ── B3: a slow summary is not ended by the session's own heartbeat ─────────────────────────────────────────────────────
await check('B3 no heartbeat while the summary is being made; the summary is still spoken', async () => {
  let release, closed = false;
  const r = rig({ routes: {
    '/transcript': () => (closed ? { status: 409, body: { error: 'session_ended' } } : null),
    '/summary': () => { closed = true; return new Promise((res) => { release = () => res({ status: 200, body: { ok: true, state: 'proposed', spoken: 'Slow summary.' } }); }); } } });
  await r.s.start('openai');
  const asked = r.s.sumUp(); await flush();
  const before = r.posts('/transcript').length;
  r.advance(40000); await flush();                       // two heartbeat intervals, inside the 45 s bound
  assert.equal(r.posts('/transcript').length, before, 'a heartbeat was sent after capture closed');
  assert.deepEqual(r.c.ended, []);
  release(); await asked; await flush();
  assert.deepEqual(r.c.instructions, ['Say only this, then stop: Slow summary.']);
});
await check('B3 a summary that does not answer in time ends honestly, once; a late answer is not spoken', async () => {
  let release;
  const r = rig({ routes: { '/summary': () => new Promise((res) => { release = () => res({ status: 200, body: { ok: true, state: 'proposed', spoken: 'Late.' } }); }) } });
  await r.s.start('openai');
  r.s.sumUp(); await flush();
  r.advance(45000); await flush(20);
  assert.deepEqual(r.c.ended, ['normal_end']); assert.equal(r.posts('/end').length, 1);
  assert.deepEqual(r.ended.at(-1), [MAY_APPEAR, { error: false }]);
  release(); await flush();
  assert.deepEqual(r.c.instructions, []); assert.equal(r.posts('/end').length, 1);
});
await check('B3 End during the summary closes the transport once and keeps the summary unspoken', async () => {
  let release;
  const r = rig({ routes: { '/summary': () => new Promise((res) => { release = () => res({ status: 200, body: { ok: true, state: 'proposed', spoken: 'Late.' } }); }) } });
  await r.s.start('openai');
  r.s.sumUp(); await flush();
  await r.s.end('user_ended'); await flush();
  release(); await flush();
  assert.deepEqual(r.c.ended, ['user_ended']); assert.equal(r.posts('/end').length, 1); assert.deepEqual(r.c.instructions, []);
});

// ── B4: what could not be kept is said, never claimed kept ──────────────────────────────────────────────────────────────
await check('B4 a turn lost to the network twice is said, and End does not claim everything was kept', async () => {
  const r = rig({ routes: { '/transcript': (body) => (body.events.length ? 'network' : null) } });
  await r.s.start('openai');
  r.c.hear([user('Lost words.')]); await flush();
  assert.ok(r.said.some(([w]) => w === D.NOT_KEPT), 'no warning when the turn was not kept');
  await r.s.end('user_ended'); await flush();
  assert.deepEqual(r.posts('/end')[0].body, { last_item_id: 't0', unacknowledged: ['t0'] });
  assert.deepEqual(r.ended.at(-1), [INCOMPLETE(1), { error: true }]);
});
await check('B4 a refused (422) turn is said and the end says some was not kept', async () => {
  const r = rig({ routes: { '/transcript': (body) => (body.events.length ? { status: 422, body: { error: 'time' } } : null) } });
  await r.s.start('openai');
  r.c.hear([user('Refused words.')]); await flush();
  assert.ok(r.said.some(([w]) => w === D.NOT_KEPT));
  await r.s.end('user_ended'); await flush();
  assert.deepEqual(r.ended.at(-1), [INCOMPLETE(1), { error: true }]);
});
await check('B4 a turn lost to the network once is sent again under the same id; kept then, the end is the ordinary one', async () => {
  let down = 1;
  const r = rig({ routes: { '/transcript': (body) => (body.events.length && down-- > 0 ? 'network' : null) } });
  await r.s.start('openai');
  r.c.hear([user('Words to keep.')]); await flush();
  await r.s.end('user_ended'); await flush();
  const sent = r.posts('/transcript').filter((x) => x.body.events.length);
  assert.equal(sent.length, 2); assert.equal(sent[1].body.events[0].item_id, 't0');       // two attempts, one id (§5.2)
  assert.deepEqual(r.posts('/end')[0].body, { last_item_id: 't0', unacknowledged: [] }); assert.deepEqual(r.ended.at(-1), [D.ENDED, { error: false }]);
});
await check('B4 a partial write that fails again is not kept, and the end says so', async () => {
  const r = rig({ routes: { '/transcript': (body) => (body.events.length ? { status: 409, body: { error: 'not_kept', kept: 0 } } : null) } });
  await r.s.start('openai');
  r.c.hear([user('One.'), { speaker: 'assistant', text: 'Two.', completed: true }]); await flush();
  await r.s.end('user_ended'); await flush();
  assert.deepEqual(r.posts('/end')[0].body.unacknowledged, ['t0', 't1']); assert.deepEqual(r.ended.at(-1), [INCOMPLETE(2), { error: true }]);
});
await check('B4 a session known incomplete says so in the thread, after a reload too', async () => {
  const doc = fakeDocument(), thread = doc.createElement('ol');
  const t = T.voiceThread({ doc, direct: DIRECT, thread: () => thread, mine: () => null, conversationId: () => 'c-1', post: async () => ({}), reveal: async () => {} });
  t.paint([{ session_id: SID, state: 'ended', started_at: '2026-10-28T10:00:00.000Z', line: null, summary: 'none', capture: 'incomplete' }]);
  assert.match(thread.querySelector(`[data-voice-line="${SID}"]`)?.textContent || '', /was not kept/);
});
await check('B4 edge: a leaving page sends End once, with one post of turns, and says incomplete for what did not fit', async () => {
  const r = rig({ routes: { '/transcript': (body) => (body.events.length ? new Promise(() => {}) : null) } });
  await r.s.start('openai');
  r.c.hear([user('first')]); await flush();
  const many = [user('first')];
  for (let i = 1; i < 30; i++) many.push({ speaker: i % 2 ? 'assistant' : 'user', text: `turn ${i}`, completed: true });
  r.c.hear(many); await flush();
  const before = r.calls.length;
  r.s.end('pagehide');
  const sent = r.calls.slice(before);
  assert.deepEqual(sent.map((x) => x.url.split('/').pop()), ['end']);
  assert.equal(sent[0].opts.keepalive, true);
  assert.equal(sent[0].body.events.length, 20); assert.equal(sent[0].body.events[0].item_id, 't0');
  assert.equal(sent[0].body.last_item_id, 't29'); assert.equal(sent[0].body.unacknowledged.length, 30);   // the server counts
});

// ── B5: Keep talking withdraws the proposal on the server too ──────────────────────────────────────────────────────────
await check('B5 Keep talking sends the withdrawal after the posts before it', async () => {
  const r = rig({ routes: { '/transcript': (body) => (body.events.length ? { status: 200, body: { ok: true, kept: 1, propose_end: true } } : null),
    '/keep_talking': () => ({ status: 200, body: { ok: true, proposal: 'withdrawn' } }) } });
  await r.s.start('openai');
  r.c.hear([user("We're done.")]); await flush();
  r.s.keepTalking(); await flush();
  const last = r.calls.at(-1);                                   // its own route now (Spec 165 §5.3), after the posts before it
  assert.ok(last.url.endsWith('/keep_talking')); assert.deepEqual(last.body, {});
  assert.ok(r.calls.indexOf(r.posts('/transcript').at(-1)) < r.calls.indexOf(last));
});

// ── B6: the spoken summary ends when its own response has been heard (Spec 165 §5.3: one tracked response) ───────────────
const SPOKEN = 'We looked at item twelve and the kiln seal.';
async function summarized(spoken = SPOKEN) {
  const r = rig({ routes: { '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken } }) } });
  await r.s.start('openai');
  await r.s.sumUp(); await flush();
  return r;
}
await check('B6 the summary ends the session only after its own response\'s audio has drained', async () => {
  const r = await summarized();
  r.c.options.onAssistantTurn('We looked at item twelve and the kiln seal.'); await flush();
  assert.deepEqual(r.c.ended, [], 'ended at generation completion, before playback drained');
  r.c.options.onPlaybackDrained('resp_sum'); await flush();
  assert.deepEqual(r.c.ended, ['normal_end']);
});
await check('B6 an unrelated response draining first does not close the summary', async () => {
  const r = await summarized();
  r.c.options.onPlaybackDrained('resp_other'); await flush();
  assert.deepEqual(r.c.ended, []);
  r.c.options.onPlaybackDrained('resp_sum'); await flush();
  assert.deepEqual(r.c.ended, ['normal_end']);
});
await check('B6 playback that never reports drained is let play its length at 15 characters a second plus 5 s', async () => {
  const r = await summarized();
  const bound = Math.ceil(SPOKEN.length / 15) * 1000 + 5000;
  r.advance(bound - 1); await flush(); assert.deepEqual(r.c.ended, []);
  r.advance(1); await flush(); assert.deepEqual(r.c.ended, ['normal_end']);
});
await check('B6 a long summary is let play to its own bound, past 45 s', async () => {
  const long = 'We went through the board item by item and agreed what comes next. '.repeat(12).trim();   // ~59 s to say
  const bound = Math.ceil(long.length / 15) * 1000 + 5000;
  const r = await summarized(long);
  r.advance(bound - 1); await flush();
  assert.ok(bound > 45000); assert.deepEqual(r.c.ended, []);
  r.advance(1); await flush();
  assert.deepEqual(r.c.ended, ['normal_end']);
});

// ── B7: a lost answer is not a failed action ────────────────────────────────────────────────────────────────────────────
await check('B7 an answer lost on the way back is asked again as the same call and its real result spoken', async () => {
  let n = 0;
  const r = rig({ routes: { '/consult': (body) => (++n === 1 ? 'network' : { status: 200, body: { ok: true, call_id: body.call_id, answered: true, saved: true, receipt: 'op-1', spoken: 'Saved.' } }) } });
  await r.s.start('openai');
  r.c.utter('Save a note: the kiln is hot');
  const out = await r.c.options.onFunctionCall({ call_id: 'call_1', name: 'save_note', arguments: {} });
  assert.equal(out.saved, true); assert.equal(out.spoken, 'Saved.');
  assert.deepEqual(r.posts('/consult').map((p) => p.body.call_id), ['call_1', 'call_1']);
});
await check('B7 a retry the server no longer knows (a restart) leaves the first attempt unknown, not failed', async () => {
  let n = 0;
  const r = rig({ routes: { '/consult': () => (++n === 1 ? 'network' : { status: 409, body: { ok: false, error: 'session ended' } }) } });
  await r.s.start('openai');
  r.c.utter('Run the nightly job.');
  const out = await r.c.options.onFunctionCall({ call_id: 'call_1', name: 'consult', arguments: {} });
  assert.equal(out.outcome, 'unknown'); assert.equal(out.spoken, UNKNOWN);
});
await check('B7 an answer that never comes back is unknown, never "did not go through"', async () => {
  const r = rig({ routes: { '/consult': () => 'network' } });
  await r.s.start('openai');
  r.c.utter('Run the nightly job.');
  const out = await r.c.options.onFunctionCall({ call_id: 'call_1', name: 'consult', arguments: {} });
  assert.equal(out.outcome, 'unknown'); assert.equal(out.spoken, UNKNOWN); assert.doesNotMatch(out.spoken, /did not go through/);
});

if (failures.length) {
  console.log(`${failures.length} of ${cases} review regressions FAILED:\n  ${failures.join('\n  ')}`);
  process.exit(1);
}
console.log(`${cases} Chunk B review regressions passed (B1–B7, capture edge); fake controller, fetch and timers only.`);
