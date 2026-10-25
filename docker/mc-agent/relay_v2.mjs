// mc-relay v2 (overnight build, 7 Oct 2026, step 4): relay.mjs plus three things, and nothing removed.
//   1. images     a user message may carry image parts (data URLs; png, jpeg, webp, gif; at most 4, 5 MB each, the
//                 type checked against the bytes); the text of a request is still capped at 256 KB.
//   2. agents     the model must be one of openclaw/mc-agent or openclaw/mc-chat (the lean conversation agent).
//   3. jobs       POST /v1/jobs starts work that runs for minutes, GET /v1/jobs/<id> reports it, POST /v1/jobs/<id>/stop
//                 stops it. Jobs have their own budget (a deadline of up to 30 minutes, no idle abort), run as the
//                 job agent mc-agent under their own session, never block the chat lane, are deduped by job id,
//                 and survive nothing they should not: a relay restart ends a running job as "unknown", and a
//                 stop for a job the relay has never heard of is remembered so a late start of it is refused.
//                 A job reads its files only from its own inbox (realpath inside it, no links, sha256 checked);
//                 its audit is read from OpenClaw's own run record (tool calls with targets, flagged when a call
//                 touches an area the job guidance forbids); the files it wrote are checked on the host (exists,
//                 size, sha256). GET /v1/capabilities says what this relay can do.
// The protocol the portal speaks is documented in minimoi_portal/guild_ui/mc/job_relay.py.
//
// mc-relay: the one-way relay between the portal and Master Craftsman's own
// OpenClaw (MC spec v0.9 §4). Runs from the MC image in MC's own Compose
// project, on two networks: mc-front (portal + relay) and mc-net (relay + MC +
// gateway). It only ever forwards to MC itself, so MC can reach nothing of the
// portal through it; the portal is on no network MC is on.
//
// Rules (v0.9 §4):
//   callers   the portal, with MC_RELAY_TOKEN; anything else 401
//   paths     POST /v1/chat/completions, GET /readyz (proxied), GET /healthz
//             (the relay itself, no auth, for the container healthcheck);
//             everything else, WebSocket upgrades included: 403
//   model     exactly "openclaw/mc-agent"; aliases and openclaw/default: 403
//   user      must start "guild-mc:"
//   headers   only Content-Type goes on; Authorization is replaced with MC's
//             own OpenClaw token (MC_OPENCLAW_GATEWAY_TOKEN, held only here);
//             every x-openclaw-* header is dropped
//   body      parsed (duplicate keys refused), checked, re-serialized; 256 KB
//             cap; only model, user, messages (and stream) go on
//   stream    (streaming spec v0.2 §3, v0.3 §2) "stream" only as a boolean
//             (true streams); a caller's stream_options is refused (the relay sets
//             include_usage itself). Stream headers are written only after
//             MC answers 200 text/event-stream; anything else keeps the JSON
//             error path. The relay emits its own minimal NDJSON, one object
//             per line, and nothing of MC's JSON passes:
//               {"t":"run","id":"chatcmpl_<uuid>"}  (first, once: OpenClaw's run
//                 id from its opening chunk, so the portal can bind this run's
//                 tool calls to the owner's turn; MC spec v1.2 §7.1)
//               {"t":"delta","text":…} {"t":"finish","reason":…}
//               {"t":"usage","prompt_tokens":…,"completion_tokens":…}
//               {"t":"error","class":deadline|idle|too_large|upstream|stopped}
//             Limits: the 300 s deadline, 300 s idle (any upstream byte resets
//             it), 256 KB of emitted text, a 64 KB pending line. A caller that
//             disconnects does NOT stop the run: the relay reads MC to the end
//             and stays in flight until MC has settled. Only a limit, or
//             POST /v1/turns/stop {correlation_id} (the portal's Stop), aborts
//             MC's call.
//   in flight one request at a time; a second gets 429 relay_busy
//   trace     X-MC-Correlation-Id (32 hex) is logged and echoed back, never
//             forwarded; logs never carry a token or message text
import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createHash, timingSafeEqual } from "node:crypto";

let DatabaseSync = null;                       // OpenClaw's run record is read through node:sqlite when this node has it
try { ({ DatabaseSync } = await import("node:sqlite")); } catch { /* the audit is then reported as unavailable */ }
const VERSION = "v2";

const PORT = Number(process.env.MC_RELAY_PORT || 8790);
const TARGET = process.env.MC_RELAY_TARGET || "http://mc-agent:18789";
// Callers (MC spec v0.9 §4, amended by Rooms R1, ROOMS_R1.md §11): the portal
// (Shop floor, user "guild-mc:<...>") and the Rooms worker (minimoi-rooms-worker,
// user "guild-mc:rooms:<per-turn hash>"), both on mc-front with this one caller
// token. The relay stays single-flight for both: a busy answer (429) is
// queued by the worker, never dropped.
const CALLER_TOKEN = process.env.MC_RELAY_TOKEN || "";
const MC_TOKEN = process.env.MC_OPENCLAW_GATEWAY_TOKEN || "";
const MAX_BODY = 256 * 1024;
const DEADLINE_MS = Number(process.env.MC_RELAY_DEADLINE_MS || 300000);
const IDLE_MS = Number(process.env.MC_RELAY_IDLE_MS || 300000);
const STREAM_TEXT_MAX = Number(process.env.MC_RELAY_STREAM_TEXT_MAX || 256 * 1024);
const LINE_MAX = Number(process.env.MC_RELAY_LINE_MAX || 64 * 1024);
const MODEL = "openclaw/mc-agent";
const MODELS = new Set(["openclaw/mc-agent", "openclaw/mc-chat"]);
const MAX_BODY_IMAGES = Number(process.env.MC_RELAY_MAX_BODY_IMAGES || 24 * 1024 * 1024);
const IMAGES_MAX = 4;
const IMAGE_MAX_BYTES = 5 * 1024 * 1024;
const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
const CORRELATION = /^[0-9a-f]{32}$/;
let inFlight = false;
let current = null;          // the streaming turn in flight: { correlation, abort(reason) }

function log(fields) {
  console.log(JSON.stringify({ at: new Date().toISOString(), relay: "mc", ...fields }));
}

function reply(res, status, type, message, extra = {}) {
  res.writeHead(status, { "content-type": "application/json", "cache-control": "no-store", ...extra });
  res.end(JSON.stringify({ error: { type, message } }));
}

