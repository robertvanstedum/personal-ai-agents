// Review CX A1-C2, FR M2, V R2-M1 (Spec 165 §5.1): the real controller and the real adapters bind each function call, through
// its response id, to the owner input item that response answers. A response the provider made from his speech answers his
// utterance that ended: the item committed before it started (OpenAI's input_audio_buffer.committed), or, while its item is
// not yet known (xAI without that event), the next item to appear. A response the controller asked for answers what it
// continues: a function result's follow-up inherits the owner item of the call it continues, and the page's own line
// answers none. Never whichever transcript finished last. Synthetic provider events only: no provider, microphone or
// network. The event orders here are the providers' documented ones; none was observed live.
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

const root = process.env.VOICE_REVIEW_ROOT ? pathToFileURL(process.env.VOICE_REVIEW_ROOT + '/') : new URL('../../', import.meta.url);
const { XAIWebSocketAdapter } = await import(new URL('core/realtime_voice/static/adapters/xai-websocket-adapter.js', root));
const { OpenAIWebRTCAdapter } = await import(new URL('core/realtime_voice/static/adapters/openai-webrtc-adapter.js', root));
const { RealtimeVoiceController } = await import(new URL('core/realtime_voice/static/realtime-voice-controller.js', root));
const failures = [];
let cases = 0;
async function check(name, fn) { cases++; try { await fn(); } catch (e) { failures.push(`${name}: ${e.message.split('\n')[0]}`); } }

function session(adapter, { trackResponses = false } = {}) {
  const calls = [], turns = [], seenAtTurn = [], tags = [];
  const c = new RealtimeVoiceController({
    trackResponses,
    onFunctionCall: async (call) => { calls.push(call); return {}; },
    onTranscriptUpdate: (list) => { turns.splice(0, turns.length, ...list); },
    onUserTurn: () => { seenAtTurn.push(calls.map((call) => c.inputOf?.(call))); },
  });
  c._adapter = adapter; c._state = 'active'; c._wireAdapterEvents();
  adapter.sendFunctionResult = (_id, _output, tag) => { tags.push(tag || null); };
  adapter.sendContinuationInstruction = (_text, tag) => { tags.push(tag || null); };
  const bound = (call) => (typeof c.inputOf === 'function' ? c.inputOf(call) : { state: 'none' });
  return { c, calls, turns, bound, seenAtTurn, tags };
}
const callIn = (id, callId, name = 'save_note') => ({ type: 'response.done', response: { id, output: [{ type: 'function_call', call_id: callId, name, arguments: '{}' }] } });
const said = (item, text) => ({ type: 'conversation.item.input_audio_transcription.completed', item_id: item, transcript: text, event_id: `ev_${item}_${text.length}` });

