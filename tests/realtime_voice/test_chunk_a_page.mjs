import assert from "node:assert/strict";
import { applyEvent, pageRecord, publicPageRecord } from "../../core/realtime_voice/bench/page.js";

const record = pageRecord("openai");
const events = [
  ["speech_stopped", {}],
  ["input_transcript", { completed: false }],
  ["input_transcript", { completed: true }],
  ["playback_started", {}],
  ["speech_stopped", { source: "browser_vad" }],
  ["speech_stopped", {}],
  ["input_transcript", { completed: true }],
  ["consult_started", {}],
  ["playback_started", {}],
  ["consult_finished", {}],
  ["interrupted", {}],
];
let now = 1000;
for (const [name, extra] of events) {
  now += 500;
  applyEvent(record, { name, ...extra }, now);
}
const shown = publicPageRecord(record);
assert.equal(shown.committedUserTurns, 2);
assert.equal(shown.interruptions, 1);
assert.equal(shown.consults, 1);
assert.deepEqual(shown.ordinarySpeechEndToPlaybackMs, [1500]);
assert.deepEqual(shown.consultSpeechEndToPlaybackMs, [1500]);
assert.equal(shown.playbackOncePerSession, true);
assert.equal(JSON.stringify(shown).includes("half"), false);
assert.equal(pageRecord("xai").playbackOncePerSession, false);
console.log("chunk A page record: passed");
