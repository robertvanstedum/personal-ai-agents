# Shared LiteLLM Model Gateway — Specification

**Status:** Local G1/G2/G3 and COS bounded-search adapter implemented; awaiting Robert's diff review
**Date:** 2026-08-15  
**Owner / decision point:** Robert  
**Initial consumer:** Chief of Staff / COS Agent A  
**Intended consumers:** Curator, Mein Deutsch, Meu Português, Guild, and future domains  
**Related:** Spec #133 intelligence layer; Spec #146 OpenClaw COS Gateway;
`docs/design/SOLUTION_APPROACH_COS_MASTER_CRAFTSMAN.md`

---

## 1. Purpose

Create a reusable MinimoI model-gateway module that owns provider selection,
fallback, provider health, and model-cost telemetry independently of any domain
or agent runtime.

The first rollout replaces the provisional provider-routing logic inside COS.
OpenClaw remains COS Agent A's runtime and continues to own identity, session,
workspace, tool policy, and the agent loop. LiteLLM becomes the single routing
authority beneath OpenClaw.

This specification governs the shared module and its first COS integration. It
is intentionally reusable: later domains adopt the same gateway contract rather
than copying provider loops into domain Python.

---

## 2. Problem statement

The first COS Agent A fallback implementation exposed several structural
failures:

- provider order existed in both OpenClaw and COS Python;
- the configured primary could be reported even when another provider served;
- credentials and non-secret selections were not reproducible after container
  recreation;
- a reachable Ollama model was assumed to be an agent fallback without proving
  tool-call compatibility;
- critical runtime state could differ from the committed configuration;
- the pattern could not be reused by another domain without copying COS code.

These are architecture defects, not merely configuration mistakes. The gateway
must provide one source of truth and fail closed when an enabled route has not
passed its declared capability tests.

---

## 3. Architecture decision

```text
Domain application
    -> optional agent runtime (OpenClaw for COS Agent A)
        -> MinimoI Model Gateway (LiteLLM)
            -> xAI
            -> Anthropic
            -> OpenAI (disabled until configured and accepted)
            -> Ollama on the development host
```

### Responsibility boundary

| Layer | Owns | Must not own |
|---|---|---|
| Domain | Intent, domain policy, memory, user experience | Provider retry loops |
| Agent runtime | Identity, session, tools, agent loop | Cross-domain provider policy |
| Model gateway | Provider adapters, model groups, fallback, health, usage | Domain memory or agent personality |
| Provider/runtime | Model inference | MinimoI authorization decisions |

LiteLLM is a separately deployable service, not a Python package imported by
COS. Consumers use its authenticated OpenAI-compatible HTTP boundary.

---

## 4. Current and deferred scope

### Approved current scope

- pinned LiteLLM container and declarative YAML configuration;
- private Docker-network access with an optional loopback-only development port;
- independent gateway credential, never a provider API key;
- model groups and ordered fallbacks owned only by LiteLLM;
- xAI primary and fast routes;
- Anthropic independent-provider fallback;
- tool-capable Ollama fallback for Mac development;
- OpenAI route present but disabled until its credential and exact model ID are
  accepted;
- COS Agent A migration through one logical gateway model;
- automated structural tests and live primary/fallback/local capability probes;
- rollback to the already-supported direct OpenClaw provider configuration.

### Explicitly deferred

- production deployment;
- migration of Curator, language domains, Guild, or other consumers;
- AWS Bedrock evaluation;
- SGLang, vLLM, GPU EC2, SageMaker, or production self-hosted inference;
- automatic budget enforcement;
- dashboard/UI work;
- automatic routing based on prompt content or model-scored classification.

Bedrock remains a later AWS model option. SGLang/vLLM and GPU serving remain
roadmap options only when scale, privacy, or cost justify dedicated compute.

---

## 5. Shared gateway contract

### Logical model names

Consumers request stable MinimoI logical names rather than provider model IDs.
The first name is:

`minimoi-cos-agent`

Later domains receive their own logical names only when their requirements or
budgets differ. Shared requirements should reuse a common model group.

### Authentication

- Consumers authenticate with `MINIMOI_MODEL_GATEWAY_KEY`.
- Provider credentials are available only to the gateway container.
- OpenClaw receives the gateway key but no xAI, Anthropic, or OpenAI key after
  migration.
