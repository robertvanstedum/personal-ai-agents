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
