# Spec #146: OpenClaw CoS Gateway — Installation, Configuration & Acceptance

**File:** `spec_146_openclaw_cos_gateway_2026-08-09.md`
**Status:** Draft v2 — revised per Codex review 2026-08-09; pending second review and Robert approval
**Date:** 2026-08-09
**Build queue:** #146
**Author:** OpenClaw (Mini-moi) — design session with Robert
**Reviews:** Codex v1 review incorporated 2026-08-09; second review pending
**Owner / decision point:** Robert
**Governing spec:** Spec #133 v1.3 §Phase 1; Architecture Principle #7 (no vendor lock-in)
**Supersedes:** Guild #135 (stale — retired `_collect_response` protocol, incorrect npm assumptions)
**Related:** Spec #145 §Stage 3 (voice Confer depends on this spec passing acceptance first); `config/cos_interface.md` v0.2

---

## OpenClaw's role in this spec

OpenClaw is the primary build agent for standalone Gateway provisioning and
acceptance evidence only. It must not edit mini-moi application code, implement
the later adapter, merge to main, deploy, or change production without Robert's
reviewed-diff approval.

---

## Purpose

Define the installation, configuration, model chain, acceptance tests, and
version management for **OpenClaw #2** — a dedicated, CoS-scoped Gateway
instance that is the swappable agent backend behind `call_backend() -> str`
inside `chief_of_staff.py`.

OpenClaw #2 does not replace `chief_of_staff.py`. That coordination layer
(routing, memory, scope enforcement, Telegram) stays platform-owned. Grok
remains the default backend until OpenClaw #2 passes full acceptance and Robert
explicitly changes the env var.

---

## Scope boundary

**In scope:** OpenClaw #2 installation, model chain, auth, acceptance tests,
version management, and the endpoint/token contract mini-moi will eventually use.

**Out of scope:** changes to `chief_of_staff.py` or `openclaw_backend.py`; mini-moi
integration wiring (a separate thin implementation after this spec passes
acceptance); Spec #145 voice layer; OpenClaw #1 (untouched).

---

## Architecture decision: separate profile instance

OpenClaw #2 runs as a **separate Gateway process under its own profile** on dev
and as the only OpenClaw instance on the production host. Rationale:

- Dev mirrors prod topology exactly — what passes on dev applies unchanged to prod.
- Version upgrades are isolated: the CoS Gateway can be upgraded and tested
  without touching OpenClaw #1 or the personal assistant session.
- Setup complexity is resolved on dev before prod is touched.
- Spec #133 specifies "a dedicated, CoS-scoped Gateway instance"; this is that.

