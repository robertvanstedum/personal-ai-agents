# Learning Loop Vision

**mini-moi · language-german domain**

The goal is not to deliver better exercises.

The goal is for the system to *know Robert as a learner* — not just as a user who happens to practice German.

Every session (Gespräche, Schreiben, Wörter, Telegram drills) should leave a trace.  
Every trace should inform the next session.  
Over time the system should surface the right focus at the right moment, back off when improvement is detected, and remember the relationship history with each persona.

This is the difference between a sophisticated flashcard app and a true language partner.

## Why This Matters

- Current state: stateless sessions. Every conversation starts from zero.
- Future state: cumulative memory per persona + cross-session learner profile.
- Result: Robert experiences continuity, the tutor feels responsive, and progress becomes visible.

## Core Principle

**Memory is not a feature. It is the foundation.**

Without it, the HTML interface, the personas, and the drills remain isolated tools.  
With it, they become a coherent system that adapts to the learner.

## Three-Layer Architecture

1. **Persona Memory** — per relationship, per round  
   Tracks topics, errors, strengths, vocabulary, and rapport with each persona (Frau Berger, Herr Müller, etc.). Rounds auto-close after N sessions; summaries are generated.

2. **Learner Profile** — cross-persona, cross-tab patterns  
   Aggregates error trends, strengths, and active focus areas across all interactions (Gespräche + Schreiben + Wörter).

3. **Tutor Layer** — surfaces suggestions, manages focus, celebrates improvement  
   Generates actionable recommendations when thresholds are crossed, injects focus into prompts, and clears focus when improvement is confirmed.

## Prerequisite

Safe concurrent JSON writes (Telegram + HTML) must be solved first. All memory writes will use the new `safe_read_json` / `safe_write_json` helpers.

## Scope Note

This is v1.1 of the German domain.  
Spec only — no implementation yet.  
Build order and definition of done are documented in the companion spec.

---

*Written 2026-05-23 — vision before code.*