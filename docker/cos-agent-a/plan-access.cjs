#!/usr/bin/env node
/* CoS on a subscription, only where a host asks for it: COS_RUNTIME_MODEL_ACCESS=plan. Every other host keeps the pinned
 * gateway config unchanged. In plan mode the model allowlist holds subscription models, there are no config fallbacks,
 * and the gateway provider is gone, unless a host names one of the two exceptions below. CoS's web search keeps its own key, a separate service.
 * The one exception is a host's explicit COS_METERED_ROUTES: Grok tiers an owner may choose per conversation, pay per use.
 * They are allowed, never a fallback, in either mode, and only with the capped-key proof below: a Grok
 * turn never spends on the master key. Without the proof the route isn't there, and CoS still starts. In plan mode a host
 * may name one of them as the runtime's default with COS_PRIMARY_ROUTE; a turn with a route still runs on that route.
 * COS_FAILOVER_ROUTES names a route's declared API target (core/model_routes catalogue, failover). Allowed
 * in plan mode only and never a config fallback: MiniMoi decides a switch from the turn's signal and tool evidence. It needs
 * proof CoS holds its own capped key, never the master key: the key file, or COS_GATEWAY_KEY_CAPPED=1 where a host passes
 * its own key by environment. A key file holding the master key isn't proof. Without that proof there is no target, so no
 * switch; CoS still starts. */
'use strict';
const fs = require('node:fs');
const REF = /^openai\/[a-zA-Z0-9._-]+$/;      // Claude needs its CLI in this image and its own limits first
const METERED = /^minimoi-gateway\/minimoi-xai-(?:strong|strong-deep|mid|low)$/;
const GATEWAY = 'minimoi-gateway';

function meteredRoutes(env) {
  const refs = String(env.COS_METERED_ROUTES || '').split(',').map(s => s.trim()).filter(Boolean);
  if (new Set(refs).size !== refs.length || refs.some(r => !METERED.test(r))) throw Error('COS_METERED_ROUTES must name minimoi-xai tiers');
  return refs;
}

const FAILOVER = /^minimoi-gateway\/minimoi-(?:openai|anthropic)-(?:strong|mid|low)$/;
// This runs before apply-config.sh swaps the key file into MINIMOI_MODEL_GATEWAY_KEY, so that variable still holds the
// master key here, and a key file equal to it is not a capped key.
function cappedKey(env) {
  if (env.COS_GATEWAY_KEY_CAPPED === '1') return true;
  try {
    const key = fs.readFileSync(env.COS_GATEWAY_KEY_FILE || '/run/cos-key/key', 'utf8').split('\n')[0].trim();
    return /^sk-[A-Za-z0-9_-]{16,200}$/.test(key) && key !== env.MINIMOI_MODEL_GATEWAY_KEY;
  } catch { return false; }
}
function offeredRoutes(env) {
  const refs = meteredRoutes(env);
  return refs.length && cappedKey(env) ? refs : [];
}
function failoverRoutes(env) {
  const refs = String(env.COS_FAILOVER_ROUTES || '').split(',').map(s => s.trim()).filter(Boolean);
  if (new Set(refs).size !== refs.length || refs.some(r => !FAILOVER.test(r))) throw Error('COS_FAILOVER_ROUTES must name OpenAI or Anthropic API tiers');
  return cappedKey(env) ? refs : [];
}

function meteredModels(refs) {
  return refs.map(r => r.split('/')[1]).map(id => ({id, name: id, input: ['text', 'image'], contextWindow: 131072, maxTokens: 8192,
    compat: {supportsPromptCacheKey: true}}));   // OpenClaw sends its session cache key; the gateway keeps xAI warm (#553)
}

function gatewayProvider(refs) {
  return {baseUrl: 'http://model-gateway:4000/v1', apiKey: '${MINIMOI_MODEL_GATEWAY_KEY}', api: 'openai-completions', models: meteredModels(refs)};
}

