# mini-moi Code Quality Review Plan
*Created: 2026-06-19 — Claude.ai*
*Location: docs/CODE_REVIEW_PLAN.md*
*Status: Active roadmap — runs parallel to AWS migration phases*

---

## Why this exists

Moving to AWS production is the right moment to do a deliberate code
quality review. The migration will expose every hardcoded path, every
leaked credential, every error handler that swallows failures silently.
Better to find them deliberately before the migration than have them
surface as production failures after.

This is also a demonstration of how AI is used for code verification
in mini-moi — not to write code blindly, but to systematically review
it for security, performance, and correctness before a significant
infrastructure change. Each review pass is documented. Significant
findings produce Decision Records.

**This is not a "scan everything at once" exercise.** Broad reviews
produce shallow feedback. Each pass is focused, tied to a specific
migration phase, and produces actionable findings — not a generic
list of suggestions.

---

## Review areas and phase alignment

| Review Area | Priority | AWS Phase | Why it matters then |
|-------------|----------|-----------|-------------------|
| Hardcoded paths + config | Critical | Phase 0 (Containerization) | Docker containers can't use local paths — must be found before first build |
| Secrets + credential handling | Critical | Phase 0-1 | Security before anything goes to AWS |
| Error handling + logging | High | Phase 1-2 | Needed for monitoring to work correctly |
| Auth flows + input validation | High | Phase 2 | Production security before DNS cutover |
| Performance hot spots | High | Phase 3-4 | Analysis jobs + Curator loop — expensive in the cloud |
| Dead code + unused functions | Medium | Phase 4 | Clean codebase before data layer migration |
| Dependency audit | Medium | Phase 4 | Pin versions, remove unused packages |

---

## What each review covers

### Phase 0 — Hardcoded paths + secrets

**Hardcoded paths:**
- Every `open()` call with a non-relative path
- Any reference to `/Users/vanstedum/`, `~/Projects/`, or similar
- Paths not driven by environment variables
- File writes to locations that won't exist in a container

**Secrets + credentials:**
- API keys in code (not env vars)
- Keys in config files that get committed
- Secrets in log output
- `.env` in `.gitignore` confirmed
- macOS Keychain references that won't work on Linux/EC2

**Output:** List of every violation with file, line, and fix.
These are blocking issues — must be resolved before Phase 0 completes.

---

### Phase 1-2 — Error handling + auth

**Error handling:**
- Bare `except:` or `except Exception:` that swallows errors silently
- Missing error responses on API routes (returns 200 with error in body)
- Background threads with no exception handling
- Missing timeouts on external API calls (LLM, OpenAI TTS, Whisper)

**Auth flows:**
- Routes that should require login but don't
- Session handling correctness
- WebSocket connection auth (covered in WebSocket spec)
- Admin routes accessible without admin role

**Output:** Findings with severity (blocking / high / medium).
Blocking findings fixed before Phase 2 DNS cutover.

---

### Phase 3-4 — Performance + dead code

**Performance:**
- Analysis pipeline — where is the time going? (already instrumented
  in WebSocket spec, review the actual numbers)
- Curator scoring loop — any N+1 patterns, unnecessary re-processing
- Database queries — missing indexes, queries in loops
- S3 calls — batching where possible vs. one call per file

**Dead code:**
- Functions defined but never called
- Routes that return 404 or are commented out
- Commented-out code blocks that should be deleted
- Feature flags or conditions that can never be true

**Dependency audit:**
- `pip list --outdated` — packages with security patches
- Packages imported but unused
- Pin all versions in `requirements.txt` for reproducible builds

**Output:** Performance findings with before/after estimates.
Dead code list. Updated `requirements.txt` with pinned versions.

---

## How findings are documented

**Minor findings** (style, cleanup, low risk): fixed silently in the
relevant commit, noted in the commit message.

**Significant findings** (security issues, performance patterns,
architectural problems): produce a short Decision Record.
- What was found
- Why it matters
- What was done about it
- Whether it reveals a pattern worth watching

**Blocking findings** (must fix before proceeding to next phase):
logged as GitHub issues, linked to the relevant AWS phase.

---

## How AI is used in these reviews

Each review pass runs with Claude Code or Grok reading the relevant
files with specific instructions — not "review everything" but
"find every hardcoded path in these three apps" or "identify all
routes missing authentication."

The reviewer reads actual code with full file context, not a chat
interface where code is pasted in pieces. Cursor (AI-assisted editor)
is the recommended tool for review passes — it sees the full codebase
without manual pasting.

Findings are verified by a human before any fix is made. AI surfaces
candidates; Robert or Claude Code confirms and fixes.

---

## Relationship to AWS migration

The code review runs parallel to AWS phases — it's not a blocker,
it's a companion. Phase 0 containerization work and Phase 0 code
review inform each other. Finding a hardcoded path during review
is one less surprise during the Docker build.

The review does not slow down the migration. If a finding is
significant enough to block a phase, it becomes a GitHub issue
with the phase label. Everything else is addressed in the natural
course of the migration work.

---

## Roadmap status

| Review pass | Aligned with | Status |
|-------------|-------------|--------|
| Hardcoded paths + secrets | AWS Phase 0 | target — start when Phase 0 begins |
| Error handling + auth | AWS Phase 1-2 | target |
| Performance + dead code | AWS Phase 3-4 | target |

---

*Code Review Plan · 2026-06-19 · Claude.ai*
*Spec: `spec_code_review_phase0_2026-06-19.md` — in build queue*
