/** Zero-provider S0 probe against the shipped OpenClaw 2026.9.6 modules.
 *
 * Usage: node openclaw_pinned_hook_probe.mjs /path/to/openclaw/package
 * Run in a disposable state/config directory with provider credentials removed.
 * Uses real hook dispatch/wrapping, but a synthetic tool and host-supplied
 * contexts. It does NOT prove portal admission, MCP/plugin registration,
 * authenticated service transport, restart fencing or the full S0 gate.
 * No package installation, gateway start, model call or network operation.
 */
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const root = resolve(process.argv[2] ?? 'missing-runtime-path');
assert.equal(JSON.parse(readFileSync(resolve(root, 'package.json'))).version, '2026.9.6');
const pins = {
  'agent-tools.before-tool-call-D6M7yTR3.mjs': 'd2d7ac072f05c1a816be4e50d9ad32f3205e1d312ae0a2a2fc029218dd67a171',
  'hook-runner-global-C81Znoo2.mjs': '719ec3d1b37d512b51367dc08dbafaade7e72ed533910ab5e491b26287df2cb6',
};
for (const [name, digest] of Object.entries(pins)) {
  assert.equal(createHash('sha256').update(readFileSync(resolve(root, 'dist', name))).digest('hex'), digest);
}
const load = name => import(pathToFileURL(resolve(root, 'dist', name)));
const {u: wrap} = await load('agent-tools.before-tool-call-D6M7yTR3.mjs');
const {i: initialize, a: reset} = await load('hook-runner-global-C81Znoo2.mjs');
const records = [];
let effects = 0;
let behavior = 'allow';
function initializeFixture() {
  reset();
  initialize({hooks: [], plugins: [], trustedToolPolicies: [], typedHooks: [{
    pluginId: 'mc-zero-provider-fixture', hookName: 'before_tool_call',
    handler: (event, ctx) => {
      records.push({event, ctx});
      if (behavior === 'error') throw new Error('fixture_hook_failed');
      if (behavior === 'block') return {block: true, blockReason: 'fixture_denied'};
      if (behavior === 'rewrite') return {params: {...event.params, injected: 'hook-value'}};
    },
  }]});
}
function fixture(runId, sessionKey = 'session-A') {
  const tool = {
    name: 'minimoi_fixture', description: 'Synthetic no-effect S0 test tool',
    parameters: {type: 'object', properties: {value: {type: 'string'}}, additionalProperties: false},
    execute: async (callId, params) => {
      effects++;
      return {content: [{type: 'text', text: 'fixture result'}], details: {callId, params, status: 'done'}};
    },
  };
  return wrap(tool, {
    agentId: 'fixture-agent', sessionKey, sessionId: sessionKey + '-uuid', runId,
    config: {tools: {loopDetection: {enabled: false}}},
  }, {emitDiagnostics: false});
}
initializeFixture();
const cases = [];
async function check(name, fn) { await fn(); cases.push(name); }
try {
  await check('trusted context is separate from model arguments', async () => {
    const result = await fixture('run-A').execute('call-A', {
      value: 'hello', runId: 'forged', sessionKey: 'forged', minimoi_ctx: {runId: 'forged'},
    });
    assert.equal(records.at(-1).ctx.runId, 'run-A');
    assert.equal(records.at(-1).ctx.sessionKey, 'session-A');
    assert.equal(records.at(-1).event.runId, 'run-A');
    assert.equal(result.details.params.runId, 'forged'); // data survives; not authority
  });
  await check('overlapping sessions retain their host contexts', async () => {
    await Promise.all([
      fixture('run-B', 'session-B').execute('call-B', {value: 'b'}),
      fixture('run-C', 'session-C').execute('call-C', {value: 'c'}),
    ]);
    const byCall = new Map(records.map(x => [x.ctx.toolCallId, x.ctx]));
    assert.equal(byCall.get('call-B').sessionKey, 'session-B');
    assert.equal(byCall.get('call-C').runId, 'run-C');
  });
  await check('old wrapper never acquires a new same-session run identity', async () => {
    const old = fixture('run-old');
    await fixture('run-new').execute('call-new', {value: 'new'});
    await old.execute('call-old', {value: 'old'});
    assert.equal(records.at(-1).ctx.runId, 'run-old');
    // No generation registry in this probe: host identity preservation does
    // not itself revoke this call. BindingStore must refuse the old run.
  });
  await check('hook veto prevents execution', async () => {
    behavior = 'block';
    const before = effects;
    await fixture('run-veto').execute('call-veto', {value: 'x'});
    assert.equal(effects, before);
  });
  await check('hook failure fails closed', async () => {
    behavior = 'error';
    const before = effects;
    try { await fixture('run-error').execute('call-error', {value: 'x'}); } catch {}
    assert.equal(effects, before);
  });
  await check('aborted invocation prevents execution', async () => {
    behavior = 'allow';
    const before = effects;
    const abort = new AbortController(); abort.abort();
    await assert.rejects(fixture('run-aborted').execute('call-aborted', {value: 'x'}, abort.signal));
    assert.equal(effects, before);
  });
  await check('hook-injected unknown parameters are not a schema security boundary', async () => {
    behavior = 'rewrite';
    const result = await fixture('run-schema').execute('call-schema', {value: 'x'});
    assert.equal(result.details.params.injected, 'hook-value');
    // additionalProperties:false did not remove the hook's extra property.
    // Action-service strict validation remains mandatory; never trust this
    // mechanism to hide bearer references from tool traces/results.
  });
  await check('structured tool results survive the actual wrapper', async () => {
    behavior = 'allow';
    const result = await fixture('run-result').execute('call-result', {value: 'x'});
    assert.equal(result.details.status, 'done');
    assert.equal(result.content[0].text, 'fixture result');
  });
  console.log(JSON.stringify({runtime: 'openclaw@2026.9.6', node: process.version,
    passed: cases.length, cases, provider_calls: 0, s0_gate_passed: false}));
} finally { reset(); }
