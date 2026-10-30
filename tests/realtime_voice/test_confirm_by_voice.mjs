// Confirm by voice, v3 (V3_BUILD_BRIEF.md "The voice side"; DESIGN_confirm_by_voice_v3_simple.md): the app acts; the voice
// only talks. Through the real direct page (direct-voice-session.js), the real controller and the real OpenAI and xAI
// adapters, with fake HTTP (the server's decisions answered from a table), synthetic provider events and a fake clock.
// What is asserted is what the page sends: the utterances it posts, the questions it asks and when it tells the server they
// started, the action it sends after a yes (the request's own owner item and hash, never the yes's), the instructions it
// gives the voice on every path (none carries his words, except a read-back he asked for), and that no model call acts.
// No provider, microphone or network.
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { DIRECT, SID, flush } from '../js_helpers/direct_voice_rig.mjs';

const root = process.env.VOICE_REVIEW_ROOT ? pathToFileURL(process.env.VOICE_REVIEW_ROOT + '/') : new URL('../../', import.meta.url);
const { RealtimeVoiceController } = await import(new URL('core/realtime_voice/static/realtime-voice-controller.js', root));
const { XAIWebSocketAdapter } = await import(new URL('core/realtime_voice/static/adapters/xai-websocket-adapter.js', root));
const { OpenAIWebRTCAdapter } = await import(new URL('core/realtime_voice/static/adapters/openai-webrtc-adapter.js', root));
const D = await import(process.env.VOICE_SESSION_MODULE ? pathToFileURL(process.env.VOICE_SESSION_MODULE)
  : new URL('core/realtime_voice/static/direct-voice-session.js', root));

const PAGE_RESPONSES = { openai: true, xai: false };
const HOST = { ...DIRECT, providers: [{ provider: 'openai', label: 'OpenAI' }, { provider: 'xai', label: 'Grok' }],
  model_actions: { openai: false, xai: false }, page_responses: PAGE_RESPONSES };
const WORDS = 'make a note that the kiln is hot';           // his words: never in a line the app has the voice say
const failures = [];
let cases = 0;

