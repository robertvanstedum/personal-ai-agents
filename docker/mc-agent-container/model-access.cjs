// Explicit dev-only gateway mode. A virtual key is not a provider credential.
const {isDeepStrictEqual} = require('node:util');
const ROUTE = 'minimoi-gateway-mc/minimoi-mc-agent';
// Pay-per-use Grok tiers a host may let an owner choose per conversation (MC_METERED_ROUTES). Off unless named. They are
// allowed, never the primary and never a fallback, and they reach the gateway with MC's own capped key.
const METERED_PROVIDER = 'minimoi-gateway';
const METERED = /^minimoi-gateway\/minimoi-xai-(?:strong|mid|low)$/;
function meteredRoutes(env) {
  const refs = String(env.MC_METERED_ROUTES || '').split(',').map(s => s.trim()).filter(Boolean);
  if (!refs.length) return refs;
  if (new Set(refs).size !== refs.length || refs.some(r => !METERED.test(r))) throw Error('MC_METERED_ROUTES must name minimoi-xai tiers');
  if (!env.MC_PLAN_MODEL) throw Error('A metered route needs an explicit plan default');
  return refs;
}
// A plan route's declared API target (core/model_routes catalogue, failover), offered to the runtime only through this list.
// Allowed, never the primary and never a config fallback: MiniMoi decides a switch from the turn's signal and tool evidence,
// which OpenClaw's own fallback would not. Only MC's capped key serves it. No valid key: no target, so no switch; MC still starts.
const FAILOVER = /^minimoi-gateway\/minimoi-(?:openai|anthropic)-(?:strong|mid|low)$/;
const CAPPED_KEY = /^sk-[A-Za-z0-9_-]{16,256}$/;
function failoverRoutes(env) {
  const refs = String(env.MC_FAILOVER_ROUTES || '').split(',').map(s => s.trim()).filter(Boolean);
  if (!refs.length) return refs;
  if (new Set(refs).size !== refs.length || refs.some(r => !FAILOVER.test(r))) throw Error('MC_FAILOVER_ROUTES must name OpenAI or Anthropic API tiers');
  if (!env.MC_PLAN_MODEL) throw Error('A failover route needs an explicit plan default');
  return CAPPED_KEY.test(env.MC_MODEL_GATEWAY_KEY || '') ? refs : [];
}
function meteredProvider(refs) {
  return {baseUrl: 'http://127.0.0.1:4000/v1', apiKey: '${MC_MODEL_GATEWAY_KEY}', api: 'openai-completions',
    models: refs.map(r => r.split('/')[1]).map(id => ({id, name: id, input: ['text','image'], contextWindow: 131072, maxTokens: 8192}))};
}
function mode(env) {
  for (const name of ['ANTHROPIC_API_KEY','OPENAI_API_KEY','XAI_API_KEY','MC_ANTHROPIC_API_KEY']) {
    if (env[name]) throw Error('Raw provider credential is forbidden: ' + name);
  }
  const selected = env.MC_RUNTIME_MODEL_ACCESS || 'plan';
  if (selected === 'plan') {
    failoverRoutes(env);
    if (meteredRoutes(env).length) {
      if (!CAPPED_KEY.test(env.MC_MODEL_GATEWAY_KEY || '')) throw Error('Dedicated gateway credential missing or invalid');
    } else if (env.MC_MODEL_GATEWAY_KEY && !env.MC_FAILOVER_ROUTES) throw Error('Gateway credential requires explicit dev gateway mode');
  } else if (selected === 'gateway') {
    if (env.MC_METERED_ROUTES || env.MC_FAILOVER_ROUTES) throw Error('Metered routes belong to plan mode');
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
// Claude's own tools sit outside OpenClaw's tool policy. OpenClaw runs it with user settings and no way to change its
// arguments, so they are refused there, and only MC's governed tools (mcp__openclaw__*) reach it. Three layers, because a
// list of names goes stale:
// 1. A deny rule per capital letter. Claude's own tools are capitalised and MC's are not, so a tool a newer CLI adds is off
//    the list the model sees. Honoured by 2.1.309 and 2.1.317; 2.1.76 ignores these rules.
// 2. The names, for a CLI that ignores those rules. Names no CLI has are harmless.
// 3. A hook refusing any call that isn't mcp__openclaw__*, whatever the permission mode.
// mc_container_security_test.py check 8a reads the CLI's own tool list in the running container and fails on anything else.
const CLAUDE_DENY_BY_SHAPE = [...'ABCDEFGHIJKLMNOPQRSTUVWXYZ'].map(c => c + '*');
const CLAUDE_DENY = ['Agent','Artifact','ArtifactCheck','ArtifactComments','ArtifactData','AskUserQuestion','Bash','BashOutput','Brief','Config',
  'CronCreate','CronDelete','CronList','Edit','EnterPlanMode','EnterWorktree','ExitPlanMode','ExitWorktree','Glob','Grep','KillBash','KillShell','LS',
  'LSP','ListAgents','ListMcpResourcesTool','MCPSearch','Monitor','MultiEdit','NotebookEdit','NotebookRead','PowerShell','PushNotification','REPL',
  'Read','ReadMcpResourceTool','RemoteTrigger','ReportFindings','ScheduleWakeup','SendMessage','SendUserMessage','Skill','Sleep','SlashCommand',
  'StructuredOutput','SubscribePR','Task','TaskCreate','TaskGet','TaskList','TaskOutput','TaskStop','TaskUpdate','TeamCreate','TeamDelete','TodoRead',
  'TodoWrite','ToolSearch','WebBrowser','WebFetch','WebSearch','Workflow','Write'];
const CLAUDE_HOOKS = {PreToolUse: [{matcher: '^(?!mcp__openclaw__)', hooks: [{type: 'command',
  command: "echo 'Refused: this tool is outside MC governed tools. Use the mcp__openclaw__minimoi_ tools.' >&2; exit 2"}]}]};
function claudeSettings(env) {
  return mode(env) === 'plan' && env.MC_PLAN_MODEL && planRoutes(env).some(r => r.startsWith('anthropic/'))
    ? {permissions: {deny: [...CLAUDE_DENY_BY_SHAPE, ...CLAUDE_DENY]}, hooks: CLAUDE_HOOKS} : null;
}
function overlay(env) {
  if (mode(env) === 'plan') {
    if (!env.MC_PLAN_MODEL) return {}; // existing Claude-plan configuration
    const ref = env.MC_PLAN_MODEL;
    const refs = planRoutes(env);
    const profile = env.MC_PLAN_AUTH_PROFILE;
    if (refs.some(r => r.startsWith('openai/')) && (!profile || !profile.startsWith('openai:'))) throw Error('Explicit OpenAI OAuth profile required');
    const metered = [...meteredRoutes(env), ...failoverRoutes(env)];
    const model = {primary: ref, fallbacks: []};
    const policy = {allow: [...refs, ...metered]};
    const models = Object.fromEntries([...refs.map(r => [r, {agentRuntime: {id: r.startsWith('openai/') ? 'openclaw' : 'claude-cli'}}]),
      ...metered.map(r => [r, {agentRuntime: {id: 'openclaw'}}])]);
    return {
      ...(metered.length ? {models: {providers: {[METERED_PROVIDER]: meteredProvider(metered)}}} : {}),
      ...(refs.some(r => r.startsWith('openai/')) ? {auth: {profiles: {[profile]: {provider: 'openai', mode: 'oauth'}}, order: {openai: [profile]}}} : {}),
      agents: {
        defaults: {model, modelPolicy: policy, utilityModel: '', decisionModel: '', models},
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
    if (!isDeepStrictEqual(config.models?.providers || {}, wanted.models?.providers || {})) throw Error('Remove custom API providers before selecting a plan');
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
// Turning Grok off: a live config keeps what an earlier start patched in, and a plan start refuses any API provider. This
// takes it out of `live` and returns the patch that removes it (null deletes a key). Nothing to remove: null, no patch.
function retire(live, env) {
  if (mode(env) !== 'plan' || !env.MC_PLAN_MODEL || meteredRoutes(env).length || failoverRoutes(env).length) return null;
  if (!live.models?.providers?.[METERED_PROVIDER]) return null;
  delete live.models.providers[METERED_PROVIDER];
  const patch = {models: {providers: {[METERED_PROVIDER]: null}}, agents: {defaults: {models: {}}, entries: {}}};
  const stale = (a, into) => {
    for (const ref of Object.keys(a?.models || {})) if (ref.startsWith(METERED_PROVIDER + '/')) { delete a.models[ref]; into[ref] = null; }
  };
  stale(live.agents?.defaults, patch.agents.defaults.models);
  for (const [name, a] of Object.entries(live.agents?.entries || {})) stale(a, (patch.agents.entries[name] = {models: {}}).models);
  return patch;
}
module.exports = {mode, overlay, validate, planRoutes, meteredRoutes, failoverRoutes, retire, claudeSettings, ROUTE};
if (require.main === module) {
  try { mode(process.env); } catch (e) { console.error(e.message); process.exit(78); }
}
