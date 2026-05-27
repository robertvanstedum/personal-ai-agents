# Operations — Guild Cabinet

Production guardian. Formalizes what already informally exists across the Curator
and language-german pipelines. When something runs in production, Operations owns it.

**Role definition:** [GUILD_CHARTER.md §8.3](../GUILD_CHARTER.md#83-operations)

---

## Scope

| Domain | Ops responsibility |
|--------|--------------------|
| Curator | launchd daily run, Telegram delivery, Grok API health, cost monitoring |
| language-german | Flask server uptime, drill pipeline health, Telegram bot |
| Guild | game-state.json currency, intent-register freshness |

## First Deliverable

`OPS_HEALTH_MONITOR_SPEC_v1.0.md` — approved spec, lives in `_working/`.
Implementation deferred to post-Chicago (per MEMORY.md). This cabinet is the home
for that implementation when it ships.

## Trigger Phrases

- "Health of [domain]?" → domain-health.md check + live status commands
- "Something broke in [X]" → situation-room escalation
- "Deploy [X]" → launchd + git pull + validate

## Maps To

launchd + monitoring scripts + Claude Code

## Runbooks

Incident-specific playbooks: `runbooks/`