async function run(provider, scenario, { routes = {} } = {}) {
  const real = Date.now; let now = 100000; Date.now = () => now;
  const a = provider === 'xai' ? new XAIWebSocketAdapter() : new OpenAIWebRTCAdapter();
  const frames = [];
  if (provider === 'openai') a._dc = { send: (f) => frames.push(JSON.parse(f)), close() {} };
  else a._ws = { send: (f) => frames.push(JSON.parse(f)), readyState: 1, close() {} };
  const posts = [], said = [], timers = new Map();
  let serial = 0, c = null;
  class C extends RealtimeVoiceController {
    constructor(options) { super(options); c = this; }
    async startSession() {
      this._adapter = a; this._sessionId = SID; this._state = 'active';
      this._owned = this._pageResponses && PAGE_RESPONSES[provider] === true;
      this._wireAdapterEvents();
      this._onSessionInfo({ voice_session_id: SID, provider, voice_notices: [] });
      this._onStateChange('active');
    }
  }
  const ui = new Proxy({ say: (text, o) => said.push([text, o || {}]) }, { get: (o, k) => o[k] || (() => {}) });
  const s = D.directVoice({ page: { conversation: { id: 'c-1' } }, direct: HOST, ui, headers: () => ({}), clock: () => now,
    fetch: async (url, opts = {}) => {
      const body = opts.body ? JSON.parse(opts.body) : null;
      if (opts.method === 'POST') posts.push({ url, body });
      const key = Object.keys(routes).sort((x, y) => y.length - x.length).find((k) => url.split('?')[0].endsWith(k));
      let out = key ? await routes[key](body) : null;
      if (!out) {
        out = url.endsWith('/consult') ? { ok: true, outcome: 'done', saved: true, spoken: 'Saved.' }
          : url.endsWith('/asked') ? { ok: true, state: 'asked', expires_in: 30 }
          : url.endsWith('/utterance') ? { ok: true, decision: 'chat' }
          : url.endsWith('/end') ? { ok: true, ended: true, capture: 'complete' } : { ok: true, kept: body?.events?.length || 0 };
      }
      return { ok: true, status: 200, json: async () => out };
    },
    loadController: async () => ({ RealtimeVoiceController: C }),
    setTimeout: (fn, ms) => { timers.set(++serial, { fn, due: now + ms }); return serial; }, clearTimeout: (id) => timers.delete(id),
    setInterval: () => ++serial, clearInterval: () => {} });
  const ev = (e) => a._handleServerEvent(e);
  const tagOf = () => frames.filter((f) => f.type === 'response.create').at(-1)?.response?.metadata?.minimoi_tag || null;
  const t = {
    a, c: () => c, s, frames, said, ev,
    posts: (end) => posts.filter((p) => p.url.split('?')[0].endsWith(end)).map((p) => p.body),
    consults: () => posts.filter((p) => p.url.endsWith('/consult')).map((p) => p.body),
    lines: () => frames.filter((f) => f.type === 'conversation.item.create' && f.item.type === 'message').map((f) => f.item.content[0].text),
    creates: () => frames.filter((f) => f.type === 'response.create').length,
    speak: async (id, text) => {           // one utterance of his: speech, its words, its end (and on OpenAI its commit)
      ev({ type: 'input_audio_buffer.speech_started', item_id: id });
      ev({ type: 'conversation.item.input_audio_transcription.completed', item_id: id, transcript: text, event_id: 'ev_' + id, status: 'completed' });
      ev({ type: 'input_audio_buffer.speech_stopped' });
      if (provider === 'openai') ev({ type: 'input_audio_buffer.committed', item_id: id });
      for (let i = 0; i < 6; i++) await flush();
    },
    created: (id) => ev({ type: 'response.created', response: { id } }),
    asked: (id) => ev({ type: 'response.created', response: { id, ...(tagOf() ? { metadata: { minimoi_tag: tagOf() } } : {}) } }),
    replied: (id, text) => {                // a reply's words, then its end
      ev({ type: provider === 'openai' ? 'response.output_audio_transcript.done' : 'response.output_audio_transcript.done', item_id: 'out_' + id,
        transcript: text, response_id: id, event_id: 'out_' + id });
      ev({ type: 'response.done', response: { id, status: 'completed', output: [] } });
    },
    call: (id) => ev({ type: 'response.done', response: { id, status: 'completed', output: [{ type: 'function_call', call_id: 'call_' + id, name: 'save_note', arguments: '{}' }] } }),
    async advance(ms) {
      const until = now + ms;
      for (;;) {
        const next = [...timers].filter(([, x]) => x.due <= until).sort((p, q) => p[1].due - q[1].due)[0];
        if (!next) break;
        timers.delete(next[0]); now = next[1].due; next[1].fn(); await flush();
      }
      now = until; await flush();
    },
  };
  try { await s.start(provider); await scenario(t); } finally { Date.now = real; }
}
async function check(name, fn) { cases++; try { await fn(); } catch (e) { failures.push(`${name}: ${e.message.split('\n')[0]}`); } }

// The server's decisions, as the utterance route answers them (tests/realtime_voice/test_confirm_by_voice.py proves the
// server's side): a request asks; a yes confirms it; the table names anything else.
const ASK = { decision: 'ask', action_id: 'a1', kind: 'note', owner_item_id: 't0', text_sha256: 'h1' };
const YES = { decision: 'confirm', action_id: 'a1', kind: 'note', owner_item_id: 't0', confirm_item_id: 't1', text_sha256: 'h1' };
const table = (rows) => ({ '/utterance': (body) => ({ ok: true, ...(rows[body.text] || { decision: 'chat' }) }) });
const contentFree = (t) => {
  for (const line of t.lines()) assert.ok(!line.toLowerCase().includes('kiln'), `a line carried his words: ${line}`);
};

// ── OpenAI: the page's own response for his item waits for its decision, then asks, content-free ─────────────────────────
await check('openai: request, ask, yes: one action, on the request\'s own item and hash, never the yes; every line content-free', () => run('openai', async (t) => {
  await t.speak('a', WORDS);
  assert.deepEqual(t.posts('/utterance'), [{ conversation_id: 'c-1', owner_item_id: 't0', text: WORDS }]);
  assert.deepEqual(t.lines(), [D.ASK_LINE]); assert.equal(t.creates(), 1);          // one response, the asking instruction
  t.asked('RA'); await flush(); await flush();
  assert.deepEqual(t.posts('/asked'), [{ after_item: 't0' }]);                      // the question started: the server is told
  t.replied('RA', 'Sure, just confirm for me.');
  await t.speak('b', 'Yes.');
  assert.deepEqual(t.consults(), [{ session_id: SID, call_id: 'confirm-a1', action_id: 'a1', confirm_item_id: 't1',
    owner_item_id: 't0', text_sha256: 'h1' }]);
  await flush(); await flush();
  assert.deepEqual(t.lines(), [D.ASK_LINE, 'Say this briefly, adding nothing: Saved.']);
  assert.equal(t.creates(), 2, 'a response was made for the yes');                  // none: only the outcome line
  contentFree(t);
}, { routes: table({ [WORDS]: ASK, 'Yes.': YES }) }));

