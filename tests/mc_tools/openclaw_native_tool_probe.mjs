// Real-runtime proof for MC's tool path on the native OpenClaw that dev runs (design §2–3).
// An isolated gateway (own HOME, state and config under a temp dir; loopback only; no real provider)
// loads the minimoi-tools plugin. A fake model provider asks for minimoi_guest_requests; a fake
// MiniMoi listener records exactly what the plugin sends. The probe checks that:
//   1. mc-chat's tool list (the dev policy with group:plugins lifted, minimoi-tools allowed) offers every catalogued tool;
//   2. the plugin sends the session key OpenClaw derived from the portal's `user`, and the agent id;
//   3. the main agent is offered none of the tools;
//   4. with Robert's real plugin list enabled (names only, no credentials, read from PROBE_LIVE_CONFIG or
//      ~/.openclaw/openclaw.json), mc-chat's whole tool list is still only its read tools plus the MiniMoi tools;
//   5. with the credential file missing, the gateway still starts, other agents answer, and no MiniMoi tool exists;
//   6. under OpenClaw's default file policy (not workspace-only) the plugin offers no tools (the agent could read the
//      credential with its own file tools).
// The mc-chat tool policy is tests/mc_tools/fixtures/mc_chat_tools_policy.json: dev's own, changed only as the handoff says.
// Usage: node tests/mc_tools/openclaw_native_tool_probe.mjs [path/to/openclaw.mjs]   (run under heavy_job.py)
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {mkdtempSync, mkdirSync, writeFileSync, chmodSync, realpathSync, cpSync, readFileSync, rmSync} from 'node:fs';
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
const executor = randomBytes(32).toString('base64url');
cpSync(pluginSrc, join(root, 'plugin'), {recursive: true});
writeFileSync(join(root, 'token'), executor + '\n'); chmodSync(join(root, 'token'), 0o600);
const policy = JSON.parse(readFileSync(join(here, 'fixtures/mc_chat_tools_policy.json'), 'utf8'));
const catalogue = JSON.parse(readFileSync(join(pluginSrc, 'tools.json'), 'utf8')).tools.map(t => t.name).sort();
// Robert's other plugins by NAME only (no config, so no credentials ever leave his file).
const liveConfig = process.env.PROBE_LIVE_CONFIG || join(process.env.HOME || '', '.openclaw/openclaw.json');
let liveNames = [];
try { liveNames = Object.keys(JSON.parse(readFileSync(liveConfig, 'utf8')).plugins?.entries || {}).filter(n => n !== 'minimoi-tools'); } catch {}
const READ_TOOLS = new Set([...policy.alsoAllow.filter(n => !n.includes('-')), 'session_status']);

async function freePort() {
  const spare = createServer();
  const port = await listen(spare);
  await new Promise(r => spare.close(r));
  return port;
}

async function withGateway(name, {tokenFile, extraEntries = {}, workspaceOnly = true}, work) {
  const dir = join(root, name);
  const port = await freePort();
  const gatewayToken = randomBytes(24).toString('base64url');
  const config = {
    gateway: {mode: 'local', port, bind: 'loopback', auth: {mode: 'token', token: gatewayToken},
              http: {endpoints: {chatCompletions: {enabled: true}}}},
    update: {checkOnStart: false},
    models: {mode: 'replace', providers: {fake: {baseUrl: `http://127.0.0.1:${providerPort}/v1`, apiKey: 'fake-key',
      api: 'openai-completions', models: [{id: 'fake-model', name: 'Fake', input: ['text'], contextWindow: 32768, maxTokens: 1024}]}}},
    plugins: {load: {paths: [join(root, 'plugin')]}, entries: {...extraEntries, 'minimoi-tools': {enabled: true, config: {
      enabled: true, endpoint: `http://127.0.0.1:${listenerPort}/internal/mc/v1/tools`, tokenFile, agents: ['mc-chat']}}}},
    ...(workspaceOnly ? {tools: {fs: {workspaceOnly: true}}} : {}),  // required by the plugin: no agent file tool can read the credential
    agents: {defaults: {model: {primary: 'fake/fake-model'}},
             entries: {main: {name: 'main', default: true},
                       'mc-chat': {name: 'probe mc-chat', workspace: join(dir, 'ws-mc-chat'), model: {primary: 'fake/fake-model', fallbacks: []},
                                   tools: policy, sandbox: {mode: 'off'}}}},
  };
  mkdirSync(dir, {recursive: true});
  writeFileSync(join(dir, 'openclaw.json'), JSON.stringify(config, null, 1)); chmodSync(join(dir, 'openclaw.json'), 0o600);
  const env = {HOME: join(dir, 'home'), PATH: `${dirname(process.execPath)}:/usr/bin:/bin`, OPENCLAW_CONFIG_PATH: join(dir, 'openclaw.json'),
               OPENCLAW_STATE_DIR: join(dir, 'state'), OPENCLAW_NO_AUTO_UPDATE: '1', OPENCLAW_DISABLE_BONJOUR: '1'};
  const gateway = spawn(process.execPath, [OPENCLAW, 'gateway', 'run', '--port', String(port), '--bind', 'loopback'],
                        {env, cwd: dir, stdio: ['ignore', 'pipe', 'pipe']});
  let log = '';
  gateway.stdout.on('data', d => { log += d; }); gateway.stderr.on('data', d => { log += d; });
  try {
    const deadline = Date.now() + 120000;
    while (true) {
      try { if ((await fetch(`http://127.0.0.1:${port}/readyz`)).ok) break; } catch {}
      if (Date.now() > deadline || gateway.exitCode !== null) throw new Error(`${name}: gateway did not start:\n` + log.slice(-3000));
      await new Promise(r => setTimeout(r, 500));
    }
    const user = 'guild-mc:' + createHash('sha256').update('guild:robert:c-probe').digest('hex').slice(0, 32);
    const turn = model => fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {method: 'POST',
      headers: {authorization: `Bearer ${gatewayToken}`, 'content-type': 'application/json'},
      body: JSON.stringify({model, user, messages: [{role: 'user', content: 'Any guest requests?'}]})});
    return await work({turn, user, log: () => log});
  } finally {
    // Wait for the gateway to really stop, so its state is not still being written when we clean up.
    if (gateway.exitCode === null) {
      const exited = new Promise(r => gateway.once('exit', r));
      try { gateway.kill('SIGTERM'); } catch {}
      const t = setTimeout(() => { try { gateway.kill('SIGKILL'); } catch {} }, 15000);
      await exited;
      clearTimeout(t);
    }
  }
}

