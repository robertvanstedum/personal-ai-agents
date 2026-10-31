// The real shared controller (core/realtime_voice/static/realtime-voice-controller.js) for page tests, with synthetic
// adapters in place of the provider transports (the adapter takes the microphone in connect(), so connects counts every
// time it would have) and a bootstrap held until the test answers it (answer(body)) or fails it as the controller's own
// bound would abort it (fail()). Its duration watch's interval is stubbed while in use. VOICE_REVIEW_ROOT=<dir> loads the
// controller from another copy of the tree. No provider, microphone or network.
//
// realController({ adapters: 'real' }): the real controller with its real OpenAI and xAI adapters, and the browser stubbed
// under them (Gate 2 V S2): getUserMedia is held until the test grants it (state.grant()), and its stream's tracks record
// stop(); RTCPeerConnection, WebSocket, AudioContext and an audio element are inert fakes that record what is used.
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';

export async function realController({ adapters = 'synthetic' } = {}) {
  if (adapters === 'real') return realAdapters();
  const state = { connects: 0, adapters: [], bootstraps: [], answer: null, fail: null };
  class Adapter {
    constructor() { this.listeners = {}; this.ended = false; state.adapters.push(this); }
    on(name, fn) { (this.listeners[name] ||= []).push(fn); }
    emit(name, e) { for (const fn of this.listeners[name] || []) fn(e); }
    prepareSession() {}
    async connect() { state.connects += 1; }
    start() { this.emit('connected', {}); }
    end() { this.ended = true; }
    stopCapture() {} mute() {} unmute() {}
    sendContinuationInstruction() {} createResponse() {} sendFunctionOutput() {} sendFunctionResult() {}
  }
  globalThis.__realControllerOpenAI = class extends Adapter {};
  globalThis.__realControllerXAI = class extends Adapter {};
  const root = process.env.VOICE_REVIEW_ROOT ? pathToFileURL(process.env.VOICE_REVIEW_ROOT + '/') : new URL('../../', import.meta.url);
  const source = fs.readFileSync(new URL('core/realtime_voice/static/realtime-voice-controller.js', root), 'utf8').replace(/^import .*;$/gm, '');
  const shim = 'const OpenAIWebRTCAdapter=globalThis.__realControllerOpenAI,XAIWebSocketAdapter=globalThis.__realControllerXAI;\n';
  const { RealtimeVoiceController } = await import('data:text/javascript;base64,' + Buffer.from(shim + source).toString('base64'));
  const saved = { fetch: globalThis.fetch, setInterval: globalThis.setInterval, clearInterval: globalThis.clearInterval };
  globalThis.fetch = (url) => new Promise((resolve, reject) => {
    state.bootstraps.push(url);
    state.answer = (body) => resolve({ ok: true, status: 200, json: async () => body });
    state.fail = () => reject(new DOMException('The operation was aborted.', 'AbortError'));
  });
  globalThis.setInterval = () => 0; globalThis.clearInterval = () => {};
  state.restore = () => Object.assign(globalThis, saved);
  return { RealtimeVoiceController, state };
}

async function realAdapters() {
  const state = { bootstraps: [], answer: null, fail: null, grant: null, asked: 0, tracks: [], peers: [], channels: 0,
    sockets: [], contexts: 0, sdp: 0 };
  const root = process.env.VOICE_REVIEW_ROOT ? pathToFileURL(process.env.VOICE_REVIEW_ROOT + '/') : new URL('../../', import.meta.url);
  const saved = {};
  for (const k of ['fetch', 'setInterval', 'clearInterval', 'RTCPeerConnection', 'WebSocket', 'AudioContext', 'document'])
    saved[k] = Object.getOwnPropertyDescriptor(globalThis, k);
  const savedNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  const track = () => { const t = { kind: 'audio', enabled: true, stopped: false, stop() { t.stopped = true; } }; state.tracks.push(t); return t; };
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: { getUserMedia: () => new Promise((resolve) => {
    state.asked += 1;
    state.grant = () => { const tracks = [track()]; resolve({ getTracks: () => tracks, getAudioTracks: () => tracks }); };
  }) } } });
  globalThis.RTCPeerConnection = class {
    constructor() { this.added = []; this.closed = false; state.peers.push(this); }
    addTrack(t) { if (this.closed) throw new Error('InvalidStateError: the connection is closed'); this.added.push(t); }
    createDataChannel() { state.channels += 1; return { addEventListener() {}, close() {}, send() {} }; }
    async createOffer() { return { type: 'offer', sdp: 'v=0' }; }
    async setLocalDescription() {} async setRemoteDescription() {}
    close() { this.closed = true; }
  };
  globalThis.WebSocket = class {
    constructor() { this.readyState = 0; state.sockets.push(this); }
    addEventListener() {} send() {} close() { this.readyState = 3; }
  };
  globalThis.WebSocket.OPEN = 1;
  globalThis.AudioContext = class {
    constructor() { state.contexts += 1; this.state = 'running'; }
    async resume() {} close() {}
    createMediaStreamSource() { return { connect() {} }; }
    createScriptProcessor() { return { connect() {}, disconnect() {} }; }
  };
  globalThis.document = { createElement: () => ({ autoplay: false, pause() {}, addEventListener() {} }) };
  globalThis.fetch = (url) => new Promise((resolve, reject) => {
    if (String(url).startsWith('https://')) { state.sdp += 1; return; }        // the provider's SDP exchange: never answered
    state.bootstraps.push(url);
    state.answer = (body) => resolve({ ok: true, status: 200, json: async () => body });
    state.fail = () => reject(new DOMException('The operation was aborted.', 'AbortError'));
  });
  globalThis.setInterval = () => 0; globalThis.clearInterval = () => {};
  const { RealtimeVoiceController } = await import(new URL('core/realtime_voice/static/realtime-voice-controller.js', root));
  state.restore = () => {
    for (const [k, d] of Object.entries(saved)) { if (d) Object.defineProperty(globalThis, k, d); else delete globalThis[k]; }
    if (savedNavigator) Object.defineProperty(globalThis, 'navigator', savedNavigator); else delete globalThis.navigator;
  };
  return { RealtimeVoiceController, state };
}
