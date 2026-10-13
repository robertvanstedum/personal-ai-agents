// Dictation (insertion-only): speech becomes text in the draft box next to the button, and nothing else happens. Off unless the server switched it on
// (data-dictation) AND the capabilities call answers; a button exists in the page only while the flag is on.
//
// It reuses the shared memo controller (the microphone goes to the speech provider for TRANSCRIPTION only; the owner taps the button again to finish). The words land where
// the cursor was when dictation started, or replace the selection: ordinary textarea behaviour. It never sends a message, never keeps a note and never calls Master
// Craftsman; the existing draft handling sees an ordinary `input` event. Only one microphone is open at a time (dictation in one box, or MC Voice, never both).
// Private is a bounded gap, not a policy: dictation does not start there and stops if Private begins. Audio and its transcript are not stored by the page.
//
// What is and is not known up front: the capabilities call says the shared transport supports the provider, NOT that a key is configured. A missing or refused key shows as a
// plain failure when a session starts; typing keeps working.
import { $, announce } from './dom.js';
import { live, onChange } from './state.js';
import { recordMode } from './api.js';

const WORDS = {
  private: 'Dictation stopped: it is not built for Private yet. Typing in Private works as before.',
  record_unknown: 'Dictation stopped: this tab does not know whether you are on the record.',
  minimized: 'Dictation stopped: the chat was minimized.',
  unavailable: 'Dictation stopped: this box is not available now.',
  signed_out: 'Dictation stopped: you are signed out.',
};

