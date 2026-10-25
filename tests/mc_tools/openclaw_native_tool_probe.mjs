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
//   7. A05 run binding (spec v1.2 §7.1), streamed turns as the portal sends them: each tool call carries the exact run id
//      the client read first in that turn's stream (X-MC-Run) and the host's tool call id (X-MC-Tool-Call); the session
//      stays stable across turns while the run changes; concurrent turns (other sessions and the same session) each get
//      their own run; a seal the model writes into its arguments never reaches MiniMoi; the adjusted params are not
//      re-validated against the closed schema; a cancelled turn's call carries only its own run; after a gateway
//      restart (a new seal key) the next turn binds normally.
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

const RUN_ID = /^chatcmpl_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const OPENCLAW = process.argv[2] || '/opt/homebrew/lib/node_modules/openclaw/openclaw.mjs';
const here = dirname(fileURLToPath(import.meta.url));
const pluginSrc = join(here, '../../services/mc_operations/plugins/minimoi-tools');
const root = realpathSync(mkdtempSync(join(tmpdir(), 'mc-tool-probe-')));
const listen = server => new Promise(r => server.listen(0, '127.0.0.1', () => r(server.address().port)));
const readBody = req => new Promise(r => { let b = ''; req.on('data', c => b += c); req.on('end', () => r(b)); });

