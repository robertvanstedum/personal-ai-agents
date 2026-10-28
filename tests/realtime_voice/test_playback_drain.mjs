// Codex's review B6, as regressions on Spec 165 §5.3's contract: the spoken summary is one tracked response, and the session
// ends when that response's audio has drained (the controller's optional onPlaybackDrained(responseId), fed by the adapters),
// at barge-in, or at its bound. Real controller and real adapters on a synthetic audio context and synthetic provider
// events; no provider, microphone or network.
//
// The host here behaves as a host page does: it asks with speakTracked() and ends on its response's drain when the controller
// offers them, else (an older controller) it ends at the summary's generation end, as A1's page did before its playback wait.
// So against an older tree (VOICE_REVIEW_ROOT=<checkout>) the cases fail on cut-off audio, not on a missing method (review
// R2 N10).
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

const root = process.env.VOICE_REVIEW_ROOT ? pathToFileURL(process.env.VOICE_REVIEW_ROOT + '/') : new URL('../../', import.meta.url);
const { XAIWebSocketAdapter } = await import(new URL('core/realtime_voice/static/adapters/xai-websocket-adapter.js', root));
const { OpenAIWebRTCAdapter } = await import(new URL('core/realtime_voice/static/adapters/openai-webrtc-adapter.js', root));
const { RealtimeVoiceController } = await import(new URL('core/realtime_voice/static/realtime-voice-controller.js', root));
const flush = async (n = 6) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)); };
const failures = [];
let cases = 0;
async function check(name, fn) { cases++; try { await fn(); } catch (e) { failures.push(`${name}: ${e.message.split('\n')[0]}`); } }

// A synthetic audio context: each scheduled chunk is a source that ends when the test says so.
function context() {
  const sources = [];
  return { sources, closed: 0, currentTime: 1, destination: {},
    close() { this.closed += 1; },
    createBuffer(_channels, length, rate) { return { duration: length / rate, copyToChannel() {} }; },
    createBufferSource() {
      const source = { listeners: {}, connect() {}, start() {}, stop() { source.end(); },
        addEventListener(type, fn) { source.listeners[type] = fn; }, end() { const fn = source.listeners.ended; source.listeners.ended = null; fn?.(); } };
      sources.push(source);
      return source;
    } };
}
const CHUNK = Buffer.alloc(9600).toString('base64');                     // 0.2 s of silence at 24 kHz, PCM16

// The host: the summary instruction, tracked; the end when its own response drains (or, on an older controller, at its turn).
function host(adapter) {
  const ended = [];
  let mine;
  const c = new RealtimeVoiceController({
    trackResponses: true,                                             // as the direct session module does
    onFunctionCall: async () => ({ ok: true, outcome: 'done', spoken: 'Done.' }),
    onPlaybackDrained: (id) => { if (mine !== undefined && id === mine) { ended.push('drained'); c.endSession('normal_end'); } },
    onAssistantTurn: () => { if (typeof c.speakTracked !== 'function' && !ended.length) { ended.push('turn'); c.endSession('normal_end'); } },
  });
  c._adapter = adapter; c._state = 'active'; c._wireAdapterEvents();
  const tags = [];                                                    // what each tagged request asked the provider to echo
  adapter.sendContinuationInstruction = (_text, tag) => { tags.push(tag || null); };
  adapter.sendFunctionResult = (_id, _output, tag) => { tags.push(tag || null); };
  const ask = async () => {
    if (typeof c.speakTracked === 'function') { const p = c.speakTracked('Say only this, then stop: …'); return p.then((id) => { mine = id; }); }
    c.speak?.('Say only this, then stop: …');
  };
  // The provider's response.created for the instruction: OpenAI echoes the response metadata it was sent (review FR N3).
  const created = (id, tag = tags.at(-1)) => ({ type: 'response.created', response: { id, ...(tag ? { metadata: { minimoi_tag: tag } } : {}) } });
  return { c, ended, ask, created, tags, mine: () => mine };
}
function xai() {
  const a = new XAIWebSocketAdapter();
  a._audioContext = context();
  a.end = () => { a._audioContext?.close(); };
  return { a, ctx: a._audioContext, ...host(a) };
}

