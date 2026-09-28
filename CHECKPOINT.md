# MC stage 1a checkpoint (paused for laptop move, 2026-09-28)

Branch `claude/mc-stage-1a`, based on origin/main `66c5350f`. Not a PR yet.

## Done
- Combined config `docker/cos-agent-a/openclaw.cos-mc.json`, MC workspace seed, `start-with-mc.sh` + `selfcheck.mjs`
  (loopback CHECK phase before LAN serve; N11 split by agent; timeouts are "inconclusive", never a CoS failure).
- Staging overlay `docker-compose.staging-mc.yml`, lib/up/verify (9a-9p)/build.sh, gateway MC route (staging only),
  runbook in `scripts/staging/README.md`.
- Portal `MasterCraftsmanBackend` switch (off/stub/openclaw/grok-not-built), honest states, stub never kept as MC.
- No-spend gates `scripts/staging/mc_probe/gates.py`: final run 63/63 pass on image `mc-probe:stage1a-final`
  (built from 5034b6d6; the later commits change only host-side docs and the gate script). Raw results were in the session scratchpad.
- Full suite: 2119 passed, 24 skipped.
- Deviations note (untracked, root checkout): `planning-studio/initiatives/INIT-2026-0007-interaction-vision/documents/MC_STAGE1A_DEVIATIONS_2026-09-28.md`.

## Left, in order
1. Delete this file (or leave it out of the PR) and optionally squash the WIP commit message.
2. Open the draft PR "Master Craftsman stage 1a: partner agent in CoS's OpenClaw (dev only, no spend)":
   what is in 1a, what 1b needs (Robert creates MC's key and cap), gate results, N8 deviation + residual risk
   (shared process is NOT key-isolated), Tool Search behaviour change for CoS (gate g), rollback.
3. Report to the coordinator.

## Next step
`cd ~/.worktrees/mc-stage1a && git log --oneline -4`, then open the draft PR with `gh pr create --draft`.
No probe containers, volumes or networks are left running; staging was never touched.
