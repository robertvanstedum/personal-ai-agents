// Review CX A1-C2 (Spec 165 §5.1): the real controller and the real adapters bind each function call, through its response id,
// to the owner input item that response answers: the item committed before the response started (OpenAI's
// input_audio_buffer.committed), or, while his speech has ended with its item not yet known (xAI without that event), the next
// item to appear. Never whichever transcript finished last. Synthetic provider events only: no provider, microphone or
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

function session(adapter) {
  const calls = [], turns = [], seenAtTurn = [];
  const c = new RealtimeVoiceController({
    onFunctionCall: async (call) => { calls.push(call); return {}; },
    onTranscriptUpdate: (list) => { turns.splice(0, turns.length, ...list); },
    onUserTurn: () => { seenAtTurn.push(calls.map((call) => c.inputOf?.(call))); },
  });
  c._adapter = adapter; c._state = 'active'; c._wireAdapterEvents();
  adapter.sendFunctionResult = () => {};
  const bound = (call) => (typeof c.inputOf === 'function' ? c.inputOf(call) : { state: 'none' });
  return { c, calls, turns, bound, seenAtTurn };
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
await check('CX A1-C2: a call with no response, or from a response that answered no owner input, is bound to nothing', async () => {
  const a = new OpenAIWebRTCAdapter();
  const { c, bound } = session(a);
  a._handleServerEvent({ type: 'response.created', response: { id: 'resp_greeting' } });   // before he has said anything
  assert.equal(bound({ call_id: 'x', response_id: 'resp_greeting' }), null);
  assert.equal(bound({ call_id: 'y' }), null);
  assert.equal(bound({ call_id: 'z', response_id: 'resp_unknown' }), null);
  assert.ok(c);
});

if (failures.length) {
  console.log(`${failures.length} of ${cases} response-binding regressions FAILED:\n  ${failures.join('\n  ')}`);
  process.exit(1);
}
console.log(`${cases} response-binding regressions passed (CX A1-C2); real controller and adapters, synthetic provider events.`);
