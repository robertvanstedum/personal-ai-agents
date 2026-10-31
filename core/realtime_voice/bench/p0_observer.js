/**
 * Phase 0 timings for German Gespräche. Spec 164 §4 and §5.
 *
 * The shared controller is unchanged. This wraps adapter.on after the
 * controller wires it, and records times only. Spoken words are never read.
 *
 * speech-end to first audible playback is not claimed here. OpenAI plays
 * audio inside the WebRTC stream, and the controller has no playback event.
 * That split is a Phase 1 measurement hook.
 */
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

export function attachObserver(controller, session, clock) {
  const original = controller._wireAdapterEvents.bind(controller);
  controller._wireAdapterEvents = function () {
    const adapter = this._adapter;
    if (adapter && !adapter.__p0Observed) {
      adapter.__p0Observed = true;
      observe(adapter, session, clock);
    }
    original();
  };
}

export function sessionRecord(provider, persona, scene) {
  return {
    provider, persona, scene,
    speechEndAt: null,
    openReply: null,
    replies: [],
    committedUserTurns: 0,
    interruptions: 0,
  };
}

export function publicRecord(session, ended) {
  return {
    provider: session.provider,
    persona: session.persona,
    scene: session.scene,
    committedUserTurns: session.committedUserTurns,
    interruptions: session.interruptions,
    speechEndToFirstTranscriptMs: session.replies.map(reply => reply.speechEndToFirstTranscriptMs),
    events: session.events ? session.events() : [],
    ended,
    notMeasured: ["speech_end_to_first_audible_playback"],
  };
}
