import {test} from 'node:test';
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {toolFactory, hook, endpointUrl} from '../../docker/cos-agent-a/plugins/cos-bound/index.mjs';
import {runSealer} from '../../docker/cos-agent-a/plugins/cos-bound/shared.mjs';

const TOKEN = 't'.repeat(40);
const RUN = 'chatcmpl_00000001-0000-0000-0000-000000000000';
const SESSION = 'agent:cos-agent-a:openai-user:cos-confer:' + '0'.repeat(32);
const ENDPOINT = 'http://cos-scheduler:8771/internal/cos/v1';

function fakeFetch(answer = {status: 'done', code: 'ok', message: 'Done.', data: {}}) {
  const calls = [];
  const f = async (url, options) => {
    calls.push({url, options, body: JSON.parse(options.body)});
    const payload = url.endsWith('/instance') ? {status: 'recorded'} : answer;
    return new Response(JSON.stringify(payload), {status: 200, headers: {'content-type': 'application/json'}});
  };
  f.calls = calls;
  return f;
}

function rig({agent = 'cos-agent-a', accept = ['cos-agent-a'], ctx = {}} = {}) {
  const sealer = runSealer(Buffer.alloc(32, 7));
  const fetchImpl = fakeFetch();
  const create = toolFactory({endpoint: ENDPOINT, token: TOKEN, tokenFile: '/run/secrets/cos-bound/token',
    agents: ['cos-agent-a', 'cos-meeting'], acceptCredentialReadableBy: accept, sealer, instance: 'a'.repeat(32), fetchImpl});
  const h = hook({sealer, agents: ['cos-agent-a', 'cos-meeting'], url: endpointUrl(ENDPOINT), token: TOKEN,
                  instance: 'a'.repeat(32), fetchImpl});
  const context = {sessionKey: SESSION.replace('cos-agent-a', agent), agentId: agent, ...ctx};
  return {tools: create(context), h, context, fetchImpl};
}

test('a sealed call reaches the listener with host identity only', async () => {
  const {tools, h, context, fetchImpl} = rig();
  const sealed = h({toolName: 'cos_actions', params: {operation: 'open', title: 'Deck'}},
                   {...context, runId: RUN, toolCallId: 'call-1'});
  const out = await tools[0].execute('call-1', sealed.params);
  assert.equal(out.details.status, 'done');
  const sent = fetchImpl.calls.find(c => c.url.endsWith('/tools/cos_actions'));
  assert.deepEqual(sent.body, {arguments: {operation: 'open', title: 'Deck'}});
  assert.equal(sent.options.headers['X-CoS-Run'], RUN);
  assert.equal(sent.options.headers['X-CoS-Tool-Call'], 'call-1');
  assert.equal(sent.options.headers['X-CoS-Session'], SESSION);
});

test('an unsealed, forged or replayed-for-another-call seal sends nothing', async () => {
  const {tools, h, context, fetchImpl} = rig();
  assert.equal((await tools[0].execute('call-1', {operation: 'list'})).details.code, 'run_identity_missing');
  const forged = {operation: 'list', __minimoi_run: {run: RUN, call: 'call-1', mac: 'x'.repeat(43)}};
  assert.equal((await tools[0].execute('call-1', forged)).details.code, 'run_identity_missing');
  const sealed = h({toolName: 'cos_actions', params: {operation: 'list'}}, {...context, runId: RUN, toolCallId: 'call-1'});
  assert.equal((await tools[0].execute('call-2', sealed.params)).details.code, 'run_identity_missing');
  assert.equal(fetchImpl.calls.filter(c => c.url.includes('/tools/')).length, 0);
});

test('the hook blocks cos_actions without a complete identity or for another agent', () => {
  const {h, context} = rig();
  assert.equal(h({toolName: 'cos_actions', params: {}}, {...context, toolCallId: 'c'}).block, true);
  assert.equal(h({toolName: 'cos_actions', params: {}}, {...context, agentId: 'mc-chat', runId: RUN, toolCallId: 'c'}).block, true);
});

test('native write and exec are logged and never blocked', async () => {
  const {h, context, fetchImpl} = rig();
  assert.equal(h({toolName: 'exec', params: {command: 'python3 make_deck.py'}}, {...context, runId: RUN, toolCallId: 'n1'}), undefined);
  assert.equal(h({toolName: 'write', params: {path: '/minimoi/outbox/deck.pptx'}}, {...context, runId: RUN, toolCallId: 'n2'}), undefined);
  assert.equal(h({toolName: 'read', params: {path: '/x'}}, {...context, runId: RUN, toolCallId: 'n3'}), undefined);
  await new Promise(r => setTimeout(r, 10));
  const logged = fetchImpl.calls.filter(c => c.url.endsWith('/native')).map(c => c.body);
  assert.deepEqual(logged, [{tool: 'exec', target: 'python3 make_deck.py'}, {tool: 'write', target: '/minimoi/outbox/deck.pptx'}]);
});