// Duplicate keys are refused, at any depth (a smuggled second "model" must
// not win: JSON.parse keeps the last one).
export function parseStrict(text) {
  const value = JSON.parse(text);
  const key = /"((?:[^"\\]|\\.)*)"\s*:/y;
  const stack = [];
  let inString = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (inString) {
      if (ch === "\\") i += 1;
      else if (ch === "\"") inString = false;
      continue;
    }
    if (ch === "{") stack.push(new Set());
    else if (ch === "}") stack.pop();
    else if (ch === "\"") {
      key.lastIndex = i;
      const m = key.exec(text);
      if (m && stack.length) {
        const keys = stack[stack.length - 1];
        if (keys.has(m[1])) throw new Error(`duplicate key ${m[1]}`);
        keys.add(m[1]);
        i = key.lastIndex - 1;
      } else {
        inString = true;
      }
    }
  }
  return value;
}

// Image bytes must be what their label says: the first bytes are checked, not the claimed type alone.
function imageMatches(type, bytes) {
  if (type === "image/png") return bytes.length > 8 && bytes.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));
  if (type === "image/jpeg") return bytes.length > 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff;
  if (type === "image/gif") return bytes.length > 6 && bytes.subarray(0, 4).toString("latin1") === "GIF8";
  if (type === "image/webp") return bytes.length > 12 && bytes.subarray(0, 4).toString("latin1") === "RIFF" && bytes.subarray(8, 12).toString("latin1") === "WEBP";
  return false;
}

const DATA_URL = /^data:(image\/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/]+={0,2})$/;

// A message's content is text, or a list of parts: {type:"text",text} and {type:"image_url",image_url:{url}} only.
// Returns [problem, cleanContent, imageCount, textBytes]; the clean content has only the allowed keys.
function checkContent(role, content) {
  if (typeof content === "string") return [null, content, 0, Buffer.byteLength(content)];
  if (!Array.isArray(content) || content.length === 0) return ["each message needs a role and text content", null, 0, 0];
  if (role !== "user") return ["only a user message may carry parts", null, 0, 0];
  const clean = [];
  let images = 0;
  let textBytes = 0;
  for (const part of content) {
    if (!part || typeof part !== "object" || Array.isArray(part)) return ["a content part must be an object", null, 0, 0];
    if (part.type === "text") {
      if (typeof part.text !== "string" || Object.keys(part).some((k) => k !== "type" && k !== "text")) return ["a text part is {type, text} only", null, 0, 0];
      textBytes += Buffer.byteLength(part.text);
      clean.push({ type: "text", text: part.text });
    } else if (part.type === "image_url") {
      const keys = Object.keys(part).filter((k) => k !== "type");
      const inner = part.image_url;
      if (keys.length !== 1 || keys[0] !== "image_url" || !inner || typeof inner !== "object" || typeof inner.url !== "string" || Object.keys(inner).some((k) => k !== "url")) return ["an image part is {type, image_url:{url}} only", null, 0, 0];
      const m = DATA_URL.exec(inner.url);
      if (!m) return ["an image must be a base64 data URL of png, jpeg, webp or gif", null, 0, 0];
      images += 1;
      if (images > IMAGES_MAX) return [`at most ${IMAGES_MAX} images per request`, null, 0, 0];
      const bytes = Buffer.from(m[2], "base64");
      if (bytes.length === 0 || bytes.length > IMAGE_MAX_BYTES) return ["an image is over 5 MB or empty", null, 0, 0];
      if (!imageMatches(m[1], bytes)) return ["an image's bytes are not the type it says", null, 0, 0];
      clean.push({ type: "image_url", image_url: { url: inner.url } });
    } else {
      return ["unsupported content part", null, 0, 0];
    }
  }
  return [null, clean, images, textBytes];
}

export function checkBody(body) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return "the body must be a JSON object";
  if (!MODELS.has(body.model)) return `model must be exactly ${MODEL} or openclaw/mc-chat`;
  if ("stream" in body && typeof body.stream !== "boolean") return "stream must be a boolean (true streams)";
  if ("stream_options" in body) return "stream_options is set by the relay, not the caller";
  if (typeof body.user !== "string" || !body.user.startsWith("guild-mc:")) return "user must start guild-mc:";
  if (!Array.isArray(body.messages) || body.messages.length === 0) return "messages must be a non-empty list";
  let text = 0;
  for (const m of body.messages) {
    if (!m || typeof m !== "object" || typeof m.role !== "string") return "each message needs a role and text content";
    if (!["user", "system", "assistant"].includes(m.role)) return "unsupported message role";
    const [problem, , , bytes] = checkContent(m.role, m.content);
    if (problem) return problem;
    text += bytes;
  }
  if (text > MAX_BODY) return "the text of a request is larger than 256 KB";
  return null;
}

// What goes on to MC: only the allowed keys of each message.
function cleanMessages(messages) {
  return messages.map((m) => ({ role: m.role, content: checkContent(m.role, m.content)[1] }));
}

// Constant-time comparison of the caller token (lengths first: timingSafeEqual
// needs equal lengths, and the length itself is not secret).
const EXPECTED = Buffer.from(`Bearer ${CALLER_TOKEN}`);
export function authorized(req) {
  if (CALLER_TOKEN.length < 16) return false;
  const got = Buffer.from(String(req.headers.authorization || ""));
  return got.length === EXPECTED.length && timingSafeEqual(got, EXPECTED);
}

