# Model tiers and switching (#458)

MiniMoi's model gateway offers three providers at three tiers. Every agent that calls the gateway asks for a **role**
(`minimoi-cos-agent`, …), and each role takes its model from one **tier**. To switch a role between models, change one
line and restart the gateway.

## The tiers

| Tier | Anthropic | OpenAI | xAI |
|---|---|---|---|
| Strong (effort `high`) | `minimoi-anthropic-strong`: Opus 5.5, $4 / $20 | `minimoi-openai-strong`: GPT-6 Astra, $10 / $50 | `minimoi-xai-strong`: Grok 4.7, $2 / $6 |
| Mid (effort `medium`) | `minimoi-anthropic-mid`: Sonnet 5.5, $2 / $10 | `minimoi-openai-mid`: GPT-6.1 Sol, $2 / $10 | `minimoi-xai-mid`: Grok 4.6, $2 / $6 |
| Low (effort `low`) | `minimoi-anthropic-low`: Haiku 5.5, $0.10 / $0.50 | `minimoi-openai-low`: GPT-6 Luna, $0.10 / $0.50 | `minimoi-xai-low`: Grok 4.3, $1.25 / $2.50 |

Prices are input / output per million tokens, from LiteLLM 1.106.6's cost map (31 Oct 2026). The tiers are role
equivalents in our routing, not claims of matched performance. Every tier sets its effort explicitly, because Opus 5.5
would otherwise default to medium.

Each tier is also a route that can be called by name, for example to measure a model before switching a role to it.

## The roles and where they point now

| Role | Caller | Tier | Own settings |
|---|---|---|---|
| `minimoi-cos-agent` | CoS (Agent A) | `xai-low` | fallback position 0 |
| `minimoi-cos-agent-xai-fast` | CoS's first fallback | `xai-low` | effort `none` |
| `minimoi-cos-agent-anthropic` | CoS's second fallback | `anthropic-mid` | |
| `minimoi-cos-web-search` | CoS's bounded search (`/v1/responses`) | `xai-low` | |
| `minimoi-mc-agent` (staging only) | MC's provider route | `xai-low` | |
| `minimoi-voice-summary`, `minimoi-voice-intent` (staging host, voice line) | voice | `xai-low` | effort `none` |

**Not covered by the gateway:**
- **Master Craftsman's own model:** set in its OpenClaw config (`docker/mc-agent-container/openclaw.json`), shipped by
  an MC release.
- **Services that call providers directly:** Curator, German, Portuguese, the Guild loops and Records still name their
  models in code. Moving them onto these roles is tracked on #458.

## How to switch a role

1. **Edit the role's merge line** in the environment's config:
   - `services/model_gateway/litellm.staging.yaml` for dev; the AWS staging host's copy is
     `/opt/minimoi-staging/config/litellm.staging.yaml`;
   - `services/model_gateway/litellm.prod.yaml` for production.

   ```yaml
   - model_name: minimoi-cos-agent
     litellm_params: {<<: *anthropic-strong, timeout: 120}   # was *xai-low
   ```

   Keep the role's own keys (its timeout, and its effort where it differs from the tier). The tests check that every
   role reads from a tier and that every route and fallback serves one of the nine models.
2. **Dev:** restart the gateway with `bin/compose.sh up -d --no-build --force-recreate model-gateway` on the host, then
   verify (below).
3. **Production:** the gateway image bakes `litellm.prod.yaml`. A switch is a PR, referenced to the tracking ticket. On
   Robert's word it merges, and `deploy.yml` rebuilds and redeploys only `model-gateway`.

## How to verify a switch

- **The prompt arrives:** send a system message holding a code word, then ask for the code word. Grok 4.7 sometimes
  refuses this as a secret-extraction attempt even when the prompt arrives, so for xAI-strong use a persona check
  instead: "You are Tamsin, …", then "Who are you?".
- **The role answers in persona:** send one real CoS turn.
- **Cost per turn:** read the receipt (`model_gateway_receipts.jsonl` in production, the usage store on staging). It
  records `served_model`, the token counts and `cost_usd`.

## Traps checked on LiteLLM 1.106.6 (staging, 31 Oct 2026)

| Trap | Result |
|---|---|
| GPT-6 and `max_tokens` | 1.93.1 sent `max_tokens`, so OpenAI returned 400. Fixed from 1.98.1 on, which is why the gateway moved to 1.106.6. |
| Opus 5.5 and Sonnet 5.5 with `tool_choice: required` | 1.93.1 passed it through, so Anthropic returned 400. 1.106.6 adapts it, and it now returns 200. |
| Effort `none` on a role switched to Anthropic or OpenAI | Returns 200: LiteLLM drops it or maps it. |
| `thinking: {type: disabled}` | No caller in the repo sends it. |
| Streaming with tools, and usage on the final chunk | Works on all three providers. |
| `mode: responses` on any route | Forbidden: it dropped the system prompt on every Grok route (#454). A test checks for it. |
