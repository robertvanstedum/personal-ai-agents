#!/usr/bin/env node
/* CoS on a subscription, only where a host asks for it: COS_RUNTIME_MODEL_ACCESS=plan. Every other host keeps the pinned
 * gateway config unchanged. In plan mode no API route can serve a turn: the model allowlist holds only subscription models,
 * there are no fallbacks, and the gateway provider is gone. CoS's web search keeps its own key, a separate service.
 * The one exception is a host's explicit COS_METERED_ROUTES: Grok tiers an owner may choose per conversation, pay per use.
 * They are allowed, never a primary and never a fallback, in either mode. */
'use strict';
const fs = require('node:fs');
const REF = /^openai\/[a-zA-Z0-9._-]+$/;      // Claude needs its CLI in this image and its own limits first
const METERED = /^minimoi-gateway\/minimoi-xai-(?:strong|mid|low)$/;
const GATEWAY = 'minimoi-gateway';

function meteredRoutes(env) {
  const refs = String(env.COS_METERED_ROUTES || '').split(',').map(s => s.trim()).filter(Boolean);
  if (new Set(refs).size !== refs.length || refs.some(r => !METERED.test(r))) throw Error('COS_METERED_ROUTES must name minimoi-xai tiers');
  return refs;
}

function meteredModels(refs) {
  return refs.map(r => r.split('/')[1]).map(id => ({id, name: id, input: ['text', 'image'], contextWindow: 131072, maxTokens: 8192}));
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

function apply(config, env) {
  const metered = meteredRoutes(env);
  if ((env.COS_RUNTIME_MODEL_ACCESS || 'gateway') === 'gateway') return metered.length ? offerOnGateway(config, metered) : config;
  if (env.COS_RUNTIME_MODEL_ACCESS !== 'plan') throw Error('Unknown COS model access');
  const profile = env.COS_PLAN_AUTH_PROFILE;
  if (!profile || !profile.startsWith('openai:')) throw Error('Explicit OpenAI OAuth profile required');
  const refs = routes(env), model = {primary: refs[0], fallbacks: []}, modelPolicy = {allow: [...refs, ...metered]};
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
  const metered = meteredRoutes(env), refs = routes(env), allow = [...refs, ...metered];
  const providers = config.models?.providers || {};
  if (JSON.stringify(providers) !== JSON.stringify(metered.length ? {[GATEWAY]: gatewayProvider(metered)} : {})) throw Error('No API provider may remain in plan mode');
  for (const a of [config.agents.defaults, ...Object.values(config.agents.entries)]) {
    if (a.model?.primary !== refs[0] || (a.model.fallbacks || []).length || JSON.stringify(a.modelPolicy?.allow) !== JSON.stringify(allow))
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
module.exports = {apply, validate, routes, meteredRoutes};
