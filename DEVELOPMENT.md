# Development Process

## Methodology: Human-AI Collaborative Development

This project is built through structured collaboration between:

- **Robert** (human architect & decision-maker)
- **Claude Code** (Anthropic's ACP coding assistant for implementation)
- **Mini-moi** (OpenClaw assistant for planning, memory, and coordination)

### Why This Approach

**Distributed cognition:** Leverage AI strengths (code generation, testing, documentation) while human maintains strategic control and final decision authority.

**Explicit handoffs:** Clear division of labor prevents duplication and confusion. Each agent has defined responsibilities.

**Test-driven iteration:** Formalized checklist creates quality gates at every step.

**Documentation as workflow:** Writing it down forces clarity and creates continuity across sessions.

---

## Workflow Pattern

### 1. Design Phase

- Multi-agent discussion of architecture and approach
- Human makes final architectural decisions
- Design documented in roadmap before any code written
- Test strategy defined upfront

### 2. Build Phase

- Claude Code implements features incrementally
- Human bridges coordination between agents
- Each component tested independently before integration
- Small, atomic commits with clear messages

### 3. Test Phase

Formalized checklist executed after every code change:

```bash
□ python3 -c "import <module>"                    # Imports clean
□ python3 <script>.py                             # Usage/error handling
□ python3 <script>.py --dry-run                   # Logic without side effects
□ python3 curator_rss_v2.py --dry-run --model=xai # End-to-end integration
□ Web UI sanity test (localhost:5000)             # Frontend unbroken
```

### 4. Ship Phase

- Only after all tests pass
- Commit to main branch (feature branches deferred until v1.0)
- Update roadmap and memory files
- Monitor first production run

---

## What This Achieves

**Zero production regressions** across 7 major phases (Feb-Mar 2026)

**Rapid iteration with quality gates:**
- Phase 3C-alpha: design → implementation → test → ship in 4 hours
- Phase 2B: serendipity reserve + temporal decay in single afternoon session

**Knowledge persistence:**
- `CURATOR_ROADMAP.md` — feature phases, architecture decisions
- `memory/YYYY-MM-DD.md` — daily session logs
- `MEMORY.md` — distilled learnings (private, not in repo)

**Transparent development:**
- All commits public in real-time
- Commit history shows incremental progress
- See git log for evidence of "build little, test little" discipline

---

## Example: Phase 3C Bug Prevention

**Context:** Phase 3C adds domain-scoped signal storage to prepare for multi-domain curation (Finance/Geopolitics, Health/Science, etc.). Requires exact string matching between writer (`x_adapter.py`) and reader (`curator_rss_v2.py`).

**Bug:** Claude Code's initial implementation used the raw X folder name `"Finance and geopolitics"` (lowercase "g") instead of the canonical name `"Finance and Geopolitics"` (uppercase "G") defined in `curator_config.py`.

**How it was caught:** Test checklist step 5:

```bash
python3 x_adapter.py --from-bootstrap --dry-run
```

Printed output showed the mismatch. Fixed before the code ever touched the preferences file.

**What this prevented:** A silent production failure. The curator would have written signals to `domain_signals["Finance and geopolitics"]` but read from `domain_signals["Finance and Geopolitics"]` — resulting in an empty profile and broken personalization. User would have received generic scoring with no explanation.

**The principle:** Build incrementally, test each component, catch issues early. Dry-run flags are safety nets, not optional features.

---

## Testing Checklist (Detailed)

Run after every code change:

### Import Test
```bash
python3 -c "import x_import_archive, x_adapter, curator_config"
```
**Purpose:** Verify no syntax errors, no missing dependencies, no circular imports

### Usage Test
```bash
python3 x_import_archive.py
python3 x_adapter.py
```
**Purpose:** Verify usage/error handling, command-line interface works

### Dry-Run Test
```bash
python3 x_adapter.py --from-bootstrap --dry-run
```
**Purpose:** Verify logic without side effects (no file writes, no API calls with side effects)

### End-to-End Integration
```bash
python3 curator_rss_v2.py --dry-run --model=xai
```
**Purpose:** Verify the full pipeline with real data, check LLM prompt injection, validate output format

### Web UI Sanity Test
```bash
python3 curator_server.py
# Open localhost:5000, click through features
```
**Purpose:** Verify frontend still works, no broken buttons, no console errors

---

## Division of Labor

### Mini-moi (OpenClaw Assistant)
**Responsibilities:**
- Strategic planning and architecture review
- Memory continuity across sessions
- Update `CURATOR_ROADMAP.md`, `MEMORY.md`, `memory/YYYY-MM-DD.md`
- Coordinate between human and Claude Code
- Catch design issues before implementation

### Claude Code (ACP Coding Assistant)
**Responsibilities:**
- Feature implementation
- Write tests and validation scripts
- Update code-specific docs (README sections, inline comments)
- Run test checklist after changes
- Flag blockers or design questions

### Robert (Human)
**Responsibilities:**
- Final decision-maker on all architecture and design
- Bridge coordination between agents (no direct agent-to-agent communication yet)
- Strategic direction and priority setting
- Quality gate: approve or reject before shipping

---

## File Organization

| Path | Purpose | Owner |
|---|---|---|
| `CURATOR_ROADMAP.md` | Feature phases, architecture decisions | Mini-moi |
| `DEVELOPMENT.md` | This file — process and workflow | Mini-moi |
| `memory/YYYY-MM-DD.md` | Daily session logs | Mini-moi |
| `MEMORY.md` | Distilled long-term learnings (private) | Mini-moi |
| `README.md` | Public-facing project documentation | Both (strategic sections: Mini-moi; code sections: Claude Code) |
| `*.py` | Source code + inline comments | Claude Code |

---

## Branching Strategy

**Current (through v1.0):** Stay on `main` branch

**Rationale:**
- Solo developer, sequential features
- No parallel work requiring isolation
- `--dry-run` flags function as feature branches (safe testing before write)
- Small, frequent commits provide rollback points

**When to reconsider:**
- Multiple domains being built simultaneously
- Second contributor joins
- CI/CD pipeline added

---

## Cost Management

**Pattern:** Use Claude Code for implementation (cheaper, faster for code generation), use OpenClaw assistant for verification and coordination.

**Result:** Significant API cost savings on iterative development. Phase 3C-alpha implementation cost ~$2 in Claude Code API calls vs. ~$10+ if done entirely through premium assistant.

---

## Why This Works for Portfolio/Job Search

**Demonstrates:**
- Complex workflow orchestration (multi-agent coordination)
- Quality discipline (testing before shipping, zero regressions)
- Process thinking (not just "I built X" but "here's how I work")
- Human-AI collaboration patterns (emerging skill in 2026 job market)
- Transparent development (commit history shows iteration, not just final state)

**Portfolio differentiation:** Most AI projects show what was built. This shows *how* it was built — and proves the process works through production stability.

---

## Session Example (Mar 1, 2026)

**Goal:** Implement Phase 3C-alpha (domain-scoped signals)

**Session flow:**
1. **Design review (30 min):** Human + Mini-moi + Claude Code discuss architecture
2. **Implementation (2 hours):** Claude Code builds `curator_config.py`, `x_import_archive.py`, updates existing files
3. **Testing (30 min):** Full checklist executed, bug caught (string mismatch)
4. **Bug fix (15 min):** Claude Code fixes canonical name reference
5. **Re-test (15 min):** All tests pass
6. **Documentation (30 min):** Mini-moi updates roadmap and memory files
7. **Ship (5 min):** 7 commits pushed to public GitHub

**Result:** Complex feature shipped in 4 hours with zero production risk. Production v0.9 continues running unchanged. Tuesday integration test scheduled when archive data arrives.

---

**Status:** This process has shipped 7 major features (Phase 0 → Phase 3C-alpha) with zero production outages. It scales.