await check('B6 xAI: the summary\'s response done with audio queued is not cut: the session ends after the last chunk plays', async () => {
  const { a, ctx, c, ended, ask } = xai();
  const asked = ask();
  a._handleServerEvent({ type: 'response.created', response: { id: 'resp_sum' } });
  await asked;
  for (let i = 0; i < 20; i++) a._handleServerEvent({ type: 'response.output_audio.delta', response_id: 'resp_sum', delta: CHUNK });
  c._pendingAssistantTurns.push('We looked at item twelve.');
  a._handleServerEvent({ type: 'response.done', response: { id: 'resp_sum', output: [] } });
  await flush();
  assert.equal(ctx.closed, 0, 'audio cut off: closed at generation end with four seconds still queued');
  for (const s of ctx.sources.slice(0, -1)) s.end();
  await flush();
  assert.equal(ctx.closed, 0, 'audio cut off: closed before the last chunk played');
  ctx.sources.at(-1).end();
  await flush();
  assert.deepEqual(ended, ['drained']); assert.equal(ctx.closed, 1);
});
await check('B6 xAI: another response draining first never closes the summary', async () => {
  const { a, ctx, ended, ask } = xai();
  a._handleServerEvent({ type: 'response.created', response: { id: 'resp_other' } });   // a reply, its audio still playing
  a._handleServerEvent({ type: 'response.output_audio.delta', response_id: 'resp_other', delta: CHUNK });
  a._handleServerEvent({ type: 'response.done', response: { id: 'resp_other', output: [] } });
  const asked = ask();                                                    // at a pause: no response under way (FR N3)
  a._handleServerEvent({ type: 'response.created', response: { id: 'resp_sum' } });
  await asked;
  a._handleServerEvent({ type: 'response.output_audio.delta', response_id: 'resp_sum', delta: CHUNK });
  ctx.sources[0].end();                                                   // the other response's audio drains
  await flush();
  assert.deepEqual(ended, [], 'closed by another response');
  a._handleServerEvent({ type: 'response.done', response: { id: 'resp_sum', output: [] } });
  ctx.sources[1].end();
  await flush();
  assert.deepEqual(ended, ['drained']);
});
await check('B6 xAI: barge-in stops what was queued, and that counts as drained', async () => {
  const { a, ended, ask } = xai();
  const asked = ask();
  a._handleServerEvent({ type: 'response.created', response: { id: 'resp_sum' } });
  await asked;
  a._handleServerEvent({ type: 'response.output_audio.delta', response_id: 'resp_sum', delta: CHUNK });
  a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });
  await flush();
  assert.deepEqual(ended, ['drained']);
});
await check('B6 xAI: a response with no audio drains at its end', async () => {
  const { a, ended, ask } = xai();
  const asked = ask();
  a._handleServerEvent({ type: 'response.created', response: { id: 'resp_sum' } });
  await asked;
  a._handleServerEvent({ type: 'response.done', response: { id: 'resp_sum', output: [] } });
  await flush();
  assert.deepEqual(ended, ['drained']);
});
await check('B6 OpenAI: the summary ends at output_audio_buffer.stopped for its response, not at response.done', async () => {
  for (const last of ['output_audio_buffer.stopped', 'output_audio_buffer.cleared']) {
    const a = new OpenAIWebRTCAdapter();
    let closed = 0;
    a.end = () => { closed += 1; };
    const { ended, ask, created } = host(a);
    const asked = ask();
    a._handleServerEvent(created('resp_sum'));
    await asked;
    a._handleServerEvent({ type: 'output_audio_buffer.started', response_id: 'resp_sum' });
    a._handleServerEvent({ type: 'response.done', response: { id: 'resp_sum', output: [{ content: [{ type: 'output_audio' }] }] } });
    await flush();
    assert.equal(closed, 0, `${last}: audio cut off at response.done`);
    a._handleServerEvent({ type: 'output_audio_buffer.started', response_id: 'resp_other' });
    a._handleServerEvent({ type: 'output_audio_buffer.stopped', response_id: 'resp_other' });
    await flush();
    assert.equal(closed, 0, `${last}: closed by another response`);
    a._handleServerEvent({ type: last, response_id: 'resp_sum' });
    await flush();
    assert.deepEqual(ended, ['drained'], last); assert.equal(closed, 1, last);
  }
});
await check('CX A1-C4 OpenAI: a summary response with no audio, or cancelled with none, drains at its end', async () => {
  for (const status of ['completed', 'cancelled']) {
    const a = new OpenAIWebRTCAdapter();
    let closed = 0;
    a.end = () => { closed += 1; };
    const { ended, ask, created } = host(a);
    const asked = ask();
    a._handleServerEvent(created('resp_sum'));
    await asked;
    a._handleServerEvent({ type: 'response.done', response: { id: 'resp_sum', status, output: [] } });
    await flush();
    assert.deepEqual(ended, ['drained'], status); assert.equal(closed, 1, status);
  }
});
await check('B6 German and Portuguese pass no callback: drains reach nobody and their session runs as before', async () => {
  const a = new XAIWebSocketAdapter();
  a._audioContext = context();
  const c = new RealtimeVoiceController({});
  c._adapter = a; c._state = 'active'; c._wireAdapterEvents();
  a._handleServerEvent({ type: 'response.created', response: { id: 'r1' } });
  a._handleServerEvent({ type: 'response.output_audio.delta', response_id: 'r1', delta: CHUNK });
  a._handleServerEvent({ type: 'response.done', response: { id: 'r1', output: [] } });
  a._audioContext.sources[0].end();
  await flush();
  assert.equal(c._state, 'active'); assert.equal(a._audioContext.closed, 0);
});

