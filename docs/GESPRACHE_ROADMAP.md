# Gespräche — Vision, Mobile Priority & Roadmap
*Created: 2026-06-16 — Claude.ai*
*Location: docs/GESPRACHE_ROADMAP.md*
*Status: Active — near-term specs in build queue, vision extends to learning system*

---

## What Gespräche is becoming

Gespräche started as a conversation practice tab. It's becoming something
more specific: a mobile-first language practice environment where the
phone is the natural device, voice is the natural interface, and the
feedback loop runs automatically from conversation through analysis to the
next session.

The phone matters because language is a mobile skill. You're not sitting
at a desk when you walk into a Viennese café. The practice environment
should match the environment where you'll actually use what you're
practicing. Curator works well at a desk. Gespräche works best in your
hand.

---

## Why mobile is the priority now

Three reasons that compound:

**1. The experience is already good enough to be the primary surface.**
After today's mobile audit, all five German pages render correctly on
mobile. The Gespräche flow — persona selection, voice session, Analysieren
— works on iPhone and Android. The foundation is there. Polishing it now
makes it useful immediately, not eventually.

**2. It's the portfolio piece you can hand someone.**
minimoi.ai is a professional showcase. Pulling it up on your phone in an
interview and walking through a live Gespräche session — that lands
differently than a desktop demo. The mobile experience is the one that
happens in the room. It needs to be ready.

**3. Mobile is where the voice command vision starts.**
The longer-term direction — voice commands to backend agents, automatic
transcript handoff, building a backlog of sessions the review agent learns
from — all of that is native to mobile. Polishing the current mobile
experience is the foundation for that vision, not a distraction from it.

---

## The near-term build — what's in the queue now

These specs are produced and ready for Claude Code. They represent the
immediate step: making Gespräche genuinely good on mobile, not just
functional.

### Already shipped (Claude Code, 2026-06-16)
- All 5 German pages render on mobile (fixed `proxy.py` global override)
- Gespräche persona tap auto-scrolls to detail panel
- `selectPersona()` resets scroll — action buttons always visible
- Preview banner readable at 390px

### In build queue — ship in order

**1. Post-analysis dead-end fix (Part C)**
*Spec: `spec_gesprache_testrun_followup_2026-06-15.md`*
After Analysieren there's no way back to start without a page reload.
"Neue Sitzung" button restores the resting state with the same persona
selected. Highest priority — this blocks the basic loop from working.

**2. Consolidated layout pass (7 changes)**
*Spec: `spec_gesprache_consolidated_2026-06-15.md` + Parts A and C
from `spec_gesprache_testrun_followup_2026-06-15.md` folded in*
Tab rename, VAD silence window, layout tightening, feedback box styling,
collapsible analysis sections. Spacing tightening and persona name padding
from the test run fold into this pass — one coherent layout commit.

**3. Transcript paste panel**
*Spec: `spec_gesprache_transcript_paste_panel_2026-06-16.md`*
Full-width textarea in Nach der Sitzung. Clipboard button fills it in
one tap. Pre-populated from KI-Sitzung transcript or empty for external
paste (Grok, Claude, any model). Analysieren button below. This is the
feature that closes the external workflow loop on mobile.

**4. Latency investigation**
*Spec: `spec_gesprache_testrun_followup_2026-06-15.md` Part B*
Measure before fixing. Instrument the turn pipeline:
`transcribe_ms` / `ai_turn_ms` / `tts_ms` / `total_turn_ms`.
Likely fix: route `run_chat_turn()` to a fast model, keep `run_review()`
on the stronger model. Ship the "denkt nach…" thinking indicator
regardless of which lever the numbers point to — it directly addresses
"is this even working" on a slow connection.

**5. Safe area + font size pass**
*Fold into next German CSS commit*
`padding-bottom: env(safe-area-inset-bottom)` on bottom tab bar.
All inputs ≥ 16px to prevent Safari auto-zoom. One pass, all German pages.

**6. iOS share sheet**
*Spec: `spec_gesprache_share_session.md` (to produce)*
`navigator.share({ text: transcript })` after a session. Native iOS and
Android share sheet. More natural than "Telegram öffnen" for sharing
the completed transcript outward. Gate with `if (navigator.share)`.

**7. Post-session summary card**
*Spec: `spec_gesprache_session_summary_card.md` (to produce)*
Compact card above Nach der Sitzung: turns taken, persona, duration,
1-line summary. No LLM call — from session state only. Gives a quick
read before deciding whether to Analysieren.

---

## The medium-term direction — automatic loop

The next meaningful step after the near-term queue is closing the manual
steps out of the practice loop. Currently:

```
Session ends → manually tap Analysieren → read feedback →
manually start Neue Sitzung → repeat
```

The target:

```
Session ends → transcript posts automatically → analysis runs in
background → results waiting when you open Nach der Sitzung →
tap Neue Sitzung → repeat
```

Two changes make this happen:
- Automatic transcript post at session end (remove the manual Analysieren tap)
- Background analysis so results are ready, not loading