- The local credential lives in the ignored development secret store.
- Production credentials must be encrypted SSM `SecureString` parameters under
  `/minimoi/production/*` and restored into a fresh runtime during acceptance.

### Route declaration

Every route declares:

- logical group;
- provider/model ID;
- credential environment reference, when required;
- enabled state;
- capability class;
- timeout and retry policy;
- cost metadata source;
- environment eligibility (`development`, `production`, or both).

Provider/model IDs never appear in domain business code.

### Capability classes

The initial COS group requires:

- text chat;
- system messages;
- structured tool definitions;
- an actual tool-selection round trip;
- multi-turn compatibility through OpenClaw;
- context sufficient for the Agent A identity and COS platform context.

A model that can chat but rejects tool schemas is not eligible for the COS
agent group. It may later belong to a separately named chat-only group.

---

## 6. Initial routing policy

The intended order is:

1. `xai/grok-4`
2. `xai/grok-4-1-fast`
3. `anthropic/claude-sonnet-4-6`
4. accepted OpenAI model, disabled initially
5. accepted tool-capable Ollama model for development

`qwen3:1.7b` passed direct tool selection but failed the full OpenClaw path by
repeatedly emitting the reserved `NO_REPLY` sentinel for direct COS messages,
including with thinking disabled. `llama3.2:3b` passed direct tool selection
but returned a hallucinated tool payload in the full Agent A path. Both are
excluded from the COS agent group. The active local acceptance candidate is
therefore `qwen3:4b`, with reasoning kept in Ollama's separate thinking field
so OpenClaw does not expose it as assistant-visible content.

`gemma3:1b` is excluded from the COS agent group because live acceptance proved
that it rejects tool schemas. It may remain installed for unrelated text-only
experiments.

### Failure policy

- one gateway owns fallback; OpenClaw and COS must not maintain competing chains;
- authentication, rate-limit, connection, timeout, and provider-availability
  failures may advance to the next accepted route;
- invalid request, unsafe content, or application-policy failures must not be
  disguised as provider availability failures;
- session corruption is an agent-runtime failure and must not trigger a model
  provider retry loop;
- routes failing capability or startup probes remain disabled;
- cooldowns prevent repeated requests from hammering a known-failed provider.

---

## 7. Configuration and state

### Committed, reproducible state

- pinned LiteLLM image version and immutable digest;
- gateway YAML without secret values;
- logical model groups and route order;
- timeouts, retry, cooldown, and health policy;
- OpenClaw custom-provider definition pointing to the gateway;
- automated contract and structural tests.

### Durable runtime state

The first local slice does not require the LiteLLM administrative database or
dashboard. Avoiding that state keeps the gateway reconstructable from config.
Persistent usage/cost storage is added in the cost-observability phase with an
explicit schema and backup policy.

OpenClaw identity and session state remain in the existing Agent A volumes.

### Prohibited state

- provider keys in YAML, Dockerfiles, images, logs, or Git;
- critical configuration edited only inside a running container;
- unpinned `latest` images;
- provider fallback lists in domain Python;
- enabled routes that have never passed capability acceptance.

### COS Agent A bounded-search adapter

COS Agent A retains OpenClaw's native agent loop and decides when to search,
what query to submit, whether another search is useful, and how to synthesize
the evidence. MinimoI does not pre-classify or mediate ordinary search intent.

OpenClaw 2026.7.1's bundled xAI `web_search` provider sends configured base URLs
through its public-endpoint SSRF guard. That guard correctly rejects the private
Docker address used by LiteLLM, and the bundled provider exposes no supported
configuration switch to its self-hosted path. Provider keys must not be copied
into Agent A and LiteLLM must not be exposed publicly merely to bypass that
guard.

The approved solution is a small, image-bundled OpenClaw web-search provider:
`docker/cos-agent-a/plugins/cos-bounded-search/`. It is an adapter, not a second
agent or an orchestration layer. It uses OpenClaw's documented plugin SDK to
register the standard `web_search` capability and sends requests only to the
fixed internal LiteLLM Responses endpoint. The model cannot provide or alter
the destination URL. Agent A receives only `MINIMOI_MODEL_GATEWAY_KEY`; xAI and
other provider keys remain exclusive to LiteLLM.

