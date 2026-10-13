// Speaking with Master Craftsman (MC voice, v1). Off unless the server says so (data-mc-voice) AND the capabilities call answers.
//
// Chained, on the shared Confer controller: the microphone goes straight to the speech provider for TRANSCRIPTION only; each final transcript is submitted through the
// page's ordinary kept-note path (conversation.js submitVoiceUtterance), so Master Craftsman himself answers; then his kept reply is read aloud from the server's record.
// The controller's transcript is the owner's request, never a model's paraphrase. Nothing here stores audio or a provider transcript; the page keeps only what the note
// path keeps. On the record only: going Private, an unknown record mode, minimizing, or leaving the page ends the session first, including one still starting.
//
// What is and is not known up front: the capabilities call says the shared transport supports this provider, NOT that a key is configured. A missing or refused key shows
// as a plain failure when the session is started ("Voice could not start"); typing keeps working.
import { $, announce } from './dom.js';
import { live, onChange } from './state.js';
import { recordMode } from './api.js';
import { submitVoiceUtterance, panelMode } from './conversation.js';

const WORDS = {
  private: 'Voice stopped: it is not built for Private yet. Typing in Private works as before.',
  record_unknown: 'Voice stopped: this tab does not know whether you are on the record.',
  minimized: 'Voice stopped: the chat was minimized.',
  limit: 'Voice stopped: the session reached its time limit.',
};

export function initMcVoice(page) {
  const button = $('[data-mc-voice-btn]');
  const status = $('[data-mc-voice-status]');
  if (!button || !status || document.body.dataset.mcVoice !== 'true') return;
  const api = page.urls.api;
  // `session` numbers the attempt: stop() moves it on, and every callback of an older attempt (a late transcript, error, state change or answer) is then ignored.
  let controller = null, starting = false, session = 0, provider = null, showingError = false;

  const say = (text, { loud = false, error = false } = {}) => { showingError = error; status.textContent = text; status.hidden = !text; if (loud && text) announce(text); };
  const paint = (on) => { button.setAttribute('aria-pressed', String(on)); button.textContent = on ? 'Stop voice' : 'Voice'; };
  const headers = () => ({ 'X-CSRF-Token': page.csrf_token, 'X-Record-Mode': recordMode() });
  const busy = () => Boolean(controller || starting);

  async function available() {
    try {
      const r = await fetch(`${api}/mc/voice/capabilities`, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
      const body = await r.json().catch(() => ({}));
      if (!r.ok || !body.ok || !(body.providers || []).length) return null;
      return body.providers[0].label || body.providers[0].provider || 'the speech provider';
    } catch (e) { return null; }
  }

  // Ends the attempt in every state: still loading the code, still asking the provider, connected, or working on a turn.
  function stop(reason, { quiet = false } = {}) {
    session += 1;
    const c = controller;
    controller = null;
    starting = false;
    if (c) { try { c.stop(reason); } catch (e) { /* already closed */ } }
    paint(false);
    if (!quiet) say(WORDS[reason] || '', { loud: Boolean(WORDS[reason]) });
  }

  async function start() {
    if (busy()) return;
    if (!live.known) { say(WORDS.record_unknown, { loud: true, error: true }); return; }
    if (live.off) { say('Voice is not built for Private yet. Go back on the record to use it; typing in Private works as before.', { loud: true, error: true }); return; }
    document.dispatchEvent(new CustomEvent('guild:mic-claim', { detail: { who: 'voice' } }));        // dictation yields the microphone
    starting = true;
    const mine = ++session;
    const current = () => mine === session;
    paint(true);
    say(`Connecting… Your speech is sent to ${provider} to be turned into text, and Master Craftsman's replies are read aloud by the same service.`);
    try {
      const { ConferVoiceController } = await import(`${api}/mc/voice/static/realtime-confer-controller.js`);
      if (!current()) return;                        // stopped while the code loaded
      class McVoiceController extends ConferVoiceController {
        // The shared controller would POST the transcript to a turn URL and play a server speech URL. Here the transcript goes through the page's own note path and
        // the kept reply is read from the record, so only the submit step is replaced; the session, transcription, duplicate-item and stale-generation handling are the controller's.
        async _submitTurn(text, requestId) {
          if (!this._active || !current()) return;       // a queued utterance of a stopped attempt is dropped, not sent
          this._onUserTurn(text);
          this._onStateChange('thinking');
          const result = await submitVoiceUtterance(text, requestId);
          if (!this._active || !current()) return;       // stopped, minimized, Private, or a new attempt since: nothing is spoken
          if (!result.ok) {
            this._onStateChange('listening');
            this._onError({ reason: result.reason, detail: result.message });
            return;
          }
          const c = page.conversation ? `?c=${encodeURIComponent(page.conversation.id)}` : '';
          await this._play(`${api}/mc/voice/speech/${encodeURIComponent(result.note_request_id)}${c}`);
        }

      }
      const guarded = (fn) => (...args) => { if (current()) fn(...args); };     // nothing from an older attempt repaints the page
      const c = new McVoiceController({
        capabilitiesUrl: `${api}/mc/voice/capabilities`,
        bootstrapUrl: `${api}/mc/voice/bootstrap`,
        turnUrl: null,
        conversationId: page.conversation ? page.conversation.id : 'owner',
        requestHeaders: headers,
        onStateChange: guarded((state, info) => {
          if (state === 'listening') { if (!showingError) say('Listening… say your message; I will send it when you pause.'); }   // a problem stays on screen until the next turn
          else if (state === 'thinking') say('Working on it…');
          else if (state === 'speaking') say('Speaking…');
          else if (state === 'reconnecting') say('Reconnecting…');
          else if (state === 'interrupted') say('Listening…');
          else if (state === 'stopped' && info && info.reason === 'duration_limit') stop('limit');
        }),
        onProvisionalTranscript: guarded((text) => { if (text) say(`Hearing: ${text}`); }),
        onWarning: guarded(() => say('Voice will stop in about two minutes.', { loud: true })),
        onError: guarded((e) => {
          say((e && (e.detail || e.reason)) ? `Voice: ${e.detail || e.reason}` : 'Voice could not continue.', { loud: true, error: true });
          if (e && ['start_failed', 'microphone_denied', 'reconnect_failed'].includes(e.reason)) stop('failed', { quiet: true });
        }),
      });
      controller = c;
      await c.start('openai');
    } catch (e) {
      if (current()) { stop('failed', { quiet: true }); say(`Voice could not start${e && e.message ? `: ${e.message}` : ''}. Typing still works.`, { loud: true, error: true }); }
    } finally {
      if (current()) starting = false;
    }
  }

  button.addEventListener('click', () => { if (busy()) stop('user_ended'); else start(); });
  document.addEventListener('guild:mic-claim', (e) => { if (e.detail?.who !== 'voice' && busy()) stop('user_ended', { quiet: true }); });
  onChange(() => { if (busy() && (live.off || !live.known)) stop(live.off ? 'private' : 'record_unknown'); });
  new MutationObserver(() => { if (busy() && panelMode() === 'pill') stop('minimized'); }).observe(document.body, { attributes: true, attributeFilter: ['data-mc-mode'] });
  window.addEventListener('pagehide', () => { if (busy()) stop('pagehide', { quiet: true }); });

  available().then((label) => { if (label) { provider = label; button.hidden = false; } });
}
