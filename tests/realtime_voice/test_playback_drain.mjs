// Codex's review B6 as a regression asserting the CORRECT behaviour (converted from review-evidence-codex/repro_playback_end.mjs,
// which asserted the defect): with the real controller and the real xAI adapter on a synthetic audio context, a host that ends
// after its closing line waits for the queued audio to play out (controller.afterPlayback, the adapters' optional
// whenPlaybackIdle) before the context is closed. Also the OpenAI adapter's provider signal, barge-in and the bound.
// No provider, microphone or network. VOICE_REVIEW_ROOT=<checkout> runs it against another tree (it fails at 93bf00c6).
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
        addEventListener(type, fn) { source.listeners[type] = fn; }, end() { source.listeners.ended?.(); } };
      sources.push(source);
      return source;
    } };
}
const CHUNK = Buffer.alloc(9600).toString('base64');                     // 0.2 s of silence at 24 kHz, PCM16
function xaiSession() {
  const a = new XAIWebSocketAdapter();
  a._audioContext = context();
  const c = new RealtimeVoiceController({});
  c._adapter = a; c._state = 'active'; c._wireAdapterEvents();
  return { a, c, ctx: a._audioContext };
}

await check('B6 xAI: response.done with queued audio does not close the context until the audio has played', async () => {
  const { a, c, ctx } = xaiSession();
  let done = false;
  c._onAssistantTurn = () => c.afterPlayback(15000).then(() => { done = true; c.endSession('normal_end'); });
  for (let i = 0; i < 20; i++) a._handleServerEvent({ type: 'response.output_audio.delta', delta: CHUNK });   // four seconds queued
  c._pendingAssistantTurns.push('Synthetic spoken summary');
  a._handleServerEvent({ type: 'response.done', response: { output: [] } });
  await flush();
  assert.equal(ctx.closed, 0, 'closed at generation completion with audio still queued');
  assert.equal(done, false);
  for (const s of ctx.sources.slice(0, -1)) s.end();
  await flush();
  assert.equal(ctx.closed, 0, 'closed before the last chunk played');
  ctx.sources.at(-1).end();
  await flush();
  assert.equal(done, true); assert.equal(ctx.closed, 1);
});
await check('B6 xAI: a response with no audio lets the host end at once', async () => {
  const { c } = xaiSession();
  let done = false;
  c.afterPlayback(15000).then(() => { done = true; });
  await flush();
  assert.equal(done, true);
});
await check('B6 xAI: barge-in (playback stopped) counts as played out', async () => {
  const { a, c } = xaiSession();
  a._handleServerEvent({ type: 'response.output_audio.delta', delta: CHUNK });
  let done = false;
  c.afterPlayback(15000).then(() => { done = true; });
  await flush(); assert.equal(done, false);
  a._handleServerEvent({ type: 'input_audio_buffer.speech_started' });
  await flush(); assert.equal(done, true);
});
await check('B6 xAI: playback that never ends is waited for at most maxMs', async () => {
  const { a, c } = xaiSession();
  a._handleServerEvent({ type: 'response.output_audio.delta', delta: CHUNK });
  const started = Date.now();
  await c.afterPlayback(30);
  assert.ok(Date.now() - started >= 25);
});
await check('B6 OpenAI: the host waits for output_audio_buffer.stopped (or cleared) when audio started', async () => {
  for (const last of ['output_audio_buffer.stopped', 'output_audio_buffer.cleared']) {
    const a = new OpenAIWebRTCAdapter();
    const c = new RealtimeVoiceController({});
    c._adapter = a;
    let done = false;
    const nothing = c.afterPlayback(15000);                                    // nothing playing: at once
    await nothing;
    a._handleServerEvent({ type: 'output_audio_buffer.started' });
    c.afterPlayback(15000).then(() => { done = true; });
    a._handleServerEvent({ type: 'response.done', response: { output: [] } });
    await flush(); assert.equal(done, false, last);
    a._handleServerEvent({ type: last });
    await flush(); assert.equal(done, true, last);
  }
});
await check('B6 a controller whose adapter cannot tell lets the host end at once (German and Portuguese never ask)', async () => {
  const c = new RealtimeVoiceController({});
  c._adapter = { on() {} };
  let done = false;
  c.afterPlayback(15000).then(() => { done = true; });
  await flush();
  assert.equal(done, true);
});

if (failures.length) {
  console.log(`${failures.length} of ${cases} playback-drain regressions FAILED:\n  ${failures.join('\n  ')}`);
  process.exit(1);
}
console.log(`${cases} playback-drain regressions passed (review B6); synthetic audio context, no provider or microphone.`);
