// MC voice on the direct path (UI_CONTRACT_chunk_B.md §2–§7): the shared session module (core/realtime_voice/static/
// direct-voice-session.js, Spec 165 §4.3; mc_voice_direct.js until A1) driven with a fake controller, a fake fetch and fake
// timers. The fake controller offers only the public surface (sessionId(), speak()), so a private read fails here.
// No DOM, network, provider or microphone.
import assert from 'node:assert/strict';
const D = await import(new URL('../core/realtime_voice/static/direct-voice-session.js', import.meta.url));
const flush = async (n = 6) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)); };

const SID = '7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f';
const BASE = '/guild-next/api/v1/mc/voice/direct/api/realtime-voice';
const DIRECT = { path: 'direct', providers: [{ provider: 'openai', label: 'OpenAI' }, { provider: 'xai', label: 'Grok' }], default_provider: 'openai',
  bootstrap_url: `${BASE}/bootstrap`, consult_url: `${BASE}/consult`, end_url: `${BASE}/session/{session_id}/end`,
  transcript_url: `${BASE}/session/{session_id}/transcript`, summary_url: `${BASE}/session/{session_id}/summary`,
  summary_decision_url: `${BASE}/session/{session_id}/summary/decision`, heartbeat_seconds: 20, persona: 'master_craftsman',
  locale: 'en-US', card_release: '0.1.18', person: { name: 'Master Craftsman', short: 'MC' },
  words: { no_conversation: 'Open a saved conversation before using voice.',                           // MC's own words (the server's)
    not_listening: 'Voice is on the record only. Go back on the record to use it.', invalid: 'Voice unavailable: open a saved conversation, on the record.' } };
const HEADERS = { 'Content-Type': 'application/json', 'X-CSRF-Token': 'csrf-synthetic', 'X-Record-Mode': 'on_record' };

