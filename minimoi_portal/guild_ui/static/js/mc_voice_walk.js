// The walk (spec v1.1). The page is a telephone: no transcript on screen, no tone, no words of its own.
// MC's finished sentence is held by the server. A later bare yes writes the note. The model says "Noted" only after a receipt.

export async function startMcWalk({ api, headers, current, say, setPhase }) {
  const { OpenAIWebRTCAdapter } = await import(`${api}/mc/voice/static/adapters/openai-webrtc-adapter.js`);
  const adapter = new OpenAIWebRTCAdapter();
  const turns = [];
  let sessionId = null;
  let active = true;
  const post = (path, body) => fetch(`${api}/mc/voice/walk/${path}`, {
    method: 'POST', credentials: 'same-origin', headers: headers(), body: JSON.stringify(body),
  }).then((r) => r.json().catch(() => ({})));

  try {
    const lock = navigator.wakeLock?.request ? await navigator.wakeLock.request('screen') : null;
    adapter._wakeLock = lock;
  } catch (_) { /* a locked phone may still drop the mic; test 13 is the evidence */ }

  adapter.on('connected', () => { if (current()) { setPhase('listening'); say('Listening…'); } });
  adapter.on('speech_started', () => { if (current()) setPhase('listening'); });
  adapter.on('output_transcript', (ev) => {
    if (!active || !current() || !ev?.completed || !ev.text || !sessionId) return;
    turns.push({ role: 'mc', text: ev.text });
    post('hold', { session: sessionId, sentence: ev.text });
  });
  adapter.on('input_transcript', async (ev) => {
    if (!active || !current() || !ev?.completed || !ev.text || !sessionId) return;
    turns.push({ role: 'user', text: ev.text });
    const body = await post('utterance', { session: sessionId, text: ev.text });
    if (body?.say && body.receipt) {
      adapter.sendContinuationInstruction(
        `The register returned receipt ${body.receipt}. Say only: Noted.`,
        'noted',
        'system',
      );
    }
  });
  adapter.on('function_call', async (ev) => {
    if (!active || !ev?.call_id) return;
    let query = '';
    try {
      const args = typeof ev.arguments === 'string' ? JSON.parse(ev.arguments || '{}') : (ev.arguments || {});
      query = args.query || '';
    } catch (_) { query = ''; }
    const body = await post('tool', { name: ev.name, query });
    adapter.sendFunctionResult(ev.call_id, JSON.stringify(body || {}), 'read');
  });
  adapter.on('interrupted', () => { if (current()) setPhase('listening'); });

  const started = await fetch(`${api}/mc/voice/bootstrap`, {
    method: 'POST', credentials: 'same-origin', headers: headers(), body: JSON.stringify({ provider: 'openai' }),
  });
  const cred = await started.json().catch(() => ({}));
  if (!started.ok || !cred.client_secret) throw new Error(cred.error || 'could not start');
  sessionId = cred.voice_session_id;
  adapter.prepareSession(cred);
  await adapter.connect(cred);
  await adapter.start();
  const startedAt = Date.now();
  const warnMs = (cred.warning_minutes || 45) * 60 * 1000;
  const maxMs = (cred.max_minutes || 60) * 60 * 1000;
  let warned = false;
  let timer = null;
  function stop(reason) {
    if (!active) return;
    active = false;
    if (timer) clearInterval(timer);
    try { adapter._wakeLock?.release?.(); } catch (_) { /* already released */ }
    adapter.end(reason);
    if (sessionId) post('end', { session: sessionId, turns });
  }
  timer = setInterval(() => {
    if (!active) { clearInterval(timer); return; }
    const elapsed = Date.now() - startedAt;
    if (elapsed >= maxMs) stop('duration_limit');
    else if (!warned && elapsed >= warnMs) {
      warned = true;
      if (current()) say('This call will close itself after an hour.');
      adapter.sendContinuationInstruction(
        'Say one short sentence, then go on with the conversation: this call will close itself after an hour.',
        'duration_warning',
        'system',
      );
    }
  }, 5000);

  return { stop };
}
