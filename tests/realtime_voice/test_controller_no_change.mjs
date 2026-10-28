// The shared controller and adapters after Spec 165 A1, for German and Portuguese: byte-for-byte the files they ran before,
// plus additive public methods they never call (the controller's sessionId(), speak() and afterPlayback(); the adapters'
// whenPlaybackIdle(), review B6) and new adapter cache keys. Pinned two ways: each source with exactly those additions taken
// out hashes to its 93bf00c6 file; and the methods, driven on a synthetic transport, send nothing unless a host calls them,
// and nothing on the managed path. No provider, microphone or network.
import fs from 'node:fs';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';

const PATH = new URL('../../core/realtime_voice/static/realtime-voice-controller.js', import.meta.url);
const BEFORE = '682906489b0b3f9f02cd3df951da6fbaaef74c2aa49927d5905827ea3b8c7f05';     // 93bf00c6, as German loads it today
const ADDED_METHODS = `  /** The session's id (the server's voice_session_id when it issued one), or null. For a host page (Spec 165 §4.3);
   *  German and Portuguese do not call it. */
  sessionId() { return this._sessionId; }

  /** A host's one-off instruction to the voice on the direct path (a holding line, "say this briefly"): true when the
   *  session's transport took it. Never on the managed path. German and Portuguese do not call it. */
  speak(instruction) {
    if (this._managedBootstrap || !this._adapter) return false;
    try { this._adapter.sendContinuationInstruction(instruction); return true; } catch (_) { return false; }
  }

  /** Resolves once the voice's queued audio has played out (the adapter's optional whenPlaybackIdle), or after maxMs, or at
   *  once when the adapter cannot tell: a host ending after a closing line waits on it (Spec 165, review B6). German and
   *  Portuguese do not call it. */
  afterPlayback(maxMs = 15000) {
    const adapter = this._adapter;
    if (!adapter || typeof adapter.whenPlaybackIdle !== "function") return Promise.resolve();
    return Promise.resolve().then(() => adapter.whenPlaybackIdle(maxMs)).catch(() => {});
  }

`;
const KEYS = [['// Both adapters changed for the playback event: new keys so browsers fetch them (Spec 165 §4.3).\n', ''],
  ['openai-webrtc-adapter.js?v=20261028-playback1', 'openai-webrtc-adapter.js?v=20261015-managed-end1'],
  ['xai-websocket-adapter.js?v=20261028-playback1', 'xai-websocket-adapter.js?v=20260725-transcript1']];
let n = 0;

const source = fs.readFileSync(PATH, 'utf8');
{
  assert.equal(source.split(ADDED_METHODS).length, 2, 'the two methods, once, exactly as reviewed');
  let before = source.replace(ADDED_METHODS, '');
  for (const [now, then] of KEYS) { assert.equal(before.split(now).length, 2, now); before = before.replace(now, then); }
  assert.equal(crypto.createHash('sha256').update(before).digest('hex'), BEFORE);   // nothing else changed
  n++;
}
// The adapters: exactly the additions below, nothing else (each taken out, the file is its 93bf00c6 bytes).
const IDLE_WAIT = `    return new Promise((resolve) => {
      let timer = null;
      const done = () => { clearTimeout(timer); resolve(); };
      timer = setTimeout(done, maxMs);
      this._idleWaiters.push(done);
    });
  }
`;
const ADAPTERS = {
  'adapters/xai-websocket-adapter.js': ['afe83aa8aae73e4b47d6eaa920f8893932cf4bf04ee225e0918db06d5db3afbf', [
    ['    this._idleWaiters = [];\n', ''],
    [`  // Optional (Spec 165, review B6): resolves once every scheduled audio chunk has played out or been stopped, or after
  // maxMs. A host ending after a closing line waits on it; German and Portuguese never call it.
  whenPlaybackIdle(maxMs = 15000) {
    if (!this._playbackSources.size) return Promise.resolve();
${IDLE_WAIT}
  _playbackIdle() {
    if (!this._playbackSources.size) for (const done of this._idleWaiters.splice(0)) done();
  }

`, ''],
    ['source.addEventListener("ended", () => { this._playbackSources.delete(source); this._playbackIdle(); }, { once: true });',
      'source.addEventListener("ended", () => this._playbackSources.delete(source), { once: true });'],
    ['    this._playbackQueueTime = this._audioContext?.currentTime || 0;\n    this._playbackIdle();\n',
      '    this._playbackQueueTime = this._audioContext?.currentTime || 0;\n']]],
  'adapters/openai-webrtc-adapter.js': ['dae118fd252add9a80755a363d64ce6b4ef377b004d177e8dc10bba5a3c274a7', [
    ['    this._playing = false;\n    this._idleWaiters = [];\n', ''],
    [`  // Optional (Spec 165, review B6): resolves once the provider says its audio for the session has played out
  // (output_audio_buffer.stopped or .cleared, WebRTC only), or at once when none is playing, or after maxMs. That the event
  // means audible playback has drained in the browser is not verified against the provider. German and Portuguese never
  // call it.
  whenPlaybackIdle(maxMs = 15000) {
    if (!this._playing) return Promise.resolve();
${IDLE_WAIT}
`, ''],
    [`      case "output_audio_buffer.started":
        this._playing = true;
        break;
      case "output_audio_buffer.stopped":
      case "output_audio_buffer.cleared":
        this._playing = false;
        for (const done of this._idleWaiters.splice(0)) done();
        break;
`, '']]],
};
for (const [name, [hash, edits]] of Object.entries(ADAPTERS)) {
  let text = fs.readFileSync(new URL(name, PATH), 'utf8');
  for (const [now, then] of edits) { assert.equal(text.split(now).length, 2, `${name}: ${now.slice(0, 60)}`); text = text.replace(now, then); }
  assert.equal(crypto.createHash('sha256').update(text).digest('hex'), hash, name);
  n++;
}

