// Queue board and item page: Save = owner confirmation → local overlay
// ("pending real write") with a receipt. The real queue is never written.
import { $, $$, el, notice } from './dom.js';
import { world, onChange } from './world.js';
import { queueSave } from './proposals.js';

const ACTIVE = ['spec_ready', 'in_build'];
const nice = (s) => s.replace('_', ' ');

function effective(form) {
  const o = world.overlay.queue[form.dataset.itemId];
  return o ? o.to : form.dataset.sourceStatus;
}

function applyForms() {
  for (const form of $$('[data-status-form]')) {
    const eff = effective(form);
    const sel = $('[data-status-select]', form);
    if (form.dataset.dirty !== 'true') sel.value = eff; // keep an unsaved choice
    $('[data-save]', form).hidden = sel.value === eff;
    $('[data-note-wrap]', form).hidden = sel.value !== 'blocked';
  }
}

function applyBoard() {
  const board = $('.board');
  if (!board) return;
  const moved = [];
  for (const card of $$('[data-queue-card]')) {
    const id = card.dataset.itemId;
    const o = world.overlay.queue[id];
    const chip = $('[data-pending]', card);
    chip.hidden = !o;
    if (o) { chip.textContent = `pending real write · ${o.receipt}`; chip.dataset.chip = 'pending'; }
    const eff = o ? o.to : $('[data-status-form]', card).dataset.sourceStatus;
    if (ACTIVE.includes(eff)) {
      card.hidden = false;
      const col = $(`[data-col="${eff}"] [data-col-cards]`);
      if (card.parentElement !== col) col.append(card);
    } else {
      card.hidden = true;
      moved.push({ id, to: eff, receipt: o.receipt });
    }
  }
  for (const id of Object.keys(world.overlay.queue)) {
    const o = world.overlay.queue[id];
    if (!$(`[data-queue-card][data-item-id="${id}"]`) && ACTIVE.includes(o.to)) moved.push({ id, to: o.to, receipt: o.receipt, added: true });
  }
  let total = 0;
  for (const col of $$('[data-col]')) {
    const n = $$('[data-queue-card]', col).filter((c) => !c.hidden).length;
    total += n;
    $('[data-col-count]', col).textContent = String(n);
    $('[data-col-empty]', col).hidden = n > 0;
  }
  $('[data-active-count]').textContent = String(total);
  const box = $('[data-queue-moved]');
  box.replaceChildren();
  box.hidden = moved.length === 0;
  for (const m of moved) {
    const line = el('span', {}, `#${m.id} ${m.added ? 'set to' : 'moved to'} ${nice(m.to)} locally (pending real write · ${m.receipt}) · `);
    const a = el('a', { href: world.page.urls.item.replace('__ID__', m.id) }, 'Open item');
    box.append(line, a, el('br'));
  }
}

function applyItem() {
  const grid = $('[data-item-id].item-grid');
  if (!grid) return;
  const id = grid.dataset.itemId;
  const o = world.overlay.queue[id];
  const form = $('[data-status-form]');
  const eff = $('[data-effective-status]');
  if (!eff.dataset.serverText) eff.dataset.serverText = eff.textContent; // keeps "unknown status '…'" from the server
  eff.textContent = o ? nice(o.to) : eff.dataset.serverText;
  const chip = $('.page-meta [data-pending]');
  chip.hidden = !o;
  if (o) { chip.textContent = `pending real write · ${o.receipt}`; chip.dataset.chip = 'pending'; }
  const ul = $('[data-history-local]');
  ul.replaceChildren();
  for (const h of world.overlay.queue_history.filter((x) => String(x.item) === String(id))) {
    ul.append(el('li', {}, `${h.at} · ${nice(h.from)} → ${nice(h.to)} · ${h.by} · local only · pending real write · ${h.receipt}`));
  }
}

export function initQueue(refuse) {
  for (const form of $$('[data-status-form]')) {
    const sel = $('[data-status-select]', form);
    sel.addEventListener('change', () => { form.dataset.dirty = String(sel.value !== effective(form)); applyForms(); });
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const res = queueSave(Number(form.dataset.itemId), effective(form), sel.value, $('[data-note]', form).value);
      if (res.refused) { refuse(res.refused); return; }
      form.dataset.dirty = 'false';
      applyForms();
    });
  }
  const back = $('[data-back]');
  if (back) back.addEventListener('click', () => {
    const sameOrigin = document.referrer && document.referrer.startsWith(window.location.origin);
    if (sameOrigin && window.history.length > 1) window.history.back();
    else window.location.href = back.dataset.fallback;
  });
  const hl = $('.q-card.is-highlight');
  if (hl) hl.scrollIntoView({ block: 'center' });
  const all = () => { applyForms(); applyBoard(); applyItem(); };
  onChange(all);
  all();
  if (!$('.board') && !$('.item-grid')) notice('Queue unavailable.');
}
