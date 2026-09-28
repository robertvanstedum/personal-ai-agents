// Stand-in for `openclaw gateway call <method> --params <json> --json`, used by
// selfcheck.mjs through MINIMOI_OPENCLAW_CLI in tests/cos/test_start_with_mc.py.
// FAKE_TOOLS_JSON: {"<agentId>": [tool ids]} overrides the effective tools.
// FAKE_ENABLED_JOB: a job name reported as enabled (combined config only).
import { readFileSync } from "node:fs";
const args = process.argv.slice(2);
const method = args[2];
const params = JSON.parse(args[args.indexOf("--params") + 1] || "{}");
const tools = { "cos-agent-a": ["session_status", "web_search"], "mc-agent": ["session_status"],
  ...JSON.parse(process.env.FAKE_TOOLS_JSON || "{}") };
let out;
// FAKE_TRANSPORT_ERROR: a method that never gets an answer (a slow, busy host).
if (process.env.FAKE_TRANSPORT_ERROR === method) {
  console.log(JSON.stringify({ ok: false, error: { type: "gateway_transport_error", kind: "timeout" } }));
  process.exit(1);
}
if (method === "sessions.create") out = { ok: true, key: params.key, runStarted: false };
else if (method === "tools.effective") {
  const agent = String(params.sessionKey).split(":")[1];
  out = { agentId: agent, groups: [{ id: "core", tools: (tools[agent] || []).map((id) => ({ id })) }] };
} else if (method === "cron.status") out = { enabled: false, jobs: 2 };
else if (method === "cron.list") {
  const jobs = [{ name: "heartbeat-cos-agent-a", enabled: false }];
  // Only the combined config "creates" the job (as a second agent would).
  const combined = readFileSync(process.env.OPENCLAW_CONFIG_PATH, "utf8").includes('"mc-agent"');
  if (process.env.FAKE_ENABLED_JOB && combined) jobs.push({ name: process.env.FAKE_ENABLED_JOB, enabled: true });
  out = { jobs };
} else out = { ok: false };
console.log(JSON.stringify(out, null, 2));
