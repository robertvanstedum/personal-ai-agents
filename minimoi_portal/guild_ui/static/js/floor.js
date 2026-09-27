// Shop floor state on screen: lights, Needs you and the opening briefing, kept
// current by polling the API every 60 s while the page is visible (and once
// after each Save). Rules only; nothing here calls a model.
import { $, $$, el, shapeSvg, localTime, localizeTimes } from './dom.js';
import { apiGet } from './api.js';
import { explain, setBriefing } from './conversation.js';

let page;
let current = null;
let etag = null;
let timer = null;
const POLL_MS = 60000;

function setLight(node, light) {
  node.dataset.lightState = light.state;
  const old = node.querySelector('svg.lshape');
  if (old) old.replaceWith(shapeSvg(light.shape));
}

function renderLights(lights) {
  for (const l of lights) {
    for (const node of $$(`[data-light="${l.id}"], [data-lc="${l.id}"]`)) {
      setLight(node, l);
      const word = node.querySelector('[data-light-word], [data-lc-word]');
      if (word) word.textContent = l.word;
      const reason = node.querySelector('[data-light-reason]');
      if (reason) reason.textContent = l.reason;
      const src = node.querySelector('[data-light-src]');
      if (src) { src.textContent = l.source_mark; src.dataset.source = l.source; }
    }
    for (const node of $$(`[data-ps-light="${l.id}"]`)) {
      node.dataset.unknown = String(l.state === 'unknown');
      node.querySelector('[data-ps-word]').textContent = l.word;
      node.querySelector('[data-ps-reason]').textContent = l.reason;
    }
  }
}

function needsLineText(needs) {
  if (needs.status !== 'ok') return needs.text;
  const at = localTime(needs.observed_at);
  if (!needs.total) return `Nothing needs you · checked ${at}`;
  const shown = needs.items.length;
  return `${needs.total} in all${needs.total > shown ? ` · ${shown} shown` : ''} · checked ${at}`;
}

function renderNeeds(needs) {
  const list = $('[data-reminders]');
  if (list) {
    list.replaceChildren();
    for (const r of needs.items) {
      const li = el('li', { class: 'reminder', 'data-reminder': true });
      li.append(el('span', { class: 'reminder-tag' }, r.tag), el('a', { href: r.href, 'data-reminder-link': true }, r.text));
      list.append(li);
    }
  }
  for (const line of $$('[data-needs-line]')) line.textContent = needsLineText(needs);
  const count = needs.total == null ? '?' : String(needs.total);
  for (const c of $$('[data-reminder-count]')) c.textContent = count;
  for (const c of $$('[data-needs-count]')) c.textContent = needs.total == null ? 'unknown' : String(needs.total);
  const phone = $('[data-needs-list]');
  if (phone) {
    phone.replaceChildren();
    for (const r of needs.items) {
      const li = el('li', { 'data-needs-row': true });
      li.append(el('span', { class: 'tag', 'data-tag': 'needs' }, r.tag), document.createTextNode(' '), el('a', { href: r.href }, r.text));
      phone.append(li);
    }
  }
  const urgent = $('[data-floor-urgent]');
  if (urgent) {
    const first = needs.items[0];
    urgent.hidden = !first;
    if (first) {
      const a = $('[data-urgent-link]');
      a.href = first.href;
      a.textContent = `${first.tag} · ${first.text}`;
    }
  }
}

export function applyState(state) {
  current = state;
  page.floor = state;
  renderLights(state.lights);
  renderNeeds(state.needs);
  setBriefing(state.briefing);
  document.body.dataset.observedAt = state.observed_at;
  localizeTimes();
}

export async function refresh() {
  const r = await apiGet('/floor', etag);
  if (r.notModified) return current;
  if (r.ok && r.body && r.body.lights) {
    etag = r.etag;
    applyState(r.body);
  }
  return current;
}

function schedule() {
  window.clearInterval(timer);
  timer = null;
  if (document.visibilityState === 'visible') timer = window.setInterval(refresh, POLL_MS);
}

function toggle(attr, btn, openText, closedText) {
  const on = document.body.dataset[attr] !== 'true';
  document.body.dataset[attr] = String(on);
  btn.setAttribute('aria-expanded', String(on));
  const label = btn.querySelector('[data-lights-more]');
  if (label && openText) label.textContent = on ? openText : closedText;
}

export function initFloorState(p) {
  page = p;
  current = p.floor;
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') refresh(); schedule(); });
  schedule();
  if (page.page !== 'floor') return;
  const lightsBtn = $('[data-lights-toggle]');
  lightsBtn.addEventListener('click', () => toggle('lightsOpen', lightsBtn, 'Hide ▴', 'Details ▾'));
  const sheetBtn = $('[data-sheet-toggle]');
  sheetBtn.addEventListener('click', () => toggle('sheetOpen', sheetBtn));
  for (const b of $$('[data-ask]')) {
    b.addEventListener('click', () => {
      const light = (current.lights || []).find((l) => l.id === b.dataset.ask);
      if (light) explain(light);
    });
  }
}
