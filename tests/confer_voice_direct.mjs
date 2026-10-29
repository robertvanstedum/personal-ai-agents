// Chief of Staff's Confer on the shared direct voice (Spec 165 v0.3 §6.6; A3_BRIEF.md items 1–8): the real cos_ui.html module
// script, the real Confer voice page (domains/cos/static/confer-voice.js), the real shared session module
// (core/realtime_voice/static/direct-voice-session.js) and the real conferVoiceAvailability, with a fake direct controller,
// fake today's controllers, a fake Dictate binding, a fake fetch for her routes, a small fake DOM (tests/js_helpers/
// fake_dom.mjs) and fake timers. No provider, microphone, network, page server or browser.
//
// Covered: not admitted = today, by behaviour (one capabilities read, then today's two modes, Dictate and the handover as
// before); admitted: one Voice button, the provider choice, the live bar, nothing said shown while live, Check's words,
// xAI's Check-only words, Sum up and Keep talking, End and the reveal (capture, not acted on with why, partial, LV6's
// repeated words kept), Download transcript, the summary card, Ask for a summary, the needs-attention list, a refused
// start, Dictate and the companion handover (the microphone released first, a connecting session included), pagehide's
// one End, the portal path; and a phone-width layout model (no sideways scroll at 375 px, every control reachable).
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import { fakeDocument } from './js_helpers/fake_dom.mjs';
import { realController } from './js_helpers/real_controller.mjs';

process.env.TZ = 'UTC';
const ROOT = new URL('../', import.meta.url);
const PAGE = await import(new URL('domains/cos/static/confer-voice.js', ROOT));
const SESSION = await import(new URL('core/realtime_voice/static/direct-voice-session.js', ROOT));
const { conferVoiceAvailability } = await import(new URL('core/realtime_voice/static/confer-input-controls.js', ROOT));
// CONFER_TEMPLATE=<file> runs the same scenarios against another copy of the page. With CONFER_PRIOR=1 as well, the
// not-admitted cases expect that page to make no capabilities read at all: run against the page before this chunk
// (85282f8c), they pass, so today's behaviour is the same but for that one read; every admitted case fails there.
const TEMPLATE = fs.readFileSync(process.env.CONFER_TEMPLATE || new URL('domains/cos/templates/cos_ui.html', ROOT), 'utf8');
const CSS = fs.readFileSync(new URL('domains/cos/static/cos.css', ROOT), 'utf8');
const SCRIPT = (() => {
  let code = TEMPLATE.split('<script type="module">')[1].split('</script>')[0].replace(/^import .*;$/gm, '');
  const swaps = [['import(`../static/confer-voice.js?v=${DIRECT_KEY}`)', 'importConfer()'],
    ['import(`../static/realtime-voice/direct-voice-session.js?v=${DIRECT_KEY}`)', 'importSession()'],
    ['import(`../static/realtime-voice/realtime-voice-controller.js?v=${DIRECT_KEY}`)', 'importController()']];
  for (const [from, to] of swaps) {
    if (!process.env.CONFER_TEMPLATE) assert.equal(code.split(from).length, 2, from);
    code = code.replace(from, to);
  }
  assert.ok(!/\bimport\(/.test(code), 'an import the harness does not stand in for');
  return code;
})();
const flush = async (n = 14) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)); };

const SID = '7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f';
const P = '/api/realtime-voice/direct/api/realtime-voice';
const CAPS = { path: 'direct', providers: [{ provider: 'openai', label: 'OpenAI' }, { provider: 'xai', label: 'Grok' }],
  default_provider: 'openai', bootstrap_url: `${P}/bootstrap`, consult_url: `${P}/consult`, end_url: `${P}/session/{session_id}/end`,
  transcript_url: `${P}/session/{session_id}/transcript`, summary_url: `${P}/session/{session_id}/summary`,
  summary_decision_url: `${P}/session/{session_id}/summary/decision`, reconcile_url: `${P}/consult/{session_id}/{owner_item_id}`,
  keep_talking_url: `${P}/session/{session_id}/keep_talking`, heartbeat_seconds: 20, persona: 'chief_of_staff', locale: 'en-US',
  card_release: '0.1.0', person: { name: 'Chief of Staff', short: 'CoS' }, model_actions: { openai: true, xai: false },
  words: { no_conversation: "Voice unavailable: Confer's conversation is not available here.", invalid: 'Voice unavailable: open Confer to talk with CoS.',
    kept_in: "in Chief of Staff's voice records, which she can recall" },
  unvoiced: { xai: 'Voice unavailable: no Grok voice is chosen for Chief of Staff yet.' },
  capabilities_url: `${P}/capabilities`, sessions_url: `${P}/sessions`, session_url: `${P}/sessions/{session_id}`,
  session_transcript_url: `${P}/sessions/{session_id}/transcript`, session_dismiss_url: `${P}/sessions/{session_id}/dismiss`,
  direct_voice: true, direct_records: false };
const VIEW = { ok: true, session_id: SID, state: 'ended', started_at: '2026-10-28T10:00:00.000Z', ended_at: '2026-10-28T10:04:00.000Z',
  capture: 'complete', summary: 'none', consults: { asked: 0, pending: 0, unknown: 0 }, repaired: false, damaged: false,
  dismissed: false, reasons: [], needs_attention: false, complete_read: true, not_acted: {},
  records: [{ speaker: 'owner ASR', role: 'user', item_id: 't0', text: 'What is on my agenda?', partial: false, interrupted: false },
    { speaker: 'assistant generated; playback unverified', role: 'assistant', item_id: 't1', text: 'Two meetings.', partial: false, interrupted: false }] };

