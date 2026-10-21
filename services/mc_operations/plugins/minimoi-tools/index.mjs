/** MiniMoi tools for Master Craftsman on native OpenClaw (docs/design/mc_tool_path_design_2026-10-21.md).
 * Default disabled. Each call carries only host context (ctx.sessionKey, ctx.agentId) and the
 * model's schema-checked arguments; MiniMoi binds it to Robert's live turn and refuses otherwise.
 * No identity, turn, grant or approval is ever taken from the model. The credential is read from a
 * private file; it never enters a tool result.
 */
import {openSync, closeSync, fstatSync, readSync, lstatSync, readFileSync, statfsSync, constants} from 'node:fs';
import {execFile} from 'node:child_process';
import {totalmem, freemem} from 'node:os';

const MAX_BYTES = 65536;
const STATUSES = ['done', 'needs_confirmation', 'needs_clarification', 'refused', 'unknown'];
const CATALOGUE = JSON.parse(readFileSync(new URL('./tools.json', import.meta.url), 'utf8'));
const GB = 1024 ** 3;
const text = (s, details) => ({content: [{type: 'text', text: s}], details});
const NOT_SENT = 'MiniMoi did not receive this call, so nothing was done.';
const UNKNOWN = 'MiniMoi could not confirm what happened. Do not repeat it; tell Robert.';

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

export function endpointUrl(endpoint) {
  const url = new URL(endpoint);
  const loopback = url.protocol === 'http:' && url.hostname === '127.0.0.1' && url.port !== '';
  if ((url.protocol !== 'https:' && !loopback) || url.username || url.password || url.search || url.hash
      || url.pathname !== '/internal/mc/v1/tools') throw new Error('MiniMoi tool endpoint invalid');
  return url;
}

const okText = s => typeof s === 'string' && s === s.trim() && !/[\p{Cc}\p{Cf}\p{Co}\p{Cs}]/u.test(s);

/** The same strict check MiniMoi repeats: unknown keys, wrong types and out-of-range values refuse. */
export function checkArguments(schema, args) {
  if (!args || typeof args !== 'object' || Array.isArray(args) || Object.getPrototypeOf(args) !== Object.prototype) return false;
  const props = schema.properties;
  const keys = Object.keys(args);
  if (keys.some(k => !Object.hasOwn(props, k)) || (schema.required || []).some(k => !Object.hasOwn(args, k))) return false;
  return keys.every(k => checkValue(props[k], args[k]));
}

function checkValue(spec, v) {
  if (spec.type === 'string') {
    if (!okText(v) || v.length < (spec.minLength ?? 0) || v.length > (spec.maxLength ?? 1000)) return false;
    if (spec.enum && !spec.enum.includes(v)) return false;
    return !spec.pattern || new RegExp(spec.pattern).test(v);
  }
  if (spec.type === 'integer') return Number.isSafeInteger(v) && v >= spec.minimum && v <= spec.maximum;
  if (spec.type === 'array') {
    if (!Array.isArray(v) || v.length < spec.minItems || v.length > spec.maxItems) return false;
    if (spec.uniqueItems && new Set(v.map(x => JSON.stringify(x))).size !== v.length) return false;
    return v.every(item => checkValue(spec.items, item));
  }
  return false;
}

function dockerContainers(docker) {
  return new Promise(resolve => {
    if (!docker) return resolve({error: 'not_configured'});
    // Fixed argv, no shell, minimal environment: the docker CLI finds its context under HOME.
    execFile(docker, ['ps', '-a', '--format', '{{.Names}}\t{{.Status}}'],
      {timeout: 5000, maxBuffer: 64 * 1024, env: {HOME: process.env.HOME || '/', PATH: '/usr/bin:/bin'}},
      (error, stdout) => {
        if (error) return resolve({error: 'docker_unavailable'});
        const rows = String(stdout).split('\n').filter(Boolean).slice(0, 60).map(line => {
          const [name, status = ''] = line.split('\t');
          return {name: name.replace(/[^A-Za-z0-9_.-]/g, '').slice(0, 128),
                  status: status.replace(/[^A-Za-z0-9 ()_.:,-]/g, '').trim().slice(0, 80) || 'unknown'};
        }).filter(c => /^[A-Za-z0-9]/.test(c.name));
        resolve(rows);
      });
  });
}

/** What host.health reports: measured here, on the runtime host, never supplied by the model. */
export async function observeHost({diskPaths = ['/'], docker} = {}) {
  const disks = diskPaths.slice(0, 4).map(mount => {
    const s = statfsSync(mount);
    return {mount, total_gb: +(s.blocks * s.bsize / GB).toFixed(2), free_gb: +(s.bavail * s.bsize / GB).toFixed(2)};
  });
  return {host: process.platform === 'darwin' ? 'mac' : 'linux', disks,
          memory: {total_gb: +(totalmem() / GB).toFixed(2), free_gb: +(freemem() / GB).toFixed(2)},
          containers: await dockerContainers(docker)};
}

