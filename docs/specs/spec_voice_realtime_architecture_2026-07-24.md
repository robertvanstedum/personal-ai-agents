# Specification: Provider-Swappable Realtime Voice Architecture for Gespräche/Conversas

**File:** `docs/specs/spec_voice_realtime_architecture_2026-07-24.md`
**Date:** July 24, 2026
**Status:** Spec ready; not yet in build
**Priority:** Normal
**Owner:** Robert
**Implementation agent:** Claude Code after a separately approved build handoff
**Planning/registration:** Register in Guild's build queue; no GitHub issue
required by this handoff

## Summary

Replace the current `MediaRecorder` + polling-VAD + three-sequential-HTTP-call
voice pipeline in Mein Deutsch's Gespräche and Meu Português's Conversas with
a true realtime speech-to-speech architecture, swappable between providers
via a single config change. This is the direct outcome of
`_working/INVESTIGATION_voice_session_pauses_2026-07-24.md` and
`_working/FIXPLAN_voice_session_pauses_2026-07-24.md` — full findings and
research live there; this spec is the build-ready distillation.

## Background

Robert reported increased silent pauses, a live transcript that looks
"corrected," and no way for the persona to recover from a stall or pick up a
natural scene beat without him breaking character. Investigation found the
real cause: neither domain uses a realtime voice API today. Each turn is
three sequential HTTP round-trips — batch Whisper transcription, a plain
chat completion, then batch TTS — measured at **9–11 seconds per turn on
German's current default (Grok)**, ~4–5s on OpenAI, ~5s on Portuguese's
default (Claude Haiku). Robert's own workaround — copying prompts into
vendor apps (Grok's or ChatGPT's, whichever is convenient) to get better
voice quality, then pasting the transcript back by hand — is itself informal
evidence that native realtime voice, done well, is what he actually wants
inside minimoi.

Robert explicitly does not want the fix to be "switch the default provider
for speed" — Grok's personality and understanding are preferred, and
provider quality preference may shift over time. What he wants is the
backend fully toggleable between providers' realtime voice APIs via a
simple config change, so he can A/B providers over real usage (e.g. a week
on Grok, a week on OpenAI) and choose a default from experience.

Research (2026-07-24) found this is genuinely feasible: xAI's Grok Voice
Agent API is **explicitly compatible with the OpenAI Realtime API
specification**, so a provider-swappable client can share one integration
path with a thin per-provider adapter, rather than two separate
implementations. Both are true speech-to-speech (no separate
transcribe/chat/TTS legs) with glass-to-glass latency around 500ms–1.2s
first turn, 300–600ms subsequent — roughly 10–20x faster than today's
pipeline. Anthropic Claude has no public developer-facing realtime voice
API as of this writing (only a consumer voice mode inside Anthropic's own
apps) — Claude is **not** part of the realtime provider toggle in this
spec; it continues to serve its current role (post-session written
feedback/review), unaffected.

The codebase already anticipated some of this: `review_router.py` has a
comment referencing a "Grok Voice header/footer" — voice-mode instructions
("wait for start trigger") present in the persona `.txt` prompt files but
explicitly stripped out for the current text-chat flow. This should be
reused, not redesigned from scratch.

## Goals

1. Replace the per-turn REST pipeline with a realtime speech-to-speech
   session (WebRTC for the browser client) for both Gespräche and Conversas.
2. Make the realtime provider (OpenAI or Grok) swappable via a single
   config value — no code change required to switch — so Robert can A/B
   providers over real sessions.
3. Port existing persona `.txt` prompts into the realtime session's
   instructions/system-prompt field, reusing the existing "Grok Voice"
   scaffolding found in the persona files rather than writing new
   instructions from scratch.
4. Replace the custom RMS-threshold VAD and (previously proposed) custom
   pause-timeout with the provider's native turn-detection/interruption
   handling, plus a persona-level instruction to keep a scene moving on its
   own beats (the coffee example) rather than waiting indefinitely.
5. Capture a transcript via the provider's native transcription events for
   post-session display only — never live during the conversation — using
   whatever raw output the provider's transcription actually produces (no
   assumption that it will be cleaner or dirtier than the current Whisper
   output; confirm during implementation).
6. Keep German and Portuguese from duplicating this work twice: evaluate
   sharing one realtime-session implementation between the two domains
   (they are separate, hand-copied implementations today) rather than
   porting the same architecture change independently into each.
7. Resolve the "translation stays pinned" detail: any word-lookup/
   translation-assist feature during a session should be able to keep using
   a specific model independent of which provider is running the realtime
   voice engine.

## Non-goals

- No change to the post-session written feedback/review flow — Claude stays
  exactly where it is for that.
- No attempt to add Claude to the realtime voice-provider toggle — not
  available as a public API today. Revisit if Anthropic ships one later.
- No change to persona content, scoring, or the Anki/session-history
  features unrelated to the live audio path.
- Not scoping a native mobile app or PWA — this stays a web client, per
  Robert's stated priority (MacBook web app first, likely how his daughters
  will use the platform too).
- Not attempting to make Whisper's or the new provider's transcription
  "less corrected" — Step 4 of the investigation already confirmed no
  correction pass exists; any smoothing is inherent to the ASR model itself.

## Preconditions

1. Robert confirms this spec's shape before build starts — this document is
   the "detailed spec," registered as `spec_ready`, not yet approved to
   build.