async function forward(path, init) {
  const headers = { authorization: `Bearer ${MC_TOKEN}` };
  if (init.body) headers["content-type"] = "application/json";
  return fetch(`${TARGET}${path}`, { ...init, headers, signal: AbortSignal.timeout(DEADLINE_MS) });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://relay");
  const correlation = CORRELATION.test(String(req.headers["x-mc-correlation-id"] || ""))
    ? String(req.headers["x-mc-correlation-id"]) : null;
  const echo = correlation ? { "x-mc-correlation-id": correlation } : {};
  if (req.method === "GET" && url.pathname === "/healthz") {
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(`{"ok":true,"version":"${VERSION}"}`);
  }
  if (!authorized(req)) {
    log({ event: "refused", reason: "caller", path: url.pathname });
    return reply(res, 401, "relay_unauthorized", "the relay needs the portal's caller token");
  }
  if (req.method === "GET" && url.pathname === "/readyz") {
    try {
      const r = await forward("/readyz", { method: "GET" });
      res.writeHead(r.status, { "content-type": "application/json" });
      return res.end(r.status === 200 ? '{"ready":true}' : '{"ready":false}');
    } catch {
      return reply(res, 503, "relay_upstream_down", "Master Craftsman's runtime is not answering");
    }
  }
  if (req.method === "POST" && url.pathname === "/v1/turns/stop") return stopTurn(req, res);
  if (req.method === "GET" && url.pathname === "/v1/capabilities") return capabilities(res);
  if (url.pathname === "/v1/jobs" || url.pathname.startsWith("/v1/jobs/")) return jobRoute(req, res, url);
  if (!(req.method === "POST" && url.pathname === "/v1/chat/completions")) {
    log({ event: "refused", reason: "path", method: req.method, path: url.pathname });
    return reply(res, 403, "relay_path_refused", "only POST /v1/chat/completions, POST /v1/turns/stop, GET /readyz, GET /v1/capabilities and the /v1/jobs routes pass the relay");
  }
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY_IMAGES) {
      log({ event: "refused", reason: "too_large", correlation });
      return reply(res, 413, "relay_too_large", "the request is larger than the relay accepts", echo);
    }
    chunks.push(chunk);
  }
  const raw = Buffer.concat(chunks);
  if (raw.length > MAX_BODY && !raw.includes('"image_url"')) {     // text-only requests keep v1's 256 KB cap exactly
    log({ event: "refused", reason: "too_large", correlation });
    return reply(res, 413, "relay_too_large", "the request is larger than 256 KB", echo);
  }
  let body;
  try {
    body = parseStrict(raw.toString("utf8"));
  } catch (error) {
    return reply(res, 400, "relay_bad_json", String(error.message || "unreadable JSON").slice(0, 80), echo);
  }
  const problem = checkBody(body);
  if (problem) {
    log({ event: "refused", reason: "body", problem, correlation });
    return reply(res, 403, "relay_body_refused", problem, echo);
  }
  if (inFlight) {
    log({ event: "refused", reason: "busy", correlation });
    return reply(res, 429, "relay_busy", "one Master Craftsman turn at a time", echo);
  }
  inFlight = true;
  const started = Date.now();
  if (body.stream === true) return streamTurn(res, body, correlation, echo, started);
  try {
    const clean = JSON.stringify({ model: body.model, user: body.user, messages: cleanMessages(body.messages) });
    const r = await forward("/v1/chat/completions", { method: "POST", body: clean });
    const text = await r.text();
    log({ event: "turn", correlation, status: r.status, ms: Date.now() - started });
    res.writeHead(r.status, { "content-type": "application/json", "cache-control": "no-store", ...echo });
    res.end(text);
  } catch (error) {
    const timeout = error?.name === "TimeoutError";
    log({ event: "turn", correlation, status: timeout ? 504 : 502, ms: Date.now() - started });
    reply(res, timeout ? 504 : 502, timeout ? "relay_timeout" : "relay_upstream_down",
      timeout ? "Master Craftsman did not answer within the deadline" : "Master Craftsman's runtime is not answering", echo);
  } finally {
    inFlight = false;
  }
});

// ── Streaming (spec v0.2 §3) ──────────────────────────────────────────────

// OpenClaw's run id (openai-http: `chatcmpl_${randomUUID()}`), written on the
// opening assistant-role chunk before the agent starts. Only this exact shape
// is ever forwarded; any other id is dropped as before.
export const RUN_ID = /^chatcmpl_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export function runIdOf(line) {
  if (!line.startsWith("data:")) return null;
  const data = line.slice(5).trim();
  if (data === "[DONE]") return null;
  let chunk;
  try { chunk = JSON.parse(data); } catch { return null; }
  const id = chunk && typeof chunk === "object" ? chunk.id : null;
  return typeof id === "string" && RUN_ID.test(id) ? id : null;
}

// Reads MC's SSE and yields the allow-listed events: text deltas, the finish
// reason and the trailing usage. Everything else (model, roles, tool_calls,
// x-openclaw-*) is dropped; the run id goes on separately (runIdOf, once).
export function* sseEvents(line) {
  if (!line.startsWith("data:")) return;
  const data = line.slice(5).trim();
  if (data === "[DONE]") { yield { t: "done" }; return; }
  let chunk;
  try { chunk = JSON.parse(data); } catch { return; }
  if (!chunk || typeof chunk !== "object") return;
  if (chunk.error) { yield { t: "error", class: "upstream" }; return; }
  const choice = Array.isArray(chunk.choices) ? chunk.choices[0] : null;
  const text = choice && choice.delta && choice.delta.content;
  if (typeof text === "string" && text) yield { t: "delta", text };
  if (choice && typeof choice.finish_reason === "string") yield { t: "finish", reason: choice.finish_reason.slice(0, 32) };
  const u = chunk.usage;
  if (u && Number.isInteger(u.prompt_tokens) && Number.isInteger(u.completion_tokens)) {
    yield { t: "usage", prompt_tokens: u.prompt_tokens, completion_tokens: u.completion_tokens };
  }
}

