// Explicit dev-only gateway mode. A virtual key is not a provider credential.
const {isDeepStrictEqual} = require('node:util');
const ROUTE = 'minimoi-gateway-mc/minimoi-mc-agent';
function mode(env) {
  for (const name of ['ANTHROPIC_API_KEY','OPENAI_API_KEY','XAI_API_KEY','MC_ANTHROPIC_API_KEY']) {
    if (env[name]) throw Error('Raw provider credential is forbidden: ' + name);
  }
  const selected = env.MC_RUNTIME_MODEL_ACCESS || 'plan';
  if (selected === 'plan') {
    if (env.MC_MODEL_GATEWAY_KEY) throw Error('Gateway credential requires explicit dev gateway mode');
  } else if (selected === 'gateway') {
    if (env.MINIMOI_ENV !== 'dev') throw Error('Gateway mode is dev-only');
    if (!/^sk-[A-Za-z0-9_-]{16,256}$/.test(env.MC_MODEL_GATEWAY_KEY || '')) throw Error('Dedicated gateway credential missing or invalid');
  } else throw Error('Unknown MC model access mode');
  return selected;
}
function overlay(env) {
  if (mode(env) === 'plan') return {};
  const model = {primary: ROUTE, fallbacks: []};
  const policy = {allow: [ROUTE]};
  return {
    models: {providers: {'minimoi-gateway-mc': {
      baseUrl: 'http://127.0.0.1:4000/v1', apiKey: '${MC_MODEL_GATEWAY_KEY}', api: 'openai-completions',
      models: [{id: 'minimoi-mc-agent', name: 'Master Craftsman gateway', input: ['text','image'], contextWindow: 131072, maxTokens: 8192}]
    }}},
    agents: {defaults: {model, modelPolicy: policy, utilityModel: '', decisionModel: ''}, entries: Object.fromEntries(['mc-agent','mc-chat'].map(id => [id, {model, modelPolicy: policy, utilityModel: '', decisionModel: ''}]))}
  };
}
function validate(config, env) {
  if (mode(env) !== 'gateway') return;
  const p = config.models?.providers || {};
  if (Object.keys(p).length !== 1 || !isDeepStrictEqual(p['minimoi-gateway-mc'], overlay(env).models.providers['minimoi-gateway-mc'])) throw Error('Unexpected MC gateway provider configuration');
  const entries = config.agents?.entries || {};
  if (Object.keys(entries).sort().join(',') !== 'mc-agent,mc-chat') throw Error('Unexpected MC agent entry');
  for (const a of [config.agents?.defaults, ...Object.values(entries)]) {
    if (a?.model?.primary !== ROUTE || (a.model.fallbacks || []).length || JSON.stringify(a.modelPolicy?.allow) !== JSON.stringify([ROUTE]) || a.utilityModel || a.decisionModel) throw Error('Unexpected MC model route or fallback');
  }
  for (const p of Object.values(config.auth?.profiles || {})) if (p.mode === 'api_key') throw Error('API-key auth profile forbidden');
}
module.exports = {mode, overlay, validate, ROUTE};
if (require.main === module) {
  try { mode(process.env); } catch (e) { console.error(e.message); process.exit(78); }
}
