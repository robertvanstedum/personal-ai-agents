# Spec — Mein Deutsch v1.1 Landing Blurb + GitHub Release Notes Update
*Created: 2026-06-15 — Claude.ai*
*Amends: `spec_mein_deutsch_v11_release_2026-06-15.md` (supersedes landing copy section)*
*Status: Ready for `_working/`*

---

## Context

The Mein Deutsch v1.1 landing blurb went through two rounds of review this
session (including parallel Grok review) and is now locked. This spec amends
the landing copy section of the existing v1.1 release spec and provides the
final GitHub release notes text. Both ship in the same commit.

---

## Change 1 — Landing page blurb

**File:** `minimoi_portal/templates/landing.html` (or equivalent static file
once Cloudflare Pages migration is complete)

**Replace** the current Mein Deutsch entry (Chicago/scarcity copy) with:

> Mein Deutsch v1.1 — German practice built around your own life. Build your
> own KI-Personas and scenes, manage reading lists tuned to your interests, and
> practice with KI-Personas or real people — transcripts flow through the same
> feedback pipeline either way. Mistakes become drills. Vienna-tested.

No other changes to the landing page in this commit.

---

## Change 2 — GitHub release notes

**Action:** Create GitHub release tagged `mein-deutsch-v1.1`

**Title:** `Mein Deutsch v1.1 — Cross-Workflow Learning Loop`

**Release notes body:**

---

### What's new in v1.1

**Voice conversation with KI-Personas**
Speak and listen in real time. Voice activity detection handles turn-taking
naturally — no push-to-talk. The persona responds in character, in context,
and the scene evolves. You'll never have the exact same conversation twice.

**Mit echtem Mensch — real-person sessions**
Practice with a real German speaker and feed the transcript back in afterward.
The same feedback pipeline that grades KI-Persona sessions grades real-person
sessions. One system, two input sources.

**Analysieren**
Structured feedback after every session: what you got right, what you got
wrong, and why. Errors surface as drills automatically — nothing to configure.

**Tab workflow**
- Mit KI-Persona: persona selection, voice session, post-session analysis
- Mit echtem Mensch: pre-session brief, session, transcript submission,
  analysis

**Reading — daily-refreshed lists**
Lesen reading lists refresh daily. Simplified and full article views.
Word hover translation without leaving the page.

**Writing and vocabulary**
Schreiben prompts and Wörter drills built from your own session errors —
not a generic frequency list.

---

### Why it's built this way

Finding real German speakers depends on where you live. KI-Personas fill the
gap when they're not available; real sessions feed back in when they are. The
loop is the point.

Vienna-tested.

---

### What's next

- Cross-session error pattern detection
- Persona aus echtem Gespräch erstellen (build a KI-Persona from a real
  conversation transcript)
- Tool-agnostic invitation flow (replace Google Meet specificity)
- Streaming TTS for lower-latency conversations

---

## Definition of Done

- Landing page Mein Deutsch blurb matches the locked copy above exactly —
  no punctuation or wording changes.
- GitHub release exists at tag `mein-deutsch-v1.1` with title and body as
  specified above.
- No other landing page content changed.
- No other files changed in this commit.
- Verified: load minimoi.ai landing page and confirm the new blurb renders
  in the Mein Deutsch card.

## Commit

`Update Mein Deutsch v1.1 landing blurb and publish GitHub release notes.`

---

*Spec · 2026-06-15 · Claude.ai*
