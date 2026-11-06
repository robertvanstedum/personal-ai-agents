/** CoS bound tools (F2 design v0.3, step 2). The CoS counterpart of MC's minimoi-tools.
 *
 * Every bound call (cos_actions, cos_tasks, cos_board) carries only host context (ctx.sessionKey, ctx.agentId, and the run and tool-call ids sealed
 * by this plugin's before_tool_call hook) plus the model's arguments. cos-scheduler's listener resolves the sealed run
 * to the live turn's binding and refuses otherwise; who, why, where and when come from that binding, never the model.
 *
 * Native write, edit and exec calls (Robert, Q1): never blocked. The hook reports each one to the listener's native
 * log, fire-and-forget, with the same sealed identity; a failed report never changes what the agent may do.
 *
 * Credential exposure: cos-meeting must be workspace-only with the credential outside its workspace, or no tools are
 * offered. cos-agent-a keeps native exec in owner turns (Q1), so its exec can read the credential: the recorded
 * residual. Its tools are offered only when the operator lists it in acceptCredentialReadableBy.
 */
import {runBoundSealer, backendCallId, tokenFromFile, boundedJson, RUN_ID, CALL_ID} from './shared.mjs';
import {randomBytes} from 'node:crypto';

export const ENDPOINT_PATH = '/internal/cos/v1';
export const NATIVE = new Set(['write', 'edit', 'exec', 'apply_patch', 'process']);
// Meeting entries (review S2/S3): the hook is the second gate behind the config deny list. cos-meeting may call only
// the bound tool and bounded search; cos-closed (a room with anyone outside Robert's team) may call nothing.
export const RESTRICTED = Object.freeze({'cos-meeting': new Set(['cos_actions', 'cos_tasks', 'web_search']), 'cos-closed': new Set()});
export const BOUND = new Set(['cos_actions', 'cos_tasks', 'cos_board']);
const STATE = Symbol.for('cos-bound/process-state/v1');
globalThis[STATE] ??= Object.freeze({instance: randomBytes(16).toString('hex'), sealKey: randomBytes(32)});
export const INSTANCE = globalThis[STATE].instance;
const BINDINGS = Symbol.for('cos-bound/run-bindings/v1');
globalThis[BINDINGS] ??= new Map();
const PROCESS_SEALER = runBoundSealer({key: globalThis[STATE].sealKey, entries: globalThis[BINDINGS],
  onRefuse: detail => console.warn('cos-bound: seal refused ' + JSON.stringify(detail))});
const UNKNOWN = 'CoS could not confirm what happened. Do not repeat it; tell Robert.';
const text = (s, details) => ({content: [{type: 'text', text: s}], details});
const str = {type: 'string'};

export const ACTIONS_SPEC = {
  name: 'cos_actions', label: 'CoS Actions',
  description: 'Use Robert’s Actions workspace to keep agreed work and archive briefings. Types and priorities are independent of status. This records work; it does not perform external actions. List/get before changing an existing item; pass its current revision. Use open for a new record (default assigned to CoS), comment for details or briefing content (up to 4000 characters; split longer content into ordered comments), update for progress, close with done or dropped for completed or cancelled. Only say saved when the status is done. On failure or timeout, do not recreate or blindly retry: list/get to reconcile first. Never claim an action was performed just because it was recorded. In a meeting you can read but not change records.',
  parameters: {type: 'object', additionalProperties: false, required: ['operation'], properties: {
    operation: {type: 'string', enum: ['list', 'get', 'open', 'update', 'comment', 'close']},
    item_id: str, revision: {type: 'integer', minimum: 1}, offset: {type: 'integer', minimum: 0},
    title: {type: 'string', maxLength: 200},
    kind: {type: 'string', enum: ['action', 'briefing', 'follow-up', 'research', 'decision', 'risk']},
    priority: {type: 'integer', minimum: 1, maximum: 3}, status: {type: 'string', enum: ['open', 'doing', 'waiting']},
    with: {type: 'string', enum: ['cos', 'robert', 'mc']}, text: {type: 'string', maxLength: 4000},
    note: {type: 'string', maxLength: 2000}, outcome: {type: 'string', enum: ['done', 'dropped']}}},
};

