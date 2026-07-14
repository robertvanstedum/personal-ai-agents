# Plan: OpenClaw #2 — CoS-Scoped Gateway, Dev Setup
**Date:** 2026-07-14
**Spec:** #133 v1.3, Phase 1, Task 9
**Status:** Ready to build — Dockerfile still blocked on three items OpenClaw must supply (see §What OpenClaw Needs to Supply). Everything else can be written immediately.
**Reviews:** Claude.ai (2026-07-14) · Grok (2026-07-14) · OpenClaw (2026-07-14) — all three agree on issues; fixes incorporated below.

---

## Context

The current primary backend for CoS is `grok_backend.py` — proven, running in production. `openclaw_backend.py` exists as a stub that raises `NotImplementedError`. Goal: provision an isolated OpenClaw Gateway instance (#2, port 18770, own config dir) and implement the real WebSocket client so CoS can swap between any backend via env var with zero changes to `chief_of_staff.py` (the coordination layer).

**Runtime decision:** Docker container — isolated from personal OpenClaw #1 (port 18789, `~/.openclaw/`), self-contained, portable.

This is dev-only. Prod wiring follows after dev is stable.

---

## What Changed from the Original Plan (2026-07-14 reviews)

Three fixes, all agreed across Claude.ai, Grok, and OpenClaw:

1. **`_maybe_update_memory` — vendor hardcoding removed.** The original had a hardcoded call to a specific external model API inside `openclaw_backend.py`. This undercuts the point of evaluating OpenClaw's own memory judgment, and violates the vendor-agnostic principle. Fixed: memory-worthiness judgment moves to the coordination layer, where it belongs — called back from any backend through the `write_memory` callable. See §Memory Judgment Architecture below.

2. **`_collect_response` — uncertainty made explicit, not buried in a comment.** The original had `# adjust if protocol sends further messages after this` — a known gap left for later. Fixed: this is now an explicit pre-build open question OpenClaw must answer at session start before `_collect_response` is written. See §What OpenClaw Needs to Supply.

3. **Spec version reference** — "spec #133 v1.2" in the A/B harness section corrected to **v1.3** throughout.

---

## Memory Judgment Architecture (Grok's recommendation, adopted)

**The problem with the original approach:** putting a model-specific API call inside each backend's `_maybe_update_memory` means every new backend has to re-implement memory judgment, and worse, has to hardcode a specific model to do it — creating hidden vendor dependencies inside supposedly vendor-agnostic backends.

**The correct pattern, per Grok's review:** memory-worthiness judgment belongs in the coordination layer (`chief_of_staff.py`), not in individual backends. The backend's job is to return a response; the coordination layer decides whether that response is worth remembering, using whatever model is configured for that role at the platform level. No backend should import or call an external model API directly.

**Concrete change to `openclaw_backend.py`:** `_maybe_update_memory` is removed from the backend entirely. Instead, `chief_of_staff.py`'s coordination layer — which already owns `_append_memory()` and `_maybe_update_memory()` — calls its own judgment function after receiving the response from `call_backend()`. The `write_memory` callable passed to the backend constructor is for direct writes only (e.g. confirmed decisions the backend itself identifies), not for the worthiness-check call.

**Phase 2 note:** once OpenClaw's native memory mechanism is better understood, the judgment call can optionally be routed through the active backend's own signal if it provides one. Phase 1 deliberately keeps judgment in the coordination layer so we can fairly compare backends on equal footing — neither backend has an advantage or disadvantage from its own memory logic during the evaluation.

---

## Division of Labor

| Agent | Responsibility |
|---|---|
| **OpenClaw** | Provides: (1) npm install command, (2) CLI start command, (3) minimal config schema, (4) `_collect_response` protocol answer — all at session start before build begins |
| **Claude Code** | Implements `openclaw_backend.py` WS client; moves memory judgment to coordination layer; wires `COS_BACKEND_TYPE` env-var switch into `init_backend()`; writes `Dockerfile.openclaw-cos`; adds compose service; writes A/B conformance harness |
| **Robert** | Starts session, verifies backend swap end-to-end in browser UI, approves push |

---

## Files to Create / Modify

| File | Action |
|---|---|
| `domains/cos/backends/openclaw_backend.py` | Implement (stub → real WS client, no vendor-specific memory call) |
| `domains/cos/chief_of_staff.py` | Modify `init_backend()` — add env-var switch; confirm memory judgment stays in coordination layer |
| `docker/Dockerfile.openclaw-cos` | Create — Node.js container for Gateway #2 (blocked on OpenClaw answers) |
| `docker/openclaw-cos-config/` | Create — config directory for Gateway #2 (blocked on OpenClaw schema) |
| `docker-compose.yml` | Add `openclaw-cos` service; add env vars to `cos` service |
| `scripts/cos_ab_test.py` | Create — A/B conformance harness |
| `docker/requirements.cos-agent.txt` | Add `websockets` package |

---

## Implementation Details

### 1. `init_backend()` switch (`chief_of_staff.py` ~line 660)

```python
def init_backend():
    global _backend
    backend_type = os.environ.get("COS_BACKEND_TYPE", "grok").lower()
    if backend_type == "openclaw":
        from domains.cos.backends.openclaw_backend import OpenClawBackend
        gateway_url = os.environ.get("COS_OPENCLAW_URL", "ws://localhost:18770")
        _backend = OpenClawBackend(
            write_memory=_append_memory,
            dispatch_tool=_dispatch_tool,
            gateway_url=gateway_url,
        )
    else:
        from domains.cos.backends.grok_backend import GrokBackend
        _backend = GrokBackend(write_memory=_append_memory, dispatch_tool=_dispatch_tool)
    log.info("Backend initialized: %s (%s)", _backend.backend_label, _backend.model_label)
```

No other changes to `chief_of_staff.py` — the coordination layer's own `_maybe_update_memory()` already handles memory judgment and is called after `call_backend()` returns, regardless of which backend is active.

### 2. `openclaw_backend.py` — WebSocket client

**Protocol confirmed by OpenClaw (2026-07-14):**
- Schema: `openclaw.inbound_meta.v2`
- Tool scope: per-message via `toolsAllow` array
- Auth: none required for localhost

**`_collect_response` — still requires OpenClaw's answer before implementation.** See §What OpenClaw Needs to Supply, question 4. Do not implement `_collect_response` with a guess — wait for the confirmed termination signal.

```python
import asyncio, json, logging
import websockets  # add to requirements.cos-agent.txt

log = logging.getLogger("cos.openclaw_backend")

_GATEWAY_PORT = 18770
_OBSERVATION_TOOLS = ["read", "exec", "web_search"]  # verify against live Gateway

class OpenClawBackend:
    backend_label = "OpenClaw"
    model_label   = "openclaw-gateway"

    def __init__(self, write_memory, dispatch_tool,
                 gateway_url=f"ws://localhost:{_GATEWAY_PORT}"):
        self._write_memory = write_memory
        self._dispatch_tool = dispatch_tool  # parity only; not invoked
        self._gateway_url = gateway_url

    def call_backend(self, prompt: str, context: dict, tool_policy: dict) -> str:
        return asyncio.run(self._call_async(prompt, context, tool_policy))

    async def _call_async(self, prompt, context, tool_policy) -> str:
        tools = _OBSERVATION_TOOLS if tool_policy.get("observation", True) else []
        payload = {
            "schema":      "openclaw.inbound_meta.v2",
            "channel":     "openclaw",
            "provider":    "openclaw",
            "surface":     "gateway",
            "chat_type":   "direct",
            "message":     prompt,
            "lightContext": True,
            "toolsAllow":  tools,
        }
        async with websockets.connect(self._gateway_url) as ws:
            await ws.send(json.dumps(payload))
            reply = await self._collect_response(ws)
        # Memory judgment is NOT done here — the coordination layer
        # (chief_of_staff.py) calls _maybe_update_memory() after call_backend()
        # returns, using its own configured model. No backend should make that
        # judgment call or import an external model API directly.
        return reply

    async def _collect_response(self, ws) -> str:
        # IMPLEMENTATION BLOCKED — awaiting OpenClaw's answer on:
        # (a) Does Gateway send additional messages after kind:"agentTurn"?
        # (b) Is "response" a reliable fallback key?
        # Do not implement with a guess. See §What OpenClaw Needs to Supply.
        raise NotImplementedError("_collect_response: awaiting OpenClaw protocol confirmation")
```

### 3. Docker — `Dockerfile.openclaw-cos`

**Blocked on OpenClaw answers (questions 1–3 below).** Template only:

```dockerfile
FROM node:22-slim
WORKDIR /app
# TODO: RUN npm install -g <openclaw-package-name>   ← OpenClaw to supply (Q1)
COPY docker/openclaw-cos-config/ /app/config/
EXPOSE 18770
# TODO: CMD ["<start-command>", "--port", "18770", "--config", "/app/config/"]  ← OpenClaw to supply (Q2)
```

### 4. `docker-compose.yml` changes

New service:
```yaml
openclaw-cos:
  build:
    context: .
    dockerfile: docker/Dockerfile.openclaw-cos
  container_name: minimoi-openclaw-cos
  ports:
    - "127.0.0.1:18770:18770"
  volumes:
    - ./docker/openclaw-cos-config/:/app/config/
  restart: unless-stopped
```

Add to existing `cos` service:
```yaml
environment:
  - COS_OPENCLAW_URL=ws://openclaw-cos:18770   # container-to-container
depends_on:
  - openclaw-cos
```

`COS_BACKEND_TYPE` unset = defaults to current primary backend. Set `COS_BACKEND_TYPE=openclaw` in `.env` to activate OpenClaw.

### 5. A/B conformance harness (`scripts/cos_ab_test.py`)

Per `config/cos_interface.md` v0.2 §Conformance Test (concrete shape) and spec #133 v1.3:

**Prompts** — CoS-flavored, not generic (per Grok's review: synthetic-but-specific prompts better catch real behavioral differences than abstract tests):
```
1. "What's currently blocking the career page build?"
2. "How is German domain performing — any signals from the loops?"
3. "Is there anything unusual in the build queue right now?"
4. "What EC2 health metrics should I be watching?"
5. "What's the most important thing I should be doing this week?"
```

**Swap point:** after prompt 3.
- Prompts 1–3 → current primary backend
- Prompts 4–5 → OpenClawBackend (in-process swap, no restart)

**Pass criteria (binary — any failure = boundary not clean):**
- Both halves complete without error
- `cos_memory.md` accumulates entries across both halves
- `tool_policy` respected: observation calls succeed, mutation refused, in both halves
- Swap requires zero special-casing in `chief_of_staff.py`

**Qualitative signals to watch** (per Grok's review — beyond the conformance pass/fail):
- Does OpenClaw's response quality differ from the primary backend's on the same prompt?
- Does `cos_memory.md` reflect genuinely different memory judgment between backends (what gets stored, what doesn't)?
- Does OpenClaw's memory judgment feel more or less conservative than the primary backend's — does it store more, store less, or store different kinds of things?
- Does OpenClaw maintain continuity correctly using only the context passed via `call_backend()`'s `context` dict, without needing any special state from the coordination layer?

---

## What OpenClaw Needs to Supply at Session Start

**All four answers required before build begins. `_collect_response` stays `NotImplementedError` until Q4 is answered.**

1. **npm install command** — exact package name and install command for the Gateway
2. **CLI start command** — exact syntax with `--port` and `--config` override flags
3. **Minimal config schema** — what fields are required for a CoS-scoped instance (observation-only tools, no personal context from OpenClaw #1); which tool names map to `read` / `exec` / `web_search`
4. **`_collect_response` protocol** — after a message with `kind: "agentTurn"`, does the Gateway ever send additional messages in the same exchange? Is `"response"` a reliable fallback key if `kind` isn't present?

---

## Out of Scope

- `/opt/minimoi/.env.cos` dedicated credential file (prod task)
- Whisper voice wiring (separate task)
- Guild CoS view (separate task)
- `agent_logs/` per-agent directory reorganization (separate task)
- Containerizing OpenClaw #2 for prod (after dev stable)
- OpenClaw native memory judgment call (Phase 2, after native mechanism understood)

---

## Verification Sequence

1. `docker compose up openclaw-cos` → logs confirm Gateway listening on 18770
2. Set `COS_BACKEND_TYPE=openclaw` in `.env`
3. `docker compose up cos` → logs: "Backend initialized: OpenClaw (openclaw-gateway)"
4. Open `/ui` in browser → send a message → response comes from OpenClaw #2
5. `python scripts/cos_ab_test.py` → PASS
6. Set `COS_BACKEND_TYPE` back to default → restart cos → same prompt → primary backend responds → **clean swap confirmed**
7. Check qualitative signals in `cos_memory.md` — see A/B harness §Qualitative signals above

---

## Commit

| Item | Location | Actor |
|---|---|---|
| This plan | `docs/specs/plan_openclaw2_dev_setup_2026-07-14.md` | Claude Code |
| Build queue entry | New item, spec_ready, linked to #133 v1.3 Task 9 | Claude Code |

Registration and build approval: Robert. Build does not start until OpenClaw supplies all four answers above.

---

*Plan: OpenClaw #2 Dev Setup · 2026-07-14 · Claude.ai (Fable 5)*
*Reviews: Grok 2026-07-14 · OpenClaw 2026-07-14 · Claude.ai 2026-07-14 — all three agree*
*Three fixes from review incorporated: no vendor hardcoding, memory judgment in coordination layer, spec version corrected*
