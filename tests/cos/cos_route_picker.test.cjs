const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../../domains/cos/static/cos-route-picker.js'), 'utf8');
const view = (route, revision, last) => ({selected_route_id: route, revision, selectable: true, last_turn: last || null,
  routes: ['a', 'b'].map(r => ({route_id: r, tier_letter: r, label: r, billing_label: 'Plan', available: true, reason: null}))});
function rig() {
  const handlers = {}, doc = {}, calls = [];
  const select = {value: '', disabled: false, replaceChildren() {}, addEventListener: (k, f) => { handlers.change = f; }};
  const control = {hidden: true}, note = {textContent: ''};
  const nodes = {'cos-route-control': control, 'cos-route': select, 'cos-route-note': note};
  const fetch = (url, opts = {}) => new Promise((resolve, reject) => calls.push({url, opts, resolve, reject}));
  vm.runInNewContext(code, {location: {pathname: '/app/cos/ui/confer'}, fetch, encodeURIComponent, JSON, cosWriteHeaders: () => ({'X-CSRF-Token': 'tok'}),
    document: {getElementById: id => nodes[id], createElement: () => ({}), addEventListener: (k, f) => { doc[k] = f; }}});
  const reply = (call, body, ok = true) => call.resolve({ok, json: async () => body});
  return {handlers, doc, calls, select, control, note, reply, to: id => doc['cos:conversation']({detail: {id}})};
}
const tick = () => new Promise(r => setImmediate(r));
(async () => {
  { // the write carries the portal's CSRF header and the shown revision
    const r = rig(); r.to('A'); r.reply(r.calls[0], view('a', 3)); await tick();
    assert.equal(r.select.value, 'a'); assert.equal(r.control.hidden, false);
    r.select.value = 'b'; const done = r.handlers.change(); await tick();
    const post = r.calls[1];
    assert.equal(post.opts.method, 'POST'); assert.equal(post.opts.headers['X-CSRF-Token'], 'tok');
    assert.deepEqual(JSON.parse(post.opts.body), {route_id: 'b', revision: 3});
    r.reply(post, view('b', 4)); await done;
    assert.equal(r.note.textContent, 'Applies from your next message.'); assert.equal(r.select.disabled, false);
  }
  { // a slow first read for A cannot overwrite B
    const r = rig(); r.to('A'); r.to('B');
    r.reply(r.calls[1], view('b', 7)); await tick();
    r.reply(r.calls[0], view('a', 1)); await tick();
    assert.equal(r.select.value, 'b');
    r.select.value = 'a'; const done = r.handlers.change(); await tick();
    assert.match(r.calls[2].url, /B\/route$/); assert.equal(JSON.parse(r.calls[2].opts.body).revision, 7);
    r.reply(r.calls[2], view('a', 8)); await done;
  }
  { // A's slow POST answer, success or failure, leaves B alone and never moves the picker back to A
    for (const ok of [true, false]) {
      const r = rig(); r.to('A'); r.reply(r.calls[0], view('a', 1)); await tick();
      r.select.value = 'b'; const done = r.handlers.change(); await tick();
      r.to('B'); r.reply(r.calls[2], view('a', 9)); await tick();
      r.reply(r.calls[1], ok ? view('b', 2) : {message: 'nope'}, ok); await done; await tick();
      assert.equal(r.calls.length, 3, 'no reload of A');
      assert.equal(r.select.value, 'a'); assert.equal(r.select.disabled, false);
      r.select.value = 'b'; const again = r.handlers.change(); await tick();
      assert.match(r.calls[3].url, /B\/route$/); assert.equal(JSON.parse(r.calls[3].opts.body).revision, 9);
      r.reply(r.calls[3], view('b', 10)); await again;
    }
  }
  { // a refused choice says why and shows the true state again
    const r = rig(); r.to('A'); r.reply(r.calls[0], view('a', 1)); await tick();
    r.select.value = 'b'; const done = r.handlers.change(); await tick();
    r.reply(r.calls[1], {message: 'Sign-in expired'}, false); await tick();
    r.reply(r.calls[2], view('a', 1)); await done;
    assert.equal(r.note.textContent, 'Sign-in expired'); assert.equal(r.select.value, 'a');
  }
  { // a finished turn refreshes the receipt, only for the chat on show
    const r = rig(); r.to('A'); r.reply(r.calls[0], view('a', 1)); await tick();
    r.doc['cos:turns-changed']({detail: {id: 'other'}}); assert.equal(r.calls.length, 1);
    r.doc['cos:turns-changed']({detail: {id: 'A'}});
    r.reply(r.calls[1], view('a', 1, {served: {provider: 'openai', model: 'gpt-6-astra'}})); await tick();
    assert.equal(r.note.textContent, 'Last turn: served by openai/gpt-6-astra');
    r.doc['cos:turns-changed']({detail: {id: 'A'}});
    r.reply(r.calls[2], view('a', 1, {served: null})); await tick();
    assert.equal(r.note.textContent, 'Last turn: no evidence of who served it');
  }
  console.log('picker ok');
})().catch(e => { console.error(e); process.exit(1); });
