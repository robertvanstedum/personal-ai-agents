/** Isolated pinned-runtime probe: fake provider and one no-effect fixture tool.
 * Run in a network-none ephemeral container, no product state or credentials.
 */
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, dirname} from 'node:path';
const [entry, modulePath] = process.argv.slice(2);
assert.equal(JSON.parse(readFileSync(join(dirname(entry), 'package.json'))).version, '2026.9.6');
const root = mkdtempSync(join(tmpdir(), 'depth-pinned-'));
const trace = join(root, 'proof.jsonl');
const listen = server => new Promise(r => server.listen(0, '127.0.0.1', () => r(server.address().port)));
const requests = [];
let toolId = 0;
const provider = createServer(async (req, res) => {
  let raw = ''; for await (const part of req) raw += part;
  const body = JSON.parse(raw); requests.push(body);
  const messages = body.messages || [];
  const at = messages.findLastIndex(m => m.role === 'user');
  const wantsTool = JSON.stringify(messages[at]?.content).includes('USE_FIXTURE')
    && !messages.slice(at + 1).some(m => m.role === 'tool');
  const delta = wantsTool ? {tool_calls: [{index: 0, id: 'fixture_' + ++toolId, type: 'function',
    function: {name: 'tool_call', arguments: JSON.stringify({id: 'depth_fixture', args: {}})}}]} : {content: 'Fixture answer'};
  res.writeHead(200, {'content-type': 'text/event-stream'});
  const send = (d, finish) => res.write('data: ' + JSON.stringify({id: 'provider-fixture', object: 'chat.completion.chunk',
    created: 1, model: 'fixture', choices: [{index: 0, delta: d, finish_reason: finish}]}) + '\n\n');
  send(delta, null); send({}, wantsTool ? 'tool_calls' : 'stop'); res.end('data: [DONE]\n\n');
});
const providerPort = await listen(provider);
const spare = createServer(); const port = await listen(spare); await new Promise(r => spare.close(r));
const plugin = join(root, 'plugin'); mkdirSync(plugin);
writeFileSync(join(plugin, 'package.json'), JSON.stringify({name: 'depth-fixture', version: '1.0.0', type: 'module',
  openclaw: {extensions: ['./index.mjs']}}));
writeFileSync(join(plugin, 'openclaw.plugin.json'), JSON.stringify({id: 'depth-fixture', name: 'Depth fixture',
  contracts: {tools: ['depth_fixture']}, configSchema: {type: 'object', properties: {}, additionalProperties: false}}));
writeFileSync(join(plugin, 'index.mjs'), `import {createRequire} from 'node:module';
import {appendFileSync} from 'node:fs';
const {tracker,identity}=createRequire(import.meta.url)(${JSON.stringify(modulePath)});
export default {id:'depth-fixture', register(api){
 const proof=tracker({instance:'a'.repeat(32),publish:async receipt=>appendFileSync(${JSON.stringify(trace)},JSON.stringify({receipt,public_key:identity('a'.repeat(32)).publicKey})+'\\n')});
 api.on('model_call_started',proof.modelStarted);
 api.on('before_tool_call',(event,ctx)=>{appendFileSync(${JSON.stringify(join(root, 'tools.jsonl'))},JSON.stringify({event,ctx})+'\\n');return proof.beforeTool(event,ctx)});
 api.on('agent_end',proof.ended);
 api.registerTool({name:'depth_fixture',description:'A no-effect fixture',parameters:{type:'object',properties:{},additionalProperties:false},execute:async()=>{appendFileSync(${JSON.stringify(join(root, 'executions'))},'1');return {content:[{type:'text',text:'fixture complete'}]}}});
}};`);
const config = {gateway: {mode: 'local', port, bind: 'loopback', auth: {mode: 'token', token: 'synthetic-depth-gateway-token'},
  http: {endpoints: {chatCompletions: {enabled: true}}}}, update: {checkOnStart: false},
  models: {mode: 'replace', providers: {fixture: {baseUrl: `http://127.0.0.1:${providerPort}/v1`, apiKey: 'synthetic',
    api: 'openai-completions', models: [{id: 'fixture', name: 'Fixture', input: ['text'], contextWindow: 32768, maxTokens: 1024}]}}},
  plugins: {enabled: true, allow: ['depth-fixture'], load: {paths: [plugin]},
    entries: {'depth-fixture': {enabled: true, hooks: {allowConversationAccess: true}}}, slots: {memory: 'none'}},
  tools: {profile: 'minimal', alsoAllow: ['depth_fixture'], deny: ['exec', 'process', 'gateway']},
  agents: {defaults: {model: {primary: 'fixture/fixture'}}, entries: {main: {name: 'fixture', default: true,
    workspace: join(root, 'workspace'), model: {primary: 'fixture/fixture', fallbacks: []}}}}};
const configPath = join(root, 'openclaw.json'); writeFileSync(configPath, JSON.stringify(config));
const child = spawn(process.execPath, [entry, 'gateway', 'run', '--port', String(port), '--bind', 'loopback'],
  {cwd: root, env: {HOME: join(root, 'home'), PATH: `${dirname(process.execPath)}:/usr/bin:/bin`,
    OPENCLAW_CONFIG_PATH: configPath, OPENCLAW_STATE_DIR: join(root, 'state'),
    OPENCLAW_NO_AUTO_UPDATE: '1', OPENCLAW_DISABLE_BONJOUR: '1'}, stdio: ['ignore', 'pipe', 'pipe']});
let logs = ''; child.stdout.on('data', d => logs += d); child.stderr.on('data', d => logs += d);
try {
  const deadline = Date.now() + 180000;
  for (;;) {
    try {if ((await fetch(`http://127.0.0.1:${port}/readyz`)).ok) break;} catch {}
    if (child.exitCode !== null || Date.now() > deadline) throw Error('gateway failed: ' + logs.slice(-4000));
    await new Promise(r => setTimeout(r, 500));
  }
  const ids = [];
  for (const text of ['PLAIN', 'USE_FIXTURE']) {
    const response = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {method: 'POST',
      headers: {authorization: 'Bearer synthetic-depth-gateway-token', 'content-type': 'application/json'},
      body: JSON.stringify({model: 'openclaw/main', user: 'fixture-' + text, messages: [{role: 'user', content: text}]})});
    assert.equal(response.status, 200);
    ids.push((await response.json()).id);
  }
  let proofs = [];
  for (let i = 0; i < 100; i++) {
    if (existsSync(trace)) proofs = readFileSync(trace, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);
    if (proofs.length >= 2) break;
    await new Promise(r => setTimeout(r, 50));
  }
  assert.equal(proofs.length, 2, logs.slice(-3000));
  assert.deepEqual(proofs.map(p => p.receipt.run), ids);
  assert.equal(readFileSync(join(root,'executions'),'utf8'),'1');
  assert.deepEqual(proofs.map(p => [p.receipt.model_calls, p.receipt.success]), [[1,true],[2,true]]);
  assert.equal(proofs[0].receipt.tool_calls,0);
  assert.ok(proofs[1].receipt.tool_calls >= 1, JSON.stringify({proofs, logs:logs.slice(-1500)}));
  console.log(JSON.stringify({runtime: '2026.9.6', provider_calls: requests.length, real_provider_calls: 0, proofs}));
} finally {
  child.kill('SIGTERM');
  const timer = setTimeout(() => child.kill('SIGKILL'), 10000); timer.unref();
  await new Promise(r => child.exitCode !== null ? r() : child.once('exit', r));
  clearTimeout(timer); provider.close();
}
