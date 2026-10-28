// The Guild composer and floating panel on the direct voice path (UI_CONTRACT_chunk_B.md §1, §8, §9): mc_voice.js picks the
// direct path only when the capabilities answer offers it, hands the microphone over, ends voice when the panel closes, and
// clears its status line. The real mc_voice.js, mc_voice_direct.js (the Guild's thread) and the shared session module
// (core/realtime_voice/static/direct-voice-session.js, Spec 165 §4.3), with a fake controller, fetch, DOM nodes and timers.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import { fakeDocument } from './js_helpers/fake_dom.mjs';
const DIRECT_URL = new URL('../minimoi_portal/guild_ui/static/js/mc_voice_direct.js', import.meta.url);
const realDirect = await import(DIRECT_URL);
const SESSION_URL = new URL('../core/realtime_voice/static/direct-voice-session.js', import.meta.url);
const realSession = await import(SESSION_URL);
const source = fs.readFileSync(new URL('../minimoi_portal/guild_ui/static/js/mc_voice.js', import.meta.url), 'utf8')
  .replace(/^import .*;$/gm, '').replace('export function initMcVoice', 'function initMcVoice')
  .replaceAll('import(`${api}/mc/voice/static/realtime-voice-controller.js`)', 'importControlled()')
  .replace('import(`${api}/mc/voice/static/realtime-confer-controller.js`)', 'importConfer()')
  .replace("import('./mc_voice_direct.js')", 'importDirect()')
  .replace('import(`${api}/mc/voice/static/direct-voice-session.js`)', 'importSession()');
assert.ok(!source.includes("import('./mc_voice_direct.js')") && !source.includes('realtime-confer-controller.js') && !source.includes('direct-voice-session.js'));
const flush = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)); };

const SID = '7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f';
const BASE = '/api/mc/voice/direct/api/realtime-voice';
const DIRECT = { path: 'direct', providers: [{ provider: 'openai', label: 'OpenAI' }, { provider: 'xai', label: 'Grok' }], default_provider: 'openai',
  bootstrap_url: `${BASE}/bootstrap`, consult_url: `${BASE}/consult`, end_url: `${BASE}/session/{session_id}/end`,
  transcript_url: `${BASE}/session/{session_id}/transcript`, summary_url: `${BASE}/session/{session_id}/summary`,
  summary_decision_url: `${BASE}/session/{session_id}/summary/decision`, heartbeat_seconds: 20, persona: 'master_craftsman',
  person: { name: 'Master Craftsman', short: 'MC' } };

