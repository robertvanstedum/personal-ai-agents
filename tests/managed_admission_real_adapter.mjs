import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const root = process.env.MANAGED_ADMISSION_REPO || new URL('../', import.meta.url).pathname;
const adapterPath = process.env.MANAGED_ADMISSION_ADAPTER || `${root}/core/realtime_voice/static/adapters/openai-webrtc-adapter.js`;
const adapterSource = fs.readFileSync(adapterPath, 'utf8');
const { OpenAIWebRTCAdapter } = await import(`data:text/javascript;base64,${Buffer.from(adapterSource).toString('base64')}`);
globalThis.RealAdapter = OpenAIWebRTCAdapter;
let controllerSource = fs.readFileSync(`${root}/core/realtime_voice/static/realtime-voice-controller.js`, 'utf8').replace(/^import .*;$/gm, '');
controllerSource = 'const OpenAIWebRTCAdapter=globalThis.RealAdapter, XAIWebSocketAdapter=globalThis.RealAdapter;\n' + controllerSource;
const { RealtimeVoiceController } = await import(`data:text/javascript;base64,${Buffer.from(controllerSource).toString('base64')}`);
const hostSource = fs.readFileSync(`${root}/minimoi_portal/guild_ui/static/js/mc_voice.js`, 'utf8')
  .replace(/^import .*;$/gm, '').replace('export function initMcVoice', 'function initMcVoice')
  .replace('await import(`${api}/mc/voice/static/realtime-voice-controller.js`)', 'await importControlled()');
const flush = () => new Promise(resolve => setImmediate(resolve));
let stopped = 0;
globalThis.document = { createElement: () => ({ pause() {} }) };
globalThis.RTCPeerConnection = class {
  addTrack() {}
  createDataChannel() { return { addEventListener() {}, close() {} }; }
  async createOffer() { return { type: 'offer', sdp: 'synthetic SDP' }; }
  async setLocalDescription() {}
  async setRemoteDescription() { throw Error('refused SDP must never be installed'); }
  close() {}
};
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: {
  mediaDevices: { getUserMedia: async () => ({
    getTracks: () => [{ stop() { stopped += 1; } }], getAudioTracks: () => [{ stop() { stopped += 1; } }],
  }) },
} });

async function rig(code) {
  const nodes = {}, instances = [], requests = [];
  const node = selector => nodes[selector] ??= {
    textContent: '', value: selector.includes('provider') ? 'openai' : '', disabled: false,
    hidden: false, checked: false, attrs: {},
    setAttribute(key, value) { this.attrs[key] = value; },
    addEventListener(key, handler) { this[key] = handler; },
  };
  globalThis.fetch = async (url, options) => {
    requests.push([url, options]);
    return { status: 409, ok: false, json: async () => ({ ok: false, code, error: 'PRIVATE RAW BODY' }) };
  };
  class ActualController extends RealtimeVoiceController {
    constructor(options) { super(options); instances.push(this); }
  }
  const sandbox = {
    AbortController, setTimeout, clearTimeout,
    $: node, announce() {}, live: { known: true, off: false }, onChange() {},
    recordMode: () => 'on', submitVoiceUtterance() { throw Error('no native calls'); },
    panelMode: () => 'open', document: { body: { dataset: { mcVoice: 'true' } }, dispatchEvent() {}, addEventListener() {} },
    CustomEvent: class {}, MutationObserver: class { observe() {} }, window: { addEventListener() {} },
    fetch: async () => ({ ok: true, json: async () => ({ ok: true, providers: [{ provider: 'openai' }], managed_realtime: { session_url: '/synthetic/session' } }) }),
    importControlled: async () => ({ RealtimeVoiceController: ActualController }),
  };
  vm.createContext(sandbox);
  vm.runInContext(hostSource, sandbox);
  sandbox.initMcVoice({ urls: { api: '/api' }, csrf_token: 'synthetic', conversation: { id: 'synthetic' } });
  await flush();
  node('[data-mc-voice-btn]').click();
  await flush(); await flush();
  return { node, instances, requests, status: () => node('[data-mc-voice-status]').textContent };
}

for (const [code, match] of [
  ['voice_session_unresolved', /earlier voice session has not been confirmed ended/],
  ['agent_action_unresolved', /earlier agent action needs verification/],
]) {
  const result = await rig(code);
  const message = result.status();
  assert.match(message, match);
  assert.match(message, /Typing still works/);
  assert(!message.includes('PRIVATE RAW BODY'));
  assert.equal(result.instances[0]._state, 'ended');
  assert.equal(result.node('[data-mc-voice-btn]').disabled, true);
  assert.deepEqual(result.requests.map(([url]) => url), ['/synthetic/session']);
  const controller = result.instances[0];
  controller._onStateChange('ended');
  controller._onInputState('speech_started');
  assert.equal(result.status(), message);
  result.node('[data-mc-voice-btn]').click(); await flush();
  assert.equal(result.instances.length, 1);
}
assert(stopped >= 2);
// Unknown server codes/raw error strings must remain generic, never trusted as UI text.
{
  const result = await rig('PRIVATE_UNREVIEWED_CODE');
  assert(!result.status().includes('PRIVATE'));
  assert.match(result.status(), /termination is unconfirmed/);
}
// Adapter itself emits no lossy generic fatal event for the marked refusal.
{
  const adapter = new OpenAIWebRTCAdapter(), events = [];
  adapter.on('fatal_error', event => events.push(event));
  const error = Object.assign(new Error('fixed admission message'), { managedAdmissionRefused: true, code: 'voice_session_unresolved' });
  await assert.rejects(() => adapter.connect({ exchangeSdp: async () => { throw error; } }), actual => actual === error);
  assert.deepEqual(events, []);
  adapter.end('synthetic cleanup');
}
console.log('4 actual adapter/controller/MC-host refusal and privacy scenarios passed; no provider/microphone/network calls.');