async function streamTurn(res, body, correlation, echo, started) {
  const controller = new AbortController();
  let why = null;
  const abort = (reason) => { if (!why) why = reason; controller.abort(); };
  const deadline = setTimeout(() => abort("deadline"), DEADLINE_MS);
  let idle = null;
  const touch = () => { clearTimeout(idle); idle = setTimeout(() => abort("idle"), IDLE_MS); };
  current = { correlation, abort };
  let callerGone = false;
  let streaming = false;
  res.on("close", () => { if (!res.writableFinished) callerGone = true; });
  const send = (event) => { if (streaming && !callerGone && res.writable) res.write(`${JSON.stringify(event)}\n`); };
  let outcome = "ended";
  try {
    touch();
    const clean = JSON.stringify({ model: body.model, user: body.user, stream: true, stream_options: { include_usage: true },
      messages: cleanMessages(body.messages) });
    const r = await fetch(`${TARGET}/v1/chat/completions`, { method: "POST", body: clean, signal: controller.signal,
      headers: { authorization: `Bearer ${MC_TOKEN}`, "content-type": "application/json" } });
    touch();
    if (r.status !== 200 || !String(r.headers.get("content-type") || "").includes("text/event-stream")) {
      const text = await r.text();
      outcome = `http_${r.status}`;
      if (r.status === 200) {
        return reply(res, 502, "relay_not_a_stream", "Master Craftsman did not answer with a stream", echo);
      }
      res.writeHead(r.status, { "content-type": "application/json", "cache-control": "no-store", ...echo });
      return res.end(text);
    }
    res.writeHead(200, { "content-type": "application/x-ndjson; charset=utf-8", "cache-control": "no-cache, no-transform",
      "x-accel-buffering": "no", ...echo });
    streaming = true;
    const decoder = new TextDecoder("utf-8");
    const reader = r.body.getReader();
    let pending = "";
    let emitted = 0;
    let runSent = false;
    read: for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      touch();
      pending += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = pending.indexOf("\n")) >= 0) {
        const line = pending.slice(0, nl).replace(/\r$/, "");
        pending = pending.slice(nl + 1);
        if (!runSent) {
          const run = runIdOf(line);
          if (run) { runSent = true; send({ t: "run", id: run }); }
        }
        for (const event of sseEvents(line)) {
          if (event.t === "done") break read;
          if (event.t === "error") { send(event); outcome = "upstream"; break read; }
          if (event.t === "delta") {
            emitted += Buffer.byteLength(event.text);
            if (emitted > STREAM_TEXT_MAX) { abort("too_large"); break read; }
          }
          send(event);
        }
      }
      if (pending.length > LINE_MAX) { abort("too_large"); break; }
    }
    if (why) { send({ t: "error", class: why }); outcome = why; }
    else if (outcome === "ended") outcome = "done";
  } catch (error) {
    const reason = why || (error?.name === "TimeoutError" ? "deadline" : "upstream");
    outcome = reason;
    if (streaming) send({ t: "error", class: reason });
    else if (reason === "stopped") reply(res, 409, "relay_stopped", "the turn was stopped before Master Craftsman answered", echo);
    else if (reason === "deadline" || reason === "idle") reply(res, 504, "relay_timeout", "Master Craftsman did not answer within the deadline", echo);
    else reply(res, 502, "relay_upstream_down", "Master Craftsman's runtime is not answering", echo);
  } finally {
    clearTimeout(deadline);
    clearTimeout(idle);
    if (!controller.signal.aborted && why) controller.abort();
    current = null;
    inFlight = false;              // only now: MC's call has settled
    log({ event: "stream", correlation, outcome, caller_gone: callerGone, ms: Date.now() - started });
    if (streaming && !callerGone && res.writable) res.end();
  }
}

async function stopTurn(req, res) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 1024) return reply(res, 413, "relay_too_large", "a stop request is small");
    chunks.push(chunk);
  }
  let id = null;
  try { id = JSON.parse(Buffer.concat(chunks).toString("utf8")).correlation_id; } catch { /* checked below */ }
  if (!CORRELATION.test(String(id || ""))) return reply(res, 400, "relay_bad_stop", "name the turn (correlation_id, 32 hex)");
  if (!current || current.correlation !== id) {
    log({ event: "stop", correlation: id, found: false });
    return reply(res, 404, "relay_not_running", "no such turn is in flight");
  }
  current.abort("stopped");
  log({ event: "stop", correlation: id, found: true });
  res.writeHead(200, { "content-type": "application/json", "cache-control": "no-store" });
  return res.end('{"stopped":true}');
}

// ── Jobs (v2) ─────────────────────────────────────────────────────────────
//
// A job is one long OpenClaw agent run, started by the portal and read back by polling. The relay holds the connection
// to the gateway itself (a run is cancelled when its connection drops), so the portal never has to stay connected.
// Configuration (all required for jobs; without them POST /v1/jobs answers 503 jobs_not_configured and starts nothing):
//   MC_RELAY_STATE_DIR      where job records and stop tombstones are kept (private files)
//   MC_RELAY_INBOX_ROOT     the portal's job inboxes, as seen from this host: <root>/<job id>/
//   MC_RELAY_PROJECT_ROOT   the project the job may read
//   MC_RELAY_WORKSPACE      MC's own workspace (a place it may write)
//   MC_RELAY_HANDOFF        the one hand-off folder it may write to besides its workspace
//   MC_RELAY_AGENT_DB       OpenClaw's run record for mc-agent (sqlite), read-only, for the audit
//   MC_RELAY_DENY           optional JSON list of extra path fragments the job must not touch
const STATE_DIR = process.env.MC_RELAY_STATE_DIR || "";
const INBOX_ROOT = process.env.MC_RELAY_INBOX_ROOT || "";
const PROJECT_ROOT = process.env.MC_RELAY_PROJECT_ROOT || "";
const WORKSPACE = process.env.MC_RELAY_WORKSPACE || "";
const HANDOFF = process.env.MC_RELAY_HANDOFF || "";
const AGENT_DB = process.env.MC_RELAY_AGENT_DB || "";
const MAX_JOBS = Number(process.env.MC_RELAY_MAX_JOBS || 2);
const JOB_BODY_MAX = 16 * 1024;
const TASK_MAX = 8000;
const JOB_DEADLINE_MAX_S = Number(process.env.MC_RELAY_JOB_DEADLINE_MAX_S || 1800);
const JOB_DEADLINE_MIN_S = 5;
const JOB_GRACE_MS = Number(process.env.MC_RELAY_JOB_GRACE_MS || 60000);
const RESULT_MAX = 64 * 1024;
const UPSTREAM_BODY_MAX = 4 * 1024 * 1024;
const TOOLS_MAX = 200;
const WRITES_MAX = 50;
const INBOX_FILE_MAX = 5 * 1024 * 1024;
const STOP_CONFIRM_MS = Number(process.env.MC_RELAY_STOP_CONFIRM_MS || 8000);
// Retain job/tombstone audit until an explicit reviewed disposition; no automatic expiry.
const JOB_ID = /^j-[0-9a-f]{16}$/;
const INBOX_NAME = /^\d{2}-[A-Za-z0-9_.\-]{1,80}$/;
const OPENCLAW_NO_RESPONSE = "No response from OpenClaw.";
const jobsConfigured = () => Boolean(STATE_DIR && INBOX_ROOT && PROJECT_ROOT && WORKSPACE && HANDOFF);
const DENY_DEFAULT = [".openclaw/openclaw.json", ".openclaw/credentials", ".openclaw/agents/main", "/.ssh", "/.aws", "/.gnupg", "/.env",
  "library/keychains", "id_rsa", "id_ed25519", ".pem", "_working/rvs-career", "/secrets", "api_key", "bot_token", "relay.token"];
let denyList = DENY_DEFAULT;
try { if (process.env.MC_RELAY_DENY) denyList = DENY_DEFAULT.concat(JSON.parse(process.env.MC_RELAY_DENY).map(String)); } catch { /* the default list stands */ }
const jobs = new Map();

const jobPath = (id) => path.join(STATE_DIR, "jobs", `${id}.json`);
const tombPath = (id) => path.join(STATE_DIR, "tombstones", id);