// The page, run as the browser runs it, against a fake server. `caps`: the direct capabilities answer (null: 404, 'network').
function page({ caps = CAPS, path = '/ui/confer', managed = true, routes = {}, saved = null, sessions = [], controller = null } = {}) {
  const fdoc = fakeDocument();
  const calls = [], appended = [], timers = new Map(), docEvents = {}, winEvents = {}, imports = [];
  let serial = 0, now = Date.parse('2026-10-28T10:00:00.000Z');
  const realNow = Date.now; Date.now = () => now;
  const nodes = {};
  const node = (id) => nodes[id] ??= (() => {
    const n = fdoc.createElement(id === 'voice-mode' ? 'select' : id.startsWith('btn') ? 'button' : 'div');
    n.setAttribute('id', id);
    if (id === 'voice-mode' || id === 'voice-keep-transcript') { const label = fdoc.createElement('label'); label.append(n); n.closest = () => label; }
    if (id === 'voice-mode') { n.value = 'realtime'; n.options = [{ value: 'realtime' }, { value: 'chained' }]; }
    if (id === 'voice-live' || id === 'voice-sessions') n.hidden = true;
    return n;
  })();
  const ids = ['btn-voice', 'voice-status', 'voice-provider', 'voice-transcript', 'btn-dictate', 'send-input', 'btn-send', 'voice-live', 'voice-sessions',
    ...(managed ? ['voice-mode', 'voice-keep-transcript'] : [])];
  for (const id of ids) node(id);
  const chat = fdoc.createElement('div');
  chat.scrollTop = 0; chat.scrollHeight = 100;
  const document = { getElementById: (id) => (ids.includes(id) ? node(id) : null), createElement: fdoc.createElement,
    createTextNode: fdoc.createTextNode, querySelector: () => ({ content: 'csrf-synthetic' }),
    addEventListener: (k, fn) => (docEvents[k] ||= []).push(fn), dispatch: (k, e) => (docEvents[k] || []).map((fn) => fn(e)) };
  const store = new Map(saved ? [['minimoi.cos.voice.provider', saved]] : []);
  const window = { addEventListener: (k, fn) => { winEvents[k] = fn; },
    setTimeout: (fn, ms) => { timers.set(++serial, { fn, due: now + ms }); return serial; }, clearTimeout: (id) => timers.delete(id),
    setInterval: (fn, ms) => { timers.set(++serial, { fn, due: now + ms, every: ms }); return serial; }, clearInterval: (id) => timers.delete(id),
    localStorage: { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) } };
  // Today's two modes: the managed realtime controller (static import) and the chained Confer controller.
  const today = { managed: [], chained: [], ended: [], stopped: [] };
  class RealtimeVoiceController {
    constructor(o) { this.o = o; today.managed.push(this); }
    async startSession(args) { this.args = args; this.o.onStateChange('active'); }
    async endSession(reason) { today.ended.push(reason); this.o.onStateChange('ended'); }
  }
  class ConferVoiceController {
    constructor(o) { this.o = o; today.chained.push(this); }
    async availableProviders() { return { providers: [{ provider: 'openai' }] }; }
    async start(p) { this.started = p; this.o.onStateChange('listening'); this.o.onProvisionalTranscript('half a thought'); }
    stop(reason) { today.stopped.push(reason); this.o.onStateChange('stopped'); }
  }
  // Dictate, as bindConferDictation hands the page its two methods; press() runs its own start: beforeStart, then the mic.
  const dictation = { stops: [], available: null, on: false, starts: 0, opts: null };
  const bindConferDictation = (o) => {
    dictation.opts = o;
    return { stop: async (reason) => { dictation.stops.push(reason); if (reason === 'voice_started' && dictation.slow) await dictation.slow; dictation.on = false; },
      setAvailable: (v) => { dictation.available = v; } };
  };
  dictation.press = async () => { await dictation.opts.beforeStart(); dictation.on = true; dictation.starts += 1; };
  // The direct controller: the public surface the session module reads, started on a synthetic transport.
  const direct = { instances: [], gate: null };
  class DirectController {
    constructor(o) { this.o = o; this._state = 'idle'; this.turns = []; this.ended = []; this.instructions = []; this.mic = false; direct.instances.push(this); }
    sessionId() { return this.sid || null; } state() { return this._state; }
    speak(t) { this.instructions.push(t); return true; }
    speakTracked(t) { this.instructions.push(t); return Promise.resolve('resp_summary'); }
    mute() {}
    inputOf() { return null; } placeOf(item) { return Number(String(item).replace(/\D/g, '')); }
    async startSession(args) {
      this.args = args; this._state = 'requesting_mic';
      const answer = routes.bootstrap ? routes.bootstrap(args) : null;
      if (answer) { this._state = 'idle'; this.o.onFatalError({ reason: 'bootstrap_rejected', status: answer.status, detail: answer.error }); return; }
      this.sid = SID; this.o.onSessionInfo({ provider: args.provider, voice_session_id: SID, voice_notices: routes.notices || [] });
      this._state = 'connecting'; this.o.onStateChange('connecting'); this.mic = true;
      if (direct.gate) await direct.gate;
      if (this._state === 'ended') return;
      this._state = 'active'; this.o.onStateChange('active');
    }
    endSession(reason) {
      if (['ending', 'ended'].includes(this._state)) return;
      this.ended.push(reason); this.mic = false; this._state = 'ended'; this.o.onStateChange('ended');
      this.o.onFinalize({ turns: this.turns.map((t) => ({ ...t })), session_id: this.sid });
    }
    utter(text) { this.turns = [...this.turns, { speaker: 'user', text, completed: true }]; this.o.onTranscriptUpdate(this.turns.map((t) => ({ ...t }))); this.o.onUserTurn(text); }
    reply(text) { this.turns = [...this.turns, { speaker: 'assistant', text, completed: true }]; this.o.onTranscriptUpdate(this.turns.map((t) => ({ ...t }))); this.o.onAssistantTurn(text); }
  }
  const server = { view: VIEW, sessions };
  const fetch = async (url, opts = {}) => {
    const body = opts.body ? JSON.parse(opts.body) : null;
    calls.push({ url, method: opts.method || 'GET', body, opts });
    const bare = url.split('?')[0];
    const own = Object.keys(routes).filter((k) => k.startsWith('/')).sort((a, b) => b.length - a.length).find((k) => bare.endsWith(k));
    let out = own ? await routes[own](body, url) : null;
    if (!out) {
      if (bare.endsWith('/api/realtime-voice/direct/api/realtime-voice/capabilities')) {
        if (caps === 'network') throw new Error('offline');
        out = caps ? { status: 200, body: caps } : { status: 404, body: {} };
      } else if (bare.endsWith('/sessions')) out = { status: 200, body: { ok: true, sessions: server.sessions } };
      else if (bare.endsWith(`/sessions/${SID}`)) out = { status: 200, body: server.view };
      else if (bare.endsWith('/transcript')) out = { status: 200, body: { ok: true, kept: body.events.length } };
      else if (bare.endsWith('/consult')) out = { status: 200, body: { ok: true, outcome: 'done', spoken: 'Two meetings today.' } };
      else if (bare.endsWith('/end')) { if (server.holdEnd) await server.holdEnd; out = { status: 200, body: { ok: true, ended: true, capture: 'complete' } }; }
      else if (bare.endsWith('/keep_talking')) out = { status: 200, body: { ok: true } };
      else out = { status: 404, body: {} };
    }
    return { ok: out.status >= 200 && out.status < 300, status: out.status, json: async () => out.body };
  };
  const sandbox = { document, window, fetch, console, URL, Blob, setTimeout, clearTimeout, Promise, AbortController,
    UI_BASE: path.replace(/\/ui(\/[^/]*)?\/?$/, '') + '/ui/', chatLog: chat, cosWriteHeaders: () => ({ 'X-CSRF-Token': 'csrf-synthetic' }),
    appendMsg: (role, text) => appended.push([role, text]),
    ConferVoiceController, RealtimeVoiceController, bindConferDictation, conferVoiceAvailability,
    importConfer: async () => { imports.push('confer-voice'); return PAGE; },
    importSession: async () => { imports.push('session'); return SESSION; },
    importController: async () => { imports.push('controller'); return { RealtimeVoiceController: controller || DirectController }; } };
  vm.createContext(sandbox);
  vm.runInContext(SCRIPT, sandbox);
  const live = () => node('voice-live');
  const q = (root, sel) => root.querySelector(sel);
  return {
    nodes: node, chat, calls, appended, today, dictation, direct, server, imports, docEvents, winEvents, store, timers,
    get c() { return direct.instances.at(-1); },
    async click(n) { n.click(); await flush(); },
    async voice() { node('btn-voice').click(); await flush(); },
    bar: (sel) => q(live(), sel),
    line: () => node('voice-transcript').textContent,
    posts: (end) => calls.filter((x) => x.method === 'POST' && x.url.split('?')[0].endsWith(end)),
    gets: (part) => calls.filter((x) => x.method === 'GET' && x.url.includes(part)),
    async advance(ms) {
      const until = now + ms;
      for (;;) {
        const next = [...timers.entries()].filter(([, t]) => t.due <= until).sort((a, b) => a[1].due - b[1].due)[0];
        if (!next) break;
        const [id, t] = next; now = t.due;
        if (t.every) t.due += t.every; else timers.delete(id);
        t.fn(); await flush(4);
      }
      now = until; await flush();
    },
    done() { Date.now = realNow; },
  };
}
// Every case runs; failures are listed with their assertion, then the exit code is 1.
const t = (() => {
  const failures = [];
  let cases = 0;
  return {
    async check(name, fn) { cases++; try { await fn(); } catch (e) { failures.push(`${name}: ${String(e.message).split('\n').slice(0, 6).join(' | ')}`); } },
    done() {
      if (failures.length) { console.log(`${failures.length} of ${cases} Confer direct-voice scenarios FAILED:\n  ${failures.join('\n  ')}`); process.exit(1); }
      console.log(`${cases} Confer direct-voice scenarios passed (not admitted = today, the live bar, the reveal, recovery, the summary card, needs attention, Dictate and companion handover, pagehide, Check-only words, phone width); fake controller, fetch, DOM and timers only.`);
    },
  };
})();
async function scenario(name, opts, fn) {
  await t.check(name, async () => {
    const p = page(opts);
    try { await flush(); await fn(p); } finally { p.done(); }
  });
}
const textOf = (n) => (n ? n.textContent : null);
const all = (root, sel) => root.querySelectorAll(sel);

