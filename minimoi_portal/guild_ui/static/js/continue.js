// Continue (S6, decision 5): the work item you were on, kept on the server per
// principal and the same on every front end. Opening an item on the record
// sets it; off the record, or while this tab's record mode is unknown,
// nothing is sent.
import { $, $$, el } from './dom.js';
import { apiPut, recordMode } from './api.js';
import { live } from './state.js';
import { newKey } from './actions.js';

export function renderContinue(zone) {
  for (const slot of $$('[data-continue]')) {
    slot.dataset.continueState = zone.state;
    delete slot.dataset.stale;
    if (zone.target) slot.replaceChildren(el('a', { href: zone.target.href, 'data-continue-link': true }, zone.text));
    else slot.replaceChildren(el('span', { 'data-continue-text': true }, zone.text));
  }
  // The Shop floor's context rail shows Continue only when there is somewhere to continue to.
  // (A conversation's own work item, when linked, keeps its line.)
  for (const rail of $$('[data-rail-continue]')) rail.hidden = !rail.querySelector('[data-focus-link]') && zone.state === 'ok' && !zone.target;
}

export async function continueFromItem(page) {
  // Automatic, so only when this tab is known to be on the record.
  if (page.page !== 'item' || !page.item_id || !$('.item-grid') || live.off || !live.known) return;
  const r = await apiPut('/continue', { kind: 'item', ref: page.item_id, idempotency_key: newKey(), record_mode: recordMode() });
  if (r.ok && r.body && r.body.result === 'set') renderContinue({ state: 'ok', target: r.body.continue, text: r.body.text });
}
