// CoS waiting messages (Robert, 11 Nov 2026: CoS should behave like MC while it works).
// While CoS works, a sent message joins a line kept on the server; the page sends the line in order once CoS is free,
// through the ordinary send path, and a failed send stops the line rather than skipping ahead.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const template = fs.readFileSync('domains/cos/templates/cos_ui.html', 'utf8');
const source = template.slice(template.indexOf('async function sendMessage()'), template.indexOf('// ── Files from CoS'));

function el() {
  return { children: [], hidden: false, textContent: '', className: '', title: '', disabled: false,
    append(...k) { this.children.push(...k); }, replaceChildren(...k) { this.children = k; }, remove() {},
    setAttribute() {}, querySelector() { return el(); } };
}

function harness({ failSend = false } = {}) {
  const server = { queue: [], sends: [] };
  const queueBox = el();
  let n = 0;
  const ctx = {
    console, uploading: 0, attached: [], renderTray() {}, btnAttach: { disabled: false },
    sendInput: { value: '', disabled: false, readOnly: false, focus() {} },
    appendMsg() {}, appendThinking: () => el(), loadCosFiles() {},
    cosWriteHeaders: () => ({}), CustomEvent: class {},
    crypto: { randomUUID: () => `00000000-0000-4000-8000-${String(++n).padStart(12, '0')}` },
    document: { getElementById: () => queueBox, createElement: () => el(), addEventListener() {}, dispatchEvent() {} },
    runTurn: async (payload) => {
      server.sends.push(payload.text);
      server.queue = server.queue.filter(q => q.request_id !== payload.request_id);   // ui_send drops it from the line
      return failSend ? { resp: { ok: false, status: 502 }, data: {} }
        : { resp: { ok: true, status: 200 }, data: { reply: 'ok: ' + payload.text, history: { saved: true } } };
    },
    fetch: async (url, init = {}) => {
      const body = init.body ? JSON.parse(init.body) : null;
      if (url.startsWith('/ui/queue?')) return { ok: true, json: async () => ({ items: server.queue }) };
      if (url === '/ui/queue') {
        if (server.queue.length >= 5) return { ok: false, json: async () => ({ error: 'queue_full' }) };
        server.queue.push({ ...body });
        return { ok: true, json: async () => ({ outcome: 'queued', items: server.queue }) };
      }
      if (url === '/ui/queue/cancel') { server.queue = server.queue.filter(q => q.request_id !== body.request_id); return { ok: true, json: async () => ({ removed: true }) }; }
      throw new Error('unexpected ' + url);
    },
  };
  ctx.window = { MiniMoiNotices: { show() {}, clear() {} }, cosVoiceTyped() {},
    cosHistory: { ready: true, conversationId: 'cosc-aaaaaaaaaaaa', pendingTurn: false, device: 'd',
      requestId: () => '11111111-1111-4111-8111-111111111111', adopt: (id) => id,
      persistDraft() {}, confirm() {}, uncertain() {} } };
  vm.createContext(ctx);
  vm.runInContext(source + '\nthis.api={sendMessage,drainQueue};', ctx);
  return { ctx, server, queueBox };
}
const item = (id, text) => ({ request_id: `${id}-0000-4000-8000-000000000000`, conversation_id: 'cosc-aaaaaaaaaaaa', text, attachments: [] });

(async () => {
  // A message sent while CoS works joins the line, and the box is free again at once.
  const h = harness();
  h.ctx.window.cosTurnRunning = true;
  for (const t of ['second', 'third']) { h.ctx.sendInput.value = t; await h.ctx.api.sendMessage(); }
  assert.deepEqual(h.server.queue.map(q => q.text), ['second', 'third']);
  assert.equal(h.ctx.sendInput.value, '');
  assert.equal(h.server.sends.length, 0);
  assert.equal(h.queueBox.hidden, false);

  // Once CoS is free, the line is sent in order and empties.
  h.ctx.window.cosTurnRunning = false;
  await h.ctx.api.drainQueue();
  assert.deepEqual(h.server.sends, ['second', 'third']);
  assert.deepEqual(h.server.queue, []);
  assert.equal(h.queueBox.hidden, true);

  // While others wait, a new message joins behind them instead of jumping the line.
  const j = harness();
  j.server.queue.push(item('22222222', 'waiting'));
  await j.ctx.api.drainQueue();
  j.server.queue.push(item('55555555', 'older'));
  j.ctx.window.cosTurnRunning = false;
  await j.ctx.api.drainQueue();
  assert.deepEqual(j.server.sends, ['waiting', 'older']);

  // A turn still running from before a reload holds the line too.
  const r = harness();
  r.ctx.window.cosHistory.pendingTurn = true;
  r.ctx.sendInput.value = 'after reload';
  await r.ctx.api.sendMessage();
  assert.deepEqual(r.server.queue.map(q => q.text), ['after reload']);
  await r.ctx.api.drainQueue();
  assert.equal(r.server.sends.length, 0);

  // A failed send stops the line: the next message is not sent past it, and its words come back to the box.
  const f = harness({ failSend: true });
  f.server.queue.push(item('33333333', 'one'), item('44444444', 'two'));
  await f.ctx.api.drainQueue();
  assert.deepEqual(f.server.sends, ['one']);
  assert.equal(f.ctx.sendInput.value, 'one');

  // Five waiting is the limit; the sixth stays in the box.
  const full = harness();
  full.ctx.window.cosTurnRunning = true;
  for (const t of ['1', '2', '3', '4', '5', '6']) { full.ctx.sendInput.value = t; await full.ctx.api.sendMessage(); }
  assert.equal(full.server.queue.length, 5);
  assert.equal(full.ctx.sendInput.value, '6');
  console.log('CoS queue: joins the line while busy, sends in order, holds for a running turn, stops on failure');
})().catch(e => { console.error(e); process.exitCode = 1; });
