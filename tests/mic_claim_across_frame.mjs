// FF-04 (#526): one voice at a time with CoS and MC side by side. The real frame relay from mc_voice.js (MC's pane) and the
// real parent bridge from cos-history-ui.js (the CoS page), on two fake windows joined by a postMessage model that keeps the
// browser's rules: a message reaches a window only when targetOrigin is that window's origin, it arrives later (never in the
// sender's own call), and the receiver sees the sender's window as event.source and its origin as event.origin. MC Voice
// and Dictate in the pane are stood in for by their guild:mic-claim rule (yield to any claim not their own), as in
// mc_voice.js and dictation.js. No browser, microphone, provider or network.
import fs from 'node:fs';
import assert from 'node:assert/strict';

const ROOT = new URL('../', import.meta.url);
const MC = fs.readFileSync(new URL('minimoi_portal/guild_ui/static/js/mc_voice.js', ROOT), 'utf8');
const COS = fs.readFileSync(new URL('domains/cos/static/cos-history-ui.js', ROOT), 'utf8');
const relaySource = MC.slice(MC.indexOf('const MIC_CLAIM_MESSAGE'), MC.indexOf('export function initMcVoice'));
const bridgeSource = COS.slice(COS.indexOf('// ── One voice at a time beside MC (FF-04'));
assert.ok(relaySource.includes('function relayMicClaimAcrossFrame') && bridgeSource.includes('window.cosClaimMic'), 'the FF-04 code moved');
// The pane's own consumers keep the rule this relies on: each yields to any claim that is not its own.
assert.ok(MC.includes("document.addEventListener('guild:mic-claim', (e) => { if (e.detail?.who !== 'voice' && busy()) stop("));
assert.ok(fs.readFileSync(new URL('minimoi_portal/guild_ui/static/js/dictation.js', ROOT), 'utf8')
  .includes("document.addEventListener('guild:mic-claim', (e) => { if (e.detail?.who !== 'dictation' && busy()) stop("));
assert.ok(MC.includes('export function initMcVoice(page) {\n  relayMicClaimAcrossFrame();'), 'the relay is not installed first, before MC Voice may return early');

const ORIGIN = 'https://minimoi.example';
const settle = () => new Promise((r) => setImmediate(r));
const posts = [];

function makeWindow(origin) {
  const et = new EventTarget(), doc = new EventTarget(), listened = { message: 0 };
  return { location: { origin }, document: doc, listened,
    addEventListener(k, f) { listened[k] = (listened[k] || 0) + 1; et.addEventListener(k, f); },
    dispatchEvent: (e) => et.dispatchEvent(e) };
}
// A WindowProxy: the same object across navigations of the window it stands for, as frame.contentWindow and window.parent are.
function proxy(current, from) {
  return { postMessage(data, targetOrigin) {
    const sender = from(), target = current();
    posts.push({ to: target, data, targetOrigin });
    if (!target || targetOrigin !== target.location.origin) return;           // the browser drops it
    const copy = structuredClone(data), origin = sender.win.location.origin, source = sender.proxy;
    setImmediate(() => { const e = new Event('message'); Object.assign(e, { data: copy, origin, source }); target.dispatchEvent(e); });
  } };
}
function relay(win) { return new Function('window', 'document', 'CustomEvent', `${relaySource}\nreturn relayMicClaimAcrossFrame(window, document);`)(win, win.document, CustomEvent); }
function bridge(win, frame) {
  const doc = win.document;
  doc.getElementById = (id) => (id === 'mc-both' ? frame : null);
  new Function('window', 'document', 'location', 'CustomEvent', bridgeSource)(win, doc, win.location, CustomEvent);
}

