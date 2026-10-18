// The MC "..." options menu (owner, 18 Oct; Codex review): Escape or a click elsewhere closes it and its Escape never reaches the
// panel (which would minimize it and end voice). Only active dictation holds it open, against Escape, outside clicks, its own
// summary and any other close, so Stop dictation stays reachable. A pressed Private lock never holds it. Runs the real
// wireOptionsMenu from conversation.js on stand-in elements: no DOM, network or microphone.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source = fs.readFileSync(new URL('../minimoi_portal/guild_ui/static/js/conversation.js', import.meta.url), 'utf8');
const start = source.indexOf('function wireOptionsMenu(options, doc) {');
const end = source.indexOf('\n}\n', start);
assert.ok(start > 0 && end > start, 'wireOptionsMenu present in conversation.js');
const fn = source.slice(start, end + 2);

function rig({ dictating = false, privateOn = false, open = true } = {}) {
  const on = (store) => ({ addEventListener(kind, f) { (store[kind] ??= []).push(f); } });
  const fire = (store, kind, event) => (store[kind] || []).forEach((f) => f(event));
  const state = { dictating, privateOn };
  const optionsL = {}, summaryL = {}, docL = {};
  const summary = { focused: 0, focus() { this.focused++; }, ...on(summaryL) };
  const options = {
    _open: open,
    get open() { return this._open; },
    set open(v) { const was = this._open; this._open = v; if (was !== v) fire(optionsL, 'toggle', {}); },
    contains: (t) => Boolean(t && t.inside),
    querySelector(sel) {
      if (sel === 'summary') return summary;
      if (sel === '[data-dictate][aria-pressed="true"]') return state.dictating ? {} : null;
      if (sel === '[aria-pressed="true"]') return state.dictating || state.privateOn ? {} : null;   // what a too-broad check would see
      return null;
    },
    ...on(optionsL),
  };
  const doc = on(docL);
  const sandbox = { options, doc };
  vm.createContext(sandbox);
  vm.runInContext(`${fn}\nwireOptionsMenu(options, doc);`, sandbox);
  const event = (extra = {}) => ({ stopped: false, prevented: false, stopPropagation() { this.stopped = true; },
    preventDefault() { this.prevented = true; }, ...extra });
  return {
    options, summary, state,
    escape() { const e = event({ key: 'Escape' }); fire(optionsL, 'keydown', e); return e; },
    outside() { const e = event({ target: { inside: false } }); fire(docL, 'click', e); return e; },
    inside() { const e = event({ target: { inside: true } }); fire(docL, 'click', e); return e; },
    summaryClick() { const e = event(); fire(summaryL, 'click', e); return e; },
  };
}

// 1. A pressed Private lock never holds the menu open (Codex finding 1).
{ const r = rig({ privateOn: true }); const e = r.escape();
  assert.equal(r.options.open, false); assert.equal(e.stopped, true); assert.equal(r.summary.focused, 1); }
{ const r = rig({ privateOn: true }); r.outside(); assert.equal(r.options.open, false); }

// 2. Escape closes, stops at the menu (never the panel's Escape), and returns focus to the summary.
{ const r = rig(); const e = r.escape();
  assert.equal(r.options.open, false); assert.equal(e.stopped, true); assert.equal(e.prevented, true); assert.equal(r.summary.focused, 1); }

// 3. Escape while closed does nothing and lets the key travel (the panel's own Escape still works).
{ const r = rig({ open: false }); const e = r.escape(); assert.equal(e.stopped, false); assert.equal(r.options.open, false); }

// 4. Outside click closes; a click inside keeps it open.
{ const r = rig(); r.inside(); assert.equal(r.options.open, true); r.outside(); assert.equal(r.options.open, false); }

// 5. Active dictation holds it open against Escape, outside clicks, its own summary and any other close (Codex finding 2).
{ const r = rig({ dictating: true });
  const e = r.escape(); assert.equal(r.options.open, true); assert.equal(e.stopped, true);
  r.outside(); assert.equal(r.options.open, true);
  const s = r.summaryClick(); assert.equal(s.prevented, true); assert.equal(r.options.open, true);
  r.options.open = false; assert.equal(r.options.open, true, 'a close by any other route reopens while listening'); }

// 6. Once dictation stops, the summary and Escape close it normally again.
{ const r = rig({ dictating: true }); r.state.dictating = false;
  const s = r.summaryClick(); assert.equal(s.prevented, false);
  r.escape(); assert.equal(r.options.open, false); }

console.log('6 options-menu scenarios passed; no DOM, network or microphone.');
