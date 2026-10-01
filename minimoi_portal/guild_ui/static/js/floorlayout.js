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
let serverBlockers = []; // the latest blockers the server gave (page load, then each /floor answer)

function railPref() {
  return load(KEY, (v) => (v === 'open' || v === 'closed' ? v : null), () => 'open', 'rail setting');
}

const overlayRail = () => !window.matchMedia(WIDE).matches && !window.matchMedia(PHONE).matches;
const drawerHistory = () => window.matchMedia('(max-width: 899px)').matches;

function syncScrim() {
  const scrim = $('[data-floor-scrim]');
  if (!scrim) return;
  const railOver = document.body.dataset.rail === 'open' && overlayRail();
  const drawer = document.body.dataset.history === 'open' && drawerHistory();
  scrim.hidden = !(railOver || drawer);
}

// focus: 'into' moves focus into the panel that opened; 'back' returns it to its toggle.
function setRail(open, persist, focus = null) {
  document.body.dataset.rail = open ? 'open' : 'closed';
  for (const b of $$('[data-rail-toggle]')) b.setAttribute('aria-expanded', String(open));
  if (persist && window.matchMedia(WIDE).matches) save(KEY, open ? 'open' : 'closed');
  syncScrim();
  if (focus === 'into' && open) {
    // The first control actually shown (the folded summary is hidden off a phone).
    const first = $$('#main a, #main button, #main summary').find((n) => n.getClientRects().length > 0);
    if (first) first.focus();
  } else if (focus === 'back') {
    const t = $('[data-rail-toggle]');
    if (t) t.focus();
  }
}

function setHistory(open, focus = null) {
  const drawer = $('[data-floor-history]');
  if (open && drawer && drawerHistory()) {
    // The drawer opens below the chat header, so the ☰ button stays uncovered.
    const bar = $('[data-floorbar]');
    const top = bar ? Math.max(0, Math.round(bar.getBoundingClientRect().bottom)) : 0;
    drawer.style.setProperty('--gu-drawer-top', `${top}px`);
  }
  document.body.dataset.history = open ? 'open' : 'closed';
  for (const b of $$('[data-history-toggle]')) b.setAttribute('aria-expanded', String(open));
  syncScrim();
  if (focus === 'into' && open) {
    const close = $('[data-history-close]');
    if (close) close.focus();
  } else if (focus === 'back') {
    const t = $('[data-history-toggle]');
    if (t) t.focus();
  }
}

function fitRailToWidth() {
  // The context <details>: folded below the chat on a phone; always open elsewhere
  // (its summary is hidden there), including after rotating a phone to landscape.
  const context = $('[data-floor-context]');
  const phone = window.matchMedia(PHONE).matches;
  if (context) context.open = !phone;
  if (phone) { setRail(true, false); return; }
  setRail(window.matchMedia(WIDE).matches ? railPref() === 'open' : false, false);
}

// Needs you: the badge is the one place it appears; the rail shows only the
// single most urgent action, or one quiet line.
export function renderFloorNeeds(needs, mode = 'live', since = '') {
  if (!needs) return;
  const known = needs.status === 'ok';
  for (const badge of $$('[data-needs-badge]')) {
    // Hidden only at a live, known zero. A stale, unknown or signed-out floor
    // shows the badge with "?" and a dashed mark: never a zero that looks live.
    badge.hidden = mode === 'live' && known && !needs.total;
    badge.dataset.needsStatus = needs.status;
    badge.dataset.fresh = mode;
    if (mode !== 'live') {
      const count = badge.querySelector('[data-reminder-count]');
      if (count) count.textContent = '?';
    }
    badge.title = mode !== 'live' ? `Needs you · ${mode === 'stale' ? `stale, last good read ${since}` : 'unknown'}`
      : known ? 'Needs you — open the wall' : needs.text;
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
  // "Nothing needs you right now" only on a live, known read: never while stale.
  const fresh = document.body.dataset.freshness || 'live';
  const needsKnown = (!badge || badge.dataset.needsStatus === 'ok') && fresh === 'live';
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
  if (blockers !== undefined) serverBlockers = blockers || [];
  const line = $('[data-mc-blocker]');
  if (!line) return;
  const items = [...serverBlockers];
  if (failed) items.push({ kind: 'failed', text: failed });
  line.replaceChildren(...items.map((b) => el('span', { 'data-blocker': b.kind }, b.text)));
  line.hidden = items.length === 0;
}

export function answerFailed(text) {
  failed = `Last answer failed · ${text}`;
  renderBlockers();              // with the latest server blockers, not the page-load ones
}

export function answerArrived() {
  if (!failed) return;
  failed = null;
  renderBlockers();
}

// The history row's count follows the thread: every kept note on screen
// (with "+" when the page loaded only the latest ones).
function countKept() {
  const meta = $('[data-fh-count]');
  const thread = $('[data-mc-thread]');
  const notesLine = $('[data-notes-line]');
  if (!meta || !thread || (notesLine && notesLine.dataset.notesState !== 'ok')) return;
  const n = thread.querySelectorAll('[data-kind="note"][data-note]').length;
  meta.textContent = `${n}${meta.dataset.more === 'true' ? '+' : ''} kept`;
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
  // The phone's Type bar (F10, left as it is): on the floor it sits inside the
  // composer's block, below the form; only a bar fixed outside it needs room.
  const barH = bar && !(dock && dock.contains(bar)) ? bar.getBoundingClientRect().height : 0;
  document.documentElement.style.setProperty('--gu-typebar', `${Math.round(barH)}px`);
  const below = (dock ? dock.getBoundingClientRect().height : 0) + barH;
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
    b.addEventListener('click', () => {
      const open = document.body.dataset.rail !== 'open';
      setRail(open, true, open && overlayRail() ? 'into' : null);
    });
  }
  for (const b of $$('[data-history-toggle]')) {
    b.addEventListener('click', () => {
      const open = document.body.dataset.history !== 'open';
      setHistory(open, open ? 'into' : null);
    });
  }
  for (const b of $$('[data-history-close]')) b.addEventListener('click', () => setHistory(false, 'back'));
  const scrim = $('[data-floor-scrim]');
  if (scrim) {
    scrim.addEventListener('click', () => {         // a tap outside closes what is open over the chat
      if (document.body.dataset.history === 'open') setHistory(false, 'back');
      if (document.body.dataset.rail === 'open' && overlayRail()) setRail(false, false, 'back');
    });
  }
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (document.body.dataset.history === 'open') setHistory(false, 'back');
    if (document.body.dataset.rail === 'open' && overlayRail()) setRail(false, false, 'back');
  });
  const thread = $('[data-mc-thread]');
  if (thread) new MutationObserver(countKept).observe(thread, { childList: true });
  window.matchMedia(WIDE).addEventListener('change', fitRailToWidth);
  window.matchMedia(PHONE).addEventListener('change', fitRailToWidth);
  keyboardAware();
  fitThread();
  window.addEventListener('resize', fitThread);
  renderFloorNeeds(page.floor && page.floor.needs);
  renderBlockers(page.floor && page.floor.blockers);
}