await check('CX A1-C2 OpenAI: a delayed call from the first response is bound to the first owner item, not the second', async () => {
  const a = new OpenAIWebRTCAdapter();
  const { calls, bound, turns } = session(a);
  a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: 'item_A' });
  a._handleServerEvent({ type: 'response.created', response: { id: 'resp_1' } });
  a._handleServerEvent(said('item_A', 'Save a note: first request'));
  a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: 'item_B' });
  a._handleServerEvent({ type: 'response.created', response: { id: 'resp_2' } });
  a._handleServerEvent(said('item_B', 'Save a note: second request'));
  a._handleServerEvent(callIn('resp_1', 'call_late'));                    // the first response's call, after the second turn
  await new Promise((r) => setImmediate(r));
  assert.equal(calls[0].response_id, 'resp_1');
  assert.deepEqual(bound(calls[0]), { state: 'bound', index: 0, text: 'Save a note: first request' });
  assert.equal(turns[0].text, 'Save a note: first request');               // index 0 is the host's t0
});
await check('CX A1-C2 OpenAI: a call whose owner item is not transcribed yet is pending, then bound to that item', async () => {
  const a = new OpenAIWebRTCAdapter();
  const { calls, bound } = session(a);
  a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: 'item_A' });
  a._handleServerEvent({ type: 'response.created', response: { id: 'resp_1' } });
  a._handleServerEvent(callIn('resp_1', 'call_early'));                   // before its transcript
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(bound(calls[0]), { state: 'pending' });
  a._handleServerEvent(said('item_A', 'Make a note that the kiln is hot'));
  assert.deepEqual(bound(calls[0]), { state: 'bound', index: 0, text: 'Make a note that the kiln is hot' });
});
await check('CX A1-C2 xAI without a committed event: a response after his speech is bound to the next item to appear', async () => {
  const a = new XAIWebSocketAdapter();
  const { calls, bound, seenAtTurn } = session(a);
  a._handleServerEvent(said('item_A', 'What is stuck on #12?'));
  a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });
  a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
  a._handleServerEvent({ type: 'response.created', response: { id: 'resp_2' } });
  a._handleServerEvent(callIn('resp_2', 'call_x'));
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(bound(calls[0]), { state: 'pending' }, 'bound to the earlier item while the new one was unknown');
  a._handleServerEvent({ type: 'conversation.item.input_audio_transcription.updated', item_id: 'item_B', transcript: 'Make a note that the glaze is ready', completed: true, event_id: 'ev_1' });
  a._handleServerEvent({ type: 'conversation.item.input_audio_transcription.completed', item_id: 'item_B', transcript: 'Make a note that the glaze is ready', event_id: 'ev_2' });
  assert.deepEqual(bound(calls[0]), { state: 'bound', index: 1, text: 'Make a note that the glaze is ready' });
  assert.deepEqual(seenAtTurn.at(-1), [{ state: 'bound', index: 1, text: 'Make a note that the glaze is ready' }],
    'not yet bound when the host heard the turn');                        // attribution runs before the host's callbacks
  assert.equal(seenAtTurn.length, 3);                                     // a duplicate completion: one item, no new input
});
// FR M2: xAI's cumulative interim transcript can arrive while he is still speaking, before speech_stopped; with or without
// a committed event after it, the response is bound to that utterance's item, never to his next words (the reviewer's
// probes/p1_xai_interim_binding.mjs, asserting the correct behaviour). xAI's real order is not observed live.
const upd = (item, text, completed = false, n = 0) => ({ type: 'conversation.item.input_audio_transcription.updated', item_id: item, transcript: text, completed, event_id: `u_${item}_${n}` });
for (const committed of [false, true]) {
  await check(`FR M2 xAI: an interim transcript before speech_stopped${committed ? ', then committed,' : ''} binds the response to that utterance`, async () => {
    const a = new XAIWebSocketAdapter();
    const { calls, bound } = session(a);
    a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });
    a._handleServerEvent(upd('item_A', 'Make a note that', false, 1));          // interim, while he speaks
    a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
    if (committed) a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: 'item_A' });
    a._handleServerEvent({ type: 'response.created', response: { id: 'resp_A' } });
    a._handleServerEvent(said('item_A', 'Make a note that the kiln is hot'));
    a._handleServerEvent(callIn('resp_A', 'call_A'));
    await new Promise((r) => setImmediate(r));
    assert.deepEqual(bound(calls[0]), { state: 'bound', index: 0, text: 'Make a note that the kiln is hot' });
    a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });          // his next words
    a._handleServerEvent(upd('item_B', 'Delete the old', false, 1));
    a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
    a._handleServerEvent(said('item_B', 'Delete the old glaze notes'));
    assert.deepEqual(bound(calls[0]), { state: 'bound', index: 0, text: 'Make a note that the kiln is hot' }, 'rebound to his next words');
  });
}
await check('V record: a response the page asked for answers none of his inputs (it was bound to the newest input before)', async () => {
  const a = new OpenAIWebRTCAdapter();
  const { c, calls, bound } = session(a);
  a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });
  a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
  a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: 'item_A' });
  a._handleServerEvent({ type: 'response.created', response: { id: 'resp_A' } });
  a._handleServerEvent(said('item_A', 'What time is it'));
  assert.equal(c.speak('Say this briefly, adding nothing: it is noon.'), true);
  a._handleServerEvent({ type: 'response.created', response: { id: 'resp_page_line' } });   // the page's speak()
  a._handleServerEvent(callIn('resp_page_line', 'call_P'));
  await new Promise((r) => setImmediate(r));
  assert.equal(bound(calls[0]), null);
});
await check('CX A1-C2: a call with no response, or from a response that answered no owner input, is bound to nothing', async () => {
  const a = new OpenAIWebRTCAdapter();
  const { c, bound } = session(a);
  a._handleServerEvent({ type: 'response.created', response: { id: 'resp_greeting' } });   // before he has said anything
  assert.equal(bound({ call_id: 'x', response_id: 'resp_greeting' }), null);
  assert.equal(bound({ call_id: 'y' }), null);
  assert.equal(bound({ call_id: 'z', response_id: 'resp_unknown' }), null);
  assert.ok(c);
});

// V R2-M1, R2-S1 (the reviewer's probes/r2_binding.mjs cases A and E, asserting the correct behaviour): a function result's
// follow-up starts while Robert has begun his next words. It answers the call it continues, never his new words, and never
// uses up his utterance, so his own next response answers his own words. On both providers; OpenAI with its response tag
// reported back, and without it (the order of the requests decides).
const tick = () => new Promise((r) => setImmediate(r));
const variants = [['OpenAI, tag reported back', () => new OpenAIWebRTCAdapter(), true, true],
  ['OpenAI, no tag reported back', () => new OpenAIWebRTCAdapter(), true, false], ['xAI', () => new XAIWebSocketAdapter(), false, false]];
