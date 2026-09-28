// Start-up self-check for Master Craftsman's own OpenClaw container
// (MC spec v0.8 §2.1, v0.9 §5 C8 and §9). Run by start-mc.sh.
//
//   node selfcheck.mjs static  <config>   the config file (MiniMoi's rules, then OpenClaw's
//                                         own `config validate`), before any gateway
//   node selfcheck.mjs runtime <config>   asks the running, loopback-only gateway:
//                                         effective tools, loaded plugins (the
//                                         gateway's own `health` answer, not the
//                                         cold `plugins list`, which also lists
//                                         bundled providers it never loads),
//                                         scheduler and device pairing
//
// Prints one JSON line {"phase", "failures": [...], "inconclusive": [...]} and exits:
//   0  every check passed
//   3  a VERDICT: a value is wrong, or the gateway refused a request
//      (gateway_request_error / INVALID_REQUEST, CLI exit 1). Repeating it would
//      give the same answer, so start-mc.sh writes a sticky marker and never serves.
//   4  INCONCLUSIVE: a call timed out or hit a transport error, twice. Not
//      sticky; start-mc.sh exits and the restart policy (on-failure:3) retries.
// Never prints a secret value.

import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";

export const MC_ID = "mc-agent";
export const MC_TOOLS = ["session_status"];
export const MC_ROUTE = "minimoi-gateway-mc/minimoi-mc-agent";
// Plugins MC may load; everything else must be off (v0.9 §9). These must
// never be loaded, whatever the list says.
export const ALLOWED_PLUGINS = ["memory-core"];
export const FORBIDDEN_PLUGINS = ["github", "browser", "file-transfer", "document-extract", "web-readability",
  "cos-bounded-search", "device-pair", "canvas", "talk-voice"];
const OPENCLAW = process.env.MINIMOI_OPENCLAW_CLI || "/app/openclaw.mjs";
const CALL_TIMEOUT_MS = "120000";
const CALL_ATTEMPTS = 2;

export class Inconclusive extends Error {
  constructor(message) { super(message); this.name = "Inconclusive"; }
}
export class RequestRefused extends Error {
  constructor(message) { super(message); this.name = "RequestRefused"; }
}

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const sorted = (xs) => [...xs].sort();

