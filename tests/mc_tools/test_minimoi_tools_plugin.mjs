// MC's minimoi-tools plugin: host context only, strict arguments, bounded and honest results.
// Run: node --test tests/mc_tools/test_minimoi_tools_plugin.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, writeFileSync, chmodSync, realpathSync, symlinkSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import plugin, {toolFactory, checkArguments, endpointUrl, tokenFromFile, observeHost} from '../../services/mc_operations/plugins/minimoi-tools/index.mjs';

const TOKEN = 'k'.repeat(40);
const SESSION = 'agent:mc-chat:openai-user:guild-mc:' + 'a'.repeat(32);
const json = (status, body, type = 'application/json') => new Response(JSON.stringify(body), {status, headers: {'content-type': type}});

function setup(reply = () => json(200, {status: 'done', code: 'listed', message: 'ok', data: {}}), extra = {}) {
  const sent = [];
  const fetchImpl = async (url, init) => { sent.push({url, init}); return reply(url, init); };
  const make = toolFactory({endpoint: 'http://127.0.0.1:5011/internal/mc/v1/tools', token: TOKEN, tokenFile: '/secure/mc/token',
    agents: ['mc-chat'], tools: ['minimoi_guest_requests', 'minimoi_data_repair_ticket', 'minimoi_host_health'],
    diskPaths: ['/'], fetchImpl, ...extra});
  // A workspace-only run whose credential lies outside the workspace, as deployment requires.
  const create = ctx => make(ctx && {fsPolicy: {workspaceOnly: true}, workspaceDir: '/ws/mc-chat', ...ctx});
  return {create, sent, make};
}
const tool = (tools, name) => tools.find(t => t.name === name);

test('no tools outside an allowed agent with a host session key', () => {
  const {create} = setup();
  assert.equal(create({agentId: 'main', sessionKey: SESSION}), null);
  assert.equal(create({agentId: 'mc-chat'}), null);
  assert.equal(create({agentId: 'mc-chat', sessionKey: ''}), null);
  assert.equal(create(undefined), null);
  assert.deepEqual(create({agentId: 'mc-chat', sessionKey: SESSION}).map(t => t.name),
                   ['minimoi_host_health', 'minimoi_guest_requests', 'minimoi_data_repair_ticket']);
});

test('identity comes from the host context, never from arguments', async () => {
  const {create, sent} = setup();
  const reject = tool(create({agentId: 'mc-chat', sessionKey: SESSION}), 'minimoi_data_repair_ticket');
  for (const args of [{ref: 'guild:1', sessionKey: 'agent:mc-chat:x'}, {ref: 'guild:1', principal: 'robert'},
                      {ref: 'guild:01'}, {ref: 'guild:1​'}, {}, null, ['guild:1']]) {
    const r = await reject.execute('c1', args);
    assert.equal(r.details.status, 'refused', JSON.stringify(args));
  }
  assert.equal(sent.length, 0);
  await reject.execute('c2', {refs: ['guild:7', 'guild:9'], change: 'reject'});
  assert.equal(sent.length, 1);
  const {url, init} = sent[0];
  assert.equal(url, 'http://127.0.0.1:5011/internal/mc/v1/tools/minimoi_data_repair_ticket');
  assert.equal(init.headers['X-MC-Session'], SESSION);
  assert.equal(init.headers['X-MC-Agent'], 'mc-chat');
  assert.equal(init.redirect, 'error');
  assert.deepEqual(JSON.parse(init.body), {arguments: {refs: ['guild:7', 'guild:9'], change: 'reject'}});
});

test('results: only the exact envelope is believed; anything else is unknown', async () => {
  const cases = [
    [() => json(200, {status: 'needs_confirmation', code: 'proposed', message: 'Reply Confirm', data: {}}), 'needs_confirmation'],
    [() => json(200, {status: 'done', code: 'x', message: 'ok', data: {}, extra: 1}), 'unknown'],
    [() => json(200, {status: 'great', code: 'x', message: 'ok', data: {}}), 'unknown'],
    [() => json(200, {status: 'done', code: 'x', message: 'leak ' + TOKEN, data: {}}), 'unknown'],
    [() => json(200, {status: 'done', code: 'x', message: 'ok', data: {}}, 'text/html'), 'unknown'],
    [() => json(401, {error: 'tool_unauthorized'}), 'refused'],
    [() => json(401, {error: 'other'}), 'unknown'],
    [() => json(500, {error: 'boom'}), 'unknown'],
    [() => new Response('x'.repeat(70000), {status: 200, headers: {'content-type': 'application/json'}}), 'unknown'],
    [() => { throw new TypeError('connection reset'); }, 'unknown'],
  ];
  for (const [reply, status] of cases) {
    const {create} = setup(reply);
    const r = await tool(create({agentId: 'mc-chat', sessionKey: SESSION}), 'minimoi_guest_requests').execute('c', {});
    assert.equal(r.details.status, status, reply.toString());
    assert.ok(!r.content[0].text.includes(TOKEN));
  }
});