async function boundedJson(response) {
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

export function toolFactory({endpoint, token, tokenFile, agents, tools, diskPaths, docker, fetchImpl = fetch,
                             allowTokenReadableByAgent = false}) {
  // Trusted operator configuration, never tool parameters.
  const url = endpointUrl(endpoint);
  if (!/^[A-Za-z0-9_-]{32,256}$/.test(token) || !Array.isArray(agents) || !agents.length
      || !agents.every(a => typeof a === 'string' && /^[a-z][a-z0-9-]{0,63}$/.test(a))
      || !Array.isArray(tools) || !tools.length || new Set(tools).size !== tools.length
      || !tools.every(n => CATALOGUE.tools.some(t => t.name === n))) throw new Error('MiniMoi tool configuration invalid');
  const allowedAgents = Object.freeze([...agents]);
  const enabled = Object.freeze(CATALOGUE.tools.filter(t => tools.includes(t.name)));
  const host = Object.freeze({diskPaths: Object.freeze([...(diskPaths || ['/'])]), docker});

  return function create(ctx) {
    // Host context only. Strings are immutable; nothing here can be changed later by the caller.
    const sessionKey = ctx?.sessionKey, agentId = ctx?.agentId;
    if (typeof sessionKey !== 'string' || !sessionKey || sessionKey.length > 200
        || typeof agentId !== 'string' || !allowedAgents.includes(agentId)) return null;
    // The agent's own file tools must not be able to read the credential (review P1-2): OpenClaw's default is
    // tools.fs.workspaceOnly=false. Offer no tools unless this run is workspace-only and the credential lies
    // outside the workspace, or the operator explicitly accepted the risk for dev.
    if (!allowTokenReadableByAgent) {
      const ws = typeof ctx.workspaceDir === 'string' ? ctx.workspaceDir.replace(/\/+$/, '') + '/' : null;
      if (ctx.fsPolicy?.workspaceOnly !== true || !ws || typeof tokenFile !== 'string' || tokenFile.startsWith(ws)) return null;
    }

    return enabled.map(spec => ({
      name: spec.name, label: spec.label, description: spec.description, parameters: spec.parameters,
      execute: async (_toolCallId, args, signal) => {
        if (!checkArguments(spec.parameters, args)) {
          return text('Those arguments do not fit this tool, so nothing was sent to MiniMoi.', {status: 'refused', code: 'arguments_invalid'});
        }
        let body;
        try {
          body = {arguments: {...args}};
          if (spec.name === 'minimoi_host_health') body.observation = await observeHost(host);
        } catch { return text('The host could not be measured, so nothing is reported.', {status: 'refused', code: 'observation_failed'}); }
        if (signal?.aborted) return text(NOT_SENT, {status: 'refused', code: 'not_sent'});
        let response;
        try {
          const abort = signal ? AbortSignal.any([AbortSignal.timeout(25000), signal]) : AbortSignal.timeout(25000);
          response = await fetchImpl(`${url.href}/${spec.name}`, {method: 'POST', redirect: 'error', signal: abort,
            headers: {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                      'X-MC-Session': sessionKey, 'X-MC-Agent': agentId},
            body: JSON.stringify(body)});
        } catch {
          // Once a send has started it may have reached MiniMoi: unknown, never a silent retry.
          return text(UNKNOWN, {status: 'unknown', code: 'transport'});
        }
        const result = await boundedJson(response);
        if (response.status !== 200) {
          const before = {401: 'tool_unauthorized', 413: 'tool_request_too_large', 415: 'json_required', 400: 'tool_request_invalid'};
          if (result && Object.keys(result).join() === 'error' && result.error === before[response.status]) {
            return text('MiniMoi refused this call before doing anything.', {status: 'refused', code: result.error});
          }
          return text(UNKNOWN, {status: 'unknown', code: 'http_' + response.status});
        }
        if (!result || Object.keys(result).sort().join() !== 'code,data,message,status' || !STATUSES.includes(result.status)
            || typeof result.message !== 'string' || typeof result.code !== 'string'
            || result.message.includes(token) || JSON.stringify(result.data).includes(token)) {
          return text(UNKNOWN, {status: 'unknown', code: 'malformed'});
        }
        return text(result.message, {status: result.status, code: result.code, data: result.data});
      },
    }));
  };
}

export default {id: 'minimoi-tools', name: 'MiniMoi tools', register(api) {
  const config = api.pluginConfig || {};
  if (config.enabled !== true) return;
  // A missing or unsafe credential, or bad configuration, must never take the gateway down with it (Robert's
  // Telegram and WhatsApp run there): log one line without details and register nothing (review P2-5).
  let factory, tools;
  try {
    tools = config.tools || CATALOGUE.tools.map(t => t.name);
    factory = toolFactory({endpoint: config.endpoint, token: tokenFromFile(config.tokenFile), tokenFile: config.tokenFile,
                           agents: config.agents || ['mc-chat'], tools, diskPaths: config.diskPaths,
                           docker: config.docker, allowTokenReadableByAgent: config.allowTokenReadableByAgent === true});
  } catch {
    (api.logger?.warn ?? console.warn).call(api.logger ?? console, 'minimoi-tools: not registered (credential or configuration unavailable)');
    return;
  }
  api.registerTool(factory, {names: tools});
}};