The adapter calls the stable logical route `minimoi-cos-web-search`. Concrete
provider and model selection remains in LiteLLM YAML, so a provider swap does
not require an OpenClaw or COS code change.

Quality and safety bounds for the first accepted slice are:

- public-search query of at most 500 characters;
- 60-second adapter timeout;
- up to five provider reasoning turns;
- up to 20 materially relevant citation URLs;
- at most 12,000 returned answer characters;
- explicit preference for authoritative, primary, current, and directly
  relevant sources;
- no padding to reach 20 sources and no minimum citation quota;
- retrieved content marked as untrusted evidence, never instructions;
- `web_fetch`, browser, X search, filesystem, runtime, messaging, and subagent
  tools remain denied.

These limits can reduce depth for unusually complex research, but they do not
filter topics, select an allowlist of websites, or rewrite the user's query.
Deep page inspection remains out of scope while `web_fetch` is denied. Search
costs are emitted through the same sanitized receipt ledger as model calls;
prompts and responses are not retained.

This adapter intentionally introduces a narrow OpenClaw-version coupling. The
coupling is accepted to preserve OpenClaw as the agent rather than moving
search intent into COS Python. Every OpenClaw upgrade must therefore pass all
of the following on development before production is considered:

1. image rebuild against the proposed pinned OpenClaw version;
2. `openclaw config validate`;
3. `openclaw plugins inspect cos-bounded-search --runtime --json`, proving the
   plugin is loaded and owns only the `minimoi` web-search provider;
4. focused structural and credential-boundary tests;
5. a COS Confer search returning real citation URLs through
   `minimoi-cos-web-search`;
6. verification that prohibited tools remain absent and that a sanitized xAI
   search-cost receipt is recorded.

If the adapter fails after an OpenClaw upgrade, keep the prior pinned image or
disable bounded search. Do not expose LiteLLM publicly, distribute provider
keys to Agent A, enable generic runtime/network tools, or silently bypass the
acceptance gate.

---

## 8. Health and observability

The module distinguishes:

1. **Liveness:** gateway process accepts requests.
2. **Readiness:** configuration loaded and authentication boundary works.
3. **Capability:** each enabled route completes its required synthetic probe.
4. **End-to-end:** the consuming domain completes an actual turn.

Health checks must not call paid models continuously. Capability probes run at
setup, after configuration/version changes, and during explicit acceptance.

Logs must include a correlation ID, logical model, selected provider/model,
fallback reason/position, latency, status, and token/cost fields when available.
They must not include credentials or unnecessary personal prompt content.

The gateway must eventually emit a routing receipt consumable by COS so the UI
and cost system report the actual serving provider. Static primary-model labels
are prohibited. The first integration may expose this as structured gateway
telemetry rather than embedding it in the assistant reply, but production
acceptance requires correlation-safe per-turn reporting.

---

## 9. Cost reporting and COS checkpoint

Cost observability is the next gateway phase after stable routing. It will
capture:

- input, output, cache, and reasoning tokens where providers expose them;
- provider-reported or registry-derived request cost;
- logical model, domain, task class, and serving route;
- fallback attempts and failed-attempt cost;
- Ollama latency and request count (zero marginal API cost is not zero
  infrastructure cost);
- daily, weekly, and monthly totals and comparison to configurable thresholds.

COS will receive a scheduled cost checkpoint that summarizes spend, anomalies,
fallback waste, and idle infrastructure. COS may recommend changes but must not
alter budgets, routing, or infrastructure without Robert's approval.

---

## 10. Implementation phases

### Phase G1 — Shared local gateway foundation

- add pinned LiteLLM service and YAML;
- add gateway credential template and Docker isolation;
- configure logical routes;
- add liveness/readiness and structural tests;
- prove direct xAI, Anthropic, and Ollama tool calls through the gateway.

### Phase G2 — COS Agent A migration

- define LiteLLM as an OpenClaw custom provider;
- remove provider credentials and fallback ownership from Agent A;
- remove the provisional COS Python routing modules;
- preserve Agent A identity, tools, session, notes, and `/new` behavior;
- prove primary, forced cloud fallback, and Ollama fallback end to end;
- preserve a documented direct-provider rollback.

### Phase G3 — Cost and routing receipts

