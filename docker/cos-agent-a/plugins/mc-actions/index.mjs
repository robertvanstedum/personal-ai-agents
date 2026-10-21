/** Pinned OpenClaw contextVersion2 adapter; default disabled, not installed.
 * The trusted initiator must register its run BEFORE supplying toolBindings.
 * No binder credential, admission endpoint, shell, file, URL or SQL tool here.
 * #360 confinement and the full dispatch/budget gates still precede activation.
 */
import {openSync, closeSync, fstatSync, readSync, lstatSync, constants} from 'node:fs';

const MAX_BYTES = 65536;
const operationName = /^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$/;
const identifier = value => typeof value === 'string' && /^[!-~]{1,256}$/.test(value);
const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
class NotSent extends Error { constructor() { super('MC action was not sent. Its runtime invocation is no longer current.'); } }
class Refused extends Error { constructor() { super('MC action was refused before execution by the action service.'); } }
const failure = () => new Error('MC action outcome is unconfirmed. Reconcile its operation record before retrying.');

function tokenFromFile(path) {
  let fd;
  try {
    if (typeof path !== 'string' || !path.startsWith('/')) throw Error();
    // Bootstrap configuration is owner-controlled and outside the model's writable workspace.
    // Refuse symlink ancestors as well as a symlink final file. Deployment must
    // keep these directories protected; this is not a sandbox against their owner.
    const parts = path.split('/');
    if (parts.some((part, i) => i > 0 && (!part || part === '.' || part === '..'))) throw Error();
    for (let i = 2; i < parts.length; i++) {
      const parent = lstatSync(parts.slice(0, i).join('/'));
      if (!parent.isDirectory() || parent.isSymbolicLink()) throw Error();
    }
    fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const stat = fstatSync(fd);
    if (!stat.isFile() || stat.uid !== process.getuid() || stat.nlink !== 1 || (stat.mode & 0o077) !== 0 || stat.size < 32 || stat.size > 257) throw Error();
    const bytes = Buffer.alloc(258);
    const length = readSync(fd, bytes, 0, bytes.length, 0);
    const token = bytes.subarray(0, length).toString('utf8').replace(/\n$/, '');
    if (!/^[A-Za-z0-9_-]{32,256}$/.test(token)) throw Error();
    return token;
  } catch { throw new Error('MC action credential unavailable'); }
  finally { if (fd !== undefined) closeSync(fd); }
}

function jsonTree(value, depth = 0) {
  if (depth > 32) throw Error();
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
  if (typeof value === 'number' && Number.isFinite(value)) return;
  if (typeof value !== 'object') throw Error();
  if (!Array.isArray(value) && Object.getPrototypeOf(value) !== Object.prototype) throw Error();
  for (const item of Object.values(value)) jsonTree(item, depth + 1);
}

function requestBody(args, operations) {
  if (!args || Array.isArray(args) || Object.keys(args).sort().join(',') !== 'arguments,operation,operation_version'
      || !operations.includes(args.operation) || !Number.isSafeInteger(args.operation_version)
      || args.operation_version < 1 || !args.arguments || Array.isArray(args.arguments)
      || typeof args.arguments !== 'object') throw new Error('MC action arguments refused');
  try {
    jsonTree(args);
    const encoded = JSON.stringify(args);
    if (Buffer.byteLength(encoded, 'utf8') > MAX_BYTES) throw Error();
    return encoded;
  } catch { throw new Error('MC action arguments refused'); }
}

async function boundedResponse(response) {
  if (!/^application\/json(?:;|$)/i.test(response.headers.get('content-type') || '')
      || !response.body) {
    await response.body?.cancel().catch(()=>{});
    throw failure();
  }
  const reader = response.body.getReader();
  const chunks = []; let size = 0;
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BYTES) throw failure();
      chunks.push(value);
    }
    return new TextDecoder('utf-8', {fatal:true}).decode(Buffer.concat(chunks));
  } catch { await reader.cancel().catch(()=>{}); throw failure(); }
  finally { reader.releaseLock(); }
}

