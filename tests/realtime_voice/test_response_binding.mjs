// A voice request acts on the words Robert said for it, by construction (Spec 165 §5.1 and §7 as amended for Robert's C2
// decision of 28 Oct 2026, "A: tie by construction"; A1_BINDING_BRIEF.md I1–I6). The real controller and the real adapters,
// on synthetic provider events and a fake clock; no provider, microphone or network.
//
// On OpenAI (eligible: the bootstrap says page_responses) the page creates every response that may act, one for each
// committed owner item, named by input_audio_buffer.committed's item_id; one page request is in flight at a time; a response
// is the page's by its echoed tag or as the one request in flight; a call's words are its owner item's transcript, by the
// provider's item id; a response cancelled, cut short or overtaken by his next words acts on nothing. On xAI (not eligible
// until A4) the session has no action tool (tests/realtime_voice/test_direct_binding.py), and here no call can be tied to any
// of his words at all.
//
// Every event order the review rounds found (M1, V-M1's three orders and the hold variant, LP1, LP2, F1's two cases, R3-S1
// F, G and H, W N4's two cases) is a regression of the invariants: on OpenAI the right owner or nothing; on xAI nothing.
// They replace the inference cases of A1's fix round (one-to-one spans, holds, order mode, the 3 s mark), which encoded the
// rules this pass removes. The event orders are the providers' documented ones; none was observed live.
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

const root = process.env.VOICE_REVIEW_ROOT ? pathToFileURL(process.env.VOICE_REVIEW_ROOT + '/') : new URL('../../', import.meta.url);
const { XAIWebSocketAdapter } = await import(new URL('core/realtime_voice/static/adapters/xai-websocket-adapter.js', root));
const { OpenAIWebRTCAdapter } = await import(new URL('core/realtime_voice/static/adapters/openai-webrtc-adapter.js', root));
const { RealtimeVoiceController } = await import(new URL('core/realtime_voice/static/realtime-voice-controller.js', root));
const failures = [];
let cases = 0;
const tick = () => new Promise((r) => setImmediate(r));

// A fake clock for Date.now and the controller's timers, installed for one case.
function clock() {
  const real = { now: Date.now, setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout };
  let now = 1_000_000, serial = 0;
  const timers = new Map();
  Date.now = () => now;
  globalThis.setTimeout = (fn, ms) => { const id = ++serial; timers.set(id, { fn, due: now + (Number(ms) || 0) }); return id; };
  globalThis.clearTimeout = (id) => { timers.delete(id); };
  return {
    pass(ms) {
      const until = now + ms;
      for (;;) {
        const next = [...timers].filter(([, t]) => t.due <= until).sort((x, y) => x[1].due - y[1].due)[0];
        if (!next) break;
        timers.delete(next[0]); now = next[1].due; next[1].fn();
      }
      now = until;
    },
    restore() { Date.now = real.now; globalThis.setTimeout = real.setTimeout; globalThis.clearTimeout = real.clearTimeout; },
  };
}
async function check(name, fn) {
  cases++;
  const time = clock();
  try { await fn(time); } catch (e) { failures.push(`${name}: ${e.message.split('\n')[0]}`); } finally { time.restore(); }
}

// One session on a real adapter, the controller wired as the direct page wires it (pageResponses). `owned`: the bootstrap
// said page_responses (OpenAI; never xAI until A4). The provider's frames are captured where the adapter writes them.
function session(provider, { owned = provider === 'openai', answer = null, drained = null } = {}) {
  const a = provider === 'openai' ? new OpenAIWebRTCAdapter() : new XAIWebSocketAdapter();
  const frames = [], calls = [], unbound = [];
  if (provider === 'openai') a._dc = { send: (f) => frames.push(JSON.parse(f)) };
  else a._ws = { send: (f) => frames.push(JSON.parse(f)), readyState: 1, close() {} };
  const c = new RealtimeVoiceController({
    pageResponses: true, onUnbound: (info) => unbound.push(info),
    onFunctionCall: async (call) => { calls.push(call); return answer ? answer(call) : { ok: true }; },
    ...(drained ? { onPlaybackDrained: drained } : {}),
  });
  c._adapter = a; c._state = 'active'; c._wireAdapterEvents(); c._owned = owned;   // as startSession sets it from the bootstrap
  const ev = (e) => a._handleServerEvent(e);
  const creates = () => frames.filter((f) => f.type === 'response.create');
  const tagOf = (f) => f?.response?.metadata?.minimoi_tag || null;
  const s = {
    a, c, frames, calls, unbound, ev, creates,
    bound: (i) => c.inputOf(calls[i]),
    speech: (item = null) => ev({ type: 'input_audio_buffer.speech_started', ...(item ? { item_id: item } : {}) }),
    stopped: () => ev({ type: 'input_audio_buffer.speech_stopped' }),
    commit: (item) => ev({ type: 'input_audio_buffer.committed', ...(item ? { item_id: item } : {}) }),
    utter(item, { commit = true } = {}) { s.speech(); s.stopped(); if (commit) s.commit(item); },
    said: (item, text) => ev({ type: 'conversation.item.input_audio_transcription.completed', item_id: item, transcript: text, event_id: `ev_${item}_${text.length}` }),
    // The provider starts a response. By default the one the page asked for last, its tag echoed on OpenAI (response.metadata;
    // not verified live, so `echo: false` checks the single-request rule alone). `tag: null` and no request: one it was not asked for.
    start(id, { echo = provider === 'openai', tag = tagOf(creates().at(-1)) } = {}) {
      ev({ type: 'response.created', response: { id, ...(echo && tag ? { metadata: { minimoi_tag: tag } } : {}) } });
    },
    done(id, { status = 'completed', call = null, name = 'save_note' } = {}) {
      ev({ type: 'response.done', response: { id, status,
        output: call ? [{ type: 'function_call', call_id: call, name, arguments: '{}' }] : [] } });
    },
  };
  return s;
}
const words = (s, i) => { const b = s.bound(i); return b && b.state === 'bound' ? b.text : b && b.state; };
const ALL = [['OpenAI', 'openai'], ['xAI', 'xai']];

