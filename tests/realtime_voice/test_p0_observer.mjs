import assert from "node:assert/strict";
import { publicRecord, sessionRecord, observe } from "../../core/realtime_voice/bench/p0_observer.js";

function fakeAdapter() {
  const handlers = new Map();
  return {
    on(name, handler) { handlers.set(name, handler); },
    emit(name, event) { handlers.get(name)?.(event); },
  };
}

const session = sessionRecord("openai", "maria", "cafe");
const seen = [];
const adapter = fakeAdapter();
let now = 1000;
observe(adapter, session, () => now);
adapter.on("speech_stopped", () => seen.push("speech"));
adapter.on("input_transcript", (event) => seen.push(event.text));
adapter.on("output_transcript", (event) => seen.push(event.text));
adapter.on("interrupted", () => seen.push("cut"));
adapter.on("assistant_stopped", () => seen.push("stopped"));

adapter.emit("speech_stopped", { source: "browser_vad" });
adapter.emit("speech_stopped", {});
now = 1800;
adapter.emit("input_transcript", { completed: false, text: "half said" });
adapter.emit("input_transcript", { completed: true, text: "the whole sentence" });
now = 2400;
adapter.emit("output_transcript", { text: "Guten Tag" });
adapter.emit("interrupted", {});
adapter.emit("assistant_stopped", {});

const record = publicRecord(session, null);
assert.equal(session.committedUserTurns, 1);
assert.equal(session.interruptions, 1);
assert.deepEqual(record.speechEndToFirstTranscriptMs, [1400]);
assert.equal(JSON.stringify(record).includes("Guten Tag"), false);
assert.equal(JSON.stringify(record).includes("half said"), false);
assert.equal(JSON.stringify(record).includes("the whole sentence"), false);
assert.deepEqual(seen, ["speech", "speech", "half said", "the whole sentence", "Guten Tag", "cut", "stopped"]);
assert.deepEqual(record.notMeasured, ["speech_end_to_first_audible_playback"]);
console.log("p0 observer events: passed");
