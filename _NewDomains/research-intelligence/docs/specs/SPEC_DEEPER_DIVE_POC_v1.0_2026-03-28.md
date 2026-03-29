# SPEC_DEEPER_DIVE_POC_v1.0_2026-03-28.md
*Date: March 28, 2026*
*Author: claude.ai design session*
*Status: POC Spec — ready for OpenClaw review and Claude Code build*
*Companion docs:*
- *SPEC_DEEPER_DIVE_ANALYSIS_v1.0_2026-03-28.md (intellectual design)*
- *VISION_DEEPER_DIVE_READING_ROOM_2026-03-28.md (broader vision)*

---

## POC Intent

Build and validate the two-agent Deeper Dive analysis as a standalone Python script against real data. This is a backend-first POC — no UI, no flow integration, no frontend. The goal is to generate a real Deeper Dive essay from a real research thread, read it, and decide if it's worth building into the system.

If the output is good, this script becomes the core of the Deeper Dive generation layer. If it needs iteration, we refine the prompts over a week before wiring into the closing flow.

**This pattern is designed to be reusable across all mini-moi domains** — not just Research. Any domain with a motivation statement and accumulated sources (Language Learning, Job Search, future domains) can use the same two-agent architecture with domain context passed as a parameter.

---

## POC Test Case

**Primary:** `strait-of-hormuz` thread
- 2 sessions (small, clean, good for first run)
- Real motivation, real sources
- Low cost to generate, easy to evaluate

**Secondary (run in parallel, evaluate after):** `taiwan-defense` thread
- Newly started, will accumulate sessions over coming days
- Richer intellectual content once it has 3-5 sessions

Do not use `empire-landpower` — early testing artifact, not representative.

---

## What the Script Does

Single Python script: `scripts/generate_deeper_dive.py`

Takes a topic name as input, loads all session data for that thread, runs two sequential agent calls, and writes the output essay to a file for review.

```bash
python scripts/generate_deeper_dive.py --topic strait-of-hormuz
```

Output: `data/deeper_dives/strait-of-hormuz-deeper-dive-2026-03-28.md`

---

## Input Data

The script loads from existing data structures — no new data formats needed:

1. **Thread motivation** — from `research_config.json` (motivation field for the topic)
2. **Session findings** — all session `.md` files under `topics/<topic>/`
3. **User annotations** — from `data/annotations/` filtered to this topic (if any exist)
4. **Session cost/metadata** — from session files for the header block

Reading Room placeholder: create `data/reading_room/<topic>.json` as an empty stub during this POC. Not populated, not used. Architecture acknowledges it; building it is deferred.

---

## Agent Architecture

### Sequential, not parallel
Agent 2 (Challenger) receives Agent 1's full output before running. This creates genuine dialectic — the Challenger responds to what was actually synthesized, not to the raw data independently.

### Agent 1: The Synthesizer
**Model:** claude-opus-4-5 (or latest Opus available)
**Temperature:** 0.3 (analytical, grounded)

**System prompt:**
```
You are a rigorous research synthesizer. Your job is to honestly 
assess what a body of research actually found — not what the 
researcher hoped to find. Be accurate, be complete, surface 
uncertainty where it exists.

Priority order for your analysis:
1. Evidence from the research sessions (treat as primary data)
2. The researcher's own annotations and reactions
3. Established knowledge, canonical texts, and authoritative 
   commentary you can bring to bear beyond what the sessions found

Do not soften findings. Do not omit inconvenient evidence. 
Your output will be reviewed by a second agent whose job is 
to challenge it — so be honest now.
```

**User prompt:**
```
Research topic: {topic}
Domain: {domain}
Original motivation: {motivation}

Session findings:
{all_session_findings}

User annotations (if any):
{annotations}

Produce:
1. WHAT THE RESEARCH FOUND — honest synthesis (3-5 paragraphs)
2. WHERE THE HYPOTHESIS HOLDS — strongest confirming evidence
3. KEY SOURCES — 3-5 most significant sources and what they establish
4. WHAT REMAINS UNCERTAIN — gaps, unresolved questions, absence of evidence

Be specific. Cite sources by name. Do not generalize.
```

---

### Agent 2: The Challenger
**Model:** claude-opus-4-5 (or latest Opus available)
**Temperature:** 0.7 (more generative, willing to push)

**System prompt:**
```
You are a structured analytic challenger, modeled on Devil's 
Advocacy and Red Team Analysis techniques from intelligence 
community tradecraft (Heuer & Pherson, CIA Tradecraft Primer 2009).

Your job is not to be contrarian for its own sake. Your job is 
to make the researcher's thinking better by finding what they 
missed, what the evidence actually doesn't support, and what 
they didn't expect.

IMPORTANT: Only challenge using:
- Evidence that actually appeared in the research sessions
- Well-established facts and authoritative sources you can cite
- Logical gaps in the synthesis you just read

Do NOT invent counter-evidence. Do NOT hallucinate sources.
If you cannot find genuine challenge, say so honestly.
```