// ── I1, I2: only page-created responses act; one page request in flight; exact correlation ────────────────────────────
await check('latency (OpenAI): the page sends its response.create in the same tick as the commit, tagged, with no wait', async () => {
  const s = session('openai');
  s.speech(); s.stopped();
  assert.equal(s.creates().length, 0, 'a response asked for before his input was committed');
  s.commit('item_A');                                                  // synchronous: nothing awaited before this line
  assert.equal(s.creates().length, 1);
  assert.deepEqual(s.frames.at(-1), { type: 'response.create', response: { metadata: { minimoi_tag: 'minimoi-1' } } });
});
await check('I1 (OpenAI): his committed item\'s response binds its call to that item\'s words, pending until they come', async () => {
  const s = session('openai');
  s.utter('item_A'); s.start('R_A');
  s.done('R_A', { call: 'c1' }); await tick();
  assert.deepEqual(s.bound(0), { state: 'pending' });
  s.said('item_A', 'Make a note that the kiln is hot');
  assert.deepEqual(s.bound(0), { state: 'bound', index: 0, text: 'Make a note that the kiln is hot' });
});
for (const echo of [true, false]) {
  await check(`I2 (OpenAI${echo ? ', tag echoed' : ', no tag echoed'}): a provider response the page did not ask for binds nothing, and is logged`, async () => {
    const s = session('openai');
    s.start('R_stray', { tag: null });                                 // none in flight
    s.done('R_stray', { call: 'c_stray' }); await tick();
    assert.equal(s.bound(0), null);
    assert.deepEqual(s.unbound.map((u) => u.reason), ['unrequested']);
    s.start('F_stray', { echo }); s.done('F_stray');                    // its refusal's follow-up ("say it again")
    s.utter('item_A'); s.start('R_A', { echo });                        // his own, as the one request in flight
    s.start('R_extra', { echo: false });                                // one too many
    s.done('R_extra', { call: 'c_extra' }); s.done('R_A', { call: 'c_A' }); await tick();
    s.said('item_A', 'Check the board');
    assert.equal(s.bound(1), null, 'one response too many took his words');
    assert.equal(words(s, 2), 'Check the board');
    assert.ok(s.unbound.some((u) => u.reason === 'one_too_many'));
  });
}
await check('I2 (OpenAI): a response carrying another request\'s tag is not the one in flight', async () => {
  const s = session('openai');
  s.utter('item_A');
  s.start('R_old', { tag: 'minimoi-99' });                             // a late start of an expired or foreign request
  s.done('R_old', { call: 'c_old' }); await tick();
  s.start('R_A'); s.done('R_A', { call: 'c_A' }); await tick();
  s.said('item_A', 'Check the board');
  assert.equal(s.bound(0), null); assert.equal(words(s, 1), 'Check the board');
});
await check('I2 (OpenAI): once the provider has echoed a tag, an untagged start is not the request in flight', async () => {
  const s = session('openai');
  s.utter('item_A'); s.start('R_A'); s.done('R_A');                   // the tag echoed: the provider shows it echoes them
  s.utter('item_B');
  s.start('R_untagged', { echo: false });                             // a response the page did not ask for, while B's is in flight
  s.start('R_B');                                                     // B's own, its tag echoed
  s.done('R_untagged', { call: 'c_u' }); s.done('R_B', { call: 'c_B' }); await tick();
  s.said('item_B', 'Check the board');
  assert.equal(s.bound(0), null, 'an untagged response took the request in flight');
  assert.equal(words(s, 1), 'Check the board');
  assert.ok(s.unbound.some((u) => u.reason === 'untagged' && u.response_id === 'R_untagged'));
});
await check('new: a holding line and a summary wait their turn behind an owner response, each then known as its own', async () => {
  const s = session('openai');
  s.utter('item_A'); s.start('R_A');
  assert.equal(s.c.speak('Say this briefly, adding nothing: Still checking.'), true);
  let summary = null;
  s.c.speakTracked('Say only this, then stop: we checked the board.').then((id) => { summary = id; });
  assert.equal(s.creates().length, 1, 'a line was sent while his response was in flight');
  s.done('R_A');
  assert.equal(s.creates().length, 2);                                  // the line, now
  assert.equal(s.frames.at(-2).item.content[0].text, 'Say this briefly, adding nothing: Still checking.');
  s.start('R_line');
  assert.equal(s.creates().length, 2, 'the summary was sent while the line was in flight');
  s.done('R_line');
  assert.equal(s.creates().length, 3);
  assert.equal(s.frames.at(-2).item.content[0].text, 'Say only this, then stop: we checked the board.');
  s.start('R_summary'); await tick();
  assert.equal(summary, 'R_summary');
});
await check('new (OpenAI): a page request whose response never starts is let go at its bound; his next one goes', async (time) => {
  const s = session('openai');
  s.utter('item_A');                                                   // its response never starts
  s.c.speak('Say this briefly, adding nothing: Still checking.');
  assert.equal(s.creates().length, 1);
  time.pass(9999); assert.equal(s.creates().length, 1);
  time.pass(1); assert.equal(s.creates().length, 2, 'the line still waits behind a request that never started');
  s.start('R_line'); s.done('R_line');
  s.start('R_late', { tag: 'minimoi-1' }); s.done('R_late', { call: 'c_late' }); await tick();   // the dead request, late
  s.said('item_A', 'Make a note that the kiln is hot');
  assert.equal(s.bound(0), null, 'a request past its bound was given an owner');
});

// ── Ordinary speech on OpenAI: every one-to-one case still acts on its own words ─────────────────────────────────────
for (const echo of [true, false]) {
  await check(`ordinary (OpenAI${echo ? '' : ', no tag echoed'}): three requests in a row, each acting on its own words`, async () => {
    const s = session('openai');
    const said = ['What is stuck on #12?', 'Make a note that the kiln is hot', 'Check the board'];
    said.forEach((text, i) => {
      s.utter(`item_${i}`); s.start(`R_${i}`, { echo });
      if (i === 1) s.said(`item_${i}`, text);                          // the words before the call, or after it
      s.done(`R_${i}`, { call: `c_${i}` });
      if (i !== 1) s.said(`item_${i}`, text);
    });
    await tick();
    for (let i = 0; i < 3; i++) { s.start(`F_${i}`, { echo }); s.done(`F_${i}`); }   // each result's follow-up
    assert.deepEqual(s.calls.map((_, i) => words(s, i)), said);
    assert.equal(s.creates().length, 6);                               // three replies and three follow-ups, one at a time
  });
}
await check('ordinary (OpenAI): a transcript delta before the commit, then the completed words', async () => {
  const s = session('openai');
  s.speech(); s.ev({ type: 'conversation.item.input_audio_transcription.delta', item_id: 'item_A', delta: 'Make a note', event_id: 'd1' });
  s.stopped(); s.commit('item_A'); s.start('R_A'); s.done('R_A', { call: 'c_A' }); await tick();
  assert.deepEqual(s.bound(0), { state: 'pending' });
  s.said('item_A', 'Make a note that the glaze is ready');
  assert.equal(words(s, 0), 'Make a note that the glaze is ready');
});
await check('ordinary (OpenAI): a reply that only talks, then his next request: it acts on the next one\'s words', async () => {
  const s = session('openai');
  s.utter('item_A'); s.said('item_A', 'Good morning'); s.start('R_A'); s.done('R_A');
  s.utter('item_B'); s.said('item_B', 'Save a note: the kiln is hot'); s.start('R_B'); s.done('R_B', { call: 'c_B' }); await tick();
  assert.equal(words(s, 0), 'Save a note: the kiln is hot');
});

for (const echo of [true, false]) {
  await check(`new (OpenAI${echo ? ', tag echoed' : ', no tag echoed'}): a request let go before it started, then a start: never the wrong owner`, async (time) => {
    const s = session('openai');
    s.utter('item_A'); s.said('item_A', 'Save a note: first request');   // A's response never starts in time
    time.pass(10000);
    s.utter('item_B'); s.said('item_B', 'Check the board');
    assert.equal(s.creates().length, 2);
    // The next start could be A's late one or B's own: with the tag echoed it is told; without, it answers no one.
    s.start('R_next', { echo, tag: echo ? s.creates()[1].response.metadata.minimoi_tag : null });
    s.done('R_next', { call: 'c_next' }); await tick();
    if (echo) assert.equal(words(s, 0), 'Check the board'); else assert.equal(s.bound(0), null);
    assert.notEqual(words(s, 0), 'Save a note: first request');
    if (!echo) assert.ok(s.unbound.some((u) => u.reason === 'after_lost_request'));
    s.start('F_next', { echo }); s.done('F_next');                       // its result's follow-up
    s.utter('item_C'); s.start('R_C', { echo }); s.done('R_C', { call: 'c_C' }); await tick();
    s.said('item_C', 'Check the kiln');
    // B S1: without the echo, no untagged start binds for the rest of the session (A's could still start); tagged, exact.
    if (echo) assert.equal(words(s, 1), 'Check the kiln'); else assert.equal(s.bound(1), null);
  });
  // B S1 (the independent review of 8eb30a10; its probe, asserting the correct behaviour): A's request is let go at 10 s;
  // B's own untagged start comes; C's request goes; then A's reply starts at last, carrying the save_note chosen for A. It
  // must never act on C's words. With the tag echoed the same order binds exactly.
  await check(`B S1 (OpenAI${echo ? ', tag echoed' : ', no tag echoed'}): a lost request's late reply never takes a later request's owner`, async (time) => {
    const s = session('openai');
    s.utter('a'); s.said('a', 'Save a note that the kiln is hot.');
    time.pass(10500);                                                   // #1 is let go, never started
    s.utter('b'); s.said('b', 'Hello again.');
    const tagB = s.creates()[1].response.metadata.minimoi_tag;
    s.start('RB', { echo, tag: tagB }); s.done('RB');                    // #2's own start
    s.utter('c'); s.said('c', 'Check the board.');
    const tagA = s.creates()[0].response.metadata.minimoi_tag;
    s.start('RA_late', { echo, tag: tagA }); s.done('RA_late', { call: 'c_late', name: 'save_note' }); await tick();
    assert.notEqual(words(s, 0), 'Check the board.', "the lost request's reply acted on C's words");
    assert.equal(s.bound(0), null);
    if (echo) {                                                         // the tag tells: C's own reply binds C
      s.start('RC'); s.done('RC', { call: 'c_C' }); await tick();       // #3's own reply, still the one in flight
      assert.equal(words(s, 1), 'Check the board.');
    }
  });
}

