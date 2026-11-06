# Task routes on plan and API billing

The same pattern as [model_tiers.md](model_tiers.md): a task asks for a **role**, and each role reads one **route**.
Moving a task to another model is one line. The catalogue is `core/model_routes/catalogue.json`. A host opts in by
naming its own copy in `MINIMOI_MODEL_ROUTES_FILE`, read fresh on each turn; on a host that has not opted in, nothing
changes and no route is sent.

Tier (`strong`/`mid`/`low`, shown as A/B/C), provider and billing (Plan or API) are separate. A route is offered to an
agent only when `offered.<agent>` says `available`, which means it was proved on that agent with real tools. Anything
else in that field is the reason it is not offered, and the Options menu shows it. Nothing falls back: a route that
cannot serve stops the turn with nothing sent.

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

## Limits

- **Effort:** every MC plan route runs at the runtime's `thinkingDefault` (high), whatever its tier.
- **Evidence:** it comes from OpenClaw's own session record, read by the relay, and only from the reply stamped with this
  turn's exact run id. If that reply isn't there, the receipt says no evidence, never the selected route. `served_matches`
  compares provider and model only, so it does not prove billing; that takes `api`, the auth in use, and API key spend.
- **Claude plan routes (swap test 5 Nov):** not offered. Moving from OpenAI to Claude loses earlier turns (OpenClaw logs
  `cli session history refused across auth boundary: reason=auth-unknown`), and Claude's native tools (`Bash`, `Write` and
  others) sit outside MC's tool policy, including `fs.workspaceOnly`. Claude back to OpenAI keeps history.
- **Waiting messages:** a waiting message takes the conversation's choice when it is sent, not when it was queued.
- **CoS:** its plan routes are listed as unavailable until CoS is signed in and its tool adapter is reviewed. CoS's own
  page does not show the selector yet.
- **Other services:** voice, search and embeddings keep their own routes. A chat subscription is not proof for them.
