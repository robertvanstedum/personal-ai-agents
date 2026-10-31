# Independent review: Spec 165 draft v0.1 (MiniMoi 1.2 voice, MC and CoS on one standard)

**Reviewer:** a fresh Claude Code review agent. It read the spec, the code at `grok/mc-voice-conversation` `93bf00c6`
and `origin/main` `4954ad9f`, Spec 164 v0.3, and the Chunk B build notes, contract and reviews. Read-only. It
changed nothing except this file.
**Date:** 28 October 2026.

## Verdict: revise (v0.2), not a redesign

The architecture is right. Moving the host-agnostic half of `mc_voice_direct.py` into `core/realtime_voice/` is
feasible, and it is the only place both images can share, because `Dockerfile.cos-scheduler` copies `core/` and
`domains/` but not `minimoi_portal/guild_ui/`. The fidelity to Robert's 28 October decisions is good: CoS is a core
goal, not optional, and no German or Portuguese behaviour is planned for 1.2.

Seven findings must still be fixed before Robert approves. Each one either breaks something on day one (every CoS
consult fails, production MC voice goes dark at merge) or rests on a wrong fact that Robert would decide on (five
images instead of a full release, D1's framing, where CoS keeps her records).

**Counts:** 7 must fix · 11 should fix · 10 minor.

---

## Must fix

**1. Merging 1.2 switches production MC voice off until the admission is turned on. The "fast rollback" leaves no MC
voice at all.** §11.5 step 1, §11.6, §9 row 1.
- Evidence: production runs MC voice today: `docker-compose.prod.yml` portal sets `MINIMOI_GUILD_VOICE_PRODUCTION=1`
  and `MINIMOI_MC_VOICE=1`. On `main` the composer falls back to the chained relay (`main:mc_voice.js:136-141`). On
  the branch it tries direct, then managed, then shows "Voice unavailable" (`mc_voice.js:147-149`).
- So step 1 ("merge with both production admissions off") removes MC voice from production the moment it deploys.
  The "fast" rollback in §11.6 ("Voice unavailable … Typing and Dictate work") is a second outage, not a rollback.
  Only the full image rollback restores voice. CoS is different: with her switch off, Confer stays as it is today
  (§6.8), so the two hosts behave differently.
- Change: pick one and say it in §11:
  - (a) Until the cleanup release, the composer uses the relay when the capabilities answer does not offer direct
    (as on `main`). This is not a silent fallback: a page with direct on never tries the relay. Merge and fast
    rollback then cost nothing.
  - (b) Accept the gap, say it plainly, and make it a decision for Robert (merge and MC admission on the same day,
    minutes apart).

  (a) is recommended. Either way, also fix §9 row 1 ("unreachable from the composer … already so on the branch").

**2. The 1.2 release is a full release of all nine services, not five images.** §2.4, §11.5 step 1, §11.7.
- Evidence: `scripts/ci/classify_release.py` maps unknown paths to `full` (`:405-407`).
  `services/mc_operations/personas/…` and `scripts/mc_persona/…` match no rule. Run the classifier on the branch
  diff (`git diff --name-only origin/main..93bf00c6`) and it returns `"release_class": "full"`: portal, curator,
  german, portuguese, system-bot, cos-bot, cos-scheduler, cos-agent-a, model-gateway. The same is true of every
  future MC card adoption (§5: "a new version if the card text changes after the audition"). D3 and D1 changes
  would each be a full deploy.
- Change: correct §2.4, §11.5 and §11.7 to what the classifier says. Then either add a classifier rule in A1 for
  `services/mc_operations/personas/` (portal, plus whatever reads the persona) and `scripts/mc_persona/`, with a
  test, or accept full releases and add curator and cos-agent-a to the post-merge checks.

**3. CoS's voice store would sit inside the folder her agent runtime reads and greps.** §6.6, D5.
- Evidence: `cos-agent-a` mounts `/opt/minimoi/cos/conversations` read-only (`docker-compose.prod.yml:102`). That
  is the same host folder as `cos-scheduler`'s `COS_CONVERSATIONS_DIR` (`:431`, `:448`). `conversation_log.py:5`
  says "CoS's runtime reads it read-only and searches it like any file", and `system_note` (`:44-48`) tells her to
  grep it.
- Problem: `<COS_CONVERSATIONS_DIR>/voice/<session>.jsonl` holds live, dropped and undecided records. These are
  exactly what must stay hidden until End or recovery. The runtime could find them and quote them in a typed Confer
  reply or a Telegram reply (`cos-bot` uses the same runtime) while the session is live.
- Change: D5 should recommend a voice folder that is not mounted into `cos-agent-a`, for example
  `/app/data/cos-voice` from a new `COS_VOICE_DIR` (today's option (b)). Only the End-time append from D6 goes into
  the recall folder. Add a release-check assertion that the voice folder is not in `cos-agent-a`'s mounts.

**4. Every CoS consult would be refused: Confer's request id must be a UUID.** §6.4.
- Evidence: §6.4 passes `request_id=<vc-session-n>`. `ConferTurnService` runs `normalize_request_id`, which raises
  `InvalidConferTurn("request_id must be a valid UUID.")` for anything that is not a UUID
  (`domains/cos/confer_service.py:188-194`). Under §6.4's own mapping, every consult would then be spoken as "That
  did not go through."
- Change: specify a deterministic UUID, for example `uuid5(namespace, session_id + call_id)`. Retries then keep one
  operation id. Add a test that a consult with a real `vc-…` call id reaches the turn service.

**5. CoS consults and summaries go through a 30-second portal timeout, but her turns take up to 120 seconds or
more.** §6.4, §6.7, §6.9.
- Evidence: the portal's default `/app/cos` bound is `(5, 30)`. Only `ui/send` and `chat` get 125 s
  (`minimoi_portal/proxy.py:481-485`). CoS turns "wait up to 120 s for Agent A" (`proxy.py:479`). Typed Confer runs
  turns in the background and polls, because "a research turn … can outlast the edge's 120 s request limit"
  (`chief_of_staff.py:1507-1509`).
- Problem: the core consult route is synchronous (`consult.py:101`). A CoS consult that passes 30 s gets a 504 from
  the portal while the turn keeps running and may still have an effect. Voice then says it did not go through,
  which is review B #2 again, now for CoS.
- Change: in §6.9, add route timeouts for the CoS direct consult and summary routes. Also state what happens past
  the edge limit. Either the consult starts a background turn and the core keeps waiting within a bound, or past N
  seconds voice says "I'll finish that in writing" and the reply lands in the thread when it completes, with its
  receipt.

**6. CoS cannot set a reminder by voice. Script step 5 and the §6.4 "reminder" row can never pass.** §6.4, §6.5,
§11.3 CoS step 5, §12.1.
- Evidence: Confer reminders exist only as `/remind <UTC ISO time> <text>` (`confer_service.py:51-55`, `:157-185`).
  "Remind me at 5 pm to stretch" does not parse as a reminder; it goes to the agent as ordinary text. (MC's
  natural-language "remind me" parser is in `minimoi_portal/guild_ui/owner_actions.py`, which CoS cannot import.)
  Also, the MC operating rules that §4.2 moves into the MC adapter tell the model to ask for "remind me at <time> to
  …". CoS must not inherit that sentence.
- Change: for 1.2, say CoS voice does not set reminders: "Set reminders in writing." Drop step 5 and the reminder
  receipt test. Or add a spoken-reminder parser to CoS as new, named scope with its own review. A parser turns
  speech into a scheduled action, so it is an authority question, not a detail.

**7. D1 is mis-framed: MC and CoS share one canonical persona that Robert approved, and CoS's runtime already
speaks it.** §6.2, D1, §1 ("MC's manner is not inherited by CoS by side effect").
- Evidence:
  - The canonical persona is titled "Master Craftsman and Chief of Staff — shared persona"
    (`planning-studio/initiatives/INIT-2026-0007-interaction-vision/documents/MASTER_CRAFTSMAN_PERSONA.md:1`).
  - Its "Your manner" section is compiled into CoS's runtime SOUL at image build (`docker/Dockerfile.cos-agent-a`,
    `build-mc-soul.mjs`). The build is hash-locked, with the lock `approved_by: "Robert"`.
  - The section ends "Voice and text should feel like the same teammate."
  - CoS's consult answers, which voice speaks as returned (`mc_voice_direct.py` TOOLS: "Speak the result's 'spoken'
    text"), come from that runtime.
- So the Austrian manner is not "MC's manner appended" by accident: it is an owner-approved shared persona. D1 (b)
  or (c) as a card-only change would give her voice one manner and her consult replies, typed replies and Telegram
  replies another.
- Change: state this in §6.2 and D1. Options (b) and (c) mean changing the canonical persona and its lock (owner
  adoption), not only her voice card. Keep (a) as the recommendation, and describe "each person has their own card"
  as voice ids and voice-surface wording within the shared persona.

---

## Should fix

**8. Extending the browser-bridge trust decision to CoS is a new decision, and the spec does not name it.** §1, §8.
- Evidence: Spec 164 §3 "relaxes [the managed rule] for MC", and §15.1 approved it for MC.
- Change: add D18, "the Spec 164 §3 trust model applies to CoS too". State what a CoS typed turn can do (her tools,
  the Vault allow-list, `cos-scheduler` has `/var/run/docker.sock` mounted). This also matters because today
  Confer's typed sends are CSRF-checked only when `MINIMOI_COS_WRITE_GUARD` is on (`app.py:749-751`; production
  value unverified). Voice would then be better protected than typing, which is fine but should be said.

