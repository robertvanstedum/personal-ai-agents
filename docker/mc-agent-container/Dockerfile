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
ENV DISABLE_AUTOUPDATER=1 \
    CLAUDE_CONFIG_DIR=/home/node/.claude \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    OPENCLAW_NO_AUTO_UPDATE=1 \
    NODE_USE_ENV_PROXY=1

# Root-owned, read-only to the node user. start.sh verifies the persona against its manifest on every start.
COPY --chown=root:root docker/mc-agent-container/openclaw.json docker/mc-agent-container/start.sh docker/mc-agent-container/egress-proxy.mjs /opt/minimoi/mc-agent/
# The reviewed portal relay (relay v2; byte-identical to the pinned native relay source). Runs as the mc-relay service.
COPY --chown=root:root docker/mc-agent/relay_v2.mjs /opt/minimoi/mc-agent/relay_v2.mjs
COPY --chown=root:root services/mc_operations/personas/mc-chat/releases/0.1.12/ /opt/minimoi/mc-agent/persona/
RUN chmod 0755 /opt/minimoi/mc-agent/start.sh \
 && mkdir -p /home/node/.claude /minimoi \
 && chown node:node /home/node/.claude
USER node

ENTRYPOINT ["tini", "-g", "--", "/opt/minimoi/mc-agent/start.sh"]
ARG MINIMOI_RELEASE_SHA=unknown
ENV MINIMOI_RELEASE_SHA=${MINIMOI_RELEASE_SHA}
LABEL minimoi.runtime.role=guild-mc minimoi.runtime.implementation=openclaw minimoi.persona=0.1.12
