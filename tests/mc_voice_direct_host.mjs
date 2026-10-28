// The Guild composer and floating panel on the direct voice path (UI_CONTRACT_chunk_B.md §1, §8, §9): mc_voice.js picks the
// direct path only when the capabilities answer offers it, hands the microphone over, ends voice when the panel closes, and
// clears its status line. The real mc_voice.js and mc_voice_direct.js, with a fake controller, fetch, DOM nodes and timers.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const DIRECT_URL = new URL('../minimoi_portal/guild_ui/static/js/mc_voice_direct.js', import.meta.url);
const realDirect = await import(DIRECT_URL);
const source = fs.readFileSync(new URL('../minimoi_portal/guild_ui/static/js/mc_voice.js', import.meta.url), 'utf8')
  .replace(/^import .*;$/gm, '').replace('export function initMcVoice', 'function initMcVoice')
  .replaceAll('import(`${api}/mc/voice/static/realtime-voice-controller.js`)', 'importControlled()')
  .replace("import('./mc_voice_direct.js')", 'importDirect()');
assert.ok(!source.includes("import('./mc_voice_direct.js')"));
const flush = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)); };

const SID = '7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f';
const BASE = '/api/mc/voice/direct/api/realtime-voice';
const DIRECT = { path: 'direct', providers: [{ provider: 'openai', label: 'OpenAI' }, { provider: 'xai', label: 'Grok' }], default_provider: 'openai',
  bootstrap_url: `${BASE}/bootstrap`, consult_url: `${BASE}/consult`, end_url: `${BASE}/session/{session_id}/end`,
  transcript_url: `${BASE}/session/{session_id}/transcript`, summary_url: `${BASE}/session/{session_id}/summary`,
  summary_decision_url: `${BASE}/session/{session_id}/summary/decision`, heartbeat_seconds: 20, persona: 'master_craftsman' };

async function rig({ capabilities = { ok: true, mode: 'agent_conversation', providers: [], managed_realtime: null, direct_voice: DIRECT }, summary = null } = {}) {
  const nodes = {}, instances = [], docEvents = {}, windowEvents = {}, dispatched = [], calls = [], timers = [];
  let directLoads = 0;
  const node = (s) => nodes[s] ??= { selector: s, textContent: '', value: s.includes('provider') ? 'openai' : '', disabled: false, hidden: s !== '[data-mc-voice-btn]' ? false : true,
    open: false, checked: false, attrs: {}, listeners: {}, parentElement: { hidden: false },
    options: s.includes('provider') ? [{ value: 'openai', textContent: 'OpenAI', disabled: false, dataset: {} }, { value: 'xai', textContent: 'Grok (xAI) · not available yet', disabled: true, dataset: {} }] : undefined,
    setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; },
    addEventListener(k, f) { (this.listeners[k] ||= []).push(f); this[k] = () => this.listeners[k].forEach((fn) => fn({})); } };
  nodes['[data-mc-voice-live]'] = { ...node('[data-mc-voice-live]'), hidden: true };
  const document = { body: { dataset: { mcVoice: 'true' } }, dispatchEvent(e) { dispatched.push(e); (docEvents[e.type] || []).forEach((fn) => fn(e)); },
    addEventListener(k, f) { (docEvents[k] ||= []).push(f); } };
  class CustomEvent { constructor(type, init) { this.type = type; this.detail = init?.detail; } }
  class Controller {
    constructor(options) { this.options = options; this._state = 'idle'; this._sessionId = null; this.ended = []; this.instructions = [];
      this._adapter = { sendContinuationInstruction: (t) => this.instructions.push(t) }; instances.push(this); }
    async startSession(args) {
      this.args = args; this._sessionId = SID;
      this.options.onSessionInfo?.({ provider: args.provider, voice_session_id: SID, voice_notices: ['openai voice is unset, using marin'] });
      this._state = 'active'; this.options.onStateChange('active');
    }
    endSession(reason) {
      if (this._state === 'ended') return;
      this.ended.push(reason); this._state = 'ended'; this.options.onStateChange('ended');
      this.options.onFinalize({ turns: [], partial_reason: reason, session_id: SID });
    }
  }
  const fetch = async (url, opts = {}) => {
    const body = opts.body ? JSON.parse(opts.body) : null;
    calls.push({ url, opts, body });
    let out = { status: 200, body: { ok: true } };
    if (url.endsWith('/mc/voice/capabilities')) out = { status: 200, body: capabilities };
    else if (url.includes('/notes?')) out = { status: 200, body: { notes: [{ id: 9, text: 'revealed', voice: { kind: 'owner_turn', session_id: SID, text: 'revealed' } }], voice_sessions: [] } };
    else if (url.endsWith('/summary') && summary) out = summary;
    return { ok: out.status === 200, status: out.status, headers: { get: () => 'application/json' }, json: async () => out.body };
  };
  const merged = [];
  const win = { addEventListener(k, f) { windowEvents[k] = f; }, setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length; }, clearTimeout(id) { if (timers[id - 1]) timers[id - 1].fn = () => {}; },
    setInterval() { return 0; }, clearInterval() {}, localStorage: { getItem: () => null, setItem() {} } };
  const sandbox = { $: node, announce() {}, live: { known: true, off: false }, onChange() {}, recordMode: () => 'on_record', panelMode: () => 'open',
    mergeNotes: (notes) => merged.push(...notes), paintVoiceButton: undefined, document, AbortController, setTimeout, clearTimeout, CustomEvent,
    MutationObserver: class { observe() {} }, window: win, fetch, console,
    importControlled: async () => ({ RealtimeVoiceController: Controller }), importDirect: async () => { directLoads++; return realDirect; } };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  const page = { urls: { api: '/api' }, csrf_token: 'csrf-synthetic', conversation: { id: 'c-1' }, floor: { notes: { recent: [], voice_sessions: [] } } };
  sandbox.initMcVoice(page);
  await flush();
  return { nodes, node, instances, calls, dispatched, docEvents, windowEvents, timers, merged, sandbox, page, get directLoads() { return directLoads; },
    async click(sel = '[data-mc-voice-btn]') { node(sel).click(); await flush(); }, status: () => node('[data-mc-voice-status]').textContent,
    posts: (end) => calls.filter((c) => c.opts.method === 'POST' && c.url.endsWith(end)) };
}
let n = 0;

