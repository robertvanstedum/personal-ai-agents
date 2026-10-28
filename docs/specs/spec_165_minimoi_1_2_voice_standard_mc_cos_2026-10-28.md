# Spec 165 — MiniMoi 1.2 voice: Master Craftsman and Chief of Staff on one standard direct-path voice; German and Portuguese next

| | |
|---|---|
| **Spec** | 165 |
| **Date** | 28 October 2026 |
| **Author** | Claude Code, for Robert |
| **Status** | **Draft v0.1 for review.** Authorizes no build, deploy, flag, merge, credential or provider call. |
| **Decision owner** | Robert |
| **Reviews required** | Claude Code self-review; Codex; one more (Claude chat recommended, §16 D15) |
| **Builds on** | Spec 164 v0.3, approved 27 Oct (`origin/grok/spec-164-mc-voice:docs/specs/spec_164_mc_voice_conversation_mode_2026-10-27.md`, PR #421) |
| **Replaces** | Spec 164's phase order (§9 MC, §10 CoS "later decision", §11 German backport). Everything else in 164 stands unless this spec says otherwise. |
| **Code base** | `grok/mc-voice-conversation` at `93bf00c6` (PR #426, open, not merged): Chunk B backend, Guild UI, two independent reviews, all fixes |
| **Standard** | `docs/specs/spec_shared_voice_interaction_standard_2026-08-15.md` (companion amendment in §15.4) |
| **Initiative** | INIT-2026-0010. GitHub issue: none yet (to be opened when Robert approves the spec) |
| **References** | `file:line` is `93bf00c6` unless marked `main` (`4954ad9f`). Anything this spec could not check is marked **unverified** and listed in §20. |

## In plain words

MiniMoi 1.2 is one voice, used two ways. Master Craftsman and Chief of Staff both talk with Robert through the
same direct voice path that German Gespräche already uses and that Chunk B extended for MC: the browser talks to
OpenAI or Grok Voice directly, and when the voice needs real work done it asks the real agent ("let me check that"),
which runs exactly as if Robert had typed the words.

What changes for Robert:
- **MC voice in production moves** from today's turn-by-turn relay (transcribe, MC writes, text-to-speech reads it)
  to a live conversation. He can interrupt, a pause doesn't send, nothing scrolls on screen while he talks, and what
  was said appears when the session ends, with a short summary he confirms in writing.
- **CoS voice moves too, at the same time, onto the same code.** Today she has two voice modes of her own, OpenAI
  only. After 1.2 she has one, the same as MC's, with OpenAI or Grok Voice. She keeps her own card and her own
  manner; by default she sounds as she does today, unless Robert decides otherwise.
- **Dictate stays exactly as it is**, on both pages.
- **German and Portuguese stay as they are in 1.2.** Their improvements come after 1.2 is in production, together,
  as one piece of work (Part B), gated on fresh measurements of both languages on both providers.

What "standard" means here: one server core, one browser session module, one card format, one way to capture,
hide, reveal and summarize, one consult contract, one budget, one release check. MC and CoS are thin adapters on
that core. Nothing is copied.

**Honest limits.**
- Nothing in 1.2 has been heard yet. Chunk B is synthetic tests only; Robert's listening and phone tests are the
  acceptance, and no agent claims them.
- Voices are chosen by ear. MC in production today speaks with OpenAI's `cedar`; the direct path's default is
  `marin`. CoS speaks `cedar` today. If nobody picks, both change sound.
- Any change under `core/realtime_voice/` redeploys the German and Portuguese images too (the release classifier,
  §2). "German deferred" means German's behaviour is unchanged in 1.2, not that its image is untouched. 1.2 must
  prove that with tests and a short listen.
- Provider bills are real. The daily voice allowance is MiniMoi's own limit, not a provider cap.

## 1. Robert's decisions (binding)

28 October 2026:
1. "We need a thorough spec and should target making MC and CoS both use new solution from the start. That is
   minimoi 1.2 and we defer German until in prod with 1.2."
2. "spec german now too and put it into the queue + portuguese and we do language domains together." German and
   Portuguese are Part B of this spec, built together, starting only after Part A is in production.
3. "so even more important to standardize CoS." Standardizing CoS is a core goal of 1.2, not an add-on.

Standing, from Spec 164 v0.3 (approved 27 Oct) and the reuse decision
(`coordination/ROBERT_DECISION_spec164_reuse_and_rollout.md`):
- German's code is the base. No fourth voice implementation, no copied controller, no domain session loop.
- Dictate stays a separate control.
- Each person has their own card. A French or Portuguese CoS is a card change, not a build.
- Latency no worse than German on the same provider, expected to improve.
- MC's manner is not inherited by CoS by side effect.
- The trust model of Spec 164 §3: a voice consult from Robert's own browser carries the same trust as a typed
  note, with the §3 safeguards.
- Dessa (full duplex) is a future consideration only.

How this changes 164: 164 put CoS after MC as "an option, decided later" (§10) and German last (§11). Here MC and
CoS ship together as 1.2, and German and Portuguese follow together as Part B. 164's standalone page (§5) stops
being a separate gate (D4).

## 2. What exists today (verified)

### 2.1 Master Craftsman

| Fact | Reference |
|---|---|
| **Production MC voice is the chained relay.** On `main`, `mc_voice.js` uses the managed path only when the capabilities answer carries `managed_realtime`, else falls back to `ConferVoiceController` (transcription credential, typed turn, server text-to-speech) | `main:minimoi_portal/guild_ui/static/js/mc_voice.js:136-141` |
| The managed MC host installs only when `MINIMOI_MC_MANAGED_REALTIME=1` **and** the MC kind is `cos`; production MC is `openclaw`, and the flag is in `PRODUCTION_HELD_OFF` unless `MINIMOI_GUILD_MANAGED_VOICE_PRODUCTION=1` | `mc_managed_realtime.py:28`; `guild_mounts.py:220-222`, `:288-293` |
| The chained relay's server routes: `/mc/voice/bootstrap` (OpenAI transcription credential) and `/mc/voice/speech/<id>` (text-to-speech of MC's kept reply, voice `cedar`) | `mc_voice.py:86-118`, `:136-176`, `:188-193`; `core/realtime_voice/providers/openai_speech.py:12` |
| The direct path is built: shared bootstrap with `owner` and `admit` hooks, shared consult route, transcript capture, hide-until-end, reveal, recovery, summary proposal, voice marker, release check, Guild UI | `mc_voice_direct.py` (833 lines); `B_direct_path.md` |
| It is off unless `MINIMOI_MC_VOICE=1` and `MINIMOI_MC_VOICE_DIRECT=1`; the second is in `PRODUCTION_HELD_OFF`, so production cannot turn it on without a code change | `mc_voice_direct.py:46`, `:592`; `guild_mounts.py:222` |
| On the branch the composer never falls back to the chained relay: direct, else managed, else "Voice unavailable" | `mc_voice.js:136-153` |
| MC's card is `voice.json` in persona release `0.1.18`, whose manifest says `"status": "candidate"`; the MC agent container is deployed from `0.1.16`. The direct host pins the card with a constant | `services/mc_operations/personas/mc-chat/releases/0.1.18/manifest.json`; `.github/workflows/deploy-mc-agent.yml:47`; `mc_voice_direct.py:48` |
| `voice_card.py` reads only MC's release folder (hard-coded path) | `core/realtime_voice/voice_card.py:16` |
| MC's session text = `mc_managed_realtime.INSTRUCTIONS` + direct-path rules, then the card, then this conversation's recent voice turns | `mc_voice_direct.py:78-102` |
| The direct host imports `SessionRefused` from the managed module and MC's operating rules from the managed MC host | `mc_voice_direct.py:36`, `:92`; `mc_voice_transcripts.py:7` |
| The Guild direct UI reads two private controller members, `_sessionId` and `_adapter.sendContinuationInstruction` | `static/js/mc_voice_direct.js:222-223` |
| MC Dictate has its own route (`/dictation/bootstrap`) and is independent of the relay | `guild_ui/dictation.py:77-79`; `static/js/dictation.js:111` |

### 2.2 Chief of Staff

| Fact | Reference |
|---|---|
| Confer offers **two voice modes** when the managed host is ready: "Realtime · OpenAI" (managed) and "Voice · turn by turn" (chained); otherwise only the chained mode | `cos_ui.html:58-61`, `:491-492`, `:588-590` |
| The managed host installs only with `MINIMOI_COS_MANAGED_REALTIME=1`, an allowance, and the `voice_control_*` tables present. Production compose defaults the flag to `0`; the value in `/opt/minimoi/.env` is **unverified** | `domains/cos/managed_realtime.py:15-46`; `docker-compose.prod.yml:250`, `:408` |
| Managed CoS = OpenAI `gpt-realtime-2.1`, voice `cedar`, a server-held sideband; her instructions plus `VOICE_MANNER` appended to every managed session; tools `consult_cos_agent` and `save_cos_note` | `controlled_session.py:20`; `controlled_config.py:8-59` |
| Her voice instructions = `_build_system_prompt()` (soul, context, the whole `cos_memory.md`, ops status, build state, date) plus a voice-surface paragraph | `chief_of_staff.py:614-646`, `:649-679` |
| Her typed turns also carry `PARTNER_STYLE` (the "warm Austrian colleague" manner), so part of her Austrian manner is not voice-only | `domains/cos/confer_service.py:341-342`; `core/agent_partner.py:2-12` |
| Managed consults: `confer_dispatch` runs `_process_confer_turn(canonical_text, channel='html_voice', …)`; a note needs `parse_explicit_note` and an operation receipt | `domains/cos/realtime_tools.py:9-19`; `chief_of_staff.py:811-841` |
| Managed CoS keeps **no transcript server-side** (no `on_transcript`); the page offers "Download transcript on End" | `managed_realtime.py:51-53`; `controlled_host.py:13`; `cos_ui.html:544-551` |
| The chained mode: `create_confer_voice_blueprint` mints an OpenAI transcription credential; the page sends each final transcript to `/ui/send` with `channel: html_voice`; the reply is read by `/ui/speech/<turn_id>` (`cedar`) from a 120-second in-memory `SpokenReplyStore`; a provisional "Hearing: …" line shows while he speaks | `core/realtime_voice/confer.py:20-55`; `chief_of_staff.py:76`, `:1429-1500`, `:1593-1630`; `spoken_reply_store.py`; `cos_ui.html:503-522` |
| **CoS Dictate depends on that older blueprint:** it bootstraps from `../api/realtime-voice/confer/bootstrap`, and is enabled only when the blueprint's capabilities answer lists OpenAI | `cos_ui.html:552-557`, `:568-582`; `confer-input-controls.js:59-65` |
| Confer has **one conversation, `owner`**, and **no server-rendered thread**: the page shows only what it appended in this tab | `managed_realtime.py:26-31`; `cos_ui.html:154`, `:296-326` |
| Confer's durable record is `conversation_log` (one JSON line per typed turn, off unless `COS_CONVERSATIONS_DIR` is set); it is written by `_ui_turn` only, so managed-voice consults are not in it | `domains/cos/conversation_log.py:1-41`; `chief_of_staff.py:1480` |
| The portal owns CoS login and CSRF. It CSRF-checks `/app/cos` writes only when the write guard is on or the path is under `api/realtime-voice/managed/`, and adds `X-Minimoi-Voice-Write-Verified: 1` only for that prefix | `minimoi_portal/app.py:722-756`; `minimoi_portal/proxy.py:303-306` |
| `cos-scheduler` (which serves Confer) copies `core/`, `utils/`, `domains/guild/`, `domains/cos/` and a few files, but **not** `minimoi_portal/guild_ui/` or `services/mc_operations/`. It is one process | `docker/Dockerfile.cos-scheduler:8-15`; `chief_of_staff.py` comment above `_ui_turns` |
| Confer already listens for `minimoi:companion-opening` and ends its voice and Dictate before the floating MC panel opens | `cos_ui.html:594-600` |

### 2.3 German and Portuguese

| Fact | Reference |
|---|---|
| Both register `create_bootstrap_blueprint` with no `owner` and no `admit`: no budget reservation, identity from portal headers | `domains/german/html_server.py:127-131`; `domains/portuguese/html_server.py:77-81` |
| Both import the controller with the cache key `?v=20260809-ga1` | `german_gesprache.html:1920`; `portuguese_conversas.html:1637` |
| Neither page ends voice on `pagehide`, and neither listens for `minimoi:companion-opening` (no match for either in either template). So the floating MC panel can open over a live German session; whether the microphone is then contended is **unverified** in a browser | grep of both templates |
| Persona voices are per persona and provider (German default `alloy`/`sal`) | `domains/german/html_server.py:87-91` |
| Phase 0 baselines exist for **German only**: OpenAI median ≈705 ms, Grok median ≈775 ms with two ~3 s waits, speech end to first assistant transcript; first audible playback not measured. **Portuguese has no baseline** | `P0_run_openai_2026-10-27.md`; `P0_run_xai_2026-10-27.md` |

### 2.4 Shared code and release mechanics

| Fact | Reference |
|---|---|
| Any change under `core/realtime_voice/` redeploys `portal`, `german`, `portuguese`, `cos-bot`, `cos-scheduler` on a push to `main` | `scripts/ci/classify_release.py:370-371` |
| Changes under `domains/cos/` redeploy `cos-bot`, `cos-scheduler`; `minimoi_portal/` redeploys `portal`; `domains/guild/` redeploys `portal`, `cos-bot`, `cos-scheduler` | `classify_release.py:346-357` |
| Both adapters changed on the branch (playback event), but the controller still imports them with old cache keys (`?v=20261015-managed-end1`, `?v=20260725-transcript1`) | `realtime-voice-controller.js:24-25`; `git diff origin/main..93bf00c6 -- core/realtime_voice` |
| On Grok Voice the server-composed `session_config`, instructions included, is returned to the browser and sent by it as the first `session.update` (xAI does not take it at mint time). On OpenAI the server attaches it | `core/realtime_voice/providers/xai_voice.py:13-18`, `:74-99`; `xai-websocket-adapter.js:72-73` |
| The consult cap is 12 a session, the holding line after 10 s | `core/realtime_voice/consult.py:13-16` |
| Default voices `marin` (OpenAI) and `eve` (Grok); allowed ids are listed in `voice_card.py` | `bootstrap.py:46`; `voice_card.py:11-15` |
| The shared daily voice cap defaults to $20 per America/Chicago day, durable, fail-closed, a reserved allowance not a bill; the portal and `cos-scheduler` share one ledger | `core/realtime_voice/budget.py:1-38` |
| Provider default: explicit choice, else `VOICE_REALTIME_PROVIDER_DEFAULT`, else `openai` | `core/realtime_voice/config.py:28-61` |
| Allowed locales: `de-AT`, `pt-BR`, `en-US` | `config.py:19` |

## 3. Goal and scope of 1.2

**Goal.** Robert talks with Master Craftsman and with Chief of Staff, on the Mac and the phone, through one voice
implementation, with the same behaviour, the same safeguards and the same release check, each sounding like
themselves.

**In scope (Part A):**
- A host-agnostic direct-voice core extracted from the Chunk B Guild host (§4).
- MC moved onto the core with no change in behaviour (§5).
- CoS standardized on the core: her card, her manner written deliberately, her consult backend, her store, her
  summary, the Confer UI on the same contract (§6).
- Providers, voice ids, budgets and cost visibility per person (§7).
- Every Chunk B safeguard, for both hosts (§8).
- Retirement of the managed sideband, both chained relays and `VOICE_MANNER`, on a stated schedule (§9).
- Rollout, release checks, Robert's listening and phone tests, production switch-over and rollback (§11).

**Part B (queued, starts after 1.2 is in production):** German and Portuguese together (§14).

**What "standard" means**, concretely; each is one thing, used by both hosts:

| Standard | Is | Lives in |
|---|---|---|
| One host core | session ledger, transcript validation and caps, end intent, summary flow, decision flow, route registration | `core/realtime_voice/direct_host.py` (new, from `mc_voice_direct.py`) |
| One controller | `RealtimeVoiceController`, direct mode, plus two public methods (§4.3) | `core/realtime_voice/static/realtime-voice-controller.js` |
| One browser session module | bridge, holding line, capture and heartbeat, End and `pagehide`, server-ended handling, Check, summary speaking | `core/realtime_voice/static/direct-voice-session.js` (new, from `mc_voice_direct.js`) |
| One card format | `voice.json`, schema 1 plus optional `language` and `accent`, hash-checked against its release manifest, pinned by data | `core/realtime_voice/voice_card.py` (generalized) |
| One capture/reveal/summary contract | `UI_CONTRACT_chunk_B.md` §1–§9, renamed for any person | the contract file, republished as the 1.2 contract (§10) |
| One consult contract | `create_consult_blueprint`, unchanged: CSRF, owner, call-id dedup, in-flight guard, cap, End | `core/realtime_voice/consult.py` |
| One budget | `budget.admit(kind="realtime_direct_session", domain=<host>, required=True)` before any mint | `core/realtime_voice/budget.py` |
| One release check | the Chunk B release check, parametrized by host (§12.2) | `tests/realtime_voice/` (new shared check) plus one instance per host |

## 4. Architecture: one core, thin host adapters

### 4.1 Why extract, and where to

`mc_voice_direct.py` mixes two kinds of code. About half is host-agnostic: the session ledger with its caps,
`seen`/`live`/`expired`, end-intent tracking (`_Sessions`, `:124-268`); transcript validation (`_events`,
`:432-460`); the summary prompt and parsing (`:464-522`); the receipt line (`:338-350`); the transcript, End-events,
summary and decision route flows inside `install` (`:679-807`). The rest is the Guild: the owner guard and write
guard, the conversation store, the floor notes, the typed turn through `api._ledger_turn`/`_answer_turn`, the
tagged floor, MC's card and continuity.

CoS cannot import the Guild: `cos-scheduler` does not copy `minimoi_portal/guild_ui/` (§2.2). So the host-agnostic
half moves to `core/realtime_voice/`, which every voice image already copies, and both MC and CoS call it. **Moved,
not copied:** after the move, `mc_voice_direct.py` keeps only its adapter, and a test fails if a host file defines
its own session ledger, transcript validator, summary flow or browser bridge (§12.1).

### 4.2 What moves where (server)

| Today | Moves to | Notes |
|---|---|---|
| `_Sessions` (`mc_voice_direct.py:124-268`) | `direct_host.DirectSessions(ConsultLedger)` | durable start/end markers become adapter calls (`store.mark_start`, `store.mark_end`); `first_words` becomes an optional adapter hook |
| `_events`, `POST_MAX_*`, `SESSION_MAX_*`, `CLOCK_SLACK_S` (`:56-60`, `:432-460`) | `direct_host` | unchanged values |
| `keep_events`, `end_events`, `owned_session`, `summary`, `decide` route flows (`:679-807`) | `direct_host.install_direct_voice(app, adapter, *, url_prefix, environ, budget)` | the routes and their bodies stay exactly as `UI_CONTRACT_chunk_B.md` |
| `_summary_prompt`, `_summary_texts`, `PROPOSAL_HEAD`, `DECISION_HEAD`, `UNAVAILABLE_TEXT` (`:464-522`) | `direct_host` | the person's display name is a parameter |
| `_receipt_line`, `JOB_DONE` (`:335-350`) | `direct_host`, with receipt kinds supplied by the adapter | MC: reply, operation, job; CoS: reply, note, reminder |
| `TOOLS`, `DIRECT_RULES` (`:62-83`) | `direct_host.tools(person)`, `direct_host.direct_rules(person)` | names stay exactly `consult` and `save_note` (`bootstrap._ALLOWED_TOOL_NAMES`, `:47`) |
| `done_intent`, `affirmative`, `receipt_parts`, `full_differs`, `state` (`mc_voice_reveal.py:172-208`, `:251-276`, `:333-350`) | `direct_host` (pure functions over an adapter store) | Guild's `view()` stays Guild (floor notes) |
| `_speakable` (`mc_voice.py:121-133`) | `direct_host.speakable` | MC's chained relay goes in the cleanup (§9) |
| `SessionRefused` (`controlled_session.py`) | `core/realtime_voice/errors.py` | so the managed modules can be deleted later without breaking the direct path |
| MC's operating rules `INSTRUCTIONS` (`mc_managed_realtime.py:16-22`) | the MC adapter (`mc_voice_direct.py`) | same reason |
| `voice_card.load_card(release)` with MC's path (`voice_card.py:16-39`) | `load_card(root, release)` plus `load_pinned(root)` | §4.5 |

Stays in **`minimoi_portal/guild_ui/mc_voice_direct.py`** (the MC adapter): the guard (`owner_api`, `check_write`,
`check_stop`, MC turns on), conversation lookup (`api._conversation`, `ID_RE`), the floor store (markers, tags,
`mc_voice_transcripts.retain`, `mc_voice_reveal.view`), `_TaggedFloor`, the typed turn (`_ledger_turn`,
`_answer_turn`, `VOICE_MARKER`, `g.mc_voice_named_only`), the summary model call (`services.mc.turn`, ledger gate,
`_mc_acquire`), MC's continuity (`mc_voice_transcripts.block`), the title touch.

New **`domains/cos/direct_voice.py`** (the CoS adapter): §6.

### 4.3 What moves where (browser)

| Today (`minimoi_portal/guild_ui/static/js/mc_voice_direct.js`) | Moves to | Notes |
|---|---|---|
| `startRefusal`, `noticeWords`, `jsonPost`, `createCapture`, `directVoice` and their constants | `core/realtime_voice/static/direct-voice-session.js` | words that name the person ("Check with MC") take the person's short name |
| `voiceThread` (Guild thread DOM) | stays Guild | CoS gets a small Confer renderer (§6.8) |
| `controller._sessionId`, `controller._adapter.sendContinuationInstruction` | `controller.sessionId()` and `controller.speak(instruction)`, public, additive | German and Portuguese do not call them |

Both pages load the module the same way the Guild does today: only when the capabilities answer offers the direct
path. The shared controller's adapter imports get new cache keys, because both adapters changed (§2.4).

### 4.4 The adapter interface

One small interface, written once in `direct_host`. Each host supplies:

| Hook | MC (Guild) | CoS (Confer) |
|---|---|---|
| `owner()` → (id, display name) or None | signed-in Guild owner (`mc_voice_direct.py:604-610`) | `resolve_user_id` + tier owner + the portal's verified-write marker (as `managed_realtime.owner_guard`) |
| `guard(kind)` → refusal or None | owner, CSRF, record mode, MC turns (`:620-632`) | owner and marker; CSRF is the portal's (§6.9); Confer has no Private mode (`managed_realtime.py:26-31`) |
| `conversation(requested)` → handle | an owned, saved, on-record conversation | `owner` only; anything else refused |
| `store` | floor notes, markers, tags, `retain` | file store (§6.6) |
| `typed_turn(handle, session, call, name, owner_text)` → result | MC's typed turn (`:353-429`) | `_process_confer_turn` (§6.4) |
| `summary_model(handle, session, prompt)` → text | `services.mc.turn`, own runtime session (`:525-585`) | her backend, own runtime session (§6.7) |
| `card()` | MC's pinned card | her pinned card |
| `instructions(card, handle)` | rules, card, continuity (§5) | rules, card, briefing and continuity (§6.3) |
| `person` | `{"key": "master_craftsman", "name": "Master Craftsman", "short": "MC"}` | `{"key": "chief_of_staff", "name": "Chief of Staff", "short": "CoS"}` |

The core owns everything between: bootstrap (via `create_bootstrap_blueprint` with `owner` and `admit`, as built),
consult (via `create_consult_blueprint`), transcript, End with events, summary, decision, caps, dedup, the budget
reservation before the mint, and the capabilities metadata (`direct_voice`).

### 4.5 Cards and pins as data

A card lives in its person's own release folder with a manifest and a hash, as MC's does. The adapter does not
name the release in code: it reads a one-line **pin** in the person's voice folder (`pin.json`,
`{"release": "<v>"}`). Changing a card is a new release folder plus a pin change, both data, both reviewed as an
owner adoption (Spec 164 §3: tool or card changes go through the persona release gate, never a configuration edit).
`load_pinned(root)` refuses a missing pin, a missing release, or a hash that is not the manifest's (as `load_card`
does today, `voice_card.py:19-25`).

