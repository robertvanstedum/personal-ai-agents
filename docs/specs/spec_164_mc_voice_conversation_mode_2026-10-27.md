# MC voice in Conversation mode on the shared voice utility: specification v0.1

**27 October 2026 · Grok for Robert · INIT-2026-0010 · GitHub issue #420**

**Status: draft v0.1 for review; authorizes no build.** Reviews, in order: Claude Code, Codex, Claude chat, Robert. Robert's yes starts code, and the first code is the bench. This file authorizes no container, credential, deploy, model call, merge, or production flag.

v0.1 follows Claude Code's review of the trial design note (`REVIEW_claude-code_spec.md` in the trial folder) and his decision that Grok Voice on the tool-using path is part of this build (`coordination/CC_04_decision_xai_controlled_provider.md`). The amendment to the shared voice standard is a separate edit of `spec_shared_voice_interaction_standard_2026-08-15.md`. This spec uses that amendment. It does not restate it.

`file:line` references are to `main` at `85fa56f9965110d040ad76caf706256b6a224967`, for checking only.

## In plain words

Master Craftsman should be someone Robert can talk with, anywhere and every day. Voice is a utility the domains share. Domains do not each rebuild it.

Today the live MC voice cuts him off on a pause, shows text while he is still talking, and lags. The live-voice host that already behaves correctly does not install, because production MC is the OpenClaw container and that host only installs for the old CoS kind.

What he gets when this is built:

- **Voice** is a live conversation. A one-second pause does not send anything. He can interrupt. He sees no running transcript. When voice ends, the turns land in the conversation. When he says he is done, he gets a short summary and confirms it in writing.
- **Dictate** stays as it is. He starts and stops it. Silence never sends. The text lands in the box for him to edit.
- He can choose **OpenAI or Grok Voice** for that conversation, including when the voice is consulting Master Craftsman. A later provider joins the same way.
- Master Craftsman keeps **one voice** wherever he appears, including the floating panel on German, Portuguese, Curator, and the other domains. Chief of Staff keeps her own voice. Practice personas and domain pages keep theirs.
- He can try the voice on a **local bench** before any of this is wired into MC.

**Honest limits.**

- The bench does not write a note, a file, or a conversation. Turns on the bench die when the page reloads.
- A phone test needs HTTPS. The first bench session is on the Mac. The phone test is Robert's, later, before anyone proposes production.
- Voice ids are chosen by ear on the bench. This spec does not pick `marin`, `eve`, or any other name as Master Craftsman's voice.
- Chief of Staff's voice does not change. Her provider choice becomes available. Her default stays.
- Curator does not gain a domain voice.
- This spec does not deploy, and it does not turn the production flag on.

## 1. Context for a reader without the repository

| Fact | Reference |
|---|---|
| Two realtime paths exist. The direct path mints a short-lived browser credential. Gespräche and Conversas use it, with OpenAI or xAI, and no tools | `core/realtime_voice/bootstrap.py:45`, `:224`; `core/realtime_voice/static/adapters/openai-webrtc-adapter.js`; `core/realtime_voice/static/adapters/xai-websocket-adapter.js` |
| The controlled path holds the provider session on the server and dispatches tools. COS Confer and MC use it. Today both construct `OpenAIControlledProvider` only | `core/realtime_voice/server_openai.py:21`; `domains/cos/managed_realtime.py:47-50`; `minimoi_portal/guild_ui/mc_managed_realtime.py:37-42` |
| MC's managed host installs only when `services.mc.kind == 'cos'`, the voice flag is on, and the budget tables answer | `minimoi_portal/guild_ui/mc_managed_realtime.py:25-36` |
| A second `kind == 'cos'` gate on the voice path is inside the turn. Without it, a managed-voice turn does not get the host channel that authenticated the owner | `minimoi_portal/guild_ui/api.py:2222-2224`; binding described at `:946-953` |
| Two further `kind == 'cos'` branches in that function are Board outcomes, not the voice binding | `minimoi_portal/guild_ui/api.py:2155`, `:2208` |
| A consult keeps the utterance as a note with `page='voice'` at consult time, before the session ends | `minimoi_portal/guild_ui/mc_managed_realtime.py:131` |
| The floating MC button is one Guild conversation, injected on Mein Deutsch, Meu Português, Curator, Chief of Staff, IoT Connect, Guild, and Home. The domain name is a label. Guild pages that already have the chat do not get a second button | `minimoi_portal/companion.py:1-5`, `:19-30`; `minimoi_portal/templates/_companion.html:11-16` |
| Provider VAD commits a user turn as `speech_stopped` with no `source`. The browser RMS timer emits the same event name with `source: "browser_vad"`. The spec forbids that timer from owning boundaries | `core/realtime_voice/static/adapters/openai-webrtc-adapter.js:179-180`; `core/realtime_voice/static/adapters/xai-websocket-adapter.js:209-210`; `core/realtime_voice/static/adapters/openai-transcription-webrtc-adapter.js:178-186` |
| The controlled host already takes a `dispatch` callable. That is the consult contract | `core/realtime_voice/controlled_host.py:13` |
| Budget tables are `voice_control_owner`, `voice_control_session`, `voice_control_call`, `voice_control_usage` | `core/realtime_voice/sql/001_voice_budget.sql`; checked at `mc_managed_realtime.py:34-35` |
| Default voice ids today are `marin` (OpenAI) and `eve` (xAI), unless the persona names one | `core/realtime_voice/bootstrap.py:45`, `:224` |

