# MC voice in Conversation mode on the shared voice utility: specification v0.2

**27 October 2026 · Claude Code for Robert (v0.1 by Grok) · INIT-2026-0010 · GitHub issue #420 · PR #421**

**Status: near-final draft v0.2 for Claude chat's review, then Robert's; authorizes no build.** Robert settled the
three open decisions on 27 October (§15). Robert's yes starts
code, and the first code is the German baseline measurement and the standalone bench. This file authorizes no
container, credential, deploy, production flag, merge or provider call.

v0.2 replaces v0.1. It folds in, in this order of authority:
1. **Robert's binding decision** (`coordination/ROBERT_DECISION_spec164_reuse_and_rollout.md`): German's working voice
   code is the base; extend it; no fourth voice implementation; standalone first, then MC and CoS, then back to
   German with no degradation. Robert's words: "German is the code base, extend and expand ... I don't want a 4th
   version of voice."
2. **Codex's review** (`REVIEW_codex_spec164.md`, C1–C7).
3. **Claude Code's reviews** (`REVIEW_claude-code_spec164.md`, `REVIEW_claude-code_spec164_r2.md`).
4. Robert's earlier decisions: Grok Voice on the tool-using path (CC_04); the spec amendment (CC_01a); four reviews
   before code (CC_01a).

The biggest change from v0.1: **MC voice uses German's own direct voice path for both providers**, extended with
tools through the browser function bridge that the shared controller already has. v0.1's server-held xAI provider,
and most of the transport and billing work Codex found it would need (C1, C2), are no longer required. §3 explains
why, and the one trust decision it rests on.

The amendment to `spec_shared_voice_interaction_standard_2026-08-15.md` is the companion commit in PR #421 and is
not restated here. `file:line` references are to `main` at `85fa56f9965110d040ad76caf706256b6a224967`.

## In plain words

Master Craftsman should be someone Robert can just talk with, on the Mac or the phone, every day. Robert's German
conversations already feel right. So MC's voice becomes **the German voice, extended**: the same code, the same
turn-taking and interruption, the same OpenAI or Grok Voice choice. What MC adds is the ability to check with the
real Master Craftsman agent mid-conversation ("let me check that"), his own voice and manner, and a short summary
at the end.

What Robert gets:
- **Voice** is a live conversation. A one-second pause sends nothing. He can interrupt. No running text on screen.
  When voice ends, the conversation shows what was said. When he says he is done, MC gives a short summary of this
  session, which Robert confirms or corrects in writing.
- **Dictate** stays as it is: he starts and stops it, silence never sends, the text lands in the box to edit.
- **OpenAI or Grok Voice**, including when MC is checking with the agent.
- **MC sounds like MC everywhere** he appears, including the floating panel on German, Portuguese and the other
  domains. CoS keeps her own voice. German's practice personas keep theirs.
- **A standalone test page first**, on the Mac, before MC changes, so he hears it early.
- **German improves last**, only once proven, and only if nothing gets worse.

**Honest limits.**
- The standalone page writes nothing; its turns vanish on reload.
- The phone needs HTTPS, so phone tests come at the dev stage. Robert does the phone test before any production
  proposal; no agent claims it.
- Voices are picked by ear. No prompt guarantees a stable Austrian accent; the audition is the test.
- Provider bills are real even on the test page; the page's allowance is MiniMoi's own limit, not a provider cap.
- This spec does not change CoS's voice or default provider, and adds no Curator voice.

## 1. Context for a reader without the repository

