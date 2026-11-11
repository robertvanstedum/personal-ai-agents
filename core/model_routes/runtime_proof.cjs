/** Metadata-only terminal attestation. Never reads message content.
 * The host must grant agent_end conversation access to register this handler.
 * Tombstones live for the process lifetime: a terminal run can never use tools.
 */
const RUN = /^chatcmpl_[0-9a-f-]{36}$/;
const NATIVE_RUN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const KEY = Symbol.for('minimoi/depth-runtime-proof/v1');
globalThis[KEY] ??= new Map();
const crypto = require('node:crypto');
const SIGNERS = Symbol.for('minimoi/depth-runtime-signers/v1');
globalThis[SIGNERS] ??= new Map();
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

function tracker({instance, publish, states = globalThis[KEY], limit = 4096}) {
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
      row = {...id, instance, models: new Set(), tools: new Set(), terminal: false, valid: true};
      states.set(key, row);
    }
    if (row && (row.session !== id.session || row.agent !== id.agent)) row.valid = false;
    return row;
  }
  return {
    modelStarted(event, ctx) {
      const row = find(event, ctx, true);
      if (row) {
        if (row.terminal) row.valid = false;
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
      const call = ctx?.toolCallId ?? event?.toolCallId;
      if (typeof call !== 'string') row.valid = false;
      else row.tools.add(call);
      return undefined;
    },
    async ended(event, ctx) {
      const row = find(event, ctx);
      if (!row || row.terminal) return;
      row.terminal = true; // fence native AND bound tools before publishing
      if (!row.valid || row.models.size < 1) return;
      const receipt = {instance, run: row.run, session: row.session, agent: row.agent,
        success: event?.success === true, model_calls: row.models.size, tool_calls: row.tools.size};
      await publish({...receipt, signature: identity(instance).sign(receipt)});
    },
  };
}

module.exports = {tracker, identity};
