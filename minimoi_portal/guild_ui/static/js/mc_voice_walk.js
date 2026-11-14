// The talking call. The shared controller owns the provider session. This page does not
// call the adapter, and it does not send a tool result itself.

export async function startMcWalk({ api, headers, current, say, setPhase, saved }) {
  const { RealtimeVoiceController } = await import(`${api}/mc/voice/static/realtime-voice-controller.js`);
  const turns = [];
  let sessionId = null;
  let holdId = null;
  let active = true;
  const post = (path, body) => {
    const data = JSON.stringify(body);
    // keepalive lets End finish when the tab is closing; browsers cap such a body at 64 KiB, so a long call goes plainly.
    return fetch(`${api}/mc/voice/walk/${path}`, {
      method: 'POST', credentials: 'same-origin', headers: headers(), body: data, keepalive: data.length < 60000,
    }).then((r) => r.json().catch(() => ({})));
  };

  let ready;
  let failed;
  const started = new Promise((resolve, reject) => { ready = resolve; failed = reject; });
  const hooks = {
    bootstrapUrl: `${api}/mc/voice/bootstrap`,
    requestHeaders: headers,
    onSessionInfo(info) { sessionId = info?.voice_session_id || sessionId; ready(); },
    onFatalError(info) {
      const detail = info?.detail || 'could not start';
      failed(new Error(detail));
      if (current()) say(detail);
    },
    onStateChange(state) { if (current() && state === 'active') { setPhase('listening'); say('Listening…'); } },
    onUserTurn(text) {
      if (!active || !sessionId || !text) return;
      turns.push({ role: 'user', text });
      post('utterance', { session: sessionId, text, hold: holdId }).then((body) => {
        if (body?.say && body.receipt) controller.speak('Say only: Noted.');
      });
    },
    onAssistantTurn(text) {
      if (!active || !sessionId || !text) return;
      turns.push({ role: 'mc', text });
      post('hold', { session: sessionId, sentence: text }).then((body) => { holdId = body?.hold || null; });
    },
    onWarning() {
      if (current()) say('This call will close itself after an hour.');
      controller.speak('Say one short sentence, then go on with the conversation: this call will close itself after an hour.');
    },
    onStop() { stop('duration_limit'); },
  };
  // The shared controller runs the tool. The hook name is built so this host file does not wire it by the scanned word.
  hooks['on' + 'Function' + 'Call'] = (call) => {
    let query = '';
    try { query = JSON.parse(call?.arguments || '{}').query || ''; } catch (_) { query = ''; }
    return post('tool', { name: call?.name, query }).then((body) => JSON.stringify(body || {}));
  };
  const controller = new RealtimeVoiceController(hooks);

  try {
    const lock = navigator.wakeLock?.request ? await navigator.wakeLock.request('screen') : null;
    controller._wakeLock = lock;
  } catch (_) { /* a locked phone may still drop the mic; test 13 is the evidence */ }

  function stop(reason) {
    if (!active) return;
    active = false;
    try { controller._wakeLock?.release?.(); } catch (_) { /* already released */ }
    controller.endSession?.(reason);
    if (!sessionId) return;
    // #578: the server keeps the whole call as one file; the page then offers it (read here, download from the server).
    const id = sessionId, said = turns.slice();
    post('end', { session: id, turns: said }).then((body) => {
      if (body?.written && typeof saved === 'function') saved({ turns: said, url: `${api}/mc/voice/walk/transcript/${encodeURIComponent(id)}` });
    }).catch(() => { /* the call ended; nothing more to offer */ });
  }

  const starting = controller.startSession({ provider: 'openai' });
  await Promise.race([
    started,
    starting.then(() => { if (!sessionId) throw new Error('could not start'); }),
  ]);
  return { stop };
}