const results = {live_plugin_names: liveNames};
try {
  // Phase 1: the tool path itself.
  await withGateway('phase1', {tokenFile: join(root, 'token')}, async ({turn, user}) => {
    const r1 = await turn('openclaw/mc-chat');
    const a1 = await r1.json();
    results.mc_chat_status = r1.status;
    results.mc_chat_answer = a1.choices?.[0]?.message?.content;
    results.all_tools_offered_to_mc_chat = modelCalls.find(c => c.tools.length)?.tools || [];
    results.offered_to_mc_chat = results.all_tools_offered_to_mc_chat.filter(n => n.startsWith('minimoi_'));
    results.tool_calls = toolCalls.map(c => ({url: c.url, session: c.session, agent: c.agent, body: c.body,
                                             bearer_matches: c.auth === `Bearer ${executor}`}));
    assert.equal(r1.status, 200, JSON.stringify(a1));
    assert.deepEqual([...results.offered_to_mc_chat].sort(), catalogue);
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
  });

  // Phase 2: Robert's real plugin list (names only) beside it; record mc-chat's whole effective tool list.
  const extra = Object.fromEntries(liveNames.map(n => [n, {enabled: true}]));
  await withGateway('phase2', {tokenFile: join(root, 'token'), extraEntries: extra}, async ({turn}) => {
    const before = modelCalls.length;
    const r = await turn('openclaw/mc-chat');
    results.live_plugins_mc_chat_status = r.status;
    results.live_plugins_tools_offered_to_mc_chat = modelCalls.slice(before).find(c => c.tools.length)?.tools || [];
    assert.equal(r.status, 200);
    const others = results.live_plugins_tools_offered_to_mc_chat.filter(n => !n.startsWith('minimoi_'));
    results.live_plugins_non_minimoi_tools = others;
    assert.ok(others.every(n => READ_TOOLS.has(n)), 'mc-chat gained a tool beyond its read tools: ' + others.join(','));
    assert.deepEqual(results.live_plugins_tools_offered_to_mc_chat.filter(n => n.startsWith('minimoi_')).sort(), catalogue);
  });

  // Phase 3: the credential file is missing. The gateway must still start and answer; no MiniMoi tool exists.
  await withGateway('phase3', {tokenFile: join(root, 'missing-token'), extraEntries: extra}, async ({turn, log}) => {
    const before = modelCalls.length;
    const main = await turn('openclaw/main');
    const mc = await turn('openclaw/mc-chat');
    results.missing_token_main_status = main.status;
    results.missing_token_mc_chat_status = mc.status;
    results.missing_token_minimoi_tools = modelCalls.slice(before).flatMap(c => c.tools).filter(n => n.startsWith('minimoi_'));
    results.missing_token_logged = /minimoi-tools: not registered/.test(log());
    assert.equal(main.status, 200);
    assert.equal(mc.status, 200);
    assert.deepEqual(results.missing_token_minimoi_tools, []);
  });

  // Phase 4: OpenClaw's default file policy (not workspace-only): the plugin must offer no tools at all.
  await withGateway('phase4', {tokenFile: join(root, 'token'), workspaceOnly: false}, async ({turn}) => {
    const before = modelCalls.length;
    const r = await turn('openclaw/mc-chat');
    results.default_fs_policy_minimoi_tools = modelCalls.slice(before).flatMap(c => c.tools).filter(n => n.startsWith('minimoi_'));
    assert.equal(r.status, 200);
    assert.deepEqual(results.default_fs_policy_minimoi_tools, []);
  });
  results.passed = true;
} catch (error) {
  results.passed = false;
  results.error = String(error?.stack || error).slice(0, 3000);
} finally {
  provider.close(); listener.close();
}
console.log(JSON.stringify({probe: 'mc-tool-path-native-openclaw', openclaw: OPENCLAW, node: process.version, ...results}, null, 1));
// Gateway state is a few hundred MB per run and the Mac's disk is tight: remove it unless asked to keep it.
if (!process.env.PROBE_KEEP) {
  try { rmSync(root, {recursive: true, force: true, maxRetries: 10, retryDelay: 300}); }
  catch { console.error('probe: could not remove ' + root); }
}
process.exitCode = results.passed ? 0 : 1;
