// A local, OpenAI-compatible stand-in for the model gateway, for the no-spend
// Master Craftsman isolation gates (MC spec v0.7 §2.2, "capture server").
// It answers from a script and records every request, so real OpenClaw turns
// run with no provider and no spend. It never calls anything.
//
//   node capture-server.mjs            (listens on :4000 model API, :4001 control)
//
// Env: CAPTURE_COS_KEY, CAPTURE_MC_KEY: the two placeholder keys, used only to
// LABEL each request's key as "cos-key", "mc-key", "none" or "other". Raw key
// values are never recorded or printed.
//
// Control API on :4001 (JSON):
//   POST /script {"model": "<route>", "steps": [step, ...], "replace": bool}  queue replies for a route
//        step: {"text": "..."} | {"tool": "name", "args": {...}} | {"hang": true}
//              | {"malformed": true} | {"status": 401} | {"usage_prompt_tokens": N, "text": "..."}
//   GET  /log            every recorded request (summary + body)
//   POST /reset          clear the log and the scripts
import http from "node:http";

const COS_KEY = process.env.CAPTURE_COS_KEY || "";
const MC_KEY = process.env.CAPTURE_MC_KEY || "";
const log = [];
const scripts = new Map();
const hung = new Set();

// CAPTURE_KEYS: {"label": "value", ...} labels more keys (for example the
// gateway's provider keys when this server stands in for Anthropic).
const EXTRA_KEYS = JSON.parse(process.env.CAPTURE_KEYS || "{}");
function keyLabel(header) {
  const value = String(header || "").replace(/^Bearer\s+/i, "");
  if (!value) return "none";
  if (COS_KEY && value === COS_KEY) return "cos-key";
  if (MC_KEY && value === MC_KEY) return "mc-key";
  for (const [label, v] of Object.entries(EXTRA_KEYS)) if (v && value === v) return label;
  return "other";
}

// Anthropic Messages API shape, for when this server stands in for api.anthropic.com.
function anthropicMessage(model, step) {
  return {
    id: `msg_cap_${log.length}`, type: "message", role: "assistant", model,
    content: [{ type: "text", text: step.text ?? "capture ok" }], stop_reason: "end_turn", stop_sequence: null,
    usage: { input_tokens: step.usage_prompt_tokens ?? 50, output_tokens: step.output_tokens ?? 5 },
  };
}

function readBody(req) {
  return new Promise((resolve) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
  });
}

function completion(model, step) {
  const message = step.tool
    ? { role: "assistant", content: null, tool_calls: [{ id: `call_${log.length}`, type: "function", function: { name: step.tool, arguments: JSON.stringify(step.args || {}) } }] }
    : { role: "assistant", content: step.text ?? "capture ok" };
  return {
    id: `cap-${log.length}`, object: "chat.completion", created: Math.floor(Date.now() / 1000), model,
    choices: [{ index: 0, message, finish_reason: step.tool ? "tool_calls" : "stop" }],
    usage: { prompt_tokens: step.usage_prompt_tokens ?? 50, completion_tokens: 5, total_tokens: (step.usage_prompt_tokens ?? 50) + 5 },
  };
}

function streamCompletion(res, model, step) {
  const full = completion(model, step);
  const msg = full.choices[0].message;
  const base = { id: full.id, object: "chat.completion.chunk", created: full.created, model };
  res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
  const send = (obj) => res.write(`data: ${JSON.stringify(obj)}\n\n`);
  if (msg.tool_calls) {
    send({ ...base, choices: [{ index: 0, delta: { role: "assistant", tool_calls: msg.tool_calls.map((t, i) => ({ index: i, ...t })) }, finish_reason: null }] });
  } else {
    send({ ...base, choices: [{ index: 0, delta: { role: "assistant", content: msg.content }, finish_reason: null }] });
  }
  send({ ...base, choices: [{ index: 0, delta: {}, finish_reason: full.choices[0].finish_reason }], usage: full.usage });
  res.end("data: [DONE]\n\n");
}

const api = http.createServer(async (req, res) => {
  const raw = await readBody(req);
  let body = null;
  try { body = raw ? JSON.parse(raw) : null; } catch { body = null; }
  const model = body?.model ?? null;
  const entry = {
    seq: log.length, at: new Date().toISOString(), method: req.method, path: req.url,
    key: keyLabel(req.headers.authorization || req.headers["x-api-key"]),
    api_key_label: keyLabel(req.headers["x-api-key"]), auth_label: keyLabel(req.headers.authorization),
    model, stream: body?.stream === true, tools: (body?.tools || []).map((t) => t?.function?.name || t?.name || "?"),
    body: raw.slice(0, 400000),
  };
  log.push(entry);
  const queue = scripts.get(model) || [];
  const step = queue.length ? queue.shift() : { text: "capture ok" };
  entry.step = Object.keys(step).join(",");
  if (step.hang) { hung.add(res); return; }            // never answer
  if (step.status) {
    res.writeHead(step.status, { "content-type": "application/json" });
    return res.end(JSON.stringify({ error: { message: step.message || `capture status ${step.status}`,
      type: step.type || "auth_error", code: String(step.status) } }));
  }
  if (String(req.url).startsWith("/v1/messages")) {
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify(anthropicMessage(model, step)));
  }
  if (step.malformed) {
    res.writeHead(200, { "content-type": "text/event-stream" });
    return res.end("data: {not json\n\ndata: [DONE\n\n");
  }
  if (String(req.url).startsWith("/v1/responses")) {       // OpenAI/xAI Responses API shape
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify({
      id: `resp_cap_${log.length}`, object: "response", created_at: Math.floor(Date.now() / 1000), status: "completed", model,
      output: [{ type: "message", id: `msg_cap_${log.length}`, status: "completed", role: "assistant",
                 content: [{ type: "output_text", text: step.text ?? "capture ok", annotations: [] }] }],
      usage: { input_tokens: 50, output_tokens: 5, total_tokens: 55 },
    }));
  }
  if (!String(req.url).includes("/chat/completions")) {
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify({ id: "cap-other", output: [], usage: { input_tokens: 1, output_tokens: 1 } }));
  }
  if (body?.stream) return streamCompletion(res, model, step);
  res.writeHead(200, { "content-type": "application/json" });
  res.end(JSON.stringify(completion(model, step)));
});

const control = http.createServer(async (req, res) => {
  const raw = await readBody(req);
  const reply = (obj) => { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify(obj)); };
  if (req.method === "GET" && req.url === "/log") return reply(log);
  if (req.method === "POST" && req.url === "/reset") {
    log.length = 0; scripts.clear();
    for (const r of hung) { try { r.destroy(); } catch {} }
    hung.clear();
    return reply({ ok: true });
  }
  if (req.method === "POST" && req.url === "/script") {
    const { model, steps, replace } = JSON.parse(raw || "{}");
    scripts.set(model, [...(replace ? [] : scripts.get(model) || []), ...(steps || [])]);
    return reply({ ok: true, queued: scripts.get(model).length });
  }
  res.writeHead(404); res.end();
});

api.listen(4000, "0.0.0.0");
control.listen(4001, "0.0.0.0");
console.log("capture server on :4000 (model API) and :4001 (control)");