**9. Speaking German to CoS (and MC) may be transcribed as English, and the consult acts on that transcript.** §6.2,
§7, §11.3.
- Evidence:
  - A direct session sets one transcription language from the locale: `en-US` becomes `en`
    (`bootstrap.py:61-64`; `openai_realtime.py:181-183`; on xAI, `language_hint`).
  - Today's managed CoS sends no language and uses a bilingual hint (`controlled_config.py:31-34`, `:48-50`).
  - Her draft card promises "When he speaks German, follow …".
- Change: state the 1.2 behaviour, and make bilingual transcription a requirement or an accepted limit (a
  decision). In the scripts, check what the transcript and the consult received after a German sentence, not only
  how the reply sounded.

**10. CoS has no production admission mechanism, and the portal's CSRF gate depends on a flag.** §6.9, §6.10, §11.1,
D11.
- Evidence: `production_environ` exists only in `guild_mounts.py`. `cos-scheduler` reads its environment directly,
  so "the CoS equivalent" does not exist yet. The portal renders the CSRF meta tag and CSRF-checks the CoS prefix
  only on env conditions (`app.py:722-723`, `:749-751`), so the two processes must agree.
- Change:
  - Name the CoS direct prefix (it must cover bootstrap, consult, transcript, end, summary, decision and sessions).
  - Have the portal CSRF-check that prefix and add the verified-write marker for it unconditionally, with no flag.
  - Put the production admission in one place, `cos-scheduler`.