| Fact | Reference |
|---|---|
| German Gespräche runs voice through the shared browser controller `RealtimeVoiceController`, direct mode: the server mints a short-lived provider credential, the browser talks to OpenAI (WebRTC) or xAI (WebSocket) | `domains/german/templates/german_gesprache.html:1920`, `:1996`; `core/realtime_voice/bootstrap.py:173-240` |
| The server, not the browser, builds the session instructions from a named persona; browser-sent instructions are discarded | `core/realtime_voice/bootstrap.py:185-220` |
| Voice per persona and provider, else defaults `marin` / `eve` | `core/realtime_voice/bootstrap.py:45`, `:224` |
| Both provider session builders already accept `tools` and `tool_choice`; the bootstrap does not pass any yet | `core/realtime_voice/providers/openai_realtime.py:160-190`; `core/realtime_voice/providers/xai_voice.py:40-89` |
| Both browser adapters already emit `function_call` and send `function_call_output` | `static/adapters/openai-webrtc-adapter.js:166`, `:217-218`; `static/adapters/xai-websocket-adapter.js:185`, `:218-219` |
| The shared controller already has a browser function bridge: in direct mode it calls the host's `onFunctionCall` and returns the result to the provider | `core/realtime_voice/static/realtime-voice-controller.js:44`, `:65`, `:216-235` |
| COS used that bridge in its August beta, then moved to a server "sideband" that only OpenAI supports | `73a0280d` (16 Aug); `core/realtime_voice/controlled_session.py:1-8`; `tests/cos/test_confer_voice_wiring.py:14` |
| The managed (sideband) path is OpenAI-only end to end | `controlled_api.py:23`; `realtime-voice-controller.js` managed branch; `controlled_session.py:61-100`, `:287-319`, `:380-419` |
| The managed configuration appends a Viennese partner manner to every managed session, CoS included, and fixes `voice: 'cedar'` | `core/realtime_voice/controlled_config.py:9-29`, `:51` |
| Allowed direct-path locales are `de-AT`, `pt-BR`; capabilities already list `en-US` | `core/realtime_voice/config.py:19`; `core/realtime_voice/capabilities.py:47`, `:59` |
| MC's managed host installs only for `kind == 'cos'`; prod MC is `openclaw`, so the composer falls back silently to the chained `ConferVoiceController` (this morning's failure) | `minimoi_portal/guild_ui/mc_managed_realtime.py:28`; `static/js/mc_voice.js:136-141` |
| Voice gates on `kind == 'cos'`: install (`mc_managed_realtime.py:28`) and the voice channel binding (`api.py:2223`). `api.py:2155`, `:2208` are Board outcomes; `mc/backend.py:208`, `:306` are not gates | as listed |
| The OpenClaw adapter JSON-serializes turn context into the message | `minimoi_portal/guild_ui/mc/openclaw.py:196-202` |
| Voice turns are already retained as platform notes during a session | `minimoi_portal/guild_ui/mc_voice_transcripts.py:16-36` |
| The floating MC panel is one Guild conversation in an iframe on each domain; the companion script already coordinates the microphone with the page underneath | `minimoi_portal/companion.py`; `minimoi_portal/static/companion.js:40-57` |

## 2. One implementation: what is reused, extended, untouched, retired

**Reused as is:** `RealtimeVoiceController` (direct mode), both browser adapters, provider VAD settings
(`standards.py`), the duration guard, the session allowance, the Memo controller (Dictate).

**Extended (small, shared, opt-in):**
- `bootstrap.py`: a persona may carry an allow-listed tool list, passed to the provider builders that already accept
  it; a persona may run without a scene; the `en-US` locale is allowed for the direct path (`config.py`). German
  personas carry no tools and are byte-for-byte unchanged.
- The persona source for MC: a short **voice card** plus provider voice ids (§7), loaded by the existing
  `get_persona` callable.
- `realtime-voice-controller.js`: measurement hooks (§5) and a per-turn "first audible playback" event; no change
  to turn-taking.

**New, but thin hosts only:**
- The standalone test page (§5): a page plus one consult endpoint; no session loop.
- MC's consult endpoint (§3, §9): the existing typed turn service behind a bridge route.

**Untouched by this spec:** the managed sideband path (`controlled_*`, `server_openai.py`) as COS uses it today, and
German's pages. CoS's move, if any, is decided in phase 3 (§10).

**Retired for MC voice:** the chained `ConferVoiceController` fallback in `mc_voice.js`. If MC voice cannot start,
the button shows "Voice unavailable" with the reason; it never silently becomes the old relay (Codex C7).

