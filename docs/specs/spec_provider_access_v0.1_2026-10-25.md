# Spec — Provider access: keys, plans, caps and loud failure, v0.1

**25 Oct 2026 · Claude Code for Robert · draft for review (Codex), then
Robert. Dev first; prod follows the same design after the dev proof.**

Trigger: the Anthropic balance drained to zero on 24 Oct. Native OpenClaw
fell back silently from the plan token to a metered key (log, 24 Oct 10:10
Chicago: *"anthropic (claude-fable-5-1) returned a billing error — your API
key has run out of credits"*). The read-only inventory below then showed every
provider key loaded into nearly every dev container.

## 1. Principles

1. **Plans stay in the vendor's own product.** Claude plans cover Claude Code
   and Claude.ai. ChatGPT plans cover Codex and ChatGPT. Grok plans cover
   Grok's own apps. A plan token is never used inside OpenClaw, the gateway
   or a MiniMoi service. (For Claude this is Anthropic's reported terms since
   Feb 2026; verify each vendor's current terms, §6.)
2. **Only the model gateway holds MiniMoi's provider keys.** Agents and
   services get **capped virtual keys**: one per agent or service, per
   provider route, per environment.
3. **No silent fallback across billing modes.** A failing plan, cap or key
   errors out loudly. It never quietly continues on another, metered,
   credential.
4. **A hard limit on every provider key at the provider** (backstop), and a
   separate key per scope: MiniMoi dev, MiniMoi prod, personal OpenClaw.
5. **Seen, not assumed:** daily spend per virtual key from gateway receipts,
   with alerts at 50%, 80% and 100% of each cap.
6. **Enforced in code:** a service other than the gateway that holds a provider
   key fails tests and start-up checks.

## 2. Current state (dev Mac, 25 Oct, read-only fingerprints)

| Provider | Key | Where |
|---|---|---|
| Anthropic | `8a9a35a8` | portal, Curator, German, Portuguese, cos-scheduler and Postgres containers; repo `.env`; staging `.env`; Keychain |
| Anthropic | `971af42b` | native OpenClaw (`~/.openclaw/auth-profiles.json`): personal `main`, `mc-chat`, `mc-agent` |
| Anthropic | `07f33f7c` | MC's own key, `mc.env`, inside the gateway only (correct pattern) |
| xAI | `8463f056` | **one key everywhere**: the same six containers, both `.env` files, Keychain, personal OpenClaw `main` |
| OpenAI | `6aac0a80` | the same six containers, both `.env` files, Keychain |

Root cause: the dev stack loads the whole `.env` into every service. Postgres
has no use for any of these keys. Prod (`/opt/minimoi/.env` plus SSM) was not
inspected and needs the same audit.

## 3. Per provider

### Anthropic (Claude)
- **Plan (Claude):** Claude Code. That covers Robert's own sessions and,
  subject to the §6 check, the headless `claude-code` worker MC delegates to.
  Heavy build and analysis work goes here.
- **API keys, three in total:**
  - `minimoi-dev`, gateway only, capped;
  - `minimoi-prod`, gateway only, capped;
  - `personal-openclaw`, its own Console workspace with a monthly limit.
- **Gateway routes:** `minimoi-mc-agent`, `minimoi-cos-agent-anthropic`, plus
  any services that need Claude, each through its own virtual key.
- **Native OpenClaw:** `auth.order.anthropic` holds one profile only, which
  removes the silent fallback (staged: `scripts/ops/openclaw_auth_order.py`).
  The plan token inside OpenClaw is Robert's decision and terms risk (§6). The
  recommended end state is the `personal-openclaw` key.
- **Provider backstop:** a monthly spend limit per Console workspace;
  auto-reload off until §5 is complete.

### xAI (Grok)
- **Plan:** SuperGrok / X Premium cover xAI's own apps. To my knowledge they
  don't cover the API; verify (§6). Everything MiniMoi does on Grok is metered
  API.
- **API keys, replacing the single shared key `8463f056`:**
  - `minimoi-dev`, gateway only;
  - `minimoi-prod`, gateway only;
  - `personal-openclaw`, separate.
- **Gateway routes:** the CoS routes and `minimoi-mc-agent` already route
  through it. **Curator's Grok scoring** (prod's largest steady spend, about
  $0.31 per run) and the language domains move from direct key use to virtual
  keys.
- **Provider backstop:** prepaid credits with auto top-up off, plus xAI's team
  or key spending limits where the console offers them (verify, §6).

### OpenAI
- **Plan (ChatGPT):** Codex (Robert's sessions, and the Codex connection
  worker), subject to the §6 check for unattended use.
- **API keys, replacing `6aac0a80`:**
  - `minimoi-dev`;
  - `minimoi-prod`;
  - optional dedicated keys for realtime/voice, if those calls can't go
    through the gateway.
- **Who uses it today:** to be mapped by searching the code (§5, step 3).
  Likely voice or realtime and possibly embeddings. Anything LiteLLM can't
  proxy (for example realtime audio sessions) gets its **own OpenAI project
  key with a project budget**, held by a single named service, and listed as
  an explicit exception.
- **Provider backstop:** project-level budgets and limits in the OpenAI
  dashboard, and usage alerts.

## 4. Enforcement

- **Compose:** no service loads the whole `.env`. Each service gets an
  explicit environment allowlist. Provider keys appear only in the gateway's
  and in the named exceptions' environment.
- **Tests:** a repo test fails if any service other than the gateway or a
  listed exception references `*_API_KEY` for a provider, and if any agent
  config names a model provider other than the gateway.
- **Start-up checks:** agent containers (MC first) refuse to start with a
  provider key or a non-gateway provider in their config.
- **Audit job:** the inventory script used for §2, run nightly in the batch
  window. It reports fingerprints only, alerts on any new location, and never
  prints a value.
- **Spend:** gateway receipts become a daily per-key spend report, with
  50/80/100% alerts to Telegram.

## 5. Migration (dev first, each step reversible)

1. **Robert, at the provider consoles:** new keys per §3; limits; auto-reload
   off. (Credential handoff: Claude Code names the storage command, Robert
   types the value.)
2. **Native OpenClaw: one Anthropic profile, no fallback.** Applied when
   Robert is done with MC. It's a hot reload, with backup and rollback.
3. **Map provider use in code:** which service calls which provider, and
   whether LiteLLM can proxy it.
4. **Gateway:** load the new provider keys. Each service and agent gets its
   own virtual key and cap; service by service, its provider keys are removed
   from its environment and it's checked.
5. **Personal OpenClaw:** its own Anthropic and xAI keys with limits; the old
   shared keys come out of `~/.openclaw`.
6. **Revoke** the old shared keys (`8a9a35a8`, `971af42b`, `8463f056`,
   `6aac0a80`) once nothing uses them.
7. **Prod:** the same audit, the same steps, through a release.

## 6. To verify (primary sources, not blogs)

- Anthropic's current terms on plan tokens in third-party tools, and whether
  an unattended headless Claude Code worker is within the plan's terms.
- Whether OpenAI's ChatGPT plan permits Codex running unattended as a worker.
- Whether xAI plans include any API usage, and which spending-limit controls
  xAI's console offers.

## 7. Decisions for Robert

- **P1:** the personal OpenClaw end state: its own capped API key
  (recommended), or the plan token at his own terms risk.
- **P2:** caps per virtual key. Proposed monthly starting points: MC $25,
  CoS $25, Curator $15, languages $5 each.
- **P3:** whether an OpenAI exception is needed (after step 3).
