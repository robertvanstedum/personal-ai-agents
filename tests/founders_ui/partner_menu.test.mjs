// The shared ⋯ menu (#661): one partner-menu.js for MC and CoS, run here on stand-in elements (no DOM, network or mic).
// Same controls and words for both twins; Model follows Voice; an unavailable voice is shown off with its reason; the page
// sends only the voice, the revision and the conversation; a stale page reloads; a live call holds the choice.
import assert from 'node:assert/strict';
import { initPartnerMenu } from '../../core/founders_ui/static/partner-menu.js';

function el(extra = {}) {
  const listeners = {};
  return { hidden: true, textContent: '', disabled: false, title: '', dataset: {}, ...extra,
    addEventListener(kind, fn) { (listeners[kind] ??= []).push(fn); },
    fire(kind) { return Promise.all((listeners[kind] || []).map((fn) => fn({}))); } };
}

function rig(answers) {
  const options = [el({ value: 'openai', textContent: 'OpenAI' }), el({ value: 'xai', textContent: 'Grok' })];
  const select = el({ options, value: 'openai' });
  const parts = { '[data-partner-voice-row]': el(), '[data-partner-voice]': select, '[data-partner-model-row]': el(),
    '[data-partner-model]': el(), '[data-partner-last]': el(), '[data-partner-note]': el() };
  const root = { querySelector: (s) => parts[s] || null };
  const calls = [], voices = [];
  const request = async (method, url, body) => { calls.push({ method, url, body }); return answers.shift(); };
  const menu = initPartnerMenu({ root, settingsUrl: '/x/partner-settings', request, conversationId: () => 'c-1',
    onVoice: (v) => voices.push(v), key: () => 'pm-key-0001' });
  return { menu, select, parts, calls, voices, options };
}

const view = (partner, voice, extra = {}) => ({
  partner, revision: voice === 'xai' ? 1 : 0, routes: true,
  voice: { selected: voice, options: [{ provider: 'openai', label: 'OpenAI', available: true, reason: null },
                                      { provider: 'xai', label: 'Grok', available: true, reason: null }] },
  model: voice === 'xai' ? { text: 'Grok 4.7 · API' } : { text: 'GPT-6 Astra · Plan' },
  last_answer: '', ...extra });

const ok = (body) => ({ ok: true, status: 200, body });

const seen = {};
for (const partner of ['mc', 'cos']) {
  const r = rig([ok(view(partner, 'openai')), ok(view(partner, 'xai'))]);
  await r.menu.load();
  assert.equal(r.calls[0].url, '/x/partner-settings?conversation=c-1');
  assert.equal(r.parts['[data-partner-voice-row]'].hidden, false);
  assert.equal(r.parts['[data-partner-model]'].textContent, 'GPT-6 Astra · Plan');
  assert.equal(r.parts['[data-partner-last]'].hidden, true);
  r.select.value = 'xai';
  await r.select.fire('change');
  assert.deepEqual(r.calls[1], { method: 'POST', url: '/x/partner-settings',
    body: { voice: 'xai', revision: 0, conversation: 'c-1', idempotency_key: 'pm-key-0001' } });   // nothing else is sent
  assert.equal(r.parts['[data-partner-model]'].textContent, 'Grok 4.7 · API');                  // Model follows Voice
  assert.equal(r.parts['[data-partner-note]'].textContent, 'Applies from your next message and your next call.');
  assert.deepEqual(r.voices, ['openai', 'xai']);
  seen[partner] = [r.options.map((o) => o.textContent), r.parts['[data-partner-model]'].textContent];
}
assert.deepEqual(seen.mc, seen.cos);                                                            // the twins say the same

{ // an unavailable voice is off, with its reason; the Last-answer line shows in plain words
  const r = rig([ok(view('cos', 'openai', { last_answer: 'Last answer: Claude Opus 5.5 (API) (fallback)',
    voice: { selected: 'openai', options: [{ provider: 'openai', label: 'OpenAI', available: true },
      { provider: 'xai', label: 'Grok', available: false, reason: 'Grok model not set up for CoS here' }] } }))]);
  await r.menu.load();
  assert.equal(r.options[1].disabled, true);
  assert.equal(r.options[1].textContent, 'Grok · not available here');
  assert.equal(r.options[1].title, 'Grok model not set up for CoS here');
  assert.equal(r.parts['[data-partner-last]'].textContent, 'Last answer: Claude Opus 5.5 (API) (fallback)');
  assert.equal(r.parts['[data-partner-last]'].hidden, false);
  assert.ok(!/plan-|api-|gateway/.test(r.options.map((o) => o.textContent).join(' ')));      // no route ids
}

{ // a stale page: the server's words, the old choice back, then a fresh read
  const r = rig([ok(view('mc', 'openai')),
    { ok: false, status: 409, body: { error: 'settings_changed', message: 'The voice changed on another device. Reload to see it.' } },
    ok(view('mc', 'xai'))]);
  await r.menu.load();
  r.select.value = 'xai';
  await r.select.fire('change');
  assert.equal(r.calls[2].method, 'GET');
  assert.equal(r.select.value, 'xai');                                                         // the other device's choice
}

{ // a live call keeps its provider: a reload never re-enables the choice
  const r = rig([ok(view('cos', 'openai')), ok(view('cos', 'openai'))]);
  await r.menu.load();
  r.select.dataset.callLive = 'true';
  r.select.disabled = true;
  await r.menu.load();
  assert.equal(r.select.disabled, true);
}

{ // unreadable: a plain line, nothing invented
  const r = rig([{ ok: false, status: 503, body: {} }]);
  await r.menu.load();
  assert.equal(r.parts['[data-partner-note]'].textContent, 'Voice and model settings could not be read.');
  assert.equal(r.parts['[data-partner-voice-row]'].hidden, true);
}

console.log('7 partner-menu checks passed for MC and CoS');