const started = (s, id, echo) => ({ type: 'response.created', response: { id, ...(echo && s.tags.at(-1) ? { metadata: { minimoi_tag: s.tags.at(-1) } } : {}) } });
for (const [name, make, commits, echo] of variants) {
  await check(`V R2-S1 case A (${name}): a chained call in the follow-up acts on the request it continues, not on his new words`, async () => {
    const a = make(); const s = session(a, { trackResponses: true });
    a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });
    a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
    if (commits) a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: 'item_A' });
    a._handleServerEvent({ type: 'response.created', response: { id: 'R1' } });
    a._handleServerEvent(said('item_A', 'What is stuck on #12? Make a note of it.'));
    a._handleServerEvent(callIn('R1', 'call_1', 'consult')); await tick();               // its result is sent: the follow-up asked for
    a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });                  // he starts his next words meanwhile
    a._handleServerEvent(started(s, 'R_followup', echo));
    a._handleServerEvent(callIn('R_followup', 'call_2', 'save_note')); await tick();      // a chained call meant for the first request
    a._handleServerEvent(started(s, 'R_follow2', echo));                                 // that call's own follow-up
    a._handleServerEvent({ type: 'response.done', response: { id: 'R_follow2', output: [] } });
    a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
    if (commits) a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: 'item_B' });
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_B' } });
    a._handleServerEvent(said('item_B', 'No wait, delete the kiln notes'));
    a._handleServerEvent(callIn('R_B', 'call_B', 'save_note')); await tick();
    const first = { state: 'bound', index: 0, text: 'What is stuck on #12? Make a note of it.' };
    assert.deepEqual(s.bound(s.calls[0]), first);
    assert.deepEqual(s.bound(s.calls[1]), first, 'the chained call acted on his new words');
    assert.deepEqual(s.bound(s.calls[2]), { state: 'bound', index: 1, text: 'No wait, delete the kiln notes' });
  });
  await check(`V R2-M1 case E (${name}): his own response after a follow-up that started while he spoke answers his own words`, async () => {
    const a = make(); const s = session(a, { trackResponses: true });
    a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });
    a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
    if (commits) a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: 'item_A' });
    a._handleServerEvent({ type: 'response.created', response: { id: 'R1' } });
    a._handleServerEvent(said('item_A', 'What is stuck on twelve?'));
    a._handleServerEvent(callIn('R1', 'call_1', 'consult')); await tick();
    a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });                  // he starts B while the consult runs
    a._handleServerEvent(started(s, 'R_followup', echo));                               // the consult result's response
    a._handleServerEvent({ type: 'response.done', response: { id: 'R_followup', output: [] } });
    a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
    if (commits) a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: 'item_B' });
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_B' } });           // his own response for B
    a._handleServerEvent(callIn('R_B', 'call_B', 'save_note')); await tick();
    a._handleServerEvent(said('item_B', 'Make a note that the glaze is ready'));          // xAI: the transcript after the response
    assert.deepEqual(s.bound(s.calls[1]), { state: 'bound', index: 1, text: 'Make a note that the glaze is ready' },
      'bound to his previous request');
  });
}
// V note B: a noise utterance (speech with no input item) never makes the page's line, or a follow-up, wait for his next
// words, and an ended utterance with no input item expires on its own bound (MARK_TTL_MS, 3 s).
await check('V note B (xAI): after a cough, the page\'s line and a response of no known origin never wait for his next words', async () => {
  const a = new XAIWebSocketAdapter(); const s = session(a, { trackResponses: true });
  a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });
  a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
  a._handleServerEvent({ type: 'response.created', response: { id: 'R0' } });
  a._handleServerEvent(said('item_A', 'Status of twelve?'));
  a._handleServerEvent(callIn('R0', 'call_0', 'consult')); await tick();
  a._handleServerEvent({ type: 'response.created', response: { id: 'R_follow' } });       // its follow-up
  a._handleServerEvent({ type: 'response.done', response: { id: 'R_follow', output: [] } });
  const realNow = Date.now; let now = realNow();
  Date.now = () => now;
  try {
    a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });                  // a cough: no item follows
    a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
    now += 3000;                                                                          // its bound passes
    assert.equal(s.c.speak('Say this briefly, adding nothing: still checking.'), true);
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_page' } });
    a._handleServerEvent(callIn('R_page', 'call_p', 'consult')); await tick();
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_stray' } });      // a response of no known origin
    a._handleServerEvent(callIn('R_stray', 'call_s', 'consult')); await tick();
  } finally { Date.now = realNow; }
  a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });
  a._handleServerEvent(said('item_B', 'Cancel the nightly job'));
  assert.equal(s.bound(s.calls[1]), null, 'the page\'s line was bound to his next words');
  assert.equal(s.bound(s.calls[2]), null, 'an expired noise utterance bound a response to his next words');
});
await check('V note B: an ended utterance with no input item yet is still his within its bound (xAI: the transcript comes later)', async () => {
  const a = new XAIWebSocketAdapter(); const s = session(a, { trackResponses: true });
  const realNow = Date.now; let now = realNow();
  Date.now = () => now;
  try {
    a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });
    a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
    now += 2999;
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_A' } });
  } finally { Date.now = realNow; }
  a._handleServerEvent(callIn('R_A', 'call_A', 'save_note')); await tick();
  a._handleServerEvent(said('item_A', 'Make a note that the kiln is hot'));
  assert.deepEqual(s.bound(s.calls[0]), { state: 'bound', index: 0, text: 'Make a note that the kiln is hot' });
});

