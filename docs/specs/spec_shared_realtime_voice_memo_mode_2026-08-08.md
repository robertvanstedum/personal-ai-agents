# Specification: Shared Realtime Voice Architecture — Memo (Dictation) Mode

**File:** `docs/specs/spec_shared_realtime_voice_memo_mode_2026-08-08.md`
**Date:** August 8, 2026
**Status:** Spec ready; not yet in build — sent to Codex and Grok for independent review before Robert approves a build handoff
**Priority:** Normal (see paired stopgap in Preconditions — do not let this spec's review timeline leave production broken)
**Owner:** Robert
**Implementation agent:** Claude Code after a separately approved build handoff
**Planning/registration:** Register in Guild's build queue; linked GitHub issue [#177](https://github.com/robertvanstedum/personal-ai-agents/issues/177)
**Companion spec:** `docs/specs/spec_voice_realtime_architecture_2026-07-24.md` (conversation mode — see Background)

## Summary

Add a second, backend-shared realtime voice mode — **memo mode**, one-directional
voice-to-text capture with no AI response — alongside the existing (built, gated,
not yet merged) **conversation mode** realtime voice architecture. Both modes
share one transport/session/provider-adapter layer; memo mode is a smaller
policy layer on top, not a third independent voice implementation. This
replaces the ad hoc, independently-broken voice-input code currently
duplicated across German Schreiben, Portuguese Escrita, and the German/
Portuguese word-practice mics.

## Background

### Why this came up

Robert reported (issue #177) that Schreiben's dictation mic cuts off during
natural pauses. A targeted fix (continuous `SpeechRecognition` + auto-restart
on non-fatal end/error) was built and validated in dev, but never reached
production — it only landed in per-domain dev worktrees and dropped out of a
later release consolidation. Robert then reproduced the identical cutoff in
production, which is what prompted this spec: patching the old browser
`SpeechRecognition` API again would fix the symptom a second time without
fixing why the same class of bug keeps needing independent rediscovery.

### The actual problem: too many separate voice-input implementations

As of 2026-08-08, voice input across the app is implemented at least three
different ways, none sharing code:

1. **Browser-native `SpeechRecognition`** — German Schreiben, Portuguese
   Escrita (dictation into a textarea). Unreliable: unsupported on Firefox,
   inconsistent on iOS Safari/home-screen web apps, and — the root of #177 —
   ends the whole session on the first detected pause unless the caller
   explicitly sets `continuous` and handles auto-restart, which nothing here
   did until a same-day patch that itself never shipped.
2. **`MediaRecorder` + batch Whisper transcription** (`/api/transcribe`,
   `/api/pt/transcribe`) — German Wörter's and Portuguese Palavras's
   practice-answer mics (fixed onto this pattern 2026-08-04, replacing an
   earlier `SpeechRecognition` copy of the same bug). Reliable, but
   fundamentally push-to-talk: record the whole attempt, then transcribe it
   as one blob — no live-as-you-speak feedback.
3. **True realtime speech-to-speech** — German Gespräche / Portuguese
   Conversas, built per `spec_voice_realtime_architecture_2026-07-24.md` on
   branch `feat/realtime-voice-shared-architecture`. Not yet merged to
   `main` ("active, gated multi-phase build," build gate not yet triggered).
   This is a full bidirectional conversation: a persona speaks, listens,
   responds, tracked as multi-turn dialogue, with a 20–30 minute session cap.

Robert's framing of the actual need: the mic in a writing/notes context
(Schreiben, Escrita) isn't there to have a conversation — it's a **voice
memo**: record a thought for a journal entry, or say something for
practice-and-correct, so the writing exercise and the speaking practice
reinforce each other. That is architecturally much closer to (2) than to
(3) — except Robert explicitly wants the reliability and live-as-you-speak
transcription quality of (3)'s underlying transport, not (2)'s push-to-talk
batch feel, and not another patch on (1)'s flaky browser API.

Separately, Robert wants full **conversation mode** — real back-and-forth
speech — available in a currently-unbuilt Chief of Staff "Confer" feature.
That's future work, out of scope to build here, but it means whatever memo
mode's design commits to must not make reusing the existing conversation-mode
stack for Confer any harder later.

### What already exists to build on

`spec_voice_realtime_architecture_2026-07-24.md`'s build produced a shared
transport/session layer, not yet merged:

- **Server:** `core/realtime_voice/bootstrap.py` — one Flask blueprint
  factory (`create_bootstrap_blueprint`), already parameterized per domain
  (German/Portuguese each register their own instance with their own
  persona lookup + locale). Handles identity check, per-user rate limiting,
  allow-listed provider/persona/scene/voice values, and ephemeral
  short-lived provider credential issuance — real API keys never reach the
  browser. Plus `core/realtime_voice/config.py`, `duration_guard.py`,
  `prompt_builder.py`, and `providers/openai_realtime.py` /
  `providers/xai_voice.py`.
- **Client:** `core/realtime_voice/static/realtime-voice-controller.js`
  (`RealtimeVoiceController`) — one shared controller used by both German
  and Portuguese today, built on top of
  `adapters/openai-webrtc-adapter.js` and `adapters/xai-websocket-adapter.js`.
  It is explicitly a **conversation** controller: it requires
  `{provider, persona, scene, learner_name}` to start a session, tracks
  multi-turn dialogue (`_items`, speaker-labeled), sends an
  `OPENING_INSTRUCTION` so the persona speaks first, and runs a fixed
  20/30-minute warning/cap timer. There is no "just transcribe, no
  response" mode in it today.

The reusable part is the transport (WebRTC/WebSocket connection handling,
reconnection, provider credential issuance, identity/rate-limit
enforcement). The non-reusable part is the conversational policy layer on
top of it. This spec's central question for Phase 1 is exactly how much of
the reusable part can be shared as-is, and what a memo-mode-specific policy
layer needs to look like.

## Goals

1. One shared backend session/transport layer for **both** memo and
   conversation mode — no second, parallel voice-session implementation.
2. Memo mode: continuous, pause-tolerant, live-as-you-speak transcription
   into a text field. A natural pause is never mistaken for "done" — the
   session ends only on explicit user action (click stop) or a safety
   duration cap.
3. No visible UI change at any memo-mode call site: same small mic icon in
   Schreiben, Escrita, Wörter, and Palavras today. No provider dropdown, no
   new controls. Backend-only change. (Provider choice/visibility is an
   open Phase 1 question, not decided here — see below.)
4. Consolidate all current memo/dictation implementations — German
   Schreiben (Tagebuch + Kontext), Portuguese Escrita (Diário + Contexto),
   German Wörter practice mic, Portuguese Palavras practice mic — onto the
   one shared memo controller. Stop the drift pattern that let #177 need
   fixing more than once in different places without the fix transferring.
5. Conversation mode (Gespräche/Conversas) is unaffected — this spec does
   not reopen that design, only decides what layer memo mode reuses from
   it.
6. Leave the door open for Chief of Staff Confer to reuse the existing
   conversation-mode stack later, without this build making that harder.

## Non-goals

- Not re-litigating or rebuilding the existing conversation-mode spec/build
  for Gespräche/Conversas.
- Not building Chief of Staff Confer's voice feature now — only ensuring
  memo mode doesn't foreclose it reusing the conversation-mode stack later.
- Not adding a user-facing provider toggle for memo mode. Robert's stated
  preference is no visible UI change; a toggle is only worth considering if
  there's a genuinely unobtrusive way to add one, which is an open question
  below, not a goal.
- Not changing the Wörter/Palavras practice mic's *interaction* beyond the
  transport swap — it stays "record an attempt, get it transcribed," now on
  shared infrastructure instead of ad hoc `MediaRecorder` + batch Whisper.
- Not adding live translation or correction during memo capture — Schreiben's
  existing post-capture "Korrigieren" step is unaffected and unrelated.

## Preconditions

1. **Stopgap, independent of this spec's timeline:** the already-built #177
   fix (continuous `SpeechRecognition` + auto-restart) should be redeployed
   to production now, regardless of how long this spec takes to review and
   build. Production should not stay broken while a bigger design is
   discussed.
2. This spec is reviewed by Codex and Grok before Robert approves a build
   handoff — his explicit request this round. Their independent read on the
   Phase 1 design questions below is the point of this precondition, not a
   formality.
3. Sequencing with the existing conversation-mode build
   (`feat/realtime-voice-shared-architecture`, unmerged) needs to be
   resolved before Phase 2 starts: does memo mode build as a sibling branch
   off the same point, wait for that branch to merge first, or something
   else? Flagged here, decided in Phase 1.
4. Cost awareness, mirroring the companion spec's own precondition:
   confirm real per-minute/per-session cost of a transcription-only
   realtime session before this becomes the default for what are likely
   short, frequent dictations (a Schreiben entry, a single practice
   answer) — a different usage pattern than conversation mode's longer
   sessions, and the cost math may not carry over directly.
5. Work happens on an isolated branch/worktree, dev-tested before any
   production deploy, per this project's standing workflow.

## Phase 1: Design details not yet resolved (read-only investigation before code)

This is the core of what Codex and Grok should weigh in on independently.

- **Does memo mode need a live model connection at all, or just streaming
  ASR?** OpenAI's Realtime API supports sessions configured with
  `modalities: ["text"]` (no audio output) and minimal/no `instructions` —
  effectively using it as a robust streaming-transcription service rather
  than a conversational one. Confirm whether this is genuinely simpler/
  cheaper than running a "silent" full conversation session, and whether
  the existing `bootstrap.py`/adapter code already supports a
  modalities-restricted session or needs extending. Confirm Grok Voice
  Agent API's equivalent (or absence of one) — this may be the deciding
  factor in the provider question below.
- **What does "turn boundary" mean with no response to trigger?**
  Conversation mode's server-side voice-activity detection ends a "turn" in
  order to trigger a model response. Memo mode has no response to trigger.
  Likely answer: every VAD-detected segment just appends transcribed text
  to the running memo, and the session itself never ends on a pause — only
  on explicit user stop. Confirm this against the real Realtime API
  turn-detection event semantics, not assumed from conversation mode's
  usage of them.
- **Session duration cap.** Conversation mode caps at 20–30 minutes. Memo
  entries and practice answers are typically short. Confirm a sensible
  (probably much shorter) cap using the same `DurationGuard` module,
  primarily to guard against an accidentally-left-open mic rather than to
  constrain a real use case.
- **Provider choice for memo mode.** Does memo mode need the same
  OpenAI/Grok toggle as conversation mode, or can it be hardcoded to one
  provider? Robert has said no visible toggle is needed here unless there's
  a discreet way to offer one — confirm whether that's a real technical
  constraint (e.g., only one provider supports a transcription-only mode)
  or purely a product choice, and if hardcoded, which provider and why.
- **Shared code shape.** Likely split: same `providers/*.py` credential/
  session logic, same client-side WebRTC/WebSocket adapters, and either a
  generalized `bootstrap.py` accepting a `mode: "memo" | "conversation"`
  parameter or a separate `bootstrap_memo.py` that reuses the same
  underlying provider/credential functions. Client-side: a new, much
  smaller `RealtimeMemoController` replacing `RealtimeVoiceController`'s
  persona/turns/opening-instruction logic with "accumulate transcript,
  expose it via a callback, stop on demand." Confirm this factoring against
  any better alternative — this is exactly the kind of decision worth an
  independent second and third opinion on before committing.
- **Exact migration scope.** Confirm this is the complete list, not missing
  a call site: German Schreiben (Tagebuch + Kontext mics), Portuguese
  Escrita (Diário + Contexto mics), German Wörter practice-answer mic,
  Portuguese Palavras practice-answer mic.
- **Real cost, not assumed.** Get actual per-session cost figures for a
  transcription-only realtime session vs. the batch-Whisper alternative
  already in production use (`/api/transcribe`) for short dictations.
  Confirm the live-as-you-speak requirement justifies realtime's cost over
  batch, rather than assuming it does.

### Required deliverable

A short design note (addendum to this spec or a follow-up doc) answering
the above, incorporating Codex's and Grok's review, before Phase 2 code
work begins. Robert approves the design note before Phase 2 starts.

## Phase 2: Build

Expected shape, subject to what Phase 1 resolves:

1. Server-side: generalize or add a sibling to
   `core/realtime_voice/bootstrap.py` for memo-mode sessions — identity
   check and rate limiting reused as-is, no persona/scene required, a
   modalities-restricted session request to the provider.
2. Client-side: new `RealtimeMemoController` reusing the existing
   WebRTC/WebSocket provider adapters, replacing the conversational
   turn-loop with a streaming-transcript-to-callback loop. Session ends
   only on explicit stop or the (shorter) duration guard.
3. Migrate all four current call sites (German Schreiben, Portuguese
   Escrita, German Wörter, Portuguese Palavras) onto the new controller,
   removing the old per-page `SpeechRecognition`/`MediaRecorder`
   implementations entirely.
4. No UI change beyond swapping the mic button's backend wiring — same
   icon, same placement, no provider selector.
5. Explicitly out of this build, noted for later: Chief of Staff Confer
   reuses the *existing* conversation-mode controller/bootstrap once that
   feature is separately spec'd and approved.

## Required verification

- Manual test: dictate into Schreiben and Escrita with a natural mid-thought
  pause of 15–20+ seconds — confirm no cutoff and confirm text still
  appears progressively as spoken, not only after stopping.
- Manual test: Wörter and Palavras practice mics still correctly capture a
  spoken answer into the practice input.
- Confirm zero visible UI/UX change at any of the four call sites beyond
  reliability.
- Confirm the session ends cleanly on explicit stop, and on the duration
  guard if a mic is left open accidentally.
- Cost check: real per-session cost logged and reviewed against the
  batch-Whisper alternative before this becomes the permanent default —
  mirrors the companion spec's cost-visibility acceptance criterion.
- Full test suite, plus dev-only live testing, before any production
  deploy — per this project's standing workflow.

## Acceptance criteria

1. All four current memo/dictation call sites (German Schreiben, Portuguese
   Escrita, German Wörter, Portuguese Palavras) run on one shared
   memo-mode controller and backend — zero independent
   `SpeechRecognition`/`MediaRecorder` implementations left among them.
2. A natural mid-dictation pause of at least 15–20 seconds never ends the
   session or cuts off capture; only an explicit stop or the duration
   guard ends it.
3. No new visible UI at any of the four call sites — same small mic icon,
   no provider selector, no layout change.
4. Memo mode and conversation mode share the transport/provider-adapter/
   session-bootstrap layer — verified by code review, not just by both
   happening to work independently.
5. Real cost per typical memo session is known and acknowledged by Robert
   before this becomes the default in production.
6. Chief of Staff Confer is not blocked or made harder to build later by
   any memo-mode-specific choice made in this build.

## Build-queue registration

- **Title:** Shared realtime voice architecture — memo (dictation) mode
- **Status:** `spec_ready`
- **Priority:** `normal` — paired with an independent, immediate stopgap
  (redeploy the already-built #177 `SpeechRecognition` fix now, not gated
  on this spec)
- **GitHub issue:** [#177](https://github.com/robertvanstedum/personal-ai-agents/issues/177)
- **Blocked reason:** Sequencing against the unmerged companion build
  (`feat/realtime-voice-shared-architecture`) needs to be resolved in
  Phase 1 — awaiting Codex/Grok review and Robert's go-ahead to start
  Phase 1 design-detail resolution.
- **Notes:** Direct outcome of Robert reproducing the #177 cutoff bug in
  production after a targeted fix was built but never deployed, and his
  follow-up framing that the real issue is architectural drift across
  independent voice-input implementations, not a single bad regex. Sent to
  Codex and Grok for independent review before a build handoff is approved.
