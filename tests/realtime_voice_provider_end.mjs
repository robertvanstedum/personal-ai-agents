/** German and Portuguese's path through the real controller and the real OpenAI and xAI adapters: when the provider ends a
 *  session, the microphone is released. Only each adapter's connect is synthetic: no network, microphone or provider. */
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const root = fileURLToPath(new URL('../core/realtime_voice/static/', import.meta.url));
const load = async text => import('data:text/javascript;base64,' + Buffer.from(text).toString('base64'));
const {OpenAIWebRTCAdapter} = await load(fs.readFileSync(root + 'adapters/openai-webrtc-adapter.js', 'utf8'));
const {XAIWebSocketAdapter} = await load(fs.readFileSync(root + 'adapters/xai-websocket-adapter.js', 'utf8'));
globalThis.RealOpenAI = OpenAIWebRTCAdapter;
globalThis.RealXAI = XAIWebSocketAdapter;
const source = 'const OpenAIWebRTCAdapter=globalThis.RealOpenAI,XAIWebSocketAdapter=globalThis.RealXAI;\n'
  + fs.readFileSync(root + 'realtime-voice-controller.js', 'utf8').replace(/^import .*;$/gm, '');
const {RealtimeVoiceController: Controller} = await load(source);

let adapter, stops;
for (const Real of [OpenAIWebRTCAdapter, XAIWebSocketAdapter]) {
  Real.prototype.prepareSession = () => {};
  Real.prototype.start = () => {};
  Real.prototype.connect = async function () {
    adapter = this;
    this._micStream = {getAudioTracks: () => [{stop() { stops += 1; }}]};
    this._emit('connected', {});
  };
}
globalThis.fetch = async () => ({ok: true, json: async () => globalThis.bootstrap});
globalThis.setInterval = () => 1;
globalThis.clearInterval = () => {};

async function session(provider) {
  stops = 0;
  globalThis.bootstrap = {provider, model: 'synthetic', warning_minutes: 20, max_minutes: 30, session_config: {}};
  const seen = {states: [], finals: [], fatals: []};
  const controller = new Controller({
    bootstrapUrl: '/synthetic',
    onStateChange: s => seen.states.push(s),
    onFatalError: f => seen.fatals.push(f),
    onFinalize: f => seen.finals.push(f),
  });
  await controller.startSession({provider, persona: 'p', scene: 's'});
  assert.equal(controller._state, 'active');
  assert.equal(stops, 0);
  return {controller, seen, adapter};
}

let n = 0;
for (const provider of ['openai', 'xai']) {
  // The provider closes the connection: the page stops, and so does the microphone.
  {
    const {controller, seen, adapter} = await session(provider);
    adapter._emit('closed', {reason: 'connection_closed'});
    assert.equal(controller._state, 'ended');
    assert.equal(stops, 1, `${provider}: close releases the microphone`);
    assert.equal(seen.finals.length, 1);
    assert.equal(seen.finals[0].partial_reason, 'disconnect');
    adapter._emit('closed', {reason: 'connection_closed'});    // a late second close changes nothing
    assert.equal(stops, 1);
    assert.equal(seen.finals.length, 1);
    n++;
  }
  // One provider error leaves the session up; a second ends it and releases the microphone.
  {
    const {controller, seen, adapter} = await session(provider);
    adapter._emit('recoverable_error', {reason: 'provider_error', detail: 'first'});
    assert.equal(controller._state, 'reconnecting');
    assert.equal(stops, 0, `${provider}: one error keeps the microphone`);
    adapter._emit('recoverable_error', {reason: 'provider_error', detail: 'second'});
    assert.equal(controller._state, 'ended');
    assert.equal(stops, 1, `${provider}: a second error releases the microphone`);
    assert.equal(seen.finals.length, 1);
    assert.equal(seen.finals[0].partial_reason, 'recoverable_error_no_retry');
    n++;
  }
  // The owner's Stop is as it was: synchronous, one release, one result, not partial.
  {
    const {controller, seen} = await session(provider);
    assert.equal(controller.endSession('user_ended'), undefined);
    assert.equal(controller._state, 'ended');
    assert.equal(stops, 1);
    assert.deepEqual(seen.states.slice(-2), ['ending', 'ended']);
    assert.equal(seen.finals.length, 1);
    assert.equal(seen.finals[0].partial, false);
    n++;
  }
  // A fatal error is as it was too.
  {
    const {controller, seen, adapter} = await session(provider);
    adapter._emit('fatal_error', {reason: 'synthetic', detail: 'x'});
    assert.equal(controller._state, 'ended');
    assert.equal(stops, 1);
    assert.deepEqual(seen.fatals, [{reason: 'synthetic', detail: 'x'}]);
    assert.equal(seen.finals.length, 1);
    n++;
  }
}
console.log(`${n} provider-end scenarios passed`);
