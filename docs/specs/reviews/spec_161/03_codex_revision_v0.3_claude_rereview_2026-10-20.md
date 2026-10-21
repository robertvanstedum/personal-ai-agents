# Spec 161 v0.3 — revision and Claude re-review request

Robert requested a revised spec now, with Claude re-review, rather than stopping at findings. The v0.2 predecessor and Codex review are unchanged in history. This revision is v0.3 because v0.2 was already the input.

- Branch: `codex/spec161-review`.
- Spec: `docs/specs/spec_161_minimoi_observability_platform_2026-10-20.md`.
- SHA-256: `53953e9d4849abdc98bf13c95abcbac371ef59c65d9dfc4c810a8c6501745636`.
- Predecessor: `cc72288c`; first review: `5b4b765d`.
- New rule design fixtures: `evidence_v0.3/rules.yaml`, `evidence_v0.3/acceptance-tests.yaml`.
- Status: proposed repair design, ready for Claude re-review. Authorized independent base coding continues. No activation/production approval implied.

## Actual revision

All nine review findings have proposed dispositions in the spec body and traceability table. It preserves the OTel/Prometheus/Alertmanager design and MC authority; adds independent expected identities, absent/stale detection, source-time/null semantics, finite raw-ingress privacy, bounded asynchronous library/CLI, one O1 delivery path, concrete deployment/source ownership, and credential-free CI/acceptance. Existing MC brief/learning/delivery paths are retained until separately verified retirement. No custom rules daemon or broker is added.

Claude's O1 source at `f9a03753` was inspected for six reported differences. Separate observability dependencies, explicit catalogue and OTLP-only ingestion are retained. Bridge-readability metric remains a compatibility diagnostic; unified coverage includes source time. DeployNotFinished intent/name is retained; process-safe local persistence modifies the per-service filename approach. The spec distinguishes these design dispositions from independent approval of that build. PyYAML's open range still needs a reproducible pin.

## Verification actually performed

- promtool3.15.0 parses26 rules (25 alerts +1 recording rule).
- Five targeted scenarios pass: missing emitter firing/recovery, fresh-running non-firing, genuine stuck firing, healthy other host cannot mask loss, one absent exporter-failure family cannot erase another.
- Spec links and whitespace checked locally.
- No Collector/container pipeline, all-rule coverage, real notification, dev installation or production acceptance claimed. Full V1–V16 is the implementation acceptance matrix; V8 has explicit subcases. The former V1–V12 shorthand no longer covers the whole revision.

## Requested Claude re-review

Review the actual v0.3 diff against v0.2 and these five rule scenarios; retain attribution and decisions separately. Prioritize:
1. Whether the design closes F1/F2 with real Collector behavior and generated filter syntax; whether proposed caps are reasonable on the current host.
2. Concrete code/spec deltas for the existing O1 build, including source semantics, dependency locks and missing native producer wiring.
3. Whether pure OTel adaptation stays small; simplify unnecessary mechanisms without losing no-blocking, missing-data, privacy or authority invariants.
4. Rule identity, original source time, never-ran semantics, and startup/recovery behavior.
5. Exact remaining owner choices: external destinations/new timings/routing, not a demand to repeat already approved watchlist values.

Record findings/dispositions in `04_claude_rereview_v0.3_2026-10-20.md` on your own branch (or link that exact review from your shared status). Do not edit Codex's active worktree. Work on owned modules continues while this review runs; independently cleared diff and activation evidence still gate shared dev.

## Tonight's coordination

Robert supplied the two-lane plan. Codex: publish this spec; review/integrate O1 Compose/dev work with fake/blackhole destinations; continue MC runtime binding and end-to-end dev journeys; complete private planning import after Claude clears the already-prepared inventory. Claude: re-review this spec and MC repair SHAs, additive standing-check telemetry, inventory review. No main merge, no production changes; public planning files remain. Telegram/external heartbeat are pending until provisioned; do not claim those tests passed. The morning new-feature trial is dev, and readiness depends on actual MC journey results.

MC repair references: `a871adb7`, `ed6d53a7`, `5580050b`; S3a-7 `6b2bbb4b`. RC9-F1 cannot be a comparison with nonexistent native plugin ctx.runId; pinned initiator/hook binding proof remains necessary. Review results must name the exact repair SHA, not an earlier summary.
