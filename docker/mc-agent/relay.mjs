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
//               {"t":"delta","text":…} {"t":"finish","reason":…}
//               {"t":"usage","prompt_tokens":…,"completion_tokens":…}
//               {"t":"error","class":deadline|idle|too_large|upstream|stopped}
//             Limits: the 120 s deadline, 30 s idle (any upstream byte resets
//             it), 256 KB of emitted text, a 64 KB pending line. A caller that
//             disconnects does NOT stop the run: the relay reads MC to the end
//             and stays in flight until MC has settled. Only a limit, or
//             POST /v1/turns/stop {correlation_id} (the portal's Stop), aborts
//             MC's call.
//   in flight one request at a time; a second gets 429 relay_busy
//   trace     X-MC-Correlation-Id (32 hex) is logged and echoed back, never
//             forwarded; logs never carry a token or message text
import http from "node:http";
import { timingSafeEqual } from "node:crypto";

const PORT = Number(process.env.MC_RELAY_PORT || 8790);
const TARGET = process.env.MC_RELAY_TARGET || "http://mc-agent:18789";
const CALLER_TOKEN = process.env.MC_RELAY_TOKEN || "";
const MC_TOKEN = process.env.MC_OPENCLAW_GATEWAY_TOKEN || "";
const MAX_BODY = 256 * 1024;
const DEADLINE_MS = Number(process.env.MC_RELAY_DEADLINE_MS || 120000);
const IDLE_MS = Number(process.env.MC_RELAY_IDLE_MS || 30000);
const STREAM_TEXT_MAX = Number(process.env.MC_RELAY_STREAM_TEXT_MAX || 256 * 1024);
const LINE_MAX = Number(process.env.MC_RELAY_LINE_MAX || 64 * 1024);
const MODEL = "openclaw/mc-agent";
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

export function checkBody(body) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return "the body must be a JSON object";
  if (body.model !== MODEL) return `model must be exactly ${MODEL}`;
  if ("stream" in body && typeof body.stream !== "boolean") return "stream must be a boolean (true streams)";
  if ("stream_options" in body) return "stream_options is set by the relay, not the caller";
  if (typeof body.user !== "string" || !body.user.startsWith("guild-mc:")) return "user must start guild-mc:";
  if (!Array.isArray(body.messages) || body.messages.length === 0) return "messages must be a non-empty list";
  for (const m of body.messages) {
    if (!m || typeof m !== "object" || typeof m.role !== "string" || typeof m.content !== "string") return "each message needs a role and text content";
    if (!["user", "system", "assistant"].includes(m.role)) return "unsupported message role";
  }
  return null;
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
    return res.end('{"ok":true}');
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
  if (!(req.method === "POST" && url.pathname === "/v1/chat/completions")) {
    log({ event: "refused", reason: "path", method: req.method, path: url.pathname });
    return reply(res, 403, "relay_path_refused", "only POST /v1/chat/completions, POST /v1/turns/stop and GET /readyz pass the relay");
  }
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY) {
      log({ event: "refused", reason: "too_large", correlation });
      return reply(res, 413, "relay_too_large", "the request is larger than 256 KB", echo);
    }
    chunks.push(chunk);
  }
  let body;
  try {
    body = parseStrict(Buffer.concat(chunks).toString("utf8"));
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
    const clean = JSON.stringify({ model: MODEL, user: body.user, messages: body.messages.map((m) => ({ role: m.role, content: m.content })) });
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

// Reads MC's SSE and yields the allow-listed events: text deltas, the finish
// reason and the trailing usage. Everything else (ids, model, roles,
// tool_calls, x-openclaw-*) is dropped.
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
    const clean = JSON.stringify({ model: MODEL, user: body.user, stream: true, stream_options: { include_usage: true },
      messages: body.messages.map((m) => ({ role: m.role, content: m.content })) });
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
    read: for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      touch();
      pending += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = pending.indexOf("\n")) >= 0) {
        const line = pending.slice(0, nl).replace(/\r$/, "");
        pending = pending.slice(nl + 1);
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