await check('openai: a decision later than 1.5 s gets a normal response; a reply that asked no question gets the page\'s, once', () => {
  let release;
  return run('openai', async (t) => {
    await t.speak('a', WORDS);
    assert.equal(t.creates(), 0);                                                    // waiting for the decision
    await t.advance(1500);
    assert.equal(t.creates(), 1); assert.deepEqual(t.lines(), []);                   // a normal response at the bound
    t.asked('RA'); await flush();
    release(); for (let i = 0; i < 6; i++) await flush();
    assert.deepEqual(t.posts('/asked'), [{ after_item: 't0' }]);
    t.replied('RA', 'Done, I saved that.'); for (let i = 0; i < 6; i++) await flush();   // no question: the page asks
    assert.deepEqual(t.lines(), [D.ASK_LINE]);
    t.asked('RQ'); for (let i = 0; i < 6; i++) await flush();
    assert.equal(t.posts('/asked').length, 2);                                       // re-armed after the page's question
    contentFree(t);
  }, { routes: { '/utterance': () => new Promise((r) => { release = () => r({ ok: true, ...ASK }); }) } });
});

await check('openai: no is dropped with "All right."; a stray yes says nothing waits; nothing acts', () => run('openai', async (t) => {
  await t.speak('a', WORDS); t.asked('RA'); await flush(); t.replied('RA', 'Shall I go ahead?');
  await t.speak('b', 'No.');
  assert.deepEqual(t.lines(), [D.ASK_LINE, 'Say this briefly, adding nothing: All right.']);
  t.asked('RB'); t.replied('RB', 'All right.'); await flush();
  await t.speak('c', 'Yes.');
  assert.equal(t.lines().at(-1), `Say this briefly, adding nothing: ${D.NOTHING_WAITING}`);
  assert.deepEqual(t.consults(), []);
  contentFree(t);
}, { routes: table({ [WORDS]: ASK, 'No.': { decision: 'dropped', action_id: 'a1', kind: 'note', reason: 'no' },
  'Yes.': { decision: 'nothing_waiting' } }) }));

await check('openai: read it back reads his words as data, re-arms the question, and the yes after it acts once', () => run('openai', async (t) => {
  await t.speak('a', WORDS); t.asked('RA'); await flush(); t.replied('RA', 'Shall I go ahead?');
  await t.speak('b', 'Read it back.');
  assert.equal(t.lines().at(-1), D.readBackLine(WORDS));                            // his words, because he asked
  t.asked('RB'); for (let i = 0; i < 4; i++) await flush();
  assert.equal(t.posts('/asked').length, 2);                                         // the question re-armed after it
  t.replied('RB', 'It says: make a note that the kiln is hot. Shall I go ahead?');
  await t.speak('c', 'Yes.');
  assert.equal(t.consults().length, 1); assert.equal(t.consults()[0].owner_item_id, 't0');
}, { routes: table({ [WORDS]: ASK, 'Read it back.': { decision: 'read_back', action_id: 'a1', kind: 'note', owner_item_id: 't0', text: WORDS },
  'Yes.': { ...YES, confirm_item_id: 't2' } }) }));

await check('openai: thirty seconds with no answer: dropped, and the screen says not done', () => run('openai', async (t) => {
  await t.speak('a', WORDS); t.asked('RA'); for (let i = 0; i < 4; i++) await flush();
  assert.match(t.said.at(-1)[0], /^Note waiting for your yes: “make a note that the kiln is hot”/);   // on screen only
  await t.advance(29999); assert.notEqual(t.said.at(-1)[0], D.NOT_DONE_SCREEN);
  await t.advance(1); assert.equal(t.said.at(-1)[0], D.NOT_DONE_SCREEN);
  assert.deepEqual(t.consults(), []);
}, { routes: table({ [WORDS]: ASK }) }));