// The CoS page with MC in #mc-both. Either side can be reloaded (a new window object behind the same proxy).
function sideBySide({ parentOrigin = ORIGIN, paneLoaded = true } = {}) {
  const s = { cosClaims: 0 };
  s.parentProxy = proxy(() => s.parent, () => ({ win: s.pane, proxy: s.paneProxy }));
  s.paneProxy = proxy(() => s.pane, () => ({ win: s.parent, proxy: s.parentProxy }));
  s.frame = { contentWindow: s.paneProxy, getAttribute: (k) => (k === 'src' && paneLoaded ? '/guild-next/guild/companion/cos' : null) };
  s.loadParent = () => {
    s.parent = makeWindow(parentOrigin); s.cosClaims = 0;
    s.parent.document.addEventListener('cos:companion-mic-claim', () => { s.cosClaims += 1; });
    bridge(s.parent, s.frame);
  };
  s.loadPane = () => {
    s.pane = makeWindow(ORIGIN); s.pane.parent = s.parentProxy;
    s.mc = { voice: false, dictation: false, stops: [] };
    s.pane.document.addEventListener('guild:mic-claim', (e) => { if (e.detail?.who !== 'voice' && s.mc.voice) { s.mc.voice = false; s.mc.stops.push('voice'); } });
    s.pane.document.addEventListener('guild:mic-claim', (e) => { if (e.detail?.who !== 'dictation' && s.mc.dictation) { s.mc.dictation = false; s.mc.stops.push('dictation'); } });
    s.installed = relay(s.pane);
  };
  // What mc_voice.js start() and dictation.js start() do first.
  s.mcStarts = (who) => { s.pane.document.dispatchEvent(new CustomEvent('guild:mic-claim', { detail: { who } })); s.mc[who] = true; };
  s.loadParent(); s.loadPane();
  return s;
}
const fromOutside = (win, { origin = ORIGIN, source = {}, data = { type: 'minimoi:mic-claim' } } = {}) => {
  const e = new Event('message'); Object.assign(e, { data, origin, source }); win.dispatchEvent(e);
};

let cases = 0;
const failures = [];
async function check(name, fn) { cases++; posts.length = 0; try { await fn(); } catch (e) { failures.push(`${name}: ${String(e.message).split('\n').slice(0, 4).join(' | ')}`); } }

