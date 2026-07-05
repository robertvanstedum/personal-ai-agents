# Spec: Domain Standardization — German & Portuguese as Template
**File:** `spec_domain_standardization_2026-07-04.md`
**Status:** Spec Ready
**Date:** 2026-07-04
**Build queue:** #120
**Author:** Claude.ai design session
**Grok review:** Pending

---

## Intent

mini-moi is a growing platform. Three domains are live today (Curator, Mein Deutsch, Meu Português). More will follow. Each new domain must be buildable by reuse and extension — not from scratch. Today, German and Portuguese were built at different times with different patterns. This spec standardizes both domains against a shared template, so that template becomes the foundation every future domain inherits.

This is not a bug-fix spec. It is an architecture spec. The output is not just fixed domains — it is a documented, tested, reusable domain pattern that any actor (Robert, Claude Code, OpenClaw) can use to spin up a new domain with confidence.

---

## Architecture Principles (locked)

These principles govern all domains, now and future:

1. **JSON-first** — JSON files are the source of truth. Postgres is a rebuildable projection added only when query complexity demands it. Never migrate off JSON; add Postgres alongside it.
2. **Per-user data isolation** — all data reads and writes use the authenticated user ID from the request, never a hardcoded default.
3. **Domain server owns its data** — no external process (Telegram bot, portal, another domain) reads domain data files directly. All access goes through the domain server's HTTP endpoints.
4. **Model names never hardcoded** — LLM model names live in config, never in domain functions.
5. **No credentials in git** — ever, public or private. SSM + `.env` on host.
6. **Dev-first always** — test on dev.minimoi.ai before any ECR push. Hotfix process requires explicit approval.

---

## The Domain Template

Every mini-moi domain must conform to this structure. German and Portuguese are the reference implementations. This template is the target state after this spec ships.

### Directory structure
```
domains/<domain>/
  <domain>_server.py      — Flask app, all HTTP endpoints
  <domain>_domain.py      — domain logic, no Flask
  data/
    config/               — JSON config files (sources, personas, filters)
    <feature>/            — per-user JSON data files (user_{id}.json)
  templates/              — Jinja2 HTML templates
```

### Process management
- **Mac dev:** launchd plist in `~/Library/LaunchAgents/` — auto-restart on crash
- **EC2 prod:** Docker container in `docker-compose.prod.yml` — managed by compose

### HTTP endpoint pattern
All domain endpoints follow this pattern:
- Auth: user ID extracted from request via `_request_user_id()` — never hardcoded
- Data: read/write via domain logic functions — never direct file access in route handlers
- Errors: try/except on all routes, meaningful HTTP status codes
- Tips: `_load_tip('domain.slot')` wired to all page slots

### Per-user data isolation
```python
def _request_user_id(request):
    # Reads from auth header set by portal proxy
    # Returns authenticated user ID
    # Never falls back to hardcoded default
```

### Telegram bot pattern
The Telegram bot is a thin HTTP caller. It calls domain server endpoints. It does not:
- Import from domain modules directly
- Read or write domain data files
- Share a data volume with the domain container

### Tips system
All page slots wired to `config/curator/tips.json` via `_load_tip()`. Slot keys follow `domain.page` convention. Registry maintained in `docs/design/tips_slot_registry.md`.

---

## Current State vs Template — Gap Analysis

### German domain gaps

| Gap | Severity | Fix |
|---|---|---|
| Persona memory uses `DEFAULT_USER` hardcoded — not per-request user ID | **High** — data isolation bug, breaks multi-user | Fix `_request_user_id()` pattern in persona memory reads/writes |
| Bot imports ~20 functions from `german_domain.py` directly | **High** — domain server doesn't own its data | Bot refactor: expose HTTP endpoints, bot becomes thin caller (closes GitHub #54) |
| No launchd plist on Mac dev | **Medium** — manual restart required on crash | Add `minimoi.german.plist` to `~/Library/LaunchAgents/` |
| Gespräche sessions in JSON only | **Low** — JSON-first is correct | Document as intentional; add Postgres projection only if query needs arise |
| Bot reads German JSON files directly | **High** — violates domain-owns-its-data principle | Resolved by bot refactor above |

### Portuguese domain gaps

| Gap | Severity | Fix |
|---|---|---|
| Escrita (writing sessions) still JSON-only | **Low** — JSON-first is correct | Document as intentional |
| No launchd plist on Mac dev | **Medium** — manual restart required on crash | Add `minimoi.portuguese.plist` to `~/Library/LaunchAgents/` |
| Writing session user ID falls back to "anonymous" | **Medium** — inconsistent isolation | Fix to match German/portal pattern |

### Both domains

| Gap | Severity | Fix |
|---|---|---|
| Tips system wired but slot keys unverified against server | **Medium** — tips may silently not render | Verify all 14 slot keys match server-side `_load_tip()` calls |

---

## Build Phases

Sequenced to avoid rework. Each phase is independently deployable.

### Phase 1 — Bot refactor (gates Phase 2)
**What:** Decouple Telegram bot from German domain. Expose HTTP endpoints on `mein-deutsch` container. Bot becomes a thin HTTP caller.

