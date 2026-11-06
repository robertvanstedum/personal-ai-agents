/** Shared with MC's minimoi-tools plugin, copied verbatim (tests/cos/test_cos_bound_plugin.py fails if they drift).
 * The run sealer, the credential reader and the bounded JSON reader. */
import {openSync, closeSync, fstatSync, readSync, lstatSync, constants} from 'node:fs';
import {createHash, createHmac, randomBytes, timingSafeEqual} from 'node:crypto';

const MAX_BYTES = 65536;

export const SEAL = '__minimoi_run';
export const RUN_ID = /^chatcmpl_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
// The host's id for one call. Code mode (the ChatGPT route) nests a call as tool_search_code:<parent>:<tool>:<n>, which can
// pass 128. Same characters; the whole id binds the seal and the replay record. The listener keeps its own limit.
export const CALL_ID = /^[A-Za-z0-9_.:-]{1,256}$/;
const BACKEND_CALL_ID = /^[A-Za-z0-9_.:-]{1,128}$/;
/** The id the listener records: the host's own when it fits, else its digest. Same id, same value. */
export function backendCallId(toolCallId) {
  return BACKEND_CALL_ID.test(toolCallId) ? toolCallId : 'sha256:' + createHash('sha256').update(toolCallId).digest('hex');
}

export function runSealer(key = randomBytes(32)) {
  if (!Buffer.isBuffer(key) || key.length < 32) throw new Error('MiniMoi run seal key invalid');
  const mac = (run, call, agent, session) =>
    createHmac('sha256', key).update(JSON.stringify(['minimoi-run/1', run, call, agent, session])).digest('base64url');
  return Object.freeze({
    /** Hook side: params with this call's seal (any seal the model supplied is replaced), or null when the
     *  host did not supply a complete identity. */
    seal(params, {runId, toolCallId, agentId, sessionKey}) {
      if (typeof runId !== 'string' || !RUN_ID.test(runId) || typeof toolCallId !== 'string' || !CALL_ID.test(toolCallId)
          || typeof agentId !== 'string' || typeof sessionKey !== 'string' || !sessionKey) return null;
      const base = params && typeof params === 'object' && !Array.isArray(params) ? params : {};
      const {[SEAL]: _dropped, ...rest} = base;
      return {...rest, [SEAL]: {run: runId, call: toolCallId, mac: mac(runId, toolCallId, agentId, sessionKey)}};
    },
    /** Execute side: {run, args} with the seal removed, or null unless the seal was made by this process for
     *  exactly this call, agent and session. */
    open(args, {toolCallId, agentId, sessionKey}) {
      if (!args || typeof args !== 'object' || Array.isArray(args) || !Object.hasOwn(args, SEAL)) return null;
      const {[SEAL]: s, ...rest} = args;
      if (!s || typeof s !== 'object' || Array.isArray(s) || Object.keys(s).sort().join() !== 'call,mac,run') return null;
      if (typeof s.run !== 'string' || !RUN_ID.test(s.run) || s.call !== toolCallId || typeof s.mac !== 'string') return null;
      const want = Buffer.from(mac(s.run, s.call, agentId, sessionKey));
      const got = Buffer.from(s.mac);
      if (want.length !== got.length || !timingSafeEqual(want, got)) return null;
      return {run: s.run, args: rest};
    },
  });
}

export function tokenFromFile(path) {
  let fd;
  try {
    if (typeof path !== 'string' || !path.startsWith('/')) throw Error();
    // Owner-controlled configuration outside the model's workspace. Refuse symlinked ancestors and file.
    const parts = path.split('/');
    if (parts.some((part, i) => i > 0 && (!part || part === '.' || part === '..'))) throw Error();
    for (let i = 2; i < parts.length; i++) {
      const parent = lstatSync(parts.slice(0, i).join('/'));
      if (!parent.isDirectory() || parent.isSymbolicLink()) throw Error();
    }
    fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const stat = fstatSync(fd);
    if (!stat.isFile() || stat.uid !== process.getuid() || stat.nlink !== 1 || (stat.mode & 0o077) !== 0
        || stat.size < 32 || stat.size > 257) throw Error();
    const bytes = Buffer.alloc(258);
    const length = readSync(fd, bytes, 0, bytes.length, 0);
    const token = bytes.subarray(0, length).toString('utf8').replace(/\n$/, '');
    if (!/^[A-Za-z0-9_-]{32,256}$/.test(token)) throw Error();
    return token;
  } catch { throw new Error('MiniMoi tool credential unavailable'); }
  finally { if (fd !== undefined) closeSync(fd); }
}

export async function boundedJson(response) {
  if (!/^application\/json(?:;|$)/i.test(response.headers.get('content-type') || '') || !response.body) {
    await response.body?.cancel().catch(() => {});
    return null;
  }
  const reader = response.body.getReader();
  const chunks = []; let size = 0;
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BYTES) return null;
      chunks.push(value);
    }
    return JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(Buffer.concat(chunks)));
  } catch { return null; }
  finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