test('a failing native log never blocks the call', () => {
  const sealer = runSealer(Buffer.alloc(32, 7));
  const h = hook({sealer, agents: ['cos-agent-a'], url: endpointUrl(ENDPOINT), token: TOKEN,
                  fetchImpl: async () => { throw new Error('down'); }});
  assert.equal(h({toolName: 'exec', params: {command: 'ls'}}, {agentId: 'cos-agent-a', sessionKey: SESSION, runId: RUN, toolCallId: 'n'}), undefined);
});

test('cos-meeting gets tools only when workspace-only with the credential outside the workspace', () => {
  assert.equal(rig({agent: 'cos-meeting', accept: []}).tools, null);
  assert.equal(rig({agent: 'cos-meeting', accept: [], ctx: {fsPolicy: {workspaceOnly: true}, workspaceDir: '/run/secrets'}}).tools, null);
  assert.equal(rig({agent: 'cos-meeting', accept: [], ctx: {fsPolicy: {workspaceOnly: true}, workspaceDir: '/home/node/cos-meeting'}}).tools.length, 2);   // cos_actions, cos_tasks
  assert.equal(rig({agent: 'cos-agent-a', accept: []}).tools, null);    // owner exposure must be accepted explicitly
});

test('endpoint is internal only', () => {
  assert.throws(() => endpointUrl('https://evil.example/internal/cos/v1'));
  assert.throws(() => endpointUrl('http://cos-scheduler:8771/internal/cos-actions/v1'));
  assert.ok(endpointUrl('http://127.0.0.1:8771/internal/cos/v1'));
});

test('the instance is announced at registration, retried, and stops on a refusal (review M3)', async () => {
  const {default: plugin} = await import('../../docker/cos-agent-a/plugins/cos-bound/index.mjs');
  const {announcer} = await import('../../docker/cos-agent-a/plugins/cos-bound/index.mjs');
  const statuses = [503, 200];
  const calls = [];
  const fetchImpl = async (url) => { calls.push(url); return new Response('{}', {status: statuses.shift() ?? 200}); };
  const timers = [];
  const a = announcer({url: endpointUrl(ENDPOINT), token: TOKEN, instance: 'c'.repeat(32), fetchImpl,
                       schedule: (fn) => { timers.push(fn); return null; }});
  a.start(); a.start();                         // started once
  await new Promise(r => setTimeout(r, 5));
  assert.equal(calls.length, 1);
  timers.shift()();                             // the retry
  await new Promise(r => setTimeout(r, 5));
  assert.equal(calls.length, 2);
  assert.equal(await a(), true);                // recorded: later calls reuse it
  assert.equal(calls.length, 2);
  const retries = [];
  let refusals = 0;
  const refused = announcer({url: endpointUrl(ENDPOINT), token: TOKEN, instance: 'd'.repeat(32),
                             fetchImpl: async () => { refusals++; return new Response('{}', {status: 409}); },
                             schedule: (fn) => { retries.push(fn); return null; }});
  refused.start();
  await new Promise(r => setTimeout(r, 5));
  assert.equal(refusals, 1);
  assert.equal(retries.length, 0);              // a 4xx refusal is terminal: no retry is scheduled
  assert.ok(plugin.register);
});

test('meeting entries: cos-meeting may use only bound actions and search; cos-closed nothing (review S2/S3)', () => {
  const {h, context} = rig({agent: 'cos-meeting', accept: []});
  const ctx = {...context, runId: RUN, toolCallId: 'm1'};
  assert.equal(h({toolName: 'browser', params: {}}, ctx).block, true);
  assert.equal(h({toolName: 'exec', params: {command: 'ls'}}, ctx).block, true);
  assert.equal(h({toolName: 'web_search', params: {q: 'x'}}, ctx), undefined);
  assert.ok(h({toolName: 'cos_actions', params: {operation: 'list'}}, ctx).params);
  const closed = {sessionKey: SESSION.replace('cos-agent-a', 'cos-closed'), agentId: 'cos-closed', runId: RUN, toolCallId: 'c1'};
  assert.equal(h({toolName: 'web_search', params: {}}, closed).block, true);
  assert.equal(h({toolName: 'cos_actions', params: {}}, closed).block, true);
  const owner = {...context, agentId: 'cos-agent-a', runId: RUN, toolCallId: 'o1'};
  assert.equal(h({toolName: 'browser', params: {}}, owner), undefined);          // owner keeps its tools (Q1)
});

// The ChatGPT route: arguments stay clean under a strict schema, and code mode's long nested ids still bind.
import {servedHook} from '../../docker/cos-agent-a/plugins/cos-bound/index.mjs';
import {runBoundSealer, backendCallId, SEAL} from '../../docker/cos-agent-a/plugins/cos-bound/shared.mjs';

