# Master Craftsman's own container (spec_mc_agent_container v0.2, phase 1). Build context: the repository root.
# Same pinned OpenClaw 2026.9.6 index digest as docker/Dockerfile.mc-agent. The same image runs the egress proxy
# (entrypoint override), so there is no second base to trust.
FROM ghcr.io/openclaw/openclaw:2026.9.6@sha256:0a5ff5e682e62afa19149df126aa50063bf65ef885b5c94713ce32dc0eb12e15

# Claude Code, pinned and never self-updating: OpenClaw's claude-cli runtime runs it on Robert's plan. Its sign-in
# state lives only on the dedicated volume mounted at CLAUDE_CONFIG_DIR, never in the image.
ARG CLAUDE_CODE_VERSION=2.1.308
USER root
RUN npm install -g --no-audit --no-fund --include=optional "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && (claude --version || node "$(npm root -g)/@anthropic-ai/claude-code/install.cjs") \
 && claude --version \
 && npm cache clean --force
# ENABLE_TOOL_SEARCH=false: MC's MiniMoi tools (mcp__openclaw__minimoi_*) are always loaded, never deferred behind
# the CLI's tool search. Deferred, a fresh session called them by bare names and got "No such tool available" (27 Oct).
ENV DISABLE_AUTOUPDATER=1 \
    ENABLE_TOOL_SEARCH=false \
    CLAUDE_CONFIG_DIR=/home/node/.claude \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    OPENCLAW_NO_AUTO_UPDATE=1 \
    NODE_USE_ENV_PROXY=1

# Root-owned, read-only to the node user. start.sh verifies the persona against its manifest on every start.
COPY --chown=root:root docker/mc-agent-container/openclaw.json docker/mc-agent-container/start.sh docker/mc-agent-container/egress-proxy.mjs /opt/minimoi/mc-agent/
# The reviewed portal relay (relay v2; byte-identical to the pinned native relay source). Runs as the mc-relay service.
COPY --chown=root:root docker/mc-agent/relay_v2.mjs /opt/minimoi/mc-agent/relay_v2.mjs
COPY --chown=root:root services/mc_operations/personas/mc-chat/releases/0.1.17/ /opt/minimoi/mc-agent/persona/
# Phase 2: the MiniMoi tools plugin (the same reviewed source native MC loads) and its two fixed host helpers, plus the
# fixed TCP forwarder that is the plugin's only way to the portal (via the tools door).
COPY --chown=root:root services/mc_operations/plugins/minimoi-tools/ /opt/minimoi/mc-agent/plugins/minimoi-tools/
COPY --chown=root:root scripts/mc_host/inspect.py scripts/mc_host/artifact.py /opt/minimoi/mc-agent/helpers/
COPY --chown=root:root docker/mc-agent-container/tcp-forward.mjs /opt/minimoi/mc-agent/tcp-forward.mjs
# Bounded web search: MC's copy of CoS's reviewed provider (query only; fixed gateway route).
COPY --chown=root:root docker/mc-agent-container/plugins/mc-bounded-search/ /opt/minimoi/mc-agent/plugins/mc-bounded-search/
# The host-side release bundle (copied out by .github/workflows/deploy-mc-agent.yml, never run inside MC): the EC2
# deploy/switch script, the DB migration script and its migrations, the prod policy, the judgment collector and the
# security/model checks. One immutable artifact per release.
COPY --chown=root:root docker-compose.mc.prod.yml /opt/minimoi/mc-release/
COPY --chown=root:root scripts/operations/deploy_mc_container_ec2.sh scripts/operations/mc_prod_db_migrate.sh \
     scripts/judgment/judgment_store.py scripts/ops/mc_container_security_test.py scripts/ops/mc_container_model_tests.py \
     services/mc_operations/policy/mc-policy.prod.json /opt/minimoi/mc-release/
COPY --chown=root:root minimoi_portal/guild_ui/sql/ /opt/minimoi/mc-release/mc-migrations/minimoi_portal/guild_ui/sql/
COPY --chown=root:root core/minimoi_actions/sql/ /opt/minimoi/mc-release/mc-migrations/core/minimoi_actions/sql/
COPY --chown=root:root core/mc_domain_actions/sql/ /opt/minimoi/mc-release/mc-migrations/core/mc_domain_actions/sql/
COPY --chown=root:root core/mc_tools/sql/ /opt/minimoi/mc-release/mc-migrations/core/mc_tools/sql/
COPY --chown=root:root core/mc_delegation_dispatcher/sql/ /opt/minimoi/mc-release/mc-migrations/core/mc_delegation_dispatcher/sql/
COPY --chown=root:root core/supervision/sql/ /opt/minimoi/mc-release/mc-migrations/core/supervision/sql/
RUN chmod 0755 /opt/minimoi/mc-agent/start.sh \
 && mkdir -p /home/node/.claude /minimoi \
 && chown node:node /home/node/.claude
USER node

ENTRYPOINT ["tini", "-g", "--", "/opt/minimoi/mc-agent/start.sh"]
ARG MINIMOI_RELEASE_SHA=unknown
ENV MINIMOI_RELEASE_SHA=${MINIMOI_RELEASE_SHA}
LABEL minimoi.runtime.role=guild-mc minimoi.runtime.implementation=openclaw minimoi.persona=0.1.17
