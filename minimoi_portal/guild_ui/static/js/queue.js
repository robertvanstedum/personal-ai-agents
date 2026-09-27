// Queue board and item page: Save sends one request to the server, which
// writes the live queue (lock, unchanged-check, atomic replace, read-back) and
// answers with a receipt or a plain reason. Nothing is kept in the browser.
import { $, $$, el, notice } from './dom.js';
import { saveStatus, markChecked, newKey } from './actions.js';
import { addPlatform } from './conversation.js';
import { refresh } from './floor.js';

const ACTIVE = ['spec_ready', 'in_build'];
const nice = (s) => (s || '').replace('_', ' ');
let page;

function showResult(node, kind, text) {
  node.hidden = false;
  node.dataset.kind = kind;
  node.textContent = text;
}

// A new key when the form opens and whenever the change in it changes; the
// same key for every click and retry of that one change (review F9).
function rotateKey(form) { form.dataset.idemKey = newKey(); }

// After these the change is settled (or the item moved under it), so the next
// Save is a new change. After anything else (network, busy, uncertain, ...)
// a retry reuses the key and gets the first receipt if the write happened.
const SETTLED = ['saved', 'conflict', 'idempotency_mismatch', 'invalid', 'not_found'];

function applyForm(form) {
  const sel = $('[data-status-select]', form);
  const eff = form.dataset.sourceStatus;
  $('[data-save]', form).hidden = sel.value === eff;
  $('[data-note-wrap]', form).hidden = sel.value !== 'blocked';
}

function applyItemToForm(form, item, digest) {
  if (!item) return;
  form.dataset.digest = digest || item.item_digest;
  form.dataset.sourceStatus = item.status || item.raw_status || '';
  const sel = $('[data-status-select]', form);
  if (item.status) sel.value = item.status;
  applyForm(form);
  const eff = $('[data-effective-status]');
  if (eff && $('.item-grid')) eff.textContent = item.status_label || nice(item.status);
}

function moveCard(form, item) {
  const card = form.closest('[data-queue-card]');
  if (!card || !item) return;
  const box = $('[data-queue-moved]');
  if (ACTIVE.includes(item.status)) {
    const col = $(`[data-col="${item.status}"] [data-col-cards]`);
    if (col && card.parentElement !== col) col.append(card);
  } else {
    card.hidden = true;
    box.hidden = false;
    box.append(el('span', {}, `#${item.id} moved to ${nice(item.status)} · `),
      el('a', { href: page.urls.item.replace('__ID__', item.id) }, 'Open item'), el('br'));
  }
  let total = 0;
  for (const col of $$('[data-col]')) {
    const n = $$('[data-queue-card]', col).filter((c) => !c.hidden).length;
    total += n;
    $('[data-col-count]', col).textContent = String(n);
    $('[data-col-empty]', col).hidden = n > 0;
  }
  const count = $('[data-active-count]');
  if (count) count.textContent = String(total);
}

async function onSubmit(form) {
  const sel = $('[data-status-select]', form);
  const note = $('[data-note]', form).value;
  const button = $('[data-save]', form);
  const out = $('[data-save-result]', form);
  if (form.dataset.saving === 'true') return;   // a second click while the first is in flight
  form.dataset.saving = 'true';
  button.disabled = true;
  const res = await saveStatus(Number(form.dataset.itemId), sel.value, note, form.dataset.digest, form.dataset.idemKey);
  button.disabled = false;
  form.dataset.saving = 'false';
  if (SETTLED.includes(res.code)) rotateKey(form);
  showResult(out, res.kind, res.message || 'Nothing was saved');
  if (res.code === 'saved') {
    applyItemToForm(form, res.item, res.item_digest);
    moveCard(form, res.item);
    addPlatform('Guild platform · Save', res.message);
  } else if (res.code === 'conflict' && res.item) {
    applyItemToForm(form, res.item, res.item_digest);
  }
  refresh();
}

export function initQueue(p) {
  page = p;
  for (const form of $$('[data-status-form]')) {
    const sel = $('[data-status-select]', form);
    rotateKey(form);
    sel.addEventListener('change', () => { rotateKey(form); applyForm(form); });
    $('[data-note]', form).addEventListener('input', () => rotateKey(form));
    form.addEventListener('submit', (e) => { e.preventDefault(); onSubmit(form); });
    applyForm(form);
  }
  for (const b of $$('[data-mark-checked]')) {
    b.addEventListener('click', async () => {
      b.disabled = true;
      const res = await markChecked(b.dataset.markChecked);
      const out = b.parentElement.querySelector('[data-check-result]');
      showResult(out, res.kind, res.message || 'Nothing was changed');
      if (res.code === 'checked') b.remove(); else b.disabled = false;
      refresh();
    });
  }
  const hl = $('.q-card.is-highlight');
  if (hl) hl.scrollIntoView({ block: 'center' });
  if (!$('.board') && !$('.item-grid')) notice('Queue unknown: the queue could not be read.');
}
