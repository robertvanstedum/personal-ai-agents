// The MC Voice choice (owner, 18 Oct): offer only what the capabilities answer says this host can run, label the rest truthfully,
// restore a remembered choice only while it is still offered, and never fake a choice when nothing can run. No DOM, network or mic.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source = fs.readFileSync(new URL('../minimoi_portal/guild_ui/static/js/mc_voice.js', import.meta.url), 'utf8');
const start = source.indexOf("  const PROVIDER_KEY = 'minimoi.voice.provider';");
const end = source.indexOf('  async function available() {');
assert.ok(start > 0 && end > start, 'offerProviders block present');
const block = source.slice(start, end);

function run({ providers = [], managed = null, saved = null, storageThrows = false, model_actions = undefined } = {}) {
  const option = (value, text) => ({ value, textContent: text, disabled: false, dataset: {} });
  const select = { options: [option('openai', 'OpenAI'), option('xai', 'Grok (xAI) · not available yet')], value: 'openai', disabled: false };
  const store = { getItem() { if (storageThrows) throw new Error('blocked'); return saved; } };
  const sandbox = { $: (s) => (s === '[data-mc-voice-provider]' ? select : null), managed, window: { localStorage: store } };
  vm.createContext(sandbox);
  vm.runInContext(`${block}\nofferProviders(${JSON.stringify({ providers, model_actions })});`, sandbox);
  return select;
}
const label = (s, v) => s.options.find((o) => o.value === v).textContent;
const off = (s, v) => s.options.find((o) => o.value === v).disabled;

let s = run({ providers: [{ provider: 'openai' }] });
assert.equal(s.value, 'openai'); assert.equal(off(s, 'openai'), false); assert.equal(off(s, 'xai'), true);
assert.equal(label(s, 'xai'), 'Grok (xAI) · not available yet'); assert.equal(label(s, 'openai'), 'OpenAI');

s = run({ providers: [{ provider: 'openai' }, { provider: 'xai' }], saved: 'xai' });
assert.equal(s.value, 'xai'); assert.equal(off(s, 'xai'), false); assert.equal(label(s, 'xai'), 'Grok (xAI)');   // offered: no "not available"

s = run({ providers: [{ provider: 'openai' }], saved: 'xai' });
assert.equal(s.value, 'openai');                                                       // a remembered choice that is no longer offered

s = run({ providers: [], managed: { provider: 'openai' } });
assert.equal(s.value, 'openai'); assert.equal(off(s, 'openai'), false);              // the managed session's own provider counts

s = run({ providers: [] });
assert.equal(s.disabled, true); assert.ok(s.options.every((o) => o.disabled));       // nothing runnable: no choice is faked

s = run({ providers: [{ provider: 'openai' }], storageThrows: true });
assert.equal(s.value, 'openai');                                                       // blocked storage: works, just not remembered

// The C2 binding pass (Spec 165 §7 as amended): a provider offered on which MC's voice may not act by itself says so; one
// that may, or an answer that names none (the managed path), reads as before.
s = run({ providers: [{ provider: 'openai' }, { provider: 'xai' }], model_actions: { openai: true, xai: false } });
assert.equal(label(s, 'xai'), 'Grok (xAI) · acts only through Check with MC'); assert.equal(off(s, 'xai'), false);
assert.equal(label(s, 'openai'), 'OpenAI');
s = run({ providers: [{ provider: 'openai' }], model_actions: { openai: true, xai: false } });
assert.equal(label(s, 'xai'), 'Grok (xAI) · not available yet');                       // not offered: unavailable first

console.log('8 voice-provider choice scenarios passed; no DOM, network or microphone.');
