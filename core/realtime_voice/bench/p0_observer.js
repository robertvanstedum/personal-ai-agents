/**
 * Phase 0 timings for German Gespräche. Spec 164 §4 and §5.
 *
 * The shared controller is unchanged. This wraps the adapter after the
 * controller wires it, and records times only. Spoken words are counted,
 * never kept.
 *
 * speech-end to first audible playback is not claimed here. OpenAI plays
 * audio inside the WebRTC stream, and the controller has no playback event.
 * That split is a Phase 1 measurement hook. What this records now is
 * speech-end to the first assistant transcript event, interruption, and
 * committed user turns.
 */
export function attachObserver(controller, session) {
  const events = [];
  const note = (name) => events.push({ name, atMs: Math.round(performance.now()) });
  const original = controller._wireAdapterEvents.bind(controller);
  controller._wireAdapterEvents = () => {
    original();
    const adapter = controller._adapter;
    if (!adapter || adapter.__p0Observed) return;
    adapter.__p0Observed = true;
    const rawOn = adapter.on.bind(adapter);
    adapter.on = (name, handler) => rawOn(name, (event) => {
      if (name === "speech_stopped" && !event?.source) {
        session.speechEndAt = performance.now();
        note("speech_stopped");
      }
      if (name === "output_transcript" && session.speechEndAt != null && session.openReply == null) {
        session.openReply = {
          speechEndToFirstTranscriptMs: Math.round(performance.now() - session.speechEndAt),
        };
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
      handler(event);
    });
  };
  session.events = () => events.map(({ name, atMs }) => ({ name, atMs }));
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
