import assert from "node:assert/strict";
import fs from "node:fs";

const root = new URL("../../core/realtime_voice/static/", import.meta.url);
const openai = fs.readFileSync(new URL("adapters/openai-webrtc-adapter.js", root), "utf8");
const xai = fs.readFileSync(new URL("adapters/xai-websocket-adapter.js", root), "utf8");
const controller = fs.readFileSync(new URL("realtime-voice-controller.js", root), "utf8");

assert.equal(openai.split('this._emit("playback_started"').length, 2);
assert.match(openai, /addEventListener\("playing".*\{ once: true \}/s);
assert.match(xai, /this\._playbackSources\.size === 1\) this\._emit\("playback_started"/);
assert.match(controller, /on\("playback_started", \(\) => this\._onInputState\("playback_started"\)\)/);
console.log("playback event: passed");