await check('new (OpenAI): a commit after the transport closed asks nothing and throws nothing', async () => {
  const s = session('openai');
  s.a._dc = { send: () => { throw new Error('closed'); } };
  s.utter('item_A');                                                   // no exception out of the provider's event
  s.said('item_A', 'Check the board');
  s.a._dc = { send: (f) => s.frames.push(JSON.parse(f)) };
  s.utter('item_B');                                                   // the next commit asks for its own
  assert.equal(s.creates().length, 1);
});

// B N6 (the independent review of 8eb30a10): two properties the rewrite left without a case.
await check('B N6 (OpenAI): a call from the page\'s own line reply, and from its follow-up, binds no owner', async () => {
  const s = session('openai');
  s.utter('item_A'); s.said('item_A', 'What time is it'); s.start('R_A'); s.done('R_A');
  assert.equal(s.c.speak('Say this briefly, adding nothing: it is noon.'), true);
  s.start('R_line'); s.done('R_line', { call: 'c_line' }); await tick();
  s.start('R_line_f'); s.done('R_line_f', { call: 'c_line_f' }); await tick();
  assert.equal(s.bound(0), null); assert.equal(s.bound(1), null);
});
await check('B N6 (OpenAI): a call with no response id binds nothing', async () => {
  const s = session('openai');
  s.utter('item_A'); s.said('item_A', 'Check the board'); s.start('R_A'); s.done('R_A');
  assert.equal(s.c.inputOf({ call_id: 'x' }), null);
  assert.equal(s.c.inputOf({ call_id: 'y', response_id: null }), null);
  assert.equal(s.c.inputOf({ call_id: 'z', response_id: 'R_unknown' }), null);
});
// B N5: a started reply with no sign of life is let go after 20 s (never assigned); one whose output keeps coming is not.
await check('B N5 (OpenAI): a started reply with no output for 20 s is let go; output keeps it in flight', async (time) => {
  const s = session('openai');
  s.utter('item_A'); s.start('R_A');
  s.c.speak('Say this briefly, adding nothing: Still checking.');
  time.pass(15000);
  s.ev({ type: 'response.output_audio_transcript.delta', item_id: 'out_A', delta: 'Sure, ', event_id: 'o1' });   // alive
  time.pass(19999); assert.equal(s.creates().length, 1, 'a reply still speaking was let go');
  time.pass(1); assert.equal(s.creates().length, 2, 'a silent reply held the page past 20 s');
  s.done('R_A', { call: 'c_A' }); await tick();                       // its late end: its owner is still its own
  s.said('item_A', 'Check the board');
  assert.equal(words(s, 0), 'Check the board');
  assert.ok(s.unbound.some((u) => u.reason === 'unfinished' && u.owner === 'item_A'));
});
// B S2: where the provider creates its own replies (xAI), a page line's reply is known only when that is proven.
// Since LV3 the page holds its request while his turn's reply may still start, so the B S2 order cannot arise there: the
// provider's reply to "Yes." is its own, and the summary's start after it is known. The B S2 order remains possible when
// that reply starts only after the hold's bound; then the start is not provable either way and resolves no id.
await check('B S2 (xAI): his turn may still get the provider\'s reply: the summary waits for it, and is not taken for it', async () => {
  const s = session('xai');
  s.speech(); s.said('i0', "We're done."); s.stopped(); s.start('r0'); s.done('r0');
  s.speech(); s.said('i1', 'Yes.'); s.stopped();                       // the provider's reply to it has not started
  let id = 'unset';
  s.c.speakTracked('Say only this, then stop: we checked the board.', 60000).then((x) => { id = x; });
  assert.equal(s.frames.filter((f) => f.type === 'response.create').length, 0, 'the summary was sent while a reply was due');
  s.start('auto_yes'); await tick();                                   // the provider's reply to "Yes.": nothing of the page's
  assert.equal(id, 'unset', 'another response was taken for the summary');
  s.done('auto_yes');                                                  // it is over: the summary goes now
  assert.equal(s.frames.filter((f) => f.type === 'response.create').length, 1);
  s.start('r_summary'); await tick();
  assert.equal(id, 'r_summary');
});
await check('B S2 (xAI), past the hold: a reply to "Yes." that starts after the summary was sent resolves no id', async () => {
  const time = clock();
  try {
    const s = session('xai');
    s.speech(); s.said('i1', 'Yes.'); s.stopped();
    let id = 'unset';
    s.c.speakTracked('Say only this, then stop: we checked the board.', 60000).then((x) => { id = x; });
    time.pass(10000);                                                  // REQUEST_TTL_MS: the hold's bound
    assert.equal(s.frames.filter((f) => f.type === 'response.create').length, 1);
    s.start('auto_yes'); await tick();                                 // its reply, or the summary's? not provable
    assert.equal(id, null, 'another response was taken for the summary');
  } finally { time.restore(); }
});
await check('B S2 (xAI): the provider\'s reply started first: the summary line\'s own start is known', async () => {
  const s = session('xai');
  s.speech(); s.said('i1', 'Yes.'); s.stopped();
  s.start('auto_yes'); s.done('auto_yes');                            // the provider's reply to "Yes.": no request in flight
  let id = 'unset';
  s.c.speakTracked('Say only this, then stop: we checked the board.').then((x) => { id = x; });
  s.start('r_summary'); await tick();
  assert.equal(id, 'r_summary');
});
// G-S1 (Gate 1 at b0c3518c, REVIEW_independent_A1_binding.md "Verification at b0c3518c"): the replies still owed are
// counted, not flagged. The reviewer's order: Check's answer line is in flight when he says "Yes."; one start comes (the
// line's own reply, or the provider's to "Yes.": either); the summary is asked for; the other one starts now. It is never the
// summary's.
await check('G-S1 (xAI): a page line\'s own start never uses up the provider\'s reply still owed to "Yes."', async () => {
  const s = session('xai');
  s.c.speakLine('Say this briefly, adding nothing: Check found two items.');   // Check's answer, in flight
  s.speech(); s.said('i1', 'Yes.'); s.stopped();                                 // "Yes." ends: the provider owes it a reply
  s.start('L1_or_theirs'); s.done('L1_or_theirs'); await tick();                 // the line's reply, or the provider's
  let id = 'unset';
  s.c.speakTracked('Say only this, then stop: we checked the board.').then((x) => { id = x; });
  s.start('auto_yes'); await tick();                                             // the other of those two
  assert.equal(id, null, 'the provider\'s late reply to "Yes." was taken for the summary');
  s.done('auto_yes');
  s.start('r_summary'); await tick();                                            // one start was still owed: this one
  assert.equal(id, null);
});
await check('G-S1 (xAI), guard: once every reply owed has started, a page request\'s own start is known again', async () => {
  const s = session('xai');
  let first = 'unset', second = 'unset';
  s.c.speakTracked('Say only this, then stop: one.').then((x) => { first = x; });
  s.start('r1'); await tick();
  assert.equal(first, 'r1', 'with no reply of the provider\'s owed, the request\'s own start was not known');
  s.done('r1');
  s.speech(); s.said('i1', 'Thanks.'); s.stopped(); s.commit('i1');             // one utterance: its stop and its commit
  s.start('auto_1'); s.done('auto_1');                                           // the provider's reply to it
  s.c.speakTracked('Say only this, then stop: two.').then((x) => { second = x; });
  s.start('r2'); await tick();
  assert.equal(second, 'r2', 'a stop and its commit were counted as two turns, or the reply did not count down');
});
// LV1 (live probes at b0c3518c, oa2): OpenAI sends a second response.done for a reply already done when he speaks while
// its audio still plays: cancelled, reason turn_detected, the same output with the same function call. The logged order:
// response.done completed (call_FKRZ… consult) at 6113 ms; his "Hm" speech_started at 6325; response.done cancelled with
// the same call at 6326. The call is dispatched once, answered once and followed up once; the later done only says the
// reply's playback was cut (barged_in).
await check('LV1 (OpenAI): a second response.done for a finished reply dispatches nothing and only reports barged_in', async () => {
  const drains = [];
  const s = session('openai', { drained: (id, outcome) => drains.push([id, outcome]) });
  s.utter('item_A'); s.start('R_A');
  s.said('item_A', 'Please use the consult tool to check the status of the build board.');
  const output = [{ type: 'message', status: 'completed', content: [{ type: 'output_audio', transcript: 'Checking the build board status now.' }] },
    { type: 'function_call', call_id: 'call_FKRZMesVjxZNC5Gg', name: 'consult', arguments: '{}' }];
  s.ev({ type: 'response.done', response: { id: 'R_A', status: 'completed', output } }); await tick();
  assert.equal(s.calls.length, 1);
  assert.deepEqual(drains, [], 'a reply with audio is not over at its done');
  const outputs = () => s.frames.filter((f) => f.type === 'conversation.item.create' && f.item.type === 'function_call_output');
  assert.equal(outputs().length, 1);
  const creates = s.creates().length;                                   // his turn's reply and the call's follow-up
  s.speech('item_hm');                                                  // "Hm", while the reply still plays
  s.ev({ type: 'response.done', response: { id: 'R_A', status: 'cancelled', status_details: { type: 'cancelled', reason: 'turn_detected' },
    output } });
  await tick();
  assert.equal(s.calls.length, 1, 'the repeated call was dispatched again');
  assert.equal(outputs().length, 1, 'the call was answered twice');
  assert.equal(s.creates().length, creates, 'the repeated call was followed up again');
  assert.deepEqual(drains, [['R_A', 'barged_in']]);
  s.ev({ type: 'response.done', response: { id: 'R_A', status: 'cancelled', output } }); await tick();
  assert.deepEqual(drains, [['R_A', 'barged_in']], 'its end was reported twice');
});
await check('LV1, guard: a done for a reply not yet done still settles it, and a new call in a new reply still goes', async () => {
  const s = session('openai');
  s.utter('item_A'); s.start('R_A'); s.said('item_A', 'Check the board.');
  s.done('R_A', { call: 'call_1', name: 'consult' }); await tick();
  s.start('F_A'); s.done('F_A'); await tick();                          // the follow-up's own done
  s.utter('item_B'); s.start('R_B'); s.said('item_B', 'And the kiln.');
  s.done('R_B', { call: 'call_2', name: 'consult' }); await tick();
  assert.deepEqual(s.calls.map((c) => c.call_id), ['call_1', 'call_2']);
});
// LV2 (live probes, xa3 "Name a large city."): xAI sends interim transcripts as .completed with status "in_progress", then
// the final one with status "completed" in the same millisecond as its commit. On the direct path the item completes only
// at the final one; a controller without pageResponses (German, Portuguese) hears every completion as before.
function xaiCity(c, a) {
  const ev = (e) => a._handleServerEvent(e);
  const done = (status, transcript, n) => ev({ type: 'conversation.item.input_audio_transcription.completed', item_id: 'b30aacef',
    status, transcript, event_id: `ev_city_${n}` });
  ev({ type: 'input_audio_buffer.speech_started', item_id: 'b30aacef' });
  done('in_progress', 'Name a large', 1);
  done('in_progress', 'Name a large city.', 2);
  ev({ type: 'input_audio_buffer.speech_stopped' });
  done('in_progress', 'Name a large city.', 3);
  ev({ type: 'response.created', response: { id: '1deaf10b' } });          // xAI's own reply, before the commit
  ev({ type: 'input_audio_buffer.committed', item_id: 'b30aacef' });
  done('completed', 'Name a large city.', 4);
}
await check('LV2 (xAI): an interim .completed (status in_progress) completes nothing on the direct path; the final one does', async () => {
  const a = new XAIWebSocketAdapter(), heard = [], shown = [];
  a._ws = { send: () => {}, readyState: 1, close() {} };
  const c = new RealtimeVoiceController({ pageResponses: true, oncePerItem: true, onUserTurn: (text) => heard.push(text),
    onTranscriptUpdate: (items) => shown.push(items.map((i) => [i.text, i.completed])), onUnbound: () => {} });
  c._adapter = a; c._state = 'active'; c._wireAdapterEvents();
  xaiCity(c, a);
  assert.deepEqual(heard, ['Name a large city.'], 'a fragment was taken for his words');
  assert.deepEqual(shown[0], [['Name a large', false]]);
  assert.deepEqual(shown.at(-1), [['Name a large city.', true]]);
});
await check('LV2, guard (German, no pageResponses): every completion is heard as before', async () => {
  const a = new XAIWebSocketAdapter(), heard = [];
  a._ws = { send: () => {}, readyState: 1, close() {} };
  const c = new RealtimeVoiceController({ onUserTurn: (text) => heard.push(text) });
  c._adapter = a; c._state = 'active'; c._wireAdapterEvents();
  xaiCity(c, a);
  assert.deepEqual(heard, ['Name a large', 'Name a large city.', 'Name a large city.', 'Name a large city.']);
});
// LV3 (live probes, xAI): xAI never reports cancelled, sends no response.done for a reply overtaken before its audio, and
// silently drops a response.create sent while a reply of its own is under way. So on xAI a page line goes only when no reply
// is under way, nothing waits on a done that may never come, and a line that may have been dropped is let go at its bound.
const lineCreates = (s) => s.frames.filter((f) => f.type === 'response.create').length;
await check('LV3 (xAI, xa1): a page line asked for while xAI\'s own reply is under way is sent only after its done', async () => {
  const s = session('xai');
  s.speech(); s.said('i0', 'Please count slowly from one to ten.'); s.stopped();
  s.start('254e3097');                                                 // xAI's own reply, at 23532 ms
  s.c.speakLine('Say this briefly, adding nothing: Check found two items.');
  assert.equal(lineCreates(s), 0, 'the line was sent into an active reply (xAI drops it)');
  s.commit('i0');                                                      // the commit after the reply started (LV5's order)
  s.done('254e3097');
  assert.equal(lineCreates(s), 1);
});
await check('LV3 (xAI, xa3): a reply overtaken before its audio, which never gets a done, does not hold the page\'s line', async () => {
  const time = clock();
  try {
    const s = session('xai');
    s.speech(); s.said('a', 'Name an animal.'); s.stopped();
    s.start('1734ac51');                                               // overtaken below: xAI never sends its done
    s.c.speakLine('Say this briefly, adding nothing: Still checking.');
    assert.equal(lineCreates(s), 0);
    s.speech(); s.said('b', 'Name an animal. Trees grow tall.'); s.stopped();
    s.start('cd738d44'); time.pass(1000); s.done('cd738d44');           // the reply to his next words, and its done
    assert.equal(lineCreates(s), 1, 'the line waited on a done that never came');
  } finally { time.restore(); }
});
await check('LV3 (xAI): with no done and no speech, a reply under way stops holding the page\'s line at RESPONSE_IDLE_MS', async () => {
  const time = clock();
  try {
    const s = session('xai');
    s.speech(); s.said('a', 'Hello.'); s.stopped(); s.start('mute');      // no output, no done, ever
    s.c.speakLine('Say this briefly, adding nothing: The board is green.');
    time.pass(19000); assert.equal(lineCreates(s), 0);
    time.pass(1000); assert.equal(lineCreates(s), 1);
  } finally { time.restore(); }
});
await check('LV3 (xAI): a started page line that never gets a done is let go at RESPONSE_IDLE_MS (B N5), and the next one goes', async () => {
  const time = clock();
  try {
    const s = session('xai');
    s.c.speakLine('Say this briefly, adding nothing: one.');
    s.c.speakLine('Say this briefly, adding nothing: two.');
    assert.equal(lineCreates(s), 1);
    s.start('line_1');                                                 // the first line's reply: no output, no done
    time.pass(20000);
    assert.equal(lineCreates(s), 2, 'the next line waited on a done that never came');
    assert.ok(s.unbound.length === 0 || s.unbound.every((u) => u.owner == null));
  } finally { time.restore(); }
});
await check('LV3 (xAI): a summary line xAI silently drops is let go at its bound, never assigned, and resolves no id', async () => {
  const time = clock();
  try {
    const s = session('xai');
    let id = 'unset';
    s.c.speakTracked('Say only this, then stop: we checked the board.', 5000).then((x) => { id = x; });
    assert.equal(lineCreates(s), 1);                                   // sent, and dropped: it never starts
    time.pass(5000); await tick();
    assert.equal(id, null);                                            // the page's own bound: the summary ends as timeout
    time.pass(5000);                                                   // REQUEST_TTL_MS: the request is let go
    s.speech(); s.said('a', 'Hello?'); s.stopped(); s.start('auto'); await tick();
    assert.equal(id, null, 'a later reply was assigned to the dropped line');
    assert.equal(s.c._inflight, null);
  } finally { time.restore(); }
});
await check('LV3, guard (OpenAI): where the page makes every reply, a line is never held by a reply it did not ask for', async () => {
  const s = session('openai');
  s.start('stray', { tag: null, echo: false });                         // untagged, unrequested: answers no one
  s.c.speakLine('Say this briefly, adding nothing: Check found two items.');
  assert.equal(lineCreates(s), 1);
});
await check('B N4 (OpenAI): a holding line still waiting its turn is withdrawn; one already sent is not', async () => {
  const s = session('openai');
  s.utter('item_A'); s.start('R_A');
  const held = s.c.speakLine('Say this briefly, adding nothing: Still checking.');
  assert.equal(held.withdraw(), true);
  s.done('R_A');
  assert.equal(s.creates().length, 1, 'a withdrawn line was sent');
  const sent = s.c.speakLine('Say this briefly, adding nothing: Still working on it.');
  assert.equal(s.creates().length, 2);
  assert.equal(sent.withdraw(), false);
});
await check('B N2 (OpenAI): the place of his item, for naming a turn not acted on', async () => {
  const s = session('openai');
  s.utter('item_A'); assert.equal(s.c.placeOf('item_A'), null);       // no words yet: no place
  s.said('item_A', 'Save a note: first request'); s.utter('item_B'); s.said('item_B', 'Check the board');
  assert.equal(s.c.placeOf('item_A'), 0); assert.equal(s.c.placeOf('item_B'), 1); assert.equal(s.c.placeOf('nope'), null);
});