// ── item 1: not admitted = today, by behaviour ──────────────────────────────────────────────────────────────────────
const READS = process.env.CONFER_PRIOR ? [] : [['GET', '../api/realtime-voice/direct/api/realtime-voice/capabilities']];
for (const [why, caps] of [['her routes not installed (404)', null], ['installed, not admitted, nothing to look at (S1)',
  { ...CAPS, direct_voice: false, direct_records: false }], ['the read failing', 'network']]) {
  await scenario(`not admitted = today (${why}): one capabilities read, then today's modes exactly as before`, { caps }, async (p) => {
    assert.deepEqual(p.calls.map((x) => [x.method, x.url]), READS);
    assert.deepEqual(p.imports, [], 'her direct page was loaded');
    assert.equal(p.nodes('voice-mode').closest().hidden, false);                 // the mode select stays
    assert.equal(p.nodes('voice-keep-transcript').closest().hidden, false);
    assert.equal(p.nodes('voice-provider').textContent, 'Voice with Chief of Staff');
    assert.equal(p.nodes('voice-live').hidden, true);
    assert.equal(p.dictation.available, true);
    await p.voice();                                                            // realtime (managed), as today
    assert.equal(p.today.managed[0].args.provider, 'openai');
    assert.deepEqual(p.dictation.stops, ['voice_started']);
    assert.equal(p.nodes('btn-voice').textContent, '■');
    await p.voice();
    assert.deepEqual(p.today.ended, ['user_ended']);
    p.nodes('voice-mode').value = 'chained';                                    // turn by turn, "Hearing: …" as today
    await p.voice();
    assert.equal(p.today.chained[0].started, 'openai');
    assert.equal(p.line(), 'Hearing: half a thought');
    await p.dictation.press();                                                  // Dictate takes the microphone from it
    assert.deepEqual(p.today.stopped, ['dictation_started']);
    assert.equal(p.calls.length, READS.length, 'another request than the one capabilities read');
    assert.deepEqual(p.direct.instances, []);
  });
}
await scenario('not admitted = today, with managed realtime off: one Voice button on the turn-by-turn mode, as before', {
  caps: null, managed: false }, async (p) => {
  assert.deepEqual(p.calls.map((x) => [x.method, x.url]), READS);
  await p.voice();
  assert.equal(p.today.chained[0].started, 'openai');
  assert.equal(p.line(), 'Hearing: half a thought');
  assert.equal(p.today.managed[0].args, undefined);
  await p.voice();
  assert.deepEqual(p.today.stopped, ['user_ended']);
  assert.deepEqual(p.imports, []);
});
await scenario('not admitted, with sessions to look at (S3): they are listed once, and Voice is still today\'s', {
  caps: { ...CAPS, direct_voice: false, direct_records: true },
  sessions: [{ ...VIEW, state: 'recovering', capture: 'unconfirmed', reasons: ['recovering'], needs_attention: true }] }, async (p) => {
  await flush();
  assert.deepEqual(p.imports.sort(), ['confer-voice', 'session']);               // no controller: nothing can start on it
  assert.equal(p.gets('/sessions').length, 1);
  assert.equal(p.gets('/sessions')[0].url, `${P}/sessions`);
  assert.equal(textOf(p.nodes('voice-sessions').querySelector('[data-voice-reason]')), PAGE.RECOVERING);
  assert.equal(p.nodes('voice-mode').closest().hidden, false);
  assert.equal(p.nodes('voice-provider').textContent, 'Voice with Chief of Staff');
  await p.voice();                                                                 // today's realtime, not the direct path
  assert.equal(p.today.managed[0].args.provider, 'openai');
  assert.deepEqual(p.direct.instances, []);
});

// ── item 1, admitted: one Voice button, no mode select, no "Hearing: …" ─────────────────────────────────────────────
await scenario('admitted: one Voice button; the mode select and running transcript are gone; the provider choice is quiet', {}, async (p) => {
  assert.deepEqual(p.imports.sort(), ['confer-voice', 'session']);
  assert.equal(p.nodes('voice-mode').closest().hidden, true);
  assert.equal(p.nodes('voice-mode').disabled, true);
  assert.equal(p.nodes('voice-keep-transcript').closest().hidden, true);
  const select = p.nodes('voice-provider').querySelector('[data-voice-provider]');
  assert.ok(select, 'no provider choice');
  assert.deepEqual(select.elements.map((o) => [o.getAttribute('value'), o.disabled, o.textContent]),
    [['openai', false, 'OpenAI'], ['xai', true, 'Grok · no voice chosen yet']]);
  assert.equal(select.elements[1].getAttribute('title'), 'Voice unavailable: no Grok voice is chosen for Chief of Staff yet.');
  assert.equal(select.value, 'openai');
  assert.equal(p.dictation.available, true);                                       // Dictate as today
  assert.equal(p.nodes('voice-live').hidden, true);
});
await scenario('admitted: a provider the card names is offered; on Grok it says only Check acts, before and during', {
  caps: { ...CAPS, unvoiced: {} }, saved: 'xai' }, async (p) => {
  const select = p.nodes('voice-provider').querySelector('[data-voice-provider]');
  assert.deepEqual(select.elements.map((o) => o.textContent), ['OpenAI', 'Grok · acts only through Check with CoS']);
  assert.equal(select.value, 'xai');                                                // remembered under her own key
  await p.voice();
  assert.equal(p.c.args.provider, 'xai');
  assert.equal(textOf(p.bar('[data-voice-notice]')), 'On this voice, Chief of Staff acts only when you press Check with CoS.');
  assert.equal(p.bar('[data-voice-notice]').hidden, false);
  select.value = 'openai'; select.dispatch('change');
  assert.equal(p.store.get('minimoi.cos.voice.provider'), 'openai');
});