Schema 1 gains two optional fields:
- `language`: the session locale, one of `config.ALLOWED_LOCALES`. The adapter takes its locale from the card, not a
  constant (MC's is a constant today, `mc_voice_direct.py:50`). Absent means `en-US`.
- `accent`: a short label for the reader of the card ("light Austrian"). Informational; the card text is what the
  model reads.

So a Portuguese CoS is a new card with `"language": "pt-BR"`: no code. A **French** CoS also needs `fr-FR` in
`ALLOWED_LOCALES`, the provider capability table and the transcription-language table (`config.py:19`,
`capabilities.py:47`, `:59`, `bootstrap.py:61-64`): three allow-list lines, not a build, but not a card change only.
This spec says so rather than overpromise.

## 5. Master Craftsman on the core

MC's behaviour does not change in the extraction. The Chunk B suites are the regression harness: every test in
`tests/guild/shop_floor/test_mc_voice_direct.py`, `test_mc_voice_direct_page.py`, `tests/test_mc_voice_reveal.py`
and the five `tests/mc_voice_direct_*.mjs` files passes unchanged, apart from import paths.

What does change for MC in 1.2:
- **Card pin as data** (§4.5). Release `0.1.18` is a candidate whose base is `0.1.16` plus the card. 1.2 releases
  MC's voice card as an adopted release (a new version if the card text changes after the audition), and the pin
  names it. Whether the MC agent container moves to that release at the same time is not a voice question; the
  portal reads only `voice.json` from it.
- **Operating rules moved** into the adapter (§4.2), text unchanged.
- **Person-neutral voice rules into the shared direct rules (D17).** `VOICE_MANNER` carries rules that are not about
  who anyone is: one or two short sentences then yield; drop an interrupted explanation; don't prompt in silence;
  distinguish an unavailable tool, a failed lookup and unchecked evidence; claim a destination only from its receipt
  (`controlled_config.py:19-29`). MC's direct path does not get them today. The recommendation is to put them in the
  core's direct rules, worded for any person, so MC and CoS both have them and neither card carries the other's
  manner.
- **Voice ids by ear** (§7, D3).
- **Production switch** (§11).

## 6. Chief of Staff standardization (core goal)

### 6.1 Her card: format and location

- **Location:** `domains/cos/voice/releases/<v>/voice.json` plus `manifest.json` (hash of `voice.json`), and
  `domains/cos/voice/pin.json`. `domains/cos/` is copied into `cos-scheduler` (§2.2) and redeploys only CoS images
  when it changes (§2.4). (`domains/guild/config/` holds her soul and context, but a change there also redeploys
  the portal.)
- **Format:** MC's schema 1, with `language: "en-US"`, `accent: "light Austrian"` (the default in §6.2), `card`, and
  `voices`.
- **Voices:** see §7 and D2. The draft keeps `"openai": "cedar"`, her voice today in both of her modes
  (`controlled_config.py:51`; `openai_speech.py:12`), so she does not change sound by default; Grok is unset until
  Robert picks by ear.

### 6.2 Her manner, written deliberately

**Default: preserve her current effective voice unless Robert decides otherwise (D1).** Her effective voice today
on the managed path is her own voice-surface paragraph (`chief_of_staff.py:652-678`), plus `VOICE_MANNER` =
`PARTNER_STYLE` + MC's Viennese partner manner (`controlled_config.py:8-30`). On the chained path the spoken words
are her typed replies, which already carry `PARTNER_STYLE` (`confer_service.py:341-342`).

Nothing is inherited by side effect: each part she keeps is written into her card or her rules, by name. This
table lists every clause of `VOICE_MANNER` and where it goes for her:

| `VOICE_MANNER` clause (`controlled_config.py`) | Kind | For CoS, by default |
|---|---|---|
| `PARTNER_STYLE`: warm Austrian colleague, fluent in English and German, follows his language, `du`, Austrian phrasing, dry humour, no caricature, no lessons | identity | **her card**, reworded as hers |
| `PARTNER_STYLE`: precise confirmations; use tools before declaring unavailable; name the missing connection; note ≠ post-it ≠ request ≠ answer; label excerpts; no runtime ids; no authority | reporting | **shared direct rules** (D17) |
| "familiar Viennese partner, friend and colleague: warm, sharp, candid, dry humour; never a receptionist, concierge, servant, stranger or language examiner" | identity | **her card** (kept on purpose; marked "kept from today's manner") |
| English by default, light natural Austrian accent, intelligible, no caricature, no phonetic spelling, no invented memories or biography | identity | **her card** |
| German belongs in his day: a brief phrase in context; "how do I say…" gets one natural formulation, then pause | identity | **her card** |
| When he speaks German, follow in clear Austrian standard German with `du`, no exaggerated dialect, return to English; never a lesson | identity | **her card** |
| No canned enthusiasm, flattery, apologies, recaps, filler; a teammate at the same desk | manner | **her card** |
| No scripted offers of help or transfer announcements; report actual recipients and handoffs from receipts | reporting | **shared direct rules** |
| One or two short sentences, about 15–40 words, then yield; lead with the answer; expand only when asked | manner | **shared direct rules** (also already in her paragraph) |
| If interrupted, drop the explanation and follow; don't prompt in silence; next useful step within his authorization | manner | **shared direct rules** (also in her paragraph) |
| "use consult_cos_agent" before saying something is unavailable | tool | **her rules**, with the tool renamed `consult` |
| Distinguish an unavailable tool, a failed lookup, unchecked evidence; never invent access or promise success | reporting | **shared direct rules** |
| No claim of complete history or universal memory; distinguish supplied context, returned evidence, kept notes | reporting | **shared direct rules** |
| Guild note ≠ CoS memory note ≠ Board post-it; Board change only with a Board receipt | reporting | **shared direct rules** |
| Voice rules never expand authority | rule | **shared direct rules** |

Her own voice-surface paragraph (`:652-678`) stays hers, in her rules, with two edits: `consult_cos_agent` →
`consult`, `save_cos_note` → `save_note`.

**Draft card text** (for Robert's review; final wording after the audition):

> Chief of Staff, Robert's familiar, candid partner: the one who keeps the work pointed at the right questions.
> Warm, sharp, direct, with dry humour when it fits; never a receptionist, concierge, servant or language examiner.
> English by default, with a light, natural, intelligible Austrian accent; never a caricature, never invented shared
> memories. German belongs naturally in his day: an occasional brief phrase; when he asks how to say something, one
> natural Austrian formulation, then pause. When he speaks German, follow in clear Austrian standard German with du,
> and return to English naturally; never a lesson. No canned enthusiasm, flattery or filler. Pronounce MiniMoi, Chief
> of Staff, Master Craftsman, Claude Code, Codex and Grok the same way every time.

This keeps today's effective voice. It also means MC and CoS share an accent and much of a manner, because today's
CoS voice was MC's manner appended. That is the honest default, and the reason D1 exists: the audition (§11.4) is
where Robert hears whether she sounds like herself or like MC, and decides.

### 6.3 Her session instructions

Composed on her server, in Spec 164 §7's order, bounded:
1. **Operating rules:** the core's direct rules for her person (tools, receipts, "Shall I sum up?", never summarize
   from memory, the person-neutral rules of §6.2), then her voice-surface paragraph (renamed tools).
2. **Who she is:** her card.
3. **Data:** her briefing as today (`_build_system_prompt()`: soul, context, memory, ops status, build state,
   date), then bounded continuity: the most recent revealed voice records for `owner` (§6.6), labelled DATA.

Bound: 24,000 bytes, the managed path's own limit (`controlled_config.py:38-42`). Continuity is cut first, then the
briefing's memory section, marked as cut; the rules and card are never cut. Over the bound with continuity and
memory already cut, voice is unavailable and says so. (`cos_memory.md` is included whole today; its size in
production is **unverified**.)

On Grok Voice these instructions pass through Robert's own browser (§2.4). That is the same owner who can read her
memory page; it is named here so nobody assumes otherwise. Nothing the browser could change grants anything: the
server acts only on the owner's transcript item and the allow-listed tools (§8).

### 6.4 Her consult backend

`typed_turn` for CoS calls `_process_confer_turn(owner_text, channel="html_voice", conversation_id="owner",
request_id=<vc-session-n>)` (`chief_of_staff.py:811-841`): the same service, authority and receipts as a typed
Confer message, on Robert's own completed words, never the model's arguments.

| `ConferTurnResult` | Consult result |
|---|---|
| `reply`, no `operation` | `ok`, `answered`, spoken = `speakable(reply)`, receipt kind `reply`, id `turn_id` |
| `operation.type == "note"` with an operation id | for `save_note` or a spoken "save a note:", `saved: true`, receipt = operation id |
| reminder operation | `ok`, spoken from the reply, receipt kind `reminder` with its time |
| `/new` (session reset) | refused in voice: "Start a new CoS conversation in writing." |
| `InvalidConferTurn`, `ConferOperationFailed`, other exception | `ok: false`, spoken "That did not go through. Nothing is claimed." |
| `output_limit_reached` | `ok`, spoken from the reply plus "The rest is cut off; ask in writing." |

Typed Confer appends to `conversation_log` and enforces file claims (`_ui_turn`, `:1462-1481`); voice consults today
skip both, because the managed dispatch calls the service directly. The CoS adapter runs the same `enforce_file_claims`
and the same outbox snapshot as `_ui_turn` for a consult, so a voice answer cannot claim a download the platform did
not deliver.

**Bare confirm words.** MC's voice path refuses bare "yes", "stop" and the like as commands (review B #1). CoS's
turn service has no confirm parsing of its own (`confer_service.py:198-330`), but her runtime (`cos-agent-a`,
OpenClaw, with the `mc-actions` plugin, `docker/cos-agent-a/plugins/`) may ask questions a bare "yes" answers. Whether
it has any approval flow a spoken "yes" could trigger is **unverified**. Chunk A2 inventories it first; if one
exists, the CoS adapter applies the same rule: a bare confirmation in voice is answered "confirm that in writing".

### 6.5 Her tools

| Managed today | Direct path (1.2) | How |
|---|---|---|
| `consult_cos_agent` (`controlled_config.py:56`) | `consult` | core tool definition with her name; acts on the owner's transcript item |
| `save_cos_note` (`:57`) | `save_note` | `parse_explicit_note` (`confer_service.py:130`) must match, else "Nothing was saved. Say 'save a note:' and the note."; saved only with the operation receipt |
| `realtime_tools.confer_dispatch` (`domains/cos/realtime_tools.py`) | the CoS adapter's `typed_turn` | same checks, in the core contract; `realtime_tools.py` is retired with the managed path (§9) |
| canonical text = actual owner audio transcript | `owner_transcript_item` | unchanged principle |

### 6.6 Her store: capture, hide, reveal

Confer has no visible server thread (§2.2), so "hidden in every view" is simpler than in the Guild, and the store
must be built. File first:
- **Where:** `<COS_CONVERSATIONS_DIR>/voice/<session>.jsonl` (D5): one line per kept record (start marker, owner
  turn, CoS turn, consult request, consult receipt, end marker, summary proposal, decision), with the same fields the
  Guild keeps (session id, stable id, start/end, partial, interrupted). Written as it happens, append-only,
  `O_NOFOLLOW` as `conversation_log.append` does.
- **No store, no voice.** If the folder is not configured or the start marker cannot be written, bootstrap answers
  503 "Voice unavailable: there is no place to keep what is said." Same rule as MC (`mc_voice_direct.py:153-154`).
  Whether `COS_CONVERSATIONS_DIR` is set in production is **unverified**.
- **State** is the core's: ended, dropped (no end marker after the maximum duration), live (open in this process,
  heard within 60 s), recovering.