function rig({ routes = {}, refuse = null, holdConnect = false, conversation = 'c-1', notices = ['openai voice is unset, using marin', 'xai voice is unset, using eve'] } = {}) {
  const calls = [], said = [], phases = [], live = [], elapsed = [], notice = [], propose = [], ended = [], reveals = [], checking = [];
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
  const route = (url) => {
    const keys = Object.keys(routes).sort((a, b) => b.length - a.length);
    return keys.find((k) => url.endsWith(k));
  };
  const fetch = async (url, opts) => {
    const body = JSON.parse(opts.body);
    calls.push({ url, opts, body });
    const key = route(url);
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
    constructor(options) { this.options = options; this._state = 'idle'; this.sid = null; this.instructions = []; this.ended = []; controller = this; }
    sessionId() { return this.sid; }
    state() { return this._state; }                                                        // public (item 11)
    speak(t) { this.instructions.push(t); return true; }
    speakTracked(t) { this.instructions.push(t); return Promise.resolve(`resp_${this.instructions.length}`); }   // §5.3
    async startSession(args) {
      this.args = args; this._state = 'requesting_mic';
      this.bootstrapHeaders = this.options.requestHeaders();
      if (refuse) { this._state = 'idle'; this.options.onFatalError({ reason: 'bootstrap_rejected', ...refuse }); return; }
      this.sid = SID;
      this.options.onSessionInfo({ provider: args.provider, voice_session_id: SID, voice_notices: notices });
      this._state = 'connecting'; this.options.onStateChange('connecting');
      if (!holdConnect) this.connect();
    }
    connect() { this._state = 'active'; this.options.onStateChange('active'); }
    endSession(reason) {
      if (['ending', 'ended'].includes(this._state)) return;
      this.ended.push(reason); this._state = 'ended'; this.options.onStateChange('ended');
      this.options.onFinalize({ turns: this.turns || [], partial_reason: reason, session_id: this.sid });
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
    hear(turns) { this.turns = turns.map((t) => ({ ...t })); this.options.onTranscriptUpdate(this.turns.map((t) => ({ ...t }))); }   // what was said
  }
  const ui = {
    say: (text, o = {}) => said.push([text, o]), phase: (p) => phases.push(p), live: (on) => live.push(on), elapsed: (t) => elapsed.push(t),
    notice: (t) => notice.push(t), propose: (on) => propose.push(on), ended: (words, o) => ended.push([words, o]), checking: (on) => checking.push(on),
  };
  const page = { conversation: conversation ? { id: conversation } : null };
  const s = D.directVoice({ page, direct: DIRECT, ui, headers: () => ({ ...HEADERS }), fetch, reveal: async () => { reveals.push(true); },
    loadController: async () => ({ RealtimeVoiceController: Controller }), clock: () => now,
    setTimeout: (fn, ms) => later(fn, ms), clearTimeout: (id) => timers.delete(id),
    setInterval: (fn, ms) => later(fn, ms, true), clearInterval: (id) => timers.delete(id) });
  return { s, calls, said, phases, live, elapsed, notice, propose, ended, reveals, checking, timers, advance, get c() { return controller; },
    posts: (end) => calls.filter((c) => c.url.endsWith(end)), now: () => now, tick: (ms) => { now += ms; } };
}
const at = (r, t) => new Date(t).toISOString();
let scenarios = 0;

// §3: the controller starts in direct mode with this conversation; the page sends no instructions; voice notices show quietly.
{
  const r = rig();
  assert.equal(await r.s.start('xai'), true);
  const c = r.c;
  assert.deepEqual(c.args, { provider: 'xai', persona: 'master_craftsman', scene: '', conversation_id: 'c-1' });
  assert.equal(c.options.bootstrapUrl, DIRECT.bootstrap_url);
  assert.equal(c.options.includeTranscriptAfterEnd, true);
  assert.equal(c.options.openingInstruction, null);
  assert.equal('managedBootstrap' in c.options, false);
  assert.deepEqual(c.bootstrapHeaders, HEADERS);                                         // §2 on the bootstrap too
  assert.deepEqual(r.notice, ['Default voice (eve); not yet chosen.']);
  assert.deepEqual(r.live, [true]);
  assert.equal(r.elapsed[0], '0s');
  r.advance(61000);
  assert.equal(r.elapsed.at(-1), '1m 01s');
  assert.equal(r.s.sessionId(), SID);
  scenarios++;
}
// Without a saved conversation: the words, and no controller.
{
  const r = rig({ conversation: null });
  assert.equal(await r.s.start('openai'), false);
  assert.equal(r.c, null);
  assert.deepEqual(r.ended, [['Open a saved conversation before using voice.', { error: true }]]);   // MC's words, from the server
  scenarios++;
}
// §3 table: every refusal has its words; nothing was minted, so nothing is ended or revealed.
for (const [status, detail, words] of [
  [401, 'not_signed_in', 'Voice unavailable: sign in again.'],
  [403, 'not_allowed', "Voice unavailable: this needs the owner's sign-in."],
  [403, 'csrf', 'Voice unavailable: this page is out of date. Reload it.'],
  [409, 'not_listening', 'Voice is on the record only. Go back on the record to use it.'],
  [422, 'invalid', 'Voice unavailable: open a saved conversation, on the record.'],
  [409, 'mc_turns_off', "Voice unavailable: Master Craftsman's turns are off here."],
  [404, 'not_found', 'Voice unavailable: that conversation is not available.'],
  [503, 'unavailable', 'Voice unavailable here right now.'],
  [400, 'unknown persona', 'Voice unavailable: this voice is not set up correctly.'],
  [400, 'Unknown provider: zz', 'Voice unavailable: this voice is not set up correctly.'],
  [429, 'rate limited', 'Too many voice starts in a minute. Wait a moment.'],
  [429, "Today's voice allowance is used up.", "Today's voice allowance is used up."],
  [503, "Today's voice allowance is used up.", "Today's voice allowance is used up."],
  [502, 'OpenAI refused the session', 'Voice could not start: the voice provider refused. Typing still works.'],
]) {
  const r = rig({ refuse: { status, detail } });
  await r.s.start('openai');
  await flush();
  assert.equal(r.said.at(-1)[0], words, `${status} ${detail}`);
  assert.equal(r.said.at(-1)[1].error, true);
  assert.equal(r.calls.length, 0);                                                        // no End, no reveal: nothing was started
  assert.equal(r.reveals.length, 0);
  assert.equal(r.ended.length, 1);
  assert(words.startsWith('Voice') || words.startsWith('Too many') || words.startsWith('Today'));
  scenarios++;
}
// §4: the bridge posts Robert's own last words with the provider's call id; whatever comes back is the output.
{
  let release;
  const r = rig({ routes: { '/consult': (body) => (body.call_id === 'call_slow' ? new Promise((res) => { release = () => res({ status: 200, body: { ok: true, spoken: 'Done.' } }); })
    : body.call_id === 'call_cap' ? { status: 429, body: { ok: false, error: 'cap', spoken: "I'll check the rest after we talk." } } : null) } });
  await r.s.start('openai');
  const c = r.c;
  c.utter('What is stuck on #12?');
  const out = await c.options.onFunctionCall({ call_id: 'call_1', name: 'consult', arguments: '{"question":"the model\'s words"}' });
  const consult = r.posts('/consult')[0];
  assert.deepEqual(consult.body, { session_id: SID, call_id: 'call_1', name: 'consult', owner_transcript_item: 'What is stuck on #12?',
    owner_item_id: 't0', arguments: { question: "the model's words" } });              // the owner turn it acts on: its capture id (B1)
  assert.deepEqual(consult.opts.headers, HEADERS);
  assert.equal(consult.opts.credentials, 'same-origin');
  assert.equal(out.spoken, 'Item 12 is in build.');
  const capped = await c.options.onFunctionCall({ call_id: 'call_cap', name: 'consult', arguments: 'not json' });
  assert.deepEqual(capped, { ok: false, error: 'cap', spoken: "I'll check the rest after we talk." });   // any status: as given
  assert.deepEqual(r.posts('/consult')[1].body.arguments, {});
  const saved = c.options.onFunctionCall({ call_id: 'call_slow', name: 'save_note', arguments: {} });
  await flush();
  assert.equal(r.posts('/consult')[2].body.name, 'save_note');
  r.advance(9999); assert.deepEqual(c.instructions, []);
  r.advance(1); assert.deepEqual(c.instructions, ['Still checking.']);                    // the holding line, once
  r.advance(20000); assert.deepEqual(c.instructions, ['Still checking.']);              // 30 s: inside the page's 35 s bound (F3)
  release(); assert.deepEqual(await saved, { ok: true, spoken: 'Done.' });
  scenarios++;
}
// Review B7: a consult the server never answered is asked once more as the same call (one effect: the server keeps its
// answer), and if that is not answered either, its outcome is unknown, never "did not go through". The holding line is
// cleared when it answers in time.
const UNKNOWN_RESULT = { ok: false, error: 'unknown', outcome: 'unknown', spoken: D.UNKNOWN };
{
  const r = rig({ routes: { '/consult': () => 'network' } });
  await r.s.start('openai');
  r.c.utter('Anything new?');
  assert.deepEqual(await r.c.options.onFunctionCall({ call_id: 'call_n', name: 'consult', arguments: {} }), UNKNOWN_RESULT);
  assert.deepEqual(r.posts('/consult').map((p) => [p.body.call_id, p.body.owner_item_id]), [['call_n', 't0'], ['call_n', 't0']]);
  r.advance(20000);
  assert.deepEqual(r.c.instructions, []);
  scenarios++;
}
// An unreadable answer: refused (4xx) is not done; accepted or the server failing is not known. A refused microphone says so.
for (const [status, expected, posts] of [[502, UNKNOWN_RESULT, 2], [200, UNKNOWN_RESULT, 1],
  [400, { ok: false, error: 'unavailable', outcome: 'not_done', spoken: D.NOT_DONE }, 1]]) {
  const r = rig({ routes: { '/consult': () => ({ status, body: null }) } });
  await r.s.start('openai');
  r.c.utter('Anything new?');
  assert.deepEqual(await r.c.options.onFunctionCall({ call_id: 'call_u', name: 'consult', arguments: {} }), expected, String(status));
  assert.equal(r.posts('/consult').length, posts, String(status));
  assert.match(D.startRefusal({ reason: 'microphone_denied' }), /^Voice unavailable: the microphone/);
  scenarios++;
}
// Check with MC: the host's call id, Robert's last words, and the answer said briefly, adding nothing.
{
  const r = rig();
  await r.s.start('openai');
  await r.s.check();
  assert.match(r.said.at(-1)[0], /Say something first/);
  assert.equal(r.posts('/consult').length, 0);
  r.c.utter('  Is the kiln job done?  ');
  await r.s.check();
  const body = r.posts('/consult')[0].body;
  assert.match(body.call_id, /^host-\d+$/);
  assert.equal(body.call_id.length <= 80, true);
  assert.equal(body.owner_transcript_item, 'Is the kiln job done?');
  assert.deepEqual(r.c.instructions, ['Say this briefly, adding nothing: Item 12 is in build.']);
  scenarios++;
}
// UI review #3: Check with MC runs one at a time (a double tap sends one consult) and never twice for the same turn of
// Robert's, whoever consulted it; saying it again is a new turn and may be checked.
{
  let release = null;
  const r = rig({ routes: { '/consult': () => (release === null ? new Promise((res) => { release = () => res({ status: 200, body: { ok: true, spoken: 'Done.' } }); }) : null) } });
  await r.s.start('openai');
  r.c.utter('Run the nightly job.');
  const first = r.s.check();
  const second = r.s.check();                                                             // the double tap
  await flush();
  assert.equal(r.posts('/consult').length, 1);
  assert.deepEqual(r.checking, [true]);                                                   // the button is disabled meanwhile
  release(); await first; await second;
  assert.deepEqual(r.checking, [true, false]);
  await r.s.check();                                                                      // the same turn again
  assert.equal(r.posts('/consult').length, 1);
  assert.deepEqual(r.said.at(-1), [D.ALREADY_CHECKED, { transient: true }]);
  r.c.utter('Run the nightly job.');                                         // said again: a new turn
  await r.s.check();
  assert.equal(r.posts('/consult').length, 2);
  r.c.utter('What is stuck on #12?');
  await r.c.options.onFunctionCall({ call_id: 'call_m', name: 'consult', arguments: {} });  // the model consulted it
  await r.s.check();
  assert.equal(r.posts('/consult').length, 3);
  assert.deepEqual(r.said.at(-1), [D.ALREADY_CHECKED, { transient: true }]);
  scenarios++;
}
// §5: each item posted once when it completes, keyed by place; an interrupted reply posted partial; a heartbeat every 20 s.
{
  const r = rig();
  await r.s.start('openai');
  const c = r.c, t0 = r.now();
  c.hear([{ speaker: 'user', text: 'What is', completed: false }]);
  r.tick(1500);
  c.hear([{ speaker: 'user', text: 'What is stuck on #12?', completed: true }]);
  await flush();
  let posts = r.posts('/transcript');
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0].body.events, [{ role: 'user', item_id: 't0', index: 0, text: 'What is stuck on #12?', partial: false, interrupted: false,
    started_at: at(r, t0), ended_at: at(r, t0 + 1500) }]);
  assert.deepEqual(posts[0].opts.headers, HEADERS);
  c.hear([{ speaker: 'user', text: 'What is stuck on #12?', completed: true }, { speaker: 'assistant', text: 'Let me', completed: false }]);
  await flush();
  assert.equal(r.posts('/transcript').length, 1);                                       // nothing new completed, nothing posted
  c.hear([{ speaker: 'user', text: 'What is stuck on #12?', completed: true }, { speaker: 'assistant', text: 'Let me check', completed: false },
    { speaker: 'user', text: 'Actually', completed: false }]);
  await flush();
  posts = r.posts('/transcript');
  assert.deepEqual(posts[1].body.events.map((e) => [e.item_id, e.role, e.text, e.partial, e.interrupted]), [['t1', 'assistant', 'Let me check', true, true]]);
  const before = r.posts('/transcript').length;
  r.advance(20000);
  await flush();
  const beat = r.posts('/transcript').slice(before);
  assert.deepEqual(beat.map((p) => p.body), [{ events: [] }]);                          // the heartbeat
  r.advance(40000); await flush();
  assert.equal(r.posts('/transcript').slice(before).length, 3);
  // End: what is still incomplete is posted partial (a user item, not interrupted) before End, then the thread is revealed.
  const order = r.calls.length;
  await r.s.end('user_ended');
  const tail = r.calls.slice(order);
  assert.deepEqual(tail.map((x) => x.url.split('/').pop()), ['transcript', 'end']);
  assert.deepEqual(tail[0].body.events.map((e) => [e.item_id, e.role, e.partial, e.interrupted]), [['t2', 'user', true, false]]);
  assert.deepEqual(tail[1].body, { last_item_id: 't2', unacknowledged: [] });           // the close's report (§5.2)
  assert.equal(tail[1].url, `${BASE}/session/${SID}/end`);
  assert.deepEqual(tail[1].opts.headers, HEADERS);
  assert.equal(r.reveals.length, 1);
  assert.deepEqual(r.ended.at(-1), [D.ENDED, { error: false }]);
  assert.deepEqual(r.c.ended, ['user_ended']);
  assert.equal(r.timers.size, 0);                                                        // no heartbeat or clock left running
  r.advance(60000); await flush();
  assert.equal(r.calls.length, order + 2);
  await r.s.end('user_ended');                                                           // a second End does nothing more
  assert.equal(r.calls.length, order + 2);
  scenarios++;
}
// An item still being said at End by MC is partial and interrupted; batches stay within 20 events.
{
  const r = rig();
  await r.s.start('openai');
  const turns = Array.from({ length: 45 }, (_, i) => ({ speaker: i % 2 ? 'assistant' : 'user', text: `turn ${i}`, completed: i < 44 }));
  r.c.hear(turns);
  await flush(20);
  const posts = r.posts('/transcript');
  assert.deepEqual(posts.map((p) => p.body.events.length), [20, 20, 4]);
  assert.deepEqual(posts.flatMap((p) => p.body.events.map((e) => e.item_id)), Array.from({ length: 44 }, (_, i) => `t${i}`));
  r.c.turns = turns.map((t, i) => (i === 44 ? { ...t, speaker: 'assistant' } : t));
  await r.s.end('user_ended');
  const last = r.posts('/transcript').at(-1).body.events;
  assert.deepEqual(last.map((e) => [e.item_id, e.role, e.partial, e.interrupted]), [['t44', 'assistant', true, true]]);
  scenarios++;
}
// The transcript route's refusals: not_kept retries the rest once, then says so; 422 is not retried; cap and session_ended stop.
{
  let tries = 0;
  const r = rig({ routes: { '/transcript': (body) => (body.events.length ? (++tries <= 2 ? { status: 409, body: { error: 'not_kept', kept: 1 } } : null) : null) } });
  await r.s.start('openai');
  r.c.hear([{ speaker: 'user', text: 'one', completed: true }, { speaker: 'assistant', text: 'two', completed: true }]);
  await flush(10);
  const posts = r.posts('/transcript');
  assert.deepEqual(posts.map((p) => p.body.events.map((e) => e.item_id)), [['t0', 't1'], ['t1']]);   // the rest, once
  assert.deepEqual(r.said.at(-1), [D.NOT_KEPT, { error: true }]);
  scenarios++;
}
{
  const r = rig({ routes: { '/transcript': (body) => (body.events.length ? { status: 422, body: { error: 'time' } } : null) } });
  await r.s.start('openai');
  r.c.hear([{ speaker: 'user', text: 'one', completed: true }]);
  await flush(10);
  assert.equal(r.posts('/transcript').length, 1);
  scenarios++;
}
for (const [status, error, words] of [[429, 'cap', D.CAP], [409, 'session_ended', null], [403, 'not_your_session', null]]) {
  const r = rig({ routes: { '/transcript': (body) => (body.events.length ? { status, body: { error } } : null) } });
  await r.s.start('openai');
  r.c.hear([{ speaker: 'user', text: 'one', completed: true }]);
  await flush(10);
  if (words) assert.deepEqual(r.said.at(-1), [words, { error: true }]);
  r.c.hear([{ speaker: 'user', text: 'one', completed: true }, { speaker: 'user', text: 'two', completed: true }]);
  r.advance(40000);
  await flush(10);
  assert.equal(r.posts('/transcript').length, 1, error);                                  // posting stopped, heartbeat too
  scenarios++;
}
// UI review #2: a session the server has ended or refuses is ended here too: the provider stops, End is sent, the reason stays.
for (const [status, error, words, beat] of [
  [409, 'session_ended', D.SERVER_ENDED.session_ended, false], [403, 'not_your_session', D.SERVER_ENDED.refused, false],
  [403, 'csrf', D.SERVER_ENDED.csrf, false], [403, 'not_allowed', D.SERVER_ENDED.refused, false],
  [409, 'mc_turns_off', "Voice stopped: Master Craftsman's turns are off here. What was said before is kept.", false],
  [409, 'session_ended', D.SERVER_ENDED.session_ended, true],
  [403, 'csrf', D.SERVER_ENDED.csrf, true]]) {
  const r = rig({ routes: { '/transcript': (body) => ((beat ? !body.events.length : body.events.length) ? { status, body: { error } } : null) } });
  await r.s.start('openai');
  if (beat) r.advance(20000); else r.c.hear([{ speaker: 'user', text: 'one', completed: true }]);
  await flush(10);
  assert.deepEqual(r.c.ended, ['server_ended'], `${error} beat=${beat}`);
  assert.deepEqual(r.said.at(-1), [words, { loud: true, error: true }]);
  assert.deepEqual(r.live.at(-1), false);                                                  // the live bar goes
  assert.equal(r.posts('/end').length, 1);
  assert.deepEqual(r.ended.at(-1), [null, { error: false }]);                             // the reason is not replaced
  assert.equal(r.timers.size, 0);
  scenarios++;
}
// A consult the server answers "session ended" or refuses (403): the answer goes back, then the voice is ended here too.
for (const [status, error, words] of [[409, 'session ended', D.SERVER_ENDED.session_ended], [403, 'not your session', D.SERVER_ENDED.refused],
  [403, 'csrf', D.SERVER_ENDED.csrf]]) {
  const r = rig({ routes: { '/consult': () => ({ status, body: { ok: false, error } }) } });
  await r.s.start('openai');
  r.c.utter('Anything new?');
  assert.deepEqual(await r.c.options.onFunctionCall({ call_id: 'call_e', name: 'consult', arguments: {} }), { ok: false, error });
  await flush(10);
  assert.deepEqual(r.c.ended, ['server_ended'], error);
  assert.deepEqual(r.said.at(-1), [words, { loud: true, error: true }]);
  scenarios++;
}
// The cap only stops posting: the voice goes on.
{
  const r = rig({ routes: { '/transcript': (body) => (body.events.length ? { status: 429, body: { error: 'cap' } } : null) } });
  await r.s.start('openai');
  r.c.hear([{ speaker: 'user', text: 'one', completed: true }]);
  await flush(10);
  assert.deepEqual(r.c.ended, []); assert.equal(r.posts('/end').length, 0);
  scenarios++;
}
{
  const r = rig({ routes: { '/transcript': (body) => (body.events.length > 1 ? { status: 413, body: { error: 'too_large' } } : null) } });
  await r.s.start('openai');
  r.c.hear([{ speaker: 'user', text: 'one', completed: true }, { speaker: 'assistant', text: 'two', completed: true }, { speaker: 'user', text: 'three', completed: true }]);
  await flush(20);
  assert.deepEqual(r.posts('/transcript').map((p) => p.body.events.map((e) => e.item_id)), [['t0', 't1', 't2'], ['t0', 't1'], ['t0'], ['t1'], ['t2']]);
  scenarios++;
}
// The tab going off the record ends the session.
{
  const r = rig({ routes: { '/transcript': (body) => (body.events.length ? { status: 409, body: { error: 'not_listening' } } : null) } });
  await r.s.start('openai');
  r.c.hear([{ speaker: 'user', text: 'one', completed: true }]);
  await flush(10);
  assert.deepEqual(r.c.ended, ['not_listening']);
  assert.equal(r.posts('/end').length, 1);
  scenarios++;
}
// pagehide: the last turns go inside End, with keepalive, in the same tick; nothing is revealed on a page that is leaving.
{
  const r = rig();
  await r.s.start('openai');
  r.c.turns = [{ speaker: 'user', text: 'Before I go', completed: false }];
  const before = r.calls.length;
  r.s.end('pagehide');                                                                   // not awaited: no reply comes now
  const sent = r.calls.slice(before);
  assert.deepEqual(sent.map((x) => x.url.split('/').pop()), ['end']);
  assert.equal(sent[0].opts.keepalive, true);
  assert.deepEqual(sent[0].body.events.map((e) => [e.item_id, e.text, e.partial]), [['t0', 'Before I go', true]]);
  await flush();
  assert.equal(r.reveals.length, 0);
  scenarios++;
}
// UI review #1: pagehide while a transcript post is still unanswered: End leaves at once, carrying that post's turns and the
// last ones (the in-flight post is cancelled with the page; the server keeps a turn sent twice once).
{
  const r = rig({ routes: { '/transcript': (body) => (body.events.length ? new Promise(() => {}) : null) } });
  await r.s.start('openai');
  r.c.hear([{ speaker: 'user', text: 'First thing', completed: true }]);
  await flush();
  r.c.hear([{ speaker: 'user', text: 'First thing', completed: true }, { speaker: 'assistant', text: 'Right', completed: true },
    { speaker: 'user', text: 'And then', completed: false }]);
  await flush();
  assert.equal(r.posts('/transcript').length, 1);                                        // the second waits on the first
  const before = r.calls.length;
  r.s.end('pagehide');
  const sent = r.calls.slice(before);
  assert.deepEqual(sent.map((x) => x.url.split('/').pop()), ['end']);
  assert.equal(sent[0].opts.keepalive, true);
  assert.deepEqual(sent[0].body.events.map((e) => e.item_id), ['t0', 't1', 't2']);
  await flush(10);
  assert.equal(r.posts('/transcript').length, 1);                                        // nothing queued is sent after it
  assert.equal(r.posts('/end').length, 1);
  scenarios++;
}
// UI review #1: an End already under way (its last post unanswered) when the page goes away is sent again with keepalive.
{
  let hang = false;
  const r = rig({ routes: { '/transcript': (body) => (hang && body.events.length ? new Promise(() => {}) : null) } });
  await r.s.start('openai');
  r.c.turns = [{ speaker: 'user', text: 'Last words', completed: false }];
  hang = true;
  r.s.end('user_ended');
  await flush();
  assert.equal(r.posts('/end').length, 0);                                                // waiting on the last post
  r.s.end('pagehide');
  const ends = r.posts('/end');
  assert.equal(ends.length, 1);
  assert.equal(ends[0].opts.keepalive, true);
  assert.deepEqual(ends[0].body.events.map((e) => [e.item_id, e.text]), [['t0', 'Last words']]);
  r.s.end('pagehide');                                                                   // once
  assert.equal(r.posts('/end').length, 1);
  scenarios++;
}
// An End that has finished is not sent again when the page then goes away.
{
  const r = rig();
  await r.s.start('openai');
  await r.s.end('user_ended');
  await flush();
  r.s.end('pagehide');
  assert.equal(r.posts('/end').length, 1);
  scenarios++;
}
// The provider dropping: the error stays, the session is ended on the server and revealed.
{
  const r = rig();
  await r.s.start('openai');
  r.c.options.onFatalError({ reason: 'provider_failure', detail: 'x' });
  r.c.endSession('fatal_error');
  await flush(10);
  assert.match(r.said.at(-1)[0], /^Voice stopped: the voice connection ended/);
  assert.equal(r.posts('/end').length, 1); assert.equal(r.reveals.length, 1);
  assert.deepEqual(r.ended.at(-1), [null, { error: false }]);                            // the error line is not replaced
  scenarios++;
}
// End pressed while the provider is still connecting waits for the connection (the adapters take the microphone then).
{
  const r = rig({ holdConnect: true });
  await r.s.start('openai');
  const done = r.s.end('user_ended');
  assert.deepEqual(r.c.ended, []);
  r.c.connect();
  await done;
  assert.deepEqual(r.c.ended, ['user_ended']);
  assert.equal(r.posts('/end').length, 1);
  const r2 = rig({ holdConnect: true });
  await r2.s.start('openai');
  const done2 = r2.s.end('user_ended');
  r2.advance(15000);
  await done2;
  assert.deepEqual(r2.c.ended, ['user_ended']); assert.equal(r2.posts('/end').length, 1);
  scenarios++;
}
// §7: "we're done" offers Sum up / Keep talking and ends nothing; Keep talking withdraws it.
{
  let n = 0;
  const r = rig({ routes: { '/transcript': (body) => (body.events.length && ++n === 1 ? { status: 200, body: { ok: true, kept: 1, propose_end: true, prompt: 'Shall I sum up?' } } : null) } });
  await r.s.start('openai');
  r.c.hear([{ speaker: 'user', text: "We're done.", completed: true }]);
  await flush(10);
  assert.deepEqual(r.propose, [false, true].slice(1));
  assert.equal(r.s.proposing(), true);
  assert.equal(r.posts('/end').length, 0); assert.deepEqual(r.c.ended, []);
  r.s.keepTalking();
  assert.equal(r.s.proposing(), false); assert.deepEqual(r.propose, [true, false]);
  scenarios++;
}
// A spoken yes: the summary is asked for this conversation, spoken once ("say only this"), and the session ends after it.
{
  const r = rig({ routes: {
    '/transcript': (body) => (body.events.length ? { status: 200, body: { ok: true, kept: 1, end_confirmed: true } } : null),
    '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'We looked at item twelve.', written: '[Voice summary proposal] …', covers: {} } }),
  } });
  await r.s.start('openai');
  r.c.hear([{ speaker: 'user', text: 'Yes.', completed: true }]);
  await flush(10);
  const ask = r.posts('/summary')[0];
  assert.equal(ask.url, `${BASE}/session/${SID}/summary`);
  assert.deepEqual(ask.body, { conversation_id: 'c-1', last_item_id: 't0', unacknowledged: [] });   // the close's report (§5.2)
  assert.deepEqual(ask.opts.headers, HEADERS);
  assert.deepEqual(r.c.instructions, ['Say only this, then stop: We looked at item twelve.']);
  assert.deepEqual(r.c.ended, []);
  r.c.options.onAssistantTurn('We looked at item twelve.');
  await flush(10);
  assert.deepEqual(r.c.ended, []);                                                        // generated, not yet heard (§5.3)
  r.c.options.onPlaybackDrained('resp_1');                                                // its own response has played out
  await flush(10);
  assert.deepEqual(r.c.ended, ['normal_end']);
  assert.equal(r.posts('/end').length, 1); assert.equal(r.reveals.length, 1);
  assert.equal(r.posts('/summary').length, 1);
  scenarios++;
}
// The Sum up button does the same; an unavailable summary still ends and reveals, and says "Summary unavailable."
{
  const r = rig({ routes: { '/summary': () => ({ status: 200, body: { ok: false, error: 'busy', state: 'unavailable', line: 'Summary unavailable.' } }) } });
  await r.s.start('openai');
  await r.s.sumUp();
  await flush(10);
  assert.deepEqual(r.c.ended, ['normal_end']); assert.equal(r.posts('/end').length, 1);
  assert.deepEqual(r.ended.at(-1), [D.SUMMARY_UNAVAILABLE, { error: false }]);
  scenarios++;
}
// A spoken summary that never finishes still ends the session.
{
  const r = rig({ routes: { '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'Short.', written: 'w' } }) } });
  await r.s.start('openai');
  await r.s.sumUp();
  r.advance(45000);
  await flush(10);
  assert.deepEqual(r.c.ended, ['normal_end']);
  scenarios++;
}
console.log(`${scenarios} direct-voice session scenarios passed (bridge, holding line, capture, heartbeat, End, refusals, summary); no network, provider or microphone.`);