// The methods on a synthetic transport: the German start sends what it sent; speak() reaches the transport only when called.
class Adapter {
  constructor() { this.listeners = {}; this.sent = []; }
  on(name, fn) { (this.listeners[name] ||= []).push(fn); }
  emit(name, e) { for (const fn of this.listeners[name] || []) fn(e); }
  prepareSession() {} async connect() {} start() { this.emit('connected', {}); } end() {} stopCapture() {}
  sendContinuationInstruction(text) { this.sent.push(text); }
}
const adapters = [];
globalThis.OpenAITestAdapter = class extends Adapter { constructor() { super(); adapters.push(this); } };
globalThis.XAITestAdapter = class extends Adapter { constructor() { super(); adapters.push(this); } };
const shim = 'const OpenAIWebRTCAdapter=globalThis.OpenAITestAdapter,XAIWebSocketAdapter=globalThis.XAITestAdapter;\n';
const { RealtimeVoiceController: Controller } = await import('data:text/javascript;base64,' +
  Buffer.from(shim + source.replace(/^import .*;$/gm, '')).toString('base64'));
const saved = { fetch: globalThis.fetch, setInterval: globalThis.setInterval, clearInterval: globalThis.clearInterval };
try {
  globalThis.setInterval = () => 1; globalThis.clearInterval = () => {};
  const sent = [];
  globalThis.fetch = async (url, options) => { sent.push([url, options]);
    return { ok: true, json: async () => ({ provider: 'openai', model: 'm', warning_minutes: 20, max_minutes: 30 }) }; };
  // German: no new option, no new call. The bootstrap request and the opening instruction are as before.
  const german = new Controller({ bootstrapUrl: '/api/realtime-voice/bootstrap' });
  assert.equal(german.sessionId(), null);
  assert.equal(german.speak('nothing yet'), false);                                     // no session: nothing is sent
  await german.startSession({ provider: 'openai', persona: 'frau_berger', scene: 'bakery', learner_name: 'Robert' });
  assert.deepEqual(sent[0][1].headers, { 'Content-Type': 'application/json' });
  assert.equal(sent[0][1].body, JSON.stringify({ provider: 'openai', persona: 'frau_berger', scene: 'bakery', learner_name: 'Robert' }));
  const transport = adapters.at(-1);
  assert.equal(transport.sent.length, 1);                                                // the opening instruction, as before
  assert.match(transport.sent[0], /^Begin in character with one short, natural greeting/);
  assert.match(german.sessionId(), /^sess-/);                                            // German's own local id, as before
  n++;
  // A direct host: sessionId() is the server's id; speak() reaches the transport once per call.
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ provider: 'xai', model: 'm', warning_minutes: 20, max_minutes: 30,
    voice_session_id: '0b5c9e2e-7f1a-4b8e-9d3c-2a1f0e9d8c7b' }) });
  const host = new Controller({ bootstrapUrl: '/b', openingInstruction: null });
  await host.startSession({ provider: 'xai', persona: 'master_craftsman', scene: '', conversation_id: 'c-1' });
  assert.equal(host.sessionId(), '0b5c9e2e-7f1a-4b8e-9d3c-2a1f0e9d8c7b');
  assert.deepEqual(adapters.at(-1).sent, []);                                            // no opening instruction for a host
  assert.equal(host.speak('Still checking.'), true);
  assert.deepEqual(adapters.at(-1).sent, ['Still checking.']);
  adapters.at(-1).sendContinuationInstruction = () => { throw new Error('closed'); };
  assert.equal(host.speak('again'), false);                                              // a transport that refuses: false, no throw
  n++;
  // The managed path never takes a page instruction.
  const managed = new Controller({ bootstrapUrl: '/b', managedBootstrap: { sessionUrl: '/m' } });
  managed._adapter = new Adapter();
  assert.equal(managed.speak('x'), false);
  assert.deepEqual(managed._adapter.sent, []);
  n++;
} finally { Object.assign(globalThis, saved); }
console.log(`${n} shared-controller no-change scenarios passed (German source pinned, additive methods); no provider or microphone.`);
