const assert = require('node:assert/strict');
const fs = require('node:fs');
const p = require(process.argv[2]);
const plan = {};
const dev = {MC_RUNTIME_MODEL_ACCESS:'gateway',MINIMOI_ENV:'dev',MC_MODEL_GATEWAY_KEY:'sk-synthetic-test-key-not-real'};
assert.equal(p.mode(plan),'plan');
assert.deepEqual(p.overlay(plan),{});
for (const key of ['ANTHROPIC_API_KEY','OPENAI_API_KEY','XAI_API_KEY','MC_ANTHROPIC_API_KEY']) assert.throws(()=>p.mode({...dev,[key]:'synthetic'}));
for (const bad of [{MC_MODEL_GATEWAY_KEY:dev.MC_MODEL_GATEWAY_KEY},{...dev,MINIMOI_ENV:'production'},{...dev,MC_MODEL_GATEWAY_KEY:''},{...dev,MC_RUNTIME_MODEL_ACCESS:'other'}]) assert.throws(()=>p.mode(bad));
const merge=(i,l)=>{if(!i||!l||typeof i!=='object'||typeof l!=='object'||Array.isArray(i)||Array.isArray(l))return i;const o={...l};for(const k of Object.keys(i))o[k]=merge(i[k],l[k]);return o;};
const base=JSON.parse(fs.readFileSync(process.argv[3]));
const c=merge(p.overlay(dev),base);p.validate(c,dev);
const reordered=structuredClone(c);reordered.models.providers['minimoi-gateway-mc']=Object.fromEntries(Object.entries(reordered.models.providers['minimoi-gateway-mc']).reverse());p.validate(reordered,dev);
for(const id of ['mc-agent','mc-chat']) {assert.deepEqual(c.agents.entries[id].tools,base.agents.entries[id].tools);assert.deepEqual(c.agents.entries[id].memory,base.agents.entries[id].memory);assert.equal(c.agents.entries[id].workspace,base.agents.entries[id].workspace);}
assert.deepEqual(c.plugins,base.plugins);assert.deepEqual(c.tools,base.tools);
for(const mutate of [d=>d.agents.entries['mc-chat'].model.primary='anthropic/claude-opus-5',d=>d.agents.defaults.model.fallbacks=['anthropic/claude-opus-5'],d=>d.agents.entries['mc-agent'].utilityModel='anthropic/x',d=>d.models.providers.extra={},d=>d.models.providers['minimoi-gateway-mc'].baseUrl='http://elsewhere',d=>d.agents.entries.extra={},d=>d.auth={profiles:{x:{mode:'api_key'}}}]){const bad=structuredClone(c);mutate(bad);assert.throws(()=>p.validate(bad,dev));}
console.log('Gateway mode policy checks passed; tools, memory and persona paths preserved.');

