# Claude Code: build readiness against spec v1.2 (22 Oct, before the start signal)

**Status:** review only. Robert approved v1.2 (SHA `bab4fb14…`, verified locally) and the D12 build start, then deferred the start until he is back. Nothing below was started: no code, no probe, no operator change, no heavy test.

## 1. What I checked

- The v1.2 spec hash matches the approval record. The four domain API v0.2 companions are pinned with it.
- The v1.1→v1.2 diff (165 changed lines) against my acceptance note: every change is one of the twelve closing conditions from Claude chat and Grok, folded into §§7, 9, 16, 17. None contradicts what I accepted; two of them add M1 work I had not counted (§3 below).
- Codex's handoff to me, the closure record, Grok's second pass and Claude chat's review.

**Verdict:** v1.2 is buildable as written, and I build to it. Two items change the shape of M1.4 and one risk decides whether first light gets its tools at all.

## 2. The build order I will follow when told to start

1. **Baseline gates (M1.0):** measure disk again; under the 8 GiB gate run the two PostgreSQL suites, the full Guild regression and the installed-runtime probe on `28346c31`; record; pin.
2. **C6, reproducible dev route:** a reviewed two-line change to `~/minimoi-staging/config/vault-mc-dev-5239cbfc.yml` (`openclaw`, `on`), with effective-composition check and rollback, so the native route no longer depends on my operator overlay. Operator change on dev; applied only through the reviewed-diff gate.
3. **A05 probe first (the M1.1 action-admission gate):** extend the fake-provider installed-runtime probe to the stable session with a deliberately delayed old-run tool call during a new turn, forged run IDs, concurrent calls, the tool-before-registration race, cancellation and restart. Then the smallest adapter it proves: capture the opening run ID, forward `X-MC-Run`/`X-MC-Tool-Call` via the `before_tool_call` hook, bind once to the claim, 2-second wait at the tool boundary, no fallback to session lookup. **No tool is enabled before this passes**, and if the runtime cannot deliver it, chat stays usable, tools stay off, and the transport design goes back to review.
4. **M1.2:** policy v0; `display_state` with deterministic note key, read-back and recovery (A04); one pending offer across the legacy guest, lesson and tool families, drained at cutover (A03); exact yes/`ja`; the batch guest card; the two clocks as one constants module with both boundaries tested (A02+); English cards with absolute expiry and timezone, the one-line code reason, re-propose on expired yes (conditions 9, 11); each capability's regex front end off in the same deploy (A15).
5. **M1.3:** `minimoi_keep_preference` on the anchored ledger with owner-source span, installation-policy reference, idempotent keep/revoke and undo; A08b unanswered scope stays situational with one pending suggestion.
6. **M1.4:** the §16.4 trial record: five completed exchanges, exchange 5 as gap ownership with a persisted follow-up, one correction that holds (A08c), the one-offer count (A24), the CoS handoff (A11a), latency per §16.3.

Each slice ships as a diff with tests, migration/rollback note and A-case evidence; Codex reviews the diff; A05 and A04 are re-verified on the implemented code, not on the design.

## 3. Two items v1.2 adds to M1 that I had not counted

**A11a, the CoS→MC handoff, is real work, not a sentence.** It needs a persisted transfer (Robert's source request and a transfer ID), idempotent delivery into the MC conversation, and a destination acknowledgement. CoS today runs on its OpenClaw backend (`cos-agent-a`), which cannot dispatch tools (verified: `openclaw_backend.py:338`), and D0 is deferred. The only way to build the handoff inside M1 without D0 is **platform-side**: the CoS chat route (`domains/cos/chief_of_staff.py` `/chat`) gets a deterministic owner command for the yes after a handoff offer, and the platform performs the transfer as its own note in the MC conversation with provenance; the CoS model only offers. I'll size that at the start of M1.4. If it doesn't fit, v1.2 allows "boundary test incomplete", and I'll label it so rather than stretch M1.

**Condition 2 is a defect fix, not a refinement.** T commits a proposal before it displays it; a crash between commit and publication leaves an eligible, unseen offer (I had read T's withdrawal-on-failure as sufficient; it isn't). The fix is small, but it goes first in M1.2.

## 4. The one risk that decides first light

**The A05 probe.** Everything that lets MC *act* in first light sits behind it. Codex found the ingredients in the installed OpenClaw 2026.9.4 source (an opening run ID on the stream, `runId` and `toolCallId` on the hook context); nobody has yet proven, on the real runtime, that the run ID is bound before the first tool call arrives and that a delayed old call is refused. If the probe fails, first light still delivers the conversation (M1.1, already live) and honest reads, but no actions until a narrow transport alternative is reviewed. Robert should know this before the start: it is the reason the probe goes first, and it is free to run.

## 5. Smaller notes for the start

- Disk is below the gate again (last seen 6.4 GiB). The heavy gates cannot run until it is freed; the candidates are Robert's (dangling Docker volumes, OpenClaw backups, the Cowork workspace).
- The build session's overlay now also sets the two route variables, so a portal redeploy by either of us keeps the native route.
- The persona session (D9) is independent of all of this and can run as soon as Robert is back; the persona lands in `SOUL.md` by the file contract, and v1.2's English-platform-records rule (condition 9) doesn't change it.
- Codex's handoff is a local artifact; I'm treating Robert's words here ("review but don't start… we start when I'm back") as the authority for the deferral.
