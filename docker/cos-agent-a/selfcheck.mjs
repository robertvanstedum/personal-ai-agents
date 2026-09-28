// Start-up self-check for the combined CoS + Master Craftsman OpenClaw
// (MC spec v0.7 §1.4, N11 split by agent). Run by start-with-mc.sh, staging
// only; production never runs it.
//
//   node selfcheck.mjs static  <config> <cos-only|combined>
//   node selfcheck.mjs runtime <config> <cos-only|combined>
//
// "static" reads the config file only (no gateway needed) and runs before the
// gateway starts. "runtime" asks the running gateway, over loopback, for each
// agent's effective tools and for the scheduler state.
//
// Prints one JSON line {"cos": {...}, "mc": {...}, "inconclusive": {"cos": [...], "mc": [...]}} and exits:
//   0  every check passed
//   2  a CoS check failed (a verdict: CoS's own set is wrong, or the gateway
//      refused a CoS request, e.g. INVALID_REQUEST)
//   3  only an MC check failed (a verdict, including a gateway request error
//      on an MC-scoped call, or a scheduled job in the combined config)
//   4  a CoS-scoped call could not be answered (timeout / transport error, twice)
//   5  only MC-scoped calls could not be answered (CoS's calls completed and passed)
// A gateway answer of {"ok": false, "error": {"type": "gateway_request_error"}}
// (for example "Unknown agent id", exit 1 from `gateway call`) is a VERDICT
// against the agent the call was for, never "inconclusive": repeating it
// would give the same answer. Only a timeout or transport error, retried once,
// is inconclusive. start-with-mc.sh never lets an MC-side result keep CoS
// down (N11). Never prints a secret value: keys are compared, not shown.

import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";

export const COS_ID = "cos-agent-a";
export const MC_ID = "mc-agent";
export const COS_TOOLS = ["session_status", "web_search"];
export const MC_TOOLS = ["session_status"];
export const COS_ROUTE = "minimoi-gateway/minimoi-cos-agent";
export const MC_ROUTE = "minimoi-gateway-mc/minimoi-mc-agent";
const OPENCLAW = process.env.MINIMOI_OPENCLAW_CLI || "/app/openclaw.mjs";
const CALL_TIMEOUT_MS = "120000";
const CALL_ATTEMPTS = 2;

// The gateway could not be asked (timeout, transport error, no JSON answer).
export class Inconclusive extends Error {
  constructor(message) { super(message); this.name = "Inconclusive"; }
}

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const sorted = (xs) => [...xs].sort();