function privateWrite(file, text) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, text, { mode: 0o600 });
  fs.renameSync(tmp, file);
}

const PUBLIC_FIELDS = ["id", "state", "createdAt", "startedAt", "endedAt", "errorClass", "stopRequested", "result", "resultTruncated",
  "tools", "toolsTruncated", "writes", "auditAvailable", "inbox"];
function persist(job) {
  if (!STATE_DIR) return;
  try { privateWrite(jobPath(job.id), JSON.stringify(Object.fromEntries(PUBLIC_FIELDS.map((k) => [k, job[k]])))); }
  catch (error) { log({ event: "job_persist_failed", job: job.id, reason: String(error?.code || "error") }); }
}

// A restart of the relay loses every run it was holding: those jobs end as "unknown", with the reason, never as "running".
function loadJobs() {
  if (!STATE_DIR) return;
  const dir = path.join(STATE_DIR, "jobs");
  let names = [];
  try { names = fs.readdirSync(dir); } catch { return; }
  for (const name of names) {
    if (!name.endsWith(".json") || !JOB_ID.test(name.slice(0, -5))) continue;
    try {
      const record = JSON.parse(fs.readFileSync(path.join(dir, name), "utf8"));
      if (record.state === "running") { record.state = "unknown"; record.errorClass = "relay_restarted"; record.endedAt = Date.now(); }
      jobs.set(record.id, { ...record, req: null, timers: [], lastAlive: record.endedAt || Date.now(), auditAt: 0 });
      if (record.errorClass === "relay_restarted") persist(jobs.get(record.id));
    } catch { /* an unreadable record is left alone */ }
  }
}

function hasTombstone(id) { try { return fs.existsSync(tombPath(id)); } catch { return false; } }
function writeTombstone(id) { try { privateWrite(tombPath(id), String(Date.now())); } catch { /* best effort */ } }

// ── what a job may be given: its own inbox, nothing else ──
function sha256File(file) { return createHash("sha256").update(fs.readFileSync(file)).digest("hex"); }

export function validateInbox(jobId, inbox) {
  if (inbox === null || inbox === undefined) return { ok: true, files: [] };
  const bad = (reason) => ({ ok: false, reason });
  if (!inbox || typeof inbox !== "object" || Array.isArray(inbox) || inbox.job_id !== jobId || !Array.isArray(inbox.files)
      || inbox.files.length < 1 || inbox.files.length > 5) return bad("inbox_shape");
  let root;
  let dir;
  try {
    root = fs.realpathSync(INBOX_ROOT);
    const wanted = path.join(INBOX_ROOT, jobId);
    if (fs.lstatSync(wanted).isSymbolicLink() || !fs.lstatSync(wanted).isDirectory()) return bad("inbox_not_a_directory");
    dir = fs.realpathSync(wanted);
  } catch { return bad("inbox_missing"); }
  if (dir !== path.join(root, jobId)) return bad("inbox_outside_root");
  let manifest;
  try { manifest = JSON.parse(fs.readFileSync(path.join(dir, "MANIFEST.json"), "utf8")); } catch { return bad("inbox_no_manifest"); }
  const listed = new Map((Array.isArray(manifest.files) ? manifest.files : []).map((f) => [f.path, f.sha256]));
  const files = [];
  for (const f of inbox.files) {
    if (!f || typeof f.name !== "string" || !INBOX_NAME.test(f.name) || !/^[0-9a-f]{64}$/.test(String(f.sha256))) return bad("inbox_file_shape");
    const file = path.join(dir, f.name);
    let st;
    try { st = fs.lstatSync(file); } catch { return bad("inbox_file_missing"); }
    if (st.isSymbolicLink() || !st.isFile()) return bad("inbox_not_a_regular_file");
    if (st.size > INBOX_FILE_MAX) return bad("inbox_file_too_big");
    let real;
    try { real = fs.realpathSync(file); } catch { return bad("inbox_file_missing"); }
    if (path.dirname(real) !== dir) return bad("inbox_file_outside");
    const digest = sha256File(real);
    if (digest !== f.sha256 || listed.get(f.name) !== digest) return bad("inbox_hash_mismatch");
    files.push({ name: f.name, path: real, sha256: digest });
  }
  return { ok: true, files };
}

// Where each of the job's files is handed back: the owner's own file name (the inbox name without its leading two digits and
// dash), allocated HERE so it never depends on the model: never the name of a file already in the hand-off folder, and never a
// name shared by two files of the same job. 01-report.docx and 02-report.docx become report.docx and report (2).docx; an
// existing report.docx is never replaced. The copy is made with cp -n (no clobber) as the second line of defence.
export function allocateHandback(files, handoffDir, exists = (p) => fs.existsSync(p)) {
  const taken = new Set();
  return files.map((f) => {
    const base = f.name.replace(/^\d{2}-/, "");
    const ext = path.extname(base);
    const stem = base.slice(0, base.length - ext.length);
    let name = base;
    let n = 1;
    while (taken.has(name.toLowerCase()) || exists(path.join(handoffDir, name))) { n += 1; name = `${stem} (${n})${ext}`; }
    taken.add(name.toLowerCase());
    return { ...f, handback: path.join(handoffDir, name) };
  });
}
const shq = (s) => `'${String(s).replace(/'/g, "'\\''")}'`;

function buildJobPrompt(task, files) {
  const lines = [
    "You are running as a background JOB for the owner of this project. Work until the task is done, then reply with a plain-language result: what you did, what you found, and any file you wrote with its full path.",
    "Rules for this job. They are guidance to you, not enforced by software, so follow them with care:",
    `- You may read the MiniMoi project at ${PROJECT_ROOT}. Do not read secrets, key files, credentials, the OpenClaw configuration, or personal folders (for example the career material under _working).`,
    `- Write only inside your own workspace (${WORKSPACE}) or the hand-off folder (${HANDOFF}). Never edit source code and never write anywhere else.`,
    "- If the task needs something these rules forbid, say so and stop. A refusal is a valid answer.",
  ];
  if (files.length) {
    lines.push("- The owner gave you these files as read-only copies. To hand one back with its formatting intact, copy its bytes into the hand-off folder; do not retype it.");
    lines.push("  Each file has its hand-back path already chosen for you (the owner's own name, never replacing an existing file). Use EXACTLY the command given for it, as written: cp -n never replaces a file, chmod u+w makes the copy editable, cmp checks the bytes. Do not pick another name, do not use mv, and do not add -f. If a command fails, stop and report it instead of overwriting anything.");
    lines.push("  Your files:");
    for (const f of allocateHandback(files, HANDOFF)) lines.push(`    cp -n -- ${shq(f.path)} ${shq(f.handback)} && chmod u+w ${shq(f.handback)} && cmp ${shq(f.path)} ${shq(f.handback)}`);
  }
  lines.push("", "The owner's request, in the owner's own words:", "<<<REQUEST", task, "REQUEST>>>");
  return lines.join("\n");
}