## 2. The voice bench, first

The first build Robert can use is a local page. No MC file changes in that commit.

He opens it on the Mac, picks OpenAI or Grok Voice, and presses Voice. A one-second pause does not cut him off. He can interrupt. He sees no running transcript. When he ends the session, the turns appear on the page. A labeled stub summary appears if he says he is done. He does not confirm it inside the voice session. Dictate is on the same page so Memo can be seen beside Conversation. This trial does not rebuild Dictate.

**Which path.** A bench that can consult is a controlled session, not the direct browser path. It is a small controlled host on localhost, with the existing `dispatch(name, text, cid, operation_id)` plugged in. The stub answers a fixed set of questions and refuses the rest, and each answer is labeled as a stub. The agent implementation comes later, through the same dispatch, and the bench does not call MC until the reconnect in §4 has a reviewed consult path.

**What it needs to run.**

- A local server process holds the provider key. The key is read from the Keychain at start. It is never written to a file, a log, or the page.
- The stub stage may use a named in-memory budget, test-only, so the first listen does not require Postgres. Any stage that claims a real budget uses a local Postgres and the shared SQL in `core/realtime_voice/sql/`.
- The page is not a Guild route and not a production route. It is off in production compose. It has no production flag.

**What the page shows, after the session.** Time from provider speech-stop to first assistant audio, in milliseconds. Whether an interruption stopped the active reply. Committed user turns per scripted thought.

**Fragment.** Count only provider events. `speech_stopped` emitted by the realtime adapters from `input_audio_buffer.speech_stopped` is a committed user turn. Events with `source: "browser_vad"` are never counted. The bench script has Robert say one sentence with a deliberate one-second pause inside it. That thought passes when it produces exactly one committed user turn.

**Turns.** They stay on the page and die on reload. Keeping one would be a write, and the bench does not write.

## 3. Grok Voice on the tool-using path

Robert's decision, recorded by Claude Code: the path COS Confer and MC use can consult the agent, and that path gains Grok Voice as part of this build. It is a small shared-layer provider, not a new session loop.

- Add `core/realtime_voice/server_xai.py` beside `server_openai.py`. The server holds the xAI realtime session and relays tool calls through the same `dispatch`. Event names follow the existing browser adapter `xai-websocket-adapter.js`.
- The controlled host selects the provider per session. MC and COS Confer both gain the choice through that one provider. Neither domain gets its own copy.
- COS Confer's default provider does not change. The choice becomes available. Changing her voice is out of scope.
- The same controlled-session contract tests run against both providers, stubbed. The release check in §6 runs one consult through each.
- On the bench page, an xAI voice id can be auditioned on the tool path only after this provider exists. Until then the page says so, and does not pretend a direct-path listen is the tool path.

A third provider, when Robert names one, joins this registry the same way. It does not get its own session loop.

## 4. Reconnect MC

After the bench has been heard and the shared provider reviewed:

- `mc_managed_realtime.install` also installs when MC is the OpenClaw container (`kind == 'openclaw'`), behind the existing `MINIMOI_MC_MANAGED_REALTIME` flag. The `kind == 'cos'` path stays while that kind still exists. A missing flag still installs nothing.
- The voice binding at `api.py:2223` gets the equivalent for the OpenClaw adapter. A voice consult without that binding does not run. The Board-outcome branches at `:2155` and `:2208` are not this change. Before code, the implementer lists every `kind == 'cos'` on the voice path and shows the list in the commit. That list is not deferred to the reconnect commit's review.
- Allow-listed tools consult the container MC through the same turn service, session identity, authority, and write deduplication as typed input.
- A note is spoken as saved only after a platform receipt. No receipt, no success sentence.
- The voice model speaks. An ordinary utterance does not become an OpenClaw round trip. A consult does. That division stays visible.
- The instruction string that says "shared CoS/MC build partner" is replaced, for MC only, by the card in §7. COS keeps its own instructions.

## 5. The composer

A separate commit on the same branch.

- Voice on the MC composer uses the Conversation controller. No competing live transcript.
- Consults do not appear as live chat notes. A voice-page note is held out of the open chat until the session ends, then added with the other turns. A consult that is not a note creates no visible note at all. The current `add_note(..., page='voice')` at consult time is the behavior this commit replaces.
- On "we're done", a short summary is offered in the conversation for written confirmation. That confirmation is an ordinary typed note. The spoken "we're done" is not the confirmation. The first real summary is a consult. The bench stub summary is not this card.
- Dictate stays the Memo control it is. Its module is not edited unless a shared-file change is required to keep the two controls from fighting over the microphone. Any such change is named in the commit.
- Phone width and desktop width are both in this commit.