await check('openai: Check confirms the waiting action; the yes that follows sends nothing more', () => run('openai', async (t) => {
  await t.speak('a', WORDS); t.asked('RA'); await flush();
  await t.s.check(); await flush();
  const [tap] = t.consults();
  assert.match(tap.call_id, /^host-/); assert.equal(tap.action_id, 'a1'); assert.equal(tap.owner_item_id, 't0');
  await t.speak('b', 'Yes.');
  assert.equal(t.consults().length, 1);
}, { routes: table({ [WORDS]: ASK, 'Yes.': YES }) }));

await check('openai: the end\'s yes sums up and acts on nothing; an action\'s yes acts and never sums up', () => run('openai', async (t) => {
  await t.speak('a', WORDS); t.asked('RA'); await flush(); t.replied('RA', 'Shall I go ahead?');
  await t.speak('b', 'Yes.');
  assert.equal(t.consults().length, 1); assert.equal(t.posts('/summary').length, 0);
  for (let i = 0; i < 4; i++) await flush();
  t.asked('RL'); t.replied('RL', 'Saved.'); await flush();                           // the outcome line played
  await t.speak('c', "We're done.");
  assert.equal(t.lines().at(-1), D.END_LINE);
  t.asked('RC'); await flush();
  await t.speak('d', 'Yes please.'); for (let i = 0; i < 6; i++) await flush();
  assert.equal(t.consults().length, 1); assert.equal(t.posts('/summary').length, 1);
}, { routes: { ...table({ [WORDS]: ASK, 'Yes.': YES, "We're done.": { decision: 'ask', kind: 'end', action_id: 'e1', owner_item_id: 't2' },
  'Yes please.': { decision: 'confirm', kind: 'end', action_id: 'e1', confirm_item_id: 't3' } }),
  '/summary': () => ({ ok: true, state: 'proposed', spoken: 'We saved one note.' }) } }));

await check('openai: a late result says its outcome, never his words', () => run('openai', async (t) => {
  await t.speak('a', WORDS); t.asked('RA'); await flush(); t.replied('RA', 'Shall I go ahead?');
  await t.speak('b', 'Hello.');                                                      // its post carries the late result
  for (let i = 0; i < 4; i++) await flush();
  await t.advance(8000);
  assert.ok(t.lines().includes('Say this briefly, adding nothing: About what you asked earlier: Saved.'), JSON.stringify(t.lines()));
  contentFree(t);
}, { routes: { ...table({ [WORDS]: ASK }), '/transcript': (body) => ({ ok: true, kept: body.events.length,
  completed: [{ owner_item_id: 't0', tool: 'save_note', outcome: 'done', spoken: 'Saved. The kiln is hot.' }] }) } }));

await check('openai: refusals, pending and unknown are said in content-free words', () => run('openai', async (t) => {
  for (const n of ['a', 'b', 'c']) {
    await t.speak(n, `${WORDS} ${n}`);
    t.asked(`R${n}`); t.replied(`R${n}`, 'Hm.'); await flush();                     // his item's own reply
    await t.s.check(); for (let i = 0; i < 4; i++) await flush();
    t.asked(`L${n}`); t.replied(`L${n}`, 'Said.'); await flush();                   // the outcome line played
  }
  assert.ok(t.lines().includes(`Say this briefly, adding nothing: ${D.NOT_DONE}`));
  assert.ok(t.lines().includes(`Say this briefly, adding nothing: ${D.PENDING}`));
  assert.ok(t.lines().includes(`Say this briefly, adding nothing: ${D.UNKNOWN}`));
  contentFree(t);
}, { routes: { '/consult': (() => { let n = 0; return () => [{ ok: false, outcome: 'not_done', spoken: 'Not saved: "the kiln is hot" is too long.' },
  { ok: false, outcome: 'pending', spoken: 'Still saving the kiln note.' },
  { ok: false, outcome: 'unknown', spoken: 'Unknown: the kiln note.' }][n++]; })() } }));

// ── xAI: the provider answers by itself; the page marks its reply as the question, or asks itself ────────────────────────
await check('xai: its own reply after his words is the question; yes acts once on the request\'s item', () => run('xai', async (t) => {
  await t.speak('a', WORDS);
  t.created('X1'); for (let i = 0; i < 4; i++) await flush();                       // xAI's own reply to his request
  assert.deepEqual(t.posts('/asked'), [{ after_item: 't0' }]);
  t.replied('X1', 'Sure, just confirm for me?'); for (let i = 0; i < 4; i++) await flush();
  assert.deepEqual(t.lines(), [], 'the page asked again although the reply was a question');
  await t.speak('b', 'Yes.'); t.created('X2'); t.replied('X2', 'Okay.'); for (let i = 0; i < 6; i++) await flush();
  assert.deepEqual(t.consults(), [{ session_id: SID, call_id: 'confirm-a1', action_id: 'a1', confirm_item_id: 't1',
    owner_item_id: 't0', text_sha256: 'h1' }]);
  await t.advance(10000);
  assert.ok(t.lines().includes('Say this briefly, adding nothing: Saved.'));
  contentFree(t);
}, { routes: table({ [WORDS]: ASK, 'Yes.': YES }) }));