**User prompt:**
```
Research topic: {topic}
Domain: {domain}
Original motivation: {motivation}

The Synthesizer produced this analysis:
{synthesizer_output}

Your job:
1. WHERE IT DOESN'T HOLD — explicit challenge to the original 
   motivation using disconfirming evidence
2. ALTERNATIVE INTERPRETATIONS — how else could the same 
   evidence be read?
3. WHAT YOU DIDN'T EXPECT — lateral findings the research 
   surfaced outside the original motivation
4. REVISED FRAMING — context-sensitive closing:
   - If motivation was a hypothesis: propose a revised framing
   - If motivation was curiosity: surface the most interesting 
     questions the research opened
   - If mixed: do both briefly
   Frame this as a prompt to the researcher, not a conclusion.
   The researcher owns the conclusion.
```

---

## Output Format

The script assembles both agent outputs into a single markdown file:

```markdown
# DEEPER DIVE: {topic_title}
*Generated: {date} · {N} sessions · {N} sources · Est. cost: ${cost}*
*Original motivation: "{motivation}"*
*Domain: {domain}*

---

## What the Research Found
{synthesizer: section 1}

## Where Your Hypothesis Holds
{synthesizer: section 2}

## Key Sources
{synthesizer: section 3}

## What Remains Uncertain
{synthesizer: section 4}

---

## Where It Doesn't Hold
{challenger: section 1}

## Alternative Interpretations
{challenger: section 2}

## What You Didn't Expect
{challenger: section 3}

## Revised Framing
{challenger: section 4}

---

## Bibliography
{deduplicated source list across all sessions}

---
*Synthesizer: claude-opus · Challenger: claude-opus*
*Deeper Dive v1.0 — POC*
```

---

## Script Structure

```
scripts/generate_deeper_dive.py

Functions:
  load_thread_data(topic)         # loads config, sessions, annotations
  build_synthesizer_prompt(data)  # assembles Agent 1 user prompt
  run_synthesizer(prompt)         # Anthropic API call, returns text
  build_challenger_prompt(data, synthesizer_output)  # Agent 2 prompt
  run_challenger(prompt)          # Anthropic API call, returns text
  assemble_essay(data, s_out, c_out)  # combines into output format
  write_output(topic, essay)      # writes to data/deeper_dives/
  create_reading_room_stub(topic) # creates empty placeholder JSON

main()                            # orchestrates, logs cost
```

---

## Domain Reusability

The script is domain-agnostic by design. Domain context is passed as a parameter:

```python
generate_deeper_dive(
    topic="strait-of-hormuz",
    domain="research",           # or "language", "jobs", future domains
    motivation="...",
    session_data=[...],
    annotations=[...]
)
```

When other domains develop motivation statements and accumulated session data, the same script runs against them with no architectural changes. Only the prompts may need light domain-specific tuning.

---

## Cost Estimate

Two Opus calls per Deeper Dive:
- Synthesizer input: ~8-15k tokens (session data)
- Synthesizer output: ~1-2k tokens
- Challenger input: ~10-17k tokens (session data + synthesizer output)
- Challenger output: ~1-2k tokens

Estimated per Deeper Dive: $0.50-1.50 depending on thread size.
Strait-of-hormuz (2 sessions): expect toward the lower end (~$0.50).

Cost gate: script only runs on explicit user invocation. Never automatic.

---

## Reading Room Placeholder

Create `data/reading_room/strait-of-hormuz.json` as an empty stub:
```json
{
  "topic": "strait-of-hormuz",
  "created": "2026-03-28",
  "status": "placeholder",
  "sources": [],
  "note": "Reading Room deferred — see VISION_DEEPER_DIVE_READING_ROOM"
}
```

---

## Build Order

1. `scripts/generate_deeper_dive.py` — core script with both agents
2. `data/deeper_dives/` — output directory
3. `data/reading_room/` — stub directory + placeholder JSON
4. Run against `strait-of-hormuz` — read output, evaluate quality
5. Iterate prompts if needed
6. Run against `taiwan-defense` once it has 3-5 sessions

**Do not wire into the closing flow until output quality is confirmed.**

---

## Verification

```bash
# Run POC
python scripts/generate_deeper_dive.py --topic strait-of-hormuz

# Check output exists
cat data/deeper_dives/strait-of-hormuz-deeper-dive-*.md

# Check reading room stub created
cat data/reading_room/strait-of-hormuz.json

# Check cost logged
# (script should print cost summary to stdout on completion)
```

---

## Scope Boundary

**NOT in this POC:**
- UI of any kind
- Thread close trigger / flow integration
- Reading Room population
- Curator archive storage
- Automatic generation
- Multi-domain deployment (architecture supports it, not tested yet)

**This POC answers one question:** Is the two-agent output good enough to build around?