test('an aborted call is not sent', async () => {
  const {create, sent} = setup();
  const ac = new AbortController(); ac.abort();
  const r = await tool(create({agentId: 'mc-chat', sessionKey: SESSION}), 'minimoi_guest_requests').execute('c', {}, ac.signal);
  assert.equal(r.details.code, 'not_sent');
  assert.equal(sent.length, 0);
});

test('host health sends a plugin measurement, and reports a missing docker honestly', async () => {
  const {create, sent} = setup(undefined, {docker: '/nonexistent/docker'});
  await tool(create({agentId: 'mc-chat', sessionKey: SESSION}), 'minimoi_host_health').execute('c', {});
  const body = JSON.parse(sent[0].init.body);
  assert.deepEqual(body.arguments, {});
  assert.equal(body.observation.disks[0].mount, '/');
  assert.ok(body.observation.disks[0].total_gb > 0 && body.observation.memory.total_gb > 0);
  assert.deepEqual(body.observation.containers, {error: 'docker_unavailable'});
  assert.deepEqual((await observeHost({diskPaths: ['/']})).containers, {error: 'not_configured'});
});

test('configuration: loopback http or https only, exact path, known tools', () => {
  assert.ok(endpointUrl('https://tools.internal/internal/mc/v1/tools'));
  for (const e of ['http://localhost:5011/internal/mc/v1/tools', 'http://10.0.0.2:5011/internal/mc/v1/tools',
                   'http://127.0.0.1/internal/mc/v1/tools', 'https://x/internal/mc/v1/tools/', 'https://u:p@x/internal/mc/v1/tools',
                   'https://x/internal/mc/v1/tools?a=1']) {
    assert.throws(() => endpointUrl(e), /endpoint invalid/, e);
  }
  assert.throws(() => toolFactory({endpoint: 'https://x/internal/mc/v1/tools', token: TOKEN, agents: ['mc-chat'], tools: ['minimoi_shell']}));
  assert.throws(() => toolFactory({endpoint: 'https://x/internal/mc/v1/tools', token: 'short', agents: ['mc-chat'], tools: ['minimoi_guest_requests']}));
});

test('argument checks mirror the catalogue', () => {
  const schema = {type: 'object', additionalProperties: false, required: ['days'],
                  properties: {days: {type: 'integer', minimum: 1, maximum: 30}, name: {type: 'string', minLength: 1, maxLength: 5}}};
  assert.ok(checkArguments(schema, {days: 3}));
  for (const a of [{days: 0}, {days: 3.5}, {days: '3'}, {days: 3, name: ''}, {days: 3, name: 'abcdef'}, {days: 3, x: 1},
                   Object.create({days: 3})]) {
    assert.equal(checkArguments(schema, a), false, JSON.stringify(a));
  }
});

test('the credential file must be private, regular and not a link', () => {
  const dir = realpathSync(mkdtempSync(join(tmpdir(), 'mc-tools-')));
  const file = join(dir, 'token');
  writeFileSync(file, TOKEN + '\n'); chmodSync(file, 0o600);
  assert.equal(tokenFromFile(file), TOKEN);
  chmodSync(file, 0o644);
  assert.throws(() => tokenFromFile(file), /credential unavailable/);
  chmodSync(file, 0o600);
  symlinkSync(file, join(dir, 'link'));
  assert.throws(() => tokenFromFile(join(dir, 'link')), /credential unavailable/);
  assert.throws(() => tokenFromFile('relative/token'), /credential unavailable/);
});

test('a missing credential registers nothing and never throws into the gateway', () => {
  const registered = [], warned = [];
  const api = {pluginConfig: {enabled: true, endpoint: 'http://127.0.0.1:5011/internal/mc/v1/tools', tokenFile: '/nonexistent/token'},
               registerTool: (...a) => registered.push(a), logger: {warn: m => warned.push(m)}};
  assert.doesNotThrow(() => plugin.register(api));
  assert.equal(registered.length, 0);
  assert.match(warned[0], /not registered/);
  assert.ok(!warned[0].includes('/nonexistent'));
  assert.doesNotThrow(() => plugin.register({...api, pluginConfig: {...api.pluginConfig, endpoint: 'http://10.0.0.1/x'}}));
});

test('no tools when the agent could read the credential with its own file tools', () => {
  const {make} = setup();
  const base = {agentId: 'mc-chat', sessionKey: SESSION};
  assert.equal(make({...base}), null);                                                     // default: not workspace-only
  assert.equal(make({...base, fsPolicy: {workspaceOnly: false}, workspaceDir: '/ws/mc-chat'}), null);
  assert.equal(make({...base, fsPolicy: {workspaceOnly: true}, workspaceDir: '/secure'}), null);   // token inside it
  assert.equal(make({...base, fsPolicy: {workspaceOnly: true}}), null);                     // workspace unknown
  assert.ok(make({...base, fsPolicy: {workspaceOnly: true}, workspaceDir: '/ws/mc-chat'}));
  const devOptOut = toolFactory({endpoint: 'http://127.0.0.1:5011/internal/mc/v1/tools', token: TOKEN, tokenFile: '/x/token',
    agents: ['mc-chat'], tools: ['minimoi_host_health'], allowTokenReadableByAgent: true});
  assert.ok(devOptOut({...base}));
});