await check('xai: a reply that was no question gets the page\'s question, once, and the question is re-armed', () => run('xai', async (t) => {
  await t.speak('a', WORDS);
  t.created('X1'); for (let i = 0; i < 4; i++) await flush();
  t.replied('X1', 'Noted, all saved.'); for (let i = 0; i < 6; i++) await flush();   // evidence only: no "?"
  assert.deepEqual(t.lines(), [D.ASK_LINE]);
  t.created('P1'); for (let i = 0; i < 6; i++) await flush();                       // the page's question starts
  assert.equal(t.posts('/asked').length, 2);
  contentFree(t);
}, { routes: table({ [WORDS]: ASK }) }));

await check('xai: a dropped question (no reply within 4 s) gets one page question, then the screen', () => run('xai', async (t) => {
  const real = t.c().speakTracked.bind(t.c()), asks = [];
  t.c().speakTracked = (line) => { asks.push(line); real(line); return Promise.resolve(null); };   // it never starts
  await t.speak('a', WORDS);
  await t.advance(3999); assert.deepEqual(asks, []);
  await t.advance(1); for (let i = 0; i < 6; i++) await flush();
  assert.deepEqual(asks, [D.ASK_LINE]);                                              // one page question
  assert.equal(t.said.at(-1)[0], D.ASK_ON_SCREEN);                                    // then the screen asks
  assert.equal(t.posts('/asked').length, 1);                                          // still confirmable, by voice or tap
  await t.s.check(); await flush();
  assert.equal(t.consults()[0].action_id, 'a1');
}, { routes: table({ [WORDS]: ASK }) }));

await check('xai: a folded request plus yes is dropped: nothing acts, no note, and the screen says not done', () => run('xai', async (t) => {
  await t.speak('a', WORDS); t.created('X1'); for (let i = 0; i < 4; i++) await flush();   // overtaken before any audio
  await t.speak('b', `${WORDS}. Yes.`); for (let i = 0; i < 4; i++) await flush();
  assert.deepEqual(t.consults(), []);
  assert.equal(t.said.at(-1)[0], D.NOT_DONE_SCREEN);
}, { routes: table({ [WORDS]: ASK, [`${WORDS}. Yes.`]: { decision: 'dropped', action_id: 'a1', kind: 'note', reason: 'folded' } }) }));

// ── no model action, on either provider ──────────────────────────────────────────────────────────────────────────────
for (const provider of ['openai', 'xai']) {
  await check(`${provider}: a model's call does nothing: answered no_model_actions, no reply asked, nothing posted`, () => run(provider, async (t) => {
    await t.speak('a', WORDS);
    const before = t.creates();
    if (provider === 'openai') t.asked('RA'); else t.created('RA');
    t.call('RA'); for (let i = 0; i < 4; i++) await flush();
    const out = t.frames.filter((f) => f.type === 'conversation.item.create' && f.item.type === 'function_call_output');
    assert.equal(out.length, 1); assert.equal(JSON.parse(out[0].item.output).error, 'no_model_actions');
    assert.equal(t.creates(), before, 'a reply was asked for the call');
    assert.deepEqual(t.consults(), []);
  }));
  await check(`${provider}: End drops the waiting action: no question is marked after it`, () => run(provider, async (t) => {
    await t.speak('a', WORDS);
    await t.s.end('user_ended'); for (let i = 0; i < 4; i++) await flush();
    if (provider === 'openai') t.asked('RA'); else t.created('RA');
    for (let i = 0; i < 4; i++) await flush();
    assert.equal(t.posts('/asked').length, 0);
    assert.deepEqual(t.consults(), []);
  }, { routes: table({ [WORDS]: ASK }) }));
}

if (failures.length) {
  console.log(`${failures.length} of ${cases} confirm-by-voice page regressions FAILED:\n  ${failures.join('\n  ')}`);
  process.exit(1);
}
console.log(`${cases} confirm-by-voice page regressions passed (the real page, controller and adapters on OpenAI and xAI; fake HTTP).`);
