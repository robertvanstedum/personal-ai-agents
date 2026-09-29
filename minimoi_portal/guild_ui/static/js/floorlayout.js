// The Shop floor's layout (Guild 1.1 dev, slice 1; Robert, September 29):
//  * the context rail: open beside the chat at >= 1200 px, where Robert can
//    collapse it (remembered per browser); below 1200 px a Context button
//    opens it over the chat; on a phone it is folded below the conversation;
//  * the conversation history: a column at >= 900 px, a drawer below;
//  * Needs you in exactly one place, the chat header's badge (hidden at zero);
//  * the rail's one quiet line when nothing needs attention;
//  * what blocks chat, one short line above the composer;
//  * on a phone, the composer stays above the on-screen keyboard.
// Nothing here calls a model.
import { $, $$, el } from './dom.js';
import { load, save } from './state.js';

const WIDE = '(min-width: 1200px)';
const PHONE = '(max-width: 640px)';
const KEY = 'floor.rail';
let page;
let failed = null;       // the last failed answer, shown until the next answer

function railPref() {
  return load(KEY, (v) => (v === 'open' || v === 'closed' ? v : null), () => 'open', 'rail setting');
}

function setRail(open, persist) {
  document.body.dataset.rail = open ? 'open' : 'closed';
  for (const b of $$('[data-rail-toggle]')) b.setAttribute('aria-expanded', String(open));
  if (persist && window.matchMedia(WIDE).matches) save(KEY, open ? 'open' : 'closed');
}

function setHistory(open) {
  document.body.dataset.history = open ? 'open' : 'closed';
  for (const b of $$('[data-history-toggle]')) b.setAttribute('aria-expanded', String(open));
}

function fitRailToWidth() {
  if (window.matchMedia(PHONE).matches) { setRail(true, false); return; }   // below the chat, folded (floor.js)
  setRail(window.matchMedia(WIDE).matches ? railPref() === 'open' : false, false);
}

// Needs you: the badge is the one place it appears; the rail shows only the
// single most urgent action, or one quiet line.
export function renderFloorNeeds(needs, mode = 'live') {
  if (!needs) return;
  const known = needs.status === 'ok';
  for (const badge of $$('[data-needs-badge]')) {
    // Hidden only at a live, known zero: a stale or unknown floor shows its mark.
    badge.hidden = mode === 'live' && known && !needs.total;
    badge.dataset.needsStatus = needs.status;
    badge.title = known ? 'Needs you — open the wall' : needs.text;
  }
  updateQuiet();
}

export function updateQuiet() {
  const urgent = $('[data-floor-urgent]');
  const about = $('[data-rail-continue]');
  const badge = $('[data-needs-badge]');
  const quietLine = $('[data-rail-quiet]');
  const wall = $('[data-rail-wall]');
  if (!quietLine) return;
  const needsKnown = (!badge || badge.dataset.needsStatus === 'ok') && document.body.dataset.freshness !== 'unknown'
    && document.body.dataset.freshness !== 'signed_out';
  const quiet = needsKnown && (!urgent || urgent.hidden) && (!about || about.hidden);
  quietLine.hidden = !quiet;
  if (quiet && !quietLine.textContent.trim()) {
    quietLine.append('Nothing needs you right now · ', el('a', { href: page.urls.bench, 'data-open-wall': true }, 'Open wall'));
  }
  if (wall) wall.hidden = quiet;
}

// What blocks chat: the server's list (MC down; the cost level at act or
// stop), plus a failed answer on this page until the next answer.
export function renderBlockers(blockers) {
  const line = $('[data-mc-blocker]');
  if (!line) return;
  const items = [...(blockers || [])];
  if (failed) items.push({ kind: 'failed', text: failed });
  line.replaceChildren(...items.map((b) => el('span', { 'data-blocker': b.kind }, b.text)));
  line.hidden = items.length === 0;
}

export function answerFailed(text) {
  failed = `Last answer failed · ${text}`;
  renderBlockers(page.floor && page.floor.blockers);
}

export function answerArrived() {
  if (!failed) return;
  failed = null;
  renderBlockers(page.floor && page.floor.blockers);
}

// Phone: the thread takes exactly the room left between its top and the
// composer, so the composer and the newest reply are in the first view
// (set through the CSSOM: the CSP allows no inline style attributes).
function fitThread() {
  const thread = $('[data-mc-thread]');
  if (!thread) return;
  if (!window.matchMedia(PHONE).matches) { thread.style.removeProperty('--gu-thread-max'); return; }
  const vv = window.visualViewport;
  const visible = vv ? vv.height : window.innerHeight;
  const top = thread.getBoundingClientRect().top + window.scrollY;
  const dock = $('[data-mc-dock-bottom]');
  const bar = $('.mc-phone-bar');
  const below = (dock ? dock.getBoundingClientRect().height : 0) + (bar ? bar.getBoundingClientRect().height : 0);
  thread.style.setProperty('--gu-thread-max', `${Math.max(140, Math.floor(visible - top - below - 8))}px`);
  thread.scrollTop = thread.scrollHeight;
}

// Phone: keep the composer above the on-screen keyboard. The visual viewport
// shrinks when the keyboard opens; the layout viewport does not.
function keyboardAware() {
  const vv = window.visualViewport;
  if (!vv) return;
  const apply = () => {
    const covered = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
    document.documentElement.style.setProperty('--gu-kb', `${Math.round(covered)}px`);
    document.body.dataset.keyboard = covered > 80 ? 'open' : 'closed';
    fitThread();
  };
  vv.addEventListener('resize', apply);
  vv.addEventListener('scroll', apply);
  apply();
}

export function initFloorLayout(p) {
  page = p;
  if (page.page !== 'floor') return;
  fitRailToWidth();
  setHistory(false);
  for (const b of $$('[data-rail-toggle]')) {
    b.addEventListener('click', () => setRail(document.body.dataset.rail !== 'open', true));
  }
  for (const b of $$('[data-history-toggle]')) {
    b.addEventListener('click', () => setHistory(document.body.dataset.history !== 'open'));
  }
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (document.body.dataset.history === 'open') setHistory(false);
    if (document.body.dataset.rail === 'open' && !window.matchMedia(WIDE).matches && !window.matchMedia(PHONE).matches) setRail(false, false);
  });
  window.matchMedia(WIDE).addEventListener('change', fitRailToWidth);
  window.matchMedia(PHONE).addEventListener('change', fitRailToWidth);
  keyboardAware();
  fitThread();
  window.addEventListener('resize', fitThread);
  renderFloorNeeds(page.floor && page.floor.needs);
  renderBlockers(page.floor && page.floor.blockers);
}
