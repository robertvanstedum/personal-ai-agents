// Shared, transport-independent mic/stop presentation. It never starts audio.
// Hosts own authorization, state transitions, disabled state and termination.
export function paintVoiceButton(button, { state = 'idle', active = false } = {}) {
  const states = new Set(['idle', 'connecting', 'listening', 'working', 'speaking', 'ending', 'error']);
  if (!states.has(state)) state = 'idle';
  const stopping = active || state === 'ending';
  const text = state === 'ending' ? 'Ending voice…' : state === 'connecting' ? 'Connecting…' : stopping ? 'Stop voice' : 'Voice';
  button.setAttribute('data-voice-state', state);
  button.setAttribute('aria-label', state === 'ending' ? 'Ending voice; waiting for confirmation' :
    state === 'connecting' ? 'Stop connecting voice' : stopping ? 'Stop voice conversation' : 'Start voice conversation');
  button.setAttribute('title', state === 'speaking' ? 'Voice is speaking; stop voice' :
    state === 'listening' ? 'Voice is listening; stop voice' : state === 'working' ? 'Voice is working; stop voice' : text);
  const label = button.querySelector?.('[data-voice-label]');
  const mic = button.querySelector?.('[data-voice-icon="mic"]');
  const stop = button.querySelector?.('[data-voice-icon="stop"]');
  if (!label || !mic || !stop) {
    button.textContent = text; // Progressive enhancement; simple test/host nodes work.
    return;
  }
  label.textContent = text;
  mic.hidden = stopping;
  stop.hidden = !stopping;
}
