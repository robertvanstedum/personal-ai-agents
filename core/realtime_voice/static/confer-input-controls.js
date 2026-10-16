import { RealtimeMemoController } from './realtime-memo-controller.js';

// Shared insertion-only dictation. A host owns submission; this binding never
// sends a conversation turn or speaks a reply. Existing drafts stay in place.
export function bindConferDictation({button,input,bootstrapUrl,requestHeaders,
  beforeStart=async()=>{},onStatus=()=>{},onBusy=()=>{},MemoController=RealtimeMemoController}) {
  let controller=null,generation=0,starting=false,available=false,stopping=null;
  const paint=active=>{
    button.textContent=active?'Stop dictation':'Dictate';
    button.setAttribute('aria-pressed',String(active));
    button.setAttribute('aria-label',active?'Stop dictation':'Dictate into the message draft');
    input.readOnly=active;
    onBusy(active);
  };
  async function stop(reason='user_ended') {
    if(reason==='pagehide'){generation++;controller?.cancel(reason);controller=null;stopping=null;paint(false);return;}
    if(stopping)return stopping;
    if(!controller){generation++;paint(false);return;}
    const old=controller,mine=generation;
    const pending=(async()=>{
      try {await old.stop(reason);}
      finally {
        if(mine===generation){if(controller===old)controller=null;generation++;paint(false);}
        if(stopping===pending)stopping=null;
      }
    })();
    stopping=pending;
    return pending;
  }
  button.addEventListener('click',async()=>{
    if(controller){await stop();return;}
    if(starting||!available||input.disabled)return;
    starting=true;const mine=++generation;paint(true);
    try {
      await beforeStart();
      if(mine!==generation)return;
      controller=new MemoController({bootstrapUrl,provider:'openai',initialText:input.value,requestHeaders,
        onTranscript:text=>{if(mine===generation)input.value=text;},
        onStateChange:state=>{
          if(mine!==generation)return;
          const active=!['stopped','error'].includes(state);paint(active);
          if(!active)controller=null;
          onStatus(active?'Dictating into your draft. Stop, review, then Send.':'Dictation stopped. Review your draft, then Send.');
        },
        onWarning:()=>{if(mine===generation)onStatus('Dictation will stop in about two minutes.');},
        onError:error=>{if(mine===generation)onStatus(`Dictation stopped: ${error.detail||error.reason||'unavailable'}. Your draft is kept.`);},
      });
      await controller.start();
    } catch(error) {
      if(mine===generation){await stop('start_failed');onStatus(`Dictation could not start: ${error.message}. Your draft is kept.`);}
    } finally {starting=false;}
  });
  paint(false);
  return {stop,setAvailable:value=>{available=Boolean(value);button.disabled=!available;}};
}

// Realtime readiness is independent of the optional chained-voice service.
export function conferVoiceAvailability({legacyReady,realtimeReady,mode}) {
  if(mode){
    const chained=Array.from(mode.options).find(option=>option.value==='chained');
    if(chained)chained.disabled=!legacyReady;
    if(!legacyReady&&realtimeReady)mode.value='realtime';
  }
  return {voiceReady:Boolean(legacyReady||realtimeReady),dictationReady:Boolean(legacyReady)};
}