export function initDictation(page) {
  if (document.body.dataset.dictation !== 'true') return;
  const buttons = Array.from(document.querySelectorAll('[data-dictate]'));
  if (!buttons.length) return;
  const api = page.urls.api;
  // `session` numbers the attempt: stop() moves it on, and every callback of an older attempt (a late transcript, error or state change) is then ignored.
  let session = 0, active = null, starting = false, provider = null, showingError = false;

  const statusFor = (button) => button.closest('form, .mc-panel, .rm')?.querySelector('[data-dictation-status]') || $('[data-dictation-status]');
  const say = (button, text, { loud = false, error = false } = {}) => {
    const out = statusFor(button);
    showingError = error;
    if (out) { out.textContent = text; out.hidden = !text; }
    if (loud && text) announce(text);
  };
  const paint = (button, on) => { button.setAttribute('aria-pressed', String(on)); const label = button.querySelector('.rm-mic-label'); const word = on ? 'Stop dictation' : 'Dictate'; if (label) label.textContent = word; else button.textContent = word; };
  const headers = () => ({ 'X-CSRF-Token': page.csrf_token, 'X-Record-Mode': recordMode() });
  const busy = () => Boolean(active || starting);

  async function available() {
    try {
      const r = await fetch(`${api}/dictation/capabilities`, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
      const body = await r.json().catch(() => ({}));
      if (!r.ok || !body.ok || !(body.providers || []).length) return null;
      return body.providers[0].label || body.providers[0].provider || 'the speech provider';
    } catch (e) { return null; }
  }

  // Ends the attempt in every state: loading the code, asking the provider, connected, or finishing.
  function stop(reason, { quiet = false } = {}) {
    session += 1;
    const current = active;
    active = null;
    starting = false;
    if (!current) return;
    try { current.controller?.cancel(reason); } catch (e) { /* already closed */ }       // closes the microphone now; only the owner's own finish waits for the last words
    current.box.readOnly = false;
    paint(current.button, false);
    if (!quiet) say(current.button, WORDS[reason] || '', { loud: Boolean(WORDS[reason]) });
  }

  // Tapping the button again finishes: the shared controller commits what was said and its final text is written before the session closes. Nothing else is cancelled.
  function finish() {
    const current = active;
    if (!current || current.finishing) return;
    if (!current.controller) { stop('user_ended', { quiet: true }); return; }      // still loading: nothing was said yet
    current.finishing = true;
    say(current.button, 'Finishing…');
    try { void current.controller.stop('user_ended'); } catch (e) { stop('user_ended', { quiet: true }); }
  }

  // A room that is paused or closed takes no new writing; the Rooms page publishes its state on the box (data-room-state). Boxes without it are always writable.
  const writable = (box) => !box.disabled && (box.dataset.roomState === undefined || box.dataset.roomState === 'active');

  async function start(button) {
    const box = $(button.dataset.dictate);
    if (!box || box.disabled) return;
    if (!writable(box)) { say(button, 'Dictation is not available while this room is paused or closed.', { loud: true, error: true }); return; }
    if (!live.known) { say(button, WORDS.record_unknown, { loud: true, error: true }); return; }
    if (live.off) { say(button, 'Dictation is not built for Private yet. Go back on the record to use it; typing in Private works as before.', { loud: true, error: true }); return; }
    document.dispatchEvent(new CustomEvent('guild:mic-claim', { detail: { who: 'dictation' } }));      // MC Voice yields the microphone
    starting = true;
    const mine = ++session;
    const current = () => mine === session;
    const mark = { button, box, controller: null, wrote: false };
    active = mark;
    box.readOnly = true;                                                   // from this instant the box cannot change under the words about to be written
    paint(button, true);
    say(button, `Connecting… Your speech is sent to ${provider} to be turned into text and put in this box. Nothing is sent as a message.`);
    // The words go where the cursor was (or over the selection), with a space only where a word would otherwise touch another.
    const from = box.selectionStart ?? box.value.length, to = box.selectionEnd ?? from;
    const before = box.value.slice(0, from), after = box.value.slice(to);
    const write = (memo) => {
      if (!current()) return;
      if (!memo && !mark.wrote) return;                                   // nothing was heard: the draft and the selection stay exactly as they were
      mark.wrote = mark.wrote || Boolean(memo);
      const lead = before && memo && !/\s$/.test(before) ? ' ' : '';
      const trail = after && memo && !/^\s/.test(after) ? ' ' : '';
      box.value = before + lead + memo + trail + after;
      const caret = (before + lead + memo + trail).length;
      try { box.setSelectionRange(caret, caret); } catch (e) { /* not selectable */ }
      box.dispatchEvent(new Event('input', { bubbles: true }));            // the page's own handling (draft keeping, length check, mentions) sees an ordinary edit
    };
    try {
      const { RealtimeMemoController } = await import(`${api}/dictation/static/realtime-memo-controller.js`);
      if (!current()) return;
      const guarded = (fn) => (...args) => { if (current()) fn(...args); };
      const controller = new RealtimeMemoController({
        bootstrapUrl: `${api}/dictation/bootstrap`,
        provider: 'openai',
        initialText: '',
        requestHeaders: headers,
        onStateChange: guarded((state) => {
          if (state === 'listening') { box.readOnly = true; if (!showingError) say(button, 'Listening… tap Stop dictation when you have finished.'); }
          else if (state === 'reconnecting') say(button, 'Reconnecting…');
          else if (state === 'finalizing') say(button, 'Finishing…');
          else if (state === 'stopped' || state === 'error') { if (active === mark) { active = null; box.readOnly = false; paint(button, false); if (state === 'stopped') say(button, ''); } }
        }),
        onTranscript: guarded((memo) => write(memo)),
        onWarning: guarded(() => say(button, 'Dictation will stop in about two minutes.', { loud: true })),
        onError: guarded((e) => say(button, `Dictation: ${(e && (e.detail || e.reason)) || 'it could not continue'}`, { loud: true, error: true })),
      });
      mark.controller = controller;
      await controller.start();
    } catch (e) {
      if (current()) { stop('failed', { quiet: true }); say(button, `Dictation could not start${e && e.message ? `: ${e.message}` : ''}. Typing still works.`, { loud: true, error: true }); }
    } finally {
      if (current()) starting = false;
    }
  }

  for (const button of buttons) {
    button.addEventListener('click', () => {
      if (active && active.button === button) { finish(); return; }
      if (busy()) stop('field_changed', { quiet: true });
      void start(button);
    });
  }
  document.addEventListener('guild:mic-claim', (e) => { if (e.detail?.who !== 'dictation' && busy()) stop('user_ended', { quiet: true }); });
  onChange(() => { if (busy() && (live.off || !live.known)) stop(live.off ? 'private' : 'record_unknown'); });
  new MutationObserver(() => { if (active && active.box.closest('[data-mc-panel]') && document.body.dataset.mcMode === 'pill') stop('minimized'); }).observe(document.body, { attributes: true, attributeFilter: ['data-mc-mode'] });
  new MutationObserver(() => { if (busy() && document.body.dataset.signedOut === 'true') stop('signed_out'); }).observe(document.body, { attributes: true, attributeFilter: ['data-signed-out'] });
  for (const button of buttons) {                       // a box that becomes unavailable (a closed room, a signed-out page) ends dictation into it
    const box = $(button.dataset.dictate);
    if (box) new MutationObserver(() => { if (active && active.box === box && !writable(box)) stop('unavailable'); }).observe(box, { attributes: true, attributeFilter: ['disabled', 'data-room-state'] });
    // (4) A manual send ends dictation first: the microphone closes now, the text written so far is what is sent, and no later word can come back into a cleared box.
    if (box) {
      const endForSend = () => { if (active && active.box === box) stop('sent', { quiet: true }); };
      box.form?.addEventListener('submit', endForSend, true);
      box.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) endForSend(); }, true);
    }
  }
  window.addEventListener('pagehide', () => { if (busy()) stop('pagehide', { quiet: true }); });

  available().then((label) => { if (label) { provider = label; for (const button of buttons) button.hidden = false; } });
}