export function toolFactory({endpoint, token, operations, fetchImpl = fetch}) {
  // Values are trusted operator configuration, never tool parameters. The URL
  // is a single private service endpoint, not an arbitrary fetch capability.
  const url = new URL(endpoint);
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash
      || url.pathname !== '/internal/mc/v1/actions' || !/^[A-Za-z0-9_-]{32,256}$/.test(token)
      || !Array.isArray(operations) || !operations.length || operations.length > 100
      || !operations.every(x => typeof x === 'string' && x.length <= 128 && operationName.test(x))
      || new Set(operations).size !== operations.length) throw new Error('MC action configuration invalid');
  const allowed = Object.freeze([...operations]);
  return {contextVersion:2, create(ctx) {
    const b = ctx.toolBindings?.minimoi;
    if (!b || Object.keys(b).sort().join(',') !== 'context_reference,dispatch_id,generation,run_id,session_key'
        || typeof b.context_reference !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(b.context_reference)
        || ![b.session_key,b.run_id,b.dispatch_id].every(identifier)
        || b.session_key !== ctx.sessionKey || !Number.isSafeInteger(b.generation) || b.generation < 1
        || typeof ctx.assertInvocationCurrent !== 'function') return null;
    const binding = Object.freeze({...b});
    return {name:'minimoi_action', label:'MiniMoi action',
      description:'Use a registered MiniMoi operation. Owner authority and verified outcomes are checked by MiniMoi. An unknown outcome must be reconciled, never blindly repeated.',
      parameters:{type:'object', additionalProperties:false, required:['operation','operation_version','arguments'],
        properties:{operation:{type:'string',enum:[...allowed]}, operation_version:{type:'integer',minimum:1}, arguments:{type:'object'}}},
      execute:async(_toolCallId,args,signal) => {
        const body = requestBody(args, allowed);
        try { ctx.assertInvocationCurrent(); signal?.throwIfAborted(); }
        catch { throw new NotSent(); }
        try {
          const timeout = AbortSignal.timeout(29000);
          const abort = signal ? AbortSignal.any([timeout, signal]) : timeout;
          abort.throwIfAborted();
          const response = await fetchImpl(url.href, {method:'POST', redirect:'error', signal:abort,
            headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json',
              'X-MC-Context':binding.context_reference, 'X-MC-Session':binding.session_key,
              'X-MC-Run':binding.run_id, 'X-MC-Dispatch':binding.dispatch_id,
              'X-MC-Generation':String(binding.generation)}, body});
          const text = await boundedResponse(response);
          if (text.includes(token) || text.includes(binding.context_reference)) throw failure();
          const result = JSON.parse(text);
          // Only exact listener errors known to occur before application dispatch
          // establish a refusal. Proxy errors, 5xx, and malformed replies stay unknown.
          const preExecutionErrors = {401:'runtime_unauthorized',403:'capability_unavailable',
            413:'runtime_request_too_large',415:'json_required'};
          if (!response.ok) {
            if (result && Object.keys(result).join(',') === 'error'
                && result.error === preExecutionErrors[response.status]) {
              ctx.assertInvocationCurrent();
              throw new Refused();
            }
            throw failure();
          }
          if (!result || Object.keys(result).sort().join(',') !== 'code,confirmation,links,operation_id,receipt_id,status,verified_state,version,work_id'
              || result.version !== 1 || !uuid(result.operation_id) || !uuid(result.receipt_id)
              || !['done','accepted','needs_confirmation','needs_clarification','refused','conflict','failed','unknown'].includes(result.status)) throw failure();
          // A late response still has its durable receipt on the service. Do
          // not inject it into a superseding runtime turn through this callback.
          ctx.assertInvocationCurrent();
          return {content:[{type:'text',text}],details:result};
        } catch (error) { if (error instanceof Refused) throw error; throw failure(); }
      }};
  }};
}

export default {id:'mc-actions', name:'MiniMoi authorized actions', register(api) {
  const config = api.pluginConfig || {};
  if (config.enabled !== true) return;
  const factory = toolFactory({endpoint:config.endpoint, token:tokenFromFile(config.executeTokenFile),
                               operations:config.operations});
  api.registerTool(factory, {names:['minimoi_action']});
}};