2. Cost awareness: xAI's Grok Voice Agent API is priced at $3.00/hour for
   speech-to-speech. Robert should see this plainly before committing to
   regular use or extended A/B testing — flagging here so it isn't a
   surprise mid-build.
3. Current `main` commit recorded before work begins (build-queue entry
   below).
4. Work happens on an isolated branch/worktree, dev-tested before any
   production deploy, per this project's standing workflow.

## Phase 1: Design details not yet resolved (read-only investigation before code)

Before writing code, resolve:

- **Session lifecycle**: how a Gespräche/Conversas session starts (ephemeral
  token issuance per provider), how it ends cleanly, and what happens on
  network drop/reconnect mid-session.
- **Ephemeral token issuance**: server-side endpoint that mints short-lived
  session tokens per provider — real API keys must never reach the browser.
  Design this the same way `core/get_secret.py` is already used for other
  credentials, but confirm the realtime APIs' specific token/session model
  (OpenAI's and Grok's exact mechanics may differ in small ways despite
  spec compatibility — verify directly against current docs, not assumed
  from this spec's research phase).
- **German vs. Portuguese code sharing**: whether the new realtime-session
  client/server code should be shared (recommended, since the current
  duplication is exactly why "findings from one domain don't transfer to
  the other" was a problem during the pause investigation) or kept parallel
  for consistency with the existing pattern. Recommendation: share it — this
  is a natural point to stop duplicating.
- **Persona prompt porting**: read the existing "Grok Voice header/footer"
  content in the persona `.txt` files directly (currently stripped out by
  `build_chat_system_prompt()` in `review_router.py`) and confirm what it
  already assumes about voice-mode behavior before writing new
  session-instruction content.
- **Provider config mechanism**: where the provider toggle lives (env var,
  per-user setting, admin config) — should match Robert's ask of "toggle
  online and everything backend changes," so likely a config that takes
  effect without a redeploy, not a hardcoded default requiring a code push.
- **Translation-pinning**: confirm whether a word-lookup/translation feature
  currently exists in the live session UI, and if so, how it should call a
  fixed model independent of the realtime provider selection.

### Required deliverable

A short design note (can be an addendum to this spec or a follow-up doc)
answering the above, before Phase 2 code work begins. Robert approves the
design note before Phase 2 starts.

## Phase 2: Build

Expected shape, subject to what Phase 1 resolves:

1. Server-side: ephemeral-token issuance endpoint(s), provider adapter layer
   (OpenAI Realtime API / Grok Voice Agent API), config-driven provider
   selection.
2. Client-side: replace `MediaRecorder` + polling VAD + REST calls with a
   WebRTC session per the selected provider's realtime API.
3. Persona instruction porting, including scene-continuation guidance for
   the "keep moving on natural beats" behavior.
4. Post-session transcript capture and display (no live overlay).
5. Update both German and Portuguese to use the new architecture — together
   if Phase 1 confirms shared code, otherwise as twin changes.

## Required verification

- Full test suite, plus manual live-session testing in dev for both domains.
- Direct latency comparison against the investigation's baseline numbers
  (9–11s German/Grok, ~4–5s German/OpenAI, ~5s Portuguese/Claude-Haiku
  today) — confirm the new architecture is meaningfully faster, not just
  architecturally different.
- Provider toggle verified to actually switch backend behavior without a
  code change or redeploy, per Robert's stated requirement.
- Manual voice-quality/naturalness check by Robert, comparing the new
  in-app experience against his existing vendor-app workaround — this is
  the real acceptance bar, not just a latency number.
- Confirm transcript is not shown live, only after session end.
- Confirm scene-continuation / stall-recovery behavior with a live test
  (a natural pause during a scene, e.g. the coffee example) — Robert should
  not need to break character to prompt the persona forward.
- Production smoke test and a recorded rollback point before final merge,
  consistent with this project's other production changes.

## Acceptance criteria

1. Both Gespräche and Conversas use a realtime speech-to-speech session, not
   the current three-call pipeline.
2. Provider selectable between OpenAI and Grok via config, without a code
   change or redeploy.
3. Persona behavior (personality, scene continuity, language) is preserved
   or improved relative to today — not regressed for the sake of speed.
4. No live transcript during a session; transcript available after the
   session ends.
5. Persona recovers from silence/stalls and picks up natural scene beats
   without Robert needing to break character.
6. Claude's existing post-session review role is unaffected.
7. Cost visibility: Robert has seen and acknowledged the Grok Voice Agent
   API's $3/hour pricing before this ships to production for regular use.

## Build-queue registration

Register this as:

- **Title:** Provider-swappable realtime voice architecture for Gespräche/Conversas
- **Status:** `spec_ready`
- **Priority:** `normal`
- **GitHub issue:** `null`
- **Blocked reason:** None — spec ready, awaiting Robert's go-ahead to start
  Phase 1 design-detail resolution.
- **Notes:** Direct outcome of the 2026-07-24 voice-pause investigation.
  Full findings/research in `_working/INVESTIGATION_voice_session_pauses_2026-07-24.md`
  and `_working/FIXPLAN_voice_session_pauses_2026-07-24.md`. Real cost
  consideration: xAI Grok Voice Agent API is $3/hour for speech-to-speech —
  surface this to Robert before regular/extended use, not just at spec time.
