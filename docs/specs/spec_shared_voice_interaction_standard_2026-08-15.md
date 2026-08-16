# Specification: Shared Voice Interaction Standard

**Date:** 2026-08-15
**Status:** Approved baseline; COS conversation conformance in development
**Owner:** Robert
**Scope:** All current and future mini-moi domains

## Purpose

Voice behavior is selected by interaction mode, not rebuilt by each domain.
Domains own prompts, language, and the destination for a completed transcript.
The shared voice layer owns browser transport, ephemeral authentication, turn
boundaries, interruption, transcription events, duration guards, and errors.

## Standard modes

### Conversation

Used by Gespräche, Conversas, and COS Confer.

- provider-side voice activity detection owns speech boundaries;
- the platform baseline is 300 ms prefix padding and 1200 ms silence;
- natural pauses must not be managed by a domain-specific browser RMS timer;
- speech-start and speech-stop events have the same shared contract;
- interruption and continuous exchange are supported;
- a direct speech-to-speech domain may let the provider create its response;
- a chained domain such as COS sets `create_response: false`, sends the final
  transcript to its configured agent runtime, and speaks only that canonical
  platform-owned reply.

OpenAI documents server VAD for supported transcription and Realtime sessions:
https://developers.openai.com/api/docs/guides/realtime-vad#server-vad

Current capability evidence: `gpt-live-transcribe` rejects turn detection, so
COS uses a Realtime session with `gpt-4o-transcribe`, server VAD, and provider
response creation disabled. This preserves COS Agent A as the only reasoning
agent while reusing the proven Gespräche-style conversation boundary.

### Memo or dictation

Used by Lesen, Schreiben, Leitura, and Escrita.

- the user explicitly starts and stops recording;
- silence never commits, submits, or ends the memo;
- transcription is insertion-only and must not answer, translate, or summarize;
- long pauses, self-correction, and restarts are expected behavior.

### Command

Reserved for future short, explicit commands.

- it may use a tighter silence boundary than conversation mode;
- it must be visibly identified as command mode;
- no existing conversation or memo surface may silently adopt command timing.

## Conformance rules

1. A new voice surface declares exactly one standard mode.
2. Domain templates use shared controllers and adapters; provider protocol does
   not live in domain code.
3. Long-lived provider credentials remain server-side. Browsers receive only
   short-lived credentials.
4. A chained voice provider cannot generate a competing assistant response.
5. The completed transcript uses the same domain turn service, session identity,
   authority policy, error contract, and write deduplication as typed input.
6. Audio and full transcripts are not logged as operational telemetry.
7. Provider/model choice remains configurable inside the capability layer.
8. Every mode requires automated contract tests and a real microphone acceptance
   test before production promotion.

## Current mapping

| Surface | Mode | Turn owner | Reasoning owner |
|---|---|---|---|
| Gespräche | Conversation | Realtime provider | Realtime provider/persona |
| Conversas | Conversation | Realtime provider | Realtime provider/persona |
| COS Confer | Conversation | Realtime provider | COS Agent Runtime |
| Lesen / Schreiben | Memo | Robert presses Stop | None during capture |
| Leitura / Escrita | Memo | Robert presses Stop | None during capture |

## Acceptance baseline

- conversation: a natural one-second pause does not create a fragment;
- memo: a multi-second pause does not submit or end capture;
- chained conversation: provider response generation is disabled and exactly one
  domain-agent turn follows each completed transcript;
- typed and voice paths produce the same session, authority, and note behavior;
- failure is visible and never produces a fabricated transcript or action receipt.
