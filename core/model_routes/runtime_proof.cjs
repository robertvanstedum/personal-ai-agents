/** Metadata-only terminal attestation. Never reads message content.
 * The host must grant agent_end conversation access to register this handler.
 * Tombstones live for the process lifetime: a terminal run can never use tools.
 *
 * agent_end fires once per ATTEMPT, not once per run (#606). After a rate limit or
 * other transient error OpenClaw ends the attempt with success=false, waits, and
 * retries the SAME runId. So an error end is provisional: tools are fenced, nothing
 * is published, and the next model call of that run reopens it. The receipt is
 * published only when the run is over: a successful end, or an error end that no
 * retry follows within settleMs. A receipt is always preceded by the permanent
 * tombstone, so no tool can ever run under a run whose receipt was published.
 */
const RUN = /^chatcmpl_[0-9a-f-]{36}$/;
const NATIVE_RUN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const KEY = Symbol.for('minimoi/depth-runtime-proof/v1');
globalThis[KEY] ??= new Map();
const crypto = require('node:crypto');
const SIGNERS = Symbol.for('minimoi/depth-runtime-signers/v1');
globalThis[SIGNERS] ??= new Map();
// Longer than any gap OpenClaw leaves between attempts (exponential backoff caps at
// 30 s; a provider Retry-After of 60 s was seen on Dev). Too short only fails closed.
const SETTLE_MS = 10 * 60 * 1000;
const TIMERS = {set: (fn, ms) => { const t = setTimeout(fn, ms); t.unref?.(); return t; }, clear: t => clearTimeout(t)};
function identity(instance) {
  if (!globalThis[SIGNERS].has(instance)) {
    const keys = crypto.generateKeyPairSync('ed25519');
    const publicKey = keys.publicKey.export({type: 'spki', format: 'der'}).toString('base64');
    globalThis[SIGNERS].set(instance, Object.freeze({publicKey, sign: receipt => {
      const fields = ['instance', 'run', 'session', 'agent', 'success', 'model_calls', 'tool_calls'];
      return crypto.sign(null, Buffer.from(JSON.stringify(fields.map(k => receipt[k]))), keys.privateKey).toString('base64');
    }}));
  }
  return globalThis[SIGNERS].get(instance);
}

function tracker({instance, publish, states = globalThis[KEY], limit = 4096, settleMs = SETTLE_MS, timers = TIMERS}) {
  function runIdentity(event, ctx) {
    const run = ctx?.runId ?? event?.runId;
    const session = ctx?.sessionKey ?? event?.sessionKey;
    const agent = ctx?.agentId ?? session?.match(/^agent:([^:]+):/)?.[1];
    return typeof run === 'string' && RUN.test(run) && typeof session === 'string'
      && session.length <= 512 && typeof agent === 'string' ? {run, session, agent} : null;
  }
  function find(event, ctx, create = false) {
    const id = runIdentity(event, ctx);
    if (!id) return null;
    const key = instance + ':' + id.run;
    let row = states.get(key);
    if (!row && create && states.size < limit) {
      row = {...id, instance, models: new Set(), tools: new Set(), terminal: false, valid: true, between: false, settle: null};
      states.set(key, row);
    }
    if (row && (row.session !== id.session || row.agent !== id.agent)) row.valid = false;
    return row;
  }
  function wait(row) {
    if (row.settle !== null) timers.clear(row.settle);
    row.settle = null;
  }
  async function finish(row, success) {
    if (row.terminal) return;
    wait(row);
    row.terminal = true; // fence native AND bound tools before publishing; never cleared
    row.between = false;
    if (!row.valid || row.models.size < 1) return;
    const receipt = {instance, run: row.run, session: row.session, agent: row.agent,
      success, model_calls: row.models.size, tool_calls: row.tools.size};
    await publish({...receipt, signature: identity(instance).sign(receipt)});
  }
  return {
    modelStarted(event, ctx) {
      const row = find(event, ctx, true);
      if (row) {
        if (row.terminal) row.valid = false;
        else if (row.between) { wait(row); row.between = false; } // same-run retry: a new attempt, no receipt exists yet
        if (typeof event?.callId !== 'string' || event.callId.length > 128) row.valid = false;
        else row.models.add(event.callId);
      }
    },
    beforeTool(event, ctx) {
      if (ctx?.runId && event?.runId && ctx.runId !== event.runId)
        return {block: true, blockReason: 'Runtime turn identity conflicts'};
      const run = ctx?.runId ?? event?.runId;
      // Native CLI runs cannot produce continuation receipts. Preserve their
      // existing tool policy; a missing/malformed/conflicting identity still fails closed.
      if (typeof run === 'string' && NATIVE_RUN.test(run)) return undefined;
      const row = find(event, ctx);
      if (!row || !row.valid) return {block: true, blockReason: 'Runtime turn tracking is unavailable'};
      if (row.terminal) return {block: true, blockReason: 'This runtime turn has finished'};
      if (row.between) return {block: true, blockReason: 'This runtime turn is between attempts'};
      const call = ctx?.toolCallId ?? event?.toolCallId;
      if (typeof call !== 'string') row.valid = false;
      else row.tools.add(call);
      return undefined;
    },
    async ended(event, ctx) {
      const row = find(event, ctx);
      if (!row || row.terminal) return;
      if (event?.success === true) return finish(row, true);
      // Provisional: OpenClaw may retry this runId. Fence tools now; publish only if no attempt follows.
      row.between = true;
      wait(row);
      row.settle = timers.set(() => { if (row.between) finish(row, false).catch(() => {}); }, settleMs);
    },
  };
}

module.exports = {tracker, identity, SETTLE_MS};
