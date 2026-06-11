# Challenger Pattern — Build Plan
*Claude Code · 2026-06-10*
*Spec: `_working/spec_challenger_pattern_2026-06-10.md`*

---

## Pre-build findings (codebase audit)

| Finding | Impact |
|---|---|
| `generate_dive.py` already has a two-agent pattern — but Anthropic-only (claude-opus-4-5 for both) | Phase 1 replaces this with cross-provider. No new concept, new wiring. |
| `claude-sonnet-4-6` confirmed valid via API test | Use as-is |
| Anthropic key in keyring (`anthropic / api_key`) ✅ | No setup needed |
| xAI key in keyring (`xai / api_key`) ✅ | No setup needed |
| Spec JSON block duplicated (lines 76-134) | First block is canonical — has `challenger_prompt`/`final_review_prompt` keys |
| No `domains/guild/services/` directory yet | Create it |

---

## Architecture decision: shared service, not per-domain code

The `ChallengerService` is a single class imported by every domain. Config drives all
domain-specific behaviour. No domain writes its own challenge loop.

```
domains/guild/services/challenger.py   ← one service, all domains import this
domains/guild/config/challenger_config.json  ← all prompts and domain flags
guild.challenger_exchanges             ← one table, all domains write here
```

Round structure per the spec (unchanged):
```
Round 1 — Primary synthesis     (caller's responsibility — passed in as first_pass)
Round 2 — Challenge pass        (ChallengerService: Grok via xAI)
Round 3 — Final review          (ChallengerService: Claude via Anthropic)
```

---

## Phase 1 — Foundation (build now, shared by all domains)

**Files created:**
- `domains/guild/db/schema_challenger.sql` — `guild.challenger_exchanges` table + robert_ro grant
- `domains/guild/config/challenger_config.json` — all domain flags + versioned prompts
- `domains/guild/services/__init__.py` — empty
- `domains/guild/services/challenger.py` — `ChallengerService` class
- `domains/guild/services/test_challenger.py` — standalone end-to-end test harness
- `domains/guild/db/test_schema.py` — updated to include challenger_exchanges

**DB schema rule:** `robert_ro` grant in the same migration file (per DB_SCHEMA.md pattern).

**Test harness:** runs a full exchange on a fixed canned input — no Curator/German/Guild
integration needed. Proves cross-provider round-trip works before touching any domain.

**Gate to Phase 2:** `test_challenger.py` produces a valid exchange with accepted_count ≥ 1.

---

## Phase 2 — Curator integration

**Files changed:**
- `_NewDomains/research-intelligence/scripts/generate_dive.py` — replace existing
  Anthropic-only two-agent pattern with `ChallengerService`. Preserves existing output
  format; adds challenge digest to the essay when `show_process: true`.
- Portal template — expandable "Challenger review" section (collapsed by default).

**Regression:** existing Dive output should be unchanged when `enabled: false`.

**Gate to Phase 2 release:** 5 test Dives reviewed in transparent mode; 4/5 pass
the acceptance criteria in the spec's pre-release test protocol.

---

## Phase 3 — German writing correction

**Files changed:**
- `domains/german/` writing correction endpoint — wire ChallengerService after the
  initial correction call. Add `nuance_note` to correction response.

**Not wired into:** Gespräche, Lesen, Wörter (real-time or latency-sensitive).

---

## Phase 4 — Guild career assessment

**Files changed:**
- `domains/guild/agents/loops/cos_job_search.py` — wire ChallengerService into
  `_evaluate_pass()` before `_notify_telegram()`. Silent mode default.

---

## ChallengerService API (Phase 1 contract)

```python
from domains.guild.services.challenger import ChallengerService

svc = ChallengerService()   # loads config

result = svc.run(
    domain="curator_deep_dive",
    feature="deeper_dive",
    first_pass="...the primary synthesis text...",
    context={
        "topic_name": "strait-of-hormuz",
        "related_threads": ["iran-nuclear", "gulf-shipping"],
        "sources_summary": "...",
        "entity_id": 42,           # research.topics id (optional)
        "entity_description": "Strait of Hormuz",
    }
)

result.enabled          # bool — was challenger active?
result.show_process     # bool — should UI show the digest?
result.challenge_points # list of dicts [{type, description, accepted, impact}]
result.final            # str — final synthesis text
result.outputs_differ   # bool — final != first_pass
result.accepted_count   # int
result.rejected_count   # int
result.key_change       # str — one-sentence summary of biggest change
result.exchange_id      # int — guild.challenger_exchanges row id (None if DB down)
```

If `enabled: false`, `run()` returns immediately with `result.enabled = False`
and `result.final = first_pass`. Caller code path is unchanged.

---

## Test harness design (Phase 1 deliverable)

`domains/guild/services/test_challenger.py --domain curator_deep_dive`

Runs:
1. Load config — confirm domain enabled
2. Submit canned first_pass (50-word fixed text with a deliberate weak claim)
3. Print Round 2 challenge output (raw JSON from Grok)
4. Print Round 3 final review (raw JSON from Claude)
5. Print parsed `ChallengerResult` fields
6. Confirm row written to `guild.challenger_exchanges`
7. Exit 0 if accepted_count ≥ 1, outputs_differ = True

Supports `--dry-run` (skips API calls, prints prompt text only).
Supports `--domain <name>` to test any configured domain.

---

## What is NOT in this build

- CoS `/challenger` endpoint — not needed; domains call the service directly
- Portal insights page — Phase 4
- Neo4j graph model — Phase 4 trigger (50+ exchanges)
- German conversation/Lesen/Wörter — excluded by spec

---

## Files to create (Phase 1 — today)

| File | Status |
|---|---|
| `domains/guild/db/schema_challenger.sql` | Create |
| `domains/guild/config/challenger_config.json` | Create |
| `domains/guild/services/__init__.py` | Create |
| `domains/guild/services/challenger.py` | Create |
| `domains/guild/services/test_challenger.py` | Create |
| `domains/guild/db/test_schema.py` | Update |
