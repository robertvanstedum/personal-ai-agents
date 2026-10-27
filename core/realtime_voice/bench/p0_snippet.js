/**
 * One paste for the Gespräche console, before pressing voice. One tab only.
 * A reload removes it. The version matches the deployed template on 85fa56f9.
 * The observer below is the same logic as p0_observer.js; the console cannot
 * import that file, so the paste carries it.
 */
const VERSION = "20260809-ga1";
const mod = await import(`./static/realtime-voice/realtime-voice-controller.js?v=${VERSION}`);
const Controller = mod.RealtimeVoiceController;
if (typeof Controller !== "function" || typeof Controller.prototype?._wireAdapterEvents !== "function") {
  throw new Error("P0 observer refused: RealtimeVoiceController._wireAdapterEvents is missing. Nothing was patched.");
}

export function observe(adapter, session, clock) {
  const events = [];
  const note = (name) => events.push({ name, atMs: Math.round(clock()) });
  const rawOn = adapter.on.bind(adapter);
  adapter.on = (name, handler) => rawOn(name, (event) => {
    if (name === "speech_stopped" && !event?.source) {
      session.speechEndAt = clock();
      note("speech_stopped");
    }
    if (name === "output_transcript" && session.speechEndAt != null && session.openReply == null) {
      session.openReply = { speechEndToFirstTranscriptMs: Math.round(clock() - session.speechEndAt) };
      session.speechEndAt = null;
      note("first_assistant_transcript");
    }
    if (name === "interrupted") {
      session.interruptions += 1;
      note("interrupted");
    }
    if (name === "assistant_stopped" && session.openReply) {
      session.replies.push(session.openReply);
      session.openReply = null;
      note("assistant_stopped");
    }
    if (name === "input_transcript" && event?.completed === true) {
      session.committedUserTurns += 1;
      note("committed_user_turn");
    }
    handler(event);
  });
  session.events = () => events.map(({ name, atMs }) => ({ name, atMs }));
}

const session = {
  provider: "page", persona: "page", scene: "page",
  speechEndAt: null, openReply: null, replies: [],
  committedUserTurns: 0, interruptions: 0,
};
const original = Controller.prototype._wireAdapterEvents;
Controller.prototype._wireAdapterEvents = function () {
  const adapter = this._adapter;
  if (adapter && !adapter.__p0Observed) {
    adapter.__p0Observed = true;
    observe(adapter, session, () => performance.now());
  }
  original.call(this);
};
window.__p0 = () => ({
  provider: session.provider,
  persona: session.persona,
  scene: session.scene,
  committedUserTurns: session.committedUserTurns,
  interruptions: session.interruptions,
  speechEndToFirstTranscriptMs: session.replies.map(reply => reply.speechEndToFirstTranscriptMs),
  events: session.events ? session.events() : [],
  ended: null,
  notMeasured: ["speech_end_to_first_audible_playback"],
});
console.log("P0 observer attached. After the session: copy(JSON.stringify(window.__p0(), null, 2))");
