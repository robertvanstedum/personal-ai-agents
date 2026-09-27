// Shop floor: status strip (expand on phone), Ask, Needs-you cap, one urgent
// note, the phone sheet, and Continue. Behaviour only; markup is in ui_floor.html.
import { $, $$ } from './dom.js';
import { world, onChange, applyShowWhen } from './world.js';
import { handle } from './scenario.js';

function applyCaps() {
  const list = $('[data-reminders]');
  applyShowWhen(list);
  const cap = Number(list.dataset.cap || 3);
  let shown = 0;
  let first = null;
  for (const li of $$('[data-reminder]', list)) {
    const visible = !li.hidden;
    const inCap = visible && shown < cap;
    li.classList.toggle('is-capped', visible && !inCap);
    if (inCap) { shown += 1; if (!first) first = li; }
  }
  $('[data-reminder-count]').textContent = String(shown);
  const urgent = $('[data-floor-urgent]');
  urgent.hidden = !first;
  if (first) {
    const a = $('[data-reminder-link]', first);
    const link = $('[data-urgent-link]');
    link.href = a.getAttribute('href');
    link.textContent = `${$('.reminder-tag', first).textContent} · ${a.textContent}`;
  }
}

function toggle(attr, btn, openText, closedText) {
  const on = document.body.dataset[attr] !== 'true';
  document.body.dataset[attr] = String(on);
  btn.setAttribute('aria-expanded', String(on));
  if (openText) {
    const label = btn.querySelector('[data-lights-more]');
    if (label) label.textContent = on ? openText : closedText;
  }
}

export function initFloor(nav) {
  const lightsBtn = $('[data-lights-toggle]');
  lightsBtn.addEventListener('click', () => toggle('lightsOpen', lightsBtn, 'Hide ▴', 'Details ▾'));
  const sheetBtn = $('[data-sheet-toggle]');
  sheetBtn.addEventListener('click', () => toggle('sheetOpen', sheetBtn));
  for (const b of $$('[data-ask]')) {
    b.addEventListener('click', () => {
      handle(b.dataset.ask, 'text');
      const input = $('[data-mc-input]');
      if (input && !window.matchMedia('(max-width: 640px)').matches) input.focus();
    });
  }
  const cont = $('[data-floor-continue]');
  if (nav && nav.detail_href) { cont.href = nav.detail_href; cont.textContent = nav.detail_label || nav.detail_href; }
  onChange(applyCaps);
  applyCaps();
  world.conv.unread = 0; // the conversation is in view here
}
