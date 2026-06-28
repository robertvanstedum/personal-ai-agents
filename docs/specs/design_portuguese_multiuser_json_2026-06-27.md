# Design: Portuguese Multiuser — Per-User JSON Pattern
*2026-06-27 · Claude Code + Claude.ai*
*Status: approved design, pending build*

---

## Problem

Two user-isolation gaps in the Portuguese domain block inviting a second user (daughter):

| Feature | Current storage | Gap |
|---|---|---|
| Escrita (writing sessions) | Single shared `writing_sessions.json` | All users share one file — no isolation |
| Conversas (voice sessions) | Postgres-primary (`portuguese.sessions`) | EC2 Postgres schema never migrated; saves silently failing on production |

---

## Decision: Per-User JSON Files

**JSON is source of truth. Postgres is a rebuildable projection.**

The correct fix for shared-file isolation is not Postgres — it is per-user JSON files. This is what German already does for phrasebook and lesen drills, and it keeps the architecture consistent.

```
data/portuguese/
  writing_sessions/
    user_1.json          ← auth.users.id = 1 (Robert)
    user_2.json          ← auth.users.id = 2 (Vera, future)
    user_anonymous.json  ← unauthenticated (dev/testing only)
  conversas_sessions/
    user_1.json
    user_2.json
    user_anonymous.json
```

### Key is `auth.users.id` — not name or email

The file key is the **SERIAL integer primary key** from `auth.users.id`. This is:
- **Unique**: enforced by the database, no collision possible
- **Stable**: never changes even if user updates email or display name
- **Already available**: `_request_user_id()` reads `X-Minimoi-Auth-Id` header set by the portal proxy for every authenticated request

`user_anonymous` is a sentinel for unauthenticated requests only — used in local dev when no auth header is present. In production all users are authenticated before reaching the Portuguese domain.

---

## Architecture

### Write path

1. JSON write to `data/portuguese/{feature}/user_{id}.json` — **always happens first**
2. Postgres write (best-effort) — attempted after, non-blocking, failures logged but do not fail the request
3. JSON is what the UI reads back for display

### Read path

All reads from JSON per-user file. Postgres is never the source for user-facing reads in this design.

### File structure (both features, same pattern)

```json
{
  "entries": [
    {
      "id": "pt_ws_20260627_143022",
      "user_id": 1,
      "mode": "diario",
      "text_original": "...",
      "text_corrected": "...",
      "notes": [],
      "timestamp": "2026-06-27T14:30:22.123456"
    }
  ]
}
```

Cap: last 50 writing entries, last 100 conversas sessions per user file.

---

## Scope of Change

### Escrita writing sessions (fix isolation gap)

**File:** `domains/portuguese/html_server.py`

Replace:
- `_PT_WRITING_SESSIONS_FILE` constant (single shared path)

With:
```python
def _writing_sessions_path(user_id) -> Path:
    key = str(user_id) if user_id is not None else "anonymous"
    return BASE_DIR / "data" / "writing_sessions" / f"user_{key}.json"
```

Update `_pt_save_writing_entry(user_id, ...)` and `_pt_get_writing_sessions(user_id, ...)` to scope to per-user file.

Routes affected: `/api/pt/escrita/save`, `/escrita`, `/arquivo`

---

### Conversas sessions (move from Postgres-primary to JSON-primary)

**File:** `domains/portuguese/html_server.py`

Add:
```python
def _conversas_sessions_path(user_id) -> Path:
    key = str(user_id) if user_id is not None else "anonymous"
    return BASE_DIR / "data" / "conversas_sessions" / f"user_{key}.json"

def _pt_save_conversas_session(user_id, session_data: dict): ...
def _pt_get_conversas_sessions(user_id, limit=10) -> list: ...
```

`/api/pt/review` writes JSON first, then attempts Postgres (`_save_session`) as projection.
`/api/pt/sessions` reads from JSON, not Postgres.

Routes affected: `/api/pt/review`, `/api/pt/sessions`, `/conversas`, `/arquivo`

**Postgres helpers (`_save_session`, `_get_sessions`) are kept** — called as best-effort projection writes. They can be used later to rebuild Postgres from JSON if needed.

---

## What Doesn't Change

| Feature | Storage | Status |
|---|---|---|
| Vocabulário (Palavras) | Postgres `portuguese.vocabulary` with `user_id` | Already correct, no change |
| Leitura notes | Postgres `portuguese.leitura_notes` with `user_id` | Already correct, no change |
| Translation cache | Postgres `portuguese.translation_cache` (global) | Intentionally shared |
| Persona progress | Postgres `portuguese.persona_progress` with `user_id` | Already correct |
| Auth / user_id flow | Portal → `X-Minimoi-Auth-Id` header → `_request_user_id()` | Already works |

---

## Volume Mount

Existing mount covers both new subdirectories automatically:

```yaml
# docker-compose.prod.yml (already in place)
volumes:
  - /opt/minimoi/data/portuguese:/app/domains/portuguese/data
```

`data/writing_sessions/` and `data/conversas_sessions/` fall under this mount — all per-user files persist across deploys and container restarts. **No mount change needed.**

---

## One-Time EC2 Setup (after deploy)

```bash
aws ssm send-command \
  --instance-ids i-0d13db821169627e2 \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=[
    "mkdir -p /opt/minimoi/data/portuguese/writing_sessions",
    "mkdir -p /opt/minimoi/data/portuguese/conversas_sessions"
  ]'
```

---

## Definition of Done

- [ ] `data/writing_sessions/user_{id}.json` created on first Escrita save per user
- [ ] `/escrita` and `/arquivo` Escrita tab show only current user's entries
- [ ] `data/conversas_sessions/user_{id}.json` created after first Analisar per user
- [ ] `/arquivo` Conversas tab shows only current user's sessions
- [ ] Two different user_ids have separate, non-overlapping Arquivo views
- [ ] Unauthenticated request goes to `user_anonymous.json` (no error)
- [ ] EC2 volume mount confirmed: files persist after container restart
- [ ] Postgres projection write still attempted (logged on failure, does not block save)

---

## Why Not Just Add user_id to the Shared File?

A single `writing_sessions.json` with user_id per entry would work for reads (filter by user_id) but creates a concurrency write hazard: two users saving simultaneously both read the same file, one clobbers the other's write. Per-user files eliminate this entirely — each user's file is only ever written by one user at a time.

---

*Design session: Claude.ai (architecture) + Claude Code (exploration + spec) · 2026-06-27*