async function rig({ capabilities = { ok: true, mode: 'agent_conversation', providers: [], managed_realtime: null, direct_voice: DIRECT }, summary = null,
  notesBody = null } = {}) {
  const nodes = {}, instances = [], docEvents = {}, windowEvents = {}, dispatched = [], calls = [], timers = [];
  const confers = [], submitted = [];
  let directLoads = 0, sessionLoads = 0, conferLoads = 0;
  const node = (s) => nodes[s] ??= { selector: s, textContent: '', value: s.includes('provider') ? 'openai' : '', disabled: false, hidden: s !== '[data-mc-voice-btn]' ? false : true,
    open: false, checked: false, attrs: {}, listeners: {}, parentElement: { hidden: false },
    options: s.includes('provider') ? [{ value: 'openai', textContent: 'OpenAI', disabled: false, dataset: {} }, { value: 'xai', textContent: 'Grok (xAI) · not available yet', disabled: true, dataset: {} }] : undefined,
    setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; },
    addEventListener(k, f) { (this.listeners[k] ||= []).push(f); this[k] = () => this.listeners[k].forEach((fn) => fn({})); } };
  nodes['[data-mc-voice-live]'] = { ...node('[data-mc-voice-live]'), hidden: true };
  const fdoc = fakeDocument();
  nodes['[data-mc-thread]'] = fdoc.createElement('ol');                                      // the thread: a small fake DOM
  const document = { body: { dataset: { mcVoice: 'true' } }, dispatchEvent(e) { dispatched.push(e); (docEvents[e.type] || []).forEach((fn) => fn(e)); },
    addEventListener(k, f) { (docEvents[k] ||= []).push(f); }, createElement: fdoc.createElement, hidden: false };
  class CustomEvent { constructor(type, init) { this.type = type; this.detail = init?.detail; } }
  class Controller {
    constructor(options) { this.options = options; this._state = 'idle'; this.sid = null; this.ended = []; this.instructions = []; instances.push(this); }
    sessionId() { return this.sid; }                                                      // the public surface only (Spec 165 §4.3)
    state() { return this._state; }
    speak(t) { this.instructions.push(t); return true; }
    async startSession(args) {
      this.args = args; this.sid = SID;
      this.options.onSessionInfo?.({ provider: args.provider, voice_session_id: SID, voice_notices: ['openai voice is unset, using marin'] });
      this._state = 'active'; this.options.onStateChange('active');
    }
    utter(text) {                                          // a completed owner turn, as the controller reports it: the transcript, then the turn
      this.turns = [...(this.turns || []), { speaker: 'user', text, completed: true }];
      this.options.onTranscriptUpdate(this.turns.map((t) => ({ ...t }))); this.options.onUserTurn(text);
    }
    endSession(reason) {
      if (this._state === 'ended') return;
      this.ended.push(reason); this._state = 'ended'; this.options.onStateChange('ended');
      this.options.onFinalize({ turns: [], partial_reason: reason, session_id: SID });
    }
  }
  // Today's chained relay (production MC voice until the direct path is on there): the shared Confer controller, which mc_voice.js
  // extends with its own submit step. Only the parts that step and the start use.
  class ConferVoiceController {
    constructor(options) { this.options = options; this._active = false; this.userTurns = []; this.played = []; this.stopped = []; confers.push(this); }
    async start(provider) { this.provider = provider; this._active = true; this.options.onStateChange('listening'); }
    stop(reason) { this.stopped.push(reason); this._active = false; }
    _onUserTurn(text) { this.userTurns.push(text); }
    _onStateChange(state, info) { this.options.onStateChange(state, info); }
    _onError(e) { this.options.onError(e); }
    async _play(url) { this.played.push(url); }
  }
  const fetch = async (url, opts = {}) => {
    const body = opts.body ? JSON.parse(opts.body) : null;
    calls.push({ url, opts, body });
    let out = { status: 200, body: { ok: true } };
    if (url.endsWith('/mc/voice/capabilities')) out = { status: 200, body: capabilities };
    else if (url.includes('/notes?')) out = { status: 200, body: notesBody ? notesBody() : { notes: [{ id: 9, text: 'revealed', voice: { kind: 'owner_turn', session_id: SID, text: 'revealed' } }], voice_sessions: [] } };
    else if (url.endsWith('/summary') && summary) out = summary;
    return { ok: out.status === 200, status: out.status, headers: { get: () => 'application/json' }, json: async () => out.body };
  };
  const merged = [];
  const win = { addEventListener(k, f) { windowEvents[k] = f; }, setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length; }, clearTimeout(id) { if (timers[id - 1]) timers[id - 1].fn = () => {}; },
    setInterval() { return 0; }, clearInterval() {}, localStorage: { getItem: () => null, setItem() {} } };
  const sandbox = { $: node, announce() {}, live: { known: true, off: false }, onChange() {}, recordMode: () => 'on_record', panelMode: () => 'open',
    mergeNotes: (notes) => merged.push(...notes), paintVoiceButton: undefined, document, AbortController, setTimeout, clearTimeout, CustomEvent,
    submitVoiceUtterance: async (text, requestId) => { submitted.push([text, requestId]); return { ok: true, note_request_id: 'mc-note-1' }; },
    importConfer: async () => { conferLoads++; return { ConferVoiceController }; },
    MutationObserver: class { observe() {} }, window: win, fetch, console,
    importControlled: async () => ({ RealtimeVoiceController: Controller }), importDirect: async () => { directLoads++; return realDirect; },
    importSession: async () => { sessionLoads++; return realSession; } };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  const page = { urls: { api: '/api' }, csrf_token: 'csrf-synthetic', conversation: { id: 'c-1' }, floor: { notes: { recent: [], voice_sessions: [] } } };
  sandbox.initMcVoice(page);
  await flush();
  return { nodes, node, instances, confers, submitted, calls, dispatched, docEvents, windowEvents, timers, merged, sandbox, page, document,
    get directLoads() { return directLoads; }, get sessionLoads() { return sessionLoads; }, get conferLoads() { return conferLoads; },
    async click(sel = '[data-mc-voice-btn]') { node(sel).click(); await flush(); }, status: () => node('[data-mc-voice-status]').textContent,
    posts: (end) => calls.filter((c) => c.opts.method === 'POST' && c.url.endsWith(end)) };
}
let n = 0;

