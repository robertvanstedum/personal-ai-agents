# Task routes on plan and API billing

The same pattern as [model_tiers.md](model_tiers.md): a task asks for a **role**, and each role reads one **route**.
Moving a task to another model is one line. The catalogue is `core/model_routes/catalogue.json`. A host opts in by
naming its own copy in `MINIMOI_MODEL_ROUTES_FILE`, read fresh on each turn; on a host that has not opted in, nothing
changes and no route is sent.

Tier (`strong`/`mid`/`low`, shown as A/B/C), provider and billing (Plan or API) are separate. A route is offered to an
agent only when `offered.<agent>` says `available`, which means it was proved on that agent with real tools. Anything
else in that field is the reason it is not offered, and the Options menu shows it. A route that cannot serve stops the
turn, except for the one automatic switch under "Plan first, then the capped API route".

## Master Craftsman's tasks

| Role | Task | Can be chosen per conversation |
|---|---|---|
| `mc-conversation` | Conversation | Yes, under Options; applies from the next turn |
| `mc-private` | Private conversation | No |
| `mc-rooms` | Rooms | No |

MC's job agent (`mc-agent`) is not a role yet. It runs on the runtime's default model.

The Morning note is not a role either. `scripts/mc/morning.py` always runs on the CoS backend, so it follows CoS's own
model, not this catalogue.

## Switching a task (Dev)

Edit the role's `route` in `/opt/minimoi-staging/config/model-routes/catalogue.json`, holding
`/opt/minimoi-staging/.renewal-deploy.lock` and replacing the file atomically. The next turn reads it, with no restart.
The route must be `available` for that agent, or every turn of that task refuses. So does a catalogue missing any task
listed in `TASK_ROLES`: a missing line stops the work and never falls to the runtime's default.

## Offering a new route (Dev)

1. The runtime and relay must already know it. Their lists (`MC_PLAN_ROUTES`, `MC_RELAY_ROUTES`) are set in the MC
   compose file and come from `python -m core.model_routes`. Adding a model there restarts MC.
2. Set `offered.mc` to `available`, choose it in a labelled test chat under Options, and run a plain turn plus a real
   read-only task read.
3. Check the evidence: `routes.mc.last_turn.served` in the conversation's file (provider, model, api, harness), the
   tool admission in `guild.mc_tool_admissions`, and unchanged spend on the API key.
4. If it fails, put the reason back in `offered.mc`.

## Plan first, then the capped API route

Robert's decision, 6 Nov 2026, for MC and CoS. A turn on a plan route that declares `"failover"` in the catalogue moves to
that API route once, automatically, and says so. The next turn tries the plan again; the chosen route doesn't change.