- **Reveal:** End answers with the session's records; the page appends them to the thread (owner turns, her
  spoken turns with "interrupted" marks, one line per consult, "Checked with CoS · answered"), then the summary card.
- **Other tabs and devices:** a new `GET …/voice/sessions` lists sessions that are recovering or have an undecided
  summary proposal. Confer calls it on load and shows the recovery line or the summary card; nothing else from
  history, because Confer shows no history today.
- **Her recall (D6):** at End the revealed session is appended to `conversation_log`, one line per owner/CoS turn and
  consult, `channel: "html_voice"`, so CoS can recall what was said as she recalls typed turns. Labelled DATA; never
  re-sent as owner instructions.

### 6.7 Her summary

Same flow as MC (Spec 164 §8, `UI_CONTRACT_chunk_B.md` §7): "we're done" proposes; yes or Sum up ends capture;
the summary comes from this session's kept records only (60 records / 12,000 characters, omissions marked); spoken
and written texts differ; one proposal per session, no expiry; confirm, edit or decline in writing; confirming
authorizes nothing it mentions.

Her model path: `call_backend(prompt, context, tool_policy)` (`chief_of_staff.py:765-777`), with **its own runtime
session**, so the summary request does not land in Robert's live CoS conversation, and with no tools. How the
OpenClaw backend is given a separate session key is **unverified** (MC's adapter does it with a session id,
`mc_voice_direct.py:564`); A2 confirms it before building. A confirmed or edited summary is appended to
`conversation_log` as DATA. It is never written to `cos_memory.md` unless Robert asks for a note.