const NESTED = 'tool_search_code:' + 'c'.repeat(37) + '_' + 'f'.repeat(53) + ':cos_github_issues:1234567890123';
const CTX = {runId: RUN, toolCallId: NESTED, agentId: 'cos-agent-a', sessionKey: SESSION, toolName: 'cos_actions'};

test('a bound seal adds nothing to the arguments, opens once, and refuses a replay or changed arguments', () => {
  assert.ok(NESTED.length > 128);
  const sealer = runBoundSealer(), args = {action: 'note'};
  const clean = sealer.seal(args, CTX);
  assert.deepEqual(clean, args);
  assert.ok(!Object.hasOwn(clean, SEAL));
  assert.equal(sealer.open(args, CTX).run, RUN);
  assert.equal(sealer.open(args, CTX), null);
  assert.equal(sealer.seal(args, CTX), null);
  const other = runBoundSealer();
  other.seal(args, CTX);
  assert.equal(other.open({action: 'changed'}, CTX), null);
});

test('a refused seal names its reason and shows no arguments or id', () => {
  const seen = [], sealer = runBoundSealer({onRefuse: d => seen.push(d)});
  assert.equal(sealer.seal({a: 'SECRET'}, {...CTX, toolCallId: 'bad|id'}), null);
  assert.equal(seen[0].reason, 'call_id_invalid');
  assert.ok(!JSON.stringify(seen).includes('SECRET') && !JSON.stringify(seen).includes(RUN));
});

test('the listener gets a stable id within its own limit', () => {
  assert.match(backendCallId(NESTED), /^sha256:[0-9a-f]{64}$/);
  assert.equal(backendCallId(NESTED), backendCallId(NESTED));
  assert.equal(backendCallId('call_1'), 'call_1');
});

test('the served report names the completed call of this exact run, taking the agent from the session key', () => {
  const calls = [];
  const fetchImpl = async (url, options) => { calls.push({url: String(url), options}); return {body: null}; };
  const report = servedHook({agents: ['cos-agent-a'], url: new URL(ENDPOINT), token: TOKEN, fetchImpl});
  const ctx = {sessionKey: SESSION, runId: RUN};                        // the runtime's real context: no agentId
  const event = {outcome: 'completed', provider: 'openai', model: 'gpt-6-astra', api: 'openai-chatgpt-responses', callId: 'x', transport: 'ws'};
  report(event, ctx);
  assert.equal(calls.length, 1);
  assert.ok(calls[0].url.endsWith('/served'));
  assert.deepEqual(JSON.parse(calls[0].options.body), {provider: 'openai', model: 'gpt-6-astra', api: 'openai-chatgpt-responses'});
  assert.equal(calls[0].options.headers['X-CoS-Agent'], 'cos-agent-a');
  assert.equal(calls[0].options.headers['X-CoS-Run'], RUN);
  report({...event, outcome: 'error'}, ctx);
  report({...event, model: 'bad model'}, ctx);
  report(event, {...ctx, runId: 'nope'});
  report(event, {...ctx, sessionKey: 'agent:someone-else:openai-user:x'});
  report(event, {runId: RUN});
  assert.equal(calls.length, 1);
});

test('a tool answer gives its data in details too, because code mode reads only those', async () => {
  const data = {task_id: 'TK-1', state: 'completed'};
  const f = fakeFetch({status: 'done', code: 'ok', message: 'Read.', data});
  const sealer = runSealer();
  const created = toolFactory({endpoint: ENDPOINT, token: TOKEN, tokenFile: '/private/token', agents: ['cos-agent-a'], acceptCredentialReadableBy: ['cos-agent-a'],
    sealer, instance: 'a'.repeat(32), fetchImpl: f});
  const tools = created({sessionKey: SESSION, agentId: 'cos-agent-a', workspaceDir: '/ws'});
  const params = sealer.seal({operation: 'status', task_id: 'TK-1'}, {runId: RUN, toolCallId: 'call-1', agentId: 'cos-agent-a', sessionKey: SESSION});
  const out = await tools.find(t => t.name === 'cos_tasks').execute('call-1', params);
  assert.deepEqual(out.details, {status: 'done', code: 'ok', data});
  assert.ok(out.content[0].text.includes('completed'));
});

test('the plugin listens on the hook that needs no conversation access', () => {
  const on = [];
  const source = readFileSync(new URL('../../docker/cos-agent-a/plugins/cos-bound/index.mjs', import.meta.url), 'utf8');
  for (const m of source.matchAll(/api\.on\('([a-z_]+)'/g)) on.push(m[1]);
  assert.deepEqual(on.sort(), ['before_tool_call', 'model_call_ended']);
});
