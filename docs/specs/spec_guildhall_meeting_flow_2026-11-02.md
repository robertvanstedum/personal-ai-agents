# Guildhall Meeting Flow and Etiquette

**Version:** 0.1 · **Date:** 2 November 2026 · **Decision owner:** Robert · **Author:** Codex  
**Status:** Implemented and live-tested on dev at `63e951cd`; production integration pending. The limits of the live checks are recorded below.

## Purpose

Robert, Chief of Staff and Master Craftsman should converse naturally in one room. The **Guildhall meeting harness** handles addressing, turn-taking, context, corrections and limits beneath that conversation. Agents keep their distinct identities and capabilities. Robert should not need prescribed phrases, a recipient menu for every message, or a fixed MC-first order.

This is a standalone 1.2 work item. It governs meeting interaction, not agent installation, general task execution or the separate Agent Harness activation gates.

## Meeting rules

1. **Names and intent guide addressing.** Recognize participant names and aliases from the room directory, including ordinary language without `@`. Distinguish addressing someone from discussing them. An explicit recipient selection overrides inferred recipients. If a request is genuinely ambiguous, clarify briefly instead of silently widening an action request. Routing decisions do not grant tool authority.
2. **Open questions reach the group.** Unaddressed messages use a tool-free discussion path, so an ambiguous action request cannot run twice. To execute work, Robert names or selects a teammate. For an open question to everyone, eligible participants may prepare replies independently; display completed replies as they arrive. Neither agent permanently goes first. An unavailable participant does not block the others. Agents may abstain when they have nothing useful to add.
3. **Requested exchanges follow their actual dependencies.** “CoS, ask MC a question and MC answer” gives CoS the question turn, then gives MC her actual recorded question. This is a dependency, not a fixed script. Reverse the names and the order reverses. Each reply is produced by its named agent; nobody fabricates another participant's answer.
4. **Keep the floor open to Robert.** Typing and sending remain available while agents answer. A newer correction supersedes unstarted work from the earlier message and prevents stale automatic follow-ups. Preserve the record and anything newly typed during a send. Label late replies as answering an earlier message. Sending a correction is not proof that an already-started external action was cancelled; report its actual outcome.
5. **Bound automatic discussion.** First-release limit: one automatic agent-to-agent follow-up per owner message, then return the floor to Robert. Preserve original requested turns; never replace them according to connector timing. Room duration, turn and spend limits still apply. An agent may ask Robert to continue the exchange. Longer autonomous meetings are outside this slice.
6. **Discussion cannot re-authorize work.** Automatic follow-ups use an enforced discussion-only execution path, with no action tools or memory-write callbacks. Pass the attributed question as the current request; retain Robert's original message as provenance, not a repeated command. Deliberate work requests use the ordinary authorized action path and its receipts. A conversational “ask MC” must not create a second issue, document or task.
7. **Make presence and capability truthful.** A participant replying through the room connector is already participating; it does not need to pretend to join again. Show invited, available, answering and unavailable states from actual evidence. Agents describe only access they can establish, and never invent settings pages or another agent's capabilities.

## Minimum implementation contract

Persist the owner message, routing decision, responding identity, reply-to event and turn/attempt identifiers. Commit an agent reply and any permitted follow-up scheduling atomically; delivery retries must return the existing result without another inference or action. Recheck membership, approval, room generation, newer owner messages and remaining limits before dispatch. An uncertain attempt requires reconciliation, not automatic replay.

The harness has three responsibilities: resolve recipients and dependencies; admit and schedule bounded turns; record replies and outcomes. Agent etiquette prompts complement these controls but cannot enforce permissions or duplicate prevention. Use deterministic handling for explicit selections and clear addresses; any model-assisted interpretation must be bounded and validated against room membership. Routing must not require an extra model call for every straightforward message.

Keep the composer simple. The recipient selector remains an optional shortcut. “Send now” means a new owner message can enter the room during a reply; a future “Steer” control may make that intent explicit, but must not claim instant cancellation. Text and voice should feed the same routing rules; overlapping spoken output needs separate audio-floor handling before room voice is accepted.