**Profile name:** `cos`
**Dev port:** `18790` (one above OpenClaw #1's 18789)
**Prod port:** `18789` (only OpenClaw instance on that host)

---

## Delivery sequence

```
1. Standalone dev installation + acceptance (this spec, Parts 1–3)
2. Thin mini-moi adapter implementation and dev conformance test (separate spec)
3. Standalone prod installation + acceptance (this spec, Part 4)
4. Adapter deployed disabled; Robert explicitly enables it after reviewing evidence
```

Production Gateway installation begins only after dev acceptance passes and the
adapter integration is proven in dev. Production is not touched before that.

---

## Pinned runtime

Both dev and prod must run the **same exact reviewed version**.

| Component | Pinned version |
|---|---|
| OpenClaw | `2026.7.1` (current installed; do not use `@latest`) |
| Node.js | `>=22.22.3 <23` or `>=24.15.0 <25` or `>=25.9.0` (installed: v24.18.0 ✓) |

To install the pinned version: `npm install -g openclaw@2026.7.1`

When a newer OpenClaw release is evaluated, run the full upgrade procedure
(Part 5) on dev first. Prod upgrade follows only after dev passes.

---

## Confirmed model routing (reviewed 2026-08-17)

OpenClaw now selects the stable logical model
`minimoi-gateway/minimoi-cos-agent`. The shared LiteLLM gateway owns the
provider sequence below, so an OpenClaw upgrade or model swap does not require
rewriting the COS adapter. Do not rely on retired aliases to redirect silently
at the provider boundary.

| Tier | Provider/model ID | Purpose |
|---|---|---|
| Primary | `xai/grok-4.3` with low reasoning | Main advisory reasoning |
| Fast fallback | `xai/grok-4.3` with reasoning disabled | Lower-latency path using the current model |
| Secondary | `anthropic/claude-sonnet-4-6` | Second independent provider |
| Tertiary | `openai/gpt-5.5` | Third independent provider |
| Local | `ollama/gemma3:1b` | No-internet fallback; availability only, not full reasoning |

**At setup time:** re-enumerate the live catalog on the target host with
`openclaw --profile cos capability model list` and verify these IDs are still
present before configuring the chain. Record the actual IDs used in the
acceptance log. Grok 4.1 Fast was retired on 2026-05-15; do not restore that
alias to the chain even if a provider still redirects it.

---

## Part 1 — Dev installation (MacBook)

### Prerequisites

- OpenClaw `2026.7.1` installed globally (verify: `openclaw --version`)
- Node `v24.x` on PATH (verify: `node --version`)
- Ollama running: `ollama serve` (verify: `ollama list` shows `gemma3:1b`)
- Port `18790` free on loopback
- API keys for xAI, Anthropic, OpenAI in macOS Keychain or shell environment

### Step 1 — Interactive configuration

```bash
openclaw --profile cos configure
```

Respond to prompts:
- Agent name: `Mini-moi CoS`
- Gateway port: `18790`
- Gateway bind mode: `loopback`
- Auth mode: `token` — supply a strong random token and record it as
  `OPENCLAW_COS_GATEWAY_TOKEN` in a local `.env.cos` file (not committed)

API keys: enter when prompted, or configure as env references (preferred):

```bash
openclaw --profile cos config set providers.xai.apiKey \
  --ref-provider xai --ref-source env --ref-id XAI_API_KEY

openclaw --profile cos config set providers.anthropic.apiKey \
  --ref-provider anthropic --ref-source env --ref-id ANTHROPIC_API_KEY

openclaw --profile cos config set providers.openai.apiKey \
  --ref-provider openai --ref-source env --ref-id OPENAI_API_KEY
```

This binds each key to the environment variable rather than writing plaintext
into `~/.openclaw-cos/openclaw.json`.

### Step 2 — Model chain configuration

First, confirm the model IDs are present in the CoS profile catalog:

```bash
openclaw --profile cos capability model list
```

Then configure OpenClaw with the platform-owned logical model. The provider
fallback chain is configured in LiteLLM, not duplicated here:

```bash
# Write a patch file — do not use positional JSON
cat > /tmp/cos-model-patch.json5 << 'EOF'
{
  agents: {
    defaults: {
      model: {
        primary: "minimoi-gateway/minimoi-cos-agent",
        fallbacks: []
      }
    }
  }
}
EOF

openclaw --profile cos config patch --file /tmp/cos-model-patch.json5 --dry-run
# Review dry-run output; if correct:
openclaw --profile cos config patch --file /tmp/cos-model-patch.json5
openclaw --profile cos config validate
```

### Step 3 — Install and start the macOS service

```bash
# Back up config before installing as service
cp ~/.openclaw-cos/openclaw.json ~/.openclaw-cos/openclaw.json.backup-$(date +%Y%m%d)

openclaw --profile cos gateway install \
  --port 18790 \
  --token "$OPENCLAW_COS_GATEWAY_TOKEN"

openclaw --profile cos gateway start
openclaw --profile cos gateway status
```

Record the launchd plist name from the install output (e.g.,
`ai.openclaw.gateway.cos`) — needed for manual service management and T-010.

---

## Part 2 — Acceptance tests (dev)

All tests run against the dev profile. Tests T-001 to T-007 are smoke tests
re-run after any configuration change or upgrade. T-008 to T-012 run at initial
acceptance and after major version changes. **All 12 must pass before prod
installation begins.**

### Fallback testing method

Overriding environment variables on a CLI process does not affect credentials
in an already-running launchd Gateway. Fallback tests (T-005 to T-007) use a
**temporary foreground Gateway** with the target provider's key deliberately
absent:

```bash
# Stop the service temporarily
openclaw --profile cos gateway stop

# Run foreground with one provider missing — observe fallback behaviour
XAI_API_KEY="" openclaw --profile cos gateway run --port 18790 &
GW_PID=$!
# … run the test turn …
kill $GW_PID

# Restore normal service
openclaw --profile cos gateway start
```

After every fallback test, restore the service and confirm T-004 passes
before proceeding.

---

### T-001 Gateway health

```bash
openclaw --profile cos gateway health --json
```

**Pass:** `status: "ok"`, version `2026.7.1`, uptime > 0.
**Fail:** connection refused, error, or status ≠ ok.

### T-002 Unauthenticated connection rejected

```bash
openclaw --profile cos gateway call health \
  --url ws://127.0.0.1:18790 \
  --token "deliberately-wrong-token"
```

**Pass:** rejected with auth error (FORBIDDEN or 401 equivalent).
**Fail:** responds successfully with a wrong token.

### T-003 Scoped token accepted

```bash
openclaw --profile cos gateway call health \
  --url ws://127.0.0.1:18790 \
  --token "$OPENCLAW_COS_GATEWAY_TOKEN"
```

**Pass:** returns healthy response.
**Fail:** valid token rejected.

### T-004 Primary model (Grok) responds

```bash
openclaw --profile cos agent \
  --message "Reply with exactly the text: OPENCLAW_COS_OK" \
  --json
```

**Pass:** response contains `OPENCLAW_COS_OK`; logs show `xai/grok-4` was used.
**Fail:** no response, error, or different model in logs.

### T-005 Claude fallback responds

Using the foreground method above, start the Gateway with `XAI_API_KEY=""`:

```bash
openclaw --profile cos agent \
  --message "Reply with exactly the text: CLAUDE_FALLBACK_OK" \
  --json
```

**Pass:** response contains `CLAUDE_FALLBACK_OK`; logs show
`anthropic/claude-sonnet-4-6`.
**Fail:** errors rather than falling back.
**Restore:** restart service; confirm T-004 passes.

### T-006 OpenAI fallback responds

Foreground Gateway with `XAI_API_KEY="" ANTHROPIC_API_KEY=""`:

```bash
openclaw --profile cos agent \
  --message "Reply with exactly the text: OPENAI_FALLBACK_OK" \
  --json
```

**Pass:** response contains `OPENAI_FALLBACK_OK`; logs show `openai/gpt-5.5`.
**Fail:** errors before reaching OpenAI.
**Restore:** restart service; confirm T-004 passes.

### T-007 Ollama local fallback responds

Foreground Gateway with all three cloud keys absent:

```bash
XAI_API_KEY="" ANTHROPIC_API_KEY="" OPENAI_API_KEY="" \
  openclaw --profile cos agent \
  --message "Reply with any text." \
  --json
```

**Pass:** response received; logs show `ollama/gemma3:1b`; no outbound
provider request attempted.
**Fail:** errors rather than routing to Ollama; or Ollama not running.
**Restore:** restart service; confirm T-004 passes. Confirm `ollama serve`
is running.

### T-008 Admin scope rejected

```bash
openclaw --profile cos gateway call config.patch \
  --url ws://127.0.0.1:18790 \
  --token "$OPENCLAW_COS_GATEWAY_TOKEN" \
  --params '{"test": true}'
```

**Pass:** rejected with FORBIDDEN / MISSING_SCOPE.
**Fail:** `config.patch` succeeds — the token is over-privileged.

### T-009 Session isolation from OpenClaw #1

```bash
# Sessions on #1 (port 18789)
openclaw gateway call sessions.list --json

# Sessions on #2 (port 18790)
openclaw --profile cos gateway call sessions.list \
  --url ws://127.0.0.1:18790 \
  --token "$OPENCLAW_COS_GATEWAY_TOKEN" \
  --json
```

**Pass:** lists are independent; no session appears in both.
**Fail:** any session bleeds across profiles.

### T-010 Restart recovery

```bash
openclaw --profile cos gateway restart
sleep 10
openclaw --profile cos gateway health --json
```

Then re-run T-004.
**Pass:** health returns ok within 10 seconds; T-004 passes after restart.
**Fail:** recovery > 30 seconds or T-004 fails after restart.

### T-011 No credentials in logs

After running T-004 through T-007, inspect Gateway logs:

```bash
openclaw --profile cos gateway stability --json
# Also inspect log files under ~/.openclaw-cos/ (confirm path from install output)
```

**Pass:** no API key values or token values appear in any log line, even partially.
**Fail:** any credential substring found in logs.

### T-012 Full acceptance sign-off

All T-001 to T-011 pass. Robert reviews the test run output and approves.
Record pass evidence (command output or log excerpts) in the activity log.

**Dev acceptance gates prod installation. Prod installation gates the adapter spec.**

---

## Part 3 — Auth and token contract

The token setup path on the installed runtime uses `gateway install --token`
(confirmed from CLI help). The token supplied at install becomes the shared
Gateway secret.

**Pending verification during dev setup:**
- Confirm whether `openclaw devices` provides a mechanism to issue a
  *scoped* role token (operator.read + operator.write only) distinct from
  the full Gateway token, or whether the Gateway token is the only credential.
- If scoped tokens are available, use the minimum-scope token for mini-moi's
  client connection.
- If only the full Gateway token is available in this runtime version, document
  that explicitly and accept it as the Phase 1 credential with a note to
  revisit when scoped tokens are confirmed.

The exact connection handshake (`connect` frame, auth field) is documented in
the Gateway protocol reference at `https://docs.openclaw.ai/gateway/protocol`.
The mini-moi client must implement the full challenge-response handshake, not
assume the token can be passed as a raw HTTP header.

---

## Part 4 — Prod installation (AWS EC2)

**Begins only after Part 2 (dev acceptance) is complete and the thin adapter
spec is proven in dev.**

### Host and network

Current assumption: same EC2 host as mini-moi (loopback for the standalone
acceptance phase). The mini-moi-to-OpenClaw network path when CoS runs inside
Docker is **not resolved in this spec** — that is a binding decision for the
thin integration spec. Options include:

- Docker `--network host` (simple; reduces isolation)
- Docker bridge with host route (`host.docker.internal` on some runtimes)
- Unix-socket proxy between host and container
- Containerised OpenClaw sidecar in the same compose stack

Do not loosen Gateway bind mode to LAN as a shortcut. The integration spec
must choose and document the correct secure path before any container connects.

### Steps

```bash
# On the EC2 host
npm install -g openclaw@2026.7.1    # exact pinned version
openclaw configure                   # no --profile; only instance on this host
```

Configure API keys via environment references (same `config set --ref-source env`
pattern as dev). Do not write plaintext keys to `openclaw.json`.

If a plaintext env file is used as a transitional measure (mode 600, owned by
service user, not in git), document it as honest plaintext with a rotation plan.
Prefer the runtime's env-ref SecretRef mechanism confirmed in dev.

```bash
# Identify a dedicated service user (not root)
# State directory: /home/<service-user>/.openclaw/
# openclaw.json: mode 600, owned by <service-user>

openclaw gateway install \
  --port 18789 \
  --token "$OPENCLAW_COS_GATEWAY_TOKEN_PROD"

sudo systemctl enable openclaw-gateway
sudo systemctl start openclaw-gateway
```

Run T-001, T-003, T-004, T-007, T-010, T-011 against prod before declaring
acceptance. These six cover: health, auth, primary model, local fallback,
restart, and credential safety. Full T-001 to T-012 on any major version change.

---

## Part 5 — Version upgrade procedure

### 5.1 Pre-upgrade (mandatory)

```bash
# Back up state before every upgrade — not optional
cp ~/.openclaw-cos/openclaw.json \
   ~/.openclaw-cos/openclaw.json.backup-$(date +%Y%m%d-%H%M)

# Record the exact current version
openclaw --version > ~/.openclaw-cos/version-before-$(date +%Y%m%d).txt
```

### 5.2 Dev upgrade

```bash
npm install -g openclaw@<exact-new-version>
openclaw --profile cos gateway stop
openclaw --profile cos gateway start
openclaw --profile cos gateway status
```

Run smoke tests T-001, T-004, T-007, T-010 minimum. Full suite on major version
change. Confirm all pass before upgrading prod.

### 5.3 Prod upgrade (after dev passes)

```bash
npm install -g openclaw@<exact-version-proven-on-dev>
sudo systemctl stop openclaw-gateway
sudo systemctl start openclaw-gateway
```

Run T-001, T-004, T-007, T-010 on prod. Record results in activity log.

### 5.4 Rollback

```bash
npm install -g openclaw@<version-from-backup-file>
# Restore config if format changed:
cp ~/.openclaw-cos/openclaw.json.backup-<date> ~/.openclaw-cos/openclaw.json
openclaw --profile cos gateway start   # dev
sudo systemctl start openclaw-gateway  # prod
```

Rollback takes precedence over troubleshooting if production impact is active.
Confirm T-001 and T-004 pass after rollback.

---

## Mini-moi integration contract (boundary reference only — no code here)

| Item | Dev value | Prod value |
|---|---|---|
| Gateway URL | `ws://127.0.0.1:18790` | TBD in integration spec |
| Auth | Shared token — `OPENCLAW_COS_GATEWAY_TOKEN` env var | Separate prod token |
| Min scopes | `operator.read`, `operator.write` | Same |
| Omitted scopes | `operator.admin`, `operator.pairing`, `operator.questions`, `operator.approvals` | Same |
| First integration shape | `call_backend(prompt) -> str` over authenticated WebSocket RPC | Same |
| Frame contract | `{type:"req", id, method, params}` / `{type:"res", id, ok, payload\|error}` | Same |
| Cancellation | `sessions.abort` | Same |
| Credential rule | Token never reaches browser; API keys never reach mini-moi application code | Same |

The prod Gateway URL is not loopback for container-based CoS. It is decided in
the thin integration spec and kept out of this document.

---

## Open questions (to resolve during dev setup, before prod)

1. **Scoped token mechanism:** Does `openclaw devices` in 2026.7.1 issue a
   role-scoped token, or is the Gateway shared token the only credential?
   Resolve during dev Step 1. Update auth contract above with the answer.

2. **Prod service account:** Confirm service user name and home/state directory
   on the EC2 host. Update Part 4 with the exact path before prod installation.

3. **Prod Ollama model:** EC2 may have different RAM/disk constraints than the
   MacBook. Confirm which Ollama model is pulled on prod; update the fallback
   chain if it differs from `gemma3:1b`.

4. **xAI fast-path model:** Resolved 2026-08-17. Both xAI paths use
   `xai/grok-4.3`; the primary requests low reasoning and the fast path disables
   reasoning. The retired Grok 4.1 Fast alias is no longer configured.

5. **Mini-moi prod network path:** How does CoS inside Docker reach an OpenClaw
   Gateway running as a host process? Decided in the thin integration spec.

---

## Relationship to existing specs

| Spec | Relationship |
|---|---|
| #133 v1.3 | This spec delivers the "dedicated, CoS-scoped Gateway instance" from Phase 1. No changes to #133 architecture. |
| #135 | **Superseded.** Update queue status to `superseded`. |
| #145 Stage 3 | Voice Confer via OpenClaw depends on this spec passing prod acceptance first. |
| `config/cos_interface.md` v0.2 | Companion — mini-moi side of the same boundary. |

---

## Activity log

### 2026-08-09 — OpenClaw — v1 drafted

Initial draft created in `_working/openclaw-cos-spec/`. Passed to Codex for review.

### 2026-08-09 — Codex — review returned

Approved architecture direction. Required 14 revisions covering Node version,
pinned install, invalid CLI syntax, fallback test method, token/pairing contract,
prod network contract for Docker topology, mandatory backup, and scope statement.
Full review: `codex_review_spec_146_openclaw_cos_gateway_2026-08-09.md`.

### 2026-08-09 — OpenClaw — v2 revised

All 14 Codex findings incorporated. CLI commands verified against installed
2026.7.1 runtime before rewriting (agent `--message`, `config patch --file`,
`gateway call --url --token --params`, `capability model list`). Model IDs
confirmed from live catalog. Fallback test method changed to temporary foreground
Gateway. Token contract deferred to open question pending dev verification.
Prod Docker network contract deferred to integration spec. Backup made mandatory.
OpenClaw role scope statement added. Length reduced from ~500 lines of prose to 594 total lines including code blocks and tables (Claude Code review confirmed actual count).
Second review and Robert approval required before any installation begins.
