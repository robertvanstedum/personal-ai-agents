// Stand-in for `openclaw gateway call <method> ... --json` and `openclaw plugins
// list --json`, used by docker/mc-agent/selfcheck.mjs via MINIMOI_OPENCLAW_CLI.
//   FAKE_TOOLS=a,b            MC's effective tools (default session_status)
//   FAKE_REQUEST_ERROR=method a gateway_request_error (INVALID_REQUEST, exit 1) for that method
//   FAKE_TRANSPORT_ERROR=method a gateway_transport_error (timeout, exit 1)
//   FAKE_PLUGINS=a,b          plugins the gateway's health answer reports loaded (default memory-core)
//   FAKE_ENABLED_JOB=name     a job reported enabled
const args = process.argv.slice(2);
//   FAKE_CONFIG_INVALID=1 / FAKE_CONFIG_WARNING=1   `config validate` answers invalid / with a warning
if (args[0] === "config" && args[1] === "validate") {
  const invalid = process.env.FAKE_CONFIG_INVALID === "1";
  const warnings = process.env.FAKE_CONFIG_WARNING === "1" ? [{ path: "tools.x", message: "Unrecognized key" }] : [];
  console.log(JSON.stringify({ valid: !invalid, path: process.env.OPENCLAW_CONFIG_PATH, warnings,
    ...(invalid ? { errors: [{ path: "agents", message: "bad" }] } : {}) }));
  process.exit(invalid ? 1 : 0);
}
const method = args[2];
if (process.env.FAKE_TRANSPORT_ERROR === method) {
  console.log(JSON.stringify({ ok: false, error: { type: "gateway_transport_error", kind: "timeout" } }));
  process.exit(1);
}
if (process.env.FAKE_REQUEST_ERROR === method) {
  console.log(JSON.stringify({ ok: false, error: { type: "gateway_request_error", code: "INVALID_REQUEST", message: "Unknown agent id", retryable: false } }));
  process.exit(1);
}
const params = JSON.parse(args[args.indexOf("--params") + 1] || "{}");
let out;
if (method === "sessions.create") out = { ok: true, key: params.key, runStarted: false };
else if (method === "tools.effective") out = { groups: [{ id: "core", tools: (process.env.FAKE_TOOLS ?? "session_status").split(",").map((id) => ({ id })) }] };
else if (method === "cron.status") out = { enabled: false, jobs: 2 };
else if (method === "cron.list") out = { jobs: [{ name: "heartbeat-mc-agent", enabled: false }, ...(process.env.FAKE_ENABLED_JOB ? [{ name: process.env.FAKE_ENABLED_JOB, enabled: true }] : [])] };
else if (method === "health") out = { ok: true, plugins: { loaded: (process.env.FAKE_PLUGINS ?? "memory-core").split(",").filter(Boolean), errors: [] } };
else if (method === "device.pair.list") out = { pending: [], paired: [] };
else out = { ok: false, error: { type: "gateway_request_error", code: "INVALID_REQUEST", message: `unknown method: ${method}` } };
console.log(JSON.stringify(out, null, 2));
process.exit(out.ok === false ? 1 : 0);
