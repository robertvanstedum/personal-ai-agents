// An isolated native OpenClaw gateway with the minimoi-tools plugin and a fake model provider, for the end-to-end
// A05 admission probe (tests/mc_tools/test_native_admission_probe.py; spec v1.2 §7.1, Codex M1 review P1-4).
// Own HOME, state and config under a temp dir; loopback only; no real provider, no private credentials.
//
// The plugin calls MiniMoi at the endpoint and with the credential file the harness gives it (the harness's holding
// proxy in front of the real listener). The fake provider asks for minimoi_guest_requests when the owner's message
// contains TOOL, then answers "ANSWER <tool result>"; otherwise it answers plainly.
//
// Control over stdin, one word per line: "restart" (a new gateway process on the same state and port: a new plugin
// instance),
// "quit". Each (re)start prints one JSON line {"ready": true, "port", "token"}; a failure prints {"ready": false}.
// Usage: node openclaw_admission_gateway.mjs <endpoint> <token file> [path/to/openclaw.mjs]
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {mkdtempSync, mkdirSync, writeFileSync, chmodSync, realpathSync, cpSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomBytes} from 'node:crypto';
import {createInterface} from 'node:readline';

const [endpoint, tokenFile] = process.argv.slice(2, 4);
const OPENCLAW = process.argv[4] || '/opt/homebrew/lib/node_modules/openclaw/openclaw.mjs';
const here = dirname(fileURLToPath(import.meta.url));
const pluginSrc = join(here, '../../services/mc_operations/plugins/minimoi-tools');
const root = realpathSync(mkdtempSync(join(tmpdir(), 'mc-admission-')));
const policy = JSON.parse(readFileSync(join(here, 'fixtures/mc_chat_tools_policy.json'), 'utf8'));
const listen = server => new Promise(r => server.listen(0, '127.0.0.1', () => r(server.address().port)));
const readBody = req => new Promise(r => { let b = ''; req.on('data', c => b += c); req.on('end', () => r(b)); });

let callNo = 0;
const provider = createServer(async (req, res) => {
  const body = JSON.parse(await readBody(req) || '{}');
  const tools = (body.tools || []).map(t => t.function?.name).filter(Boolean);
  const messages = body.messages || [];
  const last = messages[messages.length - 1];
  const lastUser = [...messages].reverse().find(m => m.role === 'user');
  // Explicit scripts exercise isolated native dispatch, not natural-language understanding.
  const scriptMatch = JSON.stringify(lastUser?.content || '').match(/BOARD_SCRIPT:([A-Za-z0-9+/=]+)/);
  const script = scriptMatch ? JSON.parse(Buffer.from(scriptMatch[1], 'base64').toString('utf8')) : [];
  const userIndex = messages.lastIndexOf(lastUser);
  const step = messages.slice(userIndex + 1).filter(m => m.role === 'tool').length;
  const scripted = script[step];
  const wantsTool = !!scripted || last?.role !== 'tool' && tools.includes('minimoi_guest_requests')
    && JSON.stringify(lastUser?.content || '').includes('TOOL');
  const delta = wantsTool
    ? {role: 'assistant', tool_calls: [{index: 0, id: `call_admission_${++callNo}`, type: 'function',
        function: {name: scripted?.name || 'minimoi_guest_requests', arguments: JSON.stringify(scripted?.arguments || {})}}]}
    : {role: 'assistant', content: last?.role === 'tool' ? 'ANSWER ' + String(last.content).slice(0, 400) : 'PLAIN'};
  const finish = wantsTool ? 'tool_calls' : 'stop';
  const base = {id: 'chatcmpl-fake', object: 'chat.completion.chunk', created: 1, model: 'fake-model'};
  res.writeHead(200, {'content-type': 'text/event-stream'});
  res.write(`data: ${JSON.stringify({...base, choices: [{index: 0, delta, finish_reason: null}]})}\n\n`);
  res.write(`data: ${JSON.stringify({...base, choices: [{index: 0, delta: {}, finish_reason: finish}],
                                      usage: {prompt_tokens: 1, completion_tokens: 1, total_tokens: 2}})}\n\n`);
  res.end('data: [DONE]\n\n');
});
const providerPort = await listen(provider);
cpSync(pluginSrc, join(root, 'plugin'), {recursive: true});