### 6.8 Confer UI on the same contract

- **One Voice button.** The "Voice mode" select and the chained "turn by turn" mode are removed when the direct
  path is on (D8). The provisional "Hearing: …" line goes: nothing said shows while live.
- **Live bar:** voice on, elapsed time, End, Check with CoS; while "Shall I sum up?" is offered, Sum up and Keep
  talking. Same words as the Guild with her name.
- **Provider choice:** OpenAI or Grok, in Confer's existing quiet style, default her default (§7), remembered under
  its own key (MC's is `minimoi.voice.provider`, `mc_voice.js:70`); `voice_notices` as one small line.
- **Reveal, recovery line, summary card:** as §6.6–§6.7, in Confer's existing styles. The summary card says
  "Confirming confirms the summary only. Anything it mentions still needs its own request."
- **"Download transcript on End" (D7):** recommended kept, as a quiet link after reveal, built from the kept
  records instead of the browser's turns.
- **Dictate unchanged,** including its bootstrap (`../api/realtime-voice/confer/bootstrap`), which must therefore
  survive the retirement of the chained mode (§9).
- **Microphone handover:** Voice yields to Dictate and Dictate to Voice, as today (`cos_ui.html:555`, `:586`); the
  existing `minimoi:companion-opening` handler ends the direct session too; `pagehide` sends End with the final
  events and keepalive (the Guild's `sendFinal`).
- **Unavailable:** with the switch off, Confer is byte-for-byte today's page. With it on and a start refused, the
  button says "Voice unavailable: <reason>". No silent fallback to the managed or chained mode.

### 6.9 Portal changes for CoS

The portal is CoS's CSRF and owner gate (§2.2). For the direct path it must:
- CSRF-check every POST under the new CoS direct prefix (as it does for `api/realtime-voice/managed/`,
  `app.py:749-751`), and render the CSRF meta tag whenever the CoS direct path is on (`app.py:722-723`);
- add `X-Minimoi-Voice-Write-Verified: 1` for that prefix only, after the owner and CSRF gates, browser values
  stripped as today (`proxy.py:303-306`);
- keep `X-Record-Mode` irrelevant for CoS (she has no Private mode) and say so in the contract.

### 6.10 Her switch

`MINIMOI_COS_VOICE_DIRECT=1` on `cos-scheduler` (and read by the portal for §6.9). Off: no route, Confer unchanged.
Production admission is explicit, like MC's (§11.2).

## 7. Providers, voices, budgets, cost

| | MC | CoS |
|---|---|---|
| Providers offered | OpenAI, Grok Voice | OpenAI, Grok Voice (new for her) |
| Default provider | OpenAI (`config.resolve_provider`, no env override) | OpenAI, unchanged |
| Models | OpenAI `gpt-realtime-2.1`; Grok `grok-voice-latest` (`openai_realtime.py:19`; `xai_voice.py:25`) | same |
| Voice today in production | `cedar` (chained text-to-speech) | `cedar` (managed and chained) |
| Voice ids in 1.2 | chosen by ear before the switch (D3); unset means `marin`/`eve` with a notice | draft `openai: cedar` (keeps today's), Grok by ear (D2) |
| Budget | `admit(kind="realtime_direct_session", domain="mc", required=True)` before the mint | same with `domain="cos"` |
| Per-session reservation | `MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO` | the same value (D10) |
| Daily cap | shared `voice.daily_cap_usd`, default $20 per Chicago day, one ledger for portal and `cos-scheduler` | same |
| Session limit | `DurationGuard` defaults (warning and maximum as German) | same |
| Cost shown | capabilities carries `budget.public_status()`; refusals show the plain allowance message; "reserved allowance, not a bill" | same |

Choosing voices: Robert auditions on dev with the identity script (§11.4): `cedar`, `marin` and one or two others
on OpenAI; `eve`, `ara`, `rex` on Grok. The question per voice is "is this MC?" and "is this CoS?". Two persons on
one timbre is allowed but should be a choice, not an accident.

No new meter. The one-line daily voice spend in the morning brief stays a later improvement (Spec 164 §17).

## 8. Safety carried from the Chunk B reviews

Each holds for both hosts, enforced in the core unless marked adapter.

| Safeguard | Source | How |
|---|---|---|
| Owner, CSRF, record mode on every route; End works off the record | 164 §3; contract §2 | adapter guard; core refuses a session owner mismatch (`consult.py:89`) |
| Model arguments never become owner text; the server acts on the owner's transcript item | 164 §3 | core consult route (`consult.py:86-103`) |
| Tools allow-listed, exactly `consult` and `save_note` | 164 §3 | `bootstrap._allowlisted_tools` (`:50-60`) |
| One effect per call: call-id dedup, in-flight guard, 12-consult cap | Chunk A | `ConsultLedger` (`consult.py:44-64`) |
| Check-with button: in-flight guard, "Already checked" for a turn already consulted | UI review #3 | browser module |
| A note is spoken as saved only on its platform receipt | 164 §9 | adapter result mapping; core checks `saved` and receipt |
| Named proposal codes only, in voice; bare "yes"/"stop" refused as commands | review B #1; UI review #4 | MC adapter (`g.mc_voice_named_only`); CoS adapter after the §6.4 inventory |
| An action that happened is reported as done | review B #2 | adapter result mapping |
| Everything written during a consult is hidden with the session | review B #3; CC_16 (a) | MC `_TaggedFloor`; CoS writes only her own store during a consult, plus memory and reminders, which Confer does not display |
| Budget reserved only after the session is recorded; `abandon()` on a failed mint | review B #4 | core via bootstrap (`bootstrap.py:274-319`) |
| Caps count new turns only; times compared as times | review B #5, #6 | core |
| No title from spoken words while live | review B #7 | MC adapter hook |
| German/Portuguese import nothing new at import time | review B #8 | core keeps lazy imports |
| A failed marker read never hides a non-direct transcript | review B #9 | MC adapter (`view`) |
| Hide until End, reveal once, recovery line, never an empty gap | 164 §6 | core state; adapter store |
| Server-ended sessions end the provider too (409 `session_ended`, any 403, turns off) | UI review #2 | browser module |
| `pagehide` sends End with the last events, keepalive, same tick | UI review #1 | browser module; core End accepts events |
| Tagging fails open for MC outputs only, never for spoken words | UI review #9 | MC adapter |
| No silent fallback to any other voice path | 164 §2; Codex C7 | release check asserts the page references no other path |
| No audio or transcript in operational logs | standard rule 6 | core logs ids and outcomes only (`bootstrap._log_outcome`) |

## 9. Retirement: what is removed, when, and what stays

"No silent fallback" holds from the switch: a page with the direct path on never tries another path. Code removal
comes after, so a rollback by image is always possible (§11.6).

| Component | Used today by | 1.2 switch release | Cleanup release (after acceptance, D9) | Kept |
|---|---|---|---|---|
| MC chained relay: `/mc/voice/bootstrap`, `/mc/voice/speech`, `realtime-confer-controller.js` served by `/mc/voice/static`, `conversation.js` `submitVoiceUtterance` | production MC voice | unreachable from the composer (already so on the branch); release check pins it | routes and code removed | — |
| MC managed host `mc_managed_realtime.install` and its `mc_voice.js` branch | nothing in production (held off, kind `cos` only) | unchanged | removed; `INSTRUCTIONS` already moved (§4.2) | `MANAGED_VOICE_CHANNEL` until the Board no longer references it (check in cleanup) |
| CoS managed host (`domains/cos/managed_realtime.py`, `realtime_tools.py`) | Confer "Realtime · OpenAI", if enabled (unverified) | Confer UI no longer offers it when the direct path is on | removed | — |
| Shared managed sideband (`controlled_api.py`, `controlled_host.py`, `controlled_session.py`, `server_openai.py`, the controller's `managedBootstrap` branch) | MC managed, CoS managed | unchanged | removed with their tests; the controller branch removal touches German's file, so it lands with Part B's cross-surface checks, not alone (§14.3) | `voice_control_*` tables and their rows (§10) |
| `controlled_config.VOICE_MANNER`, `TRANSCRIPTION_HINT` | managed sessions | no longer reached by MC or CoS | removed with `controlled_config.py`; the kept clauses live in cards and core rules (§6.2) | `core/agent_partner.PARTNER_STYLE` (still used by typed CoS, `confer_service.py:341`) |
| CoS chained mode: `ConferVoiceController` on Confer, `/ui/speech`, `SpokenReplyStore`, `speech_output`/`speech_url` in `_ui_turn` | Confer "Voice · turn by turn" | removed from the Confer UI | server code removed | — |
| `create_confer_voice_blueprint` (`core/realtime_voice/confer.py`) | the chained mode **and CoS Dictate** | stays (Dictate) | narrowed to Dictate: its capabilities answer and bootstrap stay; nothing else uses it | yes, until Dictate gets an `en-US` memo bootstrap (later, not 1.2) |
| `realtime-confer-controller.js` | MC relay, CoS chained | stays (shared file) | removed once no page imports it; the release check proves none does | — |
| Chunk A bench page (`core/realtime_voice/bench/`) | Robert's measurements | stays | stays | yes: the latency bench and the P0 snippet |

## 10. Migration of existing data

- **MC:** chained-relay voice turns are ordinary kept notes and replies; they stay as they are. Managed-path
  transcript notes (`managed_voice_transcript:` authors) keep rendering as today: a session without a start marker is
  shown as before (`mc_voice_reveal.py:18`). Nothing to convert.
- **CoS:** managed sessions kept no transcript server-side; their journal rows (`voice_control_owner`, `_session`,
  `_call`, `_usage`) stay in the database untouched in 1.2 and in cleanup. Dropping tables or deleting rows is not
  part of this spec and would need Robert's separate word. `SpokenReplyStore` is in memory with a 120-second life:
  nothing to migrate. `conversation_log` lines from typed turns are unchanged; voice lines are added from 1.2 on
  (D6).
- **Budget ledger:** unchanged; new entries carry `domain="cos"` and kind `realtime_direct_session`.
- **No backfill.** Old voice sessions are not re-revealed, re-summarized or re-sent.

## 11. Rollout

### 11.1 Switches

| Switch | Where | Off means |
|---|---|---|
| `MINIMOI_MC_VOICE` + `MINIMOI_MC_VOICE_DIRECT` | portal | MC direct routes absent (as built) |
| `MINIMOI_COS_VOICE_DIRECT` | `cos-scheduler`, portal | CoS direct routes absent; Confer byte-for-byte today |
| production admission (D11): `MINIMOI_GUILD_DIRECT_VOICE_PRODUCTION`, `MINIMOI_COS_DIRECT_VOICE_PRODUCTION` | `production_environ` (`guild_mounts.py:275-300`) and the CoS equivalent | the direct switches are dropped in production even if set |

Recommended (D11): keep the direct switches in `PRODUCTION_HELD_OFF`, admitted only by an explicit production flag,
exactly as the managed path is (`guild_mounts.py:290-293`). Then the switch-over and the rollback are environment
changes on a released image, not code releases.

### 11.2 Dev first

Dev is the Mac through the Cloudflare tunnel (HTTPS, so the phone works). Order:
1. Release check green on the branch for both hosts (§12.2), providers blocked.
2. Both switches on in dev only. Robert on the Mac: MC script, then CoS script, both providers (§11.3).
3. Identity audition and voice ids (§7). Card text finalized; releases adopted; pins set.
4. Robert on the phone, both hosts, one provider each at least.
5. Conversation-quality gate (§12.4) passed by Robert's judgement, not an agent's.

### 11.3 Robert's listening test scripts

**MC (Mac, then phone), each provider:**
1. "MC, what's open on the board today?" (a consult).
2. A sentence with a deliberate one-second pause in the middle (one turn).
3. Interrupt MC mid-reply with a new question (playback stops, he follows).
4. "Make a note that the voice test ran on <provider>." (saved only with a receipt).
5. A German sentence, then an English question (language follows, returns).
6. "Check with MC" button once, then again on the same words ("Already checked").
7. "We're done" → "Shall I sum up?" → "yes"; confirm the summary card in writing.
8. Reload during a second short session: the recovery line, then the reveal.

**CoS (Mac, then phone), each provider:**
1. "What's on my agenda this week?" (consult to Agent A).
2. The paused sentence.
3. Interrupt mid-reply.
4. "Save a note: voice test on <provider>." (receipt).
5. "Remind me at 5 pm to stretch." (reminder receipt with its time).
6. A German sentence (her manner holds).
7. Open the floating MC panel during the session (her voice ends first).
8. Dictate a draft after ending (Dictate unchanged).
9. "We're done" → summary → decide on another device.

**Marks Robert records** (no transcript in any log): sounds like MC / like CoS; accent held; a stray German word
did not flip the language; names pronounced right; felt as quick as Maria; anything said that was not true.

### 11.4 Identity audition

The same 60-second script per voice candidate, per person, per provider: greeting-free start, a short answer, a
German phrase, a name list ("MiniMoi, Master Craftsman, Chief of Staff, Claude Code, Codex, Grok"). Robert's one
question: is this MC? is this CoS? Manner, brevity and pronunciation must not differ by provider; timbre will.

### 11.5 Production switch-over

One bundle, deployed on Robert's word (bundle rule), from a clean `origin/main` release worktree and PR:
1. Merge the 1.2 code with both production admissions off. Per the classifier this redeploys `portal`, `german`,
   `portuguese`, `cos-bot`, `cos-scheduler` (§2.4).
2. **German and Portuguese no-change check** on production right after: the release check's German and Portuguese
   assertions (§12.2), then a two-minute listen on Gespräche and Conversas by Robert (one provider each). Any change
   is a rollback of the bundle.
3. Turn on MC's production admission. Robert's short MC script on the phone.
4. Turn on CoS's. Robert's short CoS script on the phone.
5. One week of daily use (D9), then the cleanup release (§9).

### 11.6 Rollback

- **Fast:** turn the production admission off (env, restart portal or `cos-scheduler`). MC and CoS show "Voice
  unavailable: live voice is not on here." Typing and Dictate work. No silent fallback.
- **Full:** redeploy the previous image tags (recorded in the release note before step 1), which contain today's MC
  relay and CoS modes.
- **Data:** nothing to undo. Kept records stay; a rollback hides no session and re-sends nothing.

### 11.7 Deploy only what changed

1.2 touches `core/realtime_voice/`, `minimoi_portal/`, `domains/cos/`. The classifier then deploys five images; that
is the honest cost of shared code. To keep German and Portuguese behaviour unchanged in that deploy: every change to a
file they load is additive and opt-in (no new required callback, no changed default), and the tests in §12.1 prove
their bootstrap requests and answers are byte-identical. The cleanup release removes code from files German loads
(the controller's managed branch) only in Part B (§9).

## 12. Tests, cross-surface checks and the quality gate

### 12.1 Automated (providers and Keychain blocked, `-p noprov`)

- **Core:** session ledger, caps, end intent, transcript validation, summary and decision flows, driven through a
  fake adapter. Card loader: pin, hash, `language` allow-list, refusal cases.
- **No-copy check:** a test fails if any file outside `core/realtime_voice/` defines a consult ledger subclass, a
  transcript validator, a summary prompt, or a browser function bridge.
- **MC:** every Chunk B test passes unchanged (§5).
- **CoS:** the same refusals as MC (forged, replayed, late calls; missing CSRF at the portal; wrong owner; no store);
  `save_note` only with a receipt; reminder receipt; `/new` refused; file claims enforced; switch off = Confer
  byte-for-byte; composed instructions within 24,000 bytes and in order; no `VOICE_MANNER` text in her direct session
  except the clauses written into her card and the rules.
- **German and Portuguese no-change:** the same bootstrap request body, the same composed instructions, no tools, no
  voice notices, the same answer keys, no new import at import time; the controller with no new callback behaves as
  before (`tests/realtime_voice_direct_host.mjs` pattern).
- **Node:** the five Guild direct suites; a Confer suite for the live bar, reveal, recovery, summary card, Dictate
  handover, companion handover and `pagehide`.
- **Known failures left alone** (they fail on `main`): `test_html_server_db_conn_requires_explicit_role`,
  `build_mc_handbook.mjs`, `confer_input_controls.mjs`, `confer_write_headers.mjs`. Confer work in A3 should fix the
  last two if it can. The whole `tests` directory is not run in one go (a Telegram poller from `.env`, CC_17).

### 12.2 Release check (per host, Spec 164 §12)

On the deployed configuration, through the real backends with stubbed transports:
- capabilities report `direct_voice` with the intended providers, persona, locale and card release;
- one stub-provider session with one consult reaches the real typed turn (MC: OpenClaw adapter; CoS: her turn
  service with a stubbed runtime) and returns an answered receipt;
- forged and replayed call ids, a call after End and a missing CSRF token each have one effect or none;
- with the admission off: "Voice unavailable", and the page references no managed, chained or relay script;
- German and Portuguese bootstrap answers unchanged.
Robert's microphone and phone tests stay separate and outstanding until he does them.

### 12.3 Cross-surface checks (Spec 164 §13)

Guild Voice ↔ Dictate; Confer Voice ↔ Dictate; German voice → floating MC → German; Confer voice → floating MC →
Confer; closing the panel while connecting and during a consult; two sessions in one conversation (summary covers the
second only); a session past the continuity window; microphone denied; network loss and End during a consult; a
receipt after interrupted speech; MC conversation switching; a CoS session on two devices. Recovery after an abnormal
end is tested on every chunk, on both hosts.

### 12.4 Conversation quality is a release gate (Spec 164 §13a)

A mechanically correct session that asks permission to check, narrates receipts, reads lists aloud, or answers in
paragraphs fails, whatever its latency. Scored by Robert on his scripts: understood intent, justified clarification,
continuity, useful action, truthful and concise reply, perceived responsiveness. Latency: speech end to first audible
playback, per reply, ordinary and consult replies apart, on each provider; ordinary replies no worse than German's
Phase 0 numbers on the same provider (measured as first transcript there, §2.3; 1.2 records both metrics so Part B
has a first-audible baseline).

## 13. Part B — Language domains (queued; starts after 1.2 is in production)

Part A is §3–§12. Part B is §14. It is specified now so it can be queued; no Part B work starts before 1.2 is in
production and Robert says so.

## 14. German and Portuguese, together

### 14.1 Scope and parity rule

German Gespräche and Portuguese Conversas move together, in the same chunks and the same release. Every change is
mirrored to both in the same commit series (the German/Portuguese parity rule). Neither is promoted alone.

### 14.2 What reaches them

- **Measurement hooks and the playback event:** the controller's optional `onPlayback`, and the P0 snippet's
  first-audible measure.
- **Card composition, for persona voices only:** their persona voice ids may come from a card-shaped source, if and
  only if the resulting session is byte-identical in instructions; no MC or CoS text.
- **Shared controller fixes:** `pagehide` ends the session; a server-ended session ends the provider; the End-while-
  connecting wait (CC_16 noted the same race in direct mode for German and Portuguese).
- **The floating-panel handover:** a `minimoi:companion-opening` listener on both pages, so the panel waits for their
  voice to end (they have none today, §2.3).
- **Cache keys:** bump `?v=20260809-ga1` on both pages so browsers fetch the changed controller.
- **Removal of the controller's `managedBootstrap` branch** (§9), with the German/Portuguese no-change tests.
- **Later, if built by then:** pause the microphone without ending the session (Spec 164 §17), built once in the core,
  reaching MC, CoS, German and Portuguese together.

### 14.3 What never reaches them

Tools; consults; transcript posting, hiding or reveal (they keep their own `finalizeAndSave`); summaries; MC's or
CoS's cards, manner or rules; the direct host's routes; budget admission (D13: no behaviour change, recommended
none in Part B).

### 14.4 Phase 0 gates, both languages, both providers

Before any Part B change, on the production commit then deployed:
- **German:** re-run Phase 0 on OpenAI and Grok, now with first-audible playback (the 27 Oct runs measured first
  transcript only).
- **Portuguese: its own baseline, first time.** No Portuguese numbers exist. Same snippet, same script translated, on
  OpenAI and Grok, with a Portuguese persona.
- Record per provider: committed turns per scripted thought (counted from provider speech-stop on Grok, §2.3),
  interruption stops playback, speech end to first transcript and to first audible playback, transcript delivery at
  end, persona voice, language held.
- Promotion requires each number met or improved on both providers in both languages; persona voices unchanged;
  transcript delivery unchanged; floating-panel handover working. Any regression blocks both.

### 14.5 Persona voices unchanged

German and Portuguese persona voice ids, scenes, prompts and settings do not change in Part B. A persona voice
change is a separate owner decision.

### 14.6 Rollback

The previous German and Portuguese image tags, recorded before the release. Both roll back together.

## 15. Changes to other documents (companion, not in this file)

1. Spec 164: a header note that Spec 165 replaces its phase order (§9–§11).
2. `UI_CONTRACT_chunk_B.md` → the 1.2 direct-voice contract, person-neutral, with the CoS host table.
3. `B_direct_path.md`: unchanged (history).
4. Shared voice standard, "Current mapping": MC (Guild and floating panel) and CoS Confer are Conversation mode,
   turn owner the realtime provider, reasoning owner the provider with allow-listed `consult`/`save_note` into the
   person's typed turn; MC and CoS Dictate are Memo mode. Robert approves the amendment with this spec.

## 16. Open decisions for Robert

**D1. CoS manner.** (a) Keep today's effective manner, Austrian accent and Viennese partner included, written into her
card; (b) remove the Austrian identity now and write a CoS-only manner; (c) a new manner, written with Robert.
*Recommendation: (a) for 1.2, then decide by ear in the audition; any change is a card change.*

**D2. CoS voice ids.** (a) Draft card keeps OpenAI `cedar` (today's), Grok by ear; (b) leave unset (`marin`/`eve`);
(c) choose both by ear before the switch. *Recommendation: (a), confirmed or changed in the audition.*

**D3. MC voice ids.** MC speaks `cedar` in production today; the direct default is `marin`. (a) Choose by ear before
the switch; (b) preset `cedar`; (c) accept `marin`. *Recommendation: (a).*

**D4. Standalone page.** (a) Fold Spec 164 §5's standalone acceptance into the dev listening test on MC and CoS, keep
the bench as a measurement tool; (b) keep it as a separate gate first. *Recommendation: (a): MC on dev is the same
code and the first thing Robert should hear.*

**D5. Where CoS keeps voice records.** (a) `<COS_CONVERSATIONS_DIR>/voice/`; (b) a new `COS_VOICE_DIR`; (c) Postgres.
*Recommendation: (a), file first; voice refuses to start without it.*

**D6. CoS recall of voice sessions.** (a) Append revealed sessions to `conversation_log` as DATA; (b) keep them only
in the voice store. *Recommendation: (a): she can then recall what was said, as MC can.*

**D7. "Download transcript on End" on Confer.** (a) Keep, built from kept records; (b) drop.
*Recommendation: (a).*

**D8. CoS chained "turn by turn" mode.** (a) Remove from the UI at the switch (server code in cleanup); (b) keep as a
manual mode. *Recommendation: (a): one voice, no fallback; Dictate stays.*

**D9. When old code is deleted.** (a) A cleanup release after one week of daily production use and Robert's word;
(b) delete in the switch release; (c) keep indefinitely. *Recommendation: (a); rollback in the meantime is the
previous image.*

**D10. Allowance per host.** (a) One `MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO` for both, entries tagged by domain;
(b) a separate value per host. *Recommendation: (a).*

**D11. Production admission.** (a) Keep the direct switches held off in production, admitted by explicit flags
(`MINIMOI_GUILD_DIRECT_VOICE_PRODUCTION`, `MINIMOI_COS_DIRECT_VOICE_PRODUCTION`); (b) remove them from the held-off
list in the release code. *Recommendation: (a): switch and rollback become env changes.*

**D12. Who builds the Confer UI.** (a) Claude Code, as with Chunk B's UI (CC_16), Codex reviews; (b) Codex, per the
standing UI split. *Recommendation: (a), one writer for the shared browser module and both pages.*

**D13. Budget for German and Portuguese.** (a) No budget admission in Part B (no behaviour change); (b) add it.
*Recommendation: (a); revisit when language voice use grows.*

**D14. MC and CoS switch together or a day apart.** (a) Same release, admissions turned on one after the other on
the same day (§11.5); (b) MC first, CoS a few days later. *Recommendation: (a), per "both from the start"; (b) only
if the CoS dev test is not accepted in time.*

**D15. Third reviewer.** (a) Claude chat (reviewed 164); (b) Grok; (c) MC. *Recommendation: (a).*

**D16. Gate on CoS agent approvals.** If the §6.4 inventory finds an approval flow a bare "yes" can trigger in
`cos-agent-a`: (a) refuse bare confirmations in CoS voice, as MC does; (b) allow them. *Recommendation: (a).*

**D17. Person-neutral voice rules.** (a) Move the brevity, interruption and reporting clauses of `VOICE_MANNER` into
the core's direct rules for both MC and CoS; (b) MC as built (card only), CoS gets them in her rules only.
*Recommendation: (a): one standard, and MC gains rules he had on the managed path.*

## 17. Not in this build

A fourth voice implementation, a copied controller or a domain session loop. A server-held xAI session or media
relay. German or Portuguese tools, summaries, transcript reveal or MC/CoS personality. Changes to German or
Portuguese persona voices. Curator voice. Telegram voice (`telegram_voice` channel untouched). Voice cloning. A
French CoS (possible later, §4.5). A memo `en-US` bootstrap for CoS Dictate. Command mode. Dropping
`voice_control_*` tables. Production deploys or flags without Robert's word. Choosing voice ids for Robert.

## 18. Future (consideration only)

- **Dessa (Block), full duplex.** As of 27 Oct a research preview with no public API, pricing or language list
  (Spec 164 §16). It would join as a provider through the same registry and adapter shape; cards and records stay
  MiniMoi's. Full duplex changes the turn model (the standard assumes provider VAD with a 1,200 ms silence), and the
  consult bridge needs a model that can call tools. Revisit when it has an API or weights.
- **Kept open now, at no cost:** start/end timestamps on every record (overlap representable); host-invoked consults
  (a wake phrase later); VAD settings as provider parameters; a local model would be a registry entry with no
  credential mint.
- **Pause the microphone** without ending the session (§14.2).
- **Morning brief voice line** (Spec 164 §17).
- **CoS on the floating panel** (today the panel is MC's Guild page only).

## 19. Build plan

Each chunk: **build → independent review → fixes → verify**, then one handoff file. One writer per workspace.
Reviews are by a fresh agent given the code, this spec and the contract, not the builder's conclusions. No agent
polls or pings: each chunk ends with one file in
`planning-studio/initiatives/INIT-2026-0010-grok-dev-onboarding/minimoi-1-2-voice/`, read when Robert starts the
next session (CC_13). No push, merge, flag or deploy inside a chunk without Robert's word.

| Chunk | Builds | Review | Verify / done when |
|---|---|---|---|
| **A1 Core extraction** | `direct_host.py`, `errors.py`, `direct-voice-session.js`, controller `sessionId()`/`speak()`, adapter cache keys, card pins; MC moved onto the core | independent review of the diff; Codex on function | all Chunk B suites unchanged; no-copy check; German/Portuguese no-change tests |
| **A2 CoS backend** | `domains/cos/direct_voice.py`, her card release and pin, composition and bound, store, summary path, portal gate, switch; the §6.4 approvals inventory first | independent review; Codex | CoS refusal set; release check (CoS); 24 KB bound; switch off = Confer unchanged |
| **A3 Confer UI** | live bar, provider choice, reveal, recovery, summary card, `pagehide`, companion, Dictate untouched, chained mode hidden | independent review including a phone-width visual check | Confer node suite; Dictate and companion handover tests; the two Confer known failures fixed if possible |
| **A4 Dev and listening** | dev switches on; release checks; Robert's Mac and phone scripts; audition; voice ids; card texts adopted | Robert's marks; Claude Code records numbers only | §11.2 steps 1–5 done by Robert; quality gate passed |
| **A5 Production switch** | release worktree, PR, bundle; image tags recorded; admissions per §11.5 | Codex on the release; Robert's go | German/Portuguese no-change check; Robert's phone scripts on production |
| **A6 Cleanup** | §9 removals, after D9's week | independent review | release check: no page references a retired path; managed tests removed with their code |
| **B0 Baselines** | German re-run and Portuguese first baseline, both providers, first-audible | Claude Code checks the numbers | §14.4 table filled by Robert's runs |
| **B1 Language integration** | §14.2 changes to both, mirrored; controller managed-branch removal | independent review; Codex | no-change tests; cross-surface checks |
| **B2 Listening and promotion** | Robert's runs against B0 | Robert | every number met or better, both languages, both providers; else rollback |

## 20. What this spec could not verify

- Production environment values in `/opt/minimoi/.env`: `MINIMOI_COS_MANAGED_REALTIME` (so whether Confer in
  production offers the managed mode or only turn-by-turn), `MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO`,
  `VOICE_REALTIME_PROVIDER_DEFAULT`, `COS_CONVERSATIONS_DIR`, `MINIMOI_MC_EVERYWHERE`, `MINIMOI_GUILD_CHAT`. Read
  only from compose defaults.
- Whether `cos-agent-a` has approval flows a bare spoken "yes" could trigger (§6.4).
- How CoS's OpenClaw backend can be given a separate runtime session for her summary (§6.7).
- The size of `cos_memory.md` in production, and so whether her composed instructions fit 24,000 bytes (§6.3).
- Whether opening the floating MC panel over a live German or Portuguese session leaves both microphones open: the
  code has no handover listener on those pages; no browser test was run.
- Every listening, phone and latency claim for the direct path: nothing has been heard; Chunk B ran with providers
  blocked.
- Grok Voice's first-audible timing (the 27 Oct runs suggest its transcript arrives near the end of a reply; a
  hypothesis until measured).