The floating panel is the same composer, in an iframe. It gets these controls because it is that conversation. It does not get a second voice, and it does not take the voice of the page underneath.

## 6. Release check

The MC release check creates a managed session against the stub provider and runs one `consult` through `dispatch` to the configured MC kind, asserting an answered result, with the flag on and `kind == 'openclaw'`. It does the same for each controlled provider. With the flag off, it asserts the route is absent. A check that only asserts the route exists is not this check.

## 7. Voices

One voice per person, not one voice for the product.

| Person or surface | Voice | This build |
|---|---|---|
| Master Craftsman | One voice wherever he appears: Guild chat, the floating panel, the bench, a later phone session | Audition and lock his card and his voice ids |
| Chief of Staff | One voice wherever she appears, different from his | Unchanged. The provider choice becomes available |
| German, Portuguese, and any later domain | Own style, own voice ids | Untouched. The bench must not overwrite their persona voices |

A shared default such as `marin` or `eve` is a fallback for a surface that has not chosen. It is not Master Craftsman's voice, and it is not the Chief of Staff's.

**His card.** Short enough to send on every MC session. Not the soul pasted in. Not sent on a CoS session or a German session.

- He is Master Craftsman, Robert's colleague in the work. Warm, direct, brief. No flattery. No lists read aloud. No markdown spoken.
- English is the default for the work, spoken with a light Austrian accent: stable, easy to understand, not a caricature.
- When Robert speaks a real German sentence, answer in Austrian German, briefly, and correct gently if he is practising. An isolated German word inside English work stays inside the English reply. Do not switch because of his accent. If it is unclear which language he wants, ask in one short question.
- Mini-moi, Master Craftsman, and the other agents' names are pronounced the same way on every provider. The bench script includes them.
- One voice id per provider is stored beside the card, not inside it. The same ids are used wherever he speaks. He picks them by ear. Until he picks, the bench offers the provider's quality set and does not pretend a default is already him.

**Where the card and the ids live.** In the MC persona release, as `voice.json`, so a voice-id change is a persona release and the manifest hash covers it. The live workspace is a projection of that release. It is not a second source.

**Practice, checked 27 October 2026, not re-verified by Claude Code.** OpenAI's realtime prompting guide recommends naming accent and language as separate instructions, and recommends `marin` or `cedar` for quality: [realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations), [voice prompting](https://developers.openai.com/api/docs/guides/voice-prompting). xAI's voice-agent docs describe `eve` as British, say each voice speaks every supported language, and describe custom-voice cloning as console-limited, with API create enterprise-gated and currently US-only except Illinois: [voice](https://docs.x.ai/docs/guides/voice), [custom voices](https://docs.x.ai/developers/model-capabilities/audio/custom-voices). A reviewer who depends on one of those claims checks the page. The build does not.

The first bench audition is one script on both providers, once §3 exists: a work question in English, a German sentence, a mixed sentence, an interruption, and a one-second pause. He marks whether the accent held and whether a stray German word flipped the language. Comparing another card on the bench is allowed if the page labels whose card is playing. Comparing does not assign that card to him.

## 8. What this build does not do

- It does not add a second session loop. The xAI controlled provider is in scope. A private copy of the controller is not.
- It does not add Curator voice, and it does not change Chief of Staff's voice or her default provider.
- It does not pick voice ids. He does, by ear.
- It does not deploy, set a production flag, or claim a phone test.
- It does not edit the spec file beyond the amendment in the companion commit.
- It does not include unrelated branches.

## 9. Acceptance

- The bench script's one-sentence thought, with a one-second pause inside it, produces exactly one committed user turn, on each controlled provider.
- An interruption stops the active reply.
- MC Voice, in the Guild and in the floating panel, adds turns to the conversation only after voice ends. A consult does not appear as a live note.
- On "we're done", a short summary is offered for written confirmation. The spoken phrase is not that confirmation.
- A note is spoken as saved only after a platform receipt.
- Dictate still does not submit on silence.
- The release check in §6 passes for the OpenClaw kind with the flag on, and asserts no route with the flag off.
- Gespräche, Conversas, Lesen, Schreiben, Leitura, Escrita, and COS Confer still pass their existing voice checks. Maria's voice on Gespräche is unchanged. The Chief of Staff's voice is unchanged.

## 10. Open questions for Robert

1. **Phone path.** Recommendation from Claude Code: the first bench session on the Mac at localhost; the phone on dev later, when that gate is included. A new tunnel is not proposed.
2. **Voice ids.** His, by ear, for Master Craftsman only. This spec does not ask him to pick before the bench exists.
3. **A third provider.** Unnamed until he names it.
4. **Cloning a voice.** Optional, after he has heard the instruction-steered pair and wants the accent steadier. Not part of the first bench.