export const TASKS_SPEC = {
  name: 'cos_tasks', label: 'CoS Tasks',
  description: 'Propose tracked work from what Robert asked in this conversation, or check a task. propose: give the outcome, lead and holder (mc) and done_when; it records your proposal only. Nothing starts until Robert confirms it in his task view, so say exactly that and never claim the task started. One proposal per Robert message. status: give a task_id to read its state. In a meeting you can only use status.',
  parameters: {type: 'object', additionalProperties: false, required: ['operation'], properties: {
    operation: {type: 'string', enum: ['propose', 'status']},
    outcome: {type: 'string', maxLength: 500}, done_when: {type: 'string', maxLength: 500},
    lead: {type: 'string', enum: ['mc']}, holder: {type: 'string', enum: ['mc']},
    task_id: {type: 'string', maxLength: 80}}},
};

// The Board, bound (F2 M2). Same description and fields the agent knew from the old cos-board plugin; the key now
// lives in cos-scheduler and the call is admitted against this turn's binding before anything is signed.
const note = {type: 'string', minLength: 1, maxLength: 280};
const label = {type: ['string', 'null'], enum: ['decide', 'blocked', 'remember', 'followup', 'idea', 'fyi', null]};
export const BOARD_SPEC = {
  name: 'cos_board', label: 'CoS Board',
  description: 'Read and write Robert’s CoS Board, scoped to CoS. Use save only when Robert asks to keep a note; preserve his language, maximum 280 characters, never truncate. List/read to identify existing notes; clarify ambiguous targets. Read the current semantic_revision before update or undo and pass expected_revision. update changes text, label or done (true to complete, false to reopen). result recovers a prior operation receipt. undo reverses your own specified saved operation only when Robert asks; no force or permanent delete. Only report saved when state is verified with a receipt. On unknown, keep the operation_id and use result; never create a replacement. Board and Actions are separate. Photos use the Board UI. Never copy private material to a room without Robert’s permission. Not available in meetings.',
  parameters: {type: 'object', additionalProperties: false, required: ['operation'], properties: {
    operation: {type: 'string', enum: ['list', 'read', 'save', 'update', 'result', 'undo', 'capabilities']},
    query: {type: 'string', maxLength: 200}, state: {type: 'string', enum: ['active', 'done', 'all']},
    limit: {type: 'integer', minimum: 1, maximum: 20}, cursor: str, note_ref: str, operation_ref: str,
    expected_revision: {type: 'integer', minimum: 1}, text: note, label,
    changes: {type: 'object', additionalProperties: false, minProperties: 1,
              properties: {text: note, label, done: {type: 'boolean'}}}}},
};

export function endpointUrl(endpoint) {
  const url = new URL(endpoint);
  const internal = url.protocol === 'http:' && url.hostname === 'cos-scheduler' && url.port !== '';
  const loopback = url.protocol === 'http:' && url.hostname === '127.0.0.1' && url.port !== '';
  if ((!internal && !loopback) || url.username || url.password || url.search || url.hash
      || url.pathname !== ENDPOINT_PATH) throw new Error('CoS tool endpoint invalid');
  return url;
}

function headers(token, {sessionKey, agentId, run, toolCallId, instance}) {
  return {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'X-CoS-Session': sessionKey,
          'X-CoS-Agent': agentId, 'X-CoS-Run': run, 'X-CoS-Tool-Call': toolCallId, 'X-CoS-Instance': instance};
}

// One announcement state per endpoint, credential and instance, shared by every registration in this process (OpenClaw
// may call register() more than once). Modelled on MC's minimoi-tools announcer (review M3): announce at registration,
// retry with backoff until recorded, and stop on a 4xx (a retired instance must not keep retrying or reclaim itself).
const ANNOUNCERS = new Map();

