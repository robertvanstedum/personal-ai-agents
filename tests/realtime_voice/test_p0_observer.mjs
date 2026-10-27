import assert from "node:assert/strict";
import fs from "node:fs";
import { attachObserver, publicRecord, sessionRecord } from "../../core/realtime_voice/bench/p0_observer.js";

function fakeAdapter() {
  const handlers = [];
  return {
    on(name, handler) { handlers.push([name, handler]); },
    emit(name, event) { for (const [heard, handler] of handlers) if (heard === name) handler(event); },
  };
}

const seen = [];
const session = sessionRecord("openai", "maria", "cafe");
const adapter = fakeAdapter();
let now = 1000;
const controller = {
  _adapter: null,
  _wireAdapterEvents() {
    const on = (name, handler) => this._adapter.on(name, handler);
    on("speech_stopped", () => seen.push("speech"));
    on("input_transcript", (event) => seen.push(event.text));
    on("output_transcript", (event) => seen.push(event.text));
    on("interrupted", () => seen.push("cut"));
    on("assistant_stopped", () => seen.push("stopped"));
  },
};
attachObserver(controller, session, () => now);
controller._adapter = adapter;
controller._wireAdapterEvents();

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

function observeBody(source) {
  const start = source.indexOf("function observe(");
  const end = source.indexOf("\nexport function attachObserver") > start
    ? source.indexOf("\nexport function attachObserver")
    : source.indexOf("\nconst session = {");
  return source.slice(start, end).replace(/\s+/g, " ").trim();
}
const moduleBody = observeBody(fs.readFileSync(new URL("../../core/realtime_voice/bench/p0_observer.js", import.meta.url), "utf8"));
const snippetBody = observeBody(fs.readFileSync(new URL("../../core/realtime_voice/bench/p0_snippet.js", import.meta.url), "utf8"));
assert.equal(snippetBody, moduleBody);
console.log("p0 observer events: passed");