// R3-S1 (the reviewer's probes/r3_binding.mjs cases F, G and H, with the provider's follow-up responses starting as they
// would): by order alone (xAI; OpenAI until a tag comes back), a response that starts while a request of the controller's
// and his ended utterance both wait cannot be told apart. It binds nothing, so its calls are refused as not heard ("say it
// again"); no call ever acts on another turn's words. Both waits are let go, and the next turns bind as before.
const A_ = { state: 'bound', index: 0, text: 'What is stuck on twelve? Make a note of it.' };
for (const [name, make, commits] of [['xAI', () => new XAIWebSocketAdapter(), false], ['OpenAI in order mode', () => new OpenAIWebRTCAdapter(), true]]) {
  const utter = (a, item) => {
    a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });
    a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
    if (commits && item) a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: item });
  };
  const first = async (a) => {                                    // he asks A; its response calls consult; the result is sent
    utter(a, 'item_A');
    a._handleServerEvent({ type: 'response.created', response: { id: 'R1' } });
    a._handleServerEvent(said('item_A', A_.text));
    a._handleServerEvent(callIn('R1', 'call_1', 'consult'));
  };
  await check(`R3-S1 case F (${name}): a result sent as his speech stopped: his response and the follow-up bind nothing, never each other's turn`, async () => {
    const a = make(); const s = session(a, { trackResponses: true });
    await first(a);
    a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });                  // B begins
    await tick();                                                                       // the result is sent (its request recorded)
    a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });                 // the stop reaches the page just after
    if (commits) a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: 'item_B' });
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_B' } });           // the provider made his response first
    a._handleServerEvent(callIn('R_B', 'call_B', 'save_note')); await tick();
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_f' } });
    a._handleServerEvent(callIn('R_f', 'call_2', 'consult')); await tick();
    a._handleServerEvent(said('item_B', 'Make a note that the glaze is ready'));
    assert.deepEqual(s.bound(s.calls[0]), A_);
    assert.equal(s.bound(s.calls[1]), null, 'his request for B was bound to A');
    assert.equal(s.bound(s.calls[2]), null, 'the follow-up was bound to his new words');
  });
  await check(`R3-S1 case G (${name}): a cough, then a follow-up: the follow-up binds nothing, and his next request is his own`, async () => {
    const a = make(); const s = session(a, { trackResponses: true });
    await first(a);
    utter(a, 'item_cough');                                                             // a cough: on OpenAI committed, no words
    await tick();                                                                       // the result is sent within 3 s of it
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_f' } });
    a._handleServerEvent(callIn('R_f', 'call_2', 'save_note')); await tick();
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_f2' } });          // that call's own follow-up
    a._handleServerEvent({ type: 'response.done', response: { id: 'R_f2', output: [] } });
    utter(a, 'item_B');
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_B' } });
    a._handleServerEvent(callIn('R_B', 'call_B', 'save_note')); await tick();
    a._handleServerEvent(said('item_B', 'Make a note that the glaze is ready'));
    assert.equal(s.bound(s.calls[1]), null, 'the follow-up was bound to the cough or his next words');
    const b = s.bound(s.calls[2]);
    // W M1: on xAI the cough (no item) is still owed one when B's words appear, so they could be the cough's: re-asked.
    // OpenAI commits both, naming each item: B's request is his own.
    if (!commits) assert.equal(b, null, 'bound an item that could be the cough\'s');
    else assert.equal(b && b.text, 'Make a note that the glaze is ready', 'his next request was bound to another turn');
  });
  await check(`R3-S1 case H (${name}): a request that never starts: his next request binds nothing, and the one after is his own`, async () => {
    const a = make(); const s = session(a, { trackResponses: true });
    await first(a); await tick();                                                       // the result is sent; its response never starts
    utter(a, 'item_B');
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_B' } });
    a._handleServerEvent(callIn('R_B', 'call_B', 'save_note')); await tick();
    a._handleServerEvent(said('item_B', 'Make a note that the glaze is ready'));
    assert.equal(s.bound(s.calls[1]), null, 'his request was bound to the earlier turn');
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_Bf' } });          // "say it again": that result's follow-up
    a._handleServerEvent({ type: 'response.done', response: { id: 'R_Bf', output: [] } });
    utter(a, 'item_C');                                                                 // he says it again
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_C' } });
    a._handleServerEvent(callIn('R_C', 'call_C', 'save_note')); await tick();
    a._handleServerEvent(said('item_C', 'Make a note that the glaze is ready.'));
    assert.deepEqual(s.bound(s.calls[2]), { state: 'bound', index: 2, text: 'Make a note that the glaze is ready.' });
  });
}

