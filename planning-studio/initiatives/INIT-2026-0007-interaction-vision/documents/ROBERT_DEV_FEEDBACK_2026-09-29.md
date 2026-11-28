# Robert's dev feedback: September 29, 2026 (evening)

Collected from chat while he tested dev.minimoi.ai. It feeds the Codex handoff and the refinement pass.

1. **Production: no hurry.** "We need to do user testing in dev and refinements. The important part is Guild in workable shape there." Nothing merges to production until he has tested on dev.
2. **The Shop floor, the wall and the Workshop are "all the same, essentially".** They need to be differentiated, each with a clear distinct purpose and layout. This is a refinement item for the next design pass.
3. **CoS voice must follow the standard voice pattern** used by Mein Deutsch and Meu Português: "This should be the same and if not, that is an issue, since we should have a standard pattern for this." Confer-only differences are defects.
4. **Voice reply mode.** Gespräche talks back, and Schreiben writes. For CoS he wants a choice: speak back (the default), write only (the voice muted), or both. It is built as a standard control in the shared controller.
5. **Typed CoS works.** Spoken CoS got no reply, with both OpenAI and xAI. The diagnosis: Confer starts silent (no greeting) and shows nothing until Stop. The fix is Confer voice Phase A.
6. **`/guild-next` returned "not found"** after sign-in. It now redirects to the Shop floor.
7. **Screen pack.** He wants a PDF "wall of screens" for the new Guild, to pass to review in the morning, plus a big Codex handoff once the current work finishes.
8. **Operations crash loop:** "fix tonight". Done; the job runs from `~/.worktrees/ops-runtime` with PR #280.

## Findings from the screen pack (sample data, 2026-09-30 pack; confirm on dev)

1. **The blocker line contradicts the header.** After a good reply the header says "live" while the blocker still reads "unavailable · connected, no answer yet". The header also changes to that text while a reply streams. A Stop shows as "Last answer failed", and an MC failure appears twice.
2. **Off the record barely changes the page.** The header still says "your messages are kept as notes", and the ⓘ help says MC "has not answered yet" even while it is live.
3. **Phone.**
   - A large "Type" button sits under a composer that is already open.
   - The portal bar is cut off ("Meu Portuguê", and CoS is missing).
   - The subnav is clipped.
4. **Queue page.**
   - Blocked #31, which the floor calls the most urgent item, has no column there.
   - Times are shown in UTC on the Queue, item and wall pages, but in local time on the floor.
   - The page is called both "Workbench" and "Wall".
   - The floating MC panel covers half of the wall's Blocked panel.
5. **Sameness** (Robert: "all the same, essentially").
   - The floor, wall, Workshop, Queue and Operate share one skin: tan page, cream cards, small uppercase headers.
   - "Needs you" appears in three look-alike forms.
   - The floor rail is a small copy of the wall.
   - The Workshop's "HOST TIGHT" is a small pill rather than a dominant gauge.
   - Suggestion: one dominant element and one accent colour per room (the floor's chat, the wall's board, the Workshop's host gauge), plus a clear room header.
