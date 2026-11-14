---
name: mc-delivery
description: Open when Robert asks what's deployed, delivered, merged or running on Dev or production, or whether a deploy, CI or GitHub Actions run is in progress or finished.
---

## What was delivered (Robert, 7 Nov)
On Dev, read `/minimoi/docs/delivery/DEV.md` first: what Dev runs and the commits `main` lacks. On production, read `/minimoi/docs/delivery/PRODUCTION.md` (what production runs); `DEV.md` exists only on Dev. Production is what's merged to `main`. Place each item in production, on Dev only, or still open. If either file is missing, say so, then use GitHub's main.
To see deployments or CI, read `/repos/robertvanstedum/personal-ai-agents/actions/runs?status=in_progress` and recent runs with minimoi_github_get; answer short, with the run link. You can read GitHub; you cannot start or cancel anything there.

## Did the deploy finish? (Robert, 13 Nov, #561)
Answer in this order. First the delivery record: `PRODUCTION.md` (and `PRODUCTION.json` beside it) is written only after a verified deploy, so it is the authority on what production runs and when it was deployed; on Dev, `DEV.md`. Then live GitHub with minimoi_github_get, for a run newer than the record. If that read says rate_limited, say so in one short line and answer from the delivery record. The cached repository-awareness block comes last, always with its age ("as of 05:17 UTC, 46 min ago"); never present a cached in-progress run as current.
