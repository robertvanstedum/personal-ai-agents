// Which path Voice takes is the capabilities answer's: direct_voice present -> the direct path (mc_voice_direct.js, Spec 164
// v0.3, the provider speaking directly with MC's typed turn behind each consult); else managed_realtime present -> the managed
// path below; else "Voice unavailable" and nothing is sent. A path that was offered and failed says why; no other is tried.
// Speaking with Master Craftsman (MC voice, v1). Off unless the server says so (data-mc-voice) AND the capabilities call answers.
//
// Chained, on the shared Confer controller: the microphone goes straight to the speech provider for TRANSCRIPTION only; each final transcript is submitted through the
// page's ordinary kept-note path. Without managed voice, the button says Voice unavailable and sends nothing.
// The controller's transcript is the owner's request, never a model's paraphrase. Nothing here stores audio or a provider transcript; the page keeps only what the note
// path keeps. On the record only: going Private, an unknown record mode, minimizing, or leaving the page ends the session first, including one still starting.
//
// What is and is not known up front: the capabilities call says the shared transport supports this provider, NOT that a key is configured. A missing or refused key shows
// as a plain failure when the session is started ("Voice could not start"); typing keeps working.
import { $, announce } from './dom.js';
import { paintVoiceButton } from './voice_button.js';
import { live, onChange } from './state.js';
import { recordMode } from './api.js';
import { panelMode, mergeNotes } from './conversation.js';

