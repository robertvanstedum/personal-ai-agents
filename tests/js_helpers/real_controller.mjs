// The real shared controller (core/realtime_voice/static/realtime-voice-controller.js) for page tests, with synthetic
// adapters in place of the provider transports (the adapter takes the microphone in connect(), so connects counts every
// time it would have) and a bootstrap held until the test answers it (answer(body)) or fails it as the controller's own
// bound would abort it (fail()). Its duration watch's interval is stubbed while in use. VOICE_REVIEW_ROOT=<dir> loads the
// controller from another copy of the tree. No provider, microphone or network.
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';

export async function realController() {
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
