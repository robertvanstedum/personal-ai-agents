# VISION_DEEPER_DIVE_READING_ROOM.md
*Date: March 28, 2026*
*Author: claude.ai design session*
*Status: Vision / Pre-spec — for OpenClaw review and Reading Room spec input*

---

## The Insight

The Research domain and the Curator domain currently run in parallel but don't fully close the loop. Today's build connected them in one direction: a Deep Dive can spawn a Research thread. This document captures the return path — a Research thread that runs its course produces a **Deeper Dive**, which lands back in the Curator archive alongside the original Deep Dives.

---

## Two Formats, One Archive

| | Deep Dive | Deeper Dive |
|---|---|---|
| **Origin** | Single saved article | Research thread (multi-session) |
| **Your input** | Interest statement | Motivation + starting queries |
| **AI work** | Single analysis pass | Incremental observations over days |
| **Duration** | One moment | Days (3 / 5 / 7 / 14) |
| **Bibliography** | Sources from article | Accumulated sources across sessions |
| **Closing content** | Analysis essay | Closing synthesis essay |
| **Stored in** | Curator / Deep Dives | Curator / Deep Dives (same archive) |

Same format family. Different depth and origin. They sit side by side in the archive.

---

## The Full Loop

```
Curator Daily Briefing
  → save article
  → Deep Dive (single article analysis)
  → spawn Research Thread from Deep Dive   ← built today

Research Thread runs over N days
  → sessions find and triage sources
  → Reading Room surfaces curated sources briefly
  → AI Observations synthesize incrementally
  → thread closes → Deeper Dive generated   ← this vision doc

Deeper Dive lands in Curator archive
  → sits alongside original Deep Dives
  → bibliography included
  → can itself spawn a new Research thread   ← the loop continues
```

---

## The Reading Room

The Reading Room is the triage layer between raw session output and lasting insight. It is not a growing list — it is a brief window.

**What it is:**
- A curated view of sources surfaced by recent sessions, worth the user's attention
- Clears automatically after a short window (e.g. 7 days, or when thread closes)
- Supports reading, noting, and dismissing — nothing lingers

**What it is not:**
- A permanent archive
- A replacement for the Sessions page (that remains background/debug)
- Another growing backlog to manage

**Flow:**
```
Session runs → sources scored by agent
  → high-scoring sources surface in Reading Room (brief window)
  → user reads, notes, or dismisses
  → AI Observations draw on what was noted
  → sources age out automatically
  → thread closes → Reading Room empties → Deeper Dive generated
```

---

## The Deeper Dive — Closing Essay

When a research thread closes (manually or when `duration_days` expires), the system generates a closing synthesis essay. This becomes the Deeper Dive.

**Contents:**
- Title (derived from thread topic and motivation)
- Closing synthesis essay — short, structured, drawing on AI Observations accumulated during the thread
- Full bibliography of sources encountered across all sessions
- Thread metadata: duration, session count, cost, date range

**Trigger:**
- Manual close preferred (user decides when the inquiry is complete)
- Automatic on `duration_days` expiry as fallback

**Storage:**
- Same location and format as Deep Dives
- Distinguishable by a "Deeper Dive" label or badge in the archive
- Can itself spawn a new Research thread (the loop continues)

---

## What This Means for the Sessions Page

With Reading Room handling source triage and Deeper Dive capturing the closing output, the Sessions page becomes truly background — debug and audit only. Users should rarely need to visit it. The value surfaces elsewhere.

The "(Target 1, Target 2...)" scoring language visible on the Sessions page is agent internals and should stay there — it does not belong in Reading Room or Deeper Dive output.

---

## Open Questions for Spec Phase

1. **Manual vs automatic close** — who triggers the Deeper Dive generation?
2. **Reading Room window** — how long do sources surface before aging out?
3. **Deeper Dive in the archive** — same index as Deep Dives, or a separate section?
4. **AI Observations continuity** — do per-session observations feed directly into the closing essay, or does the closing essay make a fresh pass over all session data?
5. **Cost** — closing essay generation is an API call; capture in the thread cost metadata

---

## Next Steps

1. OpenClaw review this vision doc
2. Claude.ai spec Reading Room (v1 — source triage, brief window, auto-expiry)
3. Claude.ai spec Deeper Dive generation (thread close → essay + bibliography)
4. Sequence into roadmap alongside v1.2 items