export function announcer({url, token, instance = INSTANCE, fetchImpl = fetch, schedule = setTimeout}) {
  const target = `${url.href}/instance`;
  const key = `${target}\0${token.length}:${instance}`;
  if (ANNOUNCERS.has(key)) return ANNOUNCERS.get(key);
  let sent = null, terminal = false, started = false;
  function announce() {
    if (!sent) {
      sent = (async () => {
        try {
          const r = await fetchImpl(target, {method: 'POST', redirect: 'error', signal: AbortSignal.timeout(10000),
            headers: {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
            body: JSON.stringify({instance})});
          terminal = r.status >= 400 && r.status < 500 && ![408, 429].includes(r.status);
          await r.body?.cancel().catch(() => {});
          return r.status === 200;
        } catch { return false; }
      })();
      sent.then(ok => { if (!ok && !terminal) sent = null; });
    }
    return sent;
  }
  announce.start = () => {
    if (started) return;
    started = true;
    let delay = 1000;
    const attempt = async () => {
      if (await announce() || terminal) return;
      const timer = schedule(() => { attempt().catch(() => {}); }, delay);
      timer?.unref?.();
      delay = Math.min(delay * 2, 60000);
    };
    attempt().catch(() => {});
  };
  ANNOUNCERS.set(key, announce);
  return announce;
}

/** The tool factory: cos_actions for an allowed agent whose credential exposure the operator accepted. */
export function toolFactory({endpoint, token, tokenFile, agents, acceptCredentialReadableBy = [], sealer = PROCESS_SEALER,
                             instance = INSTANCE, fetchImpl = fetch}) {
  const url = endpointUrl(endpoint);
  if (!/^[A-Za-z0-9_-]{32,256}$/.test(token) || !Array.isArray(agents) || !agents.length) {
    throw new Error('CoS tool configuration invalid');
  }
  const announce = announcer({url, token, instance, fetchImpl});
  return function create(ctx) {
    const sessionKey = ctx?.sessionKey, agentId = ctx?.agentId;
    if (typeof sessionKey !== 'string' || !sessionKey || sessionKey.length > 200 || !agents.includes(agentId)) return null;
    if (!acceptCredentialReadableBy.includes(agentId)) {
      const ws = typeof ctx.workspaceDir === 'string' ? ctx.workspaceDir.replace(/\/+$/, '') + '/' : null;
      if (ctx.fsPolicy?.workspaceOnly !== true || !ws || tokenFile.startsWith(ws)) return null;
    }
    const run = (spec) => async (toolCallId, sealedArgs, signal) => {
      const opened = typeof toolCallId === 'string' && CALL_ID.test(toolCallId)
        ? sealer.open(sealedArgs, {toolCallId, agentId, sessionKey, toolName: spec.name}) : null;
      if (!opened) return text('CoS could not confirm which turn this call belongs to, so nothing was sent.',
                               {status: 'refused', code: 'run_identity_missing'});
      if (signal?.aborted) return text('CoS did not send this call, so nothing was done.', {status: 'refused', code: 'not_sent'});
      if (!(await announce())) return text('CoS has not recorded this runtime yet, so nothing was sent.',
                                           {status: 'refused', code: 'instance_unrecorded'});
      let response;
      try {
        const abort = signal ? AbortSignal.any([AbortSignal.timeout(15000), signal]) : AbortSignal.timeout(15000);
        response = await fetchImpl(`${url.href}/tools/${spec.name}`, {method: 'POST', redirect: 'error', signal: abort,
          headers: headers(token, {sessionKey, agentId, run: opened.run, toolCallId: backendCallId(toolCallId), instance}),
          body: JSON.stringify({arguments: opened.args})});
      } catch { return text(UNKNOWN, {status: 'unknown', code: 'transport'}); }
      const result = await boundedJson(response);
      if (response.status !== 200 || !result || Object.keys(result).sort().join() !== 'code,data,message,status'
          || !['done', 'refused', 'unknown'].includes(result.status) || JSON.stringify(result).includes(token)) {
        return text(UNKNOWN, {status: 'unknown', code: 'http_' + response.status});
      }
      return text(result.message + '\n' + JSON.stringify({status: result.status, code: result.code, data: result.data}),
                  {status: result.status, code: result.code});
    };
    const offered = [ACTIONS_SPEC, TASKS_SPEC, BOARD_SPEC]
      .filter(spec => !Object.hasOwn(RESTRICTED, agentId) || RESTRICTED[agentId].has(spec.name));
    return offered.map(spec => ({...spec, execute: run(spec)}));
  };
}

/** before_tool_call: seal cos_actions calls (or block them without a complete host identity); report native calls to
 *  the log and let them run. Any other tool passes untouched. */
export function hook({sealer = PROCESS_SEALER, agents, url, token, instance = INSTANCE, fetchImpl = fetch}) {
  const allowed = new Set(agents);
  return (event, ctx) => {
    const name = event?.toolName;
    const agentId = ctx?.agentId, sessionKey = ctx?.sessionKey;
    const runId = ctx?.runId ?? event?.runId, toolCallId = ctx?.toolCallId ?? event?.toolCallId;
    if (Object.hasOwn(RESTRICTED, agentId) && !RESTRICTED[agentId].has(name)) {
      return {block: true, blockReason: 'This tool is not available in this meeting'};
    }
    if (BOUND.has(name)) {
      if (!allowed.has(agentId)) return {block: true, blockReason: 'CoS tools are not available to this agent'};
      const params = sealer.seal(event.params, {runId, toolCallId, agentId, sessionKey, toolName: name});
      if (!params) return {block: true, blockReason: 'CoS could not confirm which turn this call belongs to'};
      return {params};
    }
    if (NATIVE.has(name) && allowed.has(agentId) && url) {
      const p = event?.params || {};
      const target = typeof p.command === 'string' ? p.command : typeof p.path === 'string' ? p.path
        : typeof p.file_path === 'string' ? p.file_path : null;
      const run = typeof runId === 'string' && RUN_ID.test(runId) ? runId : '';
      const call = typeof toolCallId === 'string' && CALL_ID.test(toolCallId) ? backendCallId(toolCallId) : '';
      fetchImpl(`${url.href}/native`, {method: 'POST', redirect: 'error', signal: AbortSignal.timeout(5000),
        headers: headers(token, {sessionKey: sessionKey || '', agentId: agentId || '', run, toolCallId: call, instance}),
        body: JSON.stringify({tool: name, target: target === null ? null : String(target).slice(0, 500)})})
        .then(r => r.body?.cancel().catch(() => {}), () => {});
    }
    return undefined;                                  // never blocks a native call (Q1)
  };
}

const SERVED_FIELD = /^[A-Za-z0-9._:\/-]{1,80}$/;
/** llm_output: tell the listener who really served this exact run, from the reply's own record. Evidence only: fire and
 *  forget, it changes nothing about the turn, and an unknown value is left out rather than guessed. */
export function servedHook({agents, url, token, instance = INSTANCE, fetchImpl = fetch}) {
  const allowed = new Set(agents);
  return (event, ctx) => {
    const agentId = ctx?.agentId, sessionKey = ctx?.sessionKey, runId = ctx?.runId ?? event?.runId;
    if (!allowed.has(agentId) || !url || typeof runId !== 'string' || !RUN_ID.test(runId)) return;
    const reply = event?.lastAssistant || {};
    const fields = {provider: reply.provider, model: reply.model, api: reply.api, harness: event?.harnessId};
    const body = Object.fromEntries(Object.entries(fields).filter(([, v]) => typeof v === 'string' && SERVED_FIELD.test(v)));
    if (!body.provider || !body.model) return;
    fetchImpl(`${url.href}/served`, {method: 'POST', redirect: 'error', signal: AbortSignal.timeout(5000),
      headers: headers(token, {sessionKey: sessionKey || '', agentId: agentId || '', run: runId, toolCallId: '', instance}),
      body: JSON.stringify(body)}).then(r => r.body?.cancel().catch(() => {}), () => {});
  };
}

export default {id: 'cos-bound', name: 'CoS bound tools', register(api) {
  const config = api.pluginConfig || {};
  if (config.enabled !== true) return;
  let factory, url, token, agents;
  try {
    agents = config.agents || ['cos-agent-a', 'cos-meeting'];
    token = tokenFromFile(config.tokenFile);
    url = endpointUrl(config.endpoint);
    factory = toolFactory({endpoint: config.endpoint, token, tokenFile: config.tokenFile, agents,
                           acceptCredentialReadableBy: config.acceptCredentialReadableBy || []});
  } catch {
    (api.logger?.warn ?? console.warn).call(api.logger ?? console, 'cos-bound: not registered (credential or configuration unavailable)');
    return;
  }
  api.on('before_tool_call', hook({agents, url, token}));
  api.on('llm_output', servedHook({agents, url, token}));
  announcer({url, token}).start();          // before any turn's first call (review M3)
  api.registerTool(factory, {names: ['cos_actions', 'cos_tasks', 'cos_board']});
}};