// F1 (Codex's delta review at a17ddc8d, P1; its response_origin.mjs, asserting the correct behaviour): a response that
// starts after his utterance ended, before that utterance has an input item, answers that utterance, never a place in the
// list of inputs that a later utterance fills. Since W M1 the rule is one-to-one or nothing: it binds only an item shown to
// be its span's (named by the provider, or appearing while that span is the only one still owed an item, within 3 s of
// the end of its speech). An item that could be another span's, or none by the bound, binds nothing: its calls, and the
// follow-ups of its calls, answer none of his inputs ("I didn't catch that; say it again"). On xAI, and on OpenAI with and
// without its response tag reported back; on OpenAI the cough's utterance is not committed (the order in which OpenAI
// commits is not observed live).
const F1_LATER = 'Save a note: a later request';
for (const [name, make, commits, echo] of [['xAI', () => new XAIWebSocketAdapter(), false, false],
  ['OpenAI, tag reported back', () => new OpenAIWebRTCAdapter(), true, true],
  ['OpenAI, no tag reported back', () => new OpenAIWebRTCAdapter(), true, false]]) {
  const open = () => {
    const a = make(); const s = session(a, { trackResponses: true });
    if (echo) {                                                        // a page line, its tag reported back: echo is on
      assert.equal(s.c.speak('Say this briefly, adding nothing: one moment.'), true);
      a._handleServerEvent({ type: 'response.created', response: { id: 'R_line', metadata: { minimoi_tag: s.tags.at(-1) } } });
      a._handleServerEvent({ type: 'response.done', response: { id: 'R_line', output: [] } });
    }
    return { a, s };
  };
  const cough = (a) => {                                               // speech, no item, no transcript
    a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });
    a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
  };
  const utterance = (a, item, text) => {                               // a whole utterance with its words
    a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });
    a._handleServerEvent(said(item, text));
    a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
    if (commits) a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: item });
  };
  const clocked = async (fn) => {
    const real = Date.now; let at = real();
    Date.now = () => at;
    try { await fn((ms) => { at += ms; }); } finally { Date.now = real; }
  };
  for (const [gap, label] of [[20000, "Codex's case: 20 s later"], [1000, 'inside 3 s']]) {
    await check(`F1 ${label} (${name}): a cough's response never takes his later utterance`, async () => {
      const { a, s } = open();
      await clocked(async (pass) => {
        cough(a);
        a._handleServerEvent({ type: 'response.created', response: { id: 'noise_response' } });
        assert.deepEqual(s.bound({ response_id: 'noise_response' }), { state: 'pending' });
        pass(gap);
        utterance(a, 'later_item', F1_LATER);
        a._handleServerEvent({ type: 'response.created', response: { id: 'R_later' } });      // his own response
        a._handleServerEvent(callIn('noise_response', 'noise_call')); await tick();          // the cough's delayed call
        a._handleServerEvent(callIn('R_later', 'later_call')); await tick();
        pass(3000);                                                    // the cough's span is past its bound
        assert.equal(s.bound(s.calls[0]), null, "the cough's call took the later utterance's item and words");
        // W M1: inside 3 s on xAI the cough is still owed an item when his words appear, so they could be the cough's: his
        // own response re-asks ("say it again"). Where the provider names the item (OpenAI's commit), it binds.
        if (gap < 3000 && !commits) assert.equal(s.bound(s.calls[1]), null, 'bound an item that could be the cough\'s');
        else assert.deepEqual(s.bound(s.calls[1]), { state: 'bound', index: 0, text: F1_LATER });
      });
    });
  }
  await check(`F1 new speech before a late item (${name}): the binding is void, and the item is not taken`, async () => {
    const { a, s } = open();
    cough(a);                                                          // utterance A, its item not known yet
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_A' } });
    a._handleServerEvent(callIn('R_A', 'call_A')); await tick();
    assert.deepEqual(s.bound(s.calls[0]), { state: 'pending' });
    a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });                     // B begins first
    a._handleServerEvent(said('item_late', 'Make a note that the kiln is hot'));            // A's or B's: not known
    assert.equal(s.bound(s.calls[0]), null, 'bound to an item that appeared after new speech began');
  });
  await check(`F1 a late item with no new speech (${name}): still binds to that utterance`, async () => {
    const { a, s } = open();
    cough(a);
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_A' } });
    a._handleServerEvent(callIn('R_A', 'call_A')); await tick();
    if (commits) a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: 'item_A' });   // a late commit
    a._handleServerEvent(said('item_A', 'Make a note that the kiln is hot'));
    assert.deepEqual(s.bound(s.calls[0]), { state: 'bound', index: 0, text: 'Make a note that the kiln is hot' });
  });
  await check(`F1 its own bound passes without an item (${name}): void, never pending for ever`, async () => {
    const { a, s } = open();
    await clocked(async (pass) => {
      cough(a);
      pass(500);
      a._handleServerEvent({ type: 'response.created', response: { id: 'R_A' } });
      a._handleServerEvent(callIn('R_A', 'call_A')); await tick();
      assert.deepEqual(s.bound(s.calls[0]), { state: 'pending' });
      pass(2500);                                                      // 3 s since his speech ended
      assert.equal(s.bound(s.calls[0]), null, 'still pending past its bound');
      a._handleServerEvent(said('item_late', 'Make a note that the glaze is ready'));     // no new speech, but too late
      assert.equal(s.bound(s.calls[0]), null, 'bound to an item that appeared past its bound');
    });
  });
  await check(`F1 follow-up inheritance (${name}): the follow-up of a voided response's call answers none of his inputs`, async () => {
    const { a, s } = open();
    await clocked(async (pass) => {
      cough(a);
      a._handleServerEvent({ type: 'response.created', response: { id: 'R0' } });
      a._handleServerEvent(callIn('R0', 'call_0', 'consult')); await tick();                 // its result: the follow-up asked for
      a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });                     // B begins: R0's binding is void
      a._handleServerEvent(started(s, 'R_f', echo));                                          // the follow-up starts
      a._handleServerEvent(callIn('R_f', 'call_f', 'save_note')); await tick();
      a._handleServerEvent(started(s, 'R_f2', echo));                                         // that call's own follow-up
      a._handleServerEvent({ type: 'response.done', response: { id: 'R_f2', output: [] } });
      a._handleServerEvent(said('item_B', 'Make a note that the glaze is ready'));
      a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
      if (commits) a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: 'item_B' });
      a._handleServerEvent({ type: 'response.created', response: { id: 'R_B' } });
      a._handleServerEvent(callIn('R_B', 'call_B', 'save_note')); await tick();
      pass(3000);                                                        // the cough's span is past its bound
      assert.equal(s.bound(s.calls[0]), null, "the voided response's call was bound");
      assert.equal(s.bound(s.calls[1]), null, 'the follow-up took his new words');
      // W M1: on xAI his words appeared while the cough was still owed an item: re-asked. OpenAI's commit names them.
      if (!commits) assert.equal(s.bound(s.calls[2]), null, 'bound an item that could be the cough\'s');
      else assert.deepEqual(s.bound(s.calls[2]), { state: 'bound', index: 0, text: 'Make a note that the glaze is ready' });
    });
  });
  // L M1 (the last pass's verification, must-fix; the reviewer's probe, asserting the correct behaviour): the cough's
  // response starts after Robert has begun his next words. Under W M1's one-to-one rule an item appearing then could be
  // either utterance's, so the cough's response binds nothing.
  await check(`L M1 (${name}): a cough's response that starts after his next speech began never takes that speech`, async () => {
    const { a, s } = open();
    await clocked(async (pass) => {
      cough(a);
      pass(300);
      a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });                   // Robert starts
      a._handleServerEvent({ type: 'response.created', response: { id: 'noise' } });         // the cough's response, late
      pass(700);
      a._handleServerEvent(said('later_item', 'Save a note: the real request'));
      a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
      if (commits) a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: 'later_item' });
      a._handleServerEvent({ type: 'response.created', response: { id: 'R_real' } });         // his own response
      a._handleServerEvent(callIn('noise', 'noise_call')); await tick();
      a._handleServerEvent(callIn('R_real', 'real_call')); await tick();
      pass(3000);                                                      // the cough's span is past its bound
      assert.equal(s.bound(s.calls[0]), null, "the cough's call took his next words");
      // W M1: on xAI his words appeared while the cough was still owed an item: re-asked. OpenAI's commit names them.
      if (!commits) assert.equal(s.bound(s.calls[1]), null, 'bound an item that could be the cough\'s');
      else assert.deepEqual(s.bound(s.calls[1]), { state: 'bound', index: 0, text: 'Save a note: the real request' });
    });
  });
  // L N1 (pre-existing; the reviewer's probe): two speech spans end before one response starts, and neither item is known
  // yet. The response could answer either: it binds nothing, never the first item to appear. Where every span's item is
  // already known, today's rule holds (the newest).
  await check(`L N1 (${name}): merged speech spans whose items are not known bind nothing`, async () => {
    const { a, s } = open();
    cough(a); cough(a);                                                // U1 ends, then U2 starts and ends
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_both' } });
    a._handleServerEvent(said('item_u1', 'Hmm, let me think.'));
    a._handleServerEvent(said('item_u2', 'Save a note that the kiln is hot'));
    a._handleServerEvent(callIn('R_both', 'call_both')); await tick();
    assert.equal(s.bound(s.calls[0]), null, 'a merged response was bound to the first item to appear');
  });
  // W M1 restates this L N1 case: U1 has its item, and U2's item appears while U2 is the only span still owed one, so it is
  // one-to-one and binds (L N1 had refused it). On OpenAI U2 is not committed, so it has no response of its own.
  await check(`L N1 (${name}): merged speech spans, the second item appearing while only its span is owed, bind one-to-one`, async () => {
    const { a, s } = open();
    utterance(a, 'item_u1', 'Hmm, let me think.');                    // U1's item known
    cough(a);                                                          // U2 ends, its item not known yet
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_both' } });
    a._handleServerEvent(said('item_u2', 'Save a note that the kiln is hot'));
    a._handleServerEvent(callIn('R_both', 'call_both')); await tick();
    if (commits) assert.equal(s.bound(s.calls[0]), null);
    else assert.deepEqual(s.bound(s.calls[0]), { state: 'bound', index: 1, text: 'Save a note that the kiln is hot' });
  });
  await check(`L N1 guard (${name}): merged speech spans whose items are all known still bind the newest`, async () => {
    const { a, s } = open();
    utterance(a, 'item_u1', 'Hmm, let me think.');
    utterance(a, 'item_u2', 'Save a note that the kiln is hot');
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_both' } });
    a._handleServerEvent(callIn('R_both', 'call_both')); await tick();
    assert.deepEqual(s.bound(s.calls[0]), { state: 'bound', index: 1, text: 'Save a note that the kiln is hot' });
  });
  // W M1 (the verification at d886bbf0, must-fix; the mirror of L M1): an utterance U0 ends with no item; Robert begins U1;
  // U0's late item appears while U0 is still owed it. It cannot be shown whose it is, so neither U0's response nor U1's
  // takes it: attribution is one-to-one or nothing. The reviewer's three orders, the hold variant and the L M1 path. On
  // OpenAI this is its first utterance (no commit seen before U1's own).
  const A0 = 'Save a note that A', B1 = 'and check the board';
  const upd = (item, text) => ({ type: 'conversation.item.input_audio_transcription.updated', item_id: item, transcript: text, completed: false, event_id: `u_${item}` });
  const vm1 = [
    ['order 1: U0\'s item during U1, U1\'s response before U1\'s item', (a, pass) => {
      a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });
      pass(300); a._handleServerEvent(said('u0', A0));
      pass(1500); a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
      a._handleServerEvent({ type: 'response.created', response: { id: 'R1' } });
      if (commits) a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: 'u1' });
      a._handleServerEvent(said('u1', B1));
    }],
    ['order 2: U1\'s interim first, then U0\'s item', (a, pass) => {
      a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });
      pass(300); a._handleServerEvent(upd('u1', 'and check'));
      pass(200); a._handleServerEvent(said('u0', A0));
      pass(1000); a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
      if (commits) a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: 'u1' });
      a._handleServerEvent({ type: 'response.created', response: { id: 'R1' } });
      a._handleServerEvent(said('u1', B1));
    }],
    ['order 3 (the hold): U1\'s response starts, then U0\'s item', (a, pass) => {
      a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });
      pass(1000); a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
      a._handleServerEvent({ type: 'response.created', response: { id: 'R1' } });
      pass(200); a._handleServerEvent(said('u0', A0));
      if (commits) a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: 'u1' });
      a._handleServerEvent(said('u1', B1));
    }]];
  for (const [label, rest] of vm1) {
    for (const lmPath of [false, true]) {
      await check(`W M1 ${label}${lmPath ? ', R0 after U1 began (the L M1 path)' : ''} (${name}): neither response takes U0's late item`, async () => {
        const { a, s } = open();
        await clocked(async (pass) => {
          cough(a);                                                    // U0 ends: "Save a note that A", no item yet
          if (!lmPath) a._handleServerEvent({ type: 'response.created', response: { id: 'R0' } });
          pass(600);
          rest(a, (ms) => { pass(ms); if (lmPath && !s.started) { s.started = true; a._handleServerEvent({ type: 'response.created', response: { id: 'R0' } }); } });
          a._handleServerEvent(callIn('R0', 'c0')); await tick();
          a._handleServerEvent(callIn('R1', 'c1')); await tick();
          const r0 = s.bound(s.calls[0]), r1 = s.bound(s.calls[1]);
          assert.notEqual(r1 && r1.text, A0, "U1's response took U0's words");
          assert.notEqual(r0 && r0.text, B1, "U0's response took U1's words");
          assert.equal(r1, null, "U1's response bound an item it cannot prove is U1's");
          assert.equal(r0 && r0.state === 'bound' ? r0.text : null, null, "U0's response bound an item it cannot prove is U0's");
        });
      });
    }
  }
  // W N4 (the verification at d886bbf0): items are counted per span, one-to-one. One span that yields two items binds
  // neither fragment; a span with two items plus a span with none binds nothing.
  await check(`W N4 (${name}): one span that yields two items binds neither fragment`, async () => {
    const { a, s } = open();
    a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });
    a._handleServerEvent(said('frag_a', 'Make a note'));
    a._handleServerEvent(said('frag_b', 'that the kiln is hot'));
    a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
    a._handleServerEvent({ type: 'response.created', response: { id: 'R' } });
    a._handleServerEvent(callIn('R', 'c')); await tick();
    assert.equal(s.bound(s.calls[0]), null);
  });
  await check(`W N4 (${name}): a span with two items and a span with none bind nothing`, async () => {
    const { a, s } = open();
    await clocked(async (pass) => {
      a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });
      a._handleServerEvent(said('frag_a', 'Make a note'));
      a._handleServerEvent(said('frag_b', 'that the kiln is hot'));
      a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
      cough(a);
      a._handleServerEvent({ type: 'response.created', response: { id: 'R' } });
      a._handleServerEvent(callIn('R', 'c')); await tick();
      pass(3000);
      assert.equal(s.bound(s.calls[0]), null);
    });
  });
}

