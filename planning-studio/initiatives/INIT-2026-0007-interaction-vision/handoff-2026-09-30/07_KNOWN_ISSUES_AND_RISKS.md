# 07: Known issues and risks

| # | Issue | Impact | Status |
|---|---|---|---|
| 1 | **Aborted streamed calls are invisible to LiteLLM 1.93.1.** No callbacks run on a client cancel, so there is no usage record and no key-budget spend, although the provider likely bills the tokens generated so far. | Spend can exceed MC's cap by the stopped turns | The portal marks each one `cost_source: unrecorded-abort`. **Production needs a provider-side limit.** |
| 2 | **OpenClaw 9.6 reports zero usage** in its compat API and stream chunks | Runtime token counts are unknown | They're recorded as null; the footer uses the gateway record. |
| 3 | **Write only still bills the voice audio.** Neither provider is asked for text-only output, because xAI's support is unverified. | Cost | The UI says so. |
| 4 | **Disk:** each staging build leaves 5–8 GB, and the Mac swaps about 5 GB. The data disk is at 94% (about 13 GB free). | Builds fail when the disk is full | Prune after every build; M0 cleanup is Robert's decision. |
| 5 | **The Workshop reading goes "unknown"** without observe/sync every 9 minutes or less | Honest, but less useful | The launchd job needs Robert's OK. |
| 6 | **Cloudflare's roughly 100 s limit** on dev can beat CoS's honest 504 at 125 s | Generic error page on dev | Known; production nginx should be 130 s. |
| 7 | **The credential scrub over-scrubs ordinary speech** ("the secret is to…") | Transcript text lost | A low-priority tune. |
| 8 | **The Systems light means "the agent loop is alive",** not "all services healthy" (#269's design) | Misreading | Consider a `not_watched` field or a separate services light. |
| 9 | **The root checkout is on the old branch `codex/cos-agent-a-runtime`,** with uncommitted changes | Confusion; native jobs used to run from it | M0; nothing runs from it now. |
| 10 | **Staging verify reports image mismatches** after partial rollouts | Noise | Expected. #264 and #279 improve verify. |
| 11 | A security follow-up | — | Tracked privately. |
| 12 | **The staging telegram bots:** `state/bots.on` exists, but cos-bot and system-bot are focus-stopped | Bots are not running on staging | Intended. |
