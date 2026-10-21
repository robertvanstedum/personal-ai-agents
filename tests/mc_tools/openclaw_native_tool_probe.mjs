// Real-runtime proof for MC's tool path on the native OpenClaw that dev runs (design §2–3).
// An isolated gateway (own HOME, state and config under a temp dir; loopback only; no real provider)
// loads the minimoi-tools plugin. A fake model provider asks for minimoi_guest_requests; a fake
// MiniMoi listener records exactly what the plugin sends. The probe checks that:
//   1. mc-chat's tool list (the dev policy with group:plugins lifted, minimoi-tools allowed) offers every catalogued tool;
//   2. the plugin sends the session key OpenClaw derived from the portal's `user`, and the agent id;
//   3. the main agent is offered none of the tools.
// The mc-chat tool policy is tests/mc_tools/fixtures/mc_chat_tools_policy.json: dev's own, changed only as the handoff says.
// Usage: node tests/mc_tools/openclaw_native_tool_probe.mjs [path/to/openclaw.mjs]   (run under heavy_job.py)
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {mkdtempSync, writeFileSync, chmodSync, realpathSync, cpSync, readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash, randomBytes} from 'node:crypto';
import assert from 'node:assert/strict';

const OPENCLAW = process.argv[2] || '/opt/homebrew/lib/node_modules/openclaw/openclaw.mjs';
const here = dirname(fileURLToPath(import.meta.url));
const pluginSrc = join(here, '../../services/mc_operations/plugins/minimoi-tools');
const root = realpathSync(mkdtempSync(join(tmpdir(), 'mc-tool-probe-')));
const listen = server => new Promise(r => server.listen(0, '127.0.0.1', () => r(server.address().port)));
const readBody = req => new Promise(r => { let b = ''; req.on('data', c => b += c); req.on('end', () => r(b)); });

// ── fake model provider: ask for the tool once, then answer with what it returned ──
const modelCalls = [];
const provider = createServer(async (req, res) => {
  const body = JSON.parse(await readBody(req) || '{}');
  const tools = (body.tools || []).map(t => t.function?.name).filter(Boolean);
  const toolResult = (body.messages || []).find(m => m.role === 'tool');
  modelCalls.push({tools, toolResult: Boolean(toolResult)});
  const wantsTool = tools.includes('minimoi_guest_requests') && !toolResult;
  const delta = wantsTool
    ? {role: 'assistant', tool_calls: [{index: 0, id: 'call_probe_1', type: 'function',
        function: {name: 'minimoi_guest_requests', arguments: JSON.stringify({})}}]}
    : {role: 'assistant', content: 'PROBE-ANSWER ' + (toolResult ? String(toolResult.content).slice(0, 200) : 'no-tool')};
  const finish = wantsTool ? 'tool_calls' : 'stop';
  const base = {id: 'chatcmpl-probe', object: 'chat.completion.chunk', created: 1, model: 'fake-model'};
  if (body.stream) {
    res.writeHead(200, {'content-type': 'text/event-stream'});
    res.write(`data: ${JSON.stringify({...base, choices: [{index: 0, delta, finish_reason: null}]})}\n\n`);
    res.write(`data: ${JSON.stringify({...base, choices: [{index: 0, delta: {}, finish_reason: finish}],
                                        usage: {prompt_tokens: 1, completion_tokens: 1, total_tokens: 2}})}\n\n`);
    res.end('data: [DONE]\n\n');
  } else {
    const message = wantsTool ? {role: 'assistant', content: null, tool_calls: delta.tool_calls.map(({index, ...t}) => t)}
                              : {role: 'assistant', content: delta.content};
    res.writeHead(200, {'content-type': 'application/json'});
    res.end(JSON.stringify({...base, object: 'chat.completion', choices: [{index: 0, message, finish_reason: finish}],
                            usage: {prompt_tokens: 1, completion_tokens: 1, total_tokens: 2}}));
  }
});

// ── fake MiniMoi listener: record exactly what arrives ──
const toolCalls = [];
const listener = createServer(async (req, res) => {
  toolCalls.push({url: req.url, session: req.headers['x-mc-session'], agent: req.headers['x-mc-agent'],
                  auth: req.headers.authorization, body: await readBody(req)});
  res.writeHead(200, {'content-type': 'application/json'});
  res.end(JSON.stringify({status: 'done', code: 'listed', message: 'PROBE-LISTENER-REPLY', data: {}}));
});