export function staticCheck(config, env = process.env) {
  const f = [];
  const entries = config?.agents?.entries ?? {};
  if (!same(Object.keys(entries), [MC_ID])) f.push(`agents are ${JSON.stringify(Object.keys(entries))}, not only mc-agent`);
  const m = entries[MC_ID] ?? {};
  if (!same(m.tools?.allow, MC_TOOLS)) f.push("tools.allow is not exactly session_status");
  if (!same(m.modelPolicy?.allow, [MC_ROUTE])) f.push("modelPolicy.allow is not exactly MC's route");
  if (!same(config?.agents?.defaults?.modelPolicy?.allow, [MC_ROUTE])) f.push("agents.defaults.modelPolicy.allow is not exactly MC's route");
  if (m.model?.primary !== MC_ROUTE) f.push("model.primary is not MC's route");
  if (!same(m.model?.fallbacks, [])) f.push("model has fallbacks");
  if (m.utilityModel !== "") f.push("utilityModel is not empty");
  if (m.decisionModel !== "") f.push("decisionModel is not empty");
  if (m.memory?.search?.enabled !== false) f.push("memory search is not off");
  if (m.tools?.codeMode !== false || m.tools?.swarm !== false) f.push("agent codeMode/swarm not off");
  if (m.tools?.elevated?.enabled !== false) f.push("elevated tools not off");
  if (m.heartbeat?.every !== "0m") f.push("heartbeat is on");
  const providers = config?.models?.providers ?? {};
  if (!same(Object.keys(providers), ["minimoi-gateway-mc"])) f.push("providers are not exactly minimoi-gateway-mc");
  if (providers["minimoi-gateway-mc"]?.apiKey !== "${MC_MODEL_GATEWAY_KEY}") f.push("provider key is not MC_MODEL_GATEWAY_KEY");
  const tools = config?.tools ?? {};
  if (tools.codeMode !== false) f.push("tools.codeMode is not false");
  if (tools.toolSearch !== false) f.push("tools.toolSearch is not false");
  if (tools.links?.enabled !== false) f.push("tools.links is not off");
  if (tools.swarm !== false) f.push("tools.swarm is not false");
  if (tools.web) f.push("tools.web is configured");
  if (config?.gateway?.controlUi?.enabled !== false) f.push("gateway.controlUi.enabled is not false");
  if (config?.cron?.enabled !== false) f.push("cron.enabled is not false");
  const plugins = config?.plugins ?? {};
  if (!same(plugins.allow, ALLOWED_PLUGINS)) f.push(`plugins.allow is not ${JSON.stringify(ALLOWED_PLUGINS)}`);
  if (plugins.entries?.["memory-core"]?.config?.dreaming?.enabled !== false) f.push("dreaming is not off");
  if (config?.skills?.workshop?.autonomous?.mode !== "off") f.push("skill workshop autonomous mode is not off");
  if (config?.update?.checkOnStart !== false) f.push("update.checkOnStart is not false");
  if (config?.models?.catalogRefresh?.enabled !== false) f.push("models.catalogRefresh is not off");
  const key = env.MC_MODEL_GATEWAY_KEY || "";
  if (!key) f.push("MC_MODEL_GATEWAY_KEY is empty");
  else if (key === (env.OPENCLAW_GATEWAY_TOKEN || "")) f.push("MC_MODEL_GATEWAY_KEY equals MC's own OpenClaw token");
  for (const name of ["MINIMOI_MODEL_GATEWAY_KEY", "COS_AGENT_A_GATEWAY_TOKEN", "ANTHROPIC_API_KEY", "XAI_API_KEY",
    "OPENAI_API_KEY", "DATABASE_URL", "LITELLM_MASTER_KEY"]) {
    if (env[name]) f.push(`${name} is in MC's environment`);
  }
  return f;
}

// The gateway's `health` answer: {plugins: {loaded: [...], errors: [...]}}.
export function pluginCheck(health) {
  const f = [];
  const loaded = health?.plugins?.loaded;
  if (!Array.isArray(loaded)) return { failures: ["health answered without plugins.loaded"], loaded: [] };
  const extra = loaded.filter((id) => !ALLOWED_PLUGINS.includes(id));
  if (extra.length) f.push(`plugins loaded beyond the allow list: ${sorted(extra).join(", ")}`);
  const bad = loaded.filter((id) => FORBIDDEN_PLUGINS.includes(id));
  if (bad.length) f.push(`forbidden plugins loaded: ${sorted(bad).join(", ")}`);
  if ((health?.plugins?.errors ?? []).length) f.push(`plugin errors: ${JSON.stringify(health.plugins.errors).slice(0, 160)}`);
  return { failures: f, loaded: sorted(loaded) };
}

// A `gateway call` answer: a refusal is a verdict; only a timeout or a
// transport error is inconclusive (the #248 F1 lesson).
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

function cli(args) {
  const run = spawnSync(process.execPath, [OPENCLAW, ...args],
    { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], maxBuffer: 16 * 1024 * 1024, timeout: 180000 });
  return run;
}

function call(method, params = {}) {
  for (let attempt = 1; ; attempt += 1) {
    const run = cli(["gateway", "call", method, "--timeout", CALL_TIMEOUT_MS, "--params", JSON.stringify(params), "--json"]);
    try { return interpret(method, run.status, run.stdout, run.error?.code); } catch (error) {
      if (!(error instanceof Inconclusive) || attempt >= CALL_ATTEMPTS) throw error;
    }
  }
}

