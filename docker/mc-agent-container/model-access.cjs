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
// Every plan route a conversation may select, the default first. Each stays a subscription route: only these two
// provider families, no custom providers and no API-key profiles (validate below), and no fallbacks between them.
function planRoutes(env) {
  const ref = env.MC_PLAN_MODEL;
  const others = String(env.MC_PLAN_ROUTES || '').split(',').map(s => s.trim()).filter(Boolean);
  const refs = [ref, ...others.filter(r => r !== ref)];
  if (new Set(refs).size !== refs.length) throw Error('Duplicate plan route');
  for (const r of refs) if (!/^(openai|anthropic)\/[a-zA-Z0-9._-]+$/.test(r)) throw Error('Invalid plan model reference');
  return refs;
}
// Claude's own tools sit outside OpenClaw's tool policy, so they are switched off and only MC's governed tools reach it.
// The defaults these replace are OpenClaw's own for claude-cli, with only --tools added.
const CLAUDE_ARGS = ['-p','--output-format','stream-json','--include-partial-messages','--verbose','--setting-sources','user',
  '--tools','','--allowedTools','mcp__openclaw__*','--disallowedTools','ScheduleWakeup,CronCreate,Bash(run_in_background:true),Monitor'];
const CLAUDE_BACKEND = {args: CLAUDE_ARGS, resumeArgs: [...CLAUDE_ARGS, '--resume', '{sessionId}']};
function overlay(env) {
  if (mode(env) === 'plan') {
    if (!env.MC_PLAN_MODEL) return {}; // existing Claude-plan configuration
    const ref = env.MC_PLAN_MODEL;
    const refs = planRoutes(env);
    const profile = env.MC_PLAN_AUTH_PROFILE;
    if (refs.some(r => r.startsWith('openai/')) && (!profile || !profile.startsWith('openai:'))) throw Error('Explicit OpenAI OAuth profile required');
    const model = {primary: ref, fallbacks: []};
    const policy = {allow: refs};
    const models = Object.fromEntries(refs.map(r => [r, {agentRuntime: {id: r.startsWith('openai/') ? 'openclaw' : 'claude-cli'}}]));
    return {
      ...(refs.some(r => r.startsWith('openai/')) ? {auth: {profiles: {[profile]: {provider: 'openai', mode: 'oauth'}}, order: {openai: [profile]}}} : {}),
      agents: {
        defaults: {model, modelPolicy: policy, utilityModel: '', decisionModel: '', models,
                   ...(refs.some(r => r.startsWith('anthropic/')) ? {cliBackends: {'claude-cli': CLAUDE_BACKEND}} : {})},
        entries: Object.fromEntries(['mc-agent','mc-chat'].map(id => [id, {model, modelPolicy: policy, utilityModel: '', decisionModel: '', models}]))
      }
    };
  }
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
  if (mode(env) === 'plan') {
    if (!env.MC_PLAN_MODEL) return; // preserve legacy plan behavior until explicitly selected
    const wanted = overlay(env);
    if (Object.keys(config.models?.providers || {}).length) throw Error('Remove custom API providers before selecting a plan');
    for (const p of Object.values(config.auth?.profiles || {})) if (p.mode === 'api_key') throw Error('API-key auth profile forbidden');
    if (config.agents?.defaults?.imageModel?.primary || (config.agents?.defaults?.imageModel?.fallbacks || []).length) throw Error('Separate image model forbidden in explicit plan mode');
    for (const a of [config.agents?.defaults, ...Object.values(config.agents?.entries || {})]) {
      if (a?.model?.primary !== env.MC_PLAN_MODEL || (a.model.fallbacks || []).length ||
          !isDeepStrictEqual(a.modelPolicy?.allow, wanted.agents.defaults.modelPolicy.allow) || a.utilityModel || a.decisionModel)
        throw Error('Unexpected plan model route or fallback');
    }
    if (Object.keys(config.agents?.entries || {}).sort().join(',') !== 'mc-agent,mc-chat') throw Error('Unexpected MC agent entry');
    for (const [ref, runtime] of Object.entries(wanted.agents.defaults.models)) {
      for (const a of [config.agents.defaults, ...Object.values(config.agents.entries)]) {
        if (!isDeepStrictEqual(a.models?.[ref], runtime)) throw Error('Unexpected subscription runtime');
      }
    }
    if (planRoutes(env).some(r => r.startsWith('anthropic/')) &&
        !isDeepStrictEqual(config.agents.defaults.cliBackends?.['claude-cli'], CLAUDE_BACKEND))
      throw Error("Claude's own tools must be off");
    const chat = config.agents.entries['mc-chat'].tools || {};
    if (!(chat.deny || []).includes('group:runtime') || chat.fs?.workspaceOnly !== true) throw Error('The conversation lane must have no shell and workspace-only files');
    if (planRoutes(env).some(r => r.startsWith('openai/'))) {
      if (!isDeepStrictEqual(config.auth?.order?.openai, [env.MC_PLAN_AUTH_PROFILE]) ||
          !isDeepStrictEqual(config.auth?.profiles?.[env.MC_PLAN_AUTH_PROFILE], {provider:'openai',mode:'oauth'}))
        throw Error('OpenAI must use only the selected subscription profile');
    }
    return;
  }
  const p = config.models?.providers || {};
  if (Object.keys(p).length !== 1 || !isDeepStrictEqual(p['minimoi-gateway-mc'], overlay(env).models.providers['minimoi-gateway-mc'])) throw Error('Unexpected MC gateway provider configuration');
  const entries = config.agents?.entries || {};
  if (Object.keys(entries).sort().join(',') !== 'mc-agent,mc-chat') throw Error('Unexpected MC agent entry');
  for (const a of [config.agents?.defaults, ...Object.values(entries)]) {
    if (a?.model?.primary !== ROUTE || (a.model.fallbacks || []).length || JSON.stringify(a.modelPolicy?.allow) !== JSON.stringify([ROUTE]) || a.utilityModel || a.decisionModel) throw Error('Unexpected MC model route or fallback');
  }
  for (const p of Object.values(config.auth?.profiles || {})) if (p.mode === 'api_key') throw Error('API-key auth profile forbidden');
}
module.exports = {mode, overlay, validate, planRoutes, ROUTE};
if (require.main === module) {
  try { mode(process.env); } catch (e) { console.error(e.message); process.exit(78); }
}
