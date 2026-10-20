/** Actual pinned plugin loading/factory/lifetime probe, with no model or gateway.
 * Run with OpenClaw state/config pointing at a disposable directory, no provider
 * credentials, and network disabled. argv: packageRoot, disposableFixtureRoot.
 * The fixture tool has no product effects and never returns its context reference.
 * Host admission and the private transport remain separate S0 acceptance gates.
 */
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync, writeFileSync, mkdirSync, mkdtempSync} from 'node:fs';
import {resolve, join} from 'node:path';
import {pathToFileURL} from 'node:url';

const root = resolve(process.argv[2]);
const fixtureRoot = resolve(process.argv[3]);
assert.equal(JSON.parse(readFileSync(join(root, 'package.json'))).version, '2026.9.6');
const modulePath = join(root, 'dist/tools-CvXdkvXK.mjs');
const moduleDigest = createHash('sha256').update(readFileSync(modulePath)).digest('hex');
assert.equal(moduleDigest, 'a31975686eddc6a779865ff896b145b3b41bd7c659ac21761448da6861b0d926');
const {n: acquire} = await import(pathToFileURL(modulePath));
const fixture = mkdtempSync(join(fixtureRoot, 'mc-plugin-'));
const workspace = join(fixture, 'workspace'); mkdirSync(workspace);
writeFileSync(join(fixture, 'package.json'), JSON.stringify({
  name: 'mc-bound-fixture', version: '0.0.1', type: 'module',
  openclaw: {extensions: ['./index.mjs']},
}));
writeFileSync(join(fixture, 'openclaw.plugin.json'), JSON.stringify({
  id: 'mc-bound-fixture', name: 'MC bound fixture',
  contracts: {tools: ['minimoi_bound_fixture']},
  configSchema: {type: 'object', properties: {}, additionalProperties: false},
}));
writeFileSync(join(fixture, 'index.mjs'), `export default {id:'mc-bound-fixture',register(api){
  api.registerTool({contextVersion:2,create(ctx){
    const binding=structuredClone(ctx.toolBindings?.minimoi);
    if(!binding || !binding.runId || !binding.contextRef) return null;
    const session=ctx.sessionKey;
    return {name:'minimoi_bound_fixture',description:'No-effect binding probe',
      parameters:{type:'object',properties:{value:{type:'string'}},additionalProperties:false},
      execute:async(id,args)=>{ctx.assertInvocationCurrent();return {
        content:[{type:'text',text:'fixture'}],details:{
          bindingMatched:binding.runId==='run-'+session && binding.contextRef==='synthetic-'+session,
          noArgumentAuthority:args.contextRef!==binding.contextRef,
        }}}};
  }},{names:['minimoi_bound_fixture']});
}};`);
const config = {plugins: {enabled: true, allow: ['mc-bound-fixture'],
  load: {paths: [fixture]}, entries: {'mc-bound-fixture': {enabled: true}}, slots: {memory: 'none'}}};
const handles = [];
async function instance(session, {bound=true, guarded=true} = {}) {
  const authority = {current: true};
  const binding = {runId: 'run-'+session, contextRef: 'synthetic-'+session};
  const params = {context: {config, workspaceDir: workspace, agentId: 'fixture', sessionKey: session,
    toolBindings: bound ? {minimoi: binding} : {}}, toolAllowlist: ['minimoi_bound_fixture']};
  if (guarded) params.assertInvocationCurrent = () => { if (!authority.current) throw Error('stale_fixture_run'); };
  const handle = await acquire(params); handles.push(handle);
  return {handle, authority, binding, tools: handle.resolveTools()};
}
const cases = [];
async function check(name, fn) { await fn(); cases.push(name); }
try {
  const a = await instance('A');
  await check('manifest loads exact declared tool through real plugin loader', async () => {
    assert.deepEqual(a.tools.map(t=>t.name), ['minimoi_bound_fixture']);
    assert.deepEqual(a.handle.registry.diagnostics.filter(d=>d.level==='error'), []);
  });
  await check('host binding stays in closure and model cannot replace it', async () => {
    const r = await a.tools[0].execute('a1', {value: 'x', contextRef: 'forged'});
    assert.equal(r.details.bindingMatched, true); assert.equal(r.details.noArgumentAuthority, true);
    assert.equal(JSON.stringify(r).includes('synthetic-'), false);
    a.binding.contextRef = 'later-host-mutation';
    assert.equal((await a.tools[0].execute('a2', {value:'x'})).details.bindingMatched, true);
  });
  const b = await instance('B');
  await check('factories for two sessions retain separate immutable bindings', async () => {
    const results = await Promise.all([a.tools[0].execute('a3', {}), b.tools[0].execute('b1', {})]);
    assert.ok(results.every(r=>r.details.bindingMatched));
  });
  await check('missing out-of-band binding exposes no fixture tool', async () => {
    assert.equal((await instance('unbound', {bound:false})).tools.length, 0);
  });
  await check('v2 factory refuses execution without admitted host authority', async () => {
    const missing = await instance('unadmitted', {guarded:false});
    assert.equal(missing.tools.length, 1);
    await assert.rejects(missing.tools[0].execute('missing', {}));
  });
  await check('revoking one invocation blocks its retained tool without affecting the other', async () => {
    a.authority.current = false;
    await assert.rejects(a.tools[0].execute('late-a', {}));
    assert.equal((await b.tools[0].execute('b2', {})).details.bindingMatched, true);
  });
  console.log(JSON.stringify({runtime:'openclaw@2026.9.6', node:process.version,
    module_sha256:moduleDigest, passed:cases.length, cases, provider_calls:0, s0_gate_passed:false}));
} finally { for (const h of handles.reverse()) await h.release(); }
