# Spec: Shared Voice Layer for Writing and Chief of Staff Confer

**File:** `docs/specs/spec_145_shared_voice_layer_2026-08-08.md`
**Date:** 2026-08-08
**Status:** Approved to build
**Owner / decision point:** Robert
**Build queue:** #145
**GitHub issue:** [#177](https://github.com/robertvanstedum/personal-ai-agents/issues/177)
**Roadmap / governing spec:** `ROADMAP.md` §3, Chief of Staff — partner build, step one; Guild #133, mini-moi Intelligence Layer
**Dependencies / blockers:** Journal memo is not blocked; OpenClaw-backed Confer depends on Guild #135
**Reviews:** Grok 2026-08-08 (design); Claude Code 2026-08-08 (repository/API fact-check); Robert 2026-08-08 (approved to build)
**Implementation:** Claude Code or Codex; reviewed implementation diff and separate Robert ship approval required
**Related work:** `docs/specs/spec_voice_realtime_architecture_2026-07-24.md`; gated German/Portuguese conversation-mode build

## 1. Decision summary

Build one shared **voice I/O layer**, with two deliberately different product
policies on top:

1. **Journal memo mode** for German Schreiben and Portuguese Escrita is
   one-way, live transcription into the existing writing field. It does not
   invoke an agent, generate a reply, or end because the speaker pauses.
2. **Confer voice mode** is a full spoken conversation with Chief of Staff.
   Speech input and output are only channels. Every user turn still passes
   through mini-moi's permanent CoS coordination layer and its configured,
   swappable agent backend. OpenClaw is the first intended standalone backend;
   it is not fused to the microphone implementation and can be replaced later.

The same small microphone control can start either experience, but the two
experiences must not be collapsed into one controller. Shared code belongs in
audio capture, connection lifecycle, transcription, speech playback,
authentication, and observability. Memo accumulation, conversational turn
handling, agent execution, memory, and tool permissions remain separate policy
layers.

This is a **platform Voice capability**, not a German, Portuguese, or CoS
feature that happens to be copied elsewhere. Domains select a mode and provide
their own language, prompt/persona data, vocabulary hints, labels, and
persistence callbacks. They do not implement or select their own audio
transport.

One platform capability does not require every provider to use one identical
internal path. OpenAI and xAI may each have a provider module containing the
adapters their published APIs require. Grok Voice can therefore remain a
first-class native conversation option while xAI's separate speech-to-text and
text-to-speech adapters serve memo and chained agent-conversation modes. The
shared contract, normalized events, policy boundaries, and capability registry
remain common.

This spec is an addendum to Guild #133 and #135, not a competing OpenClaw
initiative. It does not add a second CoS architecture or a second OpenClaw
build-queue item.

### Decisions already made

- Voice is owned by the platform and exposed through modes, not copied into
  each domain.
- Memo and conversation are separate interaction policies over shared capture,
  transcript, playback, authentication, and observability services.
- Provider-specific modules are allowed when an API genuinely differs. They
  implement the shared capability contract; they do not create a second domain
  architecture.
- OpenAI and xAI are both valid voice providers where their declared
  capabilities satisfy the selected mode.
- Day-one implementation targets both OpenAI and xAI for journal memo and
  chained CoS Confer so Robert can compare them in real use before choosing a
  near-term preference. A concrete provider blocker may reduce the first
  release to one without holding the production defect indefinitely.
- Grok Voice remains available for native persona conversation. A specialized
  native Grok path for CoS is allowed only if it preserves the permanent CoS
  coordination, memory, permission, tagging, and audit boundaries.
- Voice provider and CoS agent backend are separate selections. OpenClaw is an
  agent backend, not a microphone provider.
- Journal memo mode has no selector beside each microphone. During the A/B
  period, a shared platform Voice preference selects OpenAI or xAI for writing.
  Confer exposes a discreet per-session voice choice; Gespräche and Conversas
  retain their per-session native voice choice.
- Raw audio is not retained by default. Transcript text is the canonical input
  and record.

## 2. Context and evidence

### The production defect

Issue #177 records that the Schreiben microphone stops after a natural pause.
German Schreiben and Portuguese Escrita currently rely on browser-native
`SpeechRecognition`, whose lifecycle varies across browsers and devices. A
targeted continuous-listening patch was built in dev but did not ship. Robert's
direction is to solve the shared design properly, not make that browser patch
the planned production solution.

The writing microphone is not a conversation. It is a voice memo that becomes
editable text in a journal or context field. Its product contract is therefore
simple: listen until Robert explicitly stops it, show text while he speaks,
and tolerate the pauses that occur while composing a thought.

### The CoS need is different

Confer already has a microphone, but its current behavior is a batch operation:
record audio, stop after a short silence, transcribe the clip, then submit one
text message. That does not provide the mobile, back-and-forth conversation
described in the roadmap.

Confer needs a continuous conversational experience with visible listening,
thinking, and speaking states; interruption while CoS is speaking; continuity
between typed and spoken turns; and safe recovery from mobile network changes.
Most importantly, the voice provider must not silently become the Chief of
Staff. CoS reasoning, memory, access, and action policy must remain behind the
existing mini-moi boundary.

## 3. Roadmap and governing architecture

### Roadmap §3: Chief of Staff — partner build, step one

This specification adopts the roadmap's full CoS contract:

- **A place to converse.** The conversation surface becomes channel-agnostic.
  HTML text, HTML voice, and Telegram are input channels into one conversation;
  the channel does not determine which CoS is reached or which memory applies.
- **Light tagging.** Actions, decisions, and risks arising in conversation are
  lightly tagged by domain, actor, type, and time. This remains a side effect of
  talking, not a formal record Robert must maintain.
- **First focus: access.** CoS can inspect and talk to domains for information
  and, where a domain explicitly exposes a permitted capability, instruct it to
  act. Voice receives no special bypass around this permission model.
- **A bounded experiment.** The committed work tests whether a useful working
  relationship can be established. Expanded autonomy and periodic review wait
  for evidence from that experiment.
- **Bounded OpenClaw instance.** OpenClaw is the intended Phase 1 agent layer,
  scoped to mini-moi domains under the permission model.
- **The knows-me corpus begins.** Background, plans, and risks are added
  intentionally over time. Voice and text contribute to the same corpus under
  the same memory-worthiness rules; raw always-on audio is not a new memory
  source.

### Guild #133: permanent coordination, swappable intelligence

Guild #133 and `config/cos_interface.md` remain authoritative. The permanent
platform-owned layer is responsible for identity, routing, conversation scope,
context assembly, memory format and location, tool policy, tagging, and audit.
The backend supplies agent capability behind that boundary.

The first backend target is OpenClaw. Grok remains a compatible fallback while
the OpenClaw path is brought up, and a future backend can replace either one
without changing the browser microphone, the conversation record, or the
permission model.

### Guild #135: OpenClaw #2 dependency

**OpenClaw #2 — CoS-Scoped Gateway, Dev Setup** is a prerequisite for OpenClaw-
backed Confer. It is currently blocked until OpenClaw supplies all four items:

1. the supported npm install command;
2. the CLI start command;
3. the minimal configuration schema; and
4. the `_collect_response` protocol answer.

The bounded gateway must be installed and pass a text-only CoS conformance test
before voice is connected to it. Voice work must not guess these details,
embed an unofficial OpenClaw launch path, or make an agent runtime dependency
part of the shared audio controller.

## 4. Goals and scope

### Goals

- Replace fragile browser-native journal dictation with one pause-safe platform
  memo capability shared by German and Portuguese writing.
- Give CoS Confer a mobile-capable spoken channel without changing CoS identity,
  memory, permissions, tagging, or agent-backend ownership.
- Launch with OpenAI and xAI available for A/B comparison in both writing and
  chained Confer, while preserving a durable provider swap point.
- Reuse the gated branch's domain-neutral transcript, cost, bootstrap, and
  adapter work without importing its language-persona policy into CoS.

### Non-goals

- Rebuilding Gespräche or Conversas during the first journal/Confer delivery.
- Making OpenClaw a browser-visible voice provider.
- Giving voice-originated instructions more authority than typed instructions.
- Storing raw audio, enabling wake-word listening, or expanding CoS autonomy.

### In scope: journal memo mode

Primary call sites:

- German Schreiben: **Tagebuch** and **Kontext** fields;
- Portuguese Escrita: **Diário** and **Contexto** fields.

The existing small microphone icon and placement remain. Tap starts capture;
tap again stops and finalizes it. A compact state treatment on that same
control may indicate requesting permission, listening, reconnecting, or error.
There is no provider selector beside the microphone and no new journal panel.
A shared platform Voice preference provides the temporary OpenAI/xAI A/B
choice without duplicating controls across writing fields.

German Wörter and Portuguese Palavras practice-answer microphones are not part
of the first migration. They already have a bounded push-to-talk contract and
working batch transcription. They may later reuse generic capture utilities if
that reduces code without changing their exercise behavior. They must not be
pulled into this build merely to achieve architectural symmetry.

### In scope: CoS Confer voice

- start and stop a spoken CoS session from the existing Confer surface;
- stream transcription so Robert can see what CoS heard;
- route completed user turns through the permanent CoS coordination layer;
- use the configured `AgentBackend`, with bounded OpenClaw as the intended
  Phase 1 backend and another backend selectable by deployment configuration;
- stream or progressively present the response and speak it aloud;
- support interruption/barge-in while CoS is speaking;
- keep typed and spoken turns in the same conversation record;
- work credibly on mobile, including Bluetooth audio, permission handling,
  reconnect, and accidental-open-microphone protection;
- preserve light tagging, domain access controls, memory rules, and auditing
  regardless of input channel.

### Future scope: Gespräche, Conversas, and new domains

The gated Gespräche/Conversas realtime build already points in the right
direction: one shared controller, provider adapters for OpenAI and xAI, and
domain-supplied locale/persona/scene data. It remains untouched during the
journal and Confer work so a platform refactor does not destabilize a working
language-learning experience.

After the shared Voice capability is proven, migrate Gespräche and Conversas by
moving their remaining domain assumptions out of `core/realtime_voice` and into
mode configuration. Their product behavior remains native, low-latency
speech-to-speech with an AI persona; the migration changes ownership and reuse,
not the experience.

The intended platform modes are:

- **memo** — continuous audio to editable text, no model response;
- **practice** — bounded spoken attempt to text, optionally followed by
  correction/review;
- **persona conversation** — native speech-to-speech model embodies a language
  persona, as in Gespräche/Conversas;
- **agent conversation** — transcription and speech surround an existing text
  agent, as in CoS/OpenClaw;
- later modes such as live translation may be added through the same capability
  registry without adding domain-specific transports.

New domains should be able to adopt voice by declaring a mode and a small
domain configuration rather than copying JavaScript, bootstrap routes, or
provider APIs.

### Out of scope

- changing the German Gespräche or Portuguese Conversas persona experience;
- making OpenClaw a browser-visible provider choice;
- giving voice-originated commands broader authority than typed commands;
- storing raw audio by default;
- automatic background listening or wake-word activation;
- expanded CoS autonomy beyond the roadmap's bounded experiment;
- redesigning Wörter/Palavras practice input in the first build;
- merging or shipping any code under this document before the separate review
  and approval gates.

### Expected code surface

Verified existing implementation surfaces:

- `domains/german/templates/german_schreiben.html` and its writing-page CSS -
  German Tagebuch/Kontext microphone and current browser transcription path.
- `domains/portuguese/templates/portuguese_escrita.html` and its writing-page
  CSS - Portuguese Diario/Contexto microphone and current browser
  transcription path.
- `domains/cos/templates/cos_ui.html` - existing Confer text, microphone, and
  recording surface.
- `domains/cos/chief_of_staff.py` - permanent CoS coordination and current
  `/ui/transcribe` route.
- `domains/cos/backends/grok_backend.py` and
  `domains/cos/backends/openclaw_backend.py` - swappable agent-backend boundary.
- `core/realtime_voice/bootstrap.py`, `config.py`, `transcript.py`, `cost.py`,
  and `duration_guard.py` on the gated
  `feat/realtime-voice-shared-architecture` branch - reusable platform
  foundation to reconcile, not merge blindly.
- `core/realtime_voice/providers/openai_realtime.py`,
  `core/realtime_voice/providers/xai_voice.py`, and the branch's OpenAI/xAI
  browser adapters and shared controller - provider work to assess against the
  mode and capability contracts in this spec.

## 5. Design contract

### Four layers

#### 1. Channel and UI

The page owns the microphone control, text field, transcript display, and
accessible state labels. It does not know which agent runtime is active.

- Journal pages expose a memo capture control attached to an editable field.
- Confer exposes text and voice as equal ways to add a user turn.
- Telegram remains another CoS channel with the same downstream routing.

#### 2. Shared voice I/O

Provider-specific audio mechanics live behind narrow adapters:

- microphone permission and capture;
- WebRTC/WebSocket connection lifecycle;
- streaming transcription events;
- optional speech generation and playback;
- interruption and audio-buffer cancellation;
- reconnect, duration guards, metrics, and safe shutdown.

No adapter owns a persona, a CoS prompt, domain permissions, memory, tool
policy, or the definition of a completed business action.

Provider modules may contain several adapters rather than pretending every API
has one universal connection shape. For example, the xAI module may expose:

- an xAI streaming STT adapter for memo and chained agent conversation;
- an xAI streaming TTS adapter for chained agent conversation; and
- a native Grok Voice adapter for persona conversation and any later,
  separately proven native-agent mode.

The OpenAI module may likewise expose dedicated transcription, speech
generation, and native Realtime adapters. Shared controllers depend on the
declared capability and normalized event contracts, not on a provider's file
layout or wire protocol.

The shared module must be domain-neutral. In particular, provider allow-lists,
connection setup, and normalized events belong to the platform; locales,
personas, scenes, terminology hints, and save/review callbacks are passed in by
the domain or application mode. The current German/Portuguese locale allow-list
is an implementation artifact to remove during later consolidation, not a
platform boundary.

#### 3. Interaction policy

Two policy controllers consume the shared adapters:

- `RealtimeMemoController` accumulates transcript deltas into one editable
  memo and finalizes only on explicit stop or the duration safety cap.
- `ConferVoiceController` manages conversational turns, visible state,
  response playback, interruption, and recovery. It hands text turns to the
  same CoS application service used by typed Confer.

The existing language conversation controller may share low-level adapters,
but it is not automatically the right Confer controller. In Gespräche and
Conversas, the realtime voice model can embody the chosen persona. In Confer,
OpenClaw or the configured backend must remain the reasoning agent.

#### 4. CoS coordination and agent runtime

Target Confer flow:

```text
browser microphone
  -> streaming transcription
  -> Confer channel/session adapter
  -> chief_of_staff.py coordination
       identity + context + tags + memory policy + tool policy
  -> configured AgentBackend
       OpenClawBackend first target; Grok/future backend swappable
  -> text response/events
  -> speech generation and browser playback
  -> shared conversation record
```

OpenClaw runs as a bounded standalone service behind the CoS backend interface.
The browser never connects directly to OpenClaw and the speech service never
calls domain tools directly.

The current synchronous backend contract, `call_backend(...) -> str`, remains
valid for typed chat and compatibility. Confer voice will probably require an
optional streaming contract, for example `stream_backend(...) -> events`, with
a synchronous adapter for backends that do not stream. The exact event schema
must be decided alongside #133/#135 after the OpenClaw protocol answer arrives;
this spec does not pre-empt it.

### Provider and backend selection are separate

The word “model” currently hides two independent choices:

1. **Voice provider** — captures speech, determines turn boundaries, supplies
   transcription, and/or generates spoken audio. This primarily affects
   naturalness, latency, interruption behavior, language quality, and cost.
2. **Agent backend** — reasons, remembers, uses tools, and produces the response
   text. This primarily affects judgment, personality, domain access, and
   action quality.

For native persona conversation, one realtime provider performs both jobs.
Therefore Gespräche and Conversas can continue to offer one per-session
selector: **OpenAI Realtime** or **Grok Voice**. The existing gated build sends
that explicit choice to a shared provider resolver; its planned precedence is
explicit session choice, saved user preference, deployment default, then
application default.

For CoS, the selections must remain independent. Confer should offer a discreet
**Voice: OpenAI / Grok** session choice because Robert wants to compare
smoothness and quality. For the standard chained path, that means the selected
provider supplies STT and TTS while the configured CoS backend supplies
reasoning. Separately, the active CoS backend remains visible as a status/badge
or advanced operator setting: **Agent: OpenClaw / Grok / future**. Changing
Voice must not change the CoS identity, memory, or permissions; changing Agent
must not require a new microphone implementation.

The existing OpenAI and xAI adapters on the gated language branch are native
voice-agent adapters: their realtime model hears, reasons, and speaks. They are
appropriate for persona conversation but cannot simply be connected to
OpenClaw-backed CoS without turning the voice provider into a second agent.
Confer's standard path therefore uses the providers' decoupled speech services:
streaming speech-to-text, the configured CoS backend for reasoning, then
streaming text-to-speech. Both providers currently publish the necessary
service shapes:
[OpenAI's chained voice architecture](https://developers.openai.com/api/docs/guides/voice-agents#choose-the-right-architecture),
[xAI streaming speech-to-text](https://docs.x.ai/developers/models/speech-to-text),
and [xAI streaming text-to-speech](https://docs.x.ai/developers/models/text-to-speech).
The xAI STT and TTS documentation was rechecked on August 8, 2026; it describes
standalone streaming WebSocket services rather than requiring the coupled Grok
Voice agent. This path still needs new adapter capabilities because the current
native language adapters are not already a complete CoS implementation.

Robert's preference for Grok Voice quality remains a supported design goal.
The xAI provider module should first support the standard chained path. A later
**native Grok CoS** profile may combine a Grok Voice session with a corresponding
CoS backend adapter, but it must enter through the CoS application boundary and
produce the same canonical user turn, assistant turn, tags, permission results,
memory decisions, and audit events. If that cannot be demonstrated, native
Grok Voice remains available for persona conversation and the xAI chained path
serves Confer instead. Quality does not justify bypassing the CoS contract.

Journal memo mode does not need a provider selector beside the microphone. For
the initial A/B period, a shared platform Voice preference makes OpenAI and xAI
available and persists Robert's current choice across writing fields. After a
near-term preference is selected, it becomes the default, but both provider
adapters remain replaceable and may be re-enabled or swapped later. Any mode
can constrain the list to capabilities it actually supports.

Implement selection through a platform capability registry rather than
domain-specific `if provider == ...` logic. Each provider advertises support
for such features as streaming transcription, streaming text-to-speech, native
persona conversation, native agent conversation, interruption,
WebRTC/WebSocket transport, browser-direct ephemeral authentication,
server-proxy requirements, and target locales. The session bootstrap validates
the requested mode against that registry and returns normalized events to the
controller. Unsupported combinations fail closed and are not shown as choices.

The first implementation should reuse the gated branch's genuinely neutral
modules, including `core/realtime_voice/transcript.py` for normalized transcript
accumulation and `core/realtime_voice/cost.py` for the shared cost-log path. It
must not lift the language persona prompt/controller into memo or CoS merely
because it already speaks to both providers.

## 6. User flow: journal memo

Use a dedicated realtime transcription service, not a silent conversational
agent. Current OpenAI documentation distinguishes a transcription session from
a Realtime voice-agent session: transcription produces streaming text without
model-generated replies. xAI likewise publishes a standalone streaming STT
service. Provider-specific segmentation is allowed, but a speech-final or
utterance-final event must never end the memo controller. The controller keeps
accepting later utterances until the user explicitly stops or the duration
safety cap is reached.

For OpenAI, disable server turn detection where supported so a natural pause
does not auto-commit a turn. For xAI, normalized final utterance events may be
accumulated while the STT session remains open. These are provider-specific
mechanics behind one memo contract, not different domain behaviors.

Expected event handling:

1. request a short-lived, authenticated transcription session;
2. start microphone capture after explicit user action;
3. display transcript deltas progressively;
4. reconcile provisional text using finalized transcript events and their item
   identifiers;
5. on stop, use the selected provider's flush/finalize operation, wait briefly
   for the final transcript, then close;
6. leave the resulting text editable in the existing field;
7. on recoverable network failure, show reconnecting state and preserve already
   finalized text rather than clearing the field.

Start dev with a 15-minute duration safety cap. It guards against an
accidentally open microphone rather than defining a normal memo length. Record
real writing-session durations and tune the cap before production if it cuts
off legitimate use or leaves capture open unnecessarily.

Provider documentation establishes that transcription can remain separate
from agent replies; it does not by itself prove that every browser, network,
and provider session survives a long pause. The 20-second pause requirement is
therefore an empirical real-device acceptance test, not a documentation-only
assumption.

Official implementation references:

- [Realtime transcription](https://developers.openai.com/api/docs/guides/realtime-transcription)
- [Realtime API](https://developers.openai.com/api/docs/guides/realtime)
- [Audio and speech](https://developers.openai.com/api/docs/guides/audio)
- [xAI speech to text](https://docs.x.ai/developers/models/speech-to-text)
- [xAI text to speech](https://docs.x.ai/developers/models/text-to-speech)

The day-one target is both OpenAI and xAI, selectable through the shared Voice
preference rather than on each journal field. The memo controller consumes
normalized transcript events and the capability registry selects only a
provider that supports dedicated streaming transcription. If one provider hits
a concrete technical or operational blocker, record the blocker, hide that
unavailable choice, and release the proven provider rather than delay the live
defect fix. The blocked adapter remains follow-up work, not a reason to hardwire
the successful provider into journal pages.

## 7. User flow: CoS Confer

### One conversation, independent of channel

A Confer conversation has a stable conversation/session identifier. Typed
HTML, spoken HTML, and Telegram turns enter the same application-level turn
pipeline. Channel metadata is recorded for diagnosis and presentation, but it
does not select a different memory or agent personality.

### Turn lifecycle

1. Robert explicitly starts voice mode.
2. Audio is transcribed progressively and shown as provisional text.
3. A conversational turn boundary finalizes the user turn; unlike journal
   mode, this may use VAD because a turn is expected to produce a reply.
4. The finalized text enters `chief_of_staff.py` exactly as a typed turn would.
5. CoS assembles context, applies access/tool policy, invokes the configured
   backend, and records light tags/audit information.
6. Response text is shown and spoken. Text is the canonical conversation
   artifact; audio is a presentation channel.
7. If Robert speaks while CoS is talking, playback is cancelled, the new input
   is captured, and the interruption is represented cleanly in the turn record.

An interrupted assistant turn is recorded with an explicit
`interrupted`/`cancelled` status and, where available, the text that was actually
delivered before cancellation. The new user speech becomes a normal subsequent
turn. The two turns are never silently merged and an interrupted response is
never recorded as fully delivered.

The initial release should prefer explicit, understandable states over an
invisible always-listening loop: **Listening**, **Thinking**, **Speaking**,
**Reconnecting**, and **Stopped/Error**. A discreet status treatment is enough;
the page does not need a provider dashboard. The microphone control and each
state have accessible labels/ARIA text so the same compact control remains
usable with assistive technology in memo and conversation modes.

### Mobile requirements

- Chrome and Safari permission flows tested on real phones;
- Bluetooth headset and handset audio routing checked;
- clear recovery after tab suspension, network loss, or device handoff;
- no claim of continuing to listen when the mobile browser has suspended audio;
- duration guard and obvious stop control;
- no raw audio retention unless a later, separately approved feature requires
  it;
- transcript remains available when speech playback is muted or fails.

## 8. Security, privacy, and memory

- Voice session credentials are short-lived and issued only after normal
  mini-moi identity and authorization checks.
- Long-lived provider credentials never reach the browser.
- Audio transport and agent/tool authorization are separate checks.
- The transcript, not raw audio, is the canonical input to CoS unless Robert
  later approves audio retention.
- Voice turns use the same memory-worthiness and intentional knows-me rules as
  typed turns. Speaking does not imply automatic long-term memory.
- All domain reads and actions remain constrained by the platform permission
  model and are auditable with their originating channel and actor.
- The OpenClaw instance remains scoped to mini-moi; it does not gain host-wide
  or browser-direct authority through this feature.

## 9. Dependencies, blockers, and open questions

### Dependencies and blockers

- **Journal memo mode is not blocked by OpenClaw.** It may begin after the
  unmerged shared-voice branch is reconciled and the first transcription
  provider's secure session/bootstrap path is confirmed.
- Guild #135's four OpenClaw answers and text-only conformance block only the
  acceptance of **OpenClaw-backed Confer**. They do not block the journal fix or
  early Confer validation through the existing Grok backend.
- The optional backend streaming schema belongs to #133/#135. Its absence does
  not block a synchronous first Confer path.
- Provider capabilities, authentication shape, event normalization, limits,
  and prices must be rechecked against official documentation during build;
  voice APIs are changing quickly.

### Open questions requiring a decision

1. **Backend streaming events:** What optional event contract, if any, should
   extend `call_backend(...) -> str` under Guild #133/#135? Phase 2 must not
   invent a parallel schema. A synchronous response remains a first-class
   fallback even after streaming is added.
2. **Native Grok CoS profile:** Can a native Grok Voice session participate
   through the permanent CoS application/backend boundary while preserving the
   canonical record, tags, memory decisions, permissions, and audit? If not,
   Confer uses xAI's separate STT and TTS services around the configured agent
   backend and native Grok Voice remains a persona-conversation capability.
3. **Browser transport per provider:** Which provider modes support safe
   browser-direct ephemeral credentials and which require a mini-moi WebSocket
   proxy? The capability registry must declare this; long-lived provider keys
   never reach the browser.

## 10. Delivery and rollback

### Delivery sequence

### Phase 0A — reconcile the shared voice foundation

1. Reconcile reusable low-level code with the unmerged
   `feat/realtime-voice-shared-architecture` branch. Reuse adapters where the
   contract is genuinely generic; do not merge the language persona controller
   into CoS merely to avoid a second policy class.
2. Retain and extend the normalized transcript and cost-log modules rather than
   rebuilding those functions in a journal or CoS directory.
3. Define the provider capability registry, including provider-specific
   transport and authentication requirements.

Phase 1 depends on Phase 0A only.

### Phase 0B — close CoS/OpenClaw prerequisites

1. Obtain the four missing OpenClaw answers recorded under Guild #135.
2. Install and run the bounded OpenClaw #2 gateway in dev using the approved
   configuration.
3. Complete a text-only conformance path through `chief_of_staff.py` and
   `OpenClawBackend` before adding OpenClaw-backed voice.
4. Confirm the backend boundary and any streaming event extension under Guild
   #133, including fallback behavior when a backend cannot stream.

Phase 0B may proceed independently. It blocks OpenClaw-backed Confer acceptance,
not Phase 1 journal memo mode.

### Phase 1 — journal memo mode

1. Add authenticated bootstrap for dedicated realtime transcription sessions.
2. Add the shared memo controller and normalized transcript event model.
3. Implement and validate both OpenAI and xAI transcription adapters, plus the
   shared Voice preference used for the A/B period.
4. Migrate German Schreiben Tagebuch/Kontext and Portuguese Escrita
   Diário/Contexto together.
5. Remove browser `SpeechRecognition` from those journal call sites after the
   new path passes dev testing.
6. Leave Wörter/Palavras batch transcription unchanged.

Both providers are the Phase 1 target. If one cannot pass its provider and
real-device checks, document the blocker and release the passing provider while
keeping the registry and controller provider-neutral.

### Phase 2 — CoS Confer voice

1. Add the channel-neutral CoS turn/session service used by typed and spoken
   Confer.
2. Use the existing synchronous backend contract first; add the optional
   streaming contract only if it has been decided under #133/#135.
3. Add `ConferVoiceController`, streaming transcription, response speech,
   cancellation, state display, and reconnect behavior.
4. Implement and validate the standard chained voice path with both OpenAI and
   xAI STT/TTS, exposed through the discreet Confer voice selector.
5. Validate the standard chained path first with the existing Grok backend,
   then with bounded OpenClaw once
   the #135 text conformance gate passes. This separates voice failures from
   agent-gateway failures.
6. Validate that text, voice, and Telegram produce equivalent CoS routing,
   tagging, access checks, and memory behavior.
7. If a native Grok CoS profile is pursued, treat it as a provider-specific
   module and require the same conformance evidence before it is selectable.

Both chained voice providers are the Phase 2 target. A concrete blocker in one
provider may reduce the initial Confer release to the other without changing
the CoS backend contract or removing the future swap point.

### Phase 3 — evaluate and consolidate

1. Review whether spoken Confer is useful in the roadmap's actual daily scope.
2. Measure latency, transcription quality, interruptions, errors, and cost.
3. Decide whether Wörter/Palavras benefit from shared capture utilities.
4. Consider additional voice providers or agent backends only from observed
   need, not as a prerequisite for the first useful release.

### Rollback or failure behavior

- Keep typed Confer available if voice capture, transcription, or playback is
  unavailable; a voice failure must never bypass CoS to obtain a reply.
- Keep the current journal capture path until the shared replacement passes
  dev and real-device checks, then remove it from only the migrated call sites.
- If one day-one provider has a documented blocker, hide that provider for the
  affected mode and release the proven provider without changing the shared
  controller or future swap point.
- Provider and mode flags must allow one failing integration to be disabled
  without disabling writing, typed Confer, or the other voice provider.

## 11. Verification

### Journal memo mode

- Dictate in both German and Portuguese with multiple natural pauses of at
  least 20 seconds; capture must continue until explicit stop.
- Run the complete memo suite with both OpenAI and xAI; provider documentation
  alone does not satisfy the long-pause or transcript-integrity checks.
- Switch the shared Voice preference and confirm both writing domains follow it
  without adding or changing controls beside the journal microphones.
- Confirm text appears progressively and finalized events do not duplicate,
  reorder, or erase provisional text.
- Edit the resulting text and run the existing correction flow unchanged.
- Test denial of microphone permission, reconnect, explicit stop, duration cap,
  and rapid start/stop.
- Test on desktop Chrome, mobile Chrome, and mobile Safari with real speech and
  ordinary background noise.

### CoS Confer voice

- Continue one conversation across typed HTML, spoken HTML, and Telegram and
  confirm one coherent conversation history/context.
- Interrupt CoS while it is speaking and confirm playback stops without losing
  or duplicating the next user turn.
- Confirm light tags, domain inspection, and a permitted action behave the same
  for typed and spoken requests.
- Confirm an unpermitted action is rejected identically regardless of channel.
- Switch the configured backend in dev and confirm no browser/UI change is
  required.
- Stop or disconnect OpenClaw and confirm the documented fallback/error path;
  voice must not bypass CoS to obtain a reply.
- Run the complete Confer suite with both OpenAI and xAI chained voice and
  confirm the agent backend,
  conversation identity, memory, permissions, and stored turn shape do not
  change with that voice choice.
- If native Grok CoS is enabled, run the full CoS conformance suite against its
  canonical turns, tags, permissions, memory decisions, and audit record. Voice
  quality alone is not acceptance evidence.
- Test phone microphone, speaker, Bluetooth headset, tab suspension, network
  loss, and recovery.

### Operational and cost checks

- Record transcription, agent, and speech-generation latency separately.
- Record transcription, agent reasoning, and speech-generation usage and cost
  separately for each enabled provider profile before production approval.
- Avoid logging raw audio or sensitive transcript content in ordinary metrics.
- Run automated tests plus real-device dev validation; synthetic audio alone is
  not sufficient for voice UX approval.

## 12. Acceptance criteria

### Journal memo mode

1. Schreiben and Escrita use one shared memo controller and a dedicated
   streaming transcription session.
2. A 20-second pause does not end capture; only explicit stop or the safety cap
   does.
3. The same small microphone UI remains, with no provider selector or layout
   expansion.
4. German and Portuguese journal/context fields behave consistently and retain
   editable text after capture.
5. Browser-native `SpeechRecognition` is no longer used at those journal call
   sites.
6. Provider-specific segmentation is normalized without changing the journal
   UI or memo lifecycle; speech-final events do not stop capture.
7. OpenAI and xAI both pass the memo verification suite and are selectable from
   the shared Voice preference, unless a documented provider blocker invokes
   the approved single-provider release fallback.

### CoS Confer voice

1. Voice, typed HTML, and Telegram reach the same permanent CoS coordination
   layer, conversation scope, memory rules, tagging, and permission checks.
2. OpenClaw runs as a bounded standalone backend behind the CoS interface and
   can be replaced by configuration without changing the voice UI or stored
   conversation format.
3. The speech/transcription provider never acts as or bypasses the CoS agent.
4. Spoken responses, interruption, transcript visibility, stop, and reconnect
   work on representative desktop and mobile devices.
5. The #135 four-item blocker is resolved and OpenClaw passes text conformance
   before it is accepted as the voice-backed production CoS.
6. CoS domain access remains bounded and auditable for voice-originated turns.
7. OpenAI/xAI differences are contained in provider modules and declared in the
   capability registry; domain pages contain no provider-specific transport
   branches.
8. An interrupted assistant turn is explicitly marked and remains separate
   from the new user turn.
9. Transcription, reasoning, and speech-generation cost are measured separately
   and acknowledged before production rollout.
10. OpenAI and xAI chained voice both pass the Confer verification suite and
    remain swappable independently of the CoS agent backend, unless a documented
    provider blocker invokes the approved single-provider release fallback.

### Process

1. Grok and Claude Code independently review the design against the actual
   spec, repository surface, and current provider contracts, not a summary.
2. Robert approves the review-reconciled spec before implementation.
3. A second implementer reviews the actual implementation diff.
4. Robert separately approves the reviewed diff before merge or deployment.

## 13. Review and approval record

- **Design reviews:** Grok and Claude Code completed 2026-08-08.
- **Robert design approval:** Approved to build 2026-08-08.
- **Implementation diff review:** Pending; a second implementer must review the
  actual build diff.
- **Robert ship approval:** Pending for the future implementation diff.

### Incorporated review findings

- **Grok:** approved the design direction. Incorporated the synchronous
  fallback, conservative 15-minute starting cap, explicit interruption record,
  provider capability registry, domain-neutral reuse boundary, separate cost
  accounting, no memo provider selector, and accessible microphone states.
- **Claude Code:** independently confirmed the CoS/backend architecture and the
  relevant gated-branch code. Incorporated the decoupled Phase 0A/0B sequence,
  empirical long-pause requirement, and explicit reuse of `transcript.py` and
  `cost.py`.
- **xAI STT finding:** Claude's review reported that standalone xAI streaming
  STT was unavailable. That finding is not carried into the design because the
  official xAI documentation checked on August 8, 2026 explicitly documents
  standalone REST and streaming STT, including `wss://api.x.ai/v1/stt`, and
  separate streaming TTS. The build must recheck those live contracts rather
  than relying on either review's cached description.
- **Robert:** confirmed that a Grok-specific module is acceptable when needed,
  provided it remains part of the shared platform capability rather than a
  domain-specific fork. Robert also set both OpenAI and xAI as the day-one
  target for journal and Confer A/B comparison, with a documented
  single-provider release allowed only if one provider encounters a concrete
  blocker. Robert approved this reconciled design for implementation on
  2026-08-08.

## 14. Registration

- Keep Guild #133 as the governing intelligence-layer architecture.
- Keep Guild #135 as the OpenClaw dev/gateway prerequisite with its four named
  blockers.
- Link issue #177 to the journal memo-mode implementation work.
- Treat CoS voice as the channel capability promised by Roadmap §3 and an
  addendum to #133/#135, not a new competing CoS or OpenClaw program.
- Register this document as Guild build-queue item #145 with status
  `spec_ready`. Implementation may begin immediately, but the reviewed-diff and
  ship-approval gates remain separate.