// ── the audit: OpenClaw's own record of the run, never the model's account of itself ──
function expand(p, base) {
  if (typeof p !== "string" || !p) return "";
  const home = os.homedir();
  const full = p.startsWith("~/") ? path.join(home, p.slice(2)) : p;
  return path.resolve(base || WORKSPACE || "/", full);
}
const under = (p, root) => Boolean(root) && (p === root || p.startsWith(root.endsWith(path.sep) ? root : root + path.sep));
const WRITE_TOOLS = new Set(["write", "edit", "apply_patch", "create", "append"]);
const READ_TOOLS = new Set(["read", "ls", "dir_list", "list", "glob", "grep", "search"]);
const SECRETISH = /(bearer\s+[A-Za-z0-9._\-]{12,}|(?:token|secret|password|passwd|api[_-]?key)\s*[=:]\s*\S{6,}|\b[A-Fa-f0-9]{40,}\b)/gi;
const redact = (text) => String(text || "").replace(SECRETISH, "[redacted]");

// The files a shell command plainly writes: cp and mv (to a file, or into a directory), tee, and > or >> redirections.
// Only simple commands are understood (quotes are honoured; pipes, globs, variables and subshells are not). Anything else
// is NOT claimed: the audit says a shell command may change files in ways the relay cannot see.
function shellWords(command) {
  const words = [];
  let cur = "";
  let quote = null;
  let any = false;
  for (let i = 0; i < command.length; i += 1) {
    const ch = command[i];
    if (quote) {
      if (ch === quote) quote = null; else if (ch === "\\" && quote === '"' && i + 1 < command.length) { i += 1; cur += command[i]; } else cur += ch;
    } else if (ch === "'" || ch === '"') { quote = ch; any = true; }
    else if (/\s/.test(ch)) { if (cur || any) words.push(cur); cur = ""; any = false; }
    else if (ch === ">" || ch === "|" || ch === ";" || ch === "&") { if (cur || any) words.push(cur); cur = ""; any = false; words.push(ch === ">" && command[i + 1] === ">" ? ">>" : ch); if (ch === ">" && command[i + 1] === ">") i += 1; }
    else { cur += ch; any = true; }
  }
  if (cur || any) words.push(cur);
  return quote ? null : words;
}

export function execWrites(command, workspaceDir) {
  const words = shellWords(String(command || ""));
  if (!words) return [];
  const out = [];
  const dest = (p) => { const full = expand(p, workspaceDir); if (full) out.push(full); };
  for (let i = 0; i < words.length; i += 1) {
    const w = words[i];
    if (w === ">" || w === ">>") { if (words[i + 1] && !/^[|;&>]$/.test(words[i + 1])) dest(words[i + 1]); continue; }
    if (i === 0 || /^[|;&]$/.test(words[i - 1])) {
      if (w === "cp" || w === "mv") {
        const args = [];
        for (let j = i + 1; j < words.length && !/^[|;&>]/.test(words[j]); j += 1) if (!words[j].startsWith("-")) args.push(words[j]);
        if (args.length >= 2) {
          const target = expand(args[args.length - 1], workspaceDir);
          let isDir = args[args.length - 1].endsWith("/");
          if (!isDir) { try { isDir = fs.statSync(target).isDirectory(); } catch { /* not there yet: a file */ } }
          for (const src of args.slice(0, -1)) out.push(isDir ? path.join(target, path.basename(src)) : target);
        }
      } else if (w === "tee") {
        for (let j = i + 1; j < words.length && !/^[|;&>]/.test(words[j]); j += 1) if (!words[j].startsWith("-")) dest(words[j]);
      }
    }
  }
  return out;
}

export function classifyCall(name, args, workspaceDir) {
  const a = args && typeof args === "object" ? args : {};
  const clean = (v) => redact(v).replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "").slice(0, 300);
  if (name === "exec") {
    const command = String(a.command ?? "");
    const lower = command.toLowerCase();
    let flag = denyList.some((d) => lower.includes(d.toLowerCase())) ? "touches_restricted_area" : null;
    const writes = execWrites(command, workspaceDir);
    if (!flag && writes.some((w) => !under(w, path.resolve(WORKSPACE)) && !under(w, path.resolve(HANDOFF)))) flag = "write_outside_allowed_area";
    return { target: clean(command).slice(0, 200), flag, write: null, writes };
  }
  const raw = a.path ?? a.file ?? a.file_path ?? a.filename ?? "";
  const target = typeof raw === "string" ? expand(raw, workspaceDir) : "";
  const lower = target.toLowerCase();
  let flag = null;
  if (target && denyList.some((d) => lower.includes(d.toLowerCase()))) flag = "touches_restricted_area";
  if (WRITE_TOOLS.has(name) && target && !under(target, path.resolve(WORKSPACE)) && !under(target, path.resolve(HANDOFF))) flag = "write_outside_allowed_area";
  return { target: clean(target || (typeof raw === "string" ? raw : "")), flag, write: WRITE_TOOLS.has(name) && target ? target : null, writes: [] };
}

function readAudit(job) {
  if (!DatabaseSync || !AGENT_DB) return null;
  let db;
  try {
    db = new DatabaseSync(AGENT_DB, { readOnly: true });
    db.exec("PRAGMA busy_timeout = 500");
    const key = `agent:mc-agent:openai-user:${job.user}`;
    const sessions = db.prepare("SELECT session_id, status FROM session_windows WHERE session_key = ? ORDER BY created_at").all(key);
    const rows = [];
    for (const s of sessions) {
      rows.push(...db.prepare("SELECT seq, event_json FROM trajectory_runtime_events WHERE session_id = ? ORDER BY seq").all(s.session_id));
    }
    const calls = new Map();
    const order = [];
    for (const row of rows) {
      let e;
      try { e = JSON.parse(row.event_json); } catch { continue; }
      const d = e?.data || {};
      if (e.type === "tool.call" && typeof d.toolCallId === "string") {
        const c = classifyCall(String(d.name || ""), d.args, e.workspaceDir);
        calls.set(d.toolCallId, { name: String(d.name || "").slice(0, 60), target: c.target, ok: true, flag: c.flag, writes: c.write ? [c.write] : c.writes });
        order.push(d.toolCallId);
      } else if (e.type === "tool.result" && calls.has(d.toolCallId)) {
        calls.get(d.toolCallId).ok = d.success !== false;
      }
    }
    return { tools: order.map((id) => calls.get(id)), running: sessions.some((s) => s.status === "running"), known: sessions.length > 0 };
  } catch { return null; } finally { try { db?.close(); } catch { /* closed */ } }
}