## Acceptance examples

| Input or condition | Required result |
| --- | --- |
| “MC, are you here?” | MC answers as MC; CoS is not called solely by default. |
| “CoS, MC is unavailable. Can you help?” | CoS answers; mentioning MC does not invite him. |
| Explicit To CoS, with MC mentioned in the text | Only CoS receives the initial request. |
| “What do you both think?” | Both may answer independently, with no fixed MC-first wait. |
| Robert's example: “CoS can you ask a question for MC and MC answer please” | CoS asks; MC receives that exact question and supplies his own answer. No simulated dialogue. |
| The same exchange with names reversed or paraphrased | The intended dependency holds without an exact phrase template. |
| “Actually, only discuss it; don't open an issue” while work waits | Waiting work is superseded; running work is reconciled honestly. No stale follow-up. |
| A slow send while Robert types a correction or switches rooms | The correction and the other room's draft survive; no duplicate message. |
| Timeout, refresh or duplicate delivery during a handoff | One recorded reply and at most one follow-up; no blind replay. |
| Original request includes filing an issue, followed by a teammate question | The discussion follow-up cannot execute that action again. |
| One participant fails, or the room pauses | Other permitted replies remain usable; pause prevents new dispatch; no retry loop. |

Completion requires isolated tests, independent diff review, and post-deployment dev checks of Robert's examples with retained transcripts and screenshots. A successful greeting alone is insufficient. Exact inference latency is provider-dependent; the harness must not add a fixed waiting period behind an unrelated participant.

## Current implementation boundary

The original dev transcript exposed CoS simulating MC and misunderstanding room participation. At `63e951cd`, source separates tool-free discussion from action-capable owner turns, preserves original turns, and refuses retries that would lose discussion provenance. Independent diff and rollout review cleared; 33 isolated Python tests plus the executable browser-script checks passed. Both dev workers now declare discussion protocol 1 and Records has `RECORDS_DISCUSSION_HANDOFFS=1`.

Live checks on 2 November reproduced Robert’s exact join and CoS-asks-MC examples, a plain-language version without mentions, an open question to both agents, MC-only addressing, and explicit To CoS overriding a textual mention of MC. Distinct peer replies and the bounded follow-up were observed. Typing and sending a correction remained available during a reply; the prior reply committed just before the correction was sent, so live interruption of an already-running action was **not** proved. Offline tests cover stale-follow-up fencing and draft preservation. Voice, GitHub actions and career-file access are separate acceptance gates; these meeting checks do not establish them. Evidence is retained locally in `_working/meeting-flow-deploy-20261102/`.

Deployment order: install both workers with discussion protocol 1 and route-scoped gateway credentials, verify them, then update Records and enable `RECORDS_DISCUSSION_HANDOFFS=1`. Records will not give discussion turns to a worker that lacks the protocol declaration. Before any downgrade of Records, disable creation, stop workers, and fence **every** non-committed discussion turn into an old-code non-retryable state (`abandoned`) while the new Records code still understands its type. Include failed, cancelled, expired and uncertain rows: merely cancelling them leaves an old-code retry path. Verify only committed or abandoned discussion rows remain before starting older code; retain completed history. Disabling the flag alone is insufficient. Do not roll back by simply starting old workers against an active discussion queue.

## Related patterns and standards

- [Microsoft AutoGen Selector Group Chat](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/selector-group-chat.html) describes shared-context group chat with dynamic speaker selection and termination conditions. This is a relevant orchestration pattern, not a claim that MiniMoi uses AutoGen.
- [A2A Life of a Task](https://a2a-protocol.org/latest/topics/life-of-a-task/) distinguishes messages from tracked tasks and groups related interactions with a context identifier. A2A is a related interoperability standard; this spec does not claim A2A compatibility or adopt its wire protocol.

The intended contribution is their application to an ongoing personal meeting: natural addressing, distinct agents, owner steering, durable context and bounded authority. Uniqueness is not established by this reference check.