// The seal stays beside the arguments, never in them: a strict provider schema refuses an extra field. Mirrors MC's
// minimoi-tools runBoundSealer (ed57791b), for tools named cos_*.
export function runBoundSealer({key = randomBytes(32), entries = new Map(), now = Date.now,
                                ttlMs = 300000, maxEntries = 4096, onRefuse = () => {}} = {}) {
  const crypto = runSealer(key);
  // A refusal says which branch refused and the shape of the identity. Never arguments, a body, or an id's value.
  const refuse = (reason, ctx) => {
    try {
      onRefuse({reason, tool: ctx?.toolName, agent: ctx?.agentId, nested: String(ctx?.toolCallId).startsWith('tool_search_code:'),
                callLength: typeof ctx?.toolCallId === 'string' ? ctx.toolCallId.length : null,
                runShapeOk: typeof ctx?.runId === 'string' && RUN_ID.test(ctx.runId), sessionKnown: Boolean(ctx?.sessionKey),
                entries: entries.size, maxEntries});
    } catch { /* a report never changes the refusal */ }
    return null;
  };
  const identityProblem = ctx => {
    if (typeof ctx.runId !== 'string' || !RUN_ID.test(ctx.runId)) return 'run_id_invalid';
    if (typeof ctx.toolCallId !== 'string') return 'call_id_missing';
    if (!CALL_ID.test(ctx.toolCallId)) return 'call_id_invalid';
    if (typeof ctx.agentId !== 'string') return 'agent_missing';
    if (typeof ctx.sessionKey !== 'string' || !ctx.sessionKey) return 'session_missing';
    return null;
  };
  const identity = ctx => JSON.stringify([ctx.agentId, ctx.sessionKey, ctx.toolName, ctx.toolCallId]);
  function canonical(value) {
    if (value === null || typeof value === 'boolean' || typeof value === 'string') return value;
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (Array.isArray(value)) return value.map(canonical);
    if (value && Object.getPrototypeOf(value) === Object.prototype)
      return Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])]));
    throw Error('Not JSON');
  }
  function argumentsDigest(args) {
    if (!args || Object.getPrototypeOf(args) !== Object.prototype) throw Error('Not arguments');
    const serialized = JSON.stringify(canonical(args));
    if (Buffer.byteLength(serialized) > MAX_BYTES) throw Error('Arguments too large');
    return createHash('sha256').update(serialized).digest('hex');
  }
  const retire = row => { row.state = 'retired'; delete row.sealed; };
  return Object.freeze({
    seal(params, ctx) {
      if (!ctx || typeof ctx.toolName !== 'string' || !/^cos_[a-z][a-z0-9_]*$/.test(ctx.toolName)) return refuse('tool_name_invalid', ctx);
      try {
        if (!params || Object.getPrototypeOf(params) !== Object.prototype) return refuse('arguments_not_plain', ctx);
        const {[SEAL]: ignored, ...args} = params;
        const problem = identityProblem(ctx);
        if (problem) return refuse(problem, ctx);
        let digest;
        try { digest = argumentsDigest(args); } catch { return refuse('arguments_not_canonical', ctx); }
        const sealed = crypto.seal(structuredClone(args), ctx);
        if (!sealed) return refuse('seal_failed', ctx);
        const id = identity(ctx), existing = entries.get(id), at = now();
        if (existing) {
          if (existing.state === 'pending' && existing.expires > at && existing.run === ctx.runId && existing.digest === digest)
            return structuredClone(args);
          const reason = existing.state !== 'pending' ? 'call_already_used' : existing.expires <= at ? 'call_expired'
            : existing.run !== ctx.runId ? 'call_other_run' : 'call_arguments_changed';
          retire(existing); return refuse(reason, ctx);
        }
        // Release expired payloads but retain their identities as replay tombstones.
        for (const row of entries.values()) if (row.state === 'pending' && row.expires <= at) retire(row);
        if (entries.size >= maxEntries) return refuse('registry_full', ctx);
        entries.set(id, {state: 'pending', run: ctx.runId, digest, sealed, expires: at + ttlMs});
        return structuredClone(args);
      } catch { return refuse('seal_exception', ctx); }
    },
    open(args, ctx) {
      if (!ctx) return null;
      const row = entries.get(identity(ctx));
      if (!row || row.state !== 'pending') return null;
      const sealed = row.sealed;
      retire(row); // One-shot, synchronously before execute can perform any async operation.
      try {
        if (row.expires <= now() || Object.hasOwn(args, SEAL) || argumentsDigest(args) !== row.digest) return null;
        return crypto.open(sealed, ctx);
      } catch { return null; }
    },
  });
}
