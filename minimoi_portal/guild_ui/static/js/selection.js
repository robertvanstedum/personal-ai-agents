// Chat's selection actions (Guild 1.1 slice 1, spec §3 and §8): select text
// inside one kept message and a small toolbar offers Pin to Board and Take to
// a Room. Rules:
//  * only on the record, and only in a tab that knows its record mode; going
//    off the record (or to unknown) clears the selection and hides the bar,
//    and nothing captured before is kept or replayed;
//  * only text inside one stored, on-the-record note (a thread row with a
//    server note id): never a streaming line, an off-the-record line or a
//    platform line;
//  * Pin to Board adds a post-it through the existing guarded API (CSRF,
//    record mode, idempotency key); text over the post-it limit is refused
//    here, never cut;
//  * Take to a Room is disabled until Rooms land (slice 4).
// On a phone the bar docks over the composer while text is selected.
// No model call.
import { $, announce } from './dom.js';
import { live, onChange } from './state.js';
import { apiPost, recordMode } from './api.js';

let page = null;
let bar = null;
let current = null;      // { noteId, text, key }

const newKey = () => `sel-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

function storedNote(node) {
  const elem = node && (node.nodeType === 1 ? node : node.parentElement);
  const row = elem ? elem.closest('li[data-kind="note"][data-note]') : null;
  return row && row.closest('[data-mc-thread]') ? row : null;
}

function readSelection() {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;
  const text = sel.toString().replace(/\s+/g, ' ').trim();
  if (!text) return null;
  const a = storedNote(sel.anchorNode);
  if (!a || a !== storedNote(sel.focusNode) || !a.dataset.note) return null;
  return { noteId: a.dataset.note, text, rect: sel.getRangeAt(0).getBoundingClientRect() };
}

function say(text) {
  const out = $('[data-sel-result]', bar);
  if (out) { out.textContent = text; out.hidden = !text; }
  if (text) announce(text);
}

function hide() {
  current = null;
  if (!bar) return;
  bar.hidden = true;
  say('');
}

function place(rect) {
  // Desktop: just above the selection, kept inside the window. Phone: docked
  // just above the composer. Set through the CSSOM: the CSP allows no inline
  // style attributes.
  if (window.matchMedia('(max-width: 640px)').matches) {
    const dock = $('[data-mc-dock-bottom]');
    const top = dock ? dock.getBoundingClientRect().top : window.innerHeight;
    bar.style.setProperty('--sel-y', `${Math.round(Math.max(8, top - (bar.offsetHeight || 52) - 6))}px`);
    return;
  }
  const w = bar.offsetWidth || 320;
  const x = Math.min(Math.max(8, rect.left + rect.width / 2 - w / 2), window.innerWidth - w - 8);
  const y = Math.max(8, rect.top - (bar.offsetHeight || 40) - 8);
  bar.style.setProperty('--sel-x', `${Math.round(x)}px`);
  bar.style.setProperty('--sel-y', `${Math.round(y)}px`);
}

let hideTimer = null;

function update() {
  if (!bar) return;
  if (live.off || !live.known) { hide(); return; }
  const s = readSelection();
  window.clearTimeout(hideTimer);
  if (!s) {
    // A tap on a phone can clear the selection just before the button's
    // click: hide a moment later, unless a toolbar button has focus.
    hideTimer = window.setTimeout(() => { if (!bar.contains(document.activeElement) && !readSelection()) hide(); }, 400);
    return;
  }
  if (!current || current.noteId !== s.noteId || current.text !== s.text) {
    current = { noteId: s.noteId, text: s.text, key: newKey() };
    say('');
  }
  bar.hidden = false;
  place(s.rect);
}

// Switching mode, in either direction, clears the selection and any pending
// action (spec §8, §11): nothing selected before the switch is kept or replayed.
function onModeChange() {
  const sel = window.getSelection();
  if (sel && sel.rangeCount && !sel.isCollapsed) sel.removeAllRanges();
  window.clearTimeout(hideTimer);
  hide();
}

async function pin(btn) {
  if (!current || live.off || !live.known) { hide(); return; }
  const max = page.postit_max || 140;
  if (current.text.length > max) {
    say(`Too long for a post-it (${current.text.length} of ${max} characters). Select a shorter passage. Nothing was pinned.`);
    return;
  }
  const taken = current;
  btn.disabled = true;
  const r = await apiPost('/postits', { text: taken.text, idempotency_key: taken.key, record_mode: recordMode() });
  btn.disabled = false;
  if (current !== taken) return;            // the mode or the selection changed meanwhile
  const body = r.body || {};
  if (r.ok && body.result === 'added') {
    say('Pinned to the Board.');
    current.key = newKey();                 // a second pin of the same text is a new post-it
  } else {
    say(body.message || 'Not pinned. Nothing was changed.');
  }
}

export function initSelection(p) {
  page = p;
  bar = $('[data-sel-bar]');
  if (!bar || page.page !== 'floor') return;
  document.addEventListener('selectionchange', update);
  window.addEventListener('resize', () => { if (!bar.hidden) update(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !bar.hidden) hide(); });
  const pinBtn = $('[data-sel-pin]', bar);
  // Keep the selection while a toolbar button is pressed.
  bar.addEventListener('mousedown', (e) => { if (e.target.closest('button')) e.preventDefault(); });
  if (pinBtn) pinBtn.addEventListener('click', () => pin(pinBtn));
  onChange(onModeChange);
}
