// Optional managed realtime uses the shared controlled-session host and controller.
// The chained behavior below remains the fallback when managed metadata is absent.
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
import { paintVoiceButton } from './voice_button.js';
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
  const exportLink = $('[data-mc-voice-export]');
  const paintExport = () => {
    if (!exportLink) return;
    const allowed = live.known && !live.off && Boolean(page.conversation?.id);
    exportLink.hidden = !allowed;
    if (allowed) exportLink.setAttribute('href', `${api}/conversations/${encodeURIComponent(page.conversation.id)}/voice-transcript`);
    else exportLink.removeAttribute?.('href');
  };
  paintExport();
  onChange(paintExport);

  // `session` numbers the attempt: stop() moves it on, and every callback of an older attempt (a late transcript, error, state change or answer) is then ignored.
  let controller = null, starting = false, session = 0, provider = null, showingError = false, managed = null, transcriptEnding = null, endedAttempt = null, restartBlocked = false, ending = null, transcriptPaint = null, transcriptReveal = null, transcriptCancel = null, transcriptEpoch = 0;

  let visualState = 'idle', pressed = false;
  const draw = () => {
    if (typeof paintVoiceButton === 'function') {
      paintVoiceButton(button, {state: ending ? 'ending' : showingError ? 'error' : pressed ? visualState : 'idle', active: pressed});
    } else {
      // Older hosts/test nodes without the progressive icon helper keep labels.
      button.textContent = pressed ? 'Stop voice' : ending ? 'Ending voice…' : 'Voice';
    }
  };
  const setPhase = state => { visualState = state; draw(); };
  const say = (text, { loud = false, error = false } = {}) => { showingError = error; status.textContent = text; status.hidden = !text; draw(); if (loud && text) announce(text); };
  const paint = (on) => { pressed=on;button.setAttribute('aria-pressed', String(on));draw();button.disabled=restartBlocked||Boolean(ending); };
  const headers = () => ({ 'Content-Type':'application/json', 'X-CSRF-Token': page.csrf_token, 'X-Record-Mode': recordMode() });
  const busy = () => Boolean(controller || starting || ending);

  async function available() {
    try {
      const r = await fetch(`${api}/mc/voice/capabilities`, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
      const body = await r.json().catch(() => ({}));
      if (!r.ok || !body.ok || !(body.providers || []).length) return null;
      managed=body.managed_realtime || null;
      if (managed) $('[data-mc-managed-settings]').hidden=false;
      return body.providers[0].label || body.providers[0].provider || 'the speech provider';
    } catch (e) { return null; }
  }

  // Ends the attempt in every state: still loading the code, still asking the provider, connected, or working on a turn.
  function stop(reason, { quiet = false } = {}) {
    const privateStop=['private','record_unknown','minimized','pagehide'].includes(reason);
    if(privateStop){transcriptEpoch++;transcriptCancel?.();transcriptCancel=null;transcriptReveal=null;transcriptPaint=null;transcriptEnding=null;const box=$('[data-mc-voice-transcript]');if(box)box.hidden=true;const text=$('[data-mc-voice-transcript-text]');if(text)text.textContent='';}
    if(ending)return ending;
    endedAttempt=session;
    transcriptEnding=['user_ended','limit'].includes(reason)?session:null;
    const stoppedSession=++session,c=controller;
    controller=null;starting=false;
    let result;
    try{result=c?.stop(reason);}catch(_){restartBlocked=true;say('Voice termination could not be verified. Do not retry yet; typing still works.',{loud:true,error:true});}
    const finish=()=>{
      if(session!==stoppedSession)return;
      transcriptEnding=null;
      if(['user_ended','limit'].includes(reason))transcriptReveal?.();
      for(const selector of ['[data-mc-voice-provider]','[data-mc-voice-transcript-choice]']){const node=$(selector);if(node)node.disabled=false;}
      paint(false);
      if(!quiet&&!showingError)say(WORDS[reason]||'Voice ended. Agent work already accepted may still finish.',{loud:Boolean(WORDS[reason])});
    };
    if(result&&typeof result.then==='function'){
      const pending=Promise.resolve(result).catch(()=>{
        if(session!==stoppedSession)return;
        restartBlocked=true;say('Voice termination could not be verified. Do not retry yet; typing still works.',{loud:true,error:true});
      }).finally(()=>{if(ending===pending)ending=null;finish();});
      ending=pending;
      paint(false);
      if(!quiet&&!showingError)say('Ending voice… microphone stopped; waiting for termination confirmation.');
      return pending;
    }
    finish();
  }

  async function start() {
    if (busy() || restartBlocked) return;
    if (!live.known) { say(WORDS.record_unknown, { loud: true, error: true }); return; }
    if (live.off) { say('Voice is not built for Private yet. Go back on the record to use it; typing in Private works as before.', { loud: true, error: true }); return; }
    document.dispatchEvent(new CustomEvent('guild:mic-claim', { detail: { who: 'voice' } }));        // dictation yields the microphone
    starting = true;
    const mine = ++session;
    endedAttempt=null;
    const current = () => mine === session;
    setPhase('connecting');
    paint(true);
    if (managed) {await startManaged(mine,current);return;}
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
          if (state === 'listening') { setPhase('listening'); if (!showingError) say('Listening… say your message; I will send it when you pause.'); }   // a problem stays on screen until the next turn
          else if (state === 'thinking') {setPhase('working');say('Working on it…');}
          else if (state === 'speaking') {setPhase('speaking');say('Speaking…');}
          else if (state === 'reconnecting') {setPhase('connecting');say('Reconnecting…');}
          else if (state === 'interrupted') {setPhase('listening');say('Listening…');}
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

  async function startManaged(mine,current) {
    const cid=page.conversation?.id;
    if (!cid) {stop('failed',{quiet:true});say('Open a saved conversation before using voice.',{error:true});return;}
    const selector=$('[data-mc-voice-provider]'),choice=$('[data-mc-voice-transcript-choice]');
    transcriptCancel?.();
    const displayEpoch=++transcriptEpoch;
    let latestTurns=[],afterEnd=false,savedText='',savedRead=null;
    const displayAllowed=()=>transcriptEpoch===displayEpoch&&(current()||endedAttempt===mine)&&!live.off&&live.known&&page.conversation?.id===cid;
    const paintTranscript=()=>{
      if(!displayAllowed())return;
      const shown=afterEnd||Boolean(choice?.checked),box=$('[data-mc-voice-transcript]');
      $('[data-mc-voice-transcript-text]').textContent=afterEnd?(savedText||'Loading saved voice transcript…'):shown?latestTurns.map(t=>`${t.speaker==='user'?'You':'Voice'}: ${t.text}${t.completed?'':' [in progress / partial]'}`).join('\n\n'):'';
      box.hidden=!shown||(!afterEnd&&latestTurns.length===0);
    };
    const revealSaved=()=>{
      if(!displayAllowed())return;
      afterEnd=true;paintTranscript();
      if(savedRead)return savedRead;
      const aborter=new AbortController();transcriptCancel=()=>aborter.abort();
      const timeout=setTimeout(()=>aborter.abort(),5000);
      savedRead=(async()=>{
        try{
          const response=await fetch(`${api}/conversations/${encodeURIComponent(cid)}/voice-transcript`,
            {credentials:'same-origin',cache:'no-store',redirect:'error',headers:{Accept:'text/plain'},signal:aborter.signal});
          if(!response.ok||!/^text\/plain(?:;|$)/i.test(response.headers.get('content-type')||''))throw new Error('saved_transcript_unavailable');
          // This fixed owner-authorized export is server-bounded to1MiB. Never
          // paint HTML, error bodies or the browser buffer as a saved receipt.
          const text=await response.text();
          if(!text||text.length>1100000)throw new Error('saved_transcript_bound');
          if(displayAllowed()){savedText=text;paintTranscript();}
        }catch(_){
          if(displayAllowed()){savedText='Saved voice transcript is unavailable here. The download link can be checked later; no complete transcript is verified.';paintTranscript();}
        }finally{clearTimeout(timeout);}
      })();
      return savedRead;
    };
    transcriptPaint=paintTranscript;transcriptReveal=revealSaved;
    let terminal=false,failed=false;
    selector.disabled=true;choice.disabled=false;
    $('[data-mc-voice-transcript]').hidden=true;$('[data-mc-voice-transcript-text]').textContent='';
    say('Connecting voice…');
    try {
      const {RealtimeVoiceController}=await import(`${api}/mc/voice/static/realtime-voice-controller.js`);
      if (!current()) return;
      const c=new RealtimeVoiceController({managedBootstrap:{sessionUrl:managed.session_url},openingInstruction:null,
        requestHeaders:headers,includeTranscriptAfterEnd:true,
        onStateChange:state=>{
          if(!current())return;
          if(state==='active'&&!terminal&&!failed){setPhase('listening');say('Listening…');}
          if(state==='ending'&&!failed)setPhase('ending');
          if(state==='ended'){
            terminal=true;endedAttempt=mine;controller=null;starting=false;paint(false);
            selector.disabled=false;choice.disabled=false;
            revealSaved();
            if(!showingError)say('Voice ended. Agent work already accepted may still finish.');
          }
        },
        onInputState:phase=>{
          if(!current()||terminal||failed||showingError)return;
          const text={consulting:'Working on that…',speaking:'Speaking…',speech_started:'Listening…',
            speech_stopped:'Understanding your speech…',understood:'Preparing a reply…'}[phase];
          if(text){setPhase(phase==='speech_started'?'listening':phase==='speaking'?'speaking':'working');say(text);}
        },
        onWarning:()=>{if(current()&&!terminal&&!failed)say('Voice will stop in about two minutes.',{loud:true});},
        onStop:()=>{if(current())stop('limit');},
        onFatalError:error=>{
          if(!(current()||endedAttempt===mine))return;
          failed=true;
          if(['voice_session_unresolved','agent_action_unresolved'].includes(error?.reason)){
            restartBlocked=true;button.disabled=true;
            const message=error.reason==='voice_session_unresolved'
              ? 'An earlier voice session has not been confirmed ended. Its status needs verification before voice can start. Typing still works.'
              : 'An earlier agent action needs verification before voice can start. Typing still works.';
            say(message,{loud:true,error:true});
          }else if(error?.reason==='transcript_retention_failed'){
            say('Voice ended, but part of the conversation could not be saved. The visible transcript may help; saved history is incomplete.',{loud:true,error:true});
          }else if(error?.reason==='termination_uncertain'){
            restartBlocked=true;button.disabled=true;
            say('Voice stopped on this page, but provider termination is unconfirmed. Do not retry yet; typing still works.',{loud:true,error:true});
          }else if(!restartBlocked){
            say('Voice stopped. Any agent action already accepted may still finish; check its record. Typing still works.',{loud:true,error:true});
          }
        },
        onTranscriptUpdate:turns=>{
          if(!current()||!displayAllowed())return;
          latestTurns=turns;
          paintTranscript();
        },
        onFinalize:result=>{
          if (!(current()||transcriptEnding===mine||endedAttempt===mine)||!displayAllowed())return;
          latestTurns=result.turns||[];
          paintTranscript();
        }});
      controller={stop:reason=>c.endSession(reason)};
      await c.startSession({provider:selector.value,conversation_id:cid});
    } catch (_) {if(current()){stop('failed',{quiet:true});say(restartBlocked?'The earlier voice session needs verification before another can start. Typing still works.':'Voice could not start. No new session was confirmed; typing still works.',{loud:true,error:true});}}
    finally {if(current())starting=false;if(!busy()){selector.disabled=false;choice.disabled=false;}}
  }

  $('[data-mc-voice-transcript-choice]')?.addEventListener?.('change',()=>transcriptPaint?.());
  button.addEventListener('click', () => { if (busy()) stop('user_ended'); else start(); });
  document.addEventListener('guild:mic-claim', (e) => { if (e.detail?.who !== 'voice' && busy()) stop('user_ended', { quiet: true }); });
  onChange(() => { if (live.off || !live.known) {transcriptEpoch++;transcriptCancel?.();transcriptCancel=null;transcriptReveal=null;transcriptPaint=null;if(busy())stop(live.off?'private':'record_unknown');const box=$('[data-mc-voice-transcript]');if(box)box.hidden=true;const text=$('[data-mc-voice-transcript-text]');if(text)text.textContent='';} });
  new MutationObserver(() => { if (busy() && panelMode() === 'pill') stop('minimized'); }).observe(document.body, { attributes: true, attributeFilter: ['data-mc-mode'] });
  window.addEventListener('pagehide', () => { if (busy()) stop('pagehide', { quiet: true }); });

  available().then((label) => { if (label) { provider = label; button.hidden = false; } });
}