This is a small backend change but a meaningful UX shift. The loop tightens.
Each session flows into the next without friction.

**Repeat and modify** is the next step after automatic handoff. After
Maria corrects "einen verlängerten" → "einen Verlängerten," you say
"wiederhole" and the same scene replays with that correction active.
The session builds a backlog of flagged moments. The review agent
pulls patterns from that backlog across sessions. This is where
individual corrections become a learning trajectory, not isolated events.

---

## Voice commands to backend agents

The current Gespräche flow has one mode: conversation. You speak,
the persona responds. The pipeline is:
```
VAD → Whisper → run_chat_turn() → TTS → playback
```

The voice command vision adds a second mode on the same pipeline.
Before `run_chat_turn()` is called, a lightweight intent classifier
checks whether the input is a command or a conversation turn:

```
VAD → Whisper → intent classifier
    → command detected: route to backend agent
    → conversation turn: route to persona (current behavior)
```

Commands don't need to be a separate mode you switch into. They just
work. "Erstelle eine neue Persona — Klaus, Pensionär aus Graz, korrigiert
sanft" creates a persona. "Ändere den Schwerpunkt auf Adjektivendungen"
modifies the session emphasis. "Wiederhole" replays the last exchange.
"Speichere das" saves the current phrase to Wörter.

The intent classifier starts as a lightweight keyword/pattern match —
no second LLM call, no latency added for conversation turns. It expands
as the command vocabulary grows.

**Why this matters for mobile specifically:** voice commands remove the
need to navigate menus on a small screen. Creating a persona, adjusting
emphasis, saving a phrase — all of these are currently multi-tap flows.
With voice commands they're one sentence.

---

## Connection to the learning system

Gespräche is the domain where the learning system vision is most
immediately applicable. Every session produces:

- A transcript (conversation history)
- Error/correction pairs (structured feedback from Analysieren)
- Session metadata (persona, duration, turns, emphasis)
- Flagged moments (things worth repeating or drilling)

This is exactly the signal that feeds the local LLM over time:

**For RAG:** session transcripts and analysis outputs are indexed.
The local LLM can answer "what mistakes does Robert make most often
with Maria" from actual session history, not from training data.

**For LoRA:** accumulated error/correction pairs become training data
for a German-specific adapter. After 3-6 months of daily sessions,
the local model knows your error fingerprint — it corrects your German
the way your personas do, without a cloud API call.

**The longer vision:** the local LLM doesn't just analyze past sessions —
it shapes future ones. It knows from session history that you
consistently miss noun capitalization after articles, so it tells
the persona to watch for that specifically. It knows you're preparing
for Vienna, so it surfaces Viennese vocabulary in the scene briefs.
It knows your current level from 70+ sessions, not from a placement test.

This is the "learn together over time" promise made concrete. Gespräche
is the domain where it starts.

---

## What reading and writing mean in this context

Lesen and Schreiben are present and useful but they're not the mobile
priority. Reading works well at a desk with a larger screen. Writing
practice is deliberate and unhurried. Neither is native to a phone the
way voice conversation is.

This doesn't mean they get neglected — it means they don't drive the
mobile investment decisions. The mobile audit fixed what was broken on
Lesen and Schreiben. Future mobile investment goes into Gespräche first.
Lesen and Schreiben benefit from general mobile improvements (font size,
touch targets, safe areas) without needing dedicated mobile-specific features.

---

## Sequencing principle

The near-term specs fix what's broken and close the obvious gaps.
The medium-term direction (automatic loop, repeat-and-modify) starts
after the near-term is solid and in daily use.
Voice commands come after the automatic loop is established.
The learning system integration starts accumulating signal now and
becomes structurally significant at Phase 1 (RAG) and Phase 2 (LoRA).

Don't build the learning system before there's enough session history
to make it worthwhile. The near-term specs are what generate that history.
Each one is both a usability improvement and a step toward the longer vision.

---

## Specs referenced by this document

| Spec | Status | Priority |
|------|--------|----------|
| `spec_gesprache_testrun_followup_2026-06-15.md` | Ready | Highest — Part C first |
| `spec_gesprache_consolidated_2026-06-15.md` | Ready | High — fold Parts A+C in |
| `spec_gesprache_transcript_paste_panel_2026-06-16.md` | Ready | High |
| `spec_mobile_audit_improvement_2026-06-16.md` | Ready | High — shared with other domains |
| `spec_gesprache_share_session.md` | To produce | Medium |
| `spec_gesprache_session_summary_card.md` | To produce | Medium |
| Automatic transcript handoff | To spec | Medium — after near-term ships |
| Voice command intent routing | To design | Longer term |

---

## Decision record for this direction

*Reasoning: see [`dr_gesprache_mobile_priority_2026-06-16.md`](decision-records/dr_gesprache_mobile_priority_2026-06-16.md)*

---

*Gespräche Roadmap · 2026-06-16 · Claude.ai*
*Commit to: `docs/GESPRACHE_ROADMAP.md`*
*Review when: near-term queue ships, or voice command design session begins*
