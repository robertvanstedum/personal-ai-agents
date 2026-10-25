# Plan — Model access for all three providers, and MC in its own container

**25 Oct 2026 · Claude Code for Robert · for approval of direction before any
further build.** Experiments are allowed; big code changes wait for this
direction (Robert, 25 Oct).

## 1. The direction, in three rules

1. **Agents run on Robert's plans** through each vendor's supported
   sign-in route. That covers MC, personal OpenClaw and CoS Agent A, all of
   which are OpenClaw agents. **No silent fallback to a metered key:** when a
   plan's limit is hit, the agent errors and says so.
2. **Services that aren't agents use API keys, through the gateway only.**
   That covers Curator scoring, the language domains, voice, embeddings and
   portal features. Each service gets its own **capped** virtual key, and
   every provider key also has a provider-side limit. No service holds a raw
   provider key.
3. **Heavy build and analysis work goes to Claude Code and Codex,** which run
   on Robert's plans. MC coordinates; it doesn't grind.

## 2. Per provider (from OpenClaw 2026.9.4's own docs; read 25 Oct)

| | Anthropic (Claude) | OpenAI | xAI (Grok) |
|---|---|---|---|
| **Plan route for agents** | **Claude CLI**: OpenClaw runs the signed-in Claude Code, on the Max plan | **Codex OAuth**: ChatGPT sign-in. OpenClaw's docs: *"OpenAI explicitly supports subscription OAuth usage in external tools … like OpenClaw"* | **Grok OAuth**, with an eligible **SuperGrok or X Premium** subscription. OpenClaw's docs call it the recommended path; no API key needed |
| **Status today** | **Done on native dev, 25 Oct.** Verified `provider=claude-cli`, zero direct API calls afterwards | Not set up. No native agent uses OpenAI models today | Not set up. Personal OpenClaw `main` uses the shared **API key** `8463f056` |
| **How the plan shows usage** | Not visible to OpenClaw (Claude owns the login) | Plan, quota windows and credit balance shown | SuperGrok quota and reset time shown |
| **Services (API, gateway, capped)** | Little need, if any | Voice and realtime, possibly embeddings. One shared key, `6aac0a80`, sits in every dev container today | **Curator scoring** (the largest steady spend), language domains, prod CoS routes. One shared key, `8463f056`, sits everywhere today |
| **Terms note** | Claude Code is Anthropic's own product, and its docs say `claude -p` draws on plan limits. Anthropic's guidance prefers API keys for shared or production automation: **weigh this for 24/7 prod agents** | OpenAI explicitly permits it | xAI permits OAuth via its shared client |

## 3. What this means for MC's container

The container uses **all three plan routes inside itself**: Claude Code,
Codex OAuth and Grok OAuth.
- **Each sign-in lives only on MC's own home volume,** and Robert does each one
  once, in his own browser. No agent handles a sign-in.
- **The container holds no API keys.**
- **MC's command access can't read those sign-in files** (§6 of the
  container spec tests this).

Phases, unchanged from spec v0.2:
1. **Core MC:** persona, plan routes, mounts, core tools, bounded search,
   capture.
2. **MiniMoi tools:** Board, host and agent contact.
3. **Prod.**

## 4. Plan limits are now the ceiling

Robert's own use (Claude Code, Codex, Grok) and his agents share each plan's
limits. Controls:
- **cheap defaults:** routine agent turns on cheaper models (Sonnet, a fast
  Grok); Opus and Fable on request;
- **loud failure,** instead of a metered fallback;
- **visibility:** OpenClaw's usage view for Codex and SuperGrok quota, and
  Claude Code's own `/usage` for Claude.

## 5. Experiments first (small, reversible, no product code)

| # | Experiment | Proves |
|---|---|---|
| E1 | Codex OAuth on native OpenClaw (Robert signs in), one test turn on an `openai/*` model | The OpenAI plan route works here |
| E2 | Grok OAuth on native OpenClaw, if Robert has SuperGrok or X Premium; one test turn; move personal `main` off the API key | The xAI plan route works, and the shared key leaves personal OpenClaw |
| E3 | A throwaway OpenClaw container: Claude Code pinned, Robert signs in inside, one test turn | The Claude CLI route works **inside Docker**, before the MC image is built |

Each experiment is a sign-in plus one turn, with a rollback note. No repo code
beyond small operator scripts.

## 6. Order

1. **Robert approves the direction** and answers §7.
2. **Experiments E1–E3.**
3. **Services onto capped gateway keys:**
   - map which service calls which provider;
   - stop loading the whole `.env` into every container;
   - give each service its own gateway key and cap;
   - set provider-side limits;
   - revoke the old shared keys (`8a9a35a8`, `971af42b`, `8463f056`,
     `6aac0a80`) once nothing uses them.
4. **MC container, phase 1,** side by side, then the toggle and rehearsed
   rollback. Then **phase 2.**
5. **Prod:** the same direction through a release. CoS Agent A follows the same
   pattern as MC.

## 7. Decisions for Robert

| ID | Decision | Recommendation |
|---|---|---|
| D1 | Do you have SuperGrok or X Premium? If not, xAI stays API-only, capped | Use it for agents if you have it |
| D2 | Agents on your ChatGPT plan through Codex OAuth | Yes; OpenAI explicitly permits it |
| D3 | Prod agents running 24/7 on a server: plan routes or capped API keys | Plans for MC and CoS while usage is personal-scale; revisit if limits pinch or terms tighten |
| D4 | Monthly caps for services (API) | Curator $15, languages $5 each, voice $10 |
| D5 | Personal OpenClaw | The same plan routes as MC (Claude done; Codex and Grok per E1 and E2) |