// ── I5: barge-in, cancelled and superseded responses, the earlier commit that never got its own ──────────────────────
await check('new (OpenAI): a commit while a response is in flight: barge-in cancels it, and the newest committed item is answered', async () => {
  const s = session('openai');
  s.utter('item_A'); s.start('R_A');
  s.speech();                                                           // he speaks again: the provider's barge-in
  s.done('R_A', { status: 'cancelled', call: 'c_A' }); await tick();
  s.stopped(); s.commit('item_B');
  assert.equal(s.creates().length, 2);
  s.start('R_B'); s.done('R_B', { call: 'c_B' }); await tick();
  s.said('item_A', 'Make a note that the kiln is hot'); s.said('item_B', 'No, check the board');
  assert.equal(s.calls.length, 1, 'a call from the cancelled response was dispatched');
  assert.equal(words(s, 0), 'No, check the board');
  assert.equal(s.creates().length, 3, 'only B\'s call has a follow-up');               // B's result's follow-up
});
await check('new (OpenAI): a function call from a cancelled response is refused: nothing dispatched, no follow-up', async () => {
  const s = session('openai');
  s.utter('item_A'); s.start('R_A');
  s.done('R_A', { status: 'cancelled', call: 'c_A' }); await tick();
  s.said('item_A', 'Make a note that the kiln is hot');
  assert.equal(s.calls.length, 0);
  assert.equal(s.creates().length, 1, 'a follow-up was asked for a refused call');
  assert.ok(s.unbound.some((u) => u.reason === 'superseded_call' && u.response_id === 'R_A' && u.owner === 'item_A'));
  // B S3: the model's context is true: the call's output says it did not run, and no reply is asked for it.
  const out = s.frames.filter((f) => f.type === 'conversation.item.create' && f.item.type === 'function_call_output');
  assert.equal(out.length, 1);
  assert.equal(out[0].item.call_id, 'c_A');
  assert.deepEqual(JSON.parse(out[0].item.output), { ok: false, outcome: 'not_done', error: 'superseded',
    spoken: 'Not run: superseded by what he said next. Nothing was done.' });
});
await check('B S3 (OpenAI): an overtaken reply\'s call gets a not-done output and no reply of its own; his newer turn is answered', async () => {
  const s = session('openai');
  s.utter('item_A'); s.start('R_A');
  s.speech(); s.stopped(); s.commit('item_B');
  s.done('R_A', { call: 'c_A' }); await tick();
  const out = s.frames.filter((f) => f.type === 'conversation.item.create' && f.item.type === 'function_call_output');
  assert.deepEqual(out.map((f) => [f.item.call_id, JSON.parse(f.item.output).error]), [['c_A', 'superseded']]);
  assert.equal(s.creates().length, 2);                                 // B's reply only: none for the refused call
});
for (const status of ['incomplete', 'failed']) {
  await check(`new (OpenAI): a call from a response cut short (${status}) is refused`, async () => {
    const s = session('openai');
    s.utter('item_A'); s.start('R_A'); s.done('R_A', { status, call: 'c_A' }); await tick();
    assert.equal(s.calls.length, 0);
  });
}
await check('new (OpenAI): a response that completes after his next words began is superseded: its call acts on nothing', async () => {
  const s = session('openai');
  s.utter('item_A'); s.start('R_A');
  s.speech(); s.stopped(); s.commit('item_B');                          // B waits its turn behind R_A
  assert.equal(s.creates().length, 1);
  s.done('R_A', { call: 'c_A' }); await tick();                         // the provider did not cancel it
  assert.equal(s.calls.length, 0, 'a call from a superseded response was dispatched');
  assert.equal(s.creates().length, 2);                                  // B's response, once R_A settled
  s.start('R_B'); s.done('R_B', { call: 'c_B' }); await tick();
  s.said('item_A', 'Make a note that the kiln is hot'); s.said('item_B', 'Check the board');
  assert.equal(words(s, 0), 'Check the board');
});
await check('new (OpenAI): two commits before the first response is created: only the newest is answered, by its own words', async () => {
  const s = session('openai');
  s.c.speak('Say this briefly, adding nothing: one moment.');           // a line in flight: his commits wait their turn
  s.utter('item_A'); s.utter('item_B');
  assert.equal(s.creates().length, 1);
  s.start('R_line'); s.done('R_line');
  assert.equal(s.creates().length, 2, 'not one response for the newest committed item');
  s.start('R_B'); s.done('R_B', { call: 'c_B' }); await tick();
  s.said('item_A', 'Save a note: first request'); s.said('item_B', 'Check the board');
  assert.equal(words(s, 0), 'Check the board');
  assert.ok(s.unbound.some((u) => u.reason === 'superseded' && u.owner === 'item_A'));
  // The earlier commit never got a response of its own, so nothing ever acts on its words: he says it again if he needs it.
  assert.ok(s.calls.every((_, i) => words(s, i) !== 'Save a note: first request'));
});
await check('new (OpenAI): his committed item\'s request waits while he is speaking again, and is let go: the next commit asks', async () => {
  const s = session('openai');
  s.c.speak('Say this briefly, adding nothing: one moment.');
  s.utter('item_A');
  s.speech();                                                           // he goes on before the line's response settles
  s.start('R_line'); s.done('R_line');
  assert.equal(s.creates().length, 1, 'A\'s response was asked for while he was speaking');
  s.stopped(); s.commit('item_B');
  assert.equal(s.creates().length, 2);
  s.start('R_B'); s.done('R_B', { call: 'c_B' }); await tick();
  s.said('item_B', 'and check the board');
  assert.equal(words(s, 0), 'and check the board');
});
// Changed for LV4: A's call has no output when B's reply is made, so the page answers it pending first, and A's result is
// then said as a page line, which answers none of his inputs (a call from it acts on no one). Each turn still acts only for
// its own owner.
await check('new (OpenAI): a call still running while a new commit waits: answered pending first, its result a line; each acts for its own owner', async () => {
  let release = null;
  const s = session('openai', { answer: (call) => (call.call_id === 'c_A' ? new Promise((r) => { release = () => r({ ok: true, spoken: 'Two items.' }); }) : { ok: true }) });
  s.utter('item_A'); s.said('item_A', 'What is stuck on #12?');
  s.start('R_A'); s.done('R_A', { call: 'c_A', name: 'consult' }); await tick();
  s.utter('item_B'); s.said('item_B', 'Make a note that the kiln is hot');   // while A's consult runs
  assert.equal(s.creates().length, 2);                                  // B's response at once: nothing was in flight
  s.start('R_B');
  release(); await tick(); await tick();                                // A's result: it waits behind R_B
  assert.equal(s.creates().length, 2, 'something was sent while B\'s response was in flight');
  s.done('R_B', { call: 'c_B' }); await tick();
  const outputs = s.frames.filter((f) => f.type === 'conversation.item.create' && f.item.type === 'function_call_output');
  assert.deepEqual(outputs.map((f) => [f.item.call_id, JSON.parse(f.item.output).outcome]), [['c_A', 'pending']]);
  s.start('R_lA');                                                      // A's result, said as a line
  assert.equal(s.frames.filter((f) => f.type === 'conversation.item.create' && f.item.type === 'message').at(-1).item.content[0].text,
    'Say this briefly, adding nothing: Two items.');
  s.done('R_lA', { call: 'c_lA', name: 'consult' }); await tick();      // a call from the line's reply
  assert.equal(words(s, 0), 'What is stuck on #12?');
  assert.equal(words(s, 1), 'Make a note that the kiln is hot');
  assert.equal(s.bound(2), null, 'a call from the line acted on someone');
});
// LV4 (live probes, oa2): OpenAI spoke its tool placeholder when the page asked for a reply to his "Hm" while the consult
// call (call_FKRZ…) had no output. The page now answers that call pending before asking, and says the result later.
await check('LV4 (OpenAI, oa2): before any other reply, a call with no output is answered pending; its result comes later as a line', async () => {
  let release = null;
  const s = session('openai', { answer: (call) => (call.call_id === 'call_FKRZ' ? new Promise((r) => { release = () => r({ ok: true, outcome: 'done', spoken: 'The board is green.' }); }) : { ok: true }) });
  s.utter('item_A'); s.said('item_A', 'Please use the consult tool to check the status of the build board.');
  s.start('R_c1'); s.done('R_c1', { call: 'call_FKRZ', name: 'consult' }); await tick();   // dispatched: its consult runs
  s.utter('item_hm'); s.said('item_hm', 'Hm');                          // his "Hm", committed: the page asks for its reply
  const order = s.frames.map((f) => f.type === 'conversation.item.create' ? `${f.item.type}:${f.item.call_id || ''}` : f.type);
  const told = order.indexOf('function_call_output:call_FKRZ');
  assert.ok(told >= 0 && told < order.lastIndexOf('response.create'), 'a reply was asked for while the call had no output');
  const pending = JSON.parse(s.frames.find((f) => f.item?.call_id === 'call_FKRZ').item.output);
  assert.deepEqual(pending, { ok: false, outcome: 'pending', spoken: 'Still running; the result will be told when it lands.' });
  assert.equal(s.creates().length, 2, 'a reply was asked for the pending output');   // c1's, and Hm's: none for the output
  s.start('R_hm'); s.done('R_hm'); await tick();
  release(); await tick(); await tick();
  assert.equal(s.frames.filter((f) => f.item?.call_id === 'call_FKRZ').length, 1, 'the call was answered twice');
  assert.equal(s.frames.filter((f) => f.type === 'conversation.item.create' && f.item.type === 'message').at(-1).item.content[0].text,
    'Say this briefly, adding nothing: The board is green.');
});
await check('LV4, guard: with no other reply in between, a call\'s result is its output and follow-up, as before', async () => {
  const s = session('openai', { answer: () => ({ ok: true, spoken: 'Done.' }) });
  s.utter('item_A'); s.said('item_A', 'Check the board.');
  s.start('R_A'); s.done('R_A', { call: 'c_A', name: 'consult' }); await tick();
  const outputs = s.frames.filter((f) => f.type === 'conversation.item.create' && f.item.type === 'function_call_output');
  assert.deepEqual(outputs.map((f) => JSON.parse(f.item.output)), [{ ok: true, spoken: 'Done.' }]);
  s.start('F_A'); s.done('F_A', { call: 'c_F', name: 'consult' }); await tick();
  assert.equal(words(s, 1), 'Check the board.');                         // the follow-up still acts for A
});
await check('new (OpenAI): a commit that names no item still gets a response, which may act on no one (logged)', async () => {
  const s = session('openai');
  s.speech(); s.stopped(); s.commit(null);
  assert.equal(s.creates().length, 1);                                  // he hears a reply
  s.start('R_x'); s.done('R_x', { call: 'c_x' }); await tick();
  s.said('item_x', 'Make a note that the kiln is hot');
  assert.equal(s.bound(0), null);
  assert.ok(s.unbound.some((u) => u.reason === 'commit_without_item'));
});
await check('I3 (OpenAI): a cough is an item with empty words: its call is bound to nothing words-wise ("say it again")', async () => {
  const s = session('openai');
  s.utter('cough'); s.start('R_c'); s.done('R_c', { call: 'c_c' }); await tick();
  s.said('cough', '');
  assert.deepEqual(s.bound(0), { state: 'bound', index: 0, text: '' });   // the page refuses empty words (NOT_HEARD)
});

