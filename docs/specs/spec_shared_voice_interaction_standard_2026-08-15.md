# Specification: Shared Voice Interaction Standard

**Registered:** Build queue #149
**Date:** 2026-08-15
**Status:** Approved baseline; COS production conformance accepted 2026-08-16
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
- voice-first surfaces do not render a competing live transcript; the completed
  transcript is surfaced after the voice session ends;
- COS uses the same direct realtime conversation loop as Gespräche and Conversas.
  Its selected realtime provider owns low-latency speech, turn-taking, and
  interruption. Allow-listed function tools consult COS Agent A for current
  facts, research, durable context, and substantive domain judgment, and use the
  platform-owned note path for explicit writes.

OpenAI documents server VAD for supported transcription and Realtime sessions:
https://developers.openai.com/api/docs/guides/realtime-vad#server-vad

COS offers the same explicit provider choice as Gespräche and Conversas:
OpenAI Realtime or Grok Voice. Long-lived keys remain server-side; the browser
receives a short-lived provider credential. Typed Confer continues to address
COS Agent A directly. Voice Confer is the selected realtime voice model with
bounded, platform-owned tools into Agent A and notes. This division must be
visible in implementation and release documentation; it must never imply that
every realtime utterance made a separate OpenClaw round trip.

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
4. A voice surface may expose only providers with supported browser transport
   and short-lived authentication in the shared capability registry.
5. Agent consultations and mutations initiated from voice use the same domain
   turn service, session identity, authority policy, error contract, and write
   deduplication as typed input.
6. Audio and full transcripts are not logged as operational telemetry.
7. Provider/model choice remains configurable inside the capability layer.
8. Every mode requires automated contract tests and a real microphone acceptance
   test before production promotion.

## Current mapping

| Surface | Mode | Turn owner | Reasoning owner |
|---|---|---|---|
| Gespräche | Conversation | Realtime provider | Realtime provider/persona |
| Conversas | Conversation | Realtime provider | Realtime provider/persona |
| COS Confer | Conversation | Realtime provider | Realtime provider, with allow-listed COS Agent Runtime tools |
| Lesen / Schreiben | Memo | Robert presses Stop | None during capture |
| Leitura / Escrita | Memo | Robert presses Stop | None during capture |

## Acceptance baseline

- conversation: a natural one-second pause does not create a fragment;
- memo: a multi-second pause does not submit or end capture;
- COS conversation: OpenAI and Grok can each complete a natural multi-turn
  session, and barge-in stops the active reply;
- COS transcript: turns are added to Confer only after voice ends;
- COS tools: current/research/context requests can consult Agent A, while an
  explicit note request receives a platform receipt before success is spoken;
- typed and voice tool paths use the same authority and note behavior;
- failure is visible and never produces a fabricated transcript or action receipt.
