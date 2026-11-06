#!/usr/bin/env node
/* CoS on a subscription, only where a host asks for it: COS_RUNTIME_MODEL_ACCESS=plan. Every other host keeps the pinned
 * gateway config unchanged. In plan mode no API route can serve a turn: the model allowlist holds only subscription models,
 * there are no fallbacks, and the gateway provider is gone. CoS's web search keeps its own key, a separate service. */
'use strict';
const fs = require('node:fs');
const REF = /^openai\/[a-zA-Z0-9._-]+$/;      // Claude needs its CLI in this image and its own limits first

function routes(env) {
  const primary = env.COS_PLAN_MODEL;
  const refs = [primary, ...String(env.COS_PLAN_ROUTES || '').split(',').map(s => s.trim()).filter(Boolean)];
  const unique = [...new Set(refs)];
  if (unique.some(r => !REF.test(String(r)))) throw Error('COS plan routes must be subscription OpenAI models');
  return unique;
}

function apply(config, env) {
  if ((env.COS_RUNTIME_MODEL_ACCESS || 'gateway') === 'gateway') return config;
  if (env.COS_RUNTIME_MODEL_ACCESS !== 'plan') throw Error('Unknown COS model access');
  const profile = env.COS_PLAN_AUTH_PROFILE;
  if (!profile || !profile.startsWith('openai:')) throw Error('Explicit OpenAI OAuth profile required');
  const refs = routes(env), model = {primary: refs[0], fallbacks: []}, modelPolicy = {allow: refs};
  const models = Object.fromEntries(refs.map(r => [r, {agentRuntime: {id: 'openclaw'}}]));
  const out = JSON.parse(JSON.stringify(config));
  delete out.models;
  out.auth = {profiles: {[profile]: {provider: 'openai', mode: 'oauth'}}, order: {openai: [profile]}};
  Object.assign(out.agents.defaults, {model, modelPolicy, models, thinkingDefault: 'high'});
  for (const entry of Object.values(out.agents.entries)) Object.assign(entry, {model, modelPolicy, models});
  validate(out, env);
  return out;
}

function validate(config, env) {
  const refs = routes(env);
  if (Object.keys(config.models?.providers || {}).length) throw Error('No API provider may remain in plan mode');
  for (const a of [config.agents.defaults, ...Object.values(config.agents.entries)]) {
    if (a.model?.primary !== refs[0] || (a.model.fallbacks || []).length || JSON.stringify(a.modelPolicy?.allow) !== JSON.stringify(refs))
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
module.exports = {apply, validate, routes};