// §1: direct_voice present -> the direct path: the button shows, the provider choice offers direct.providers, no live transcript box.
{
  const r = await rig();
  assert.equal(r.node('[data-mc-voice-btn]').hidden, false);
  assert.equal(r.directLoads, 1); assert.equal(r.sessionLoads, 1);                       // loaded to paint the thread
  const select = r.node('[data-mc-voice-provider]');
  assert.deepEqual(select.options.map((o) => [o.value, o.disabled, o.textContent]), [['openai', false, 'OpenAI'], ['xai', false, 'Grok (xAI)']]);
  assert.equal(select.value, 'openai');                                                   // direct.default_provider
  assert.equal(r.node('[data-mc-managed-settings]').hidden, false);
  assert.equal(r.node('[data-mc-voice-transcript-choice]').parentElement.hidden, true);  // nothing that is said shows while live
  select.value = 'xai';
  await r.click();
  const c = r.instances[0];
  assert.deepEqual(c.args, { provider: 'xai', persona: 'master_craftsman', scene: '', conversation_id: 'c-1' });
  assert.equal(c.options.bootstrapUrl, DIRECT.bootstrap_url);
  assert.equal(JSON.stringify(c.options.requestHeaders()), JSON.stringify({ 'Content-Type': 'application/json', 'X-CSRF-Token': 'csrf-synthetic', 'X-Record-Mode': 'on_record' }));   // another realm: compared as JSON
  assert.equal(r.dispatched[0].type, 'guild:mic-claim'); assert.equal(r.dispatched[0].detail.who, 'voice');   // §8: Dictate yields
  assert.equal(r.node('[data-mc-voice-live]').hidden, false);
  assert.equal(r.node('[data-mc-voice-notice]').textContent, '');                        // the notice is for openai; xai was chosen
  assert.equal(r.node('[data-mc-voice-btn]').attrs['aria-pressed'], 'true');
  // End (the live control): End on the server, the reveal merged into the thread, the line says where it went.
  await r.click('[data-mc-voice-end]');
  await flush();
  assert.deepEqual(c.ended, ['user_ended']);
  assert.equal(r.posts('/end').length, 1);
  assert.equal(r.posts('/end')[0].url, `${BASE}/session/${SID}/end`);
  assert.deepEqual(r.merged.map((m) => m.id), [9]);
  assert.equal(r.node('[data-mc-voice-live]').hidden, true);
  assert.equal(r.status(), 'Voice ended. What was said is in the conversation.');
  assert.equal(r.node('[data-mc-voice-btn]').attrs['aria-pressed'], 'false');
  // §9 (#420): the ended line clears after about five seconds.
  const clear = r.timers.filter((t) => t.ms === 5000).at(-1);
  assert.ok(clear); clear.fn();
  assert.equal(r.status(), '');
  // Every POST carried the headers (§2).
  for (const p of r.calls.filter((x) => x.opts.method === 'POST')) assert.equal(p.opts.headers['X-CSRF-Token'], 'csrf-synthetic');
  n++;
}
// §1: no direct_voice and no managed_realtime (production today: MINIMOI_MC_VOICE=1, the direct path held off, managed off):
// today's chained relay, exactly as on main: the shared Confer controller for transcription, each final transcript through the
// page's kept-note path, MC's kept reply read aloud from the server's record. No direct file, no direct or managed controller.
{
  const r = await rig({ capabilities: { ok: true, mode: 'agent_conversation', default_provider: 'openai', providers: [{ provider: 'openai', label: 'OpenAI' }],
    managed_realtime: null, direct_voice: null } });
  assert.equal(r.node('[data-mc-voice-btn]').hidden, false);
  assert.equal(r.directLoads, 0);
  r.node('[data-mc-voice-btn]').click();
  assert.equal(r.status(), "Connecting… Your speech is sent to OpenAI to be turned into text, and Master Craftsman's replies are read aloud by the same service.");
  await flush();
  assert.equal(r.conferLoads, 1); assert.equal(r.confers.length, 1);
  const c = r.confers[0];
  assert.equal(c.provider, 'openai');                                                     // main starts the chained relay on OpenAI
  assert.equal(c.options.capabilitiesUrl, '/api/mc/voice/capabilities');
  assert.equal(c.options.bootstrapUrl, '/api/mc/voice/bootstrap');
  assert.equal(c.options.turnUrl, null);
  assert.equal(c.options.conversationId, 'c-1');
  assert.equal(JSON.stringify(c.options.requestHeaders()), JSON.stringify({ 'Content-Type': 'application/json', 'X-CSRF-Token': 'csrf-synthetic', 'X-Record-Mode': 'on_record' }));
  assert.equal(r.status(), 'Listening… say your message; I will send it when you pause.');
  assert.equal(r.node('[data-mc-voice-btn]').attrs['aria-pressed'], 'true');
  c.options.onProvisionalTranscript('What is on');
  assert.equal(r.status(), 'Hearing: What is on');
  await c._submitTurn('What is on the board?', 'req-1');                                  // a final transcript: the kept-note path
  assert.deepEqual(r.submitted, [['What is on the board?', 'req-1']]);
  assert.deepEqual(c.userTurns, ['What is on the board?']);
  assert.deepEqual(c.played, ['/api/mc/voice/speech/mc-note-1?c=c-1']);                   // the kept reply, read from the record
  assert.equal(r.instances.length, 0); assert.equal(r.directLoads, 0); assert.equal(r.sessionLoads, 0);
  assert.equal(r.calls.filter((x) => x.opts.method === 'POST').length, 0);               // the page itself posts nothing here
  assert.equal(r.node('[data-mc-voice-transcript-choice]').parentElement.hidden, false);
  await r.click();                                                                        // the button again: stopped
  assert.deepEqual(c.stopped, ['user_ended']);
  assert.equal(r.status(), 'Voice ended. Agent work already accepted may still finish.');
  await c._submitTurn('late words', 'req-2');                                             // a queued utterance of a stopped attempt
  assert.equal(r.submitted.length, 1);
  // A refused start says so, as on main; typing still works.
  const r2 = await rig({ capabilities: { ok: true, providers: [{ provider: 'openai', label: 'OpenAI' }], managed_realtime: null, direct_voice: null } });
  r2.sandbox.importConfer = async () => { throw new Error('no key'); };
  await r2.click();
  assert.equal(r2.status(), 'Voice could not start: no key. Typing still works.');
  assert.equal(r2.node('[data-mc-voice-btn]').attrs['aria-pressed'], 'false');
  n++;
}
// managed_realtime only: the managed path, unchanged (the direct file and the chained relay are never loaded).
{
  const r = await rig({ capabilities: { ok: true, providers: [{ provider: 'openai' }], managed_realtime: { session_url: '/fake', provider: 'openai' }, direct_voice: null } });
  await r.click();
  assert.equal(r.directLoads, 0); assert.equal(r.sessionLoads, 0); assert.equal(r.conferLoads, 0);
  assert.ok(r.instances[0].options.managedBootstrap);
  n++;
}
// Once the direct path is offered, a failure never falls back to the managed path or the chained relay: "Voice unavailable:
// <reason>", even when managed_realtime is offered too.
{
  const both = { ok: true, providers: [{ provider: 'openai' }], managed_realtime: { session_url: '/fake', provider: 'openai' }, direct_voice: DIRECT };
  const r = await rig({ capabilities: both });
  r.sandbox.importSession = async () => { throw new Error('the file did not load'); };
  r.sandbox.importControlled = async () => { throw new Error('the controller did not load'); };
  await r.click();
  assert.equal(r.status(), 'Voice unavailable: the voice page could not load. Typing still works.');
  assert.equal(r.conferLoads, 0); assert.equal(r.confers.length, 0); assert.equal(r.instances.length, 0);
  // The bootstrap refused: the refusal's words, and nothing else is tried.
  const r2 = await rig({ capabilities: both });
  const original = r2.sandbox.importControlled;
  r2.sandbox.importControlled = async () => {
    const { RealtimeVoiceController: Base } = await original();
    return { RealtimeVoiceController: class extends Base {
      async startSession(args) { this.args = args; this.options.onFatalError({ reason: 'bootstrap_rejected', status: 503, detail: 'unavailable' }); } } };
  };
  await r2.click();
  await flush();
  assert.equal(r2.status(), 'Voice unavailable here right now.');
  assert.equal(r2.conferLoads, 0); assert.equal(r2.confers.length, 0);
  assert.ok(r2.instances.every((c) => !c.options.managedBootstrap));
  assert.equal(r2.node('[data-mc-voice-btn]').attrs['aria-pressed'], 'false');
  n++;
}
// UI review #5 (#420 on every MC voice path): the managed path's own ended line clears after about five seconds too, or at
// the next send; an error there stays. An intended change to today's managed path (B_direct_path.md).
{
  const managedCaps = { ok: true, providers: [{ provider: 'openai' }], managed_realtime: { session_url: '/fake', provider: 'openai' }, direct_voice: null };
  const r = await rig({ capabilities: managedCaps });
  await r.click();
  r.instances[0].endSession('user_ended');                                               // the provider ended it
  await flush();
  assert.equal(r.status(), 'Voice ended. Agent work already accepted may still finish.');
  const clear = r.timers.filter((t) => t.ms === 5000).at(-1);
  assert.ok(clear, 'the managed ended line is transient'); clear.fn();
  assert.equal(r.status(), '');
  const r2 = await rig({ capabilities: managedCaps });
  await r2.click();
  r2.instances[0].endSession('user_ended');
  await flush();
  r2.node('[data-mc-composer]').submit();                                                // the next send, before five seconds
  assert.equal(r2.status(), '');
  const r3 = await rig({ capabilities: managedCaps });
  await r3.click();
  r3.instances[0].options.onFatalError({ reason: 'provider_failure' });
  r3.instances[0].endSession('fatal_error');
  await flush();
  for (const t of r3.timers.filter((x) => x.ms === 5000)) t.fn();
  assert.match(r3.status(), /^Voice stopped\. Any agent action already accepted may still finish/);   // an error stays
  n++;
}
// UI review #7: the thread's voice lines are repainted from the page's own notes reads and when the tab comes back into view,
// not only at load and after this tab's End.
{
  const OTHER = '0f1e2d3c-4b5a-4968-8776-655443322110';
  let sessions = [];
  const r = await rig({ notesBody: () => ({ notes: [], voice_sessions: sessions }) });
  const thread = r.node('[data-mc-thread]');
  const line = () => thread.querySelector(`[data-voice-line="${OTHER}"]`)?.textContent ?? null;
  const read = (conversationId, voice_sessions, notes = []) => r.docEvents['guild:notes-read'].forEach((fn) => fn({ detail: { conversationId, notes, voice_sessions } }));
  read('c-1', [{ session_id: OTHER, state: 'live', started_at: '2026-10-28T10:00:00.000Z', line: null }]);
  assert.equal(line(), realDirect.LIVE_ELSEWHERE);                                        // live on another device
  read('c-other', [{ session_id: OTHER, state: 'recovering', started_at: '2026-10-28T10:00:00.000Z', line: realDirect.RECOVERING }]);
  assert.equal(line(), realDirect.LIVE_ELSEWHERE);                                        // another conversation's read: ignored
  read('c-1', [{ session_id: OTHER, state: 'ended', started_at: '2026-10-28T10:00:00.000Z', line: null, summary: 'none' }],
    [{ id: 21, voice: { kind: 'owner_turn', session_id: OTHER, text: 'said elsewhere' } }, { id: 22, text: 'typed' }]);
  assert.equal(line(), null);                                                             // ended there: the line goes…
  assert.deepEqual(r.merged.map((m) => m.id), [21]);                                       // …and its revealed records come in
  sessions = [{ session_id: OTHER, state: 'recovering', started_at: '2026-10-28T10:00:00.000Z', line: realDirect.RECOVERING }];
  const reads = r.calls.filter((c) => c.url.includes('/notes?')).length;
  r.document.hidden = true; r.docEvents.visibilitychange.forEach((fn) => fn());
  assert.equal(r.calls.filter((c) => c.url.includes('/notes?')).length, reads);           // hidden: nothing read
  r.document.hidden = false; r.docEvents.visibilitychange.forEach((fn) => fn());
  await flush();
  assert.equal(r.calls.filter((c) => c.url.includes('/notes?')).length, reads + 1);
  assert.equal(line(), realDirect.RECOVERING);
  n++;
}
// §8: Dictate, or the floating panel closing (companion-closed), takes the microphone: voice ends, and End is sent.
for (const who of ['dictation', 'companion-closed']) {
  const r = await rig();
  await r.click();
  r.docEvents['guild:mic-claim'].forEach((fn) => fn({ detail: { who } }));
  await flush();
  assert.deepEqual(r.instances[0].ended, ['user_ended'], who);
  assert.equal(r.posts('/end').length, 1, who);
  assert.equal(r.node('[data-mc-voice-btn]').attrs['aria-pressed'], 'false');
  r.docEvents['guild:mic-claim'].forEach((fn) => fn({ detail: { who: 'voice' } }));     // its own claim never ends it
  n++;
}
// Leaving the page ends voice with keepalive.
{
  const r = await rig();
  await r.click();
  r.windowEvents.pagehide();
  await flush();
  assert.equal(r.posts('/end')[0].opts.keepalive, true);
  n++;
}
// UI review #1: End pressed, its End still unanswered, then the page goes away: End is sent again with keepalive.
{
  const r = await rig();
  await r.click();
  const original = r.sandbox.fetch;
  r.sandbox.fetch = async (url, opts) => {
    if (!(url.endsWith('/end') && !opts.keepalive)) return original(url, opts);
    r.calls.push({ url, opts, body: JSON.parse(opts.body) });
    return new Promise(() => {});                                                          // never answered
  };
  await r.click('[data-mc-voice-end]');
  await flush();
  assert.equal(r.posts('/end').length, 1);
  assert.notEqual(r.posts('/end')[0].opts.keepalive, true);
  r.windowEvents.pagehide();
  assert.equal(r.posts('/end').length, 2);
  assert.equal(r.posts('/end')[1].opts.keepalive, true);
  n++;
}
// UI review #3: Check with MC is disabled while its consult is in flight; a double tap sends one consult.
{
  const r = await rig();
  await r.click();
  let release;
  const original = r.sandbox.fetch;
  r.sandbox.fetch = async (url, opts) => {
    if (!url.endsWith('/consult')) return original(url, opts);
    r.calls.push({ url, opts, body: JSON.parse(opts.body) });
    await new Promise((res) => { release = res; });
    return { ok: true, status: 200, json: async () => ({ ok: true, spoken: 'Done.' }) };
  };
  r.instances[0].utter('Run the nightly job.');
  await r.click('[data-mc-voice-check]'); await r.click('[data-mc-voice-check]');
  assert.equal(r.posts('/consult').length, 1);
  assert.equal(r.node('[data-mc-voice-check]').disabled, true);
  release(); await flush();
  assert.equal(r.node('[data-mc-voice-check]').disabled, false);
  await r.click('[data-mc-voice-check]');
  assert.equal(r.posts('/consult').length, 1);
  assert.equal(r.status(), 'Already checked; say it again to re-ask.');
  n++;
}
// §7: while "Shall I sum up?" is offered, End asks for the summary (as Sum up does); an error line stays until the next send.
{
  const r = await rig({ summary: { status: 403, body: { error: 'csrf', message: 'This page is out of date. Reload it.' } } });
  // Patch the transcript answer to propose the end.
  const original = r.sandbox.fetch;
  r.sandbox.fetch = async (url, opts) => (url.endsWith('/transcript') && JSON.parse(opts.body).events.length
    ? { ok: true, status: 200, json: async () => ({ ok: true, kept: 1, propose_end: true, prompt: 'Shall I sum up?' }) } : original(url, opts));
  await r.click();
  r.instances[0].options.onTranscriptUpdate([{ speaker: 'user', text: "We're done.", completed: true }]);
  await flush();
  assert.equal(r.node('[data-mc-voice-propose]').hidden, false);
  assert.equal(r.posts('/end').length, 0);                                                // nothing has ended
  await r.click('[data-mc-voice-end]');
  await flush();
  assert.equal(r.calls.filter((x) => x.url.endsWith('/summary')).length, 1);             // End = Sum up while it is offered
  assert.equal(r.posts('/end').length, 1);
  assert.equal(r.status(), 'This page is out of date. Reload it.');
  for (const t of r.timers.filter((x) => x.ms === 5000)) t.fn();
  assert.equal(r.status(), 'This page is out of date. Reload it.');                     // an error stays…
  r.node('[data-mc-composer]').submit();
  assert.equal(r.status(), '');                                                          // …until the next send
  n++;
}
// §9: the next send clears an ended line before its five seconds are up.
{
  const r = await rig();
  await r.click(); await r.click();
  await flush();
  assert.equal(r.status(), 'Voice ended. What was said is in the conversation.');
  r.node('[data-mc-composer]').submit();
  assert.equal(r.status(), '');
  n++;
}
// The chained relay is never referenced by the direct path, and the composer reaches it only after the direct and managed
// paths: start() returns from either before the chained relay's first line.
{
  const composer = fs.readFileSync(new URL('../minimoi_portal/guild_ui/static/js/mc_voice.js', import.meta.url), 'utf8');
  const direct = fs.readFileSync(DIRECT_URL, 'utf8') + fs.readFileSync(SESSION_URL, 'utf8');
  for (const chained of ['realtime-confer-controller', 'ConferVoiceController', '/mc/voice/bootstrap', '/mc/voice/speech']) {
    assert(!direct.includes(chained), chained);
  }
  const body = composer.slice(composer.indexOf('async function start()'), composer.indexOf('const DIRECT_ENDED'));
  const at = (text) => { const i = body.indexOf(text); assert(i >= 0, text); return i; };
  assert(at('if (direct) {await startDirect(mine,current);return;}') < at('if (managed) {await startManaged(mine,current);return;}'));
  assert(at('if (managed) {await startManaged(mine,current);return;}') < at('realtime-confer-controller.js'));
  assert.equal(composer.split('realtime-confer-controller').length, 2);                 // one reference, inside start()
  n++;
}
console.log(`${n} direct-voice host scenarios passed (path choice, provider choice, mic handover, panel close, pagehide, sum up, status line); no network or microphone.`);