// W M1: where the provider names a span's item, the link stands and nothing is inferred. OpenAI's speech_started carries the
// item id its speech will be committed as: a cough (no item) followed within 3 s by a request whose speech_started names its
// item binds that request even with no commit at all, where by order alone it would be asked again.
await check('W M1 (OpenAI): an item named at speech_started is that speech\'s, even inside a cough\'s bound and with no commit', async () => {
  const a = new OpenAIWebRTCAdapter(); const s = session(a, { trackResponses: true });
  const real = Date.now; let at = real();
  Date.now = () => at;
  try {
    a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });                     // a cough: no item named
    a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
    a._handleServerEvent({ type: 'response.created', response: { id: 'R0' } });
    at += 1000;
    a._handleServerEvent({ type: 'input_audio_buffer.speech_started', item_id: 'item_B' });  // his request, its item named
    a._handleServerEvent(said('item_B', 'Make a note that the glaze is ready'));
    a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_B' } });
    a._handleServerEvent(callIn('R0', 'call_0')); await tick();
    a._handleServerEvent(callIn('R_B', 'call_B')); await tick();
    at += 3000;                                                                             // the cough's span is past its bound
    assert.equal(s.bound(s.calls[0]), null);
    assert.deepEqual(s.bound(s.calls[1]), { state: 'bound', index: 0, text: 'Make a note that the glaze is ready' });
  } finally { Date.now = real; }
});