function refreshAudit(job, { force = false } = {}) {
  if (!force && Date.now() - (job.auditAt || 0) < 2000) return;
  job.auditAt = Date.now();
  const audit = readAudit(job);
  if (audit === null) { job.auditAvailable = false; return; }
  job.auditAvailable = true;
  job.toolsTruncated = audit.tools.length > TOOLS_MAX;
  job.tools = audit.tools.slice(0, TOOLS_MAX).map((t) => ({ name: t.name, target: t.target, ok: t.ok, ...(t.flag ? { flag: t.flag } : {}) }));
  job._writes = audit.tools.filter((t) => t.ok).flatMap((t) => t.writes || []);
  job._sessionRunning = audit.running;
  job._sessionKnown = audit.known;
}

// The relay checks what a job says it wrote: the file is there, how big, and its hash. Nothing is taken on trust.
function verifyWrites(job) {
  const seen = new Set();
  const out = [];
  for (const file of job._writes || []) {
    if (seen.has(file) || out.length >= WRITES_MAX) continue;
    seen.add(file);
    const entry = { path: file.slice(0, 300), exists: false, bytes: null, sha256: "", verified: false };
    try {
      const st = fs.lstatSync(file);
      if (st.isFile() && !st.isSymbolicLink()) {
        entry.exists = true;
        entry.bytes = st.size;
        if (st.size <= 20 * 1024 * 1024) { entry.sha256 = sha256File(file); entry.verified = true; }
      }
    } catch { /* it does not exist */ }
    out.push(entry);
  }
  job.writes = out;
}

// ── lifecycle ──
function finishJob(job, state, errorClass = null, result = null, truncated = false) {
  if (job.state !== "running") return;
  for (const t of job.timers || []) { clearInterval(t); clearTimeout(t); }
  job.timers = [];
  job.state = state;
  job.errorClass = errorClass;
  job.endedAt = Date.now();
  if (result !== null) { job.result = result; job.resultTruncated = truncated; }
  refreshAudit(job, { force: true });
  verifyWrites(job);
  persist(job);
  log({ event: "job_end", job: job.id, state, error_class: errorClass, ms: job.endedAt - job.startedAt, tools: job.tools.length });
}

function runUpstream(job, deadlineMs, payload) {
  const target = new URL(`${TARGET}/v1/chat/completions`);
  const req = http.request({ hostname: target.hostname, port: target.port, path: target.pathname, method: "POST",
    headers: { authorization: `Bearer ${MC_TOKEN}`, "content-type": "application/json", "content-length": Buffer.byteLength(payload) } });
  job.req = req;
  // Liveness, not progress: the connection to the gateway is open. OpenClaw says nothing while a run works through its tools.
  job.timers.push(setInterval(() => { if (job.state === "running" && !req.destroyed) job.lastAlive = Date.now(); }, 5000));
  job.timers.push(setTimeout(() => { job.stopRequested = false; req.destroy(new Error("deadline")); finishJob(job, "failed", "deadline"); }, deadlineMs + JOB_GRACE_MS));
  req.on("response", (res) => {
    const parts = [];
    let size = 0;
    res.on("data", (c) => { size += c.length; if (size <= UPSTREAM_BODY_MAX) parts.push(c); });
    res.on("end", () => {
      if (job.state !== "running") return;
      if (res.statusCode !== 200) return finishJob(job, "failed", `upstream_http_${res.statusCode}`);
      if (size > UPSTREAM_BODY_MAX) return finishJob(job, "failed", "upstream_too_large");
      let data;
      try { data = JSON.parse(Buffer.concat(parts).toString("utf8")); } catch { return finishJob(job, "failed", "upstream_unreadable"); }
      const choice = Array.isArray(data?.choices) ? data.choices[0] : null;
      const text = choice?.message?.content;
      if (data?.error || typeof text !== "string" || !text.trim() || text.trim() === OPENCLAW_NO_RESPONSE) return finishJob(job, "failed", "no_answer");
      if (choice.finish_reason && !["stop", "length"].includes(choice.finish_reason)) return finishJob(job, "failed", "unexpected_finish");
      const cut = Buffer.byteLength(text) > RESULT_MAX;
      finishJob(job, "completed", null, cut ? Buffer.from(text).subarray(0, RESULT_MAX).toString("utf8") : text, cut);
    });
    res.on("error", () => { if (!job.stopRequested) finishJob(job, "failed", "upstream_interrupted"); });
  });
  req.on("error", () => {
    if (job.state !== "running") return;
    if (job.stopRequested) return;                       // a stop is settled by stopJob, from the run record
    finishJob(job, "failed", "upstream_unreachable");
  });
  req.end(payload);
}

function startJob(spec) {
  const files = validateInbox(spec.job_id, spec.inbox);
  if (!files.ok) return { code: 422, body: { error: files.reason } };
  const deadlineS = Math.min(JOB_DEADLINE_MAX_S, Math.max(JOB_DEADLINE_MIN_S, Number(spec.deadline_s) || JOB_DEADLINE_MAX_S));
  const job = { id: spec.job_id, state: "running", createdAt: Date.now(), startedAt: Date.now(), endedAt: null, errorClass: null, stopRequested: false,
    user: `guild-mc:job:${spec.job_id}`, result: null, resultTruncated: false, tools: [], toolsTruncated: false, writes: [], auditAvailable: Boolean(DatabaseSync && AGENT_DB),
    inbox: files.files.map((f) => ({ name: f.name, sha256: f.sha256 })), req: null, timers: [], lastAlive: Date.now(), auditAt: 0, _writes: [] };
  jobs.set(job.id, job);
  persist(job);
  const payload = JSON.stringify({ model: MODEL, user: job.user, messages: [{ role: "user", content: buildJobPrompt(spec.task, files.files) }] });
  log({ event: "job_start", job: job.id, deadline_s: deadlineS, files: files.files.length });
  runUpstream(job, deadlineS * 1000, payload);
  return { code: 200, body: { accepted: true, job_id: job.id, state: "running" } };
}