- persist sanitized request/usage receipts;
- expose correlation-safe serving metadata;
- integrate the existing cost-reporting surface;
- implement the COS cost checkpoint and thresholds.

### Phase G4 — Reusable domain rollout

For each domain:

1. inventory current model calls and required capabilities;
2. define or reuse a logical model group;
3. run domain-specific quality and tool probes;
4. migrate behind a feature flag;
5. compare quality, latency, and cost;
6. remove copied provider logic only after acceptance;
7. preserve and test rollback.

### Phase G5 — Production and documentation

- capacity-test LiteLLM on the production `t3.small`;
- define SSM parameters and automated secret restoration;
- add production health, backup, rollout, and rollback;
- update Architecture, Operations, roadmap, cost, deployment, restore, and
  domain-standard documentation from verified implementation evidence.

---

## 11. Acceptance criteria for the current build

The local G1/G2 build is accepted only when:

- the LiteLLM image is pinned and configuration validates;
- no secret appears in Git or generated config output;
- the gateway is inaccessible from non-loopback host interfaces;
- Agent A possesses only the gateway credential, not provider keys;
- COS and OpenClaw request one stable logical model;
- xAI primary returns successfully;
- controlled xAI failure reaches Anthropic;
- the selected Ollama model accepts and performs an actual tool call;
- OpenClaw identity and session continuity survive gateway/provider changes;
- explicit notes remain platform-owned and `/new` remains runtime-owned;
- failure messages remain safe and provider credentials never enter responses;
- automated COS and gateway tests pass;
- normal credentials and primary routing are restored after failure probes;
- the actual diff is reviewed before commit, push, or deployment.

If truthful per-turn serving metadata cannot be correlated safely through the
gateway/OpenClaw boundary, G2 stops and G3 becomes a prerequisite rather than
shipping another static label.

### Local implementation evidence — 2026-08-15

- LiteLLM is pinned by immutable image digest and listens only on loopback.
- COS Agent A receives the independent gateway credential and no provider API
  credentials; only LiteLLM receives xAI and Anthropic credentials.
- The accepted route order is xAI Grok 4, xAI Grok 4.1 Fast, Anthropic Claude
  Sonnet 4.6, then development-only Ollama Qwen 3 4B.
- Direct gateway probes passed for both cloud providers and Qwen's required
  tool call. Controlled cloud failures also reached Qwen through the complete
  COS -> OpenClaw -> LiteLLM path.
- Qwen 3 1.7B and Llama 3.2 3B remain excluded based on the full-path failures
  recorded in Section 6; direct tool support alone was not treated as proof.
- Colima was raised from 2 GiB to 3 GiB after LiteLLM's exit-137 failures were
  confirmed as memory pressure. Subsequent sequential Agent A and COS turns
  completed without container restarts.
- Authenticated, allowlisted routing receipts now correlate each COS turn with
  its actual provider, model, fallback position, latency, tokens, and available
  provider cost without storing prompts or responses.
- Primary xAI and forced Anthropic fallback receipts passed live checks; normal
  credentials and primary routing were restored afterward.
- Agent identity, conversation continuity, explicit platform note saving, and
  `/new` behavior passed after migration.
- The focused COS and gateway suite passes 60 tests.

The aggregate cost-report integration, scheduled COS cost checkpoint, G4
domain migrations, production work, and protected architecture/operations
updates remain unimplemented. They require their own verification and review;
this local build does not claim them.

---

## 12. Rollback

Rollback does not delete Agent A state:

1. restore the reviewed direct-provider OpenClaw configuration;
2. supply the previously accepted provider credentials to Agent A;
3. set Agent A's primary model to the last accepted direct provider;
4. restart only Agent A;
5. verify identity, session, `/new`, and one COS turn;
6. stop the LiteLLM service after verification.

The legacy direct Grok COS backend remains a separate application-level
rollback until Robert explicitly retires it.

---

## 13. References

- [LiteLLM gateway documentation](https://docs.litellm.ai/)
- [LiteLLM releases](https://github.com/BerriAI/litellm/releases)
- [Ollama tool-calling documentation](https://docs.ollama.com/capabilities/tool-calling)
- [OpenClaw OpenAI-compatible Gateway API](https://docs.openclaw.ai/gateway/openai-http-api)