// Explicit plan selection: preserve tools/persona and fail closed on billing/runtime drift.
const oauth = {MC_RUNTIME_MODEL_ACCESS:'plan', MC_PLAN_MODEL:'openai/gpt-6-astra', MC_PLAN_AUTH_PROFILE:'openai:synthetic'};
const planConfig=merge(p.overlay(oauth),base);
p.validate(planConfig,oauth);
for(const id of ['mc-agent','mc-chat']) {
 assert.deepEqual(planConfig.agents.entries[id].tools,base.agents.entries[id].tools);
 assert.deepEqual(planConfig.agents.entries[id].memory,base.agents.entries[id].memory);
 assert.equal(planConfig.agents.entries[id].workspace,base.agents.entries[id].workspace);
}
assert.deepEqual(planConfig.plugins,base.plugins);
for(const mutate of [
 d=>d.models.providers={'minimoi-gateway-mc':c.models.providers['minimoi-gateway-mc']},
 d=>d.auth.profiles.extra={provider:'openai',mode:'api_key'},
 d=>d.auth.order.openai.push('openai:other'),
 d=>d.agents.entries['mc-chat'].model.fallbacks=['anthropic/claude-opus-5'],
 d=>d.agents.entries['mc-agent'].model.primary='minimoi-gateway-mc/minimoi-mc-agent',
 d=>d.agents.defaults.models['openai/gpt-6-astra'].agentRuntime.id='codex',
 d=>d.agents.defaults.imageModel={primary:'openai/other'},
 d=>d.agents.entries['mc-chat'].models['openai/gpt-6-astra'].agentRuntime.id='codex',
]) {const bad=structuredClone(planConfig);mutate(bad);assert.throws(()=>p.validate(bad,oauth));}
assert.throws(()=>p.overlay({...oauth,MC_PLAN_AUTH_PROFILE:''}));
assert.throws(()=>p.overlay({...oauth,MC_PLAN_MODEL:'https://external/model'}));
assert.throws(()=>p.overlay({...oauth,MC_MODEL_GATEWAY_KEY:dev.MC_MODEL_GATEWAY_KEY}));
const claude={MC_RUNTIME_MODEL_ACCESS:'plan',MC_PLAN_MODEL:'anthropic/claude-opus-5-5'};
p.validate(merge(p.overlay(claude),base),claude);
console.log('Explicit OpenAI/Claude plan policy checks passed, with unchanged tools and no API fallback.');
// Several plan routes for per-task selection: subscription only, the default first, no fallbacks.
{
  const multi={MC_RUNTIME_MODEL_ACCESS:'plan',MC_PLAN_MODEL:'openai/gpt-6-astra',MC_PLAN_AUTH_PROFILE:'openai:owner',MC_PLAN_ROUTES:'openai/gpt-6-astra,anthropic/claude-opus-5,openai/gpt-6-sol'};
  assert.deepEqual(p.planRoutes(multi),['openai/gpt-6-astra','anthropic/claude-opus-5','openai/gpt-6-sol']);
  const o=p.overlay(multi);
  assert.deepEqual(o.agents.defaults.modelPolicy.allow,['openai/gpt-6-astra','anthropic/claude-opus-5','openai/gpt-6-sol']);
  assert.deepEqual(o.agents.defaults.model,{primary:'openai/gpt-6-astra',fallbacks:[]});
  assert.equal(o.agents.defaults.models['anthropic/claude-opus-5'].agentRuntime.id,'claude-cli');
  assert.equal(o.agents.entries['mc-chat'].models['openai/gpt-6-sol'].agentRuntime.id,'openclaw');
  const m=merge(o,base);p.validate(m,multi);
  for (const bad of ['xai/grok-4.3','minimoi-gateway-mc/minimoi-mc-agent','openai/x y']) assert.throws(()=>p.overlay({...multi,MC_PLAN_ROUTES:bad}));
  const added=structuredClone(m);added.agents.entries['mc-chat'].modelPolicy.allow.push('openai/other');assert.throws(()=>p.validate(added,multi));
  const fallback=structuredClone(m);fallback.agents.entries['mc-chat'].model.fallbacks=['anthropic/claude-opus-5'];assert.throws(()=>p.validate(fallback,multi));
  const metered=structuredClone(m);metered.models={...metered.models,providers:{x:{}}};assert.throws(()=>p.validate(metered,multi));
  const keyed=structuredClone(m);keyed.auth.profiles.k={provider:'openai',mode:'api_key'};assert.throws(()=>p.validate(keyed,multi));
  const denied=p.claudeSettings(multi).permissions.deny;
  for (const tool of ['Bash','Write','Edit','Read','Task','Agent','WebFetch','WebSearch','Workflow','CronCreate','NotebookEdit','SendMessage']) assert.ok(denied.includes(tool),tool);
  assert.ok(!denied.some(tool=>tool.startsWith('mcp__')));
  // The task list that answered "0 tasks" from outside MC's governed tools (Dev, 6 Nov), and its neighbours.
  for (const tool of ['TaskList','TaskCreate','TaskGet','TaskUpdate','TaskOutput','TodoWrite','NotebookRead','BashOutput','KillShell','ListMcpResourcesTool','ReadMcpResourceTool','ToolSearch']) assert.ok(denied.includes(tool),tool);
  // Default deny: a capitalised name nobody has listed yet is covered, and MC's own tools never are.
  const covers=name=>denied.some(rule=>rule.endsWith('*')?name.startsWith(rule.slice(0,-1)):rule===name);
  for (const tool of ['FutureNativeTool','Zebra','TaskList']) assert.ok(covers(tool),tool);
  for (const tool of ['mcp__openclaw__minimoi_task_list','mcp__openclaw__minimoi_board_note_read']) assert.ok(!covers(tool),tool);
  // The hook refuses any other call, whatever the permission mode, and lets MC's own through.
  const hook=p.claudeSettings(multi).hooks.PreToolUse[0];
  const hits=new RegExp(hook.matcher);
  for (const tool of ['TaskList','Bash','FutureNativeTool','mcp__other__x']) assert.ok(hits.test(tool),tool);
  assert.ok(!hits.test('mcp__openclaw__minimoi_task_list'));
  assert.match(hook.hooks[0].command,/exit 2$/);
  assert.equal(p.claudeSettings({...multi,MC_PLAN_ROUTES:'openai/gpt-6-sol'}),multi.MC_PLAN_MODEL.startsWith('anthropic/')?p.claudeSettings(multi):null);
  assert.equal(p.claudeSettings(dev),null);
  const shell=structuredClone(m);shell.agents.entries['mc-chat'].tools.deny=shell.agents.entries['mc-chat'].tools.deny.filter(x=>x!=='group:runtime');assert.throws(()=>p.validate(shell,multi));
  const files=structuredClone(m);delete files.agents.entries['mc-chat'].tools.fs;assert.throws(()=>p.validate(files,multi));
}
// Plan failover (6 Nov 2026): the declared API target is allowed in plan mode, on MC's capped key, never primary, never a config fallback.
{
  const key='sk-synthetic-test-key-not-real', ref='minimoi-gateway/minimoi-openai-strong', ant='minimoi-gateway/minimoi-anthropic-strong';
  const on={...oauth,MC_PLAN_ROUTES:'anthropic/claude-opus-5',MC_FAILOVER_ROUTES:`${ref},${ant}`,MC_MODEL_GATEWAY_KEY:key};
  assert.equal(p.mode(on),'plan');
  assert.deepEqual(p.failoverRoutes(on),[ref,ant]);
  const o=p.overlay(on);
  assert.equal(o.models.providers['minimoi-gateway'].apiKey,'${MC_MODEL_GATEWAY_KEY}');
  assert.deepEqual(o.models.providers['minimoi-gateway'].models.map(m=>m.id),['minimoi-openai-strong','minimoi-anthropic-strong']);
  for (const a of [o.agents.defaults,...Object.values(o.agents.entries)]) {
    assert.deepEqual(a.model,{primary:'openai/gpt-6-astra',fallbacks:[]});
    assert.deepEqual(a.modelPolicy.allow,['openai/gpt-6-astra','anthropic/claude-opus-5',ref,ant]);
    assert.equal(a.models[ref].agentRuntime.id,'openclaw');
  }
  const m=merge(o,base);p.validate(m,on);
  const fell=structuredClone(m);fell.agents.entries['mc-chat'].model.fallbacks=[ref];assert.throws(()=>p.validate(fell,on));
  // Rule 3: no capped key means no target and no switch; MC still starts on its plan, with no API provider.
  for (const missing of [{...on,MC_MODEL_GATEWAY_KEY:''},{...on,MC_MODEL_GATEWAY_KEY:'mc-placeholder-not-a-key'}]) {
    assert.equal(p.mode(missing),'plan');
    assert.deepEqual(p.failoverRoutes(missing),[]);
    assert.equal(p.overlay(missing).models,undefined);
    assert.ok(!p.overlay(missing).agents.defaults.modelPolicy.allow.includes(ref));
  }
  // Rule 6: only OpenAI and Anthropic API tiers. Grok has no plan adapter yet and stays out of this list.
  for (const bad of ['minimoi-gateway/minimoi-xai-low','xai/grok-4.3','openai/gpt-6-astra','minimoi-gateway/minimoi-mc-agent',`${ref},${ref}`]) assert.throws(()=>p.mode({...on,MC_FAILOVER_ROUTES:bad}));
  assert.throws(()=>p.mode({...on,MC_PLAN_MODEL:''}));
  assert.throws(()=>p.mode({MC_RUNTIME_MODEL_ACCESS:'gateway',MINIMOI_ENV:'dev',MC_MODEL_GATEWAY_KEY:key,MC_FAILOVER_ROUTES:ref}));
  assert.throws(()=>p.mode({...on,OPENAI_API_KEY:'synthetic'}));
  // Taking it off: the start removes what it patched in, and an unused key is refused again.
  const live=structuredClone(m);
  const off={...oauth,MC_PLAN_ROUTES:'anthropic/claude-opus-5'};
  const patch=p.retire(live,off);
  assert.equal(patch.models.providers['minimoi-gateway'],null);
  assert.equal(p.retire(structuredClone(m),on),null);
  assert.throws(()=>p.mode({...oauth,MC_MODEL_GATEWAY_KEY:key}));
  console.log('Plan failover policy checks passed: capped key only, same-provider tiers only, never a config fallback.');
}