// §1: direct_voice present -> the direct path: the button shows, the provider choice offers direct.providers, no live transcript box.
{
  const r = await rig();
  assert.equal(r.node('[data-mc-voice-btn]').hidden, false);
  assert.equal(r.directLoads, 1);                                                         // loaded to paint the thread
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
// §1: no direct_voice: today's behaviour, unchanged: no managed path either -> "Voice unavailable", nothing sent, no direct file.
{
  const r = await rig({ capabilities: { ok: true, providers: [{ provider: 'openai' }], managed_realtime: null, direct_voice: null } });
  assert.equal(r.directLoads, 0);
  await r.click();
  assert.match(r.status(), /^Voice unavailable\. Live voice is not on for this Master Craftsman/);
  assert.equal(r.instances.length, 0); assert.equal(r.directLoads, 0);
  assert.equal(r.calls.filter((x) => x.opts.method === 'POST').length, 0);
  assert.equal(r.node('[data-mc-voice-transcript-choice]').parentElement.hidden, false);
  n++;
}
// managed_realtime only: the managed path, unchanged (the direct file is never loaded).
{
  const r = await rig({ capabilities: { ok: true, providers: [{ provider: 'openai' }], managed_realtime: { session_url: '/fake', provider: 'openai' }, direct_voice: null } });
  await r.click();
  assert.equal(r.directLoads, 0);
  assert.ok(r.instances[0].options.managedBootstrap);
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
  r.instances[0].options.onUserTurn('Run the nightly job.');
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
// The chained relay is never referenced by the composer or the direct path.
{
  const composer = fs.readFileSync(new URL('../minimoi_portal/guild_ui/static/js/mc_voice.js', import.meta.url), 'utf8');
  const direct = fs.readFileSync(DIRECT_URL, 'utf8');
  for (const chained of ['realtime-confer-controller', 'ConferVoiceController', '/mc/voice/bootstrap', '/mc/voice/speech']) {
    assert(!composer.includes(chained) && !direct.includes(chained), chained);
  }
  n++;
}
console.log(`${n} direct-voice host scenarios passed (path choice, provider choice, mic handover, panel close, pagehide, sum up, status line); no network or microphone.`);
