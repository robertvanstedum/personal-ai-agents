# Cost Architecture — Findings and Fix Spec
**Date:** March 21, 2026
**Status:** Ready for implementation
**For:** OpenClaw (behavior change) + Claude Code (script updates)
**Ref:** Sessions 001–003, STORY_FOR_CLAUDE_AI.md

---

> **Claude Code read confirmation — March 21, 2026**
> Read and understood: WAY_OF_WORKING.md + this spec. Build scope is three changes to `research.py` only (2a, 2b, 2c). No refactoring outside those changes. Will show diff for each change before writing any files, one at a time, waiting for Robert's approval between each. Starting point when Robert returns: show current `research.py` in full, then propose 2a diff.

---

---

## What We Found

Three sessions revealed where the real cost is. This is the key learning for the case study.

| Layer | Cost | Model | Status |
|-------|------|-------|--------|
| `research.py` script (triage, translation, Telegram) | ~$0.01/session | Haiku | ✅ Clean |
| Brave API searches | $0 | None | ✅ Clean |
| OpenClaw conversation (reading output, summarizing, discussing) | ~$3–4/day | Sonnet | ❌ This is the problem |

**The script is not the problem. The conversation around the script is.**

Every time OpenClaw read a session file, wrote a summary doc, discussed findings, or prepared handoffs — that was Sonnet context burning tokens. The research itself cost $0.02 across two sessions. The discussion about the research cost ~$3–4.

**Ratio:** ~200x more spend on talking about research than doing it.

---

## The Fix — Two Parts

### Part 1: OpenClaw behavior change (no Claude Code needed)

**New rule:** OpenClaw calls `python research.py` and reads only the exit code. Nothing else until Robert explicitly asks for analysis.

Specifically — OpenClaw must NOT:
- Read session output files after a run
- Summarize findings unprompted  
- Write story docs or handoff docs during or after a session
- Discuss what the script found unless Robert asks
- Use Sonnet to "check" or "review" script output

OpenClaw's job after calling the script:
```
exit 0 → log "Session complete" → stop
exit 1 → send Telegram hard stop alert → stop  
exit 2 → send Telegram warn-ahead alert → stop
```

That's it. Robert reads the findings himself via the HTML reader or by opening the markdown files. Analysis happens when Robert initiates it — in Claude.ai web chat, not in OpenClaw's Sonnet context.

**When Robert asks for analysis:** OpenClaw can read one file and respond. Not proactively — only on request.

### Part 2: Claude Code script updates

Three changes to `research.py`:

**2a. Self-contained session summary in Telegram message**

The script already sends a Telegram message. Expand it so Robert gets everything he needs to decide whether to read the full findings — without OpenClaw summarizing anything.

Current message format:
```
[Research Intel] 🌐 Session complete
Cost: $X.XX | Duration: ~Xmin
```

New format:
```
[Research Intel] 🌐 Session complete

Thread: [session name]
Cost: $X.XX | Duration: ~Xmin | Candidates: N

Top find: [highest-scoring source, one sentence]
Non-English: [translated source name, or "none scored ≥4"]

Open threads:
• [thread 1 from findings]
• [thread 2 from findings]  
• [thread 3 from findings]

Full findings: topics/[topic]/[session-id].md
Reply to discuss or redirect.
```

This replaces OpenClaw's post-session summary entirely. Robert reads the Telegram message and decides whether to look deeper.

**2b. Ollama availability check with clean fallback logging**

Sessions 002 and 003 show Ollama made 0 calls, Haiku made all 12 fallback calls. Ollama isn't running. The script fell back correctly but didn't surface why.

Add to script startup:
```python
def check_ollama_available():
    """Try a lightweight Ollama ping. Return True if available."""
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=2)
        return response.status_code == 200
    except:
        return False
```

Log clearly in session notes:
```
Ollama: available / unavailable (fell back to Haiku)
```

This makes the fallback visible without any behavior change.

**2c. Per-call cost tracking in session log**

Currently `research.py` passes a single cost figure to `run.py end`. Add a per-call breakdown to the session findings file so the ledger is auditable:

```
## Cost breakdown
| Call | Model | Tokens in | Tokens out | Cost |
|------|-------|-----------|------------|------|
| triage-1 | haiku | 450 | 120 | $0.0008 |
| triage-2 | haiku | 380 | 95 | $0.0006 |
| translation | haiku | 800 | 600 | $0.0021 |
| **Total** | | | | **$0.0035** |
```

---

## What Does NOT Need to Change

- The session spec format (CONTEXT.md, five research targets) — working well
- The triage prompt — the Anglophone weighting fix (relevance first, origin as tiebreaker) is a prompt text change, not a code change. OpenClaw updates the prompt in the next session brief.
- The library structure — clean, grep works, HTML reader works
- Budget enforcement in `run.py` — correct, just needs the accurate cost figure from the per-call tracking above

---

## Case Study Note

This is the most important architectural learning of the pilot so far:

> The research is cheap. The conversation about the research is expensive. Design the system so the script is self-reporting and the human reads output directly — don't route findings through a Sonnet conversation layer.

This generalizes beyond this project. Any agentic system where an orchestration model reads and summarizes the output of a cheaper model is paying Sonnet prices for Haiku work. The fix is always the same: make the cheap model self-reporting, eliminate the summarization layer.

Log this in `journal/` as a standalone learning entry.

---

## Instructions for Claude Code

Read `WAY_OF_WORKING.md` first.

Build the three changes to `research.py` listed in Part 2 above (2a, 2b, 2c). 

Show me the diff for each change before writing any files. One change at a time. Do not refactor anything outside the specific changes listed.

After all three are approved and written, update `docs/specs/` with this document as the spec reference.

---

## Instructions for OpenClaw

Part 1 is a behavior change only — no code needed.

Effective immediately:
- Call `python research.py` → read exit code → stop
- Do not read session output files after a run unless Robert asks
- Do not summarize findings, write story docs, or discuss results unprompted
- Analysis happens in Claude.ai web chat when Robert initiates it

Add this rule to your working memory and to `agent/config.json` as a comment so it's visible to any tool reading the config.

---
*March 21, 2026 | Claude.ai + OpenClaw + Robert*  
*Save to: docs/specs/cost-architecture-fix-2026-03-21.md*
