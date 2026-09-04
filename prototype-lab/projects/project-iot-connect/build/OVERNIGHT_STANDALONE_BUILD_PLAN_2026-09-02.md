# Connect HQ Standalone Beta — Overnight Candidate Build Plan

**Version:** 0.1  
**Date:** 2026-09-02  
**Builder:** Claude Code  
**Verifier:** Codex  
**Decision owner:** Robert  
**Authority:** Isolated candidate only; no repository import or Git action

## Morning outcome

Provide Robert one reviewable candidate for the Connect HQ v0.9 standalone beta:

- a complete sanitized project tree;
- an actual baseline-to-candidate diff;
- a runtime and dependency inventory;
- test and verification output;
- commands for start, stop, reset, status, smoke, and full verification;
- a list of anything not completed or not proven; and
- Codex's independent P0/P1/P2 review.

No fiber feature is built overnight.

## Workspace boundary

Claude Code works only in the sanitized workspace supplied by Codex. It must not
read or write outside that workspace. The workspace contains:

- application source and static assets;
- contracts and the current synthetic-integration service;
- tests, scripts, SQL, synthetic fixtures, and safe Postman definitions;
- commit-safe Markdown documentation and presentation source;
- the current program plan and approved overnight disposition; and
- no `.env`, virtual environment, cache, runtime database, generated deck/output,
  Robert Comments, private role material, personal checklist, or unrelated
  mini-moi data/domain content.

## Stage 1 — Audit before modification

Record in `build-evidence/CLAUDE_CODE_IMPLEMENTATION_REPORT.md`:

1. actual repository adapter used by each start path;
2. existing process/container topology and ports;
3. Python, OS, and service dependencies;
4. reset and seed behavior;
5. persistence and restart behavior for activation/integration outcomes;
6. hardcoded absolute paths, dated asset paths, and user-visible Nightjar names;
7. baseline tests that pass or fail before modification; and
8. exact file-level plan actually followed.

Do not conceal baseline failures by changing tests before recording them.

## Stage 2 — Produce the standalone candidate

Work in the staged `build/project-connect-hq/` tree:

1. retain the source behavior and existing IoT domain rules;
2. add a minimal app `Dockerfile` and root Compose definition;
3. containerize the existing integration-stub HTTP boundary;
4. use PostgreSQL as the demonstrated persistent backend and document the status
   of memory/Snowflake adapters without redesigning them;
5. add health checks and dependency ordering;
6. add portable configuration and an obviously synthetic `.env.example`;
7. add one command each for `up`, `down`, `reset`, `status`, `smoke`, and `verify`;
8. make reset transactional, deterministic, and idempotent;
9. preserve ports 8095 and 8096 unless a verified constraint prevents it;
10. remove absolute laptop and dated presentation dependencies;
11. normalize user-visible branding to Connect HQ while preserving historical
    provenance in technical history where useful;
12. persist demonstrated activation/integration results needed after restart;
13. update Postman and SQL evidence to match the containerized URLs and schema;
14. add or adjust tests only to prove the specified packaging and reliability
    behavior; and
15. keep the original sanitized baseline unchanged for comparison.

## Stage 3 — Verification

Run and capture, as applicable:

- baseline unit/integration tests before changes;
- candidate unit and integration tests;
- Compose configuration validation and image build;
- cold start and health checks;
- deterministic reset twice with comparable evidence output;
- app-container restart with persistence verification;
- IoT activation success;
- IoT activation failure and rollback/reconciliation;
- detailed versus summarized billing behavior;
- Swagger on 8095 and integration Swagger on 8096;
- saved Postman evidence or an equivalent deterministic API runner;
- SQL/UI/API consistency checks;
- absolute-path, private-identity, PAN-like, and secret-pattern scans; and
- `down` followed by `up` with the documented persistence result.

If Docker or a host dependency prevents a test, record `NOT PROVEN` with the
exact command and failure. Never report “passed” from code inspection alone.

## Stage 4 — Builder handoff

Claude Code leaves, inside the isolated workspace:

- the complete candidate tree;
- `build-evidence/CLAUDE_CODE_IMPLEMENTATION_REPORT.md`;
- `build-evidence/TEST_RESULTS.txt` or equivalent raw capture;
- `build-evidence/BASELINE_TO_CANDIDATE.diff`;
- `build-evidence/FILES_ADDED_CHANGED.txt`;
- `build-evidence/KNOWN_GAPS.md`; and
- no commit, branch, tag, push, PR, issue, or source-tree deletion.

## Codex verification

After Claude Code stops editing, Codex will:

1. inspect the actual diff rather than relying on the implementation report;
2. check for scope creep, private material, credentials, machine paths, and
   unnecessary architecture changes;
3. run the highest-value tests independently;
4. compare UI/API/SQL behavior with the baseline where feasible;
5. assess restart, reset, idempotency, and persistence claims;
6. rank findings P0/P1/P2; and
7. recommend `reject`, `revise`, or `ready for Robert review`.

## Explicitly out of scope tonight

- Fiber entities, APIs, UI, simulators, or contracts.
- Salesforce, Zuora, Adyen, Stripe, or live-vendor access.
- AWS or other cloud deployment.
- Shared-framework extraction for Guild or mini-moi.
- Root repository documentation or configuration changes.
- Git commit, push, tag, PR, issue, merge, or source retirement.