export function staticCheck(config, mode, env = process.env) {
  const cos = [];
  const mc = [];
  const entries = config?.agents?.entries ?? {};
  const cosEntry = entries[COS_ID];

  // CoS's own set. A mistake here is a CoS failure in either mode.
  if (!cosEntry) cos.push("cos entry missing");
  else {
    if (!same(cosEntry.tools?.allow, COS_TOOLS)) cos.push("cos tools.allow is not exactly session_status, web_search");
    if (!same(cosEntry.modelPolicy?.allow, [COS_ROUTE])) cos.push("cos modelPolicy.allow is not exactly its own route");
    if (cosEntry.model?.primary !== COS_ROUTE) cos.push("cos model.primary is not its own route");
    if (!same(cosEntry.model?.fallbacks, [])) cos.push("cos has model fallbacks");
    if (cosEntry.heartbeat?.every !== "0m") cos.push("cos heartbeat is on");
    if (cosEntry.tools?.elevated?.enabled !== false) cos.push("cos elevated tools not off");
  }
  // Background model turns: off for the whole process. In the combined
  // config a mistake here is the combined file's (spec §1.4: "a job
  // scheduled" is an MC failure), so CoS falls back to the CoS-only config.
  const shared = mode === "combined" ? mc : cos;
  const plugins = config?.plugins ?? {};
  if (config?.cron?.enabled !== false) shared.push("cron.enabled is not false");
  if (plugins.entries?.["memory-core"]?.config?.dreaming?.enabled !== false) shared.push("dreaming is not off");
  if (config?.skills?.workshop?.autonomous?.mode !== "off") shared.push("skill workshop autonomous mode is not off");
  if (config?.update?.checkOnStart !== false) shared.push("update.checkOnStart is not false");
  if (config?.models?.catalogRefresh?.enabled !== false) shared.push("models.catalogRefresh is not off");

  if (mode === "cos-only") {
    if (entries[MC_ID]) cos.push("cos-only config names mc-agent");
    return { cos, mc };
  }

  // Combined: MC's set, and the process-wide isolation settings. A mistake
  // here costs MC, never CoS (N11): CoS falls back to the CoS-only config.
  const m = entries[MC_ID];
  if (!m) mc.push("mc entry missing");
  else {
    if (!same(m.tools?.allow, MC_TOOLS)) mc.push("mc tools.allow is not exactly session_status");
    if (!same(m.modelPolicy?.allow, [MC_ROUTE])) mc.push("mc modelPolicy.allow is not exactly its own route");
    if (m.model?.primary !== MC_ROUTE) mc.push("mc model.primary is not its own route");
    if (!same(m.model?.fallbacks, [])) mc.push("mc has model fallbacks");
    if (m.utilityModel !== "") mc.push("mc utilityModel is not empty");
    if (m.decisionModel !== "") mc.push("mc decisionModel is not empty");
    if (m.memory?.search?.enabled !== false) mc.push("mc memory search is not off");
    if (m.memory?.search?.rememberAcrossConversations !== false) mc.push("mc rememberAcrossConversations is not off");
    if (m.tools?.codeMode !== false) mc.push("mc tools.codeMode is not false");
    if (m.tools?.swarm !== false) mc.push("mc tools.swarm is not false");
    if (m.tools?.elevated?.enabled !== false) mc.push("mc elevated tools not off");
    if (m.heartbeat?.every !== "0m") mc.push("mc heartbeat is on");
    if (!same(m.skills, [])) mc.push("mc skills not empty");
    if (m.workspace !== "/home/node/.openclaw/workspace-mc") mc.push("mc workspace is not workspace-mc");
    if (m.sandbox?.mode !== "off") mc.push("mc sandbox mode unexpected");
  }
  const provider = config?.models?.providers?.["minimoi-gateway-mc"];
  if (!provider) mc.push("mc provider missing");
  else if (provider.apiKey !== "${MC_MODEL_GATEWAY_KEY}") mc.push("mc provider does not use MC_MODEL_GATEWAY_KEY");
  const tools = config?.tools ?? {};
  if (config?.agents?.ownership !== "explicit") mc.push("agents.ownership is not explicit");
  if (tools.sessions?.visibility !== "self") mc.push("tools.sessions.visibility is not self");
  if (tools.agentToAgent?.enabled !== false) mc.push("tools.agentToAgent is not off");
  if (tools.codeMode !== false) mc.push("tools.codeMode is not false");
  if (tools.toolSearch !== false) mc.push("tools.toolSearch is not false");
  if (tools.links?.enabled !== false) mc.push("tools.links is not off");
  if (tools.swarm !== false) mc.push("tools.swarm is not false");

  // MC's key: present, and never CoS's key (compared, never printed).
  const mcKey = env.MC_MODEL_GATEWAY_KEY || "";
  if (!mcKey) mc.push("MC_MODEL_GATEWAY_KEY is empty");
  else if (mcKey === (env.MINIMOI_MODEL_GATEWAY_KEY || "")) mc.push("MC_MODEL_GATEWAY_KEY equals the CoS gateway key");
  return { cos, mc };
}

// Thrown for a call the gateway answered with a refusal: a verdict.
export class RequestRefused extends Error {
  constructor(message) { super(message); this.name = "RequestRefused"; }
}

export function interpret(method, status, stdout, errorCode) {
  const start = (stdout || "").indexOf("{");
  let answer = null;
  if (start >= 0) {
    try { answer = JSON.parse(stdout.slice(start)); } catch { answer = null; }
  }
  if (answer?.ok === false) {
    const err = answer.error || {};
    if (err.type === "gateway_transport_error") throw new Inconclusive(`${method}: ${err.kind || "transport error"}`);
    throw new RequestRefused(`${method}: ${err.code || err.type || "refused"}: ${String(err.message || "").slice(0, 120)}`);
  }
  if (status === 0 && answer) return answer;
  if (errorCode === "ETIMEDOUT" || status === null) throw new Inconclusive(`${method}: ${errorCode || "timed out"}`);
  if (!answer) throw new Inconclusive(`${method}: no JSON answer (exit ${status})`);
  throw new RequestRefused(`${method}: exit ${status}`);
}