// FR N3, V note D: the summary's response is its own even when a function result's follow-up was asked for just before
// it and starts first. The controller asks for both, in order: OpenAI knows each by the tag it reports back; xAI by order.
const reply = { type: 'response.done', response: { id: 'resp_reply', output: [{ type: 'function_call', call_id: 'call_r', name: 'consult', arguments: '{}' }] } };
await check('FR N3 OpenAI: a function result\'s follow-up asked for before the summary, and a response of his, are not the summary', async () => {
  const a = new OpenAIWebRTCAdapter();
  let closed = 0;
  a.end = () => { closed += 1; };
  const { ended, ask, created, tags, mine } = host(a);
  a._handleServerEvent({ type: 'response.created', response: { id: 'resp_reply' } });
  a._handleServerEvent(reply); await flush();                             // its result is sent: a follow-up is asked for
  const asked = ask();
  const [follow, summary] = tags;
  a._handleServerEvent(created('resp_fn', follow));                      // the follow-up starts first
  a._handleServerEvent({ type: 'response.created', response: { id: 'resp_his' } });     // untagged: the provider's own
  a._handleServerEvent({ type: 'response.done', response: { id: 'resp_fn', output: [] } });   // drains with no audio
  await flush();
  assert.equal(mine(), undefined); assert.deepEqual(ended, []);
  a._handleServerEvent(created('resp_sum', summary));
  await asked;
  assert.equal(mine(), 'resp_sum');
  a._handleServerEvent({ type: 'response.done', response: { id: 'resp_sum', output: [] } });
  await flush();
  assert.deepEqual(ended, ['drained']); assert.equal(closed, 1);
});
await check('V note D xAI (no tag reported back): a follow-up asked for just before the summary starts first; the summary is its own', async () => {
  const { a, ctx, ended, ask, mine } = xai();
  a._handleServerEvent({ type: 'response.created', response: { id: 'resp_reply' } });
  a._handleServerEvent(reply); await flush();                             // the function result's response.create is on the wire
  const asked = ask();
  a._handleServerEvent({ type: 'response.created', response: { id: 'resp_fn' } });      // the follow-up, not started when asked
  a._handleServerEvent({ type: 'response.done', response: { id: 'resp_fn', output: [] } });
  a._handleServerEvent({ type: 'response.created', response: { id: 'resp_sum' } });
  await asked;
  assert.equal(mine(), 'resp_sum', 'the summary took the follow-up\'s response');
  await flush();
  assert.deepEqual(ended, []);                                            // the follow-up's drain did not close it
  a._handleServerEvent({ type: 'response.output_audio.delta', response_id: 'resp_sum', delta: CHUNK });
  a._handleServerEvent({ type: 'response.done', response: { id: 'resp_sum', output: [] } });
  ctx.sources.at(-1).end();
  await flush();
  assert.deepEqual(ended, ['drained']);
});

if (failures.length) {
  console.log(`${failures.length} of ${cases} playback-drain regressions FAILED:\n  ${failures.join('\n  ')}`);
  process.exit(1);
}
console.log(`${cases} playback-drain regressions passed (review B6, Spec 165 §5.3); synthetic audio context, no provider or microphone.`);