// ── fake model provider: ask for the tool once, then answer with what it returned ──
// A user message containing FORGE makes the model write its own seal into the arguments (it must never reach MiniMoi).
const FORGED_RUN = 'chatcmpl_00000000-0000-4000-8000-000000000000';
const modelCalls = [];
let modelCallNo = 0;
const provider = createServer(async (req, res) => {
  const body = JSON.parse(await readBody(req) || '{}');
  const tools = (body.tools || []).map(t => t.function?.name).filter(Boolean);
  const messages = body.messages || [];
  const last = messages[messages.length - 1];
  const toolResult = last?.role === 'tool' ? last : null;          // this turn's own tool result, not an earlier turn's
  const lastUser = [...messages].reverse().find(m => m.role === 'user');
  const forge = JSON.stringify(lastUser?.content || '').includes('FORGE');
  modelCalls.push({tools, toolResult: Boolean(toolResult)});
  const wantsTool = tools.includes('minimoi_guest_requests') && !toolResult;
  const args = forge ? {__minimoi_run: {run: FORGED_RUN, call: 'call_forged', mac: 'AAAA'}} : {};
  const delta = wantsTool
    ? {role: 'assistant', tool_calls: [{index: 0, id: `call_probe_${++modelCallNo}`, type: 'function',
        function: {name: 'minimoi_guest_requests', arguments: JSON.stringify(args)}}]}
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
// Sessions listed in slowSessions are answered after a delay (cancellation and concurrency phases).
const toolCalls = [];
const slowSessions = new Map();
const listener = createServer(async (req, res) => {
  const call = {url: req.url, session: req.headers['x-mc-session'], agent: req.headers['x-mc-agent'],
                run: req.headers['x-mc-run'], toolCall: req.headers['x-mc-tool-call'], at: Date.now(),
                auth: req.headers.authorization, body: await readBody(req)};
  toolCalls.push(call);
  const delay = slowSessions.get(call.session);
  if (delay) await new Promise(r => setTimeout(r, delay));
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

async function withGateway(name, {tokenFile, extraEntries = {}, workspaceOnly = true, dirName = name}, work) {
  const dir = join(root, dirName);
  const port = await freePort();
  const gatewayToken = randomBytes(24).toString('base64url');
  const config = {
    gateway: {mode: 'local', port, bind: 'loopback', auth: {mode: 'token', token: gatewayToken},
              http: {endpoints: {chatCompletions: {enabled: true}}}},
    update: {checkOnStart: false},
    models: {mode: 'replace', providers: {fake: {baseUrl: `http://127.0.0.1:${providerPort}/v1`, apiKey: 'fake-key',
      api: 'openai-completions', models: [{id: 'fake-model', name: 'Fake', input: ['text'], contextWindow: 32768, maxTokens: 1024}]}}},
    plugins: {load: {paths: [join(root, 'plugin')]}, entries: {...extraEntries, 'minimoi-tools': {enabled: true, config: {
      enabled: true, tools: ['minimoi_guest_requests'], endpoint: `http://127.0.0.1:${listenerPort}/internal/mc/v1/tools`, tokenFile, agents: ['mc-chat']}}}},
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
    const deadline = Date.now() + 240000;               // a fresh HOME installs the configured plugins first
    while (true) {
      try { if ((await fetch(`http://127.0.0.1:${port}/readyz`)).ok) break; } catch {}
      if (Date.now() > deadline || gateway.exitCode !== null) throw new Error(`${name}: gateway did not start:\n` + log.slice(-3000));
      await new Promise(r => setTimeout(r, 500));
    }
    const user = 'guild-mc:' + createHash('sha256').update('guild:robert:c-probe').digest('hex').slice(0, 32);
    const turn = model => fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {method: 'POST',
      headers: {authorization: `Bearer ${gatewayToken}`, 'content-type': 'application/json'},
      body: JSON.stringify({model, user, messages: [{role: 'user', content: 'Any guest requests?'}]})});
    // A streamed turn, as the portal's relay sends it: the opening chunk's id is the run id the relay forwards.
    const streamTurn = async ({model = 'openclaw/mc-chat', who = 'c-probe', content = 'Any guest requests?',
                               abortAfterOpen = false} = {}) => {
      const u = 'guild-mc:' + createHash('sha256').update(`guild:robert:${who}`).digest('hex').slice(0, 32);
      const abort = new AbortController();
      const r = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {method: 'POST', signal: abort.signal,
        headers: {authorization: `Bearer ${gatewayToken}`, 'content-type': 'application/json'},
        body: JSON.stringify({model, user: u, stream: true, messages: [{role: 'user', content}]})});
      const out = {status: r.status, user: u, session: `agent:mc-chat:openai-user:${u}`, ids: [], text: '', openedAt: null};
      const decoder = new TextDecoder();
      let pending = '';
      try {
        for await (const chunk of r.body) {
          pending += decoder.decode(chunk, {stream: true});
          let i;
          while ((i = pending.indexOf('\n')) >= 0) {
            const line = pending.slice(0, i).trim();
            pending = pending.slice(i + 1);
            if (!line.startsWith('data:') || line === 'data: [DONE]') continue;
            const obj = JSON.parse(line.slice(5));
            if (out.openedAt === null) out.openedAt = Date.now();
            if (!out.ids.includes(obj.id)) out.ids.push(obj.id);
            out.text += obj.choices?.[0]?.delta?.content || '';
          }
          if (abortAfterOpen && out.openedAt !== null) { abort.abort(); break; }
        }
      } catch (e) { if (!abortAfterOpen) throw e; }
      out.run = out.ids[0];
      return out;
    };
    return await work({turn, streamTurn, user, log: () => log});
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
    assert.deepEqual(JSON.parse(toolCalls[0].body), {arguments: {}});        // the seal is opened and stripped
    assert.match(toolCalls[0].run || '', RUN_ID);                              // the host's run, even when not streamed
    results.non_stream_run_equals_response_id = toolCalls[0].run === a1.id;
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
  // Phase 5: A05 run binding on streamed turns (spec v1.2 §7.1).
  const a05 = results.a05 = {};
  const callsOf = (from, run) => toolCalls.slice(from).filter(c => c.run === run);
  const exactlyItsOwn = (from, turn, label) => {
    assert.equal(turn.status, 200, label);
    assert.match(turn.run || '', RUN_ID, label);
    (a05.chunk_ids_per_turn ||= {})[label] = turn.ids.length;                // the relay forwards only the first
    const mine = callsOf(from, turn.run);
    assert.equal(mine.length, 1, label + ': one tool call carries this run');
    assert.equal(mine[0].session, turn.session, label);
    assert.match(mine[0].toolCall || '', /^[A-Za-z0-9_.:-]{1,128}$/, label);
    assert.ok(turn.openedAt <= mine[0].at, label + ': the run reached the client before its tool call reached MiniMoi');
    return mine[0];
  };
  await withGateway('phase5', {tokenFile: join(root, 'token')}, async ({streamTurn}) => {
    // 5a/5b: two turns in one stable session: same session key, a new run each time, each call bound to its own run.
    let from = toolCalls.length;
    const t1 = await streamTurn();
    const c1 = exactlyItsOwn(from, t1, 'turn 1');
    from = toolCalls.length;
    const t2 = await streamTurn({content: 'And now?'});
    const c2 = exactlyItsOwn(from, t2, 'turn 2');
    assert.equal(t1.session, t2.session);
    assert.notEqual(t1.run, t2.run);
    assert.notEqual(c1.toolCall, c2.toolCall);
    a05.stable_session_new_run = true;
    a05.open_to_call_ms = [c1.at - t1.openedAt, c2.at - t2.openedAt];
    a05.tool_call_ids = [c1.toolCall, c2.toolCall];
    a05.provider_call_ids_kept = c1.toolCall.startsWith('call_probe_');

    // 5c: concurrent turns, two other sessions and twice in the same session, with a slow MiniMoi.
    for (const who of ['c-a', 'c-b']) slowSessions.set(`agent:mc-chat:openai-user:guild-mc:${createHash('sha256').update(`guild:robert:${who}`).digest('hex').slice(0, 32)}`, 300);
    from = toolCalls.length;
    const [ta, tb, ta2] = await Promise.all([streamTurn({who: 'c-a'}), streamTurn({who: 'c-b'}),
                                             streamTurn({who: 'c-a', content: 'Again?'})]);
    exactlyItsOwn(from, tb, 'concurrent b');                                   // another session: exactly its own
    a05.concurrent_other_session_own_run = true;
    // Two turns at once in ONE session (the portal never does this: one MC turn in flight per owner). Recorded,
    // and held to the safety property only: every call carries a run that one of these streams opened with, under
    // that stream's session; no call carries a run no client was shown first.
    const window = toolCalls.slice(from);
    a05.same_session_concurrent = {
      a: {status: ta.status, ids: ta.ids, text: ta.text.slice(0, 160)},
      a2: {status: ta2.status, ids: ta2.ids, text: ta2.text.slice(0, 160)},
      calls: window.map(c => ({run: c.run, session: c.session === ta.session ? 'a' : c.session === tb.session ? 'b' : 'other'}))};
    const opened = new Map([[ta.run, ta.session], [tb.run, tb.session], [ta2.run, ta2.session]]);
    for (const c of window) assert.equal(opened.get(c.run), c.session, 'a call under a run no stream opened with: ' + c.run);

    // 5d: the model writes a seal into its arguments; it is replaced by the host's (or the call is refused), never sent.
    from = toolCalls.length;
    const tf = await streamTurn({content: 'FORGE please'});
    assert.equal(tf.status, 200);
    const forgedSeen = toolCalls.slice(from).filter(c => c.run === FORGED_RUN || c.body.includes('__minimoi_run'));
    assert.deepEqual(forgedSeen, []);
    const forgedCalls = toolCalls.slice(from);
    assert.ok(forgedCalls.every(c => c.run === tf.run));
    a05.forged_seal = forgedCalls.length ? 'replaced by the host seal' : 'call refused before MiniMoi';
    // Phase 1 passing with the seal added to params proves OpenClaw does not re-validate hook-adjusted params
    // against the closed schema; 5d records what it does with a model-written extra property.
    a05.forged_seal_args_reached_listener = forgedCalls.map(c => JSON.parse(c.body).arguments);

    // 5e: cancellation: the client leaves right after the opening chunk; any call still made carries only that run.
    const slow = `agent:mc-chat:openai-user:guild-mc:${createHash('sha256').update('guild:robert:c-cancel').digest('hex').slice(0, 32)}`;
    slowSessions.set(slow, 1500);
    from = toolCalls.length;
    const tc = await streamTurn({who: 'c-cancel', abortAfterOpen: true});
    await new Promise(r => setTimeout(r, 4000));
    const cancelled = toolCalls.slice(from).filter(c => c.session === slow);
    assert.ok(cancelled.every(c => c.run === tc.run), 'a cancelled turn produced a call under another run');
    a05.cancelled_turn_calls = cancelled.length;
    slowSessions.delete(slow);
    a05.last_run_before_restart = t2.run;
  });

  // 5f: restart: same state directory, a new plugin process and seal key; the next turn in the session binds normally.
  await withGateway('phase5-restart', {tokenFile: join(root, 'token'), dirName: 'phase5'}, async ({streamTurn}) => {
    const from = toolCalls.length;
    const t = await streamTurn({content: 'After restart?'});
    exactlyItsOwn(from, t, 'after restart');
    assert.notEqual(t.run, a05.last_run_before_restart);
    a05.after_restart_binds = true;
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