const WORDS = {
  private: 'Voice stopped: it is not built for Private yet. Typing in Private works as before.',
  record_unknown: 'Voice stopped: this tab does not know whether you are on the record.',
  minimized: 'Voice stopped: the chat was minimized.',
  limit: 'Voice stopped: the session reached its time limit.',
};
const STATUS_CLEAR_MS = 5000;        // #420: "Voice ended…" clears after about five seconds, or at the next send

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
  let controller = null, starting = false, session = 0, provider = null, showingError = false, managed = null, direct = null, directMod = null, directSession = null, directThread = null, transcriptEnding = null, endedAttempt = null, restartBlocked = false, ending = null, transcriptPaint = null, transcriptReveal = null, transcriptCancel = null, transcriptEpoch = 0;

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
  let clearing = null;
  const say = (text, { loud = false, error = false, transient = false } = {}) => {
    showingError = error; status.textContent = text; status.hidden = !text; draw(); if (loud && text) announce(text);
    if (clearing) window.clearTimeout?.(clearing);
    clearing = null;
    // An ended line is a passing note (#420); an error stays until the next action.
    if (transient && text && typeof window.setTimeout === 'function') clearing = window.setTimeout(() => { if (status.textContent === text && !showingError) say(''); }, STATUS_CLEAR_MS);
  };
  const paint = (on) => { pressed=on;button.setAttribute('aria-pressed', String(on));draw();button.disabled=restartBlocked||Boolean(ending); };
  const headers = () => ({ 'Content-Type':'application/json', 'X-CSRF-Token': page.csrf_token, 'X-Record-Mode': recordMode() });
  const busy = () => Boolean(controller || starting || ending);

  // The Voice choice offers what this host can actually run (the capabilities answer) and plainly marks the rest unavailable;
  // nothing is substituted silently, and the choice never changes an active session (owner, 18 Oct).
  const PROVIDER_KEY = 'minimoi.voice.provider';
  function offerProviders(body) {
    const select = $('[data-mc-voice-provider]');
    if (!select || !select.options) return;
    const usable = new Set((body.providers || []).map(p => p && p.provider));
    if (managed && managed.provider) usable.add(managed.provider);
    const options = Array.from(select.options);
    for (const option of options) {
      const name = option.dataset.name || option.textContent.split(' · ')[0];
      option.dataset.name = name;
      option.disabled = !usable.has(option.value);
      option.textContent = option.disabled ? `${name} · not available yet` : name;
    }
    let saved = null;
    try { saved = window.localStorage.getItem(PROVIDER_KEY); } catch (e) { /* the choice simply is not remembered */ }
    const pick = options.find(o => o.value === saved && !o.disabled) || options.find(o => o.value === body.default_provider && !o.disabled) || options.find(o => !o.disabled);
    if (pick) select.value = pick.value;
    else select.disabled = true;                // nothing this host can run: no choice to make, and none faked
  }

  async function available() {
    try {
      const r = await fetch(`${api}/mc/voice/capabilities`, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
      const body = await r.json().catch(() => ({}));
      direct = body.direct_voice && typeof body.direct_voice === 'object' && (body.direct_voice.providers || []).length ? body.direct_voice : null;
      if (!r.ok || !body.ok || !((body.providers || []).length || direct)) return null;
      managed=body.managed_realtime || null;
      if (managed || direct) $('[data-mc-managed-settings]').hidden=false;
      offerProviders(direct ? { providers: direct.providers, default_provider: direct.default_provider } : body);
      if (direct) { initDirect(); const first = direct.providers[0]; return first.label || first.provider; }
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
      if(!quiet&&!showingError)say(WORDS[reason]||(direct?DIRECT_ENDED:'Voice ended. Agent work already accepted may still finish.'),{loud:Boolean(WORDS[reason]),transient:!WORDS[reason]});
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
    if (direct) {await startDirect(mine,current);return;}
    if (managed) {await startManaged(mine,current);return;}
    say('Voice unavailable. Live voice is not on for this Master Craftsman, so nothing was sent. Typing still works.', { loud: true, error: true });
    setPhase('unavailable');
    starting = false;
    paint(false);
  }

  // The direct path (UI_CONTRACT_chunk_B.md). Its own file is loaded only now that the capabilities answer offered it.
  const DIRECT_ENDED = 'Voice ended. What was said is in the conversation.';
  const loadDirect = () => directMod ? Promise.resolve(directMod) : import('./mc_voice_direct.js').then((m) => (directMod = m));
  const liveBar = () => $('[data-mc-voice-live]');
  async function revealDirect() {
    const cid = page.conversation?.id;
    if (!cid || !directThread) return;
    try {
      const r = await fetch(`${api}/notes?limit=200&conversation=${encodeURIComponent(cid)}`, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
      const body = await r.json().catch(() => ({}));
      if (!r.ok || page.conversation?.id !== cid) return;
      mergeNotes(body.notes || []);
      directThread.paint(body.voice_sessions, body.notes);
    } catch (e) { /* the thread shows it on the next load */ }
  }
  async function initDirect() {
    const choice = $('[data-mc-voice-transcript-choice]');
    if (choice?.parentElement) choice.parentElement.hidden = true;          // no running text on this path: it is revealed at End
    try {
      const mod = await loadDirect();
      directThread = mod.voiceThread({ doc: document, direct, thread: () => $('[data-mc-thread]'), mine: () => directSession?.sessionId() || null,
        post: mod.jsonPost((...a) => fetch(...a), headers), conversationId: () => page.conversation?.id, reveal: revealDirect });
      directThread.paint(page.floor?.notes?.voice_sessions, page.floor?.notes?.recent);
    } catch (e) { /* Voice says so when it is pressed */ }
    // The thread's voice lines follow the conversation, not only this tab's own End (UI review #7): repainted from every notes
    // read the page already makes, and read afresh when the tab comes back into view.
    document.addEventListener('guild:notes-read', (e) => {
      const read = e.detail || {};
      if (!directThread || !page.conversation?.id || read.conversationId !== page.conversation.id) return;
      mergeNotes((read.notes || []).filter((note) => note && note.voice));
      directThread.paint(read.voice_sessions, read.notes);
    });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) revealDirect(); });
    $('[data-mc-voice-end]')?.addEventListener?.('click', () => { if (directSession?.proposing()) directSession.sumUp(); else if (busy()) stop('user_ended'); });
    $('[data-mc-voice-check]')?.addEventListener?.('click', () => directSession?.check());
    $('[data-mc-voice-sumup]')?.addEventListener?.('click', () => directSession?.sumUp());
    $('[data-mc-voice-keep]')?.addEventListener?.('click', () => directSession?.keepTalking());
  }
  async function startDirect(mine,current) {
    const selector=$('[data-mc-voice-provider]');
    if(selector)selector.disabled=true;
    say('Connecting voice…');
    let handle=null,mineSession=null;
    const mineNow=()=>controller===handle&&handle!==null;
    const ui={
      say:(text,o)=>{if(current()||endedAttempt===mine)say(text,o);},
      phase:(state)=>{if(current())setPhase(state);},
      live:(on)=>{const bar=liveBar();if(bar)bar.hidden=!on||!current();if(!on){const p=$('[data-mc-voice-propose]');if(p)p.hidden=true;}},
      elapsed:(text)=>{const n=$('[data-mc-voice-elapsed]');if(n)n.textContent=text;},
      notice:(text)=>{const n=$('[data-mc-voice-notice]');if(n){n.textContent=text||'';n.hidden=!text;}},
      propose:(on)=>{const p=$('[data-mc-voice-propose]');if(p)p.hidden=!on||!current();if(on&&current())announce('Shall I sum up?');},
      checking:(on)=>{const b=$('[data-mc-voice-check]');if(b)b.disabled=Boolean(on);},
      // The session ended by itself (a refusal, the provider, the summary, the time limit): the button is ready again.
      ended:(words,{error=false}={})=>{
        if(directSession===mineSession)directSession=null;
        if(mineNow()){controller=null;starting=false;endedAttempt=mine;paint(false);setPhase('idle');if(selector)selector.disabled=false;}
        if(words&&(current()||endedAttempt===mine))say(words,{loud:error,error,transient:!error});
      },
    };
    try {
      const mod=await loadDirect();
      if(!current())return;
      const s=mod.directVoice({page,direct,ui,headers,fetch:(...a)=>fetch(...a),reveal:revealDirect,
        loadController:()=>import(`${api}/mc/voice/static/realtime-voice-controller.js`),
        setTimeout:(fn,ms)=>window.setTimeout(fn,ms),clearTimeout:(t)=>window.clearTimeout(t),
        setInterval:(fn,ms)=>window.setInterval(fn,ms),clearInterval:(t)=>window.clearInterval(t)});
      handle={stop:reason=>s.end(reason)};
      controller=handle;directSession=mineSession=s;
      await s.start(selector?.value||direct.default_provider);
    } catch (_) {if(current()){stop('failed',{quiet:true});say('Voice unavailable: the voice page could not load. Typing still works.',{loud:true,error:true});}}
    finally {if(current())starting=false;if(!busy()&&selector)selector.disabled=false;}
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
      afterEnd=true;$('[data-mc-voice-transcript]').open=true;paintTranscript();
      if(savedRead)return savedRead;
      const aborter=new AbortController();transcriptCancel=()=>aborter.abort();
      let timeout;
      savedRead=(async()=>{
        try{
          const request=(async()=>{
            const response=await fetch(`${api}/conversations/${encodeURIComponent(cid)}/voice-transcript`,
              {credentials:'same-origin',cache:'no-store',redirect:'error',headers:{Accept:'text/plain'},signal:aborter.signal});
            if(!response.ok||!/^text\/plain(?:;|$)/i.test(response.headers.get('content-type')||''))throw new Error('saved_transcript_unavailable');
            return response.text();
          })();
          // Bound both network and body reads even when cancellation stalls.
          // A late response cannot paint after the timeout has won.
          const text=await Promise.race([request,new Promise((_,reject)=>{
            timeout=setTimeout(()=>{aborter.abort();reject(new Error('saved_transcript_timeout'));},5000);
          })]);
          // This fixed owner-authorized export is server-bounded to1MiB. Never
          // paint HTML, error bodies or the browser buffer as a saved receipt.
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
            // #420 on every MC voice path (UI review #5): the ended line clears after about five seconds or at the next send.
            if(!showingError)say('Voice ended. Agent work already accepted may still finish.',{transient:true});
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

  $('[data-mc-voice-provider]')?.addEventListener?.('change', (event) => {
    try { window.localStorage.setItem(PROVIDER_KEY, event.target.value); } catch (e) { /* not remembered */ }
  });
  $('[data-mc-voice-transcript-choice]')?.addEventListener?.('change',()=>{
    if($('[data-mc-voice-transcript-choice]').checked)$('[data-mc-voice-transcript]').open=true;
    transcriptPaint?.();
  });
  button.addEventListener('click', () => { if (busy()) stop('user_ended'); else start(); });
  // #420: the next send clears the voice status line, an ended note or an error alike (an error stays until an action).
  $('[data-mc-composer]')?.addEventListener?.('submit', () => { if (!busy() && status.textContent) say(''); });
  document.addEventListener('guild:mic-claim', (e) => { if (e.detail?.who !== 'voice' && busy()) stop('user_ended', { quiet: true }); });
  onChange(() => { if (live.off || !live.known) {transcriptEpoch++;transcriptCancel?.();transcriptCancel=null;transcriptReveal=null;transcriptPaint=null;if(busy())stop(live.off?'private':'record_unknown');const box=$('[data-mc-voice-transcript]');if(box)box.hidden=true;const text=$('[data-mc-voice-transcript-text]');if(text)text.textContent='';} });
  new MutationObserver(() => { if (busy() && panelMode() === 'pill') stop('minimized'); }).observe(document.body, { attributes: true, attributeFilter: ['data-mc-mode'] });
  // A direct session already ending (End pressed, its last post or End unanswered) is told too: it sends End again with
  // keepalive, since stop() returns the ending in progress (UI review #1).
  window.addEventListener('pagehide', () => { directSession?.end('pagehide'); if (busy()) stop('pagehide', { quiet: true }); });

  available().then((label) => { if (label) { provider = label; button.hidden = false; } });
}
