# Spec 165 — MiniMoi 1.2 voice: Master Craftsman and Chief of Staff on one standard direct-path voice; German and Portuguese next

| | |
|---|---|
| **Spec** | 165 |
| **Date** | 28 October 2026 (v0.1, v0.2 and v0.3) |
| **Author** | Claude Code, for Robert |
| **Status** | **Draft v0.3 for review.** Robert authorized the build on 28 Oct (§1). This spec authorizes no deploy, flag, merge, credential or provider call. |
| **Decision owner** | Robert |
| **Review record** | v0.1 (`64b1896f`): independent review (`docs/specs/reviews/REVIEW_independent_spec_165_v0.1.md`, `5f2ebdce`); Codex (`docs/specs/reviews/REVIEW_codex_spec_165_v0.1.md`, `89edb852`); Codex's Chunk B code review (`planning-studio/…/trial-01-mc-voice-2026-10-27/REVIEW_codex_chunk_B.md`), carried in §5. v0.2 (`729d29c9`): Codex's delta review (`docs/specs/reviews/REVIEW_codex_spec_165_v0.2_delta.md`, `49dae6de`), V2-1 to V2-5, all answered in v0.3. **Pending on v0.3:** Grok, then Claude Chat. |
| **Builds on** | Spec 164 v0.3, approved 27 Oct (`437b7f8c`, PR #421); its phase order (§9–§11) is replaced here, the rest stands unless this spec says otherwise |
| **Code base** | `grok/mc-voice-conversation` `93bf00c6` (PR #426, open; never merged alone). Build in progress: chunk A1 on `claude/voice-1-2`, stacked on `93bf00c6`, first commit `99b3d0f0`; its fix round at `005c9e81` (`A1_FIX_RESULT.md`), still being extended. §5.7 says where A1 does not yet meet this contract |
| **Initiative** | INIT-2026-0010. Part B: issue #433 (queued) |
| **References** | `file:line` is `93bf00c6` unless marked `main` (`4954ad9f`), `A1` (`99b3d0f0`) or `A1F` (`005c9e81`). **Unverified** items are in §20 |

## Changes from v0.2

CX = Codex's delta review of v0.2; A1F = the A1 fix round at `005c9e81`.

| Finding | Addressed in |
|---|---|
| CX V2-1 (P1): one effect per owner item across both tools, the request id from that key; bind each call to its own turn (CX A1-C2) | §5.1 |
| CX V2-2 (P1): the CoS recall projection follows a revision and is rebuilt when late records land | §6.5 |
| CX V2-3 (P2): one exact repair of a damaged tail | §6.5 |
| CX V2-4 (P2): `pagehide` is one close envelope, counted in event ids; a missing End is `unconfirmed`; the barrier's timeout is final (CX A1-C3) | §5.2 |
| CX V2-5 (P2): a summary has its own attempt; a client timeout is not a failure; one proposal across devices | §5.3 |
| CX: playback outcomes named apart from "heard" (B6) | §5.3 |
| CX S1 and code checks: `/new` and other commands refused before dispatch; outcomes classified by the dispatch boundary, never by exception class | §5.1; §6.4 |
| CX: D20 enforced before dispatch and tested, not left to the prompt | §6.4; §12.1 |
| CX: the 10–25 s holding-line gap | §12.4 |
| CX: handover waits for the microphone and transport to be released, a connecting session included | §14.1 |
| CX: record routes in S3 and S4; an S2 provider failure tested apart from an S3 rollback | §12.2 |
| CX: sessions a day is capacity, not a promise | §7 |
| CX: updated recommendations on D1–D23; D5 now agrees | §16 |
| Adopted from A1F: the key `(session, owner_item_id)`; the request record's text hash; the store's `put`/`get` with the host's handle first; `unacknowledged` as event ids; `capture` with `missing`; `pagehide` as A1's single End; the summary gateway's alias and key names | §4.4; §5.1; §5.2; §5.4 |
| Where A1F does not yet meet this contract | §5.7 (new) |

## Changes from v0.1

IR = independent review (M must fix, S should fix, m minor); CX = Codex spec review; B = Codex Chunk B review.

| Finding | Addressed in |
|---|---|
| Robert, 28 Oct: build authorized, A1 in progress, one bundle, nudge retired, host topology, Part B #433 | §1; header; §11.2; §19 |
| IR-M1 merging 1.2 turns production MC voice off | §2.1; §5.6; §11.1 (relay kept until admitted, A1 `99b3d0f0`); §9 |
| IR-M2 full release, not five images | §2.4 (classifier run); §11.7 (classifier rules in A1; output recorded) |
| IR-M3 voice store in the folder her runtime greps | §6.5; D5 now (b); §12.2 |
| IR-M4 Confer request id must be a UUID | §5.1; §6.4 |
| IR-M5 30 s portal timeout, 120 s turns | §5.1 (`pending`); §6.7 (route timeouts) |
| IR-M6 no reminders by voice | §6.4; D20; scripts and tests changed |
| IR-M7 D1 mis-framed: one shared persona | §1; §6.2; §4.5; D1 |
| IR-S8 trust extension to CoS | §6.9; D18 |
| IR-S9 German transcribed as English | §7; D19; scripts check the transcript |
| IR-S10 CoS admission; CSRF on a flag; reads | §6.7; §6.8 |
| IR-S11 voice notes show in Record and memory | §8; D21 |
| IR-S12 language checks before production | §11.2 step 5; §11.5 step 2 |
| IR-S13 latency criterion | §12.4 |
| IR-S14 two facts settled | §2.2; §5.4; §20 |
| IR-S15 approvals inventory premise | §2.2; §6.4 (inventory done); D16 |
| IR-S16 mixed controller versions | §14.1; D23 |
| IR-S17 two hosts on one cap | §7; D10 |
| IR-S18 phone cases, CoS script, pass conditions | §11.3 |
| IR-m19 four `.mjs` suites · m20 provider order · m21 MC kind `cos` · m22 env override · m24 two Dockerfiles | §5.5 · §2.4 · §2.1, §5.6 · §7 · §2.2 |
| IR-m25 Chunk B gaps · m26 live runtime session · m27 Grok gets her briefing · m28 note wording, Grok alias | §8 · §8 · §6.3, D2 · §11.3, §7 |
| IR-m23 line references | `controlled_host.py:14` fixed; `mc_voice_transcripts.py:7` **not changed, because** the import is on line 7 at `93bf00c6` |
| CX-S1 CoS request and receipt contract | §5.1; §6.4 |
| CX-S2 tool-disabled summary | §5.4; D22 |
| CX-S3 CoS store and recall projection | §6.5 |
| CX-S4 off, unadmitted, rollback states | §11.1; §11.6; §12.2 |
| CX-S5 do not extract Chunk B defects | §5; §5.5; §19 |
| CX-S6 CoS read identity | §4.4; §6.7 |
| CX-S7 handover needs Part A work | §14.1; D23 |
| CX-S8 like-for-like latency, language gate | §12.4; §14.3 |
| CX decision table; notes (no-copy by architecture, French is code, dev is not the Mac) | §16; §12.1, §4.5, §11.2 |
| B1, B7 · B2, B4 and the pagehide edge · B3, B5, B6 | §5.1 · §5.2 · §5.3 |

## In plain words

MiniMoi 1.2 is one voice, used two ways. Master Craftsman and Chief of Staff both talk with Robert through the
direct voice path German Gespräche already uses and Chunk B extended for MC: the browser talks to OpenAI or Grok
Voice directly, and when real work is needed the voice asks the real agent, which runs as if Robert had typed.

- **MC in production moves** from today's turn-by-turn relay to a live conversation: he can interrupt, a pause
  doesn't send, nothing scrolls while he talks, and what was said appears at End with a short summary he confirms
  in writing. Until the switch, production keeps the relay.
- **CoS moves at the same time onto the same code:** one voice mode instead of two, OpenAI or Grok. She stays the
  shared Austrian colleague persona Robert approved for both (§6.2), with her own name and voice.
- **Dictate is unchanged** on both pages.
- **German and Portuguese behave as today**, with one safety line if Robert agrees: opening the floating MC panel
  ends their voice first (D23). Their improvements are Part B (#433), after 1.2 is in production.

"Standard" means one server core, one browser module, one card format, one capture/reveal/summary contract
(corrected by the Chunk B review, §5), one consult contract, one budget, one release check; MC and CoS are thin
adapters. Nothing is copied.

**Honest limits.** Nothing has been heard yet; Robert's listening and phone tests are the acceptance. Voices are
chosen by ear (MC speaks `cedar` today; the direct default is `marin`). Shared code redeploys the German and
Portuguese images, and the bundle may deploy more (§11.7). Provider bills are real; the daily allowance is
MiniMoi's limit, not a provider cap. A CoS voice consult carries her full typed authority, including running
commands in her runtime (§6.9).

## 1. Robert's decisions (binding)

28 October 2026:
1. "We need a thorough spec and should target making MC and CoS both use new solution from the start. That is
   minimoi 1.2 and we defer German until in prod with 1.2."
2. "spec german now too and put it into the queue + portuguese and we do language domains together." Part B
   (#433), together, after Part A is in production.
3. "so even more important to standardize CoS." A core goal of 1.2.
4. "you start building voice." A1 is in progress on `claude/voice-1-2`. PR #426 is not merged on its own; 1.2
   ships as one bundle.
5. The supervision "continue" nudge is retired (PR #434); nothing here relies on it. Permanent solutions, no
   workarounds, learn on the real platform.
6. Hosts: production (AWS); an always-on staging host on a cheaper cloud (arriving 28–29 Oct); the portable Mac. A
   host's role is configuration, not hardware (INIT-2026-0011).

Standing (Spec 164 v0.3; `coordination/ROBERT_DECISION_spec164_reuse_and_rollout.md`): German's code is the base,
with no fourth implementation, copied controller or domain session loop; Dictate stays separate; each person has
their own card; latency no worse than German on the same provider; Spec 164 §3's trust model for MC (CoS: D18);
Dessa is future only.

**The shared persona.** MC and CoS share one canonical persona Robert approved on 16 Oct, compiled into CoS's
runtime (§2.2). "Their own card" therefore means their own name, voice ids and voice-surface wording within it.
Changing who either is, is a persona adoption, not a card edit (D1).

## 2. What exists today (verified)

### 2.1 Master Craftsman

| Fact | Reference |
|---|---|
| **Production MC voice is the chained relay:** compose sets `MINIMOI_GUILD_VOICE_PRODUCTION=1`, `MINIMOI_MC_VOICE=1`; direct and managed are held off; `main`'s composer falls back to `ConferVoiceController` | `docker-compose.prod.yml:242-243`; `main:mc_voice.js:136-141`; `guild_mounts.py:220-222`, `:288-293` |
| `93bf00c6` dropped that fallback ("Voice unavailable"). **A1 restores it:** direct, else managed, else the relay as on `main`; an offered path that fails never falls back | `mc_voice.js:136-153`; `A1:mc_voice.js` |
| Relay routes: `/mc/voice/bootstrap`, `/mc/voice/speech/<id>` (`cedar`) | `mc_voice.py:86-193`; `openai_speech.py:12` |
| The direct path is built and off unless `MINIMOI_MC_VOICE=1` and `MINIMOI_MC_VOICE_DIRECT=1`, the second held off in production | `mc_voice_direct.py:46`, `:592`; `guild_mounts.py:222` |
| MC's card: candidate release `0.1.18` (agent runs `0.1.16`), pinned by constant; `voice_card.py` hard-codes MC's path | `…/0.1.18/manifest.json`; `deploy-mc-agent.yml:47`; `mc_voice_direct.py:48`; `voice_card.py:16` |
| Session text = MC's rules + direct rules, card, recent voice turns | `mc_voice_direct.py:78-102` |
| Couplings: `SessionRefused` and MC's rules from the managed modules; the UI reads `_sessionId`, `_adapter.sendContinuationInstruction` | `mc_voice_direct.py:36`, `:92`; `mc_voice_transcripts.py:7`; `mc_voice_direct.js:222-223` |
| Capture keys items by place (`t<index>`), stable for the session | `mc_voice_direct.js:99`, `:122` |
| MC kind is `openclaw` per deploy; its rollback sets `MINIMOI_GUILD_CHAT=cos` (consults then run on `cos-scheduler`); the live value is unverified | `deploy_mc_container_ec2.sh:197-199` |

### 2.2 Chief of Staff

| Fact | Reference |
|---|---|
| Two Confer voice modes when managed is enabled (compose default off; `.env` unverified), else turn by turn only. Managed: `gpt-realtime-2.1`, `cedar`, her instructions + `VOICE_MANNER`, no transcript kept | `cos_ui.html:58-61`; `managed_realtime.py:15-53`; `controlled_config.py:8-59`; `controlled_host.py:14` |
| Her voice instructions = `_build_system_prompt()` (soul, context, all of `cos_memory.md`, ops, build state, date) + a voice paragraph | `chief_of_staff.py:614-679` |
| **Shared persona:** `MASTER_CRAFTSMAN_PERSONA.md` v1.1, "Master Craftsman and Chief of Staff — shared persona"; "Your manner" is compiled into `cos-agent-a`'s SOUL, hash-locked, `approved_by: "Robert"`; "Voice and text should feel like the same teammate." Typed turns carry `PARTNER_STYLE` | `planning-studio/initiatives/INIT-2026-0007-interaction-vision/documents/MASTER_CRAFTSMAN_PERSONA.md:1-25`; `Dockerfile.cos-agent-a:19`, `:34`; `build-mc-soul.mjs:9-28`; `mc-role-lock.json:3`; `confer_service.py:341-342` |
| **Her runtime's authority:** tools profile `full`, `exec` `full`, sandbox off, elevated off; loads `cos-bounded-search`, `mc-cutoff-delivery` only (not `mc-actions`); mounts `data/`, `cos/outbox`, `cos/planning-studio`, `docs`, `agent_logs` read-write, `cos/conversations` read-only. `cos-scheduler` mounts the Docker socket | `docker/cos-agent-a/openclaw.json`; `Dockerfile.cos-agent-a:36-37`; `docker-compose.prod.yml:85-110`, `:440-455` |
| **Turn service:** `request_id` must be a UUID; notes return `note_save` (`saved`/`deduplicated`, `operation_id`); reminders only as `/remind <UTC ISO> <text>` → `reminder_set` (`reminder_id`, `due_utc`); "save a note that …" and "save a note: …" parse | `confer_service.py:38-55`, `:188-195`, `:255-327` |
| OpenClaw backend: session from `conversation_id`; `tool_policy` **not enforced** | `openclaw_backend.py:132-135`, `:340-344`, `:400-409` |
| Managed consults call `_process_confer_turn` directly, skipping `conversation_log` and file-claim checks | `realtime_tools.py:9-19`; `chief_of_staff.py:811-841`, `:1462-1481` |
| Chained mode: transcription credential, `/ui/send`, a 120 s `SpokenReplyStore`, "Hearing: …"; **CoS Dictate depends on its blueprint** | `confer.py:20-55`; `chief_of_staff.py:1429-1630`; `cos_ui.html:503-582` |
| One conversation, `owner`, no server thread; Record, Track and `/ui/memory` show `cos_memory.md` | `managed_realtime.py:26-31`; `cos_ui.html:296-326`; `chief_of_staff.py:1632-1650` |
| `COS_CONVERSATIONS_DIR` **is set** in production compose, on `cos/conversations`, which `cos-agent-a` mounts and greps; append is best-effort | `docker-compose.prod.yml:102`, `:431`, `:448`; `conversation_log.py` |
| Portal: CSRF on `/app/cos` writes only with the write guard (compose default empty) or under `api/realtime-voice/managed/`; the write marker only there, POST only; timeout `(5, 30)` except `ui/send`, `chat` | `app.py:722-756`; `proxy.py:303-306`, `:474-485`; `docker-compose.prod.yml:251` |
| `Dockerfile.cos-scheduler` (staging, production) and `Dockerfile.cos` (local) copy `core/`, `domains/`, not `guild_ui/`. One process | `Dockerfile.cos-scheduler:8-15` |
| Confer ends voice and Dictate on `minimoi:companion-opening` | `cos_ui.html:594-600` |

### 2.3 German and Portuguese

| Fact | Reference |
|---|---|
| Bootstrap without `owner`/`admit`; controller key `?v=20260809-ga1`; end via `controller.endSession(…)` → their `finalizeAndSave` | `german/html_server.py:127-131`; `portuguese/html_server.py:77-81`; `german_gesprache.html:1920`, `:2032-2033` |
| No `pagehide` end, no companion listener. The panel is injected on both pages when `MINIMOI_MC_EVERYWHERE=1` and waits only for listeners, so it opens at once over a live session, **in production today** | `companion.py:4-11`; `companion.js:45-56` |
| Phase 0, German only: speech end to first assistant transcript, OpenAI median ≈705 ms, Grok ≈775 ms (5 samples, two ~3 s waits); no first-audible; no Portuguese baseline | `P0_run_*_2026-10-27.md` |

### 2.4 Shared code and release mechanics

| Fact | Reference |
|---|---|
| `core/realtime_voice/` → portal, german, portuguese, cos-bot, cos-scheduler; `domains/cos/` → cos-bot, cos-scheduler; `minimoi_portal/` → portal; **unknown paths → full** | `classify_release.py:346-371`, `:405-407` |
| `origin/main...93bf00c6` classifies **full** (nine services) because of `services/mc_operations/personas/` and `scripts/mc_persona/`; without them seven (`core/mc_tools/` adds curator, system-bot) | run by Claude Code, 28 Oct |
| Adapters changed; the controller imports them under old keys | `realtime-voice-controller.js:24-25` |
| On Grok, the composed `session_config` goes through the browser | `xai_voice.py:13-18`, `:74-99` |
| Consult ledger: in memory, call-id dedup only, cap 12, holding line 10 s | `consult.py:13-64` |
| Provider: explicit, dev query override (not production), saved preference, `VOICE_REALTIME_PROVIDER_DEFAULT`, `openai` | `config.py:28-61` |
| Transcription language from the locale (`en-US` → `en`); managed CoS sent none, with a faithful-transcription hint | `bootstrap.py:61-64`; `xai_voice.py:82`; `controlled_config.py:31-33`, `:49-50` |
| Budget: $20 per Chicago day, durable, fail-closed, one ledger for portal and `cos-scheduler`; the session allowance has no default, and unset the direct host refuses | `budget.py:1-38`; `mc_voice_direct.py:117-121`, `:640-641` |

## 3. Goal and scope of 1.2

**Goal.** Robert talks with MC and with CoS, on the Mac and the phone, through one voice implementation with the
same behaviour, safeguards and release check, each sounding like themselves.

**Part A:** the core extracted from Chunk B with the review's corrections (§4, §5); MC on it (§5.6); CoS on it (§6);
providers, voices, budget (§7); safeguards (§8); retirement (§9); rollout and Robert's tests (§11); the language-page
safety touch if D23 is adopted (§14.1). **Part B (#433):** German and Portuguese, after 1.2 is in production (§14).

| Standard | Is | Lives in |
|---|---|---|
| Host core | session ledger, owner-action admission, capture boundary, end intent, summary and decision, routes | `core/realtime_voice/direct_host.py` |
| Controller | `RealtimeVoiceController` + additive `sessionId()`, `speak()`, playback drain | `…/static/realtime-voice-controller.js` |
| Browser module | bridge, holding line, capture and acknowledgments, barrier, heartbeat, End, `pagehide`, Check, summary | `…/static/direct-voice-session.js` |
| Card | `voice.json` schema 1 + `language`, `accent`, `transcription`; hash; pin | `voice_card.py` |
| Contracts | `UI_CONTRACT_chunk_B.md` corrected by §5; consult per §5.1 | the 1.2 contract (§15) |
| Budget | `admit(kind="realtime_direct_session", domain=<host>, required=True)` before any mint | `budget.py` |
| Release check | parametrized by host (§12.2) | `tests/realtime_voice/` |

## 4. Architecture: one core, thin host adapters

### 4.1 Why extract

About half of `mc_voice_direct.py` is host-agnostic: the session ledger (`:124-268`), transcript validation
(`:432-460`), the summary prompt (`:464-522`), the receipt line (`:335-350`), the route flows (`:679-807`). CoS cannot
import the Guild (`cos-scheduler` lacks `guild_ui/`), so that half moves to `core/realtime_voice/`, which every voice
image copies: **moved, not copied**, and moved with the §5 corrections, so both hosts get them once.

### 4.2 What moves where (server)

| Today | Moves to |
|---|---|
| `_Sessions`; `_events` and its limits | `direct_host.DirectSessions` (markers via the store); `direct_host` (values unchanged) |
| route flows `keep_events`, `end_events`, `owned_session`, `summary`, `decide` | `direct_host.install_direct_voice(app, adapter, *, url_prefix, environ, budget, may_start)` |
| `_summary_prompt`, `_summary_texts`, heads; `_receipt_line`, `JOB_DONE` | `direct_host`; the person's name and receipt kinds from the adapter |
| `TOOLS`, `DIRECT_RULES` | `direct_host.tools(person)`, `direct_rules(person)`; names stay `consult`, `save_note` |
| `done_intent`, `affirmative`, `receipt_parts`, `full_differs`, `state` (`mc_voice_reveal.py`); `_speakable` | `direct_host`; Guild's `view()` stays Guild |
| `SessionRefused`; MC's `INSTRUCTIONS` | `core/realtime_voice/errors.py`; the MC adapter (text unchanged) |
| `voice_card.load_card(release)` | `load_card(root, release)`, `load_pinned(root)` (§4.5) |

The MC adapter (`guild_ui/mc_voice_direct.py`) keeps the guard, conversation lookup, floor store (markers, tags,
`retain`, `view`), `_TaggedFloor`, the typed turn, MC's continuity and the title touch. The CoS adapter is
`domains/cos/direct_voice.py` (§6).

### 4.3 What moves where (browser)

`startRefusal`, `noticeWords`, `jsonPost`, `createCapture`, `directVoice` move to `direct-voice-session.js`, worded
with the person's short name ("Check with CoS"). `voiceThread` stays Guild; Confer gets a small renderer (§6.6). The
private members become public, additive `controller.sessionId()` and `controller.speak(instruction)` (a spoken
instruction to the voice; unrelated to the retired supervision nudge), unused by German and Portuguese.

### 4.4 The adapter interface

| Hook | MC (Guild) | CoS (Confer) |
|---|---|---|
| `read_owner()` | signed-in Guild owner | portal-forwarded identity, `resolve_user_id`, tier owner; **no** write marker |
| `write_guard(kind)` | owner, CSRF, record mode, MC turns on | `read_owner()` + the portal's verified-write marker |
| `conversation(requested)` | owned, saved, on-record conversation | `owner` only |
| `store` | floor notes behind the store interface | file store (§6.5) |
| `typed_turn(handle, action)` → outcome (§5.1) | MC's typed turn | `_process_confer_turn` (§6.4) |
| `card()`, `instructions()` | pinned card; rules, card, continuity | pinned card; rules, card, briefing, continuity (§6.3) |
| `summary_key`, `person` | MC's capped gateway key; `{"key":"master_craftsman","name":"Master Craftsman","short":"MC"}` | hers; `{"key":"chief_of_staff","name":"Chief of Staff","short":"CoS"}` |

**Store interface** (core-defined, adapter-implemented; refusals typed, never logged-and-continued). As built in
A1F, every method takes the host's handle first, because MC's store is per conversation floor:
`put(handle, session, record)` → `stored | duplicate | conflict | refused(reason)`, returning only once durable; and
`get(handle, session, id)`. A2 adds only what the CoS store needs: `close(handle, session, boundary)` →
`closed | already`, `records`, `state`, and `sessions(owner, needing_attention)` for the needs-attention list
(§6.5). MC did not need them. Records carry `schema: 1`, a stable `id`, `kind`, `at`. First write wins; the same id
with other content (anything but `at`) is a `conflict`.

The core owns bootstrap (with `owner` and `admit`), consult, capture, End, summary, decision, caps, admission, the
budget reservation before the mint, and capabilities: `direct_voice` when a start is allowed, `direct_records` when
the owner has sessions needing attention.

### 4.5 Cards and pins as data

A card lives in its person's release folder with a manifest and hash; the adapter reads `pin.json`
(`{"release": "<v>"}`). A card change is a new release plus a pin change, reviewed as an owner adoption (Spec 164
§3). `load_pinned` refuses a missing pin or release or a wrong hash. Schema 1 gains optional `language` (an allowed
locale; absent `en-US`; replaces MC's constant, `mc_voice_direct.py:50`), `accent` (a label) and `transcription`
(`"bilingual"`, D19). The manifest gains `persona: {source, version, sha256}`, checked against `mc-role-lock.json`
by the release check, so a persona change flags both cards.

A Portuguese CoS is a card with `"language": "pt-BR"`. A **French** CoS also needs `fr-FR` in `ALLOWED_LOCALES` and
the capability and transcription tables (`config.py:19`; `capabilities.py:47`, `:59`; `bootstrap.py:61-64`): a small
code change with tests.

## 5. The corrected direct-voice contract, and MC on the core

Codex's Chunk B review (B1–B7) reproduced lifecycle defects the Chunk B suites miss. A1 fixes them once, in the
core, before CoS uses it. This section is the contract A1 meets for both hosts.

### 5.1 One owner action, one effect; four outcomes (B1, B7, CX-S1, CX V2-1, IR-M4, IR-M5)

- **Identity.** Every consult, from the model or from Check, carries `owner_item_id`: the capture id (`t<i>`) of
  the completed owner turn it acts on. **The action key is `(session, owner_item_id)`, across both tools** (CX
  V2-1). One thing Robert said is acted on once, whether it reaches `consult`, `save_note`, Check, or more than one
  of them. Both typed-turn paths can save a note, so a key per tool would let one sentence save two.
- **Each call is bound to its own turn** (CX A1-C2).
  - A provider's function call is bound, through the response it belongs to, to the owner item that response
    answers, and acts on that item's words. It is never rebound to whichever turn completed last.
  - A call that cannot be bound, or could be bound to more than one turn, is refused or held, never guessed.
  - A repeated completion of the same captured item is not a new turn.
  - The model's arguments never choose other owner text.
  - Which provider events carry the response id is unverified (§20).
- **Durable admission at the host.** Before dispatch the core writes a `consult_request` record under the key,
  first write wins: `{schema, id, kind, at, owner_item_id, text_sha256}`.
  - The record keeps a hash of Robert's words, not the words; the words are in the visible consult record, and the
    hash keeps the request within MC's floor-note size.
  - A second dispatch of the key gets the stored outcome, or `in_flight` while the first runs here, or `unknown` if
    it was admitted but has no outcome and runs nowhere. A second dispatch means another call id, Check after the
    model, a retry, or the other tool.
  - The second tool is told the turn was already handled. It is never handed the first one's success as its own.
  - Call-id dedup stays as a second layer.
  - Check goes through the same dispatcher as the model; the disabled button is presentation, not the guard.
- **A new effect needs a new turn.** Saying the words again is a new turn, so a deliberate repeat is a new effect.
  A refusal before dispatch did nothing, so it does not use up the turn.
- **Operation ids derive from the key**, so platform dedup also holds:
  - MC's is `vc-<session>-<owner_item_id>`;
  - CoS's is `request_id = uuid5(VOICE_NS, f"{host}:{session}:{owner_item_id}")`, where `VOICE_NS` is a core
    constant.

  Neither contains a tool name.

| Outcome | When | Spoken | At reveal |
|---|---|---|---|
| `done` | the typed turn returned, with its receipt | the reply; "Saved." only with the receipt | answered / receipt |
| `not_done` | refused before dispatch, or the backend said it did not happen | "That did not go through. Nothing was done." or the refusal | not done + reason |
| `pending` | still running after 25 s | "That's taking longer; the answer will be in the conversation." | the outcome when it lands |
| `unknown` | dispatched, no answer: network loss, timeout, 5xx, edge 504, backend exception after dispatch | "I didn't get the result back. Check it in writing before asking again." | result unknown until reconciled |

- **Outcomes follow the dispatch boundary** (CX). `not_done` means nothing was dispatched, or an authoritative
  receipt says it did not happen. An exception's class alone never makes an outcome `not_done`. An error after
  dispatch is `unknown`, even when the backend's own exception says "failed".
- **Long turns.** The core runs the typed turn in a worker and waits 25 s (under every timeout), then `pending`;
  the receipt is written when it completes; the next heartbeat answer lists completed actions and the voice says
  the result once, at a pause. No extra request, no timer.
- **Reconciliation.** `GET …/consult/<session>/<owner_item_id>` returns the stored outcome and runs nothing. MC
  also names the conversation in the query, because its store is per conversation. The browser asks once after
  `unknown`; the store answers after a restart. A request with no receipt (the process
  died) stays `unknown`, shown so; nothing re-runs by itself.

*Regressions:*
- One effect each: manual→model, model→manual, concurrent, and repeated calls under new call ids.
- One effect: a manual Check followed by the model's `save_note`, and the reverse.
- A repeated sentence is a new effect.
- No "failed": an effect committed and its response dropped (the same-key read shows it).
- A backend that records its effect and then raises gives `unknown`, with one effect.
- Two turns overlapping, with a delayed function call: it is bound to its own turn.
- A duplicate transcript completion makes no new turn.

### 5.2 Capture barrier and honest retention (B2, B4, pagehide, CX V2-4)

- Events are `queued → sent → acknowledged`, or `failed(reason)` after two attempts under the same id. Only
  acknowledged events are kept; a 422 or an exhausted retry is `failed`, never a silent warning.
- **One barrier closes capture** for End, Sum up, the spoken "yes" and a server end. In order:
  1. stop admitting owner input;
  2. flush the open partial as `partial`;
  3. post everything unacknowledged;
  4. wait, up to 5 s;
  5. send the close with `{last_item_id, unacknowledged}`, where `unacknowledged` is the list of event ids the page
     holds no acknowledgment for.

  The spoken-yes path schedules the barrier outside the capture chain's callback, never awaiting the chain from
  inside it.
- **The barrier's timeout is final** (CX V2-4, A1-C3). Finalization carries the barrier's result forward and never
  awaits the abandoned post again, so End, the reveal and the page's end always complete.
- **The server closes at that boundary.**
  - It counts the listed ids it does not hold, and writes `capture: complete`, or `capture: incomplete` with
    `missing: n`, into the end marker. End answers the same.
  - Later posts are refused (409) and counted in memory and the log, not in the marker, because first write wins.
  - A turn that completes after the boundary, such as the voice's "Sure, summing up.", is not this session's record.
  - Robert's words still being transcribed at the barrier are inside it, kept as partial.
- "What was said is in the conversation." appears only for `complete`; otherwise "Some of what was said was not
  kept (n turns). What was kept is in the conversation.", and the summary prompt states the gap.
- **`pagehide` is its own close, not the barrier** (CX V2-4). A leaving page cannot wait, and separate keepalive
  requests arrive in no guaranteed order. So the page sends **one** keepalive close envelope: End, carrying the
  owed events that fit the keepalive budget, and the ids of those that don't.
  - Counts are in unique event ids, never batches. Anything not delivered is `missing`.
  - **If End itself never arrives,** the next bootstrap, recovery or reveal on any device shows that session's
    capture as `unconfirmed`, never `complete`.

  This is A1's single End carrying one batch, now with the count. The ordered keepalive batches of v0.2 are dropped:
  a browser cannot order them.
- **A late consult receipt** is kept and revealed after the summary, marked "arrived after the summary"; it does
  not regenerate the proposal.

*Regressions:* two network failures then a successful End; 422; a failed final batch; a partial 409 after retry; a
slow or rejected final post before Sum up; reload after an incomplete session; a `pagehide` whose owed events overflow
the keepalive budget (the overflow counted missing); a `pagehide` whose End never arrives (`unconfirmed` on the next
device); a close post that never settles, on Sum up and on plain End (finalization still completes).

### 5.3 Ending: end intent, summarizing, playback (B3, B5, B6)

- **End intent is idempotent by event id:** a replay gets its first answer and never advances state. A confirmation
  must come from a later owner turn than the proposal. Keep talking clears the server's proposal
  (`POST …/keep_talking`).
- **States:** `live → closing → summarizing → speaking_summary → ended` (End goes straight to `ended`). In
  `summarizing` capture heartbeats stop and are not expected; the transport stays open. In `live` a server refusal
  still ends the voice at once.
- **A summary has its own attempt** (CX V2-5).
  - The core records one `summary_attempt` per session, first write wins, and moves it from `running` to `done` or
    `failed`. It stays `running` until the server's call ends.
  - The server's call is bounded (5 s to connect, 30 s to read) and never retried by itself, so every attempt
    concludes.
  - **45 s bounds the page's wait only.** Past it, while the attempt still runs, the page says "The summary is still
    being written. It will appear in the conversation." It never calls a running attempt unavailable.
  - **"Summary unavailable"** is said only once the attempt has concluded `failed`: no key, the gateway down, an
    error, or a malformed or tool-call-shaped answer. The records stay intact. Only then may "Ask for a summary"
    start a new attempt.
  - **A second request joins the running attempt.** It never starts another generation. Two devices asking get one
    proposal, and the first decision stands.
  - A lost answer (0, 502, 504) says "The summary may still appear in the conversation." A refusal says "The summary
    could not be asked for."
  - End during `summarizing` closes the voice; the summary still lands in writing.
- **The summary ends when it has been heard:** it is one tracked response; the session ends when the adapter
  reports that response's audio drained, at barge-in, or at a bound (15 characters a second plus 5 s, at most 90 s).
  Another response never closes it. New optional `onPlaybackDrained(responseId)`: xAI, its AudioContext queue empty;
  OpenAI WebRTC, candidate `output_audio_buffer.stopped` (**unverified**; A1 checks). German and Portuguese pass no
  callback and behave as today. The callback names how the response ended: `drained` (heard to the end),
  `barged_in`, `cancelled` or `cleared`, `no_audio`, or the bound's `timeout` (CX on B6). Each of them ends the
  session, but only `drained` is recorded as heard.

*Regressions:* lost-response retry of "Yes, we're done"; proposal and confirmation in one batch; Keep talking then
"yes"; summary before and after a heartbeat; summary failure, timeout and End during it (transport closed once,
honest written result); response done with audio queued; empty response; barge-in; an unrelated pending response; a
summary call still running past 45 s (never "unavailable"; one proposal when it lands); "Ask for a summary" while one
runs (it joins); two devices asking at once (one proposal, one decision).

### 5.4 The summary's execution boundary (CX-S2, D22)

A "use no tools" prompt is not a boundary: neither agent adapter enforces `tool_policy` (`openclaw_backend.py:343`;
MC `guild_ui/mc/openclaw.py:200-210`), and `cos-agent-a` runs `exec` `full`. The summary is a **plain completion through the
model gateway**: this session's kept records (60 records or 12,000 characters, omissions marked) and the person's
name and card, no tool definitions, no agent runtime, no conversation binding, under a capped key per host
(`voice-summary-mc`, `voice-summary-cos`); the model is gateway configuration. Gateway or key unavailable: "Summary
unavailable", never the live agent. Confirming a summary authorizes nothing in it (as built). Tests: no `tools` in
the request; a tool-call-shaped answer counts as unavailable; a missing key refuses; no agent runtime is invoked.

**As built (A1F, `4c9c7327`).**
- One non-streaming completion, at `MINIMOI_MODEL_GATEWAY_URL`, with model alias `minimoi-voice-summary`.
- The keys are read from `MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_MC` and `…_COS`.
- The alias, the keys and the portal's environment are not provisioned. Operations provides them before A4
  (`A1_FIX_RESULT.md`). Until then the summary reads "Summary unavailable".

### 5.5 Tests carried from Chunk B

`test_mc_voice_direct.py`, `test_mc_voice_direct_page.py`, `test_mc_voice_reveal.py` and the four
`tests/mc_voice_direct_*.mjs` (`host`, `reveal`, `session`, `thread`) pass apart from import paths, **except
assertions that encode a B1–B7 defect**, each replaced by the expected behaviour and named in its commit. Codex's
reproductions (`review-evidence-codex/`) become expected-behaviour regressions; Codex re-reviews the pinned A1 head.

### 5.6 What changes for MC

Production keeps the relay until MC is admitted (§11.1). His voice card is adopted as a release and pinned (the
portal reads only `voice.json`; the agent container's release is separate). His rules move into the adapter
unchanged; person-neutral rules go to the core (D17). The adapter works when MC's chat is kind `cos`, with a test.
Voice ids by ear (D3).

### 5.7 Where A1 does not yet meet this contract (A1F `005c9e81`)

The A1 fix round is still being extended. This table is a snapshot of `A1_FIX_RESULT.md` and
`A1_FIX_FINDINGS.md`; the review records are the evidence, and the next pin updates it.

| Point | This contract | A1F | Closes in |
|---|---|---|---|
| Binding a call to its turn (A1-C2) | per response (§5.1) | A 3 s wait covers a call that arrives before its transcript, not one that arrives after a later turn | the fix round (in progress) |
| The barrier's timeout (A1-C3) | final (§5.2) | being checked | the fix round |
| Summary attempt (V2-5) | its own attempt; "still being written" while running (§5.3) | "Summary unavailable" at 45 s, which holds because the server's call is bounded at 35 s; the join and two-device rules are being checked | the fix round |
| Playback outcomes (B6) | named outcomes; only `drained` is heard (§5.3) | barge-in and `cleared` count as drained | the fix round, or A3 |
| Holding lines (§12.4) | by 8 s and 17 s; `pending` at 25 s | one line at 10 s | the fix round, or A3 |
| Store `close`, `records`, `state`, `sessions` (§4.4) | A2 adds what CoS needs | not built; MC didn't need them | A2 |
| CoS projection by revision; tail repair (§6.5) | as §6.5 | not built | A2 |

## 6. Chief of Staff standardization (core goal)

### 6.1 Her card

`domains/cos/voice/releases/<v>/voice.json` + `manifest.json`, and `domains/cos/voice/pin.json` (redeploys only CoS
images). Schema 1: `language: "en-US"`, `accent: "light Austrian"`, `transcription: "bilingual"`, `card`, `voices`
(D2); the manifest names the persona.

### 6.2 Her manner: the shared persona, written for voice

Today's CoS voice is not MC's manner appended by accident: Robert approved one persona for both, compiled into her
runtime (§2.2), so her consult answers and typed and Telegram replies already speak it. The realtime model does not
read her SOUL, so her card **restates the persona's voice-relevant parts**, names the persona version (§4.5), and
adds only her name and voice-surface wording; MC's card does the same. Each part of `VOICE_MANNER`
(`controlled_config.py:8-30`) goes to one named place:

| Goes to | Clauses |
|---|---|
| Her card (from the persona) | warm Austrian colleague, `du`, dry humour; familiar Viennese partner, never a receptionist, concierge, servant, stranger or examiner; English by default with a light intelligible Austrian accent, no caricature or phonetic spelling, no invented memories; German as a brief phrase in his day, one formulation for "how do I say…"; follow his German in Austrian standard German, then return; no canned enthusiasm, flattery or filler |
| Core direct rules, both (D17) | one or two short sentences, answer first; drop an interrupted explanation; don't prompt in silence; tell an unavailable tool from a failed lookup and unchecked evidence; claim saves, recipients and handoffs only from receipts; no claim of complete memory; Guild note ≠ CoS note ≠ Board post-it; no runtime ids; voice rules never expand authority |
| Her rules | consult before saying something is unavailable; her voice paragraph (`chief_of_staff.py:652-678`) with `consult`, `save_note` |

**Draft card** (final after the audition):

> Chief of Staff, Robert's familiar, candid partner, who keeps the work pointed at the right questions. Warm,
> sharp, direct, with dry humour when it fits; never a receptionist, concierge, servant or language examiner.
> English by default, with a light, natural, intelligible Austrian accent; never a caricature, never invented
> shared memories. German belongs naturally in his day: an occasional brief phrase; asked how to say something, one
> natural Austrian formulation, then pause. When he speaks German, follow in clear Austrian standard German with
> du, then return to English; never a lesson. No canned enthusiasm, flattery or filler. Pronounce MiniMoi, Chief of
> Staff, Master Craftsman, Claude Code, Codex and Grok the same way every time.

So MC and CoS share an accent and most of a manner. If Robert wants them to differ, that is D1, a persona adoption.

### 6.3 Her session instructions

Composed on her server in Spec 164 §7's order: the core's direct rules, then her voice paragraph; her card; DATA:
her briefing (`_build_system_prompt()`), then bounded continuity. Bound 24,000 bytes (`controlled_config.py:38-42`):
continuity cut first, then memory, marked as cut; rules and card never; still over, voice is unavailable and says
so (`cos_memory.md`'s production size is unverified). On Grok, these instructions, her whole briefing included, go
to xAI through Robert's own browser: a new provider for her data (D2). Nothing the browser changes grants anything.

### 6.4 Her consult backend

`typed_turn` calls `_process_confer_turn(owner_text, channel="html_voice", conversation_id="owner",
request_id=<uuid5 of the action key>)`: the same service, authority and receipts as typed Confer, on Robert's
completed words, plus `_ui_turn`'s `enforce_file_claims` and outbox snapshot, so voice cannot claim an undelivered
download.

| Turn service result | Outcome; spoken |
|---|---|
| reply, no operation | `done`, receipt `turn_id`; `speakable(reply)` |
| `note_save` `saved` / `deduplicated` | `done`, receipt `operation_id`; "Saved." / "Already saved." |
| `reminder_set` (only via `/remind …`, not offered in voice) | `done`, receipt `reminder_id`, `due_utc`; from the reply |
| `InvalidConferTurn`; `ConferOperationFailed` raised before the operation | `not_done`; its message / "That did not go through. Nothing was done." |
| `ConferOperationFailed` after the operation check (`confer_service.py:288-301`); any exception or timeout after dispatch to her runtime | `unknown` (§5.1), unless a receipt says otherwise |
| `output_limit_reached` | `done`; the reply + "The rest is cut off; ask in writing." |

`consult` and `save_note` replace `consult_cos_agent` and `save_cos_note`; `save_note` needs `parse_explicit_note`
to match, else `not_done`: "Nothing was saved. Say 'save a note that…' and the note." **Reminders** are not by
voice in 1.2 (D20): her rules say "Set reminders in writing", without MC's "remind me at <time>" sentence.

**Refused before dispatch, in the CoS adapter** (CX).
- Consult text that the turn service would treat as a command is refused with "Do that in writing." before
  anything is recorded or sent. That covers `/new`, which resets her runtime (`confer_service.py:234-254`), so
  mapping its result to `not_done` would be false; `/remind`; and any other slash command.
- **The prompt line removes no authority.** Her runtime could still try to schedule a reminder from natural
  language. So the release check says "Remind me at 5 pm to stretch." by voice and requires that no reminder record
  exists afterwards. If one does, CoS voice is not admitted until that is fixed.

**Approvals inventory (D16), on what is loaded:** `ConferTurnService` parses no confirmations; her runtime loads
two plugins and OpenClaw's built-ins, with no approval mode configured (`exec` `full`, elevated off). No platform
approval waits on a "yes"; the risk is conversational (she asks "shall I…?" and acts on "yes"). The core refuses a
consult whose owner text is only a confirmation or stop word, both hosts: "Confirm that in writing." The "yes" to
"Shall I sum up?" stays: it closes capture and authorizes nothing.

### 6.5 Her store (D5, CX-S3)

- **Where:** `COS_VOICE_DIR=/app/data/cos-voice` on `cos-scheduler`, host folder `${MINIMOI_ROOT}/cos_voice` (the
  `cos_reminders` pattern), **not mounted into `cos-agent-a`**; the release check asserts it.
- **Files:** `<session>.jsonl`; the id validated before any path is built; folder 0700, files 0600, `O_NOFOLLOW |
  O_APPEND`, fsync before `put` returns. A per-session lock plus `flock`; the id index rebuilt from the file after a
  restart. First write wins: from two devices, the first decision stands and the second hears "Already decided".
- **A damaged tail is repaired once, exactly** (CX V2-3). A record is committed only when its line ends in a
  newline and is fsynced, and `put` answers only then. When a session file is opened, under the per-session lock
  and `flock`:
  1. Any bytes after the last newline-terminated valid line are copied to `<session>.tail-<UTC>.quarantine` (0600,
     fsynced). This includes a complete JSON object without its newline: it was never acknowledged.
  2. The file is truncated to that boundary and fsynced.
  3. A `repair` record `{kind: "repair", quarantined_bytes, at}` is appended. It keeps the session `incomplete`
     across restarts.

  No later write is acknowledged until the repair is done. An invalid line inside the file, which this rule never
  produces, stops that session's store with "Voice records damaged", shown in needs-attention. Nothing is guessed.
- **No store, no voice:** bootstrap 503 "Voice unavailable: there is no place to keep what is said."
- **Visible while live:** only the session's existence and state. After End or recovery: the records.
- **Needs attention:** `GET …/voice/sessions` lists sessions recovering, dropped, `incomplete`, with an undecided or
  unavailable summary or an `unknown` consult, until decided or dismissed; `…/voice/sessions/<id>` is the detail
  and download. A settled, decided session leaves the list: not a history product.
- **Recall (D6), by revision** (CX V2-2). The store keeps a committed revision per session: its count of committed
  records.
  - **What is written.** Once a session has ended or dropped, it is projected into the recall folder as
    `voice-<YYYY-MM-DD>-<session>.jsonl`, whose first line names the revision it was built from.
  - **How it is rebuilt.** Under the session lock, the projection is rebuilt whenever the committed revision is
    newer than the projected one: written under a temporary name, fsynced, renamed over the old file, then the
    folder fsynced. A writer compares revisions under the lock before renaming, so an older snapshot never replaces
    a newer one.
  - **When.** It runs whenever something is committed: End, recovery, a late consult outcome, the summary, the
    decision. It runs again at the sessions list and the next bootstrap, which repair a crash. No timer.
  - **Pending consults.** A session with a `pending` consult is projected with it shown as pending, and projected
    again when it lands.
  - Lines are DATA, never re-sent as instructions. Abnormal ends and late receipts are marked. `system_note` names
    the files.
- **Retention:** as `conversation_log`, no expiry in 1.2.

*Acceptance:*
- duplicate End and decision; two-device decisions;
- a crash between append, projection and rename;
- a failed disk write; a truncated line; restart; a receipt after End;
- a late outcome, summary and decision after the first projection: each projected once;
- two projections racing: the newer revision stays;
- crash, read, append, second restart: with a valid but unterminated last object, and with duplicate or conflicting
  ids. Each effect once, incomplete state visible,
nothing lost because the summary is unavailable.

### 6.6 Confer UI on the same contract

- **One Voice button** once she is admitted (D8): no mode select, no "Hearing: …"; nothing said shows while live.
- **Live bar:** voice on, elapsed, End, Check with CoS; Sum up and Keep talking when offered.
- **Provider choice** in Confer's quiet style, remembered under its own key; `voice_notices` as one line.
- **Reveal, recovery line, summary card, needs-attention list** in Confer's styles; the card says "Confirming
  confirms the summary only. Anything it mentions still needs its own request."
- **Download transcript (D7)** after reveal, from kept records, partial capture labelled.
- **Dictate unchanged**, bootstrap included. Voice and Dictate yield to each other as today; the companion handler
  ends the direct session; `pagehide` runs the barrier.
- **Not admitted:** Confer behaves as today, plus one capabilities read; tested by behaviour, not page bytes.
  Admitted and refused: "Voice unavailable: <reason>", never another mode.

### 6.7 Portal changes for CoS (IR-S10, CX-S6, IR-M5)

One prefix for every CoS direct route, `api/realtime-voice/direct/`. POSTs under it are CSRF-checked and given
`X-Minimoi-Voice-Write-Verified: 1` **unconditionally**, after the owner and CSRF gates, browser `X-Minimoi-*`
stripped; Confer always renders the CSRF meta tag. GETs: owner identity only, `Cache-Control: no-store, private`.
Timeouts `(5, 40)` for consult, `(5, 60)` for summary. Tested through the portal as owner, non-owner and forged
headers. `X-Record-Mode` is irrelevant (no Private mode).

### 6.8 Her switches

`MINIMOI_COS_VOICE_DIRECT=1` on `cos-scheduler` installs her routes; outside production it also allows starts. In
production a start also needs `MINIMOI_COS_DIRECT_VOICE_PRODUCTION=1`, read in `cos-scheduler` only, through a
held-off filter shaped like `guild_mounts.production_environ`. The portal needs no CoS flag.

### 6.9 What a CoS consult can do (D18)

A voice consult is a typed Confer message: her runtime can run commands (`exec` `full`, no sandbox), write `data/`,
`cos/outbox`, `cos/planning-studio`, `docs`, `agent_logs`, search the web; her turn service saves notes. A misheard
consult can do what a typed message can. Safeguards: Spec 164 §3's, one effect per owner action, the
bare-confirmation refusal, CSRF on every voice POST: stricter than typed Confer, whose CSRF depends on
`MINIMOI_COS_WRITE_GUARD` (production value unverified).

## 7. Providers, voices, transcription, budget

| | MC | CoS |
|---|---|---|
| Providers; models | OpenAI `gpt-realtime-2.1`, Grok `grok-voice-latest` | same (Grok new for her) |
| Default | `config.resolve_provider` (§2.4); the env default may override (production value unverified) | same |
| Voice today; in 1.2 | `cedar` (relay); by ear (D3) | `cedar`; draft `cedar`, Grok by ear (D2) |
| Transcription | bilingual (D19) | bilingual (D19) |
| Budget; allowance; cap | `domain="mc"`; `MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO`; $20 per Chicago day, shared with both Dictates | `domain="cos"`; same (D10) |

**Bilingual transcription (D19).** The consult acts on the transcript, not on what the model heard; a session
fixed to `en` may turn German into English words. Cards with `transcription: "bilingual"` omit the single
language and send the faithful-transcription hint managed CoS used; on xAI, no `language_hint` (behaviour
unverified; the scripts check it). German and Portuguese have no card and are untouched.

**Allowance.** Sessions a day = floor(cap ÷ allowance); the release note records both and the count. That is capacity, not a promise: both Dictates and the other host draw on the same pool (CX). **Grok
alias:** `grok-voice-latest` floats; 1.2 records the model xAI reports and pins a dated id if one is offered.
Robert auditions `cedar`, `marin` and others on OpenAI, `eve`, `ara`, `rex` on Grok (§11.4). No new meter.

## 8. Safety for both hosts

| Safeguard | Source | Where |
|---|---|---|
| Owner, CSRF, record mode on writes; owner-only, no-store reads | 164 §3; CX-S6 | adapter guards; core refuses owner mismatch |
| The server acts on the owner's transcript item; tools allow-listed `consult`, `save_note` | 164 §3 | core consult; bootstrap |
| One effect per owner action; four truthful outcomes; reconciliation | B1, B7 | §5.1 |
| Capture barrier; retention claimed only when acknowledged | B2, B4 | §5.2 |
| Idempotent end intent; summarizing state; summary heard to its end | B3, B5, B6 | §5.3 |
| Summary generation cannot call tools | CX-S2 | §5.4 |
| Saves spoken only with receipts; done actions reported as done | 164 §9; review B #2 | adapter mapping |
| Bare confirmations and stop words refused as consult text | review B #1; D16 | core |
| Chunk B items carried as built: budget after the record, `abandon()` on a failed mint (B #4); caps and times (B #5, #6); no live title (B #7); lazy imports (B #8); marker-read failure (B #9); hide, reveal once, recovery line; server-ended ends the provider (UI #2); tagging fails open for MC outputs only (UI #9) | Chunk B reviews | core or MC adapter |
| CoS voice store outside her runtime's mounts | IR-M3 | §6.5; release check |
| No silent fallback: a page offered direct tries nothing else | 164 §2; C7 | release check |
| No audio or transcript in operational logs | standard rule 6 | ids and outcomes only |

**Known limits, accepted:** MC notes written in a separate request during a consult, and job-result notes, are
untagged and show before End (Chunk B, "not covered"). A note saved by CoS voice appears in Record, Track and memory
at once (D21). Consults run in the live runtime session (MC's conversation; CoS's `owner`), so the runtime knows what
was said before End and a typed or Telegram reply could refer to it. On Grok, instructions pass through his browser.

## 9. Retirement: what is removed, when, what stays

Removal comes after acceptance (D9), so an image rollback always works.

| Component | Switch release | Cleanup A6 | Kept |
|---|---|---|---|
| MC relay (routes, the composer's relay branch, `submitVoiceUtterance`) | reached only when neither direct nor managed is offered: before admission and in S3 | removed | — |
| MC managed host and its branch | held off | removed | `MANAGED_VOICE_CHANNEL` while the Board uses it |
| CoS managed host, `realtime_tools.py`; CoS chained mode (`ConferVoiceController` on Confer, `/ui/speech`, `SpokenReplyStore`, `speech_*`) | not offered once admitted | removed | — |
| Shared sideband (`controlled_*.py`, `server_openai.py`, the controller's `managedBootstrap` branch) | unchanged | removed with tests; the controller branch only in Part B | `voice_control_*` tables and rows |
| `VOICE_MANNER`, `TRANSCRIPTION_HINT` | not reached by MC or CoS | removed; clauses in cards and core rules, the hint text in the core | `PARTNER_STYLE` |
| `create_confer_voice_blueprint` | stays (Dictate) | narrowed to Dictate | until an `en-US` memo bootstrap |
| `realtime-confer-controller.js` | stays | removed once no page imports it | — |
| Bench (`core/realtime_voice/bench/`) | stays | stays | yes |

## 10. Migration

MC: relay and managed notes render as today. CoS: `voice_control_*` rows untouched (dropping them needs Robert's
word); `SpokenReplyStore` is memory only; typed `conversation_log` lines unchanged, voice projections from 1.2 on.
Budget entries gain `domain="cos"`. No backfill.

## 11. Rollout

### 11.1 Switches and states (CX-S4)

Installing a host (its routes, record routes included) is separate from **admission** (starting sessions). Outside
production, installing allows starts. In production, starts also need `MINIMOI_GUILD_DIRECT_VOICE_PRODUCTION`
(portal, `production_environ`) or `MINIMOI_COS_DIRECT_VOICE_PRODUCTION` (`cos-scheduler`).

| State | Reached by | MC | CoS | Record routes (sessions, reveal, End, reconcile, decision) |
|---|---|---|---|---|
| S0 today | `main` | relay | managed if enabled, else turn by turn | none |
| S1 merged, not admitted | 1.2, admissions off | relay, as on `main` | as today | installed, empty |
| S2 admitted | admission on | direct only; a failed start says why | direct only | on |
| S3 admission off after S2 (fast rollback, until A6) | admission off | relay again: a deliberate state, not a fallback | today's modes | **on**: open sessions End, recovering ones reveal, proposals decided, outcomes reconciled; no new starts |
| S4 after A6 | cleanup | off = "Voice unavailable: live voice is not on here."; typing and Dictate work | same | on |
| S5 image rollback | previous tags | relay | today's modes | absent; records stay on disk. MC's direct records show in the old Guild as plain notes (it ignores markers: nothing hidden, proposals without controls; expected, untested). CoS's folder is untouched and unseen; projected sessions stay recallable. Redeploying 1.2 restores reveal and decisions; no old-image reader is built |

The release check exercises S1, S2 and S3 with real portal and backend flag combinations.

### 11.2 Staging first

**Staging is a role:** the production compose plus `docker-compose.staging.yml` (images from the pinned release
commit, `MINIMOI_ROLE=standby`) at an HTTPS name the phone reaches. It runs on the always-on cloud host once that is
up and checked; until then, or whenever useful, on the Mac (today's dev.minimoi.ai). Staging rather than Mac dev,
because the checks must run the production images (`Dockerfile.cos-scheduler`, `cos-agent-a`, the gateway, the
budget ledger), an always-on host lets Robert test whenever he likes, and consult round trips are closer to
production's; audio is browser-to-provider on any host. It needs owner login, staging provider keys, the summary
keys, the voice folders and the allowance.

1. Release checks green on the bundle, both hosts, providers blocked (§12.2).
2. Both hosts installed. Robert on the Mac: MC and CoS scripts, both providers (§11.3).
3. Audition and voice ids (§11.4); cards adopted; pins set.
4. Robert on the phone: both hosts, both providers, the phone cases.
5. Language smoke: Gespräche on OpenAI and Conversas on Grok (both adapters heard), two minutes each, with the
   panel handover (D23); one typed Confer turn, one CoS Dictate, one MC Dictate.
6. Quality gate (§12.4), by Robert's judgement.

### 11.3 Robert's listening scripts

**MC** (Mac, then phone; each provider):

| # | Robert | Passes when |
|---|---|---|
| 1 | "What's open on the board today?" | one consult, a short answer, one "Checked with MC · answered" |
| 2 | A sentence with a one-second pause | one owner turn |
| 3 | Interrupt mid-reply | playback stops; reveal marks MC interrupted |
| 4 | "Save a note that the voice test ran on <provider>." | "Saved." with a receipt; one note |
| 5 | A German sentence, then an English question | he follows and returns; the reveal shows German as German |
| 6 | Check twice; the same words again, Check | "Already checked"; the repeat is a new effect |
| 7 | Check on words the model just consulted | "Already checked"; one effect |
| 8 | "We're done" → "yes" | the summary heard to its end; the card in writing; confirm |
| 9 | Reload during a second session | recovery line, then reveal |
| 10 | Phone: Wi-Fi off during a consult | never "did not go through"; reveal shows the true outcome |

**CoS** (Mac, then phone; each provider):

| # | Robert | Passes when |
|---|---|---|
| 1 | "What's on my agenda this week?" | one consult; a short answer |
| 2–3 | The paused sentence; interrupt | one turn; playback stops |
| 4 | "Save a note that the voice test ran on <provider>." | "Saved." with a receipt; the note in Record |
| 5 | "Remind me at 5 pm to stretch." | "Set reminders in writing."; nothing saved |
| 6 | Check with CoS twice | "Already checked" |
| 7 | A bare "yes" when she asked something | "Confirm that in writing."; no consult |
| 8 | A German sentence | her manner holds; the reveal shows German |
| 9 | Open the floating MC panel | her voice ends first |
| 10 | Dictate after ending | unchanged |
| 11 | Reload mid-session | recovery, reveal |
| 12 | "We're done" → summary; decide on another device | one decision, shown on both |

**Phone cases** (each host, once): screen locked; app switched; AirPods or Bluetooth; speakerphone barge-in (echo
must not interrupt); Wi-Fi to cellular. Pass: the session continues, or ends with what was kept revealed and any gap
marked; never a silent loss. **Robert's marks** (never in logs): sounds like MC / CoS; accent held; a stray German
word did not flip the language; names right; as quick as Maria; anything untrue.

### 11.4 Identity audition

One 60-second script per voice, person and provider: no greeting, a short answer, a German phrase, the names
("MiniMoi, Master Craftsman, Chief of Staff, Claude Code, Codex, Grok"). Robert's question: is this MC? is this CoS?
Manner, brevity and pronunciation must not differ by provider; timbre will.

### 11.5 Production switch-over

One bundle (PR #426 and the A chunks), on Robert's word, from a clean `origin/main` release worktree and PR:
1. Record image tags and the classifier's output in the release note. Merge, admissions off (S1).
2. Health of every service deployed; the no-change assertions; Robert on Gespräche (one provider) and Conversas (the
   other), two minutes each, the panel handover once; a typed Confer turn, CoS Dictate, MC Dictate, one MC relay
   turn. Any regression: roll the bundle back.
3. MC admission on; Robert's short MC script on the phone.
4. CoS admission on; her short script. If CoS fails: her admission off (S3); MC's stays only on Robert's word.
5. Daily use and D9's evidence; then A6.

### 11.6 Rollback

Fast: admission off (S3). Full: previous image tags (S5). Data: nothing to undo; nothing is re-sent.

### 11.7 Deploy only what changed

A1 adds classifier rules, with tests, for `services/mc_operations/personas/` (portal, whose only reader of
`voice.json` is `voice_card`; the MC agent deploys through `deploy-mc-agent.yml`) and `scripts/mc_persona/`, listing
each path's readers in the commit. The bundle deploys what the classifier says (seven services at `93bf00c6`, §2.4),
recorded in the release note. Changes to files German or Portuguese load are additive and opt-in, proved by the
no-change tests.

## 12. Tests, cross-surface checks and the quality gate

### 12.1 Automated (providers and Keychain blocked, `-p noprov`)

- **Core**, through a fake adapter: §5.1–§5.4 with their regressions; the store interface; the card loader (pin,
  hash, locale, `transcription`, persona hash against the lock).
- **No-copy, by architecture:** host modules import the core and implement only §4.4 (import graph and
  definitions), and the shared contract suite runs against each adapter; not a ban on names.
- **MC:** §5.5; kind `cos`.
- **CoS:** MC's refusal set; UUID request ids, retried and repeated actions; the §6.4 mapping (exceptions after
  dispatch are `unknown`); `/new`, `/remind` and other commands refused before dispatch; a spoken reminder leaving no reminder record; bare confirmations refused; file claims; §6.5 acceptance; voice folder not in
  `cos-agent-a`'s mounts; prefix, timeouts and reads through the portal; not admitted = today; 24,000 bytes and order;
  no `VOICE_MANNER` text beyond the placed clauses; no tools in the summary request; one ledger, concurrent
  reservations adding up.
- **German and Portuguese no-change:** the same bootstrap request, answer and instructions, no tools, no notices, no
  new import; the controller without new callbacks as before; with D23, the listener and the new key on both.
- **Node:** the four Guild suites; a Confer suite (live bar, reveal, recovery, summary card, needs-attention,
  Dictate and companion handover, `pagehide`).
- **Known failures** (fail on `main`): `test_html_server_db_conn_requires_explicit_role`, `build_mc_handbook.mjs`,
  `confer_input_controls.mjs`, `confer_write_headers.mjs`; A3 fixes the last two if it can. The whole `tests`
  directory is not run at once (a Telegram poller, CC_17).

### 12.2 Release check (per host, Spec 164 §12)

On the deployed configuration, real backends, stubbed transports: capabilities report providers, persona, locale,
card release; one stub session with one consult reaches the real typed turn and returns `done`; forged and replayed
call ids, a repeated owner action, a call after End and a missing CSRF token each have one effect or none; S1, S2,
S3 behave as §11.1, and a page offered direct references no other path; the record, End, reconcile and decision routes stay installed and owner-authorized in S3 and S4; a provider failure while S2 stays selected is tested apart from a deliberate S3 rollback; no tools in the summary request; the CoS
voice folder is not mounted into `cos-agent-a`; German and Portuguese answers unchanged. Robert's microphone and
phone tests stay separate until he does them.

### 12.3 Cross-surface checks (Spec 164 §13)

Guild and Confer Voice ↔ Dictate; German voice → panel → German (needs D23); Confer voice → panel → Confer; closing
the panel while connecting and during a consult; two sessions in one conversation; a session past the continuity
window; microphone denied; network loss and End during a consult; a receipt after interrupted speech; MC conversation
switching; a CoS session on two devices; recovery after an abnormal end, both hosts, every chunk. Without D23 the
language handover is a recorded limit, never a pass.

### 12.4 Conversation quality and latency (Spec 164 §13a; IR-S13, CX-S8)

A session that asks permission to check, narrates receipts, reads lists aloud or answers in paragraphs fails,
whatever its latency. Robert scores intent, clarification, continuity, useful action, truthful concise replies and
responsiveness; the booleans (turn completeness, interruption, identity, retention, truthful outcomes) all pass.

**Latency, like for like:** speech end to first assistant transcript, German's Phase 0 metric; at least 10 ordinary
replies per provider per host; same device class and network, noted; median and worst. Proposed pass (Robert may set
it): median within 100 ms of German's Phase 0 median on that provider, and at most one reply in ten over 2 s. Grok's
reference is five samples; Part B re-measures it. **Consults** (CX): a holding line by 8 s ("Still checking."), a second
line by 17 s ("Still working on it."), and `pending` at 25 s, so there is never more than 10 s of silence. The lines
say only what is true, never a result. First audible is recorded for Part B; 1.2 claims no audible parity.

## 13. Part B — Language domains (queued, #433)

Part A is §3–§12; Part B is §14. No Part B work starts before 1.2 is in production and Robert says so.

## 14. German and Portuguese, together

### 14.1 What they get in 1.2, and only this (D23; CX-S7, IR-S16)

If D23 (a) is adopted, in A3, one mirrored commit: a `minimoi:companion-opening` listener that, if their voice is
live, ends it as their End button does (`controller.endSession(…)`, so their own `finalizeAndSave` runs). That includes
a session still connecting. It hands `waitUntil` a promise that resolves only once the microphone track has stopped
and the transport has closed, so the panel opens only after their microphone is released. The end call returning, or
the transcript being saved, does not prove the microphone is released (CX); and the controller cache key
bumped on both pages, so no browser mixes the old controller with new adapters. Nothing else changes on those pages.
The controller additions ship inert for them.

### 14.2 Part B: what reaches them, and what never does

Gespräche and Conversas move together, in the same chunks, commits and release. **Reaches them:** measurement via
the playback event; persona voice ids from a card-shaped source only if the session is byte-identical; controller
fixes (`pagehide` ends the session, a server end ends the provider, the End-while-connecting race, CC_16); removal of
`managedBootstrap`; later, pausing the microphone (Spec 164 §17), built once for all four. **Never:** tools,
consults, hiding or reveal (they keep `finalizeAndSave`), summaries, MC's or CoS's cards, manner or rules, the direct
routes, budget admission (D13).

### 14.3 Phase 0 gates, both languages, both providers

Before any Part B change, on the revision then deployed, reference commits recorded: German re-run and Portuguese's
first baseline, OpenAI and Grok, first-audible now measurable. Per provider and language: at least 10 ordinary
replies; committed turns per scripted thought; interruption; speech end to first transcript and to first audible;
transcript delivery; persona voice; language held. Promotion: §12.4's statistic and tolerance on both providers in
both languages, booleans passed, persona voices unchanged, handover working; any regression blocks both. Persona
voices, scenes and prompts do not change. Rollback: both previous image tags, together.

## 15. Changes to other documents (companion)

1. Spec 164: a header note that Spec 165 replaces its phase order.
2. `UI_CONTRACT_chunk_B.md` → the 1.2 contract: person-neutral, corrected by §5, with the CoS host and §11.1 states.
3. `B_direct_path.md`: unchanged (history).
4. Shared voice standard, "Current mapping": MC and CoS Confer are Conversation mode (turn owner the provider,
   allow-listed `consult`/`save_note` into the person's typed turn); both Dictates Memo mode. Robert approves it with
   this spec.
5. `conversation_log.system_note`: names the voice projection files.

## 16. Decisions for Robert

D1–D17 are Robert's as in v0.1, some reframed; D18–D23 are new. Each shows Claude Code's (CC), the independent
reviewer's (IR) and Codex's (CX) recommendation. In v0.3, Codex's recommendations are those of its v0.2 delta review. None is settled until Robert says so.

**D1. CoS manner — a persona decision.** (a) Keep the shared persona; her card restates it for voice; (b) a
CoS-only manner: split the persona, re-lock, rebuild `cos-agent-a`; (c) a new shared manner, same route. *CC: (a);
(b)/(c) only as a persona adoption after the audition. IR: (a), reframed so. CX: (a); Robert adopts the final card.*

**D2. CoS voice ids.** (a) OpenAI `cedar`, Grok by ear; (b) unset; (c) both by ear. Grok receives her briefing.
*CC: (a). IR: name the Grok data flow (done). CX: (a); no silent fallback to unset defaults.*

**D3. MC voice ids.** (a) By ear, `cedar` as reference; (b) preset `cedar`; (c) `marin`. *CC: (a). IR: none. CX: (a).*

**D4. Standalone gate.** (a) Fold Spec 164 §5's standalone acceptance into the staging test; keep the bench; (b)
keep it first. *CC: (a). IR: none. CX: (a) only if Robert explicitly replaces it; until then it stands.*

**D5. CoS voice records.** (a) `<COS_CONVERSATIONS_DIR>/voice/`; (b) `COS_VOICE_DIR` on `${MINIMOI_ROOT}/cos_voice`,
outside her runtime's mounts; (c) Postgres. *CC: (b): (a) is in the folder her runtime greps. IR: (b). CX: (b), changed in
its delta review; verify the mounts and backups before activation. All three agree.*

**D6. CoS recall.** (a) Project settled sessions as DATA (§6.5); (b) voice store only. *CC: (a). IR: (a), settled
sessions only. CX: (a), revision-aware and recoverable (§6.5).*

**D7. Download transcript.** (a) Keep, from kept records; (b) drop. *CC: (a). IR: none. CX: (a), owner-only,
partial labelled.*

**D8. CoS chained mode.** (a) Not offered once admitted; back only in S3; removed in A6; (b) keep as a manual mode.
*CC: (a). IR: none. CX: (a), states settled (§11.1).*

**D9. Deleting old code.** (a) A6 after a week of daily use, with evidence (S2 and S3 exercised, no open voice
incident, Robert's word); (b) in the switch release; (c) never. *CC: (a). IR: order sound. CX: (a); evidence, not
elapsed time alone.*

**D10. Allowance.** (a) One value, one shared pool for MC, CoS and both Dictates; (b) per host; (c) a per-host share.
*CC: (a), sessions a day in the release note; revisit if one host exhausts the day. IR: show sessions a day; decide
whether one host may use the other's day. CX: (a); prove one ledger and concurrent reservations; sessions a day is capacity, not a promise.*

**D11. Production admission.** (a) Per-host start permission, separate from install; off returns MC to the relay and
CoS to today's modes until A6; (b) remove from held-off. *CC: (a). IR: right shape; build the CoS half (§6.8). CX:
(a), with the state matrix.*

**D12. Confer UI writer.** (a) Claude Code, Codex reviews; (b) Codex. *CC: (a), one writer for the shared module
and both pages. IR: none. CX: (a), no concurrent edits.*

**D13. Language budgets.** (a) None in Part B; (b) add. *CC: (a). IR: none. CX: (a); no new hard cap implied.*

**D14. MC and CoS together.** (a) One bundle, admissions in order on one day; (b) MC first. *CC: (a); if CoS fails
on staging, hold the bundle; (b) only on Robert's word. IR: none. CX: (a); hold if CoS fails.*

**D15. Reviewers.** The plan now: Codex delta, Grok, Claude Chat on v0.2. *CC: the plan answers D15. IR: none. CX:
Codex delta (done), then Grok and Claude Chat; code review and listening stay separate gates.*

**D16. Bare confirmations.** Inventory done (§6.4). (a) Refuse bare confirmation and stop words as consult text in
the core, both hosts; (b) allow for CoS. *CC: (a); "yes" to "Shall I sum up?" stays. IR: inventory what is loaded
(done). CX: (a); tell harmless confirmation from granting authority; End and Stop still stop audio, and the refusal never blocks stopping.*

**D17. Person-neutral rules.** (a) In the core for both; (b) MC as built, CoS in her rules. *CC: (a). IR: none. CX:
(a); test the prompt change on both hosts and providers.*

**D18. Spec 164 §3 trust for CoS (new).** (a) Extend it with §6.9's safeguards; (b) CoS voice without `consult`;
(c) defer CoS voice. *CC: (a): typed Confer already has this authority; voice adds guards. IR: make it a named
decision. CX: (a), if Robert adopts it explicitly; bind the utterance, enforce excluded commands, keep receipts and unknowns.*

**D19. Bilingual transcription (new).** (a) MC and CoS transcribe bilingually (§7); (b) English only, an accepted
limit. *CC: (a). IR: requirement or accepted limit; check the transcript. CX: (a); test the kept German and English transcript on both providers, and if xAI cannot meet it, report that for a decision.*

**D20. CoS reminders by voice (new).** (a) Out of 1.2: "Set reminders in writing."; (b) a spoken-reminder parser as
named scope with its own review. *CC: (a): a parser turns speech into a scheduled action, an authority question.
IR: (a), or (b) as new scope. CX: (a), enforced before dispatch and tested, not left to the prompt (§6.4).*

**D21. Notes saved by CoS voice while live (new).** (a) Visible in Record, Track and memory at once; (b) hidden until
End by a session tag. *CC: (a): he asked for it and heard the receipt; it is not the conversation. IR: either, as a
decision. CX: (a), for a deliberate note with a receipt; ordinary transcripts stay hidden.*

**D22. Summary execution (new).** (a) Tool-less gateway completion, capped key per host (§5.4); (b) the agent runtime
with its own session and a no-tools prompt (as built for MC); (c) the runtime with an enforced no-tool profile
(OpenClaw support unverified). *CC: (a). IR: noted the separate session is possible (helps (b)'s session, not its
tool guarantee). CX: (a); reject (b); (c) only with a proven enforced profile, never as a fallback.*

**D23. German and Portuguese in 1.2 (new).** (a) The handover listener and cache-key bump only (§14.1), mirrored;
(b) the panel offers no voice or Dictate on those pages until Part B; (c) nothing, a recorded limit. *CC: (a): small
and permanent; (b) is a workaround to undo later. IR: bump the key or test compatibility. CX: (a), covering a session
still connecting and the actual release; else (b); (c) cannot pass the handover gate.*

## 17. Not in this build

A fourth voice implementation, copied controller or domain session loop; a server-held xAI session or media relay;
any German or Portuguese change beyond §14.1; persona voice changes; Curator or Telegram voice; voice cloning; a
French CoS; an `en-US` memo bootstrap for CoS Dictate; spoken reminders for CoS; Command mode; dropping
`voice_control_*`; deploys or flags without Robert's word; choosing voice ids for Robert.

## 18. Future (consideration only)

Dessa (Block), full duplex: a research preview with no public API (27 Oct); it would join through the same
registry, but changes the turn model and needs tool calls. Kept open at no cost: timestamps on every record,
host-invoked consults (a wake phrase later), VAD settings as provider parameters, a local model as a registry entry.
Later: pausing the microphone; a morning-brief voice line (Spec 164 §17); CoS on the floating panel.

## 19. Build plan

Each chunk: **build → independent review → fixes → verify**, Codex on function, then one handoff file in
`planning-studio/initiatives/INIT-2026-0010-grok-dev-onboarding/minimoi-1-2-voice/`, read when Robert starts the next
session. One writer per workspace. No polling or pings, and nothing relies on the retired nudge: a chunk that stops
without its handoff shows as "stalled" to Robert and MC (PR #434), and work resumes from the last handoff. No push,
merge, flag or deploy without Robert's word.

| Chunk | Builds | Done when |
|---|---|---|
| **A1 Core + corrected contract** (in progress) | relay kept (`99b3d0f0`); `direct_host.py`, `errors.py`, `direct-voice-session.js`; §5.1–§5.4; controller additions; adapter cache keys; pins, persona field; classifier rules; MC on the core | §5.5; B1–B7 regressions; no-copy; no-change; Codex re-review of the pinned head |
| **A2 CoS backend** | `domains/cos/direct_voice.py`; card and pin; instructions and bound; store and projection; summary keys; portal prefix, guards, timeouts; switches | CoS refusal set; §6.5 acceptance; release check; not admitted = today |
| **A3 Confer UI** (+ §14.1 if D23 (a)) | live bar, provider, reveal, recovery, needs-attention, summary card, `pagehide`, companion; Dictate untouched | Confer suite; handover tests on both language pages; phone-width visual check |
| **A4 Staging and listening** | install on staging; release checks; Robert's scripts, phone cases, audition; cards adopted | §11.2 steps 1–6 by Robert |
| **A5 Production** | release worktree, PR, bundle; tags and classifier output recorded | §11.5 steps 1–4 |
| **A6 Cleanup** | §9 removals after D9 | no page references a retired path |
| **B0–B2 Languages** | §14.3 baselines; §14.2 integration, mirrored; Robert's runs | every gate, both languages, both providers |

## 20. What this spec could not verify

- Production `.env` values: `MINIMOI_COS_MANAGED_REALTIME`, `MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO`,
  `VOICE_REALTIME_PROVIDER_DEFAULT`, `MINIMOI_MC_EVERYWHERE`, `MINIMOI_GUILD_CHAT`, `MINIMOI_COS_WRITE_GUARD`.
- `cos_memory.md`'s production size (the 24,000-byte bound).
- OpenAI WebRTC's playback-complete signal; xAI transcription without a language hint; a dated xAI model id.
- Which provider events carry a response id, xAI's audio deltas included, and the real order of input-transcript and
  function-call events. §5.1's binding rests on them.
- Whether OpenClaw's built-ins ask for confirmation in ways its configuration does not show.
- The cloud staging host: its name, keys, login, folders, allowance.
- The summary keys (not yet created); the gateway's reachability from portal and `cos-scheduler` (both share its
  default compose network; not exercised); backup coverage of `${MINIMOI_ROOT}/cos_voice`.
- In a browser: the panel over a live German or Portuguese session; MC's direct records under an old image (S5).
- A1's code beyond `005c9e81`. This spec states the contract; §5.7 lists what A1 does not yet meet.
- Every listening, phone and latency claim for the direct path; Grok Voice's first-audible timing.