// ── The orders found so far, as regressions of the invariants: on OpenAI the right owner or nothing; on xAI nothing ─────
for (const [name, provider] of ALL) {
  const owned = provider === 'openai';
  const none = (s) => s.calls.forEach((_, i) => assert.equal(s.bound(i), null, `call ${i} was tied to his words on xAI`));
  // M1: a cough, Robert starts speaking, then the cough's response starts late; his item appears; the cough's call.
  await check(`M1 (${name}): a cough's late response never takes his next words`, async () => {
    const s = session(provider);
    s.speech(); s.stopped();                                            // a cough: no item committed
    s.speech();                                                          // Robert starts
    s.start('noise', { tag: null });                                     // the cough's response: not the page's
    s.said('later', 'Save a note: the real request'); s.stopped(); s.commit('later');
    s.start('R_real');
    s.done('noise', { call: 'noise_call' }); s.done('R_real', { call: 'real_call' }); await tick();
    assert.equal(s.bound(0), null, "the cough's call took his words");
    if (owned) assert.equal(words(s, 1), 'Save a note: the real request'); else none(s);
  });
  // V-M1: U0 ends with no item (its response the provider's own); Robert begins U1; U0's late item appears during U1, by
  // three orders and the hold variant, and the L M1 path (R0 starting after U1 began). Neither response takes U0's words.
  const A0 = 'Save a note that A', B1 = 'and check the board';
  const upd = (item, text) => ({ type: 'conversation.item.input_audio_transcription.updated', item_id: item, transcript: text, completed: false, event_id: `u_${item}` });
  const vm1 = [
    ["order 1: U0's item during U1, U1's response before U1's item", (s, pass) => {
      s.speech(); pass(300); s.said('u0', A0); pass(1500); s.stopped(); s.commit('u1'); s.start('R1'); s.said('u1', B1);
    }],
    ["order 2: U1's interim first, then U0's item", (s, pass) => {
      s.speech(); pass(300); s.ev(upd('u1', 'and check')); pass(200); s.said('u0', A0); pass(1000); s.stopped(); s.commit('u1');
      s.start('R1'); s.said('u1', B1);
    }],
    ["order 3 (the hold): U1's response starts, then U0's item", (s, pass) => {
      s.speech(); pass(1000); s.stopped(); s.commit('u1'); s.start('R1'); pass(200); s.said('u0', A0); s.said('u1', B1);
    }]];
  for (const [label, rest] of vm1) {
    for (const lmPath of [false, true]) {
      await check(`V-M1 ${label}${lmPath ? ', R0 after U1 began' : ''} (${name}): neither response takes U0's late item`, async (time) => {
        const s = session(provider);
        s.speech(); s.stopped();                                        // U0: "Save a note that A", no item yet
        if (!lmPath) s.start('R0', { tag: null });
        time.pass(600);
        let first = true;
        rest(s, (ms) => { time.pass(ms); if (lmPath && first) { first = false; s.start('R0', { tag: null }); } });
        s.done('R0', { call: 'c0' }); s.done('R1', { call: 'c1' }); await tick();
        assert.equal(s.bound(0), null, "U0's response was tied to words");
        if (owned) assert.equal(words(s, 1), B1, "U1's response did not act on U1's own words");
        else none(s);
      });
    }
  }
  // V-M1 with U0 committed (OpenAI names every buffer): U0's own response is answered by U0's words, U1's by U1's.
  await check(`V-M1, U0 committed (${name}): each response acts on its own item, U0's late words never on U1's`, async (time) => {
    const s = session(provider);
    s.utter('u0'); s.start('R0'); s.done('R0', { call: 'c0' }); await tick();
    s.start('F0'); s.done('F0');                                        // c0's result's follow-up, before he goes on
    time.pass(600); s.speech(); time.pass(300); s.said('u0', A0); time.pass(1500); s.stopped(); s.commit('u1');
    s.start('R1'); s.done('R1', { call: 'c1' }); await tick(); s.said('u1', B1);
    if (owned) { assert.equal(words(s, 0), A0); assert.equal(words(s, 1), B1); } else none(s);
  });
  // LP1 (Codex): U0 ends with no item and R0 starts; at +3.5 s U1 starts; at +3.8 s U0's item first arrives; U1 ends, its
  // response calls before U1's own item. No bound passing makes U0's words U1's.
  for (const committed of [true, false]) {
    await check(`LP1 (${name}${committed ? '' : ', U1 not committed'}): an earlier item arriving late is never acted on for newer speech`, async (time) => {
      const s = session(provider);
      s.speech(); s.stopped(); s.start('old', { tag: null });
      time.pass(3500); s.speech(); time.pass(300); s.said('old_item', 'Save a note: earlier request'); s.stopped();
      if (committed) { s.commit('new_item'); s.start('new'); } else s.start('new', { tag: null });
      s.done('new', { call: 'c_new' }); await tick();
      assert.notEqual(words(s, 0), 'Save a note: earlier request');
      s.said('new_item', 'Check the board');
      if (owned && committed) assert.equal(words(s, 0), 'Check the board'); else assert.equal(s.bound(0), null);
    });
  }
  // LP2 (Codex): two utterances complete (on OpenAI with named speech-start and commit ids), then a delayed response to
  // U0 and its call. It never picks the newer of two sentences: U0's response is overtaken (acts on nothing); U1's acts on U1.
  await check(`LP2 (${name}): a delayed response with two utterances waiting never picks the newer one`, async (time) => {
    const s = session(provider);
    s.speech(owned ? 'a' : null); s.said('a', 'Save a note: first request'); s.stopped(); if (owned) s.commit('a');
    time.pass(500);
    s.speech(owned ? 'b' : null); s.said('b', 'Check the board'); s.stopped(); if (owned) s.commit('b');
    s.start('delayed_response_to_a', { echo: false }); s.done('delayed_response_to_a', { call: 'c_a' }); await tick();
    s.start('actual_b_response'); s.done('actual_b_response', { call: 'c_b' }); await tick();
    assert.ok(s.calls.every((c, i) => c.response_id !== 'delayed_response_to_a' || words(s, i) !== 'Check the board'),
      'the delayed response acted on the newer sentence');
    if (owned) {
      assert.equal(s.calls.length, 1, "the delayed response's call was dispatched");      // overtaken by U1: refused
      assert.equal(words(s, 0), 'Check the board');
      assert.equal(s.calls[0].response_id, 'actual_b_response');
    } else none(s);
  });
  // F1 (Codex): a cough's response, then a later utterance (20 s later, and inside 3 s), then the cough's delayed call.
  for (const [gap, label] of [[20000, "Codex's case: 20 s later"], [1000, 'inside 3 s']]) {
    for (const coughCommitted of [false, true]) {
      await check(`F1 ${label}${coughCommitted ? ', the cough committed' : ''} (${name}): a cough's response never takes his later words`, async (time) => {
        const s = session(provider);
        s.speech(); s.stopped();
        if (coughCommitted) { s.commit('cough'); s.start('noise_response'); } else s.start('noise_response', { tag: null });
        time.pass(gap);
        s.speech(); s.said('later_item', 'Save a note: a later request'); s.stopped(); s.commit('later_item');
        s.done('noise_response', { call: 'noise_call' }); await tick();     // the cough's delayed call
        s.start('R_later'); s.done('R_later', { call: 'later_call' }); await tick();
        const noise = s.calls.findIndex((c) => c.call_id === 'noise_call'), later = s.calls.findIndex((c) => c.call_id === 'later_call');
        if (noise >= 0) assert.notEqual(words(s, noise), 'Save a note: a later request', "the cough's call took his later words");
        if (owned) assert.equal(words(s, later), 'Save a note: a later request'); else none(s);
      });
    }
  }
  // R3-S1 F: a function result sent as his next speech began; G: a cough, then a follow-up; H: a request that never starts.
  const A_ = 'What is stuck on twelve? Make a note of it.';
  const first = async (s) => { s.utter('item_A'); s.start('R1'); s.said('item_A', A_); s.done('R1', { call: 'call_1', name: 'consult' }); };
  await check(`R3-S1 F (${name}): a result sent as he starts B: the follow-up and his response never act on each other's words`, async () => {
    const s = session(provider);
    await first(s);
    s.speech(); await tick();                                            // B begins; the result is sent: its follow-up in flight
    s.stopped(); s.commit('item_B');                                     // B waits behind the follow-up
    s.start('R_f'); s.done('R_f', { call: 'call_2', name: 'consult' }); await tick();   // overtaken by B: acts on nothing
    s.start('R_B'); s.done('R_B', { call: 'call_B' }); await tick();
    s.said('item_B', 'Make a note that the glaze is ready');
    if (owned) {
      assert.equal(words(s, 0), A_);
      assert.deepEqual(s.calls.map((c) => c.call_id), ['call_1', 'call_B'], "the overtaken follow-up's call was dispatched");
      assert.equal(words(s, 1), 'Make a note that the glaze is ready');
    } else none(s);
  });
  await check(`R3-S1 G (${name}): a cough while the follow-up is in flight: it acts on nothing, and his next request is his own`, async () => {
    const s = session(provider);
    await first(s); await tick();                                       // the result is sent: its follow-up in flight
    s.utter('item_cough');                                              // a cough: committed on OpenAI, no words; it waits
    s.start('R_f'); s.done('R_f', { call: 'call_2', name: 'save_note' }); await tick();   // overtaken by the cough
    if (owned) { s.start('R_cough'); s.done('R_cough'); }
    s.said('item_cough', '');
    s.utter('item_B'); s.start('R_B'); s.done('R_B', { call: 'call_B' }); await tick();
    s.said('item_B', 'Make a note that the glaze is ready');
    if (owned) {
      assert.deepEqual(s.calls.map((c) => c.call_id), ['call_1', 'call_B'], "the overtaken follow-up's call was dispatched");
      assert.equal(words(s, 1), 'Make a note that the glaze is ready');
    } else none(s);
  });
  await check(`R3-S1 G, the cough after the follow-up settled (${name}): the follow-up's call acts for A, the cough's on nothing`, async () => {
    const s = session(provider);
    await first(s); await tick();
    s.start('R_f'); s.done('R_f', { call: 'call_2', name: 'save_note' }); await tick();   // a chained call: still A's
    s.start('R_f2'); s.done('R_f2');                                    // that call's own follow-up
    s.utter('item_cough'); if (owned) { s.start('R_cough'); s.done('R_cough', { call: 'call_c' }); await tick(); }
    s.said('item_cough', '');
    if (owned) {
      assert.equal(words(s, 1), A_, 'the chained call took the cough\'s words');
      assert.equal(words(s, 2), '');                                    // the cough's own empty words: "say it again"
    } else none(s);
  });
  await check(`R3-S1 H (${name}): a request that never starts: nothing binds to it, and his next request is his own`, async (time) => {
    const s = session(provider);
    await first(s); await tick();                                       // the result is sent; its response never starts
    s.utter('item_B');                                                   // B waits behind it
    time.pass(10000);                                                    // the dead request is let go at its bound
    if (owned) assert.equal(s.creates().length, 3, "B's response was not asked for once the dead request was let go");
    s.start('R_B'); s.done('R_B', { call: 'call_B' }); await tick();
    s.said('item_B', 'Make a note that the glaze is ready');
    if (owned) assert.equal(words(s, 1), 'Make a note that the glaze is ready'); else none(s);
  });
  // W N4: one speech span that yields two items; a span with two items and a span with none.
  await check(`W N4 (${name}): one span that yields two items: a call acts on its committed item's words only`, async () => {
    const s = session(provider);
    s.speech(); s.said('frag_a', 'Make a note'); s.said('frag_b', 'that the kiln is hot'); s.stopped(); s.commit('frag_b');
    s.start('R'); s.done('R', { call: 'c' }); await tick();
    if (owned) assert.equal(words(s, 0), 'that the kiln is hot');       // as said: MC refuses a fragment that is no note command
    else none(s);
  });
  await check(`W N4 (${name}): a span with two items and a span with none: the uncommitted span gets no response`, async () => {
    const s = session(provider);
    s.speech(); s.said('frag_a', 'Make a note'); s.said('frag_b', 'that the kiln is hot'); s.stopped(); s.commit('frag_b');
    s.speech(); s.stopped();                                            // a span with no item: nothing is asked for it
    assert.equal(s.creates().length, owned ? 1 : 0);
    s.start('R'); s.done('R', { call: 'c' }); await tick();
    if (owned) assert.equal(s.calls.length, 0, 'a response overtaken by his next speech acted');
    else none(s);
  });
}

