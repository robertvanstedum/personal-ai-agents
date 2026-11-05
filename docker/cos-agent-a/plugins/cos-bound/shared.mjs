/** Shared with MC's minimoi-tools plugin, copied verbatim (tests/cos/test_cos_bound_plugin.py fails if they drift).
 * The run sealer, the credential reader and the bounded JSON reader. */
import {openSync, closeSync, fstatSync, readSync, lstatSync, constants} from 'node:fs';
import {createHmac, randomBytes, timingSafeEqual} from 'node:crypto';

const MAX_BYTES = 65536;

export const SEAL = '__minimoi_run';
export const RUN_ID = /^chatcmpl_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export const CALL_ID = /^[A-Za-z0-9_.:-]{1,128}$/;

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