// L N2 (the last pass's verification): an expired hold is dropped where it is read, so a later utterance reported only by
// its commit (no speech events) is still a new utterance with its own response.
await check('L N2 (OpenAI): after a hold expires, a commit-only utterance is his, and its response binds to it', async () => {
  const a = new OpenAIWebRTCAdapter(); const s = session(a, { trackResponses: true });
  const real = Date.now; let at = real();
  Date.now = () => at;
  try {
    a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });                     // a cough, no commit
    a._handleServerEvent({ type: 'input_audio_buffer.speech_stopped' });
    a._handleServerEvent({ type: 'response.created', response: { id: 'R0' } });              // holds the cough
    at += 3000;                                                                             // the hold's bound passes
    a._handleServerEvent({ type: 'input_audio_buffer.committed', item_id: 'item_B' });       // no speech events
    a._handleServerEvent({ type: 'response.created', response: { id: 'R_B' } });
    a._handleServerEvent(said('item_B', 'Make a note that the glaze is ready'));
    a._handleServerEvent(callIn('R_B', 'call_B')); await tick();
  } finally { Date.now = real; }
  assert.deepEqual(s.bound(s.calls[0]), { state: 'bound', index: 0, text: 'Make a note that the glaze is ready' });
});

// F2 (Codex's delta review at a17ddc8d; its response_origin.mjs precursor, asserting the correct behaviour): a host that
// asks for it (oncePerItem: MC's page) hears each owner item's completion once. A replayed provider event, or a second
// completion of an item already completed, is not a new turn. German and Portuguese pass no option and hear every
// completion as before (pinned here and in test_controller_no_change.mjs).
for (const [label, options, expected] of [
  ['a host that asks (oncePerItem)', { oncePerItem: true }, ['Save a note: first request', 'Save a note: second request']],
  ['German (no option): as before', {}, ['Save a note: first request', 'Save a note: second request', 'Save a note: first request',
    'Save a note: second request']]]) {
  await check(`F2 xAI, ${label}: a replayed completion, and a second completion of one item`, async () => {
    const a = new XAIWebSocketAdapter(); const turns = [];
    const c = new RealtimeVoiceController({ ...options, onUserTurn: (text) => turns.push(text) });
    c._adapter = a; c._state = 'active'; c._wireAdapterEvents();
    const first = said('item_a', 'Save a note: first request');
    a._handleServerEvent(first);
    a._handleServerEvent({ type: 'conversation.item.input_audio_transcription.updated', item_id: 'item_b',
      transcript: 'Save a note: second request', completed: true, event_id: 'ev_b_upd' });
    a._handleServerEvent(first);                                                         // the identical event, replayed
    a._handleServerEvent(said('item_b', 'Save a note: second request'));                 // item_b completed again
    assert.deepEqual(turns, expected);
  });
}

if (failures.length) {
  console.log(`${failures.length} of ${cases} response-binding regressions FAILED:\n  ${failures.join('\n  ')}`);
  process.exit(1);
}
console.log(`${cases} response-binding regressions passed (CX A1-C2, FR M2, V R2-M1, R3-S1, F1, F2, L M1, L N1, L N2, W M1, W N4); real controller and adapters, synthetic provider events.`);
