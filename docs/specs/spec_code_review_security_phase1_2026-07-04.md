# Spec: Code Review & Security Audit — Phase 1 (Pre-External Review)
**File:** `spec_code_review_security_phase1_2026-07-04.md`
**Status:** Backlog
**Date:** 2026-07-04
**Supersedes:** `spec_code_review_phase0_2026-06-19.md` (Phase 0 done — hardcoded paths and secrets resolved)
**Author:** Claude.ai design session

---

## Intent

Phase 0 resolved the most urgent issues: hardcoded paths, exposed secrets, and plaintext credentials in compose files. Phase 1 prepares mini-moi for external review by the T-Mobile AI team. The goal is not perfection — it is confident, honest presentation. Every known issue should be found by us first, mitigated or documented, before external eyes see the code.

This spec covers two parallel tracks: code quality review and security audit. Both must complete before any external review invitation goes out.

---

## Phase 0 — What was done (reference)

- Hardcoded paths removed
- Secrets moved to SSM + `.env` on host
- Postgres credentials rotated and moved to SSM
- Flask debug mode confirmed off
- All portal routes audited for auth
- Admin routes confirmed owner-only
- Guest scope confirmed read-only
- `.gitignore` covers `.env`, memory files, sensitive dirs

---

## Track 1: Code Quality Review

### Scope

**Architecture consistency**
- German and Portuguese domain servers: confirm identical structure (routes, error handling, logging pattern)
- Portal: confirm no domain-specific logic leaking into portal layer
- Telegram bot: confirm system-bot is a thin caller, not carrying domain logic

**Dead code and cruft**
- Identify files in repo that are no longer referenced
- Identify commented-out code blocks older than 30 days
- Identify duplicate files (copy/backup/v2 variants in production paths)

**Error handling**
- Confirm all Flask routes have try/except with meaningful error responses
- Confirm no bare `except:` blocks that swallow errors silently
- Confirm all external API calls (LLM, DeepL, Telegram) have timeout and retry handling

**Logging**
- Confirm consistent logging pattern across all domain servers
- No sensitive data (passwords, tokens, user content) in logs
- Confirm log rotation is configured

**Configuration**
- No model names hardcoded in domain functions (architecture principle)
- No port numbers hardcoded outside config/compose files
- No file paths hardcoded outside config

### Deliverable
Claude Code produces a findings report: `docs/design/code_review_findings_2026-07.md`. Each finding: file, line range, severity (high/medium/low), recommended fix. Robert reviews and approves fixes before they're applied.

---

## Track 2: Security Audit

### Scope

**Authentication and authorization**
- Full route inventory across all Flask apps (portal, german, portuguese, curator)
- Classify each route: public / login-required / owner-only / guest-scoped
- Confirm no routes are accidentally public that should be protected
- Confirm guest data isolation via X-Minimoi-Username header is enforced consistently

**Secrets and credentials**
- Full grep across repo for any remaining credential patterns: `password`, `token`, `api_key`, `secret`, `DATABASE_URL`, connection strings
- Confirm `.gitignore` covers all sensitive paths
- Confirm SSM parameter naming is consistent (`/minimoi/production/`)
- Confirm no credentials in any committed file — public or private repo

**Input validation**
- Confirm all user-supplied input is validated before use
- Confirm no SQL injection surface (parameterized queries only)
- Confirm no path traversal surface (user input not used in file paths)

**Dependencies**
- Run `pip audit` or equivalent against `requirements.txt`
- Flag any packages with known CVEs
- Flag any packages pinned to versions older than 12 months

**Network exposure**
- Confirm EC2 security group allows only necessary ports (80, 443, SSH from known IPs)
- Confirm internal container communication is on Docker network only (not exposed ports)
- Confirm no admin endpoints reachable without auth from public internet

### Deliverable
Claude Code produces a security findings report: `docs/design/security_audit_findings_2026-07.md`. Each finding: severity (critical/high/medium/low), description, recommended fix. All critical and high findings must be resolved before external review. Medium/low findings documented with mitigation plan.

---

## Pre-External Review Checklist

Before inviting T-Mobile AI team:

- [ ] Track 1 findings report produced and reviewed
- [ ] Track 2 findings report produced and reviewed
- [ ] All critical and high security findings resolved
- [ ] README rewritten for external audience (Effort 2 — separate spec)
- [ ] Old docs and superseded specs archived
- [ ] GitHub issues cleaned up (closed issues closed, stale issues triaged)
- [ ] Robert has reviewed the repo as if seeing it for the first time

---

## Definition of Done

- [ ] Both findings reports committed to `docs/design/`
- [ ] All critical/high findings resolved with commits referencing the finding
- [ ] Robert signs off: "I am comfortable with an AI engineer seeing this repo"
- [ ] External review invitation sent

---

## Commit

Two PRs on `dev` branch — one per track. Claude Code builds findings reports first, Robert reviews, fixes applied in separate commits. No ECR push needed for report-only phase; ECR push required for any fixes that touch running services.

---

## Notes for Grok review

- Track 1 and Track 2 can run in parallel — Claude Code does both investigations simultaneously
- Findings reports are the gate, not the fixes — Robert decides what gets fixed vs documented
- This spec intentionally does not define what "good" looks like in detail — findings report surfaces that
- External review posture: honest about what's a personal platform vs production system; confident about architecture decisions

---

*Spec · 2026-07-04 · Claude.ai design session · Status: Backlog*
