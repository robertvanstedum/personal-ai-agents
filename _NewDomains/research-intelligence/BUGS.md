# Bug Log
**Project:** research-intelligence  
**Maintained by:** OpenClaw, Claude Code, Robert  
**Protocol:** Log immediately when found, regardless of severity. This is a PoC — the bug log is part of the case study record.

---

## Format

```markdown
## BUG-[number]: [short title]
**Found by:** OpenClaw / Claude Code / Robert  
**Date:** YYYY-MM-DD  
**Status:** OPEN / FIXED / WONTFIX  
**Severity:** low / medium / high  

**What happened:**  
[Description]

**Expected:**  
[What should have happened]

**Fix:**  
[What was done, or blank if open]

**Commit:**  
[Commit hash when fixed, or blank]
```

---

## Open Bugs

## BUG-006: Translation runs on English-language sources — Ollama misclassifies anglophone_origin
**Found by:** Robert
**Date:** 2026-03-23
**Status:** FIXED
**Severity:** low

**What happened:**
Translation step ran on "The Bretton Woods System | World Gold Council" (plainly English).
Ollama (gemma3:1b) returned `anglophone_origin: false` for the source — a misclassification.
The pipeline correctly followed its logic (non-anglophone + score ≥ 4 → translate) but the
upstream flag was wrong. Cost: ~$0.001 wasted per session when this fires.

**Expected:**
Translation only runs on genuinely non-English sources. If `language == "English"`,
skip translation regardless of what `anglophone_origin` says.

**Fix:**
Added language guard before translation selection loop in research.py:
`if c.get("language", "").lower() == "english": continue`
Skips the candidate entirely — `anglophone_origin` flag from Ollama is not trusted
when language is explicitly reported as English.

**Commit:**
*(see next commit)*

## BUG-005: Session findings "Research Question" section hardcodes empire-landpower label
**Found by:** Robert
**Date:** 2026-03-23
**Status:** FIXED
**Severity:** low

**What happened:**
`gold-001` session findings show: *"Structured triage session — identifying highest-value
sources across the 5 research targets defined in CONTEXT.md (Kotkin / Empire & Land Power
thread)."* The label "Kotkin / Empire & Land Power thread" is hardcoded in the
findings template in `agent/research.py`, not derived from the topic.

**Expected:**
Research Question section should reference the actual topic name, not a hardcoded
empire-landpower label. Running gold-geopolitics should not reference empire-landpower.

**Fix:**
Replaced hardcoded label with dynamic `topic` variable and `len(triage_targets)` count.
Now reads: "Structured triage session — identifying highest-value sources across the N
research targets defined for topic: {topic}."

**Commit:**
*(see next commit)*

---

## Closed Bugs

## BUG-004: No agent/research.py — sessions run inside Sonnet subagent, not direct script
**Found by:** Robert + OpenClaw
**Date:** 2026-03-21
**Status:** FIXED
**Severity:** high

**What happened:**
Session 001 ran inside a Sonnet-powered subagent that orchestrated all searching, writing, and decision-making. Haiku API calls cost ~$0.002; Sonnet orchestration overhead cost ~$2.09. Budget ledger only tracked the Haiku spend — reported $0.002, actual balance drop was $2.09.

**Expected:**
Research sessions run as a direct Python script (`agent/research.py`) that calls APIs explicitly. No large-model orchestration wrapper. Haiku and Grok called directly, files written by the script itself.

**Fix:**
Built `agent/research.py` — direct 8-step script, no Sonnet wrapper. All model calls explicit and instrumented. Ollama-first with Haiku fallback. Costs accumulate via `track_cost()` and pass to `run.py end`. Dry-run tested and confirmed end-to-end.

**Commit:**
poc/research-script — see commit on this branch

---

## BUG-003: Budget ledger tracks Haiku spend only — misses orchestration and other model costs
**Found by:** Robert
**Date:** 2026-03-21
**Status:** FIXED
**Severity:** high

**What happened:**
`run.py` tracks costs passed explicitly via `--cost` flag. In Session 001 the agent reported only Haiku API cost ($0.002). Total actual spend including orchestration was ~$2.09. The budget gate would not have caught a daily limit breach from orchestration overhead.

**Expected:**
Budget ledger reflects total API spend for the session — either by reading API balance delta at start/end, or by instrumenting all model calls in `agent/research.py`.

**Fix:**
`agent/research.py` instruments every model call via `track_cost(usage)` (uses Anthropic's `usage.input_tokens` / `usage.output_tokens`). Accumulated `session_cost` passed to `run.py end --cost`. No Sonnet calls in the script, so no hidden orchestration overhead.

**Commit:**
poc/research-script — see commit on this branch

---

## BUG-002: Ollama not wired into session spec — direction doc lists it but agent never called it
**Found by:** Robert + OpenClaw
**Date:** 2026-03-21
**Status:** FIXED
**Severity:** medium

**What happened:**
The direction document (`openclaw_direction_research_intelligence_v2.md`) lists Ollama as the free first-pass triage tier. The session brief passed to the agent specified Haiku for triage, skipping Ollama entirely. Ollama was never called — not a code failure, a spec gap.

**Expected:**
Ollama should be a configurable first-pass triage option. Try Ollama, fall back to Haiku if unavailable or below quality threshold. Swappable via `agent/config.json`, not hardcoded in prompts.

**Fix:**
`agent/research.py` calls `try_ollama()` first with 3s timeout; if connection refused or timeout, silently falls back to `call_haiku()`. Which model was used logged per candidate. Config in `agent/config.json` under `triage_model.primary` / `triage_model.fallback`. Tested: Ollama not running → Haiku fallback confirmed in dry-run.

**Commit:**
poc/research-script — see commit on this branch

---

## BUG-001: Haiku model ID wrong in session spec
**Found by:** Subagent (self-reported)
**Date:** 2026-03-21
**Status:** FIXED
**Severity:** low

**What happened:**
Session brief specified `claude-haiku-3-5-20241022`. Correct ID is `claude-3-haiku-20240307` (original) or `claude-3-5-haiku-20241022` (newer). Agent corrected itself at runtime. Note: `claude-3-5-haiku-20241022` reached EOL 2026-02-19; updated to `claude-haiku-4-5` as part of this fix.

**Expected:**
Model ID should come from `agent/config.json`, not hardcoded in session prompts.

**Fix:**
Model IDs now in `agent/config.json` under `triage_model.fallback` and `models.translation.model`. Set to `claude-haiku-4-5` (current as of 2026-03-21).

**Commit:**
poc/research-script — see commit on this branch

---
*Log bugs above their respective section headers, newest first within each section*
