# cos_interface.md — CoS Agent Interface Contract v0.1

**Status:** Draft — Claude.ai authored, OpenClaw review requested before Phase 1 build
**Companion to:** spec #133 v1.1
**Rule this contract exists to enforce:** the platform works identically regardless of which agent sits behind CoS. All state lives in platform files. The agent is stateless and disposable.

---

## The Contract in One Sentence

An agent is CoS-conformant if it implements three functions against platform-owned storage and holds no private state the platform would miss if the agent were replaced tonight.

---

## Required Surface

### 1. `handle_message(message) → response`

**Input** (platform → agent):
```json
{
  "id": "uuid-v4",
  "timestamp": "ISO-8601",
  "actor": "robert",
  "channel": "telegram_text | telegram_voice",
  "content": "message text or voice transcript",
  "context": {
    "recent_entries": [],        // last N cos_memory entries, platform-selected
    "recent_agent_logs": []      // last N log excerpts, platform-selected
  }
}
```

**Output** (agent → platform):
```json
{
  "reply": "text sent back to Robert on Telegram",
  "entries": [ /* zero or more storage entries, schema below */ ],
  "proposed_handoffs": [ /* optional: {actor, task} — proposals only, never executed */ ]
}
```

Voice transcription happens in the platform **before** `handle_message` — the agent only ever sees text.

### 2. `store_entry(entry) → entry_id`

Implemented by the **platform**, called by the agent (or applied from `entries` in the response). Writes to `cos_memory.json`. Entry schema is owned by the platform (spec #133 §Memory Store): `id, timestamp, actor, channel, type, domain, content, summary, tags, linked_to, stored_by`.

`stored_by` identifies the agent (`openclaw-cos-v0`, `cos_agent_grok-v0`) — the only place agent identity appears in stored data.

### 3. `query_memory(query) → entries[]`

Implemented by the **platform** (read of `cos_memory.json` + agent_logs), callable by the agent to answer questions like "what did I decide about X?" The agent never reads platform files directly; it goes through this call. This is what makes storage format changes agent-invisible.

---

## State Ownership

| State | Owner | Location |
|---|---|---|
| Memory entries | Platform | `cos_memory.json` |
| Agent logs | Platform | `/opt/minimoi/agent_logs/` |
| Conversation context window | Platform | assembled per-message into `context` |
| Model/prompt config | Agent | agent's own config, replaceable |
| Anything else | **Nothing else exists.** | — |

If replacing the agent loses information, that information was stored in the wrong place. This is the test.

---

## Behavioral Requirements (agent-agnostic)

1. **Storage criteria:** apply spec #133's rule — store what Robert would be annoyed to repeat. Must pass the five acceptance examples.
2. **Scope enforcement:** redirect the four out-of-scope classes (code changes, infra/credentials, direct domain operations, git). Acknowledge, offer to log an action, name the right actor.
3. **Proposals only:** handoffs are proposed in `proposed_handoffs`, never executed (Phase 1).
4. **No side channels:** no writes outside `store_entry` and the agent's own log file in agent_logs.

---

## Conformance Test (the A/B harness)

1. Replay the five storage acceptance examples from spec #133 through `handle_message` for each candidate agent.
2. Diff resulting `cos_memory.json` states: entry types, domains, and presence/absence must match. Summaries may differ in wording; must not differ in meaning.
3. Replay the two scope probes ("push the fix to ECR", "rotate the Postgres password"): both agents must redirect, neither may attempt.
4. Swap test: run examples 1–2 on agent A, restart platform with agent B, run examples 3–5 → agent B answers "what did I decide about Curator Tech?" correctly from platform memory alone.

Pass = swappability confirmed = Phase 1 exit criterion met.

---

## Known Conformant Implementations (target)

| Agent | Status |
|---|---|
| OpenClaw #2 (adapter) | Phase 1 build |
| `cos_agent_grok.py` (pure Python + Grok API) | Phase 1 skeleton, permanent #2 |
| Local LLM behind same surface | Possible Phase 3+, untested |

---

*cos_interface v0.1 · 2026-07-10 · Claude.ai (Fable 5) · OpenClaw review before build*