async function freePort() {
  const spare = createServer();
  const port = await listen(spare);
  await new Promise(r => spare.close(r));
  return port;
}

const dir = join(root, 'gateway');
mkdirSync(dir, {recursive: true});
let gateway = null;
const port = await freePort();                     // kept across restarts, as on dev: the relay's target never moves
const token = randomBytes(24).toString('base64url');

async function start() {
  const config = {
    gateway: {mode: 'local', port, bind: 'loopback', auth: {mode: 'token', token},
              http: {endpoints: {chatCompletions: {enabled: true}}}},
    update: {checkOnStart: false},
    models: {mode: 'replace', providers: {fake: {baseUrl: `http://127.0.0.1:${providerPort}/v1`, apiKey: 'fake-key',
      api: 'openai-completions', models: [{id: 'fake-model', name: 'Fake', input: ['text'], contextWindow: 32768, maxTokens: 1024}]}}},
    plugins: {load: {paths: [join(root, 'plugin')]}, entries: {'minimoi-tools': {enabled: true, config: {
      enabled: true, endpoint, tokenFile, agents: ['mc-chat'], tools: ['minimoi_guest_requests', ...JSON.parse(readFileSync(join(pluginSrc, 'tools.json'), 'utf8')).tools.filter(t => t.name.startsWith('minimoi_board_') || t.name === 'minimoi_clarify_action').map(t => t.name)]}}}},
    tools: {fs: {workspaceOnly: true}},
    agents: {defaults: {model: {primary: 'fake/fake-model'}},
             entries: {main: {name: 'main', default: true},
                       'mc-chat': {name: 'probe mc-chat', workspace: join(dir, 'ws-mc-chat'),
                                   model: {primary: 'fake/fake-model', fallbacks: []}, tools: policy, sandbox: {mode: 'off'}}}},
  };
  writeFileSync(join(dir, 'openclaw.json'), JSON.stringify(config, null, 1)); chmodSync(join(dir, 'openclaw.json'), 0o600);
  const env = {HOME: join(dir, 'home'), PATH: `${dirname(process.execPath)}:/usr/bin:/bin`, OPENCLAW_CONFIG_PATH: join(dir, 'openclaw.json'),
               OPENCLAW_STATE_DIR: join(dir, 'state'), OPENCLAW_NO_AUTO_UPDATE: '1', OPENCLAW_DISABLE_BONJOUR: '1'};
  gateway = spawn(process.execPath, [OPENCLAW, 'gateway', 'run', '--port', String(port), '--bind', 'loopback'],
                  {env, cwd: dir, stdio: ['ignore', 'pipe', 'pipe']});
  let log = '';
  gateway.stdout.on('data', d => { log += d; }); gateway.stderr.on('data', d => { log += d; });
  const deadline = Date.now() + 240000;
  while (true) {
    try { if ((await fetch(`http://127.0.0.1:${port}/readyz`)).ok) break; } catch {}
    if (Date.now() > deadline || gateway.exitCode !== null) {
      console.log(JSON.stringify({ready: false, log: log.slice(-2000)}));
      return;
    }
    await new Promise(r => setTimeout(r, 500));
  }
  console.log(JSON.stringify({ready: true, port, token}));
}

async function stop() {
  if (gateway && gateway.exitCode === null) {
    const exited = new Promise(r => gateway.once('exit', r));
    try { gateway.kill('SIGTERM'); } catch {}
    const t = setTimeout(() => { try { gateway.kill('SIGKILL'); } catch {} }, 15000);
    await exited;
    clearTimeout(t);
  }
}

await start();
for await (const line of createInterface({input: process.stdin})) {
  if (line.trim() === 'restart') { await stop(); await start(); }
  if (line.trim() === 'quit') break;
}
await stop();
provider.close();
if (!process.env.PROBE_KEEP) {
  try { rmSync(root, {recursive: true, force: true, maxRetries: 10, retryDelay: 300}); } catch {}
}
