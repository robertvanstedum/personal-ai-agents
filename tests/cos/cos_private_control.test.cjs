// #510: the CoS page's Private lock, in a fake browser. The real script's own lines run here: the conversation start shared
// with New chat, and the lock, chip and Exit wiring. Synthetic content only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('domains/cos/static/cos-history-ui.js', 'utf8');
const slice = (from, to) => { const a = source.indexOf(from), b = source.indexOf(to, a); assert.ok(a >= 0 && b > a, from); return source.slice(a, b); };
const startSource = slice('  let creatingConversation', "  document.getElementById('mc-page')");
const privateSource = slice('  // ── Private (#510)', "  document.getElementById('both-open')");

// Calls are made inside the vm realm, so compare their JSON.
const same = (actual, expected) => assert.equal(JSON.stringify(actual), JSON.stringify(expected));

function element(extra = {}) {
  const attrs = {}, listeners = {};
  return {disabled: false, hidden: false, title: '', textContent: '', ...extra,
    setAttribute(k, v) { attrs[k] = String(v); }, getAttribute(k) { return attrs[k]; },
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    async click() { if (this.onclick) await this.onclick(); for (const fn of listeners.click || []) await fn(); },
    querySelector(sel) { return sel === '.cos-lock-text' ? this.words : null; }};
}

function harness() {
  const words = element(), lock = element({words}), chip = element({hidden: true}), exit = element(), newChat = element();
  const docListeners = {}, calls = [], body = {dataset: {historyPrivate: 'false'}};
  const document = {
    body,
    querySelector: sel => ({'[data-cos-private]': lock, '[data-private-chip]': chip, '[data-private-exit]': exit})[sel] || null,
    querySelectorAll: sel => sel === '[data-new-conversation]' ? [newChat] : [newChat, lock, exit],
    getElementById: () => ({disabled: false, close() {}, classList: {contains: () => true}, click() {}}),
    addEventListener(type, fn) { (docListeners[type] ||= []).push(fn); },
  };
  const ctx = {document, window: {cosHistory: {ready: true}, cosTurnRunning: false}, input: {disabled: false, value: '', focus() {}},
    uploading: 0, selecting: false, state: {textContent: ''}, persistDraft() {}, closeDrawer() {}, refreshList() {},
    api: async (path, payload) => { calls.push([path, payload]); return {conversation_id: 'cosc-new-' + calls.length, private: payload.private === true}; },
    select: async (id, doc) => { body.dataset.historyPrivate = String(Boolean(doc.private)); for (const fn of docListeners['cos:conversation-selected'] || []) fn(); }};
  vm.createContext(ctx);
  vm.runInContext(startSource + '\n' + privateSource, ctx);
  return {ctx, lock, words, chip, exit, newChat, calls, body};
}

(async () => {
  const h = harness();
  // On the record: the lock offers Go Private and the chip is hidden.
  assert.equal(h.lock.getAttribute('aria-pressed'), 'false');
  assert.equal(h.lock.getAttribute('aria-label'), 'Go Private');
  assert.equal(h.words.textContent, 'Go Private');
  assert.equal(h.chip.hidden, true);

  // The lock creates a new private conversation through the existing create API.
  await h.lock.click();
  same(h.calls[0], ['history/conversations', {title: 'New conversation', private: true}]);
  assert.equal(h.body.dataset.historyPrivate, 'true');
  assert.equal(h.chip.hidden, false);
  assert.equal(h.lock.getAttribute('aria-pressed'), 'true');
  assert.equal(h.lock.getAttribute('aria-label'), 'Exit Private');
  assert.match(h.lock.title, /^Exit Private: go back on the record/);

  // In a private chat the lock goes back on the record: a new, normal conversation.
  await h.lock.click();
  same(h.calls[1], ['history/conversations', {title: 'New conversation'}]);
  assert.equal(h.body.dataset.historyPrivate, 'false');
  assert.equal(h.chip.hidden, true);

  // The chip's Exit does the same.
  await h.lock.click();
  await h.exit.click();
  same(h.calls[3], ['history/conversations', {title: 'New conversation'}]);
  assert.equal(h.chip.hidden, true);

  // New chat never makes a private conversation.
  await h.newChat.onclick();
  same(h.calls[4], ['history/conversations', {title: 'New conversation'}]);

  // Nothing starts while a message is in flight or the history is not ready.
  const busy = harness();
  busy.ctx.window.cosTurnRunning = true;
  await busy.lock.click();
  assert.equal(busy.calls.length, 0);
  const early = harness();
  early.ctx.window.cosHistory.ready = false;
  await early.lock.click();
  assert.equal(early.calls.length, 0);

  // A refused create leaves the chat as it was, and says so.
  const refused = harness();
  refused.ctx.api = async () => { throw new Error('history_unavailable'); };
  await refused.lock.click();
  assert.equal(refused.body.dataset.historyPrivate, 'false');
  assert.equal(refused.ctx.state.textContent, 'Could not start a private conversation.');
  assert.equal(refused.ctx.window.cosHistory.ready, true);
  console.log('private lock starts, shows and exits private conversations');
})().catch(e => { console.error(e); process.exitCode = 1; });