- Also: the marker is added to POSTs only (`proxy.py:303-306`), so §4.4's CoS `owner()` (which requires the marker)
  would refuse `GET …/voice/sessions` and capabilities. Give reads an owner check without the marker.

**11. CoS notes saved during a live session show in Confer while the session is live.** §8 row "Everything written
during a consult is hidden".
- Evidence: the row says memory and reminders are things "Confer does not display". But Confer has Record and Track
  tabs fed by `/ui/api/notes` and `/ui/api/todo`, and a memory page `/ui/memory` (`chief_of_staff.py:1632-1650`).
- Change: correct the row. Either hide notes saved by a live voice session from those views until End (a tag, as
  with MC's `_TaggedFloor`), or have Robert accept the exception (a decision).

**12. Dev-first is missing for German and Portuguese, and the production check covers one adapter per language.**
§11.2, §11.5 step 2.
- Evidence: 1.2 changes the shared controller and both adapters (§2.4), but §11.2 has no German or Portuguese step
  on dev.
- Change: add a German and Portuguese dev smoke to A4. On production, require German on one provider and Portuguese
  on the other, so both the OpenAI WebRTC adapter and the xAI WebSocket adapter are heard. Also after merge: one
  typed Confer turn, one CoS Dictate, and one MC Dictate.

**13. The latency acceptance criterion cannot be checked as written.** §12.4.
- Problem: it compares 1.2's first audible playback with German's first-transcript baseline. On Grok the transcript
  arrives late (§20), so that comparison is lenient. Grok's baseline is five samples.
- Change: compare the same metric (speech end to first assistant transcript), state the statistic (median, plus
  worst of N) and the sample size (at least 10 ordinary replies per provider), and record first audible alongside
  it. Add a bound or target for consult replies, which have none.

**14. Two facts the spec marks "unverified" can be settled from the repository.** §6.6, §6.7, §20.
- `COS_CONVERSATIONS_DIR` is set in production compose (`docker-compose.prod.yml:431`, with its volume at `:448`).
  Compose `environment:` entries win over `env_file`.
- The OpenClaw backend keys the runtime session from `conversation_id` (`domains/cos/backends/openclaw_backend.py:132-134`,
  `:236`), so a distinct conversation id gives the summary its own runtime session. Then check that id against the
  Vault conversation allow-list and `conversation_log`.

**15. The §6.4 approvals inventory starts from a wrong premise.** §6.4, §20, D16.
- Evidence: `cos-agent-a` does not load `mc-actions`. The image copies only `cos-bounded-search` and
  `mc-cutoff-delivery` (`docker/Dockerfile.cos-agent-a:36-37`), and `openclaw.json` allows and loads only those.
- Change: inventory what is actually loaded: those two plugins, OpenClaw's built-ins, and `ConferTurnService`.

**16. German and Portuguese browsers may load mixed old and new controller code in 1.2.** §4.3, §14.2.
- Evidence: the controller and both adapters change in 1.2, but the German and Portuguese pages keep
  `?v=20260809-ga1` until Part B. A browser holding the old controller requests adapters under the old keys, and
  `send_from_directory` (`domains/german/html_server.py:138-144`) serves the new files.
- Change: either bump both pages' key in 1.2 (inert, and testable by the no-change tests) or add a compatibility test
  (old controller with new adapters). Also say plainly in §14.2 that the playback event and the new methods ship in
  1.2 inert, and Part B only uses them.

**17. Two hosts on one $20 daily cap, with each session reserving 60 minutes of exposure.** §7, D10.
- Evidence: `budget.py` header. MC voice, CoS voice and both Dictates draw on the same cap.
- Change: once the allowance value is known, show how many sessions a day fit. Let D10 also cover whether one host
  can use up the other's day.

**18. The phone tests and the CoS script are not concrete enough.** §11.2 step 4, §11.3.
- Change:
  - Add the phone cases: lock the screen and switch apps mid-session (`pagehide` on iOS Safari), AirPods or
    Bluetooth, speakerphone echo and barge-in, Wi-Fi to cellular.
  - Give the CoS script what the MC script has: "Check with CoS" twice ("Already checked"), a bare "yes" with
    something waiting (D16), reload recovery, and one Grok session.
  - Write a pass condition next to each step.

---

## Minor

19. **There are four `tests/mc_voice_direct_*.mjs` files, not five** (§5, §12.1): `host`, `reveal`, `session`,
    `thread`.
20. **§2.4 provider order** leaves out the saved preference and the dev query override (`config.py:46-58`).
21. **§2.1 states "production MC is `openclaw`" as fact, but §20 lists `MINIMOI_GUILD_CHAT` as unverified.** Also,
    MC's own rollback sets chat to `cos` (`scripts/operations/deploy_mc_container_ec2.sh:199`). In that case MC voice
    consults run through `cos-scheduler` and `cos-agent-a`, the same backend as CoS. Say that the MC adapter must
    work for kind `cos`, and test it.
22. **§7 says "no env override"** for the default provider, but §20 lists `VOICE_REALTIME_PROVIDER_DEFAULT` as
    unverified.
23. **Two line references are off by one:** `mc_voice_transcripts.py:7` should be `:6`; `controlled_host.py:13`
    should be `:14`.
24. **Name both CoS Dockerfiles.** Local `docker-compose.yml` builds `docker/Dockerfile.cos`; staging and production
    build `Dockerfile.cos-scheduler`. Both copy `core/`, so the extraction works for both.
25. **Known gaps from Chunk B are not carried forward.** Notes written in a separate request during a consult, and
    job result notes, are untagged (`REVIEW_independent_chunk_B.md`, "Not covered"). List them in §8 as known
    limits, with the CoS equivalent.
26. **CoS consults run in the live `owner` runtime session,** so her runtime knows what was said in voice before End.
    A typed reply in another tab, or on Telegram, could refer to it. Name this as accepted, as it is for MC.
27. **Grok becomes a new provider for CoS's data.** Her whole briefing (`cos_memory.md`, ops status) goes to xAI,
    through the browser. Name it in D2 or §7.
28. **Script wording and model alias.** "Save a note: …" depends on the transcript keeping the colon; "save a note
    that …" parses either way (`confer_service.py:41-49`). Separately, `grok-voice-latest` is a floating alias; say
    whether 1.2 pins it.

---

## Claim spot-check (29 claims)

| # | Spec claim | Reference | Result |
|---|---|---|---|
| 1 | On `main`, MC falls back to `ConferVoiceController` unless the managed path is offered | `main:mc_voice.js:136-141` | holds |
| 2 | Managed MC installs only for kind `cos` | `mc_managed_realtime.py:28` | holds |
| 3 | `PRODUCTION_HELD_OFF` and the managed production admission | `guild_mounts.py:220-222`, `:288-293` | holds |
| 4 | Direct off unless both flags; card pinned `0.1.18`; locale constant | `mc_voice_direct.py:46`, `:48`, `:50`, `:592` | holds |
| 5 | 0.1.18 manifest `candidate`; agent deployed from 0.1.16 | manifest; `deploy-mc-agent.yml:47` | holds |
| 6 | `voice_card.py` hard-codes MC's release path; hash refusal | `voice_card.py:16`, `:19-25` | holds |
| 7 | Guild direct UI reads `_sessionId` and `_adapter.sendContinuationInstruction` | `mc_voice_direct.js:222-223` | holds |
| 8 | `mc_voice_transcripts` imports `SessionRefused` from the managed module | `mc_voice_transcripts.py:7` | holds, line is `:6` |
| 9 | `_Sessions` / `_events` / summary / receipt ranges; file is 833 lines | `mc_voice_direct.py:124-268`, `:432-460`, `:464-522`, `:335-350` | holds |
| 10 | No store, no voice (start marker refused) | `mc_voice_direct.py:153-154` | holds |
| 11 | Confer offers two modes when managed is ready | `cos_ui.html:58-61`, `:491-492` | holds |
| 12 | Managed CoS: one conversation `owner`, owner guard with marker | `managed_realtime.py:15-46`, `:26-31` | holds |
| 13 | `VOICE_MANNER`, `cedar`, 24,000-byte bound, tool names | `controlled_config.py:8-30`, `:38-42`, `:51`, `:56-57` | holds |
| 14 | Her instructions = `_build_system_prompt()` + voice paragraph | `chief_of_staff.py:614-646`, `:649-679` | holds |
| 15 | `PARTNER_STYLE` on typed turns | `confer_service.py:341-342`; `agent_partner.py:2-12` | holds |
| 16 | Consult backend = `_process_confer_turn(…, request_id=<vc-session-n>)` | `chief_of_staff.py:811-841` | function holds; the id form is **wrong** (UUID required, `confer_service.py:188-194`) |
| 17 | Typed turns log and enforce file claims; managed consults skip both | `chief_of_staff.py:1462-1481` | holds |
| 18 | CoS Dictate depends on the Confer blueprint | `cos_ui.html:552-557`; `confer-input-controls.js:59-65` | holds |
| 19 | `cos-scheduler` copies `core/`, `domains/`, not `guild_ui/`; one process | `Dockerfile.cos-scheduler:8-15`; `chief_of_staff.py:1507-1509` | holds |
| 20 | Portal CSRF and marker only for the `managed/` prefix | `app.py:722-756`; `proxy.py:303-306` | holds |
| 21 | Classifier rules for `core/realtime_voice/`, `domains/cos/`, `minimoi_portal/` | `classify_release.py:346-357`, `:370-371` | holds per path; "1.2 deploys five images" is **wrong** (full) |
| 22 | Adapters changed but controller keys are old | `realtime-voice-controller.js:24-25` | holds |
| 23 | Consult cap 12, hold 10 s, owner mismatch refused | `consult.py:13-16`, `:89` | holds |
| 24 | Default voices; tool allow-list; transcription language table | `bootstrap.py:46-47`, `:50-60`, `:61-64` | holds |
| 25 | German and Portuguese register the bootstrap without `owner`/`admit`; cache key `20260809-ga1` | `german/html_server.py:127-131`; `portuguese/html_server.py:77-81`; templates `:1920`, `:1637` | holds |
| 26 | `cos-agent-a` runs with the `mc-actions` plugin | §6.4 | **wrong** (not copied or loaded) |
| 27 | `COS_CONVERSATIONS_DIR` in production is unverified | §6.6, §20 | **wrong** (set in compose `:431`) |
| 28 | Memory and reminders are not displayed by Confer | §8 | **wrong** (`chief_of_staff.py:1632-1650`) |
| 29 | Five `mc_voice_direct_*.mjs` suites; Phase 0 numbers | §5; `P0_run_*.md` | count **wrong** (4); numbers hold |

Not checked by running: the four known failing tests in §12.1. The Chunk B review records them as failing on
`main`; no run was made here.

---

## What is sound

- **The extraction analysis.** The split of `mc_voice_direct.py` into core and adapter is accurate. The hidden
  couplings it names are real: `SessionRefused` from the managed module, `INSTRUCTIONS` from the managed host, the
  reveal helpers, and the private controller members. Moving them before the managed code is deleted is the right
  order.
- **The consult contract.** It is reused unchanged: CSRF, owner, call-id dedup, in-flight guard, cap, owner
  transcript item, allow-listed tools at bootstrap, budget reserved after the session is recorded.
- **The safety table.** §8 carries every numbered item from both Chunk B reviews (B #1–#9, UI #1–#4 and #9) to both
  hosts. The exceptions are the CoS rows corrected above (findings 6 and 11).
- **Retirement order.** Code is removed only after a week of use, so an image rollback always works. The
  `voice_control_*` tables are untouched, and there is no backfill.
- **Production admission as an env flag on a released image (D11).** It is the right shape; it needs the CoS half
  built (finding 10).
- **The `VOICE_MANNER` table.** Going clause by clause makes "nothing inherited by side effect" checkable. It needs
  the shared-persona fact added (finding 7).
- **Part B.** It is properly queued and gated: Portuguese gets its first baseline, both languages and both providers
  are measured, and both roll back together. No German or Portuguese behaviour creeps into 1.2.
- **Honest limits and §20.** Both are useful. Two of the §20 items are settled in finding 14.