So there are two voice paths in the end, German's direct path and the managed sideband (which itself runs on
German's controller), and no fourth.

## 3. Tools on the direct path: the browser function bridge, and the trust decision

**How a consult works.** The provider decides to call `consult` (or `save_note`); the browser adapter emits
`function_call`; the controller hands it to MC's `onFunctionCall`, which POSTs `{call_id, name, arguments,
owner_transcript_item}` to the host's consult endpoint with the owner's session and CSRF token. The server runs it
through the **same typed turn service, authority, deduplication and receipts as a typed note**, and returns the
result; the controller returns it to the provider, which speaks it. Both providers, one code path.

**The trust decision (for Claude chat and Robert).** The managed design's rule is "only the provider's server-side
connection may feed tool calls, never the browser". This spec relaxes it for MC, on this argument: the bridge is
used only by Robert's own authenticated browser, which can already send any typed note through the same turn
service; a voice consult gets **no authority a typed note does not have**. What makes it acceptable:
- No voice-only authority anywhere: a bridged consult is processed exactly as typed owner text, and labeled as
  voice in its record.
- The server checks session, CSRF, conversation ownership and on-record state on every call, and binds each call to
  the live voice session id issued at bootstrap; calls after the session ends are refused.
- `call_id` and operation ids deduplicate: a repeated or replayed call has one effect.
- The text acted on is the owner's own transcript item, not the model's paraphrase in the arguments; the model's
  arguments are advisory.
- Tools are allow-listed per persona (MC: `consult`, `save_note`); German personas have none.

**Fallback if this is rejected:** MC voice with tools runs on the managed sideband for OpenAI only (it exists),
and Grok Voice with tools waits for a provider-supported server attach. German keeps xAI on the direct path. The
standalone bench (§5) then demonstrates both paths for Robert to compare.

This replaces v0.1's server-held xAI provider. Codex's C1 (browser media to a server xAI socket) and C2
(normalized server-side events and billing) do not arise on this path: audio stays browser-to-provider exactly as
in German, and usage is admitted by the existing bootstrap allowance as it is for German.

## 4. Phase 0: measure German first

Before any change, record German Gespräche as it is deployed (the actual production commit, not the PR baseline),
on both providers, with one fixed script (§5): speech end to first audible playback (ordinary replies), interruption
stopping playback, committed user turns per scripted thought, transcript delivery at end, persona voice. These
numbers are the baseline for every later acceptance and for the German backport.

## 5. Phase 1: the standalone test page

A local page on the Mac, a thin host of the shared code: `RealtimeVoiceController` in direct mode, bootstrapped by
the extended `bootstrap.py` with a test persona, and a stub `consult` endpoint. Not a Guild route, not in
production compose, no production flag. Provider keys come from the Keychain at start, never written anywhere. The
existing allowance and duration guard apply; the page states that provider usage is billed.

**Robert's script, run on OpenAI and on Grok Voice:** a work question in English; one sentence with a deliberate
one-second pause inside it; a German sentence; a mixed sentence with one German word; an interruption mid-reply;
a question that triggers the stub consult; "we're done".

**What the page shows after the session** (timing only, never transcript content in logs):
- **Latency:** speech end to first audible playback, per reply, split into provider VAD delay, provider generation,
  and consult time; ordinary and consult replies reported separately (Codex C6).
- **Committed user turns per scripted thought:** counted from completed user transcript items (the controller's
  `onUserTurn`), not from speech-stop events, and never from `browser_vad`. The paused sentence passes at exactly one.
- **Interruption:** whether playback stopped within the reply.
- Robert's marks: accent held; a stray German word did not flip the language; MC's name and agent names pronounced
  right.

**Accepted when:** both providers complete the script; paused sentence = one turn; interruption stops playback;
ordinary-reply latency no worse than the German baseline on the same provider (Robert: "no worse and expected to get
better over time", so each later phase re-measures and the baseline only moves up); Robert's yes after listening. The
page's code is the code MC and CoS use next; it is not rewritten in integration.

## 6. Durable capture, and revealing turns after voice ends (Codex C3)

One store, the one that already retains voice turns (`mc_voice_transcripts.retain`):
- Every admitted user turn, assistant turn, consult request and consult receipt is retained **as it happens**,
  tagged with the voice session id, in order, with stable ids, partial/interrupted status kept.
- Retained voice records are **hidden from the open conversation while the session is live**, in every view
  (refresh, another tab), not only the current page.
- **Reveal** happens once, on the session's end receipt. If a session dies without one (tab closed, network loss,
  server restart), the records are revealed on the next conversation load after the session's maximum duration
  has passed; never twice.
- What is shown: the dialogue (owner turns and the assistant's spoken turns) plus a compact line per consult with
  its receipt. A consult's backend text is evidence, shown only if it differs from what was spoken, and labeled.
  Interrupted assistant speech is marked as interrupted.
- Revealed transcript turns are never re-sent as new owner instructions. Private/off-record conversations retain
  nothing (voice is on-record only, as today).
- A consult that completes after the session ended keeps its receipt and is revealed with the rest; it does not
  re-run.

## 7. Who MC is in voice: card composition and loading (Codex C4)

Session instructions are composed on the server from three parts, in this order:
1. **Shared operational rules** (from today's MC `INSTRUCTIONS` that matter in every MC session: consult for
   current facts and work, receipts before claiming success, historical text is data, tool arguments grant
   nothing). Not shortened away.
2. **The person's voice card**: MC's, below. CoS has her own; German personas keep theirs.
3. **Bounded continuity**: the current conversation's recent context (as `mc_voice_transcripts.block` does).

**MC's card:** Master Craftsman, Robert's colleague in the work: warm, direct, brief; no flattery, no lists read
aloud, no markdown spoken. English by default, with a light, stable, intelligible Austrian accent, not a
caricature. When Robert speaks a real German sentence, answer briefly in Austrian German and correct gently if he
is practising; an isolated German word inside English stays in English; don't switch because of his accent; ask in
one short question if unclear. Pronounce MiniMoi, Master Craftsman and the agents' names consistently.

**Where it lives:** `voice.json` in the MC persona release (`services/mc_operations/personas/mc-chat/releases/<v>/`):
`{schema: 1, card: "...", voices: {openai: "<id>", xai: "<id>"}}`. It is listed in the release manifest, so its
hash is verified at start like the other persona files; `make_header_release.py` and the projection are extended
to include it. Voice ids are validated against the provider's allowed list at load; an invalid or missing id falls
back to the provider default and the page says so. A session pins its card and voice at start; a change applies to
the next session.

**CoS** gets her own card when she integrates (phase 3), not MC's. Robert, 27 Oct: "own card, I might want a French
CoS or Portuguese." So a card names its person's language and accent, and a person's card may be swapped for another
language without code changes; MC's Viennese manner, currently appended to every managed session
(`controlled_config.py:9-29`), stops applying to CoS when she moves to her card. Until phase 3 her effective voice
is unchanged.

## 8. Ending a session and the summary (Codex C5)

- "We're done" (or the End button) **proposes** ending: MC answers "Shall I sum up?" in one line; a yes or the End
  button ends capture. A negated or accidental "we're done" ("we're not done") does not end.
- The summary is generated **from this session's retained records only** (§6), bounded, with omissions marked
  ("summary covers the last N turns"); never from earlier sessions in the same conversation.
- It appears as a **summary proposal** tied to the voice session id. Robert confirms, edits or declines it in
  writing. His confirmation confirms the summary only: it authorizes nothing the summary mentions; any action
  still needs its own request and receipt.
- If the summary fails, the retained turns are still revealed, with "summary unavailable"; Robert can ask for it
  in writing later.

## 9. Phase 2: MC

- The MC composer and the floating panel use `RealtimeVoiceController` direct mode with MC's persona (§7), the
  bridge (§3) and the reveal rule (§6). Same code as the test page.
- The consult endpoint uses MC's typed turn service with the configured MC kind. For the OpenClaw kind, the adapter
  receives a voice marker it translates into its context, never the `MANAGED_VOICE_CHANNEL` sentinel serialized into
  JSON (`openclaw.py:196-202`), and never a browser-supplied substitute (Codex C7).
- A note is spoken as saved only after its platform receipt.
- If voice cannot start (flag off, missing persona, provider refused), the button shows "Voice unavailable: <reason>".
  No silent fallback to the chained relay.
- Dictate is untouched, except where a shared change is needed so Voice and Dictate don't fight over the microphone;
  any such change is named in the commit. The floating panel reuses the companion's existing microphone handover.
- The stale "Voice ended…" status line clears after a few seconds or on the next send (#420 comment).

## 10. Phase 3: CoS

CoS gets the same capability as an option, with her own persona card, her own voice ids and her current default
provider unchanged. Whether CoS Confer moves from its current voice path to this one is decided at this phase, on
the phase-1 and phase-2 evidence, by Robert. Not before.

## 11. Phase 4: back to German, with no degradation

The proven shared changes (measurement hooks, `en-US`, optional tools, card composition, playback event) reach German
through its existing integration, with German's personas, scenes and settings. German gains no tools, no summary
and no MC personality. Promotion requires German's phase-0 numbers to be met or improved on both providers, persona
voices unchanged, transcript delivery unchanged, and the floating-MC microphone handover working; any regression
blocks it, and the previous German build is the rollback.

## 12. Release check (Codex C7)

For each integration, the release check asserts on the deployed configuration:
- the voice surface reports the intended path and provider list (metadata), not merely that a route exists;
- one stub-provider session with one bridged consult reaches the configured MC kind and returns an answered receipt,
  through the real OpenClaw adapter with a stubbed native transport;
- a forged or replayed `call_id`, a call after session end, and a missing CSRF token are refused, each with one or no
  effect;
- with the flag off, the surface shows "Voice unavailable", and the chained relay is not reachable from the MC
  composer.
Robert's microphone and phone tests remain separate and outstanding until he does them.

## 13. Cross-surface checks (all phases, as relevant)

Guild Voice ↔ Dictate; German voice → floating MC → German again; closing the floating panel during connect and
during a consult; two sessions in one conversation (summary covers only the second); a long session beyond the
continuity window; microphone denied; network loss and End during a consult; a receipt arriving after speech was
interrupted; saved-conversation switching.

## 14. Not in this build

A fourth voice implementation, a copied controller, or a domain session loop. A server-held xAI session or media
relay. Changes to CoS's voice or default. Curator voice. Voice cloning. Production deploys or flags. Choosing voice
ids for Robert.

## 15. Decisions (settled by Robert, 27 October 2026)

1. **Trust model (§3): yes.** A voice consult from Robert's own authenticated browser carries the same trust as a
   typed note, with the safeguards in §3. The fallback in §3 is not needed.
2. **CoS: her own card** (§7, phase 3). Cards carry language and accent, so a French or Portuguese CoS is a card
   change, not a build.
3. **Latency: no worse than German on the same provider, expected to improve over time.** Each phase re-measures
   against the latest accepted numbers; a regression blocks promotion.

## 16. Future providers (consideration, not in this build)

Robert flagged Block's **Dessa** (27 Oct), a full-duplex speech model trained only on speech: it listens and speaks
at the same time, with natural pauses and cadence, rather than taking turns after a silence. As of 27 Oct it is a
research preview with a waitlist: no public API, pricing, benchmarks or language list; Block says it can run
locally on a high-end Mac, unverified. What it would mean here, when it becomes usable:
- It joins as a provider through the same registry and adapter shape as OpenAI and xAI; MC's card and the
  conversation records stay MiniMoi's (optionality principle).
- Full duplex changes the turn model: the shared standard's Conversation mode assumes provider VAD with a silence
  boundary (300 ms prefix, 1200 ms silence). A full-duplex model would need a standard amendment for overlapping
  speech, and the tool bridge (§3) would need a model that can call tools, which a speech-only model may not.
- A local model would change cost and privacy, and the phone path.
Revisit when it has a public API or downloadable weights.
