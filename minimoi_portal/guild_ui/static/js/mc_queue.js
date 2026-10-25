// Waiting messages (issue #346 P3). While the turn ledger is on, a kept message is queued on the server
// (POST /mc/turns with queue: true) and sent later, strictly in the owner's order, one at a time. This module
// reads the server's view (GET /mc/turns/ledger), shows what waits (a chip on each waiting message with Cancel,
// and one line above the composer), shows a pause with its Continue, and sends the head when nothing is running.
// The server decides everything: two tabs may both try, and the server lets only one send. Words claim only what
// the server's view says: waiting means not sent yet; nothing here says a waiting message changes the current answer.
import { $, el, announce } from './dom.js';
import { apiGet, apiPost } from './api.js';
import { live, onChange } from './state.js';

import { chipWords, notSentWords, barWords, nextDelay } from './mc_queue_words.js';

const MAY_PASS = ['unavailable', 'mc_unavailable', 'mc_turns_off', 'stream_off', 'stream_unsupported', 'ledger_unavailable', 'network'];
const OTHER_SENDER = ['busy', 'not_next', 'not_queued'];
// This tab may not send (Private, record mode unknown) or cannot (signed out): stop, and start again only when that changes.
const may = () => live.known && !live.off;
const QUEUE_ROUNDS = 6;          // at most five wait, so six rounds empty the queue
export function initQueue({ page, thread, dispatch, follow, readBack = async () => true }) {
  const bar = $('[data-mc-queue-bar]');
  const text = $('[data-mc-queue-text]');
  const cont = $('[data-mc-queue-continue]');
  let pumping = false;
  let timer = null;
  let delay = 0;
  let view = null;
  let needsReadback = false;
  const here = () => (page.conversation ? page.conversation.id : undefined);
  const noteRow = (requestId) => thread.querySelector(`[data-note-request="${CSS.escape(requestId)}"]`);

  function later(ms) {
    window.clearTimeout(timer);
    timer = window.setTimeout(pump, ms);
  }

  function chipFor(row) {
    let chip = row.querySelector('[data-queued-chip]');
    if (!chip) {
      chip = el('p', { class: 'mc-queued small', 'data-queued-chip': '' });
      chip.append(el('span', { 'data-queued-text': '' }));
      const cancel = el('button', { type: 'button', class: 'btn-mini', 'data-queued-cancel': '' }, 'Cancel');
      cancel.addEventListener('click', () => cancelEntry(Number(chip.dataset.ledgerId), cancel));
      chip.append(' ', cancel);
      row.append(chip);
    }
    return chip;
  }

  function render(v) {
    view = v;
    const live = new Set();
    for (const e of v.queued) {
      const row = noteRow(e.note_request_id);
      if (!row) continue;
      live.add(row);
      const chip = chipFor(row);
      chip.dataset.ledgerId = String(e.id);
      chip.querySelector('[data-queued-text]').textContent = chipWords(e.position, Boolean(v.running));
    }
    for (const chip of thread.querySelectorAll('[data-queued-chip]')) if (!live.has(chip.closest('li'))) chip.remove();
    for (const e of v.recent || []) {
      const words = notSentWords(e);
      const row = words && noteRow(e.note_request_id);
      if (row && !row.querySelector('[data-not-sent]')) row.append(el('p', { class: 'mc-queued small', 'data-not-sent': e.state }, words));
    }
    const line = barWords(v, here()) || (v.running?.conversation_id === here() && !thread.querySelector('[data-live-turn]')
      ? 'An earlier reply is still running. Its saved answer will appear here.' : '');
    if (bar) {
      bar.hidden = !line;
      bar.dataset.state = v.pause ? 'paused' : v.queued.length ? 'waiting' : 'idle';
      text.textContent = line;
      cont.hidden = !v.pause;
      cont.dataset.ledgerId = v.pause ? String(v.pause.ledger_id) : '';
    }
  }

  async function read() {
    const r = await apiGet('/mc/turns/ledger');
    if (r.status === 401) return { signedOut: true };
    if (!r.ok || !r.body || !Array.isArray(r.body.queued)) return null;
    render(r.body);
    needsReadback = !(await readBack());
    return r.body;
  }

  // Send the head while nothing runs and nothing is paused; after each turn, look again. One loop per tab, and it never
  // spins: every path ends in a bounded wait or a stop. A stop lasts until something changes (record mode, the tab
  // shown again, a new message, Continue or Cancel).
  async function pump() {
    if (pumping) return;
    pumping = true;
    window.clearTimeout(timer);
    try {
      for (let rounds = 0; rounds < QUEUE_ROUNDS; rounds += 1) {
        if (!may()) return;
        // Backgrounding a phone must not strand messages the owner already
        // queued. Only read-only recovery sleeps when the hidden tab is idle.
        if (document.hidden && view && !view.queued.length) return;
        const v = await read();
        if (!v) { delay = nextDelay(delay); later(Math.max(delay, 30000)); return; }   // the view could not be read: look again later
        if (v.signedOut || v.pause) { delay = 0; return; }
        if (!v.queued.length) {
          if (v.running) later(2000);
          else if (needsReadback) { delay = nextDelay(delay); later(delay); }
          else delay = 0;
          return;
        }
        if (!may()) return;                                              // Private or record mode unknown: nothing is sent from this tab
        if (v.running) { later(5000); return; }                          // an earlier message is being answered (here, in another tab or conversation)
        const out = await dispatch(v.queued[0], here());
        const error = out ? out.error : 'unreadable';
        if (!error) { delay = 0; continue; }                             // it went; send the next one
        if (MAY_PASS.includes(error)) { delay = nextDelay(delay); later(delay); await read(); return; }
        if (OTHER_SENDER.includes(error)) { later(3000); return; }
        await read();                                                    // anything else (refused for good, which pauses; or not sendable here): show it and stop
        return;
      }
      later(5000);
    } finally {
      pumping = false;
    }
  }

  async function cancelEntry(ledgerId, button) {
    if (!Number.isInteger(ledgerId) || ledgerId <= 0) return;
    button.disabled = true;
    const r = await apiPost(`/mc/turns/queue/${ledgerId}/cancel`, {});
    announce(r.ok ? 'Cancelled · that message will not be sent.' : (r.body && r.body.message) || 'It could not be cancelled.');
    await read();
    button.disabled = false;
    pump();
  }

  if (cont) cont.addEventListener('click', async () => {
    const ledgerId = Number(cont.dataset.ledgerId);
    if (!Number.isInteger(ledgerId) || ledgerId <= 0) return;
    cont.disabled = true;
    const r = await apiPost('/mc/turns/continue', { ledger_id: ledgerId });
    cont.disabled = false;
    announce(r.ok ? 'Continuing. The earlier message’s outcome is unchanged.' : (r.body && r.body.message) || 'Continue did not go through.');
    pump();
  });
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') pump(); });
  let wasAllowed = may();
  onChange(() => { const now = may(); if (now && !wasAllowed) pump(); wasAllowed = now; });     // back on the record: carry on
  pump();                                                              // on load: what waits, any pause, and send the head if idle
  return {
    queued(body) {                                                     // the server recorded a waiting message (202)
      if (!view) view = { queued: [], recent: [], running: null, pause: null };
      if (follow) follow();
      announce(body.paused ? 'Kept and waiting · sending is paused.' : 'Kept and waiting to send.');
      read().then(() => pump());                                       // shown at once, even while an earlier answer is still arriving
    },
    pump,
    get view() { return view; },
  };
}
