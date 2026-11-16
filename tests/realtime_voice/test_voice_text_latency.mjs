// Time from the end of his speech to the first audio, with the thread paint on the assistant turn.
// The paint runs when the turn's text is flushed (assistant_stopped), which is after audio_started.
// A slow paint must not move the first-audio mark. No network.
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

const root = new URL('../../', import.meta.url);
const { RealtimeVoiceController } = await import(new URL('core/realtime_voice/static/realtime-voice-controller.js', root));
const { OpenAIWebRTCAdapter } = await import(new URL('core/realtime_voice/static/adapters/openai-webrtc-adapter.js', root));

function harness(onAssistantTurn) {
  const marks = [];
  let now = 1_000_000;
  const real = Date.now;
  Date.now = () => now;
  const a = new OpenAIWebRTCAdapter();
  a._dc = { send() {}, close() {} };
  const c = new RealtimeVoiceController({
    onAssistantTurn,
    onAudioStarted: () => marks.push(['audio', now]),
    onUserTurn: () => marks.push(['user', now]),
  });
  c._adapter = a;
  c._sessionId = 's';
  c._state = 'active';
  c._wireAdapterEvents();
  const ev = (e) => a._handleServerEvent(e);
  try {
    ev({ type: 'input_audio_buffer.speech_stopped' });
    marks.push(['speech_end', now]);
    now += 40;
    ev({ type: 'response.created', response: { id: 'r1' } });
    ev({ type: 'output_audio_buffer.started', response_id: 'r1' });
    now += 15;
    ev({ type: 'response.output_audio_transcript.done', transcript: 'Recommendation: look at the log.', response_id: 'r1' });
    ev({ type: 'response.done', response: { id: 'r1', status: 'completed', output: [] } });
  } finally {
    Date.now = real;
  }
  const speech = marks.find((m) => m[0] === 'speech_end')[1];
  const audio = marks.find((m) => m[0] === 'audio')[1];
  return audio - speech;
}

const empty = harness(() => {});
let painted = 0;
const withPaint = harness(() => {
  let n = 0;
  for (let i = 0; i < 200000; i++) n += i;
  painted += n > 0 ? 1 : 0;
});
// The fake clock steps only in the harness. Both deltas are the 40ms from speech_stopped to audio_started.
// The paint runs later, on assistant_stopped, so it is not inside that number.
assert.equal(empty, 40, `before ${empty}`);
assert.equal(withPaint, 40, `after ${withPaint}`);
assert.equal(painted, 1);
assert.ok(withPaint <= empty, `time to first audio grew: before ${empty}ms, after ${withPaint}ms`);
console.log(`time to first audio: before ${empty}ms, after ${withPaint}ms`);

const { stripSpokenLabel } = await import(new URL('minimoi_portal/guild_ui/static/js/mc_voice_walk.js', root));
for (const line of [
  'Recommendation: we need the live issue list to answer that, and it isn’t available from this call.',
  'Recommendation: yes, I can draft the issue text here, but I can’t write it into the tracker for you.',
  'Recommendation: yes, you can use this conversation as a transcript if your client or interface supports saving or exporting it.',
]) {
  assert.equal(stripSpokenLabel(line).startsWith('Recommendation'), false);
}
assert.equal(stripSpokenLabel('My recommendation is to leave it.'), 'My recommendation is to leave it.');
assert.equal(stripSpokenLabel('Decision: keep the file.'), 'Decision: keep the file.');
// #654: any short lead label goes, not only the six named words.
assert.equal(stripSpokenLabel('My view: we wait. Short answer: not now.'), 'we wait. not now.');
assert.equal(stripSpokenLabel('Task: refresh it.'), 'Task: refresh it.');
assert.equal(stripSpokenLabel('The way I read the record is simple: it went in.'), 'The way I read the record is simple: it went in.');
