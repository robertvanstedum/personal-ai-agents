// Shop floor state on screen: lights, Needs you and the opening briefing, kept
// current by polling the API every 60 s while the page is visible (and once
// after each Save). Rules only; nothing here calls a model.
//
// A failed poll never leaves old values looking current (review F1): the
// floor turns "stale" (word, clock shape, time since the last good read), then
// grey "unknown" after two missed polls or 3 minutes; a 401 greys it at once
// as "signed out". The rules are in freshness.js.
import { $, $$, el, shapeSvg, localTime, localizeTimes } from './dom.js';
import { apiGet } from './api.js';
import { explain, setBriefing } from './conversation.js';
import { freshnessOf, lightView, needsView, bannerText, briefingText, sinceText } from './freshness.js';
import { applyFloorZones } from './zones.js';
import { renderFloorNeeds, renderBlockers, updateQuiet } from './floorlayout.js';

let page;
let current = null;
let etag = null;
let timer = null;
let ticker = null;
const POLL_MS = 60000;
const TICK_MS = 15000;

const fresh = { mode: 'live', missed: 0, signedOut: false, lastGoodMs: Date.now(), why: '' };
const clock = (ms) => localTime(new Date(ms).toISOString());
const since = () => sinceText(fresh.lastGoodMs, Date.now(), clock);

function setShape(node, shape) {
  const old = node.querySelector('svg.lshape');
  if (old && old.dataset.shape !== shape) old.replaceWith(shapeSvg(shape));
}

function renderLights(lights) {
  const at = since();
  for (const l of lights) {
    const v = lightView(l, fresh.mode, at);
    for (const node of $$(`[data-light="${l.id}"], [data-lc="${l.id}"]`)) {
      node.dataset.lightState = v.state;
      if (fresh.mode === 'live') delete node.dataset.stale; else node.dataset.stale = fresh.mode;
      setShape(node, v.shape);
      const word = node.querySelector('[data-light-word]');
      if (word) word.textContent = v.word;
      const lcWord = node.querySelector('[data-lc-word]');
      if (lcWord) lcWord.textContent = v.compactWord;
      const reason = node.querySelector('[data-light-reason]');
      if (reason) reason.textContent = v.reason;
      const src = node.querySelector('[data-light-src]');
      if (src) { src.textContent = v.src; src.dataset.source = v.source; }
    }
    for (const node of $$(`[data-ps-light="${l.id}"]`)) {
      node.dataset.unknown = String(v.unknown);
      if (fresh.mode === 'live') delete node.dataset.stale; else node.dataset.stale = fresh.mode;
      node.querySelector('[data-ps-word]').textContent = fresh.mode === 'live' ? v.word : v.compactWord;
      node.querySelector('[data-ps-reason]').textContent = fresh.mode === 'live' ? v.reason : v.src;
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
  const v = needsView(needs, fresh.mode, since(), needsLineText(needs));
  for (const line of $$('[data-needs-line]')) line.textContent = v.line;
  for (const c of $$('[data-reminder-count]')) c.textContent = v.count;
  for (const c of $$('[data-needs-count]')) c.textContent = v.countWord;
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

function renderFreshness() {
  document.body.dataset.freshness = fresh.mode;
  for (const zone of $$('[data-zone], [data-floor-urgent], [data-needs-badge], .phone-summary, [data-panel="needs"]')) {
    if (fresh.mode === 'live') delete zone.dataset.stale; else zone.dataset.stale = fresh.mode;
  }
  let banner = $('[data-stale-banner]');
  if (!banner && fresh.mode !== 'live') {
    banner = el('p', { class: 'gu-notice gu-stale', 'data-stale-banner': true, role: 'status' });
    const anchor = $('[data-notice]');
    if (anchor) anchor.after(banner); else document.body.prepend(banner);
  }
  if (!banner) return;
  banner.hidden = fresh.mode === 'live';
  banner.dataset.stale = fresh.mode;
  banner.replaceChildren();
  if (fresh.mode !== 'live') {
    banner.append(shapeSvg(fresh.mode === 'stale' ? 'stale' : 'ring'), document.createTextNode(` ${bannerText(fresh.mode, since(), fresh.why)}`));
  }
}

function render() {
  if (!current) return;
  renderLights(current.lights || []);
  renderNeeds(current.needs);
  renderFloorNeeds(current.needs, fresh.mode);
  if (current.briefing) {
    setBriefing(current.briefing);
    const text = $('[data-briefing-text]');
    if (text && fresh.mode !== 'live') text.textContent = briefingText(fresh.mode, since(), text.textContent);
  }
  renderFreshness();
  applyFloorZones(current, fresh.mode, since());
  renderBlockers(current.blockers);
  updateQuiet();
  document.body.dataset.observedAt = current.observed_at;
  localizeTimes();
}

// Re-derive the mode (failures and age) and redraw when it changes, or while
// it is not live, so "N min ago" keeps counting.
function applyFreshness(force = false) {
  const mode = freshnessOf({ missed: fresh.missed, lastGoodMs: fresh.lastGoodMs, nowMs: Date.now(), signedOut: fresh.signedOut });
  const changed = mode !== fresh.mode;
  fresh.mode = mode;
  if (force || changed || mode !== 'live') render();
}

export function applyState(state) {
  current = state;
  page.floor = state;
  render();
}

function goodRead() {
  fresh.missed = 0;
  fresh.signedOut = false;
  fresh.why = '';
  fresh.lastGoodMs = Date.now();
}

export async function refresh() {
  const r = await apiGet('/floor', etag);
  if (r.notModified) {
    goodRead();
    applyFreshness(true);
    return current;
  }
  if (r.ok && r.body && Array.isArray(r.body.lights)) {
    etag = r.etag;
    current = r.body;
    page.floor = r.body;
    goodRead();
    applyFreshness(true);
    return current;
  }
  if (r.status === 401) {
    fresh.signedOut = true;
    fresh.why = 'signed out';
  } else {
    fresh.missed += 1;
    fresh.why = r.status ? `the server answered ${r.status}` : 'the server could not be reached';
  }
  applyFreshness(true);
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
  fresh.lastGoodMs = Date.now();
  document.body.dataset.freshness = 'live';
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') { applyFreshness(); refresh(); }
    schedule();
  });
  schedule();
  window.clearInterval(ticker);
  ticker = window.setInterval(() => applyFreshness(), TICK_MS);
  if (page.page !== 'floor') return;
  const lightsBtn = $('[data-lights-toggle]');
  lightsBtn.addEventListener('click', () => toggle('lightsOpen', lightsBtn, 'Hide ▴', 'Details ▾'));
  const sheetBtn = $('[data-sheet-toggle]');     // the pre-1.1 floor's post-it sheet; gone from the 1.1 layout
  if (sheetBtn) sheetBtn.addEventListener('click', () => toggle('sheetOpen', sheetBtn));
  // Phone: the context rail starts folded below the conversation, so chat comes first.
  const context = $('[data-floor-context]');
  if (context && window.matchMedia('(max-width: 640px)').matches) context.open = false;
  for (const b of $$('[data-ask]')) {
    b.addEventListener('click', () => {
      const light = (current.lights || []).find((l) => l.id === b.dataset.ask);
      if (!light) return;
      if (fresh.mode === 'live') { explain(light); return; }
      const v = lightView(light, fresh.mode, since());
      explain({ ...light, word: v.word, reason: v.reason, source_mark: v.src, detail: [v.src, ...(light.detail || [])] });
    });
  }
}