**What switches.** Only OpenClaw's own error type on the turn: `rate_limit_error`, `insufficient_quota`, or
`authentication_error` (`PLAN_SIGNALS`, read from OpenClaw 2026.9.6's `openai-compat-errors`). Any other error, whatever its
words, is reported as before.

**What stops it.** Any doubt that a tool began: the turn stops and says so. MC: any text sent, a tool mark since the turn
began, or no way to read marks (`mc-tool-starts`, written by `minimoi-tools`' `before_tool_call` hook, read by the relay). CoS:
any text, no live binding, or any admitted call, native call or completed model call reported for the run.

**Limits.** One level, same provider. No capped key, or the cap reached: the turn says plan and API are both unavailable.
Grok has no target, and `api-xai-low` stays unoffered, until its plan adapter exists. Only page, Private and Rooms turns have a
route: MC's jobs, CoS's Telegram, MC-lane and meeting turns don't switch.

**Switching it on, per host.** All are off until set, and a missing one means no switch, never an uncapped call:

- The host's catalogue carries the route's `"failover"` line. Delete that one line to turn that route's switch off.
- MC: `MC_FAILOVER_ROUTES` (the targets) and `MC_MODEL_GATEWAY_KEY` on `mc-agent`, a capped key allowed those tiers
  (`mc_failover_keygen_py` in `scripts/staging/mc_keys.sh`). On `mc-relay`: `MC_RELAY_ROUTES` from `python -m core.model_routes`,
  `MC_RELAY_TOOL_STARTS=<state>/mc-tool-starts`, and `MC_RELAY_CHAT_DB` for the served evidence. MC's tools must be on.
- CoS: `COS_FAILOVER_ROUTES` on `cos-agent-a`, OpenAI tiers only, and CoS's own capped key allowed them (`/key/update` inside the
  gateway). The key file proves it is capped; a host passing its own key by environment sets `COS_GATEWAY_KEY_CAPPED=1`. On the
  master key the target is left out.

**Prove it on Dev first:** see "What this has not been run against" below; force each signal once in a labelled chat, then
check `routes.<agent>.last_turn` (`route_id`, `billing_label`, `served`, `switched_from`) and the key's spend.

### What this has not been run against

Tests use fake runtimes. No live plan has been exhausted or signed out. OpenClaw's classification of a real ChatGPT or Claude
limit message, and whether its stream carries the mapped type rather than `internal error`, are confirmed only by reading code;
a generic `internal error` simply never switches. Whether `before_tool_call` fires for OpenClaw's native tools on the Claude CLI
bridge is not confirmed.

## Grok: on and off (#466)

Grok text is paused and off in everything shipped. Each piece below turns on by itself; none falls back onto it, and no
plan route's failover may name it. It bills
per use against the xAI key, so check the key's cap first. Grok Voice and the `minimoi-voice-*` roles are separate: they
stay as they are, whichever way these are set.

**MC and CoS, per conversation.** The route is `api-xai-low` (`minimoi-xai-low`, Grok 4.3, shown as "pay per use · API").

- *Off, as shipped:* `"offered": {}` on that route. It isn't in the picker and can't be chosen.
- *On:* on the host, the runtime must know it first, then one line in the host's catalogue:
  1. CoS: `COS_METERED_ROUTES=minimoi-gateway/minimoi-xai-low` on `cos-agent-a`, and add `minimoi-xai-low` to the models of
     CoS's gateway key if it has its own (`/key/update` inside the gateway; a host on the master key needs nothing). Restart it.
  2. MC: `MC_METERED_ROUTES=minimoi-gateway/minimoi-xai-low` and `MC_MODEL_GATEWAY_KEY` on `mc-agent`, a new capped key for that
     tier only (`mc_grok_keygen_py` in `scripts/staging/mc_keys.sh`). MC must be in plan mode with `MC_PLAN_MODEL` set. Add
     `"api-xai-low": "minimoi-gateway/minimoi-xai-low"` to `MC_RELAY_ROUTES` on `mc-relay`. Restart both (allow 10 minutes).
  3. In the host's catalogue, copy the route in if its copy predates it, and set `"offered": {"mc": "available", "cos": "available"}`
     (or just one of them). The owner then chooses it under Options, and it applies from that conversation's next turn on the
     same history.
- *Prove it before relying on it:* a labelled chat, one plain turn and one real tool read, then step 3 of "Offering a new
  route". This route has not been run against a live runtime or provider.
- *Off again:* put back `"offered": {}`. No restart. A conversation still on Grok refuses its next turn with nothing sent until
  the owner chooses another; it is not moved for them. Then remove the env values and restart to take the runtime's permission
  away; MC's start removes what it patched in.

**Curator's daily ranking.** `CURATOR_RANKING_MODEL` in the Curator container (`/opt/minimoi/.env` in production). Unset or
`haiku`: Claude Haiku. `grok-4.3`: Grok. Any other value stops the run, so nothing is sent, rather than choosing for you.
Recreate the Curator container for a new value; the next generation uses it.

**German text.** `GERMAN_GROK_TEXT=on` in the German container's environment, then recreate it. Translation, correction and
phrases try Grok first, and Grok becomes the selected model in the conversation picker. Unset or anything else: off.

**Portuguese text.** `PORTUGUESE_GROK_TEXT=on` in the Portuguese container's environment, then recreate it. Grok is added to
the conversation picker, with Claude still selected. Unset or anything else: off.

## Limits

- **Effort:** every MC plan route runs at the runtime's `thinkingDefault` (high), whatever its tier.
- **Evidence:** it comes from OpenClaw's own session record, read by the relay, and only from the reply stamped with this
  turn's exact run id. If that reply isn't there, the receipt says no evidence, never the selected route. `served_matches`
  compares provider and model only, so it does not prove billing; that takes `api`, the auth in use, and API key spend.
- **Claude plan routes:** passed the swap test on Dev and held back for independent review. OpenClaw refuses Claude another
  provider's transcript (`cli session history refused across auth boundary`), so MiniMoi carries the kept conversation on a
  switch. Claude's own tools are denied in its user settings, written when MC starts.
- **MC restarts:** a recreated MC can wait several minutes for its gateway owner lease, and its health check runs every 3
  minutes. Allow 10 minutes before judging it failed. Don't edit lease rows.
- **Waiting messages:** a waiting message takes the conversation's choice when it is sent, not when it was queued.
- **CoS:** its plan routes are listed as unavailable until CoS is signed in and its tool adapter is reviewed. CoS's own
  page does not show the selector yet.
- **Other services:** voice, search and embeddings keep their own routes. A chat subscription is not proof for them.
