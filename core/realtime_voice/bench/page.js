/**
 * Chunk A page record. Timings only. Spoken words are counted, never kept.
 * OpenAI emits playback_started once per session; that limit is labeled, not hidden.
 */
export const SCRIPT = [
  "A work question in English.",
  "One sentence with a deliberate one-second pause inside it. Pass when this thought is exactly one committed turn.",
  "A German sentence.",
  "An English work question after the German sentence. Pass when the reply is in English.",
  "A mixed sentence with one German word.",
  "An interruption mid-reply.",
  "A question that triggers the stub consult.",
  "We're done.",
];

export function pageRecord(provider) {
  return {
    provider,
    committedUserTurns: 0,
    interruptions: 0,
    playbacks: 0,
    consults: 0,
    ordinaryMs: [],
    consultMs: [],
    speechEndAt: null,
    consultPending: false,
    playbackOncePerSession: provider === "openai",
  };
}

export function applyEvent(record, event, now) {
  if (event.name === "speech_stopped") {
    if (!event.source) record.speechEndAt = now;
    return;
  }
  if (event.name === "input_transcript" && event.completed === true) record.committedUserTurns += 1;
  if (event.name === "playback_started") {
    record.playbacks += 1;
    if (record.speechEndAt != null) {
      const elapsed = Math.round(now - record.speechEndAt);
      (record.consultPending ? record.consultMs : record.ordinaryMs).push(elapsed);
      record.speechEndAt = null;
    }
  }
  if (event.name === "interrupted") record.interruptions += 1;
  if (event.name === "consult_started") record.consultPending = true;
  if (event.name === "consult_finished") {
    record.consults += 1;
    record.consultPending = false;
  }
}

export function publicPageRecord(record) {
  return {
    provider: record.provider,
    committedUserTurns: record.committedUserTurns,
    interruptions: record.interruptions,
    playbacks: record.playbacks,
    consults: record.consults,
    ordinarySpeechEndToPlaybackMs: record.ordinaryMs,
    consultSpeechEndToPlaybackMs: record.consultMs,
    playbackOncePerSession: record.playbackOncePerSession,
    notStored: ["spoken words", "accent mark", "language mark"],
  };
}
