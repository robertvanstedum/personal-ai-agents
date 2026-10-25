/** S0 supported plugin-run spike against OpenClaw 2026.9.6.
 * argv: installed package root, fresh scratch root, installation package-lock.json.
 * Run with HOME/OPENCLAW_STATE_DIR inside scratch, credentials scrubbed and an
 * OS sandbox denying external network and real home reads. Only loopback serves
 * synthetic SSE; no provider or product action is contacted. Use heavy_job.py.
 *
 * The real loader's registered handler invokes api.runtime.agent.runEmbeddedAgent;
 * the real admission/agent loop invokes a no-effect fixture tool. This is NOT a
 * gateway authentication, MiniMoi admission, HTTP-action, restart, or #360 proof.
 * Inspection registries do not initialize global hooks; no hook claim is made.
 */
import assert from 'node:assert/strict';
import {writeFileSync, mkdirSync, readFileSync, existsSync, realpathSync} from 'node:fs';
import {join, resolve, relative, isAbsolute} from 'node:path';
import {createServer} from 'node:http';
import {randomUUID, createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';

const root = realpathSync(resolve(process.argv[2]));
const base = realpathSync(resolve(process.argv[3]));
const lock = realpathSync(resolve(process.argv[4]));
function inside(path) {
  const rel = relative(base, resolve(path));
  return rel === '' || (!rel.startsWith('..') && !isAbsolute(rel));
}
assert.ok(inside(process.env.HOME || '/missing'), 'isolated HOME required');
assert.ok(inside(process.env.OPENCLAW_STATE_DIR || '/missing'), 'isolated state required');
assert.ok(inside(process.env.OPENCLAW_CONFIG_PATH || '/missing'), 'isolated config required');
assert.ok(!existsSync(join(base, 'plugin')), 'use a fresh fixture directory');
for (const key of Object.keys(process.env)) {
  assert.ok(!/(?:API_KEY|ACCESS_TOKEN|AUTH_TOKEN|SECRET_ACCESS_KEY)$/.test(key), 'scrub provider credentials');
}
const digest = path => createHash('sha256').update(readFileSync(path)).digest('hex');
assert.equal(JSON.parse(readFileSync(join(root, 'package.json'))).version, '2026.9.6');
const pins = {
  'tools-CvXdkvXK.mjs': 'a31975686eddc6a779865ff896b145b3b41bd7c659ac21761448da6861b0d926',
  'runtime-embedded-agent.runtime-CFvzD89w.mjs': '17ebb33c4a49a5222f3e6b7f250742a54f6be8d501d14249caffe41ffefe33da',
  'loader-runtime-load-CMBCNIWa.mjs': '78bbdb00acb979102867f1ee27151afcfddb4c8d75752ee6ff27c9d9ada6cb15',
};
for (const [name, hash] of Object.entries(pins)) assert.equal(digest(join(root, 'dist', name)), hash);
const load = name => import(pathToFileURL(join(root, 'dist', name)));
const observed = [];
const failures = [];
const server = createServer(async (req, res) => {
  try {
    assert.equal(req.method, 'POST');
    assert.equal(req.url, '/v1/chat/completions');
    let raw = '';
    for await (const chunk of req) {
      raw += chunk;
      assert.ok(Buffer.byteLength(raw) <= 256 * 1024, 'fixture request too large');
    }
    assert.ok(observed.length < 12, 'unexpected model-loop repetition');
    const body = JSON.parse(raw);
    observed.push(body);
    assert.deepEqual(body.tools.map(t => t.function.name), ['mc_ingress_fixture']);
    const latestUser = body.messages.findLastIndex(m => m.role === 'user');
    const done = body.messages.slice(latestUser + 1).some(m => m.role === 'tool');
    const delta = done ? {content: 'Fixture completed.'} : {tool_calls: [{index: 0,
      id: 'fixture-call', type: 'function', function: {name: 'mc_ingress_fixture', arguments: '{}'}}]};
    res.writeHead(200, {'content-type': 'text/event-stream'});
    const send = (delta, finish_reason, usage) => res.write('data: ' + JSON.stringify({
      id: 'fixture-completion', object: 'chat.completion.chunk', created: 1, model: 'fixture-model',
      choices: [{index: 0, delta, finish_reason}], ...(usage ? {usage} : {}),
    }) + '\n\n');
    send(delta, null);
    send({}, done ? 'stop' : 'tool_calls', {prompt_tokens: 1, completion_tokens: 1, total_tokens: 2});
    res.end('data: [DONE]\n\n');
  } catch (error) {
    failures.push(error.message);
    res.writeHead(500); res.end('fixture refused');
  }
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const trace = join(base, 'trace.jsonl');
const fixture = join(base, 'plugin'); mkdirSync(fixture);
writeFileSync(join(fixture, 'package.json'), JSON.stringify({name: 'mc-ingress-fixture',
  version: '0.0.1', type: 'module', openclaw: {extensions: ['./index.mjs']}}));
writeFileSync(join(fixture, 'openclaw.plugin.json'), JSON.stringify({id: 'mc-ingress-fixture',
  name: 'MC ingress fixture', contracts: {tools: ['mc_ingress_fixture']},
  configSchema: {type: 'object', properties: {}, additionalProperties: false}}));
// Fixture-only handler accepts internal runtime parameters to exercise the SDK.
// A product ingress must instead authenticate and validate a bounded request,
// fix config/model/paths server-side, and verify MiniMoi admission before inference.
writeFileSync(join(fixture, 'index.mjs'), `import {appendFileSync} from 'node:fs';
export default {id:'mc-ingress-fixture',register(api){
  api.registerTool({contextVersion:2,create(ctx){
    const binding=structuredClone(ctx.toolBindings?.minimoi);
    if(!binding)return null;
    return {name:'mc_ingress_fixture',description:'No-effect fixture',
      parameters:{type:'object',properties:{},additionalProperties:false},execute:async()=>{
        ctx.assertInvocationCurrent();
        appendFileSync(${JSON.stringify(trace)},JSON.stringify({binding,sessionKey:ctx.sessionKey})+'\\n');
        return {content:[{type:'text',text:'fixture verified'}]};
      }};
  }},{names:['mc_ingress_fixture']});
  api.registerGatewayMethod('mcfixture.run',async({params})=>api.runtime.agent.runEmbeddedAgent(params));
}};`);
const config = {
  logging: {level: 'silent'},
  models: {providers: {mcfixture: {baseUrl: 'http://127.0.0.1:' + server.address().port + '/v1',
    apiKey: 'synthetic-no-provider-key', api: 'openai-completions', models: [{
      id: 'fixture-model', name: 'Fixture', reasoning: false, input: ['text'],
      cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}, contextWindow: 32768, maxTokens: 1024,
    }]}}},
  agents: {list: [{id: 'fixture', workspace: base, agentDir: join(base, 'agent')}],
    defaults: {workspace: base, skipBootstrap: true, model: {primary: 'mcfixture/fixture-model'}}},
  tools: {toolSearch: false, profile: 'minimal', alsoAllow: ['mc_ingress_fixture'], deny: ['exec', 'process', 'gateway']},
  plugins: {enabled: true, allow: ['mc-ingress-fixture'], load: {paths: [fixture]},
    entries: {'mc-ingress-fixture': {enabled: true}}, slots: {memory: 'none'}},
};
let handle;
const cases = [];
try {
  const {n: acquire} = await load('tools-CvXdkvXK.mjs');
  const {C: withCoordinatorDirectory} = await load('sqlite-source-handle-C0wvRR5v.mjs');
  handle = await acquire({context: {config, workspaceDir: base, agentId: 'fixture', sessionKey: 'fixture'},
    toolAllowlist: ['mc_ingress_fixture']});
  assert.deepEqual(handle.registry.diagnostics.filter(d => d.level === 'error'), []);
  const handler = handle.registry.gatewayHandlers['mcfixture.run'];
  assert.equal(typeof handler, 'function');
  async function invoke(params) {
    let reply;
    await withCoordinatorDirectory(base, () => handler({params, respond: (ok, payload, error) => {reply = {ok, payload, error};}}));
    assert.equal(reply?.ok, true);
    return reply.payload;
  }
  function params(label, identity = {}) {
    const runId = 'fixture-' + label + '-' + randomUUID();
    return {config, runId, sessionId: randomUUID(), sessionKey: 'agent:fixture:' + label.toLowerCase() + '-' + randomUUID(),
      agentId: 'fixture', workspaceDir: base, agentDir: join(base, 'agent'),
      prompt: 'Call mc_ingress_fixture once, then acknowledge.', provider: 'mcfixture', model: 'fixture-model',
      timeoutMs: 20000, toolBindings: {minimoi: {runId, marker: 'synthetic-' + label}},
      toolsAllow: ['mc_ingress_fixture'], disableMessageTool: true, disableTrajectory: true, ...identity};
  }
  const abort = new AbortController(); abort.abort(new Error('fixture_preaborted'));
  await assert.rejects(invoke({...params('cancelled'), abortSignal: abort.signal}), /fixture_preaborted/);
  assert.equal(observed.length, 0);
  assert.equal(existsSync(trace), false);
  cases.push('pre-cancelled registered invocation makes no provider or tool call');

  await assert.rejects(invoke({...params('forged'), admittedRunContext: {}}), /cannot supply host run authority/);
  assert.equal(observed.length, 0);
  cases.push('registered plugin cannot supply host admission authority');

  const runs = [params('A'), params('B')];
  for (const input of runs) {
    const result = await invoke(input);
    assert.equal(result.meta.agentMeta.terminalReceipt.runId, input.runId);
    assert.deepEqual(result.meta.agentMeta.terminalReceipt.successfulToolNames, ['mc_ingress_fixture']);
    assert.equal(result.meta.toolSummary.failures, 0);
  }
  const traces = readFileSync(trace, 'utf8').trim().split('\n').map(JSON.parse);
  assert.deepEqual(traces, runs.map(p => ({binding: p.toolBindings.minimoi, sessionKey: p.sessionKey})));
  assert.equal(observed.length, 4);
  assert.deepEqual(failures, []);
  cases.push('actual runs preserve each exact session binding and matching terminal run identity');
  assert.ok(observed.every(body => !JSON.stringify(body).includes('synthetic-')));
  assert.ok(observed.every(body => runs.every(p => !JSON.stringify(body).includes(p.runId))));
  cases.push('binding and run IDs remain outside model prompts and tool results');
  const count = observed.length;
  await assert.rejects(invoke({...params('changed'), sessionKey: runs[0].sessionKey}), /Session changed before writer admission/);
  assert.equal(observed.length, count);
  cases.push('changed persisted session identity refuses before inference');
  console.log(JSON.stringify({runtime: 'openclaw@2026.9.6', node: process.version,
    module_sha256: pins, dependency_lock_sha256: digest(lock), passed: cases.length, cases,
    loopback_provider_requests: observed.length, external_provider_calls: 0,
    gateway_authentication_tested: false, concurrency_tested: false, restart_tested: false,
    s0_gate_passed: false}));
} finally {
  await handle?.release();
  server.closeAllConnections(); await new Promise(r => server.close(r));
}
