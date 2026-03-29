# SPEC_DEEPER_DIVE_ANALYSIS_v1.0_2026-03-28.md
*Date: March 28, 2026*
*Author: claude.ai design session*
*Status: Vision / Analysis Design — backend POC before UI spec*
*Companion doc: VISION_DEEPER_DIVE_READING_ROOM_2026-03-28.md*

---

## What This Is

The intellectual design for the Deeper Dive closing essay. This is not a mechanics spec — it defines what the analysis does, how it thinks, and what it produces. The mechanics (trigger, storage, flow) are a separate document.

The goal: a Deeper Dive should feel like something a good analyst wrote after sustained inquiry — not a summary of what was found, but a genuine analytical product that challenges your original thinking.

---

## The Problem With Simple Summaries

A single-agent summary of research sessions produces confirmatory output. It finds what you were looking for, organizes it, and reflects it back. This is the intelligence community's core identified failure mode: **confirmation bias** — searching for evidence that supports rather than disconfirms prior beliefs.

Richards J. Heuer Jr.'s foundational work at the CIA (*Psychology of Intelligence Analysis*, 1999) identified this as the primary cause of intelligence failures. His solution: **Structured Analytic Techniques (SATs)** — forcing the analyst to explicitly consider competing hypotheses and disconfirming evidence before reaching a conclusion.

This is exactly the friction principle already built into the Curator daily briefing. The Deeper Dive extends it to the closing synthesis.

---

## The Two-Agent Design

The Deeper Dive is produced by two agents working in sequence, not one.

### Agent 1: The Synthesizer
**Role:** What did the research actually find?

Draws on:
- All session findings across the thread lifetime
- Sources accumulated and scored
- User notes and reactions (from annotations)
- The original motivation statement

Produces:
- A structured synthesis of what the evidence shows
- Key patterns across sessions
- Strongest sources and what they establish
- What remains uncertain or unresolved

**Model:** Diagnostic Reasoning (Heuer/Pherson SAT framework) — making the evidence base transparent and auditable before reaching conclusions.

---

### Agent 2: The Challenger
**Role:** Where is the original motivation wrong, incomplete, or too narrow?

Draws on:
- Agent 1's synthesis output
- The original motivation statement
- Disconfirming evidence from sessions (sources that cut against the motivation)
- Lateral findings — things the research surfaced that weren't in the original motivation

Produces:
- Explicit challenges to the user's original hypothesis
- Alternative interpretations of the same evidence
- What the research did NOT find (absence of evidence as data)
- Lateral connections the user may not have anticipated

**Model:** Devil's Advocacy + Red Team Analysis (CIA Tradecraft Primer, 2009) — "deliberately posing an alternative outcome to a problem that differs starkly from the accepted analysis." The Challenger is not adversarial for its own sake; it is adversarial in service of better thinking.

---

## Output Structure

```
DEEPER DIVE: [Topic]
Generated: [date] · [N] sessions · [N] sources · [cost]
Original motivation: [user's motivation statement, verbatim]

1. WHAT THE RESEARCH FOUND
   [Agent 1 synthesis — 3-5 paragraphs]
   
2. WHERE YOUR HYPOTHESIS HOLDS
   [Strongest confirming evidence, honestly assessed]
   
3. WHERE IT DOESN'T
   [Agent 2 challenge — disconfirming evidence, 
    alternative interpretations, what was absent]
   
4. WHAT YOU DIDN'T EXPECT
   [Lateral findings — things that surfaced outside 
    the original motivation]
   
5. REVISED FRAMING
   [A short synthesis paragraph: given all of the above,
    how would you restate the original motivation now?
    This is a prompt to the user, not a conclusion —
    the user owns the conclusion]

BIBLIOGRAPHY
   [Full source list across all sessions, deduplicated]
```

---

## The Friction Principle Applied

Section 3 ("Where it doesn't") is the core friction mechanism. It is not optional and it is not softened. Every Deeper Dive must contain genuine challenge to the original motivation, even if the evidence largely confirms it.

This mirrors the Curator's built-in friction (non-Anglophone sources, contrarian signals) — the system is designed to not just tell you what you want to hear.

Section 4 ("What you didn't expect") implements the lateral connection principle — surfacing things the user didn't ask for but the research found anyway. This is where genuine intellectual surprise lives.

Section 5 is deliberately a prompt, not a conclusion. The system proposes a revised framing; the user decides whether to accept it, modify it, or reject it. The analysis informs but does not conclude on the user's behalf.

---

## Grounding in Real-World Frameworks

| Deeper Dive element | Intelligence community equivalent |
|---|---|
| Agent 1: Synthesizer | Diagnostic Reasoning (Heuer/Pherson) |
| Agent 2: Challenger | Devil's Advocacy / Red Team Analysis (CIA Tradecraft Primer) |
| Section 3: Where it doesn't hold | Analysis of Competing Hypotheses (ACH) — disconfirming evidence |
| Section 4: What you didn't expect | Outside-In Thinking / Lateral connections |
| Section 5: Revised framing prompt | Key Assumptions Check — surfaces what was assumed, not proved |

Primary reference: Heuer & Pherson, *Structured Analytic Techniques for Intelligence Analysis* (CIA / Pherson Associates, 2009). CIA Tradecraft Primer (2009), publicly available.

---

## POC Approach — Backend First

Before building this into the flow, validate the output quality against real data:

**Test case 1:** Taiwan deep dive (single article, "Hellscape Taiwan: A Porcupine Defense in the Drone Age") + strait-of-hormuz research thread sessions as proxy research data.

**Test case 2:** empire-landpower thread (13 sessions, richest data available).

**Method:**
1. Write the two-agent prompts
2. Run them against real session data manually (Python script or direct API call)
3. Read the output — is it genuinely analytical? Does the challenge feel honest?
4. Iterate on prompts until the output is worth keeping
5. Only then wire into the closing flow

This is how the Curator intelligence layer was built. Prove the output first, build the UX around confirmed quality.

---

## Decisions

1. **Model selection:** Opus for both agents in the POC. Establish quality ceiling first; optimize for cost later.

2. **Input sources for Agent 1:** Three-tier input hierarchy:
   - **Primary:** Session findings and scored sources from the research thread
   - **User voice:** Annotations, notes, and corrections made during the thread
   - **Extended knowledge:** Agent may draw on established writings, canonical texts, and authoritative commentary beyond what the research sessions surfaced. The thread informs direction; it does not limit the knowledge base. If the sessions missed a foundational text, the Synthesizer can bring it in anyway.

3. **Section 5 — context-sensitive closing:** The motivation statement is not always a hypothesis. Sometimes it is curiosity. The agent reads the motivation and responds accordingly:
   - **Hypothesis motivation** ("I suspect X...") → revised framing: "The evidence suggests reframing as Y"
   - **Curiosity motivation** ("I want to understand X...") → forward questions: "The research opened these threads worth pursuing"
   - **Mixed** → both, briefly
   The user owns the conclusion. Section 5 is always a prompt, never a verdict.

4. **Length:** 800-1200 words for the full essay. Long enough to be substantive, short enough to be read.