**Why first:** Bot currently reads German JSON directly. Storage or isolation changes in Phase 2 would break the bot if it's still entangled. Closes GitHub #54.

**Endpoints to expose (German):**
- `POST /api/drill-session` — start a drill session
- `POST /api/transcript` — submit a transcript for processing
- `GET /api/session-status` — get current session state
- `POST /api/round-action` — submit a round result

**Definition of Done:**
- [ ] All bot German logic removed from `telegram_bot.py` — replaced with HTTP calls
- [ ] `telegram_system_bot.py` has no imports from `german_domain.py`
- [ ] Bot behavior unchanged from user perspective
- [ ] Tested on dev — Telegram drill flow works end to end
- [ ] GitHub #54 closed

### Phase 2 — Per-user isolation fix
**What:** Fix German persona memory to use per-request user ID. Fix Portuguese writing session user ID fallback.

**Why:** Data isolation bug. In any multi-user scenario (Vera, daughters, guests), German persona memory is shared across users. This is incorrect.

**German fix:**
```python
# Before
persona_data = load_persona_memory(DEFAULT_USER)

# After  
user_id = _request_user_id(request)
persona_data = load_persona_memory(user_id)
```

**Portuguese fix:**
```python
# Before
user_id = _request_user_id(request) or "anonymous"

# After
user_id = _request_user_id(request)  # raise 401 if missing
```

**Definition of Done:**
- [ ] German persona memory reads/writes use per-request user ID
- [ ] Portuguese writing sessions use per-request user ID, no "anonymous" fallback
- [ ] Tested on dev with owner account and a guest account
- [ ] No data cross-contamination between users

### Phase 3 — Process management
**What:** Add launchd plists for German and Portuguese on Mac dev.

**Why:** Both domain servers require manual restart on crash. Dev environment should match prod reliability pattern (auto-restart).

**Deliverables:**
- `~/Library/LaunchAgents/minimoi.german.plist`
- `~/Library/LaunchAgents/minimoi.portuguese.plist`
- Both installed and tested — confirm auto-restart after manual kill

**Definition of Done:**
- [ ] German server restarts automatically after `kill`
- [ ] Portuguese server restarts automatically after `kill`
- [ ] Plists committed to repo under `scripts/launchd/`

### Phase 4 — Tips verification
**What:** Verify all 14 German and Portuguese tip slot keys are wired server-side.

**Why:** Tips were designed in Claude.ai and committed to `tips.json`, but server-side `_load_tip()` calls for new domains may not exist yet.

**Definition of Done:**
- [ ] All 7 German slots confirmed wired in `german_server.py`
- [ ] All 7 Portuguese slots confirmed wired in `portuguese_server.py`
- [ ] Tips render correctly on dev for all tabs in both domains
- [ ] `tips_slot_registry.md` updated with confirmed status

---

## The Domain Template Document

**Deliverable of this spec (not a phase — done alongside Phase 1):**

Claude Code produces `docs/DOMAIN_TEMPLATE.md` — the canonical reference for building new domains. Contents:
- Directory structure (copy-paste ready)
- `_request_user_id()` pattern (copy-paste ready)
- launchd plist template (fill in domain name and port)
- Docker compose service block template
- Tips slot wiring pattern
- HTTP endpoint auth pattern
- Checklist: "my new domain is template-compliant when..."

This document is what Robert, Claude Code, or OpenClaw reads before starting a new domain. It is a living document — updated when the template evolves.

---

## Definition of Done (full spec)

- [ ] Phase 1 complete — bot refactor, GitHub #54 closed
- [ ] Phase 2 complete — per-user isolation correct on both domains
- [ ] Phase 3 complete — launchd plists for German and Portuguese on dev
- [ ] Phase 4 complete — all tips slots verified and rendering
- [ ] `docs/DOMAIN_TEMPLATE.md` written and committed
- [ ] Both domains pass a manual smoke test: owner account + guest account, all tabs, all features
- [ ] No regressions on prod after each phase deploys

---

## Commit

Four PRs on `dev` branch — one per phase. Each merged and deployed to prod before next phase begins. Claude Code proposes exact changes for Robert's approval before each ECR push.

Phase commit messages:
```
feat(german): bot refactor — expose HTTP endpoints, decouple from domain (#54) [Phase 1]
fix(german,portuguese): per-user isolation — persona memory + writing sessions [Phase 2]
feat(dev): launchd plists for german and portuguese domain servers [Phase 3]
feat(tips): verify and wire all german/portuguese slot keys [Phase 4]
```

---

## Notes for Grok Review

- JSON-first principle means no storage migration — confirm this is correctly applied throughout
- Phase 1 (bot refactor) is the riskiest phase — Telegram drill flow must be tested end-to-end before prod deploy
- `DOMAIN_TEMPLATE.md` is a high-value deliverable — worth a separate Grok review pass before it's used for a new domain
- Per-user isolation fix (Phase 2) should be tested with at least two simultaneous user sessions before prod
- Future domains: Career, OpenClaw CoS — both should inherit this template without modification

---

*Spec · 2026-07-04 · Claude.ai design session · Status: Spec Ready*
*Feeds: docs/DOMAIN_TEMPLATE.md (new), closes GitHub #54*