await check('CoS on, then MC on: MC Voice starting in the pane tells the CoS page once, with nothing but the claim, to its own origin only', async () => {
  const s = sideBySide();
  assert.equal(s.installed, true);
  s.mcStarts('voice');
  assert.equal(s.cosClaims, 0, 'delivered within the sender\'s own call');
  await settle();
  assert.equal(s.cosClaims, 1);
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0].data, { type: 'minimoi:mic-claim' });
  assert.equal(posts[0].targetOrigin, ORIGIN);
});
await check('Dictate in the pane while CoS voice runs: the CoS page is told too', async () => {
  const s = sideBySide();
  s.mcStarts('dictation'); await settle();
  assert.equal(s.cosClaims, 1);
});
await check('MC on, then CoS on: CoS starting posts a claim into the pane; MC Voice and Dictate there yield; nothing echoes back', async () => {
  const s = sideBySide();
  s.mc.voice = true; s.mc.dictation = true;
  s.parent.cosClaimMic(); await settle(); await settle();
  assert.deepEqual(s.mc.stops.sort(), ['dictation', 'voice']);
  assert.equal(s.cosClaims, 0, 'the pane\'s yield was sent back as a claim');
  assert.deepEqual(posts.map((p) => [p.data, p.targetOrigin]), [[{ type: 'minimoi:mic-claim' }, ORIGIN]]);
});
await check('the pane closing (companion-closed) and the host\'s own claim stay in the pane', async () => {
  const s = sideBySide();
  s.pane.document.dispatchEvent(new CustomEvent('guild:mic-claim', { detail: { who: 'companion-closed' } }));
  s.pane.document.dispatchEvent(new CustomEvent('guild:mic-claim', { detail: { who: 'host' } }));
  await settle();
  assert.equal(posts.length, 0);
  assert.equal(s.cosClaims, 0);
});
await check('the CoS page ignores a claim from any other window, origin or shape', async () => {
  const s = sideBySide();
  fromOutside(s.parent, { source: s.paneProxy, origin: 'https://elsewhere.example' });
  fromOutside(s.parent, { source: {} });                                          // another window at this origin
  fromOutside(s.parent, { source: null });
  for (const data of ['minimoi:mic-claim', null, { type: 'guild:mic-claim' }, { kind: 'minimoi:mic-claim' }]) fromOutside(s.parent, { source: s.paneProxy, data });
  await settle();
  assert.equal(s.cosClaims, 0);
  fromOutside(s.parent, { source: s.paneProxy });                                  // the pane itself, rightly: heard
  assert.equal(s.cosClaims, 1);
});
await check('the pane ignores a claim from any window but its same-origin parent, or of another shape', async () => {
  const s = sideBySide();
  s.mc.voice = true; s.mc.dictation = true;
  fromOutside(s.pane, { source: s.parentProxy, origin: 'https://elsewhere.example' });
  fromOutside(s.pane, { source: {} });
  for (const data of ['minimoi:mic-claim', null, { type: 'other' }]) fromOutside(s.pane, { source: s.parentProxy, data });
  assert.deepEqual(s.mc.stops, []);
  fromOutside(s.pane, { source: s.parentProxy });
  assert.deepEqual(s.mc.stops.sort(), ['dictation', 'voice']);
});
await check('MC framed by a page of another origin: the pane\'s claim is never delivered there, and that page\'s claim is not heard', async () => {
  const s = sideBySide({ parentOrigin: 'https://elsewhere.example' });
  s.mcStarts('voice'); await settle();
  assert.equal(posts[0].targetOrigin, ORIGIN);
  assert.equal(s.cosClaims, 0);
  s.mc.voice = true;
  s.parent.cosClaimMic(); await settle();                                          // its own origin as target: dropped
  fromOutside(s.pane, { source: s.parentProxy, origin: 'https://elsewhere.example' });
  assert.equal(s.mc.voice, true);
});
await check('MC not in a frame (its own page), or with an opaque origin: no relay, no listener', async () => {
  const win = makeWindow(ORIGIN); win.parent = win;
  assert.equal(relay(win), false);
  assert.equal(win.listened.message, 0);
  const opaque = makeWindow('null'); opaque.parent = {};
  assert.equal(relay(opaque), false);
  assert.equal(opaque.listened.message, 0);
});
await check('installed once per page: a second call adds nothing, one claim is one message', async () => {
  const s = sideBySide();
  assert.equal(relay(s.pane), false);
  s.mcStarts('voice'); await settle();
  assert.equal(posts.length, 1);
  assert.equal(s.cosClaims, 1);
});
await check('a pane never opened is not told (it has no microphone)', async () => {
  const s = sideBySide({ paneLoaded: false });
  s.parent.cosClaimMic(); await settle();
  assert.equal(posts.length, 0);
});
await check('one side reloading: the reloaded pane and the reloaded CoS page each take part again', async () => {
  const s = sideBySide();
  s.loadPane();                                                                    // the pane reloads (MC's pagehide already ended its voice)
  s.mcStarts('voice'); await settle();
  assert.equal(s.cosClaims, 1);
  s.parent.cosClaimMic(); await settle();
  assert.deepEqual(s.mc.stops, ['voice']);
  s.loadParent();                                                                  // the CoS page reloads; the pane in it is fresh too in a browser,
  s.loadPane();                                                                    // here kept apart to show each half re-arms on its own
  s.mcStarts('dictation'); await settle();
  assert.equal(s.cosClaims, 1);
  s.parent.cosClaimMic(); await settle();
  assert.deepEqual(s.mc.stops, ['dictation']);
});

if (failures.length) { console.log(`${failures.length} of ${cases} mic-claim frame scenarios FAILED:\n  ${failures.join('\n  ')}`); process.exit(1); }
console.log(`${cases} mic-claim frame scenarios passed (both directions, Dictate, origin/source/shape checks, no echo, not framed, reloads); fake windows only.`);