// `openclaw config validate --json` answer: {"valid", "warnings": [...]}.
export function validateVerdict(answer) {
  const f = [];
  if (answer?.valid !== true) f.push(`config validate: invalid ${JSON.stringify(answer?.errors ?? answer?.issues ?? "").slice(0, 200)}`);
  const warnings = answer?.warnings ?? [];
  if (warnings.length) f.push(`config validate: ${warnings.length} warning(s): ${JSON.stringify(warnings).slice(0, 200)}`);
  return f;
}

export function effectiveToolIds(answer) {
  const ids = [];
  for (const group of answer?.groups ?? []) for (const tool of group.tools ?? []) ids.push(tool.id);
  return ids;
}

export function runtimeCheck(rpc = call) {
  const failures = [];
  const inconclusive = [];
  const blame = (prefix, error) => {
    if (error?.name === "Inconclusive") inconclusive.push(`${prefix}${error.message}`);
    else failures.push(`${prefix}${String(error?.message || error).split("\n")[0].slice(0, 200)}`);
  };
  try {
    const key = `agent:${MC_ID}:minimoi-selfcheck`;
    const created = rpc("sessions.create", { agentId: MC_ID, key });
    if (created?.ok !== true || created?.runStarted !== false) failures.push("sessions.create did not return ok with runStarted false");
    const got = sorted(effectiveToolIds(rpc("tools.effective", { sessionKey: key })));
    if (!same(got, sorted(MC_TOOLS))) failures.push(`effective tools ${JSON.stringify(got)} != ${JSON.stringify(MC_TOOLS)}`);
  } catch (error) {
    blame("mc-agent: ", error);
  }
  try {
    const status = rpc("cron.status");
    if (status?.enabled !== false) failures.push("scheduler is enabled");
    const list = rpc("cron.list", { includeDisabled: true, limit: 200 });
    const enabled = (list?.jobs ?? []).filter((job) => job.enabled !== false).map((job) => job.name || job.id);
    if (enabled.length) failures.push(`enabled jobs: ${enabled.join(", ")}`);
  } catch (error) {
    blame("cron: ", error);
  }
  try {
    const checked = pluginCheck(rpc("health"));
    failures.push(...checked.failures);
  } catch (error) {
    blame("health: ", error);
  }
  try {
    const pairs = rpc("device.pair.list");
    const pending = pairs?.pending, paired = pairs?.paired;
    if (!Array.isArray(pending) || !Array.isArray(paired)) failures.push("device.pair.list answered without pending/paired lists");
    else if (pending.length || paired.length) failures.push(`device pairing not empty (${pending.length}/${paired.length})`);
  } catch (error) {
    blame("device.pair.list: ", error);
  }
  return { failures, inconclusive };
}

export function exitCode({ failures = [], inconclusive = [] }) {
  return failures.length ? 3 : inconclusive.length ? 4 : 0;
}

function main(argv) {
  const [phase, configPath] = argv;
  let result = { failures: [], inconclusive: [] };
  try {
    if (phase === "static") {
      result.failures = staticCheck(JSON.parse(readFileSync(configPath, "utf8")));
      // OpenClaw's own schema check of the exact file (#250 review F3): an
      // invalid config or any warning is a verdict, never an in-start retry.
      const run = cli(["config", "validate", "--json"]);
      try {
        result.failures.push(...validateVerdict(interpret("config validate", run.status === 1 ? 0 : run.status, run.stdout, run.error?.code)));
      } catch (error) {
        if (error?.name === "Inconclusive") result.inconclusive.push(error.message);
        else result.failures.push(String(error?.message || error).slice(0, 200));
      }
    } else if (phase === "runtime") {
      result = runtimeCheck();
    } else {
      console.error("usage: selfcheck.mjs static <config> | runtime <config>");
      return 64;
    }
  } catch (error) {
    result.failures.push(`${phase}: ${String(error?.message || error).slice(0, 160)}`);
  }
  console.log(JSON.stringify({ phase, ...result }));
  return exitCode(result);
}

if (import.meta.url === `file://${process.argv[1]}`) process.exit(main(process.argv.slice(2)));