const providerPort = await listen(provider);
const listenerPort = await listen(listener);
const spare = createServer();
const gatewayPort = await listen(spare);                 // a free loopback port for the gateway
await new Promise(r => spare.close(r));
const executor = randomBytes(32).toString('base64url');
const gatewayToken = randomBytes(24).toString('base64url');
cpSync(pluginSrc, join(root, 'plugin'), {recursive: true});
writeFileSync(join(root, 'token'), executor + '\n'); chmodSync(join(root, 'token'), 0o600);
const real = JSON.parse(readFileSync(join(here, 'fixtures/mc_chat_tools_policy.json'), 'utf8'));
const config = {
  gateway: {mode: 'local', port: gatewayPort, bind: 'loopback', auth: {mode: 'token', token: gatewayToken},
            http: {endpoints: {chatCompletions: {enabled: true}}}},
  update: {checkOnStart: false},
  models: {mode: 'replace', providers: {fake: {baseUrl: `http://127.0.0.1:${providerPort}/v1`, apiKey: 'fake-key',
    api: 'openai-completions', models: [{id: 'fake-model', name: 'Fake', input: ['text'], contextWindow: 32768, maxTokens: 1024}]}}},
  plugins: {load: {paths: [join(root, 'plugin')]}, entries: {'minimoi-tools': {enabled: true, config: {
    enabled: true, endpoint: `http://127.0.0.1:${listenerPort}/internal/mc/v1/tools`, tokenFile: join(root, 'token'),
    agents: ['mc-chat']}}}},
  agents: {defaults: {model: {primary: 'fake/fake-model'}},
           entries: {main: {name: 'main', default: true},
                     'mc-chat': {name: 'probe mc-chat', workspace: join(root, 'ws-mc-chat'), model: {primary: 'fake/fake-model', fallbacks: []},
                                 tools: real, sandbox: {mode: 'off'}}}},
};
writeFileSync(join(root, 'openclaw.json'), JSON.stringify(config, null, 1)); chmodSync(join(root, 'openclaw.json'), 0o600);

const env = {HOME: join(root, 'home'), PATH: `${dirname(process.execPath)}:/usr/bin:/bin`, OPENCLAW_CONFIG_PATH: join(root, 'openclaw.json'),
             OPENCLAW_STATE_DIR: join(root, 'state'), OPENCLAW_NO_AUTO_UPDATE: '1', OPENCLAW_DISABLE_BONJOUR: '1'};
const gateway = spawn(process.execPath, [OPENCLAW, 'gateway', 'run', '--port', String(gatewayPort), '--bind', 'loopback'],
                      {env, cwd: root, stdio: ['ignore', 'pipe', 'pipe']});
let log = '';
gateway.stdout.on('data', d => { log += d; }); gateway.stderr.on('data', d => { log += d; });
const stop = () => { try { gateway.kill('SIGTERM'); } catch {} provider.close(); listener.close(); };
const results = {};
try {
  const deadline = Date.now() + 90000;
  while (true) {
    try { if ((await fetch(`http://127.0.0.1:${gatewayPort}/readyz`)).ok) break; } catch {}
    if (Date.now() > deadline || gateway.exitCode !== null) throw new Error('gateway did not start:\n' + log.slice(-3000));
    await new Promise(r => setTimeout(r, 500));
  }
  const user = 'guild-mc:' + createHash('sha256').update('guild:robert:c-probe').digest('hex').slice(0, 32);
  const turn = (model) => fetch(`http://127.0.0.1:${gatewayPort}/v1/chat/completions`, {method: 'POST',
    headers: {authorization: `Bearer ${gatewayToken}`, 'content-type': 'application/json'},
    body: JSON.stringify({model, user, messages: [{role: 'user', content: 'Any guest requests?'}]})});

  const r1 = await turn('openclaw/mc-chat');
  const a1 = await r1.json();
  results.mc_chat_status = r1.status;
  results.mc_chat_answer = a1.choices?.[0]?.message?.content;
  results.all_tools_offered_to_mc_chat = modelCalls.find(c => c.tools.length)?.tools || [];
  results.offered_to_mc_chat = results.all_tools_offered_to_mc_chat.filter(n => n.startsWith('minimoi_'));
  results.tool_calls = toolCalls.map(c => ({url: c.url, session: c.session, agent: c.agent, body: c.body,
                                           bearer_matches: c.auth === `Bearer ${executor}`}));
  assert.equal(r1.status, 200, JSON.stringify(a1));
  assert.deepEqual(results.offered_to_mc_chat.sort(), JSON.parse(readFileSync(join(pluginSrc, 'tools.json'), 'utf8'))
                     .tools.map(t => t.name).sort());
  assert.equal(toolCalls.length, 1);
  assert.equal(toolCalls[0].url, '/internal/mc/v1/tools/minimoi_guest_requests');
  assert.equal(toolCalls[0].session, `agent:mc-chat:openai-user:${user}`);    // what core/mc_tools/binding.py parses
  assert.equal(toolCalls[0].agent, 'mc-chat');
  assert.equal(toolCalls[0].auth, `Bearer ${executor}`);
  assert.deepEqual(JSON.parse(toolCalls[0].body), {arguments: {}});
  assert.match(results.mc_chat_answer, /PROBE-LISTENER-REPLY/);

  const before = modelCalls.length;
  const r2 = await turn('openclaw/main');
  results.main_status = r2.status;
  results.offered_to_main = modelCalls.slice(before).flatMap(c => c.tools).filter(n => n.startsWith('minimoi_'));
  assert.deepEqual(results.offered_to_main, []);
  assert.equal(toolCalls.length, 1);
  results.passed = true;
} catch (error) {
  results.passed = false;
  results.error = String(error?.stack || error).slice(0, 3000);
  results.gateway_log_tail = log.slice(-2000);
} finally {
  stop();
}
console.log(JSON.stringify({probe: 'mc-tool-path-native-openclaw', openclaw: OPENCLAW, node: process.version, ...results}, null, 1));
process.exitCode = results.passed ? 0 : 1;