function statusBody(job) {
  if (job.state === "running") refreshAudit(job);
  const end = job.endedAt || Date.now();
  return { state: job.state, elapsed_s: Math.max(0, Math.round((end - job.startedAt) / 1000)),
    heartbeat_age_s: job.state === "running" ? Math.max(0, Math.round((Date.now() - job.lastAlive) / 1000)) : null,
    result: job.state === "completed" ? { text: job.result, truncated: Boolean(job.resultTruncated) } : null,
    audit: { tools: job.tools, truncated: job.toolsTruncated, available: job.auditAvailable !== false },
    writes: job.writes || [], error_class: job.errorClass };
}

async function waitNotRunning(job, ms) {
  const until = Date.now() + ms;
  for (;;) {
    if (!DatabaseSync || !AGENT_DB) return false;                          // no run record at all: it cannot be confirmed
    refreshAudit(job, { force: true });                                    // a read that fails is no evidence: try again until the time is up
    if (job.auditAvailable !== false && job._sessionKnown && !job._sessionRunning) return true;
    if (Date.now() >= until) return false;
    await new Promise((r) => setTimeout(r, 300));
  }
}

async function stopJob(id) {
  const job = jobs.get(id);
  if (!job) { writeTombstone(id); log({ event: "job_stop", job: id, found: false }); return { code: 404, body: { error: "not_found" } }; }
  if (job.state !== "running") return { code: 200, body: { stop: "not_running" } };
  job.stopRequested = true;
  persist(job);
  try { job.req?.destroy(new Error("stopped")); } catch { /* already closed */ }
  const confirmed = await waitNotRunning(job, STOP_CONFIRM_MS);
  if (confirmed) { finishJob(job, "stopped"); log({ event: "job_stop", job: id, found: true, confirmed: true }); return { code: 200, body: { stop: "confirmed" } }; }
  // Not confirmed: the connection is closed, which cancels the run, but the run record does not say so yet. Keep watching.
  log({ event: "job_stop", job: id, found: true, confirmed: false });
  const watcher = setInterval(() => {
    if (job.state !== "running") return clearInterval(watcher);
    refreshAudit(job, { force: true });
    if (job._sessionKnown && !job._sessionRunning) { clearInterval(watcher); finishJob(job, "stopped"); }
  }, 2000);
  job.timers.push(watcher);
  return { code: 200, body: { stop: "requested" } };
}

async function readSmallBody(req, max) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > max) return null;
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function sendJson(res, code, body) {
  res.writeHead(code, { "content-type": "application/json", "cache-control": "no-store" });
  res.end(JSON.stringify(body));
}

function capabilities(res) {
  return sendJson(res, 200, { version: VERSION, chat: { models: [...MODELS], images: { max: IMAGES_MAX, max_bytes: IMAGE_MAX_BYTES, types: [...IMAGE_TYPES] } },
    jobs: { enabled: jobsConfigured(), agent: MODEL, max_running: MAX_JOBS, deadline_max_s: JOB_DEADLINE_MAX_S, audit: Boolean(DatabaseSync && AGENT_DB) } });
}

async function jobRoute(req, res, url) {
  const m = /^\/v1\/jobs(?:\/(j-[0-9a-f]{16})(\/stop)?)?$/.exec(url.pathname);
  if (!m) return reply(res, 403, "relay_path_refused", "no such job route");
  const [, id, stop] = m;
  if (!jobsConfigured()) return reply(res, 503, "jobs_not_configured", "this relay is not configured to run jobs");
  if (!id) {
    if (req.method !== "POST") return reply(res, 403, "relay_path_refused", "POST /v1/jobs starts a job");
    const text = await readSmallBody(req, JOB_BODY_MAX);
    if (text === null) return reply(res, 413, "relay_too_large", "a job request is small");
    let spec;
    try { spec = parseStrict(text); } catch (error) { return reply(res, 400, "relay_bad_json", String(error.message || "unreadable JSON").slice(0, 80)); }
    const keys = spec && typeof spec === "object" && !Array.isArray(spec) ? Object.keys(spec) : null;
    if (!keys || keys.some((k) => !["job_id", "agent", "task", "inbox", "deadline_s"].includes(k))) return reply(res, 403, "relay_body_refused", "a job request is {job_id, agent, task, inbox, deadline_s}");
    if (!JOB_ID.test(String(spec.job_id))) return reply(res, 403, "relay_body_refused", "job_id must be j- and 16 hex");
    if (spec.agent !== "mc-agent") return reply(res, 403, "relay_body_refused", "jobs run as mc-agent only");
    if (typeof spec.task !== "string" || !spec.task.trim() || spec.task.length > TASK_MAX) return reply(res, 403, "relay_body_refused", "task must be text of 1 to 8000 characters");
    if (spec.deadline_s !== undefined && typeof spec.deadline_s !== "number") return reply(res, 403, "relay_body_refused", "deadline_s must be a number");
    if (hasTombstone(spec.job_id)) return sendJson(res, 409, { error: "stopped_before_start" });
    const known = jobs.get(spec.job_id);
    if (known) return sendJson(res, 200, { accepted: false, duplicate: true, state: known.state });
    if ([...jobs.values()].filter((j) => j.state === "running").length >= MAX_JOBS) return sendJson(res, 429, { error: "busy" });
    const started = startJob(spec);
    return sendJson(res, started.code, started.body);
  }
  if (stop) {
    if (req.method !== "POST") return reply(res, 403, "relay_path_refused", "POST stops a job");
    const done = await stopJob(id);
    return sendJson(res, done.code, done.body);
  }
  if (req.method !== "GET") return reply(res, 403, "relay_path_refused", "GET reads a job");
  const job = jobs.get(id);
  if (!job) return sendJson(res, 404, { error: "not_found" });
  return sendJson(res, 200, statusBody(job));
}

loadJobs();

server.on("upgrade", (req, socket) => {
  log({ event: "refused", reason: "upgrade", path: req.url });
  socket.end("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n");
});

if (import.meta.url === `file://${process.argv[1]}`) {
  if (CALLER_TOKEN.length < 16 || MC_TOKEN.length < 16 || CALLER_TOKEN === MC_TOKEN) {
    console.error("mc-relay: MC_RELAY_TOKEN and MC_OPENCLAW_GATEWAY_TOKEN must both be set (16+ characters) and differ; refusing to start");
    process.exit(1);
  }
  server.listen(PORT, "0.0.0.0", () => log({ event: "listening", port: PORT }));
}

export { server };