// ── I6 at the controller: xAI direct (not eligible) never asks for a response for his words, and no call is ever his ────
await check('I6 (xAI): commits ask for nothing, and every call is tied to no words of his', async () => {
  const s = session('xai');
  s.utter('item_A'); s.said('item_A', 'Make a note that the kiln is hot');
  assert.equal(s.creates().length, 0, 'the page asked xAI for a response');
  s.start('R_A'); s.done('R_A', { call: 'c' }); await tick();
  assert.equal(s.bound(0), null);
  assert.deepEqual(s.unbound, [], 'the provider\'s own responses are expected on xAI: nothing is logged');
});
await check('I6 (OpenAI, the bootstrap not saying page_responses): as xAI, nothing is tied to his words', async () => {
  const s = session('openai', { owned: false });
  s.utter('item_A'); s.said('item_A', 'Make a note that the kiln is hot');
  assert.equal(s.creates().length, 0);
  s.start('R_A', { tag: null }); s.done('R_A', { call: 'c' }); await tick();
  assert.equal(s.bound(0), null);
});

// F2 (Codex's delta review at a17ddc8d; its response_origin.mjs precursor, asserting the correct behaviour): a host that
// asks for it (oncePerItem: MC's page) hears each owner item's completion once. A replayed provider event, or a second
// completion of an item already completed, is not a new turn. German and Portuguese pass no option and hear every
// completion as before (pinned here and in test_controller_no_change.mjs).
const saidX = (item, text) => ({ type: 'conversation.item.input_audio_transcription.completed', item_id: item, transcript: text, event_id: `ev_${item}_${text.length}` });
for (const [label, options, expected] of [
  ['a host that asks (oncePerItem)', { oncePerItem: true }, ['Save a note: first request', 'Save a note: second request']],
  ['German (no option): as before', {}, ['Save a note: first request', 'Save a note: second request', 'Save a note: first request',
    'Save a note: second request']]]) {
  await check(`F2 xAI, ${label}: a replayed completion, and a second completion of one item`, async () => {
    const a = new XAIWebSocketAdapter(); const turns = [];
    const c = new RealtimeVoiceController({ ...options, onUserTurn: (text) => turns.push(text) });
    c._adapter = a; c._state = 'active'; c._wireAdapterEvents();
    const once = saidX('item_a', 'Save a note: first request');
    a._handleServerEvent(once);
    a._handleServerEvent({ type: 'conversation.item.input_audio_transcription.updated', item_id: 'item_b',
      transcript: 'Save a note: second request', completed: true, event_id: 'ev_b_upd' });
    a._handleServerEvent(once);                                                          // the identical event, replayed
    a._handleServerEvent(saidX('item_b', 'Save a note: second request'));                // item_b completed again
    assert.deepEqual(turns, expected);
  });
}
// German and Portuguese (no pageResponses): every request goes at once, untagged, and nothing is tracked or logged.
await check('I7: a controller without pageResponses sends each request at once, untagged, and ties nothing', async () => {
  const a = new OpenAIWebRTCAdapter(), frames = [], unbound = [];
  a._dc = { send: (f) => frames.push(JSON.parse(f)) };
  const c = new RealtimeVoiceController({ onUnbound: (i) => unbound.push(i), onFunctionCall: async () => ({ ok: true }) });
  c._adapter = a; c._state = 'active'; c._wireAdapterEvents();
  a._handleServerEvent({ type: 'response.created', response: { id: 'r1' } });
  a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: 'i1' });
  c.continueNaturally(); c.continueNaturally();
  assert.deepEqual(frames.filter((f) => f.type === 'response.create'), [{ type: 'response.create' }, { type: 'response.create' }]);
  a._handleServerEvent({ type: 'response.done', response: { id: 'r1', status: 'cancelled', output: [{ type: 'function_call', call_id: 'c1', name: 'x', arguments: '{}' }] } });
  await tick();
  assert.equal(frames.filter((f) => f.type === 'conversation.item.create' && f.item.type === 'function_call_output').length, 1);
  assert.deepEqual(unbound, []);
});

if (failures.length) {
  console.log(`${failures.length} of ${cases} response-binding regressions FAILED:\n  ${failures.join('\n  ')}`);
  process.exit(1);
}
console.log(`${cases} response-binding regressions passed (C2 by construction, I1–I7; M1, V-M1, LP1, LP2, F1, R3-S1 F/G/H, W N4, F2; B S1–N6; G-S1; LV1–LV4); real controller and adapters, synthetic provider events.`);