// ── item 2: the live bar ────────────────────────────────────────────────────────────────────────────────────────────
await scenario('the live bar: voice on, the time, Check with CoS and End; nothing said shows while live', {}, async (p) => {
  await p.voice();
  assert.deepEqual(p.dictation.stops, ['voice_started']);                          // Dictate yields first, as today
  const c = p.c;
  assert.deepEqual(c.args, { provider: 'openai', persona: 'chief_of_staff', scene: '', conversation_id: 'owner' });
  assert.equal(c.o.bootstrapUrl, `${P}/bootstrap`);
  assert.equal(c.o.bootstrapTimeoutMs, 36000);
  assert.equal(JSON.stringify(c.o.requestHeaders()), JSON.stringify({ 'Content-Type': 'application/json', 'X-CSRF-Token': 'csrf-synthetic' }));
  assert.equal(p.nodes('voice-live').hidden, false);
  assert.equal(textOf(p.bar('[data-voice-check]')), 'Check with CoS');
  assert.equal(textOf(p.bar('[data-voice-end]')), 'End');
  assert.equal(textOf(p.bar('[data-voice-elapsed]')), '0s');
  assert.equal(p.bar('[data-voice-propose]').hidden, true);
  assert.equal(p.nodes('btn-voice').textContent, '■');
  assert.equal(p.nodes('btn-voice').getAttribute('aria-pressed'), 'true');
  assert.equal(p.nodes('voice-status').textContent, 'Listening');
  await p.advance(65000);
  assert.equal(textOf(p.bar('[data-voice-elapsed]')), '1m 05s');
  c.utter('What is on my agenda?'); c.reply('Two meetings.'); await flush();
  assert.deepEqual(p.appended, [], 'what was said went into the conversation while live');
  assert.equal(p.line(), '');
  assert.ok(!p.nodes('voice-live').textContent.includes('agenda') && !p.chat.textContent.includes('agenda'));
  assert.equal(p.posts('/transcript').length >= 1, true);                          // kept on her server as it is said
});
await scenario('Sum up and Keep talking show only when offered; Keep talking withdraws; End while offered sums up', {
  routes: { '/transcript': (body) => (body.events.some((e) => e.text === 'I think we are done.') ? { status: 200, body: { ok: true, kept: 1, propose_end: true } } : null),
    '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'We covered the agenda.' } }) } }, async (p) => {
  await p.voice();
  p.c.utter('I think we are done.'); await flush();
  assert.equal(p.bar('[data-voice-propose]').hidden, false);
  await p.click(p.bar('[data-voice-keep]'));
  assert.equal(p.posts('/keep_talking').length, 1);
  assert.equal(p.bar('[data-voice-propose]').hidden, true);
  p.c.utter('I think we are done.'); await flush();                               // offered again by the next post
  assert.equal(p.bar('[data-voice-propose]').hidden, false);
  await p.click(p.bar('[data-voice-end]')); await flush();                        // End while Sum up is offered: sums up
  assert.equal(p.posts('/summary').length, 1);
  assert.equal(p.posts('/summary')[0].url, `${P}/session/${SID}/summary`);
  assert.equal(p.bar('[data-voice-propose]').hidden, true);
});

// ── item 7 (B N8): Check shows the words it sends ───────────────────────────────────────────────────────────────────
await scenario('Check shows the words it sends, then with the answer; the answer is said by the voice, not shown', {}, async (p) => {
  await p.voice();
  p.c.utter('Check the board.'); p.c.utter('okay'); await flush();
  await p.click(p.bar('[data-voice-check]'));
  assert.equal(p.posts('/consult')[0].body.owner_transcript_item, 'okay');
  assert.equal(textOf(p.bar('[data-voice-checked]')), 'Checked “okay”');
  assert.equal(p.bar('[data-voice-checked]').hidden, false);
  assert.ok(p.c.instructions.some((x) => x === 'Say this briefly, adding nothing: Two meetings today.'));
  assert.ok(!p.nodes('voice-live').textContent.includes('Two meetings today'));
});
await scenario('Check, while it runs, says "Checking" with the words and cannot be pressed again', {
  routes: { '/consult': () => new Promise(() => {}) } }, async (p) => {
  await p.voice();
  p.c.utter('Run the nightly job.'); await flush();
  await p.click(p.bar('[data-voice-check]'));
  assert.equal(textOf(p.bar('[data-voice-checked]')), 'Checking “Run the nightly job.”…');
  assert.equal(p.bar('[data-voice-check]').disabled, true);
});

// ── item 4 and 5: End, the reveal, Download transcript ──────────────────────────────────────────────────────────────
await scenario('End: End on her server, then the kept records revealed in the conversation, with Download transcript', {}, async (p) => {
  await p.voice();
  p.c.utter('What is on my agenda?'); p.c.reply('Two meetings.'); await flush();
  await p.click(p.bar('[data-voice-end]')); await flush();
  assert.deepEqual(p.c.ended, ['user_ended']);
  const [end] = p.posts('/end');
  assert.equal(end.url, `${P}/session/${SID}/end`);
  assert.equal(p.gets(`/sessions/${SID}?conversation_id=owner`).length, 1);
  const block = p.chat.querySelector(`[data-voice-session="${SID}"]`);
  assert.ok(block, 'nothing was revealed');
  assert.equal(textOf(block.elements[0]), 'Voice with CoS · 2026-10-28 10:00–10:04');
  assert.equal(textOf(block.querySelector('[data-voice-capture]')), PAGE.KEPT);
  const rows = all(block, '[data-voice-item]');
  assert.deepEqual(rows.map((r) => [r.getAttribute('data-voice-item'), textOf(r.elements[0]), textOf(r.elements[1])]),
    [['t0', 'You · spoken', 'What is on my agenda?'], ['t1', 'CoS · spoken', 'Two meetings.']]);
  const link = block.querySelector('[data-voice-download]');
  assert.equal(link.getAttribute('href'), `${P}/sessions/${SID}/transcript?conversation_id=owner`);
  assert.equal(textOf(link), 'Download transcript');
  assert.equal(p.line(), "Voice ended. What was said is kept in Chief of Staff's voice records, which she can recall.");   // E N4
  assert.equal(p.nodes('voice-live').hidden, true);
  assert.equal(p.nodes('btn-voice').textContent, '🎤');
  assert.equal(p.gets('/sessions').filter((x) => x.url.split('?')[0].endsWith('/sessions')).length, 1);   // the list, read after End
});
await scenario('the reveal marks his turns not acted on, and says why for each', {}, async (p) => {
  p.server.view = { ...VIEW, not_acted: { t0: 'cut_short', t2: 'overtaken', t3: 'closed', t4: null },
    records: [{ role: 'user', item_id: 't0', text: 'Save a note that A.', partial: false, interrupted: false },
      { role: 'assistant', item_id: 't1', text: 'Sure', partial: true, interrupted: true },
      { role: 'user', item_id: 't2', text: 'Save a note that B.', partial: false, interrupted: false },
      { role: 'user', item_id: 't3', text: 'Check the board.', partial: false, interrupted: false },
      { role: 'user', item_id: 't4', text: 'And the glaze.', partial: true, interrupted: false },
      { role: 'consult', item_id: 'kt3', text: 'consult: Check the board.', partial: false, interrupted: false },
      { role: 'receipt', item_id: 'kt3', text: 'Answered. Turn 9.', partial: false, interrupted: false }] };
  await p.voice(); await p.click(p.bar('[data-voice-end]')); await flush();
  const block = p.chat.querySelector(`[data-voice-session="${SID}"]`);
  const label = (item) => textOf(block.querySelector(`[data-voice-item="${item}"]`).elements[0]);
  assert.equal(label('t0'), `You · spoken · ${PAGE.NOT_ACTED} · its reply was cut short`);
  assert.equal(label('t1'), 'CoS · spoken · interrupted');
  assert.equal(label('t2'), `You · spoken · ${PAGE.NOT_ACTED} · overtaken by what you said next`);
  assert.equal(label('t3'), `You · spoken · ${PAGE.CUT_OFF}`);                     // Q N12: End cut its reply off
  assert.equal(label('t4'), `You · spoken · partial · ${PAGE.NOT_ACTED}`);          // named with no reason kept: no reason said
  const notes = all(block, '[data-voice-item="kt3"]').map((r) => textOf(r.elements[0]));
  assert.deepEqual(notes, ['Asked CoS', 'Result']);
});
await scenario('the reveal says capture honestly: incomplete with its count, unconfirmed, and partial download', {}, async (p) => {
  p.server.view = { ...VIEW, capture: 'incomplete', missing: 2 };
  await p.voice(); await p.click(p.bar('[data-voice-end]')); await flush();
  const block = p.chat.querySelector(`[data-voice-session="${SID}"]`);
  assert.equal(textOf(block.querySelector('[data-voice-capture]')), PAGE.incompleteLine(2));
  assert.equal(textOf(block.querySelector('[data-voice-download]')), 'Download transcript (partial)');
  const unconfirmed = PAGE.conferSessions({ doc: fakeDocument(), direct: CAPS, chat: null, list: null, get: null, post: null })
    .render({ ...VIEW, state: 'dropped', capture: 'unconfirmed', ended_at: null });
  assert.equal(textOf(unconfirmed.querySelector('[data-voice-capture]')), PAGE.DROPPED);
  assert.equal(textOf(unconfirmed.querySelector('[data-voice-download]')), 'Download transcript (partial)');
  const recovering = PAGE.conferSessions({ doc: fakeDocument(), direct: CAPS }).render({ ...VIEW, state: 'recovering', capture: 'unconfirmed', ended_at: null });
  assert.equal(textOf(recovering.querySelector('[data-voice-capture]')), PAGE.RECOVERING);
  const damaged = PAGE.conferSessions({ doc: fakeDocument(), direct: CAPS }).render({ ...VIEW, damaged: true, records: [] });
  assert.equal(textOf(damaged.querySelector('[data-voice-capture]')), PAGE.DAMAGED);
  assert.equal(damaged.querySelector('[data-voice-download]'), null);
});
await scenario('LV6: a turn the provider repeats at the start of the next is shown as kept, never merged', {}, async (p) => {
  p.server.view = { ...VIEW, records: [{ role: 'user', item_id: 't0', text: 'Name an animal.', partial: false, interrupted: false },
    { role: 'user', item_id: 't1', text: 'Name an animal. Trees grow tall.', partial: false, interrupted: false }] };
  await p.voice(); await p.click(p.bar('[data-voice-end]')); await flush();
  const block = p.chat.querySelector(`[data-voice-session="${SID}"]`);
  assert.deepEqual(all(block, '[data-voice-item]').map((r) => textOf(r.elements[1])), ['Name an animal.', 'Name an animal. Trees grow tall.']);
});

// ── item 4: the summary card, Ask for a summary ─────────────────────────────────────────────────────────────────────
const PROPOSAL = '[Voice summary proposal] For this voice session only. Not confirmed. Covers all 2 retained records of this session.\n\nWe went over the agenda: two meetings.';
await scenario('the summary card: the proposal, its scope line, Confirm; the first decision stands', {
  routes: { '/summary/decision': (body) => ({ status: 200, body: { ok: true, state: { confirm: 'confirmed', edit: 'edited', decline: 'declined' }[body.decision] } }) } }, async (p) => {
  p.server.view = { ...VIEW, summary: 'proposed', proposal: PROPOSAL };
  await p.voice(); await p.click(p.bar('[data-voice-end]')); await flush();
  const box = p.chat.querySelector('[data-voice-summary-card]');
  assert.ok(box, 'no summary card');
  assert.ok(box.textContent.includes('We went over the agenda: two meetings.'));
  assert.ok(box.textContent.includes(PAGE.CONFIRM_SCOPE));
  assert.equal(PAGE.CONFIRM_SCOPE, 'Confirming confirms the summary only. Anything it mentions still needs its own request.');
  await p.click(box.querySelector('[data-voice-summary-confirm]'));
  const [decided] = p.posts('/summary/decision');
  assert.equal(decided.url, `${P}/session/${SID}/summary/decision`);
  assert.deepEqual(decided.body, { conversation_id: 'owner', decision: 'confirm' });
  assert.equal(textOf(box.querySelector('[data-voice-summary-result]')), 'Confirmed. It confirms the summary only; it authorizes nothing it mentions.');
  assert.equal(box.querySelector('[data-voice-summary-confirm]'), null);
});
await scenario('the summary card: Edit starts from the written part and sends his wording; Decline; already decided', {}, async (p) => {
  // The card on its own (as Open shows it in the list), against the decision route's two answers.
  const post = async (url, body) => {
    p.calls.push({ url, method: 'POST', body });
    return body.decision === 'decline' ? { ok: false, status: 409, body: { ok: false, error: 'already_decided', state: 'edited' } }
      : { ok: true, status: 200, body: { ok: true, state: 'edited' } };
  };
  const sessions = PAGE.conferSessions({ doc: fakeDocument(), direct: CAPS, chat: null, list: null,
    get: async () => ({ status: 200, body: { ok: true, sessions: [] } }), post });
  const block = sessions.render({ ...VIEW, summary: 'proposed', proposal: PROPOSAL });
  const box = block.querySelector('[data-voice-summary-card]');
  box.querySelector('[data-voice-summary-edit]').click();
  const area = box.querySelector('[data-voice-summary-editor]').elements[0];
  assert.equal(area.value, 'We went over the agenda: two meetings.');
  area.value = 'Two meetings; the glaze can wait.';
  box.querySelector('[data-voice-summary-save]').click(); await flush();
  assert.deepEqual(p.calls.at(-1).body, { conversation_id: 'owner', decision: 'edit', text: 'Two meetings; the glaze can wait.' });
  assert.ok(textOf(box.querySelector('[data-voice-summary-result]')).startsWith('Your wording replaces the proposal.'));
  const other = sessions.render({ ...VIEW, summary: 'proposed', proposal: PROPOSAL }).querySelector('[data-voice-summary-card]');
  other.querySelector('[data-voice-summary-decline]').click(); await flush();
  assert.equal(textOf(other.querySelector('[data-voice-summary-result]')), 'Already decided (edited). Nothing was changed.');
});
await scenario('Summary unavailable: Ask for a summary; a proposal that comes back is shown as its card', {
  routes: { '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'x' } }) } }, async (p) => {
  p.server.view = { ...VIEW, summary: 'unavailable' };
  await p.voice(); await p.click(p.bar('[data-voice-end]')); await flush();
  const ask = p.chat.querySelector('[data-voice-summary-retry]');
  assert.ok(ask && textOf(ask) === PAGE.ASK_SUMMARY);
  p.server.view = { ...VIEW, summary: 'proposed', proposal: PROPOSAL };
  await p.click(ask); await flush();
  assert.equal(p.posts('/summary').length, 1);
  assert.ok(p.chat.querySelector('[data-voice-summary-card]'), 'the new proposal was not shown');
  assert.equal(all(p.chat, `[data-voice-session="${SID}"]`).length, 1);
});

// ── item 4: the needs-attention list ────────────────────────────────────────────────────────────────────────────────
{
  const list = [{ ...VIEW, state: 'recovering', capture: 'unconfirmed', reasons: ['recovering'], needs_attention: true },
    { ...VIEW, session_id: 'a1b2c3d4-0000-4000-8000-000000000001', capture: 'incomplete', missing: 1, reasons: ['incomplete', 'undecided'], summary: 'proposed', needs_attention: true },
    { ...VIEW, session_id: 'a1b2c3d4-0000-4000-8000-000000000002', reasons: ['unknown_consult'], consults: { asked: 2, pending: 0, unknown: 2 }, needs_attention: true },
    { ...VIEW, session_id: 'a1b2c3d4-0000-4000-8000-000000000003', damaged: true, line: 'Voice records damaged', reasons: ['damaged'], needs_attention: true }];
  await t.check('the needs-attention list: read at load when there is something, each reason in its words', async () => {
    const p = page({ caps: { ...CAPS, direct_records: true }, sessions: list,
      routes: { '/dismiss': () => ({ status: 200, body: { ok: true, dismissed: true } }) } });
    try {
      await flush(30);
      const box = p.nodes('voice-sessions');
      assert.equal(box.hidden, false);
      assert.equal(textOf(box.elements[0]), PAGE.ATTENTION_TITLE);
      const reasons = all(box, '[data-voice-reason]').map((n) => textOf(n));
      assert.deepEqual(reasons, [PAGE.RECOVERING, PAGE.incompleteLine(1), 'Its summary waits for your decision.',
        "2 checks' results are not known: check in writing before asking again.", 'Voice records damaged.']);
      const damaged = box.querySelector('[data-voice-attention="a1b2c3d4-0000-4000-8000-000000000003"]');
      assert.equal(damaged.querySelector('[data-voice-open]').disabled, true);
      const first = box.querySelector(`[data-voice-attention="${SID}"]`);
      p.server.view = { ...VIEW, state: 'recovering', capture: 'unconfirmed', ended_at: null };
      await p.click(first.querySelector('[data-voice-open]')); await flush();
      assert.ok(first.querySelector(`[data-voice-session="${SID}"]`), 'Open did not show its records');
      assert.equal(textOf(first.querySelector('[data-voice-capture]')), PAGE.RECOVERING);
      await p.click(first.querySelector('[data-voice-dismiss]')); await flush();
      assert.equal(p.posts('/dismiss')[0].url, `${P}/sessions/${SID}/dismiss`);
      assert.deepEqual(p.posts('/dismiss')[0].body, { conversation_id: 'owner' });
      assert.equal(box.querySelector(`[data-voice-attention="${SID}"]`), null);
    } finally { p.done(); }
  });
  await t.check('a summary decided from the list keeps its row and says what was kept; the list is not re-read at once', async () => {
    const proposed = { ...VIEW, reasons: ['undecided'], summary: 'proposed', proposal: PROPOSAL, needs_attention: true };
    const p = page({ caps: { ...CAPS, direct_records: true }, sessions: [proposed],
      routes: { '/summary/decision': () => ({ status: 200, body: { ok: true, state: 'confirmed' } }) } });
    try {
      await flush(30);
      const reads = p.gets('/sessions').filter((x) => x.url.split('?')[0].endsWith('/sessions')).length;
      const row = p.nodes('voice-sessions').querySelector(`[data-voice-attention="${SID}"]`);
      p.server.view = proposed;
      await p.click(row.querySelector('[data-voice-open]')); await flush();
      await p.click(row.querySelector('[data-voice-summary-confirm]')); await flush();
      assert.equal(p.posts('/summary/decision').length, 1);
      assert.equal(textOf(row.querySelector('[data-voice-summary-result]')), 'Confirmed. It confirms the summary only; it authorizes nothing it mentions.');
      assert.ok(p.nodes('voice-sessions').querySelector(`[data-voice-attention="${SID}"]`), 'the row went before its result was seen');
      assert.equal(p.gets('/sessions').filter((x) => x.url.split('?')[0].endsWith('/sessions')).length, reads);
    } finally { p.done(); }
  });
  await t.check('the needs-attention list leaves out the session just revealed in the conversation', async () => {
    const p = page({});
    try {
      await flush();
      p.server.sessions = [{ ...VIEW, capture: 'incomplete', missing: 1, reasons: ['incomplete'], needs_attention: true }];
      await p.voice(); await p.click(p.bar('[data-voice-end]')); await flush();
      assert.ok(p.chat.querySelector(`[data-voice-session="${SID}"]`));
      assert.equal(p.nodes('voice-sessions').querySelector(`[data-voice-attention="${SID}"]`), null);
      assert.equal(p.nodes('voice-sessions').hidden, true);
    } finally { p.done(); }
  });
}

// ── item 1: admitted and refused, never another mode ────────────────────────────────────────────────────────────────
await scenario('admitted and refused: "Voice unavailable: <reason>", and no other mode is tried', {
  routes: { bootstrap: () => ({ status: 503, error: 'Voice unavailable: there is no place to keep what is said.' }) } }, async (p) => {
  await p.voice(); await flush();
  assert.equal(p.line(), 'Voice unavailable: there is no place to keep what is said.');
  assert.deepEqual([p.today.managed.length && p.today.managed.some((m) => m.args), p.today.chained.some((c) => c.started)], [false, false]);
  assert.equal(p.nodes('btn-voice').textContent, '🎤');
  assert.equal(p.posts('/end').length, 0);
});
await scenario('admitted: a provider with no voice chosen is refused on the page with its reason, nothing sent', {
  caps: { ...CAPS, providers: [{ provider: 'xai', label: 'Grok' }], default_provider: 'xai' } }, async (p) => {
  await p.voice();
  assert.equal(p.line(), 'Voice unavailable: no Grok voice is chosen for Chief of Staff yet.');
  assert.deepEqual(p.direct.instances, []);
  assert.equal(p.today.managed.some((m) => m.args), false);
});

// ── item 6: Dictate unchanged; Voice and Dictate yield; the companion handler; pagehide ─────────────────────────────
await scenario('Dictate while voice is live ends the direct session first: End sent, microphone released, then Dictate', {}, async (p) => {
  await p.voice();
  const c = p.c;
  p.c.utter('Note the kiln.'); await flush();
  await p.dictation.press(); await flush();
  assert.deepEqual(c.ended, ['dictation_started']);
  assert.equal(c.mic, false);
  assert.equal(p.dictation.on, true);
  assert.equal(p.posts('/end').length, 1);
  assert.equal(p.dictation.opts.bootstrapUrl, '../api/realtime-voice/confer/bootstrap');   // Dictate's own bootstrap, as today
});
await scenario('the companion panel: the handler ends the direct session and waits until the microphone is released', {}, async (p) => {
  await p.voice();
  const c = p.c;
  let waited = null;
  for (const fn of p.docEvents['minimoi:companion-opening'] || []) fn({ detail: { waitUntil: (x) => { waited = x; } } });
  assert.ok(waited, 'the handler gave no promise');
  await waited;
  assert.deepEqual(p.dictation.stops.at(-1), 'companion_opened');
  assert.deepEqual(c.ended, ['companion_opened']);
  assert.equal(c.mic, false);
});
await scenario('the companion panel while voice is still connecting: the handover waits for the connection, then the release', {}, async (p) => {
  let connect;
  p.direct.gate = new Promise((r) => { connect = r; });
  const starting = p.voice();
  await flush();
  const c = p.c;
  assert.equal(c.state(), 'connecting');
  let waited = null, settled = false;
  for (const fn of p.docEvents['minimoi:companion-opening'] || []) fn({ detail: { waitUntil: (x) => { waited = x; } } });
  waited.then(() => { settled = true; });
  await flush();
  assert.equal(settled, false, 'the panel opened while the microphone may still be taken');
  assert.equal(c.mic, true);
  connect(); await starting; await flush();
  assert.equal(settled, true);
  assert.deepEqual(c.ended, ['companion_opened']);  // the session module ends a held start once it connects (its own rule)
  assert.equal(c.mic, false);
});
await scenario('pagehide: one End leaves at once with keepalive, carrying the owed turns; nothing waits', {}, async (p) => {
  await p.voice();
  p.c.utter('Remember the glaze.');
  p.winEvents.pagehide();
  const ends = p.posts('/end');
  assert.equal(ends.length, 1);
  assert.equal(ends[0].opts.keepalive, true);
  assert.ok(Array.isArray(ends[0].body.unacknowledged));
  assert.deepEqual(p.dictation.stops.at(-1), 'pagehide');
  await flush();
  assert.equal(p.gets(`/sessions/${SID}`).length, 0, 'a leaving page read the reveal');
  // Restored from the back-forward cache: Voice is ready again, and a new session can start.
  assert.equal(p.nodes('btn-voice').textContent, '🎤');
  assert.equal(p.nodes('voice-live').hidden, true);
  await p.voice();
  assert.equal(p.direct.instances.length, 2);
});
await scenario('stopped while Dictate is still yielding the microphone: no session starts at all', {}, async (p) => {
  let yielded;
  const gate = new Promise((r) => { yielded = r; });
  p.dictation.slow = gate;                                             // Dictate takes its time to let go
  const pressing = p.voice();                                          // Voice: Dictate is asked to yield first
  await flush();
  let waited = null;
  for (const fn of p.docEvents['minimoi:companion-opening'] || []) fn({ detail: { waitUntil: (x) => { waited = x; } } });
  await flush();
  yielded(); await pressing; await waited; await flush();
  assert.deepEqual(p.direct.instances, [], 'a session started after it was stopped');
  assert.equal(p.nodes('btn-voice').textContent, '🎤');
});

// ── the portal path: her server's paths rebased under /app/cos ──────────────────────────────────────────────────────
await scenario('through the portal: every direct URL is rebased on the page\'s place, the capabilities read relative', {
  path: '/app/cos/ui/confer' }, async (p) => {
  assert.equal(p.calls[0].url, '../api/realtime-voice/direct/api/realtime-voice/capabilities');
  await p.voice();
  assert.equal(p.c.o.bootstrapUrl, `/app/cos${P}/bootstrap`);
  p.c.utter('Hello.'); await flush();
  await p.click(p.bar('[data-voice-end]')); await flush();
  assert.equal(p.posts('/end')[0].url, `/app/cos${P}/session/${SID}/end`);
  assert.equal(p.gets(`/app/cos${P}/sessions/${SID}?conversation_id=owner`).length, 1);
  assert.equal(p.chat.querySelector('[data-voice-download]').getAttribute('href'), `/app/cos${P}/sessions/${SID}/transcript?conversation_id=owner`);
  assert.deepEqual(PAGE.rebase({ a_url: '/app/cos/x', b_url: '//evil/x', c: '/y', d_url: 'rel' }, '/app/cos'),
    { a_url: '/app/cos/x', b_url: '//evil/x', c: '/y', d_url: 'rel' });
});

// ── phone width: a layout model at 375 px (the real look waits for A4) ──────────────────────────────────────────────
await t.check('phone width (375 px model): every voice row wraps, no fixed width, long words break, taps are 44 px', async () => {
  const rules = {};
  for (const m of CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    for (const sel of m[1].split(',').map((x) => x.trim()).filter(Boolean)) rules[sel] = (rules[sel] || '') + m[2];
  }
  const decl = (sel, prop) => new RegExp(`(?:^|;)\\s*${prop}\\s*:\\s*([^;]+)`).exec(rules[sel] || '')?.[1]?.trim();
  for (const row of ['.voice-live', '.voice-propose', '.voice-actions', '.voice-attention-head']) {
    assert.equal(decl(row, 'flex-wrap'), 'wrap', `${row} does not wrap`);
    assert.ok(/flex/.test(decl(row, 'display') || ''), `${row} is not a flex row`);
  }
  for (const [sel, body] of Object.entries(rules)) {
    if (!/voice/.test(sel)) continue;
    for (const m of body.matchAll(/(?:^|;)\s*(min-width|width)\s*:\s*(\d+)px/g)) assert.ok(Number(m[2]) <= 16, `${sel} fixes ${m[1]} ${m[2]}px`);
    assert.ok(!/white-space\s*:\s*nowrap/.test(body), `${sel} never wraps its text`);
  }
  for (const text of ['.voice-checked', '.voice-notice', '.voice-line', '.msg-voice .msg-text', '.voice-card .msg-text', '.voice-download', '.voice-card-head'])
    assert.equal(decl(text, 'overflow-wrap'), 'anywhere', `${text} can push the page sideways`);
  for (const box of ['.voice-live', '.voice-act', '.voice-session', '.voice-card', '.voice-provider-choice', '.voice-sessions'])
    assert.equal(decl(box, 'max-width'), '100%', `${box} has no width bound`);
  assert.ok(/\.voice-edit textarea\s*\{[^}]*width:\s*100%/.test(CSS));
  const phone = /@media \(max-width: 600px\) \{\s*\.voice-act, \.voice-provider-choice \{ min-height: 44px; \}\s*\}/.test(CSS);
  assert.ok(phone, 'no 44 px tap height for the voice controls on a phone');
  assert.ok(/\.voice-live\[hidden\][^{]*\{\s*display: none !important;/.test(CSS), 'a hidden voice part could still show (display beats hidden)');
  // The widest control's own box at the phone's font: label characters at 0.76rem Georgia (~7 px each) plus padding and
  // border; each must fit the 375 px screen less Confer's gutters (1.25rem a side at <= 768 px), so a row never overflows.
  const width = (label) => Math.ceil(label.length * 7 + 2 * 10 + 2);
  const room = 375 - 2 * 20;
  for (const label of ['Check with CoS', 'End', 'Sum up', 'Keep talking', 'Confirm', 'Edit', 'Decline', 'Ask for a summary', 'Open', 'Dismiss',
    'Grok · acts only through Check with CoS', 'Download transcript (partial)'])
    assert.ok(width(label) <= room, `"${label}" (${width(label)} px) does not fit ${room} px`);
});
await scenario('phone width: every live control is a button in the bar, shown and enabled while live, and works by tap', {}, async (p) => {
  await p.voice();
  const bar = p.nodes('voice-live');
  for (const sel of ['[data-voice-check]', '[data-voice-end]']) {
    const b = bar.querySelector(sel);
    assert.equal(b.tagName, 'BUTTON'); assert.equal(b.hidden, false); assert.equal(b.disabled, false);
    assert.equal(b.parent, bar, `${sel} is inside something that could hide it`);
  }
  const propose = bar.querySelector('[data-voice-propose]');
  assert.deepEqual([propose.querySelector('[data-voice-sumup]').tagName, propose.querySelector('[data-voice-keep]').tagName], ['BUTTON', 'BUTTON']);
  assert.equal(all(bar, '[data-voice-provider]').length, 0);                    // no menu in the live bar
  p.c.utter('Hello.'); await flush();
  await p.click(bar.querySelector('[data-voice-end]')); await flush();          // End works by tap
  assert.equal(p.posts('/end').length, 1);
});


// ── Gate 2: Q S2 (admitted and refused), Q N10 (the restart block), E S2 (a check still running at End) ────────────────
for (const [why, caps, words] of [
  ['no store', { ...CAPS, direct_voice: false, direct_refused: 'Voice unavailable: there is no place to keep what is said.' },
    'Voice unavailable: there is no place to keep what is said.'],
  ['no allowance', { ...CAPS, direct_voice: false, direct_refused: 'Voice unavailable: no realtime voice allowance is set here.' },
    'Voice unavailable: no realtime voice allowance is set here.'],
  ['no provider offered', { ...CAPS, providers: [] }, 'Voice unavailable here right now. Typing still works.']]) {
  await scenario(`Q S2: admitted but refused (${why}): Voice says why, and today's modes are never offered`, { caps }, async (p) => {
    await flush();
    assert.equal(p.nodes('voice-mode').closest().hidden, true, 'the mode select is still offered');
    assert.equal(p.nodes('voice-keep-transcript').closest().hidden, true);
    assert.equal(p.nodes('voice-provider').textContent, 'Voice unavailable · typing still works');
    await p.voice();
    assert.equal(p.line(), words);
    assert.equal(p.today.managed.some((m) => m.args), false, "today's realtime started");
    assert.equal(p.today.chained.some((c) => c.started), false, "today's turn-by-turn started");
    assert.deepEqual(p.direct.instances, []);
    assert.equal(p.dictation.available, true);                                     // Dictate as today
  });
}
{
  let release;
  const gate = new Promise((r) => { release = r; });
  await scenario('Q N10: a managed session ends with its termination uncertain before the capabilities answer: no direct start', {
    routes: { '/capabilities': async () => { await gate; return { status: 200, body: CAPS }; } } }, async (p) => {
    await p.voice();                                                               // today's realtime, before the answer
    const managed = p.today.managed[0];
    assert.equal(managed.args.provider, 'openai');
    managed.o.onFatalError({ reason: 'termination_uncertain', detail: 'Microphone stopped locally; provider termination is unconfirmed.' });
    managed.o.onStateChange('ended');
    release(); await flush(30);                                                    // now the direct path is offered
    await p.voice();
    assert.deepEqual(p.direct.instances, [], 'a direct session started past the restart block');
    assert.equal(p.nodes('btn-voice').disabled, true);
  });
}
await scenario('E S2: End while a check runs: the reveal marks it still running and says where its result will be', {}, async (p) => {
  p.server.view = { ...VIEW, consults: { asked: 1, pending: 1, unknown: 0 }, pending: ['t0'], reasons: ['pending_consult'], needs_attention: true,
    records: [{ role: 'user', item_id: 't0', text: 'What is stuck on 12?', partial: false, interrupted: false },
      { role: 'consult', item_id: 'kt0', text: 'consult: What is stuck on 12?', partial: false, interrupted: false },
      { role: 'assistant', item_id: 't1', text: "That's taking longer; the answer will be in the conversation.", partial: false, interrupted: false }] };
  await p.voice(); await p.click(p.bar('[data-voice-end]')); await flush();
  const block = p.chat.querySelector(`[data-voice-session="${SID}"]`);
  assert.equal(textOf(block.querySelector('[data-voice-item="kt0"]').elements[0]), `Asked CoS · ${PAGE.STILL_RUNNING}`);
  assert.equal(textOf(block.querySelector('[data-voice-pending]')), PAGE.PENDING_LINE);
});
await t.check('E S2: the list names a check still running at End, and one that landed after it; Open shows it late', async () => {
  const OTHER = 'a1b2c3d4-0000-4000-8000-000000000009';
  const running = { ...VIEW, session_id: OTHER, reasons: ['pending_consult'], consults: { asked: 1, pending: 1, unknown: 0 }, pending: ['t0'], needs_attention: true };
  const landed = { ...VIEW, reasons: ['late_result'], consults: { asked: 1, pending: 0, unknown: 0 }, late: ['t0'], needs_attention: true };
  const p = page({ caps: { ...CAPS, direct_records: true }, sessions: [running, landed] });
  try {
    await flush(30);
    const box = p.nodes('voice-sessions');
    assert.deepEqual(all(box, '[data-voice-reason]').map((n) => textOf(n)),
      ['A check was still running when the voice ended; its result is kept when it lands.', 'A check finished after the voice ended: open to see its result.']);
    p.server.view = { ...landed, records: [{ role: 'user', item_id: 't0', text: 'What is stuck on 12?', partial: false, interrupted: false },
      { role: 'consult', item_id: 'kt0', text: 'consult: What is stuck on 12?', partial: false, interrupted: false },
      { role: 'receipt', item_id: 'kt0', text: 'Answered. Turn 9. Spoken: Two items are stuck on 12.', partial: false, interrupted: false }] };
    const row = box.querySelector(`[data-voice-attention="${SID}"]`);
    await p.click(row.querySelector('[data-voice-open]')); await flush();
    const receipt = all(row, '[data-voice-item="kt0"]').at(-1);
    assert.equal(textOf(receipt.elements[0]), 'Result · arrived after the voice ended');
    assert.equal(textOf(receipt.elements[1]), 'Answered. Turn 9. Spoken: Two items are stuck on 12.');
    assert.equal(PAGE.lateLabel({ summary: 'proposed' }), 'arrived after the summary');
  } finally { p.done(); }
});


// ── Gate 2: E N4 (where the record lives), Q N9 (an End not confirmed), Q N12 (cut off at End), Q S4 (Dictate's line) ──
await scenario('E N4: the ended line says where what was said is kept, which stays true after a reload; incomplete too', {
  routes: { '/end': () => ({ status: 200, body: { ok: true, ended: true, capture: 'incomplete', missing: 2 } }) } }, async (p) => {
  await p.voice();
  p.c.utter('Hello.'); await flush();
  await p.click(p.bar('[data-voice-end]')); await flush();
  assert.equal(p.line(), "Voice ended. Some of what was said was not kept (2 turns). What was kept is in Chief of Staff's voice records, which she can recall.");
  assert.ok(!p.line().includes('in the conversation'));
});
await scenario('Q N9: End not confirmed says to reload later, never that it appears by itself', {
  routes: { '/end': () => ({ status: 503, body: {} }) } }, async (p) => {
  await p.voice(); await p.click(p.bar('[data-voice-end]')); await flush();
  assert.equal(p.line(), SESSION.END_UNCONFIRMED);
  assert.equal(SESSION.END_UNCONFIRMED, 'Voice ended here, but the server did not confirm it. Reload the page later to see what was kept.');
});
await scenario("Q S4: voice's late End leaves Dictate's status line alone after the handover", {}, async (p) => {
  let answer;
  const ended = new Promise((r) => { answer = r; });
  await p.voice();
  p.c.utter('Note the kiln.'); await flush();
  assert.equal(p.nodes('voice-status').textContent, 'Listening');
  p.server.holdEnd = ended;                                                // End's answer is held until Dictate is on
  const pressing = (async () => { await p.dictation.opts.beforeStart(); p.dictation.opts.onStatus('Dictating into your draft. Stop, review, then Send.'); p.dictation.on = true; })();
  await flush();
  await pressing;
  assert.equal(p.nodes('voice-status').textContent, 'Dictating into your draft. Stop, review, then Send.');
  answer(); await flush(30);
  assert.equal(p.posts('/end').length, 1);
  assert.ok(p.chat.querySelector(`[data-voice-session="${SID}"]`), 'the reveal did not come');
  assert.equal(p.nodes('voice-status').textContent, 'Dictating into your draft. Stop, review, then Send.', "voice's End wiped Dictate's line");
});


// ── Gate 2 Q S3: a start ended while its bootstrap is slow, through the real controller ──────────────────────────────────
for (const late of ['answers at 20 s', 'never answers (the bound aborts it)']) {
  await t.check(`Q S3: the companion panel while the bootstrap is slow (${late}): after the handover nothing connects or speaks`, async () => {
    const real = await realController();
    const p = page({ controller: real.RealtimeVoiceController });
    try {
      await flush();
      const pressing = p.voice(); await flush();
      assert.equal(real.state.bootstraps.length, 1);
      let waited = null, settled = false;
      for (const fn of p.docEvents['minimoi:companion-opening'] || []) fn({ detail: { waitUntil: (x) => { waited = x; } } });
      waited.then(() => { settled = true; });
      await flush();
      assert.equal(settled, false, 'the panel opened while the start could still take the microphone');
      await p.advance(15000); await flush();                               // the session module's hold: End, the handover
      assert.equal(settled, true);
      p.dictation.opts.onStatus('Dictating into your draft. Stop, review, then Send.');
      p.nodes('voice-transcript').textContent = '';
      if (late === 'answers at 20 s') real.state.answer({ provider: 'openai', model: 'm', warning_minutes: 20, max_minutes: 30,
        voice_session_id: SID, page_responses: true });
      else real.state.fail();
      await pressing; await flush(30);
      assert.equal(real.state.connects, 0, 'the ended start connected (and took the microphone) after the handover');
      assert.equal(real.state.adapters.length, 0, 'the ended start built a transport');
      assert.equal(p.line(), '', `the ended start said something: ${p.line()}`);
      assert.equal(p.nodes('voice-live').hidden, true);
      if (late === 'answers at 20 s') {
        const ends = p.posts('/end');
        assert.equal(ends.length, 1, 'the server session it was given was not ended');
        assert.equal(ends[0].url, `${P}/session/${SID}/end`);
      } else assert.equal(p.posts('/end').length, 0);
      await p.voice(); await flush();                                      // the button is ready: a new start goes
      assert.equal(real.state.bootstraps.length, 2);
    } finally { p.done(); real.state.restore(); }
  });
}


// ── Gate 2 verification V S2: a microphone granted after the start was ended, through the real controller and adapters ──
for (const provider of ['openai', 'xai']) {
  await t.check(`V S2 (${provider}): the microphone prompt answers after the handover: every track stops at once, nothing connects`, async () => {
    const real = await realController({ adapters: 'real' });
    const caps = provider === 'xai' ? { ...CAPS, unvoiced: {} } : CAPS;
    const p = page({ controller: real.RealtimeVoiceController, caps, saved: provider });
    try {
      await flush();
      const pressing = p.voice(); await flush();
      real.state.answer({ provider, model: 'm', warning_minutes: 20, max_minutes: 30, voice_session_id: SID,
        ...(provider === 'openai' ? { client_secret: 'ek_synthetic', page_responses: true } : { ephemeral_token: 'synthetic', session_config: {} }) });
      await flush(20);
      assert.equal(real.state.asked, 1, 'the adapter did not ask for the microphone');
      let waited = null, settled = false;
      for (const fn of p.docEvents['minimoi:companion-opening'] || []) fn({ detail: { waitUntil: (x) => { waited = x; } } });
      waited.then(() => { settled = true; });
      await flush();
      assert.equal(settled, false, 'the panel opened while the start could still take the microphone');
      await p.advance(15000); await flush();                               // past the hold: End, the handover
      assert.equal(settled, true, 'the handover never came');
      real.state.grant(); await pressing; await flush(30);                 // he grants the prompt only now
      assert.ok(real.state.tracks.length > 0 && real.state.tracks.every((t) => t.stopped), 'a track granted after End is still live');
      if (provider === 'openai') {
        assert.deepEqual(real.state.peers.map((pc) => pc.added.length), [0], 'a track was added to the ended peer');
        assert.equal(real.state.channels, 0); assert.equal(real.state.sdp, 0);
      } else assert.equal(real.state.contexts, 0, 'audio capture started after End');
      assert.ok(!/unavailable|could not|stopped/i.test(p.line()), `the ended start reported a failure: ${p.line()}`);
      assert.equal(p.posts('/end').length, 1);                             // its server session was ended, once
    } finally { p.done(); real.state.restore(); }
  });
}

t.done();