// A gateway host keeps its pinned role as every agent's primary; the tiers are only added to what may be asked for.
function offerOnGateway(config, refs) {
  const out = JSON.parse(JSON.stringify(config));
  out.models.providers[GATEWAY].models.push(...meteredModels(refs));
  for (const a of [out.agents.defaults, ...Object.values(out.agents.entries)]) {
    if ((a.model.fallbacks || []).length) throw Error('Unexpected model fallback');
    a.modelPolicy.allow.push(...refs);
  }
  return out;
}

function routes(env) {
  const primary = env.COS_PLAN_MODEL;
  const refs = [primary, ...String(env.COS_PLAN_ROUTES || '').split(',').map(s => s.trim()).filter(Boolean)];
  const unique = [...new Set(refs)];
  if (unique.some(r => !REF.test(String(r)))) throw Error('COS plan routes must be subscription OpenAI models');
  return unique;
}

function primaryRef(env, offered) {
  const chosen = String(env.COS_PRIMARY_ROUTE || '').trim();
  if (!chosen) return routes(env)[0];
  if (!METERED.test(chosen) || !meteredRoutes(env).includes(chosen)) throw Error('COS_PRIMARY_ROUTE must be one of COS_METERED_ROUTES');
  return offered.includes(chosen) ? chosen : routes(env)[0];        // no capped-key proof: the route isn't there
}

function apply(config, env) {
  const offered = offeredRoutes(env);
  if ((env.COS_RUNTIME_MODEL_ACCESS || 'gateway') === 'gateway') return offered.length ? offerOnGateway(config, offered) : config;
  if (env.COS_RUNTIME_MODEL_ACCESS !== 'plan') throw Error('Unknown COS model access');
  const metered = [...offered, ...failoverRoutes(env)];
  const profile = env.COS_PLAN_AUTH_PROFILE;
  if (!profile || !profile.startsWith('openai:')) throw Error('Explicit OpenAI OAuth profile required');
  const refs = routes(env), model = {primary: primaryRef(env, offered), fallbacks: []}, modelPolicy = {allow: [...refs, ...metered]};
  const models = Object.fromEntries([...refs, ...metered].map(r => [r, {agentRuntime: {id: 'openclaw'}}]));
  const out = JSON.parse(JSON.stringify(config));
  delete out.models;
  if (metered.length) out.models = {providers: {[GATEWAY]: gatewayProvider(metered)}};
  out.auth = {profiles: {[profile]: {provider: 'openai', mode: 'oauth'}}, order: {openai: [profile]}};
  Object.assign(out.agents.defaults, {model, modelPolicy, models, thinkingDefault: 'high'});
  for (const entry of Object.values(out.agents.entries)) Object.assign(entry, {model, modelPolicy, models});
  validate(out, env);
  return out;
}

function validate(config, env) {
  const metered = [...offeredRoutes(env), ...failoverRoutes(env)], refs = routes(env), allow = [...refs, ...metered];
  const providers = config.models?.providers || {};
  if (JSON.stringify(providers) !== JSON.stringify(metered.length ? {[GATEWAY]: gatewayProvider(metered)} : {})) throw Error('No API provider may remain in plan mode');
  for (const a of [config.agents.defaults, ...Object.values(config.agents.entries)]) {
    if (a.model?.primary !== primaryRef(env, offeredRoutes(env)) || (a.model.fallbacks || []).length || JSON.stringify(a.modelPolicy?.allow) !== JSON.stringify(allow))
      throw Error('Unexpected plan model route or fallback');
  }
  for (const p of Object.values(config.auth?.profiles || {})) if (p.mode !== 'oauth') throw Error('Only a signed-in plan profile is allowed');
}

if (require.main === module) {
  const file = process.argv[2];
  const out = apply(JSON.parse(fs.readFileSync(file, 'utf8')), process.env);
  const tmp = `${file}.plan.${process.pid}`;
  fs.writeFileSync(tmp, JSON.stringify(out, null, 2) + '\n', {mode: 0o600});
  fs.renameSync(tmp, file);
}
module.exports = {apply, validate, routes, meteredRoutes, offeredRoutes, failoverRoutes};
