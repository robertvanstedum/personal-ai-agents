# cos_interface.md — CoS Backend Interface Contract v0.2

**Status:** Corrected 2026-07-12 — replaces v0.1's whole-agent-replacement framing
**Companion to:** spec #133 v1.2
**Corrects:** v0.1 assumed CoS's *entire agent* would be swapped behind a `handle_message()` boundary. Spec #133 v1.2 established a two-layer model instead — `chief_of_staff.py`'s coordination layer is permanent and platform-owned; only the backend call underneath it is the swap point. This version governs that narrower, correct boundary. Confirmed against real `chief_of_staff.py` code (2026-07-12 recheck) — v0.1 did not match the running implementation on three points: message shape, memory ownership, and memory format. All three corrected below.

---

## The Contract in One Sentence

A backend is CoS-conformant if it can receive a prompt and context from `chief_of_staff.py`'s coordination layer and return a response — without needing to own routing, scope enforcement, or the memory file's location and format.

---

## Two Layers (recap from spec #133 v1.2)

- **Coordination layer (`chief_of_staff.py`) — permanent, platform-owned.** Routing, scope enforcement (observe/mutate boundary — see spec #133 v1.2), memory file location and format (`cos_memory.md`), tool-availability policy.
- **Backend layer — swappable.** Generates the actual response. Also owns memory-*worthiness judgment* — deliberately, not an oversight: Robert wants this to be a backend quality dimension (it's specifically why OpenClaw is the target backend — better memory judgment, better conversational continuity), not identical platform-enforced logic regardless of which backend is active.

---

## Required Surface

### `call_backend(prompt, context, tool_policy) → response`

**Input** (coordination layer → backend):
```
prompt: str            — the user's message
context: dict           — relevant history assembled by the coordination layer
                           (recent cos_memory.md entries, relevant agent_logs).
                           The coordination layer decides what to include;
                           the backend doesn't need to know storage internals.
tool_policy: dict        — which observational capabilities are currently
                           permitted (git status/log, health checks, web
                           search: yes. Anything mutating: no, without
                           explicit Robert approval.) See Scope Enforcement,
                           spec #133 v1.2.
```

**Output** (backend → coordination layer):
```
response: str           — the reply to relay back to Robert
```

**Memory write is not part of this return value** — correcting v0.1's `entries[]` design, which assumed the platform stores whatever the agent reports. Reality: the backend writes directly through the coordination layer's existing write path (`_append_memory()` or equivalent) when it judges something worth keeping. The coordination layer doesn't need structured entries reported back to it; it needs writes to go through the approved path, in the approved format.

### Memory Format (corrected from v0.1)

**`cos_memory.md`** — flat markdown, dated entries. **Not** `cos_memory.json`. v0.1 assumed a structured JSON schema from a since-superseded design; v1.2 explicitly retains `.md` for Phase 1. A structured-JSON migration remains a documented future option (spec #133 v1.2, Change Log item 3), not a Phase 1 requirement or part of this contract.

---

## Tool/Capability Handling — mechanics differ by backend, policy doesn't

How `tool_policy` is actually enforced differs structurally by backend type — this wasn't addressed in v0.1 and needs to be explicit, since it's not one mechanism:

- **Direct-API backends (Grok, today):** tools are functions implemented inside `chief_of_staff.py` itself, passed to the model as function-calling schemas. The coordination layer executes them when the model requests, and `tool_policy` simply gates which functions are offered.
- **Agent-runtime backends (OpenClaw):** the backend has its *own* native tool/skill execution (shell, browser, file access, web search). `tool_policy` instead configures which of the backend's native tools are enabled for that session — observation tools enabled, mutation-capable tools disabled unless explicitly approved.

Conformance doesn't require identical plumbing — it requires both mechanisms to enforce the same observe/mutate policy from spec #133 v1.2, regardless of how each backend implements it.

---

## Conformance Test (revised)

1. Both backends (Grok, OpenClaw) respond coherently to the same prompt + context through `call_backend()`.
2. Both respect `tool_policy`: observation requests succeed; mutation requests are refused or redirected, whether caught by the backend itself or the coordination layer.
3. **Swap test:** a conversation continues coherently across a backend swap mid-session, since state lives in `cos_memory.md` (coordination layer), not in either backend.

---

## Known Conformant Implementations (target)

| Backend | Mechanism | Status |
|---|---|---|
| `grok_backend.py` | Direct Grok API call | Proven, running today |
| `openclaw_backend.py` | WebSocket client to a dedicated, CoS-scoped OpenClaw Gateway instance — own port/process, isolated from personal OpenClaw #1, native tools scoped to observation only | Phase 1 build target |

---

*cos_interface v0.2 · 2026-07-12 · Claude.ai (Fable 5) · Corrects v0.1 against real `chief_of_staff.py` code and spec #133 v1.2*