function callOnce(method, params) {
  const run = spawnSync(
    process.execPath,
    [OPENCLAW, "gateway", "call", method, "--timeout", CALL_TIMEOUT_MS, "--params", JSON.stringify(params), "--json"],
    { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], maxBuffer: 16 * 1024 * 1024, timeout: 180000 },
  );
  return interpret(method, run.status, run.stdout, run.error?.code);
}

function call(method, params = {}) {
  for (let attempt = 1; ; attempt += 1) {
    try { return callOnce(method, params); } catch (error) {
      if (!(error instanceof Inconclusive) || attempt >= CALL_ATTEMPTS) throw error;
    }
  }
}

export function effectiveToolIds(answer) {
  const ids = [];
  for (const group of answer?.groups ?? []) for (const tool of group.tools ?? []) ids.push(tool.id);
  return ids;
}

export function runtimeCheck(mode, rpc = call) {
  const cos = [];
  const mc = [];
  const inconclusive = { cos: [], mc: [] };
  const blame = (failures, side, prefix, error) => {
    if (error instanceof Inconclusive || error?.name === "Inconclusive") inconclusive[side].push(`${prefix}${error.message}`);
    else failures.push(`${prefix}${String(error?.message || error).split("\n")[0].slice(0, 200)}`);
  };
  const agents = mode === "combined"
    ? [[COS_ID, COS_TOOLS, cos, "cos"], [MC_ID, MC_TOOLS, mc, "mc"]]
    : [[COS_ID, COS_TOOLS, cos, "cos"]];
  for (const [id, want, failures, side] of agents) {
    try {
      const key = `agent:${id}:minimoi-selfcheck`;
      const created = rpc("sessions.create", { agentId: id, key });
      if (created?.ok !== true || created?.runStarted !== false) failures.push(`${id}: sessions.create did not return ok with runStarted false`);
      const got = effectiveToolIds(rpc("tools.effective", { sessionKey: key }));
      if (!same(sorted(got), sorted(want))) failures.push(`${id}: effective tools ${JSON.stringify(sorted(got))} != ${JSON.stringify(sorted(want))}`);
    } catch (error) {
      blame(failures, side, `${id}: `, error);
    }
  }
  // Scheduled jobs: MC's side in combined mode (spec §1.4: the combined config
  // is at fault, and the CoS-only fallback is checked on its own), CoS's otherwise.
  const jobSide = mode === "combined" ? "mc" : "cos";
  const jobFailures = jobSide === "mc" ? mc : cos;
  try {
    const status = rpc("cron.status");
    if (status?.enabled !== false) jobFailures.push("scheduler is enabled");
    const list = rpc("cron.list", { includeDisabled: true, limit: 200 });
    const enabled = (list?.jobs ?? []).filter((job) => job.enabled !== false).map((job) => job.name || job.id);
    if (enabled.length) jobFailures.push(`enabled jobs: ${enabled.join(", ")}`);
  } catch (error) {
    blame(jobFailures, jobSide, "cron: ", error);
  }
  return { cos, mc, inconclusive };
}

export function verdict({ cos, mc, inconclusive = { cos: [], mc: [] } }) {
  const code = cos.length ? 2 : mc.length ? 3 : inconclusive.cos.length ? 4 : inconclusive.mc.length ? 5 : 0;
  return { code, cos: { ok: !cos.length, failures: cos }, mc: { ok: !mc.length, failures: mc }, inconclusive };
}

function main(argv) {
  const [phase, configPath, mode] = argv;
  if (!["static", "runtime"].includes(phase) || !configPath || !["cos-only", "combined"].includes(mode)) {
    console.error("usage: selfcheck.mjs static|runtime <config> cos-only|combined");
    return 64;
  }
  let result;
  try {
    const config = JSON.parse(readFileSync(configPath, "utf8"));
    const first = staticCheck(config, mode);
    result = phase === "static" ? first : (() => {
      const second = runtimeCheck(mode);
      return { cos: [...first.cos, ...second.cos], mc: [...first.mc, ...second.mc], inconclusive: second.inconclusive };
    })();
  } catch (error) {
    const reason = `config unreadable: ${String(error?.message || error).slice(0, 160)}`;
    result = mode === "combined" ? { cos: [], mc: [reason] } : { cos: [reason], mc: [] };
  }
  const v = verdict(result);
  console.log(JSON.stringify({ phase, mode, cos: v.cos, mc: v.mc, inconclusive: v.inconclusive }));
  return v.code;
}

if (import.meta.url === `file://${process.argv[1]}`) process.exit(main(process.argv.slice(2)));
